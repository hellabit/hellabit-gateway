import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import solc from 'solc';
import {createCustomCommon, Mainnet, Hardfork} from '@ethereumjs/common';
import {createLegacyTx} from '@ethereumjs/tx';
import {createBlock} from '@ethereumjs/block';
import {createVM, runTx} from '@ethereumjs/vm';
import {createAccount, createAddressFromPrivateKey, createAddressFromString, hexToBytes, bytesToHex} from '@ethereumjs/util';
import {Wallet, Interface, ContractFactory, keccak256, toUtf8Bytes} from 'ethers';

const root=path.resolve(import.meta.dirname,'..');
const checks=[];
function check(name, value) { assert.ok(value,name); checks.push({name,passed:true}); }
const sourceNames=['AttestedStablecoin','PaymentRouter','TestDollar'];
const sources=Object.fromEntries(sourceNames.map(n=>[`contracts/${n}.sol`,{content:fs.readFileSync(path.join(root,'contracts',n+'.sol'),'utf8')}]));
const input={language:'Solidity',sources,settings:{optimizer:{enabled:true,runs:200},evmVersion:'cancun',metadata:{bytecodeHash:'none'},outputSelection:{'*':{'*':['abi','evm.bytecode.object','evm.deployedBytecode.object']}}}};
const compiled=JSON.parse(solc.compile(JSON.stringify(input),{import(name){
  if(!name.startsWith('@openzeppelin/contracts/')||name.includes('..'))return {error:'import outside pinned OpenZeppelin package'};
  return {contents:fs.readFileSync(path.join(root,'node_modules',name),'utf8')};
}}));
assert.deepEqual((compiled.errors||[]).filter(e=>e.severity==='error'),[],'Solidity compilation');
const contracts=Object.fromEntries(sourceNames.map(n=>[n,compiled.contracts[`contracts/${n}.sol`][n]]));
const common=createCustomCommon({chainId:31337},Mainnet,{hardfork:Hardfork.Cancun});
const vm=await createVM({common});
let timestamp=1800000000n;
const block=()=>createBlock({header:{number:1n,timestamp,gasLimit:30_000_000n,baseFeePerGas:7n}},{common});
// Published deterministic test keys exist only inside this private EVM.
const keys=Array.from({length:8},(_,i)=>'0x'+String(i+1).padStart(64,'0'));
const wallets=keys.map(k=>new Wallet(k));
const addresses=keys.map(k=>createAddressFromPrivateKey(hexToBytes(k)));
for(const a of addresses) await vm.stateManager.putAccount(a,createAccount({balance:10n**30n}));
async function send(who,to,data){
  const account=await vm.stateManager.getAccount(addresses[who]);
  const tx=createLegacyTx({nonce:account.nonce,gasLimit:12_000_000n,gasPrice:1_000_000_000n,...(to?{to:createAddressFromString(to)}:{}),data:hexToBytes(data)},{common}).sign(hexToBytes(keys[who]));
  const result=await runTx(vm,{tx,block:block()});
  return {...result,ok:!result.execResult.exceptionError};
}
async function deploy(name,args=[]){
  const c=contracts[name], factory=new ContractFactory(c.abi,'0x'+c.evm.bytecode.object);
  const tx=await factory.getDeployTransaction(...args);
  const result=await send(0,null,tx.data); assert.ok(result.ok,'deploy '+name);
  return {name,address:result.createdAddress.toString(),iface:new Interface(c.abi)};
}
const invoke=(who,c,name,args=[])=>send(who,c.address,c.iface.encodeFunctionData(name,args));
async function value(c,name,args=[]){
  const result=await vm.evm.runCall({caller:addresses[0],to:createAddressFromString(c.address),data:hexToBytes(c.iface.encodeFunctionData(name,args)),block:block()});
  assert.ok(!result.execResult.exceptionError,'view '+name);
  return c.iface.decodeFunctionResult(name,bytesToHex(result.execResult.returnValue))[0];
}
const hash=s=>keccak256(toUtf8Bytes(s));
const observation=(ceiling,sequence,proof,observedAt=timestamp)=>[ceiling,observedAt,observedAt+3600n,sequence,hash(proof)];
const stable=await deploy('AttestedStablecoin',['Example Institution Dollar','EXUSD',hash('example-issuer'),'0x555344',wallets[0].address]);
check('Issuer starts with zero supply',await value(stable,'totalSupply')===0n);
check('Unprivileged mint fails',!(await invoke(7,stable,'mint',[wallets[4].address,1n,hash('attack')])).ok);
for(const [role,who] of [['ATTESTOR_ROLE',1],['MINTER_ROLE',2],['SETTLER_ROLE',3],['COMPLIANCE_ROLE',0]]) {
  check(role+' explicitly assigned',(await invoke(0,stable,'grantRole',[await value(stable,role),wallets[who].address])).ok);
}
check('Mint before reserves fails',!(await invoke(2,stable,'mint',[wallets[4].address,1n,hash('before-reserve')])).ok);
check('Unauthorized reserve assertion fails',!(await invoke(7,stable,'attestReserve',observation(1_000_000n,1n,'bad-proof'))).ok);
check('Reserve assertion recorded',(await invoke(1,stable,'attestReserve',observation(1_000_000n,1n,'bank-proof-1'))).ok);
check('Reserve attestation replay fails',!(await invoke(1,stable,'attestReserve',observation(2_000_000n,2n,'bank-proof-1'))).ok);
check('Issuance within reserve cap succeeds',(await invoke(2,stable,'mint',[wallets[4].address,1_000_000n,hash('mint-1')])).ok);
check('Mint request replay fails',!(await invoke(2,stable,'mint',[wallets[4].address,1_000_000n,hash('mint-1')])).ok);
check('Over-issuance fails',!(await invoke(2,stable,'mint',[wallets[4].address,1n,hash('mint-2')])).ok);
check('Holder can request redemption',(await invoke(4,stable,'requestRedemption',[500_000n,hash('redemption-1')])).ok);
check('Burn keeps pending cash liability',await value(stable,'totalSupply')===500_000n&&await value(stable,'pendingRedemptions')===500_000n);
check('Pending redemption cannot be reminted',!(await invoke(2,stable,'mint',[wallets[4].address,1n,hash('mint-3')])).ok);
check('Newer reserve evidence can establish previously unused headroom',(await invoke(1,stable,'attestReserve',observation(1_500_000n,2n,'bank-proof-2'))).ok);
check('A deficit observation is retained and removes old mint authority',(await invoke(1,stable,'attestReserve',observation(400_000n,3n,'bank-proof-3'))).ok&&await value(stable,'reserveCeiling')===400_000n&&!(await invoke(2,stable,'mint',[wallets[4].address,1n,hash('blocked-after-deficit')])).ok);
check('Out-of-order or stale observations cannot restore issuance',!(await invoke(1,stable,'attestReserve',observation(1_500_000n,2n,'old-sequence'))).ok&&!(await invoke(1,stable,'attestReserve',observation(1_500_000n,4n,'old-time',timestamp-301n))).ok);
check('Unprivileged payout confirmation fails',!(await invoke(7,stable,'confirmRedemption',[wallets[4].address,hash('redemption-1'),hash('payout-1')])).ok);
check('Named settler can record payout',(await invoke(3,stable,'confirmRedemption',[wallets[4].address,hash('redemption-1'),hash('payout-1')])).ok);
check('Payout confirmation replay fails',!(await invoke(3,stable,'confirmRedemption',[wallets[4].address,hash('redemption-1'),hash('payout-1')])).ok);
check('A confirmed redemption survives the deficit and requires fresh reserve evidence',await value(stable,'reserveCeiling')===0n&&await value(stable,'pendingRedemptions')===0n&&await value(stable,'reserveValidUntil')===0n);
check('Fresh evidence can restore issuance headroom',(await invoke(1,stable,'attestReserve',observation(600_000n,4n,'bank-proof-4'))).ok);
check('Compliance freeze succeeds',(await invoke(0,stable,'setFrozen',[wallets[4].address,true])).ok);
check('Frozen holder cannot transfer',!(await invoke(4,stable,'transfer',[wallets[5].address,1n])).ok);
check('Compliance unfreeze succeeds',(await invoke(0,stable,'setFrozen',[wallets[4].address,false])).ok);
check('Pause succeeds',(await invoke(0,stable,'pause')).ok);
check('Paused transfers fail',!(await invoke(4,stable,'transfer',[wallets[5].address,1n])).ok);
check('Unpause succeeds',(await invoke(0,stable,'unpause')).ok);
timestamp+=3601n;
check('Expired reserve evidence stops mint',(await value(stable,'reserveValidUntil'))<timestamp&&!(await invoke(2,stable,'mint',[wallets[4].address,1n,hash('expired')])).ok);

