import {test} from 'node:test';import assert from 'node:assert/strict';
import {Hellabit,HellabitError} from '../sdk/hellabit.mjs';
test('SDK refuses credential-bearing and public cleartext endpoints',()=>{for(const baseURL of ['http://example.com','https://a:b@example.com','https://example.com/path','https://example.com/?token=x'])assert.throws(()=>new Hellabit({baseURL,apiKey:'hb_test_example'}));});
test('SDK binds authorization and does not retry an ambiguous mutation',async()=>{let calls=0;const client=new Hellabit({baseURL:'http://127.0.0.1:8123',apiKey:'hb_test_example',fetch:async(url,options)=>{calls++;assert.equal(options.headers.Authorization,'Bearer hb_test_example');assert.equal(options.redirect,'error');throw new Error('timeout');}});await assert.rejects(client.reserve('hb_example',{customer:'agent-01',quote:'q_1',key:'same-job'}),/timeout/);assert.equal(calls,1);});
test('SDK exposes typed server errors and encodes the exact workspace',async()=>{const client=new Hellabit({baseURL:'https://example.com',apiKey:'hb_test_example',fetch:async(url)=>{assert.equal(url,'https://example.com/api/v1/workspaces/hb_example');return {ok:false,status:403,json:async()=>({error:'workspace_forbidden'})};}});await assert.rejects(client.status('hb_example'),e=>e instanceof HellabitError&&e.status===403);assert.throws(()=>client.id('../other'));});
test('SDK recovers an unknown reservation with the original purchase key',async()=>{let calls=0;const client=new Hellabit({baseURL:'https://example.com',apiKey:'hb_agent_test_example',fetch:async(url,options)=>{calls++;assert.equal(url,'https://example.com/api/v1/workspaces/hb_example/operations/lookup');assert.deepEqual(JSON.parse(options.body),{key:'purchase-001'});return {ok:true,json:async()=>({id:'op_original',state:'reserved'})};}});assert.equal((await client.lookup('hb_example','purchase-001')).id,'op_original');assert.equal(calls,1);});
test('SDK obtains fresh access tokens without persisting or retrying them',async()=>{
  let grants=0,calls=0;const client=new Hellabit({baseURL:'https://api.example',getAccessToken:()=>{grants++;return 'header.payload.signature'+grants;},
    fetch:async(url,options)=>{calls++;assert.equal(options.headers.Authorization,'Bearer header.payload.signature'+calls);return {ok:true,json:async()=>({ok:true})};}});
  await client.status('hb_example');await client.status('hb_example');assert.equal(grants,2);assert.equal(calls,2);assert(!JSON.stringify(client).includes('signature'));
});
test('SDK refuses competing credential sources and malformed access tokens before network',async()=>{
  assert.throws(()=>new Hellabit({baseURL:'https://api.example',apiKey:'hb_test_example',getAccessToken:()=>''}));
  let calls=0;const client=new Hellabit({baseURL:'https://api.example',getAccessToken:()=> 'unsafe token',fetch:async()=>{calls++;}});
  await assert.rejects(client.status('hb_example'),/Invalid access token/);assert.equal(calls,0);
});
test('SDK bounds a stalled token callback and never submits a payment',async()=>{
  let calls=0;const client=new Hellabit({baseURL:'https://api.example',timeout:100,getAccessToken:()=>new Promise(()=>{}),fetch:async()=>{calls++;}});
  await assert.rejects(client.reserve('hb_example',{}),e=>e.code==='access_token_timeout');assert.equal(calls,0);
});
test('Operator SDK inspects the exact payout with read-only requests',async()=>{
  const calls=[],intent='intent_recovery';
  const client=new Hellabit({baseURL:'https://api.example',getAccessToken:()=> 'header.payload.signature',
    fetch:async(url,options)=>{
      calls.push([new URL(url).pathname,options.method,options.body]);
      return {ok:true,json:async()=>url.endsWith('/ops')?{intents:[{id:intent}]}:{id:intent,state:'unknown'}};
    }});
  assert.equal((await client.payouts('hb_example')).intents[0].id,intent);
  assert.equal((await client.payout('hb_example',intent)).state,'unknown');
  assert.throws(()=>client.payout('hb_example','intent_../other'));
  assert.deepEqual(calls,[['/api/v1/workspaces/hb_example/ops','GET',undefined],
    ['/api/v1/workspaces/hb_example/ops/'+intent,'GET',undefined]]);
});