const dollar=await deploy('TestDollar',[wallets[4].address,10_000_000n]);
const router=await deploy('PaymentRouter',[dollar.address]);
const domain={name:'Hellabit Payment Router',version:'1',chainId:31337,verifyingContract:router.address};
const types={Payment:[{name:'buyer',type:'address'},{name:'merchant',type:'address'},{name:'token',type:'address'},{name:'feeRecipient',type:'address'},{name:'amount',type:'uint256'},{name:'fee',type:'uint256'},{name:'deadline',type:'uint256'},{name:'operationId',type:'bytes32'}]};
const payment={buyer:wallets[4].address,merchant:wallets[5].address,token:dollar.address,feeRecipient:wallets[6].address,amount:1_000_000n,fee:1000n,deadline:timestamp+1000n,operationId:hash('paid-job-1')};
const signature=await wallets[4].signTypedData(domain,types,payment);
check('Payer approves exact settlement budget',(await invoke(4,dollar,'approve',[router.address,1_000_000n])).ok);
check('Wrong-chain signature fails',!(await invoke(7,router,'settle',[payment,await wallets[4].signTypedData({...domain,chainId:1},types,payment)])).ok);
check('Changed merchant invalidates signature',!(await invoke(7,router,'settle',[{...payment,merchant:wallets[7].address},signature])).ok);
check('Changed fee invalidates signature',!(await invoke(7,router,'settle',[{...payment,fee:2000n},signature])).ok);
check('Expired authorization fails',!(await invoke(7,router,'settle',[{...payment,deadline:timestamp-1n},signature])).ok);
check('Explicit buyer-signed payment settles',(await invoke(7,router,'settle',[payment,signature])).ok);
check('Seller receives gross less disclosed fee',await value(dollar,'balanceOf',[wallets[5].address])===999_000n);
check('Platform receives exact disclosed fee',await value(dollar,'balanceOf',[wallets[6].address])===1000n);
check('Router retains no token proceeds',await value(dollar,'balanceOf',[router.address])===0n);
check('Payment replay fails',!(await invoke(7,router,'settle',[payment,signature])).ok);
const second={...payment,operationId:hash('paid-job-2')};
const secondSignature=await wallets[4].signTypedData(domain,types,second);
check('Partial allowance established',(await invoke(4,dollar,'approve',[router.address,999_000n])).ok);
check('Fee-leg failure reverts whole settlement',!(await invoke(7,router,'settle',[second,secondSignature])).ok);
check('Failed settlement leaves balances and replay marker unchanged',await value(dollar,'balanceOf',[wallets[5].address])===999_000n&&!(await value(router,'settled',[wallets[4].address,second.operationId])));
const other=await deploy('AttestedStablecoin',['Second Issuer Dollar','OTHER',hash('second-issuer'),'0x434144',wallets[7].address]);
check('White-label deployments have independent identity and supply',await value(other,'issuerId')!==await value(stable,'issuerId')&&await value(other,'totalSupply')===0n);
for(const [role,who] of [['ATTESTOR_ROLE',1],['MINTER_ROLE',2],['SETTLER_ROLE',3]])assert.ok((await invoke(7,other,'grantRole',[await value(other,role),wallets[who].address])).ok);
assert.ok((await invoke(1,other,'attestReserve',observation(100n,1n,'second-issuer-backing'))).ok);
for(const i of [4,5])assert.ok((await invoke(2,other,'mint',[wallets[i].address,50n,hash('holder-'+i)])).ok);
const shared=hash('public-request-id');
check('One holder cannot consume another holder’s public redemption ID',(await invoke(5,other,'requestRedemption',[1n,shared])).ok&&(await invoke(4,other,'requestRedemption',[40n,shared])).ok);
check('Replay still fails within the same holder’s scope',!(await invoke(4,other,'requestRedemption',[1n,shared])).ok);
check('Settlement confirmation selects the holder’s exact claim',(await invoke(3,other,'confirmRedemption',[wallets[4].address,shared,hash('holder-four-proof')])).ok&&await value(other,'pendingRedemptions')===1n);

check('Admin cannot transfer immediately through delayed admin control',await value(stable,'defaultAdminDelay')===172800n);

const output=path.join(root,'artifacts');fs.mkdirSync(output,{recursive:true,mode:0o700});
const hashSource=crypto.createHash('sha256').update(JSON.stringify(input)).digest('hex');
for(const name of sourceNames)fs.writeFileSync(path.join(output,name+'.json'),JSON.stringify({compiler:solc.version(),evmVersion:'cancun',sourceInputSha256:hashSource,abi:contracts[name].abi,bytecode:'0x'+contracts[name].evm.bytecode.object},null,2)+'\n');
const report={schema:'hellabit.contract-proof.v1',ok:true,mode:'private-evm',chainId:31337,compiler:solc.version(),sourceInputSha256:hashSource,checks,externalTransactions:0,realUSDCTransferred:0,realStablecoinIssued:false,productionReady:false};
fs.writeFileSync(path.join(output,'contract-proof.json'),JSON.stringify(report,null,2)+'\n',{mode:0o600});
console.log(JSON.stringify({ok:true,checks:checks.length,mode:report.mode,externalTransactions:0}));
