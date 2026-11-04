const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const csrf = $('meta[name=hellabit-csrf]').content;
const views = ['overview', 'work', 'issuer', 'connections', 'developer'];
function saved() { try { return JSON.parse(sessionStorage.getItem('hellabit-view') || '{}'); } catch { return {}; } }
const preference = saved();
const state = {tenant: preference.tenant || null, data: null, asset: preference.asset || 'CAD', view: views.includes(preference.view) ? preference.view : 'overview', busy: false, keys: [], plan: null, activity:null, allActivity:null, allOperations:false, fundingOpen:false, focusOp:null, resultOp:null};
const invoiceExample={currency:'CAD',items:[{quantity:2,unit_amount_units:1500},{quantity:1,unit_amount_units:500}],tax_units:175,declared_total_units:3675};
$('#work-input').value=JSON.stringify(invoiceExample,null,2);
const viewLabels={overview:'Overview',work:'Paid work',issuer:'Issuer lab',connections:'Settlement planning',developer:'Developers'};
function pendingPurchase(){try{return JSON.parse(sessionStorage.getItem('hellabit-purchase-'+state.tenant)||'null');}catch{return null;}}
function savePending(value){try{sessionStorage.setItem('hellabit-purchase-'+state.tenant,JSON.stringify(value));}catch{throw new Error('This browser cannot retain the purchase identity for recovery. No reservation was submitted.');}}
function clearPending(){try{sessionStorage.removeItem('hellabit-purchase-'+state.tenant);}catch{}}
function closeMenu(){$('#primary-nav').dataset.open='false';$('#nav-toggle').setAttribute('aria-expanded','false');}
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const uid = prefix => prefix + '-' + crypto.randomUUID();
const errors = {
  insufficient_available_credits: 'The available balance cannot cover this reservation. Add funds or reduce the price.',
  reserve_evidence_stale: 'Record current reserve evidence before issuing tokens.',
  insufficient_attested_reserves: 'Issuance would exceed the reserve limit. Reduce the amount or record additional reserves.',
  issuer_identity_already_in_use: 'This currency identity is fixed because its books are already in use.',
  reconciliation_required: 'Review the observed outcome before changing this uncertain operation.',
  delivery_access_revoked: 'This charge was refunded and its report access was revoked.',
  refund_requires_funded_reversal: 'Proceeds have already been paid out. Restore the merchant and fee balances before refunding.',
  operation_not_found: 'No operation was found for this request key.',
  invalid_identifier: 'Use letters, numbers, dots, colons, underscores or hyphens in references.',
  invalid_workspace_name: 'Use 2–60 letters, numbers, spaces or ordinary business-name punctuation.'
};
function remember() { try { sessionStorage.setItem('hellabit-view', JSON.stringify({tenant:state.tenant, asset:state.asset, view:state.view})); } catch {} }
async function api(path, {method='GET', body}={}) {
  let response, result;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  try {
    response = await fetch(path, {method, signal:controller.signal, credentials:'same-origin', headers:{...(body ? {'Content-Type':'application/json', 'X-Hellabit-CSRF':csrf} : {})}, ...(body ? {body:JSON.stringify(body)} : {})});
    result = await response.json();
  } catch {
    const error = new Error('The request outcome is unknown. Review the payment record before repeating a money action.');
    error.code = 'outcome_unknown'; throw error;
  } finally { clearTimeout(timer); }
  if (!response.ok) { const error = new Error(errors[result.error] || result.error?.replaceAll('_', ' ') || 'Request could not be completed.'); error.code=result.error; error.status=response.status; throw error; }
  return result;
}
const base = () => '/api/v1/workspaces/' + state.tenant;
const post = (tail, body={}) => api(base() + '/' + tail, {method:'POST', body});
function notice(message) { $('#action-message').textContent=message; $('#action-feedback').hidden=false; $('#action-error').hidden=true; }
function failure(message) { $('#action-error').textContent=message; $('#action-error').hidden=false; $('#action-feedback').hidden=true; }
function amount(text, decimals, zero=false) {
  if (typeof text !== 'string' || !new RegExp('^\\d{1,12}(?:\\.\\d{0,' + decimals + '})?$').test(text.trim())) throw new Error('Enter an amount with at most ' + decimals + ' decimal places.');
  const [whole, fraction=''] = text.trim().split('.');
  const value = BigInt(whole) * 10n**BigInt(decimals) + BigInt(fraction.padEnd(decimals,'0') || '0');
  if (value < (zero ? 0n : 1n) || value > 1_000_000_000_000n) throw new Error('Enter ' + (zero ? 'a nonnegative' : 'a positive') + ' amount within the supported limit.');
  return Number(value);
}
const number = (value, asset) => new Intl.NumberFormat('en', {minimumFractionDigits:2, maximumFractionDigits:asset.decimals}).format(value / 10**asset.decimals);
const selected = () => state.data.assets.find(a => a.id===state.asset) || state.data.assets[0];
const label = asset => asset.rail==='issuer' ? state.data.issuer.symbol : asset.symbol;
const quoteFee = asset => asset.decimals===2 ? 1 : 1000;
const money = (value, asset) => number(value, asset) + ' ' + label(asset);
const timestamp = value => new Intl.DateTimeFormat('en', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'UTC'}).format(new Date(value*1000)) + ' UTC';
function clearKey() { $('#one-time-key').value=''; $('#one-time-key').type='password'; $('#key-result').hidden=true; $('#reveal-key').textContent='Show key'; }
function jump(view, focus=false) {
  if (!views.includes(view)) return;
  if (state.view==='developer' && view!=='developer') clearKey();
  if (state.view!==view) { $('#action-feedback').hidden=true; $('#action-error').hidden=true; }
  state.view=view;
  $('#current-view-label').textContent=viewLabels[view];closeMenu();
  $$('[data-pane]').forEach(pane => pane.hidden=pane.dataset.pane!==view);
  $$('[data-view]').forEach(button => { if(button.dataset.view===view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  remember();
  if(focus) { const heading=$(`[data-pane="${view}"] h1`); heading.tabIndex=-1; heading.focus({preventScroll:true}); heading.scrollIntoView({block:'start'}); }
}
function focusIdentity(element) {
  if(!element || element===document.body) return null;
  if(element.id) return '#'+CSS.escape(element.id);
  for(const attribute of ['data-op','data-revoke-key','data-asset','data-payout','data-merchant-payout']) {
    const value=element.getAttribute(attribute);
    if(value) return `[${attribute}="${CSS.escape(value)}"]` + (element.dataset.action ? `[data-action="${CSS.escape(element.dataset.action)}"]` : '');
  }
  return null;
}
async function perform(fn) {
  if(state.busy) return;
  const focus=focusIdentity(document.activeElement);
  state.busy=true; $('#main').setAttribute('aria-busy','true'); $('#action-error').hidden=true; updateControls();
  let error;
  try { await fn(); } catch(e) { error=e; }
  try { await refresh(); } catch(e) { if(!error) error=e; }
  state.busy=false; $('#main').removeAttribute('aria-busy'); updateControls();
  if(error) failure(error.message);
  const target=focus && $(focus);
  if(state.focusOp){const row=$(`[data-operation-row="${CSS.escape(state.focusOp)}"]`);state.focusOp=null;if(row){row.tabIndex=-1;row.focus({preventScroll:true});row.scrollIntoView({block:'center',behavior:'instant'});}}
  else if(target && !target.disabled && target.getClientRects().length) target.focus({preventScroll:true});
}
async function workspaces(preferred) {
  const data=await api('/api/v1/workspaces');
  $('#workspace-select').innerHTML=data.workspaces.map(w => `<option value="${esc(w.id)}">${esc(w.name)}</option>`).join('');
  const ids=data.workspaces.map(w => w.id);
  state.tenant=ids.includes(preferred) ? preferred : ids.includes(state.tenant) ? state.tenant : ids[0];
  $('#empty').hidden=!!state.tenant; $('#desk').hidden=!state.tenant; $('#workspace-select').hidden=!state.tenant;
  if(state.tenant) { $('#workspace-select').value=state.tenant; await refresh(); }
}
async function refresh() {
  if(!state.tenant) return;
  const [data, keys]=await Promise.all([api(base()),api(base()+'/keys')]);
  state.data=data; state.keys=keys.keys;
  if(!data.assets.some(a => a.id===state.asset)) state.asset='CAD';
  [state.activity,state.allActivity]=await Promise.all([api(base()+'/activity/'+state.asset),state.allOperations?api(base()+'/activity/all'):Promise.resolve(null)]);
  render(); remember();
}
function renderJourney() {
  const d=state.data, a=selected(), ops=state.activity.operations.filter(op=>op.asset===a.id);
  const held=ops.some(op=>op.state==='reserved'), delivered=ops.some(op=>op.state==='committed'&&!op.refunded);
  const funded=a.balance>0 || held || delivered;
  let title, detail, action;
  if(held) {title='The price is reserved. Run the check.';detail='Open the reserved purchase. The result and test-credit charge will be recorded together.';action='Run the reserved check';}
  else if(delivered) {title='The sample purchase is complete.';detail='View the computed result and its receipt. This proves the workflow, not commercial demand or live settlement.';action='View result and receipt';}
  else if(funded) {title='The test budget is ready.';detail='Reserve an invoice total check. Its input and price stay fixed; no charge occurs before a result is stored.';action='Try an invoice check';}
  else {title='Start with a small budget.';detail=a.rail==='issuer'?'Record reserves and issue test tokens, then use them to pay for work.':'Add test credits, reserve a sample check, and inspect its computed result and receipt.';action=a.rail==='issuer'?'Prepare your currency':'Try your first payment';}
  $('#next-title').textContent=title; $('#next-detail').textContent=detail;
  $('#next-action').textContent=action+' ↗'; $('#next-action').dataset.jump=(!funded&&a.rail==='issuer')?'issuer':'work';
  $('#journey').innerHTML=[['Budget',funded],['Reserve',held||delivered],['Deliver',delivered]].map(([name,done],index)=>`<li class="${done?'done':''}"><span>${done?'✓':'0'+(index+1)}</span>${name}</li>`).join('');
  const uncertain=d.operation_counts.uncertain||0;
  $('#attention').hidden=!uncertain;
  $('#attention').innerHTML=`<div><strong>${uncertain} unknown outcome${uncertain===1?'':'s'} need${uncertain===1?'s':''} review.</strong><span>Funds stay held until you record what happened.</span></div><button class="quiet" id="review-unknown">Review held work in all assets ↗</button>`;
}
function operationActions(op) {
  const prefix=`data-op="${esc(op.id)}"`;
  if(op.refunded)return '<span class="muted">Credits returned · future result access revoked</span>';
  if(op.state==='reserved')return `<button ${prefix} data-action="run">Run check and charge</button><button ${prefix} data-action="uncertain">Mark outcome unknown</button><button ${prefix} data-action="release">Release hold</button>`;
  if(op.state==='uncertain')return `<button ${prefix} data-action="check">Check existing purchase</button><button ${prefix} data-action="resolve">Record observed outcome</button>`;
  if(op.state==='committed'){
    const a=state.data.assets.find(a=>a.id===op.asset),missing=a.merchant<op.amount-op.fee||a.fees<op.fee;
    return (op.has_delivery?`<button ${prefix} data-action="view">View result and receipt</button><button ${prefix} data-action="download">Download result</button>`:'<span class="muted">Completion evidence recorded · no stored result</span>')+`<button class="danger" ${prefix} data-action="refund" data-policy-disabled="${missing}" ${missing?'disabled title="Available merchant and fee balances cannot fund this reversal."':''}>Return credits</button>${missing?'<span class="muted">Proceeds already paid out. A funded reversal is required.</span>':''}`;
  }
  return '<span class="muted">No charge · hold released</span>';
}
function stateLabel(op){if(op.refunded)return 'Credits returned';return {reserved:'Reserved · Not charged',uncertain:'Outcome unknown · Funds held',released:'No charge · Hold released',committed:op.has_delivery?'Charged · Result available':'Charged · Evidence recorded'}[op.state];}
function renderBalances(a){
  const group=(title,detail,values)=>`<div class="balance-group"><p class="balance-owner">${title}</p><p class="muted">${detail}</p>${values.map(([name,n])=>`<div class="balance-line"><span>${name}</span><strong>${money(n,a)}</strong></div>`).join('')}</div>`;
  $('#metrics').innerHTML=group('Customer test credits','Account '+esc(state.data.customer),[['Available',a.available],['Reserved for work',a.held]])+group('Merchant test proceeds','Separate from the customer budget',[['Available for payout',a.merchant],['Held for payout',a.payout_held]])+group('Platform test fees','Internal postings · not collected Hellabit revenue',[['Accrued',a.fees]]);
}
function renderActivity(){
  const d=state.data,a=selected(),events=state.activity.journal,work=state.allOperations?state.allActivity:state.activity,ops=work.operations;
  $('#all-operations').checked=state.allOperations;
  $('#journal-scope').textContent=`Showing ${events.length} of ${state.activity.journal_count} ${label(a)} movements. The asset filter is applied before the 30-row limit. Export includes these rows.`;
  $('#journal').innerHTML=events.length?`<div class="table-wrap"><table><thead><tr><th>Movement</th><th>Gross amount</th><th>When</th><th>Receipt</th></tr></thead><tbody>${events.map(e=>`<tr><td>${esc(e.kind.replaceAll('-',' '))}</td><td class="amount">${money(e.amount,a)}</td><td>${timestamp(e.created)}</td><td><details><summary>#${e.seq} · ${esc(e.digest.slice(0,10))}…</summary><dl class="receipt-detail"><dt>Reference</dt><dd>${esc(e.reference)}</dd><dt>SHA-256</dt><dd>${esc(e.digest)}</dd></dl></details></td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state">No ${esc(label(a))} movements yet. Test funding or issuance starts the record.</div>`;
  $('#operations').innerHTML=ops.length?`<p class="muted">${state.allOperations?'All assets':esc(label(a))+' only'} · ${ops.length} of ${work.operation_count} purchases shown · unknown outcomes and held work first. Original quotes do not represent current earnings.</p><div class="table-wrap"><table><thead><tr><th>Purchase</th><th>Quote and charges</th><th>Outcome</th><th>Next action</th></tr></thead><tbody>${ops.map(op=>{const asset=d.assets.find(x=>x.id===op.asset);return `<tr data-operation-row="${esc(op.id)}"><td><strong>Purchase ${esc(op.id.slice(-6).toUpperCase())}</strong><small class="cell-note">${op.resource==='invoice-check'?'Invoice total check':'Synthetic reference sample'} · ${esc(op.customer)}</small><details><summary>Quote split and technical details</summary><dl class="receipt-detail"><dt>Purchase ID</dt><dd>${esc(op.id)}</dd><dt>Raw work state</dt><dd>${esc(op.state)}</dd><dt>Original merchant / fee split</dt><dd>${money(op.amount-op.fee,asset)} / ${money(op.fee,asset)}</dd><dt>Reserved</dt><dd>${timestamp(op.created)}</dd><dt>State checked</dt><dd>${timestamp(work.observed_at)}</dd>${op.input_hash?`<dt>Quoted input SHA-256</dt><dd>${esc(op.input_hash)}</dd>`:''}</dl><button class="quiet" data-copy-id="${esc(op.id)}">Copy purchase ID</button></details></td><td class="amount">Quote ${money(op.amount,asset)}<small class="cell-note">Charged ${money(op.state==='committed'?op.amount:0,asset)} · returned ${money(op.refunded?op.amount:0,asset)}</small></td><td><span class="pill ${op.refunded?'refunded':op.state}">${stateLabel(op)}</span>${op.state==='uncertain'?`<small class="cell-note">${money(op.amount,asset)} remains reserved.</small>`:''}</td><td><div class="operation-actions">${operationActions(op)}</div></td></tr>`;}).join('')}</tbody></table></div>`:'<div class="empty-state">No purchases in this asset yet. Reserve the invoice check to start.</div>';
}
function render() {
  const d=state.data, a=selected(), issuer=d.issuer, token=d.assets.find(x=>x.rail==='issuer');
  $('#observed-at').textContent='Updated '+timestamp(d.observed_at);
  $('#asset-tabs').innerHTML=d.assets.map(asset=>`<button data-asset="${esc(asset.id)}" aria-pressed="${asset.id===a.id}">${esc(label(asset))}${asset.rail==='issuer'?' · test token':asset.rail==='usdc'?' · testnet':' · test'}</button>`).join('');
  renderBalances(a);
  $('#work-asset').innerHTML=d.assets.map(asset=>`<option value="${esc(asset.id)}">${esc(label(asset))}${asset.rail==='issuer'?' · test token':asset.rail==='usdc'?' · Base Sepolia testnet':' · test credits'}</option>`).join(''); $('#work-asset').value=a.id;
  $('#work-balance').textContent=money(a.available,a)+' available · '+money(a.held,a)+' held';
  $('#payout-available').textContent=money(a.merchant,a)+' merchant proceeds';
  const names={bank:['Bank money','CAD and USD'],usdc:['USDC','Native token · Base Sepolia'],issuer:['Your currency',issuer.name]};
  $('#rail-summary').innerHTML=Object.entries(names).map(([rail,[name,detail]],i)=>`<div class="rail-row"><span class="rail-icon">${i+1}</span><div class="rail-info"><strong>${esc(name)}</strong><small>${esc(detail)}</small></div><span class="rail-status">${d.enrollments.some(e=>e.rail===rail)?'SAVED · UNVERIFIED':'NOT CONNECTED'}</span></div>`).join('');
  renderActivity();
  $('#payouts').innerHTML=d.payouts.length?`<div class="table-wrap"><table><thead><tr><th>Payout / recipient</th><th>Amount</th><th>State</th><th>Action</th></tr></thead><tbody>${d.payouts.map(p=>{const asset=d.assets.find(x=>x.id===p.asset);return `<tr><td class="hash">${esc(p.id.slice(0,16))}…<small class="cell-note">${esc(p.recipient)}</small></td><td class="amount">${money(p.amount,asset)}</td><td><span class="pill">${p.state==='paid'?'Simulated payout recorded':'Awaiting simulated settlement'}</span></td><td>${p.state==='pending'?`<button class="quiet" data-merchant-payout="${esc(p.id)}">Record test settlement ↗</button>`:'Test settlement recorded'}</td></tr>`;}).join('')}</tbody></table></div>`:'<div class="empty-state">A payout holds merchant proceeds until its settlement is recorded.</div>';
  if(!$('[data-pane=issuer]').contains(document.activeElement)) { $('#issuer-name').value=issuer.name; $('#issuer-symbol').value=issuer.symbol; $('#issuer-currency').value=issuer.currency; }
  $('#issuer-metrics').innerHTML=`<div class="reserve-caption">Recorded test reserves</div><div class="reserve-number">${number(issuer.reserve,token)} <span>${esc(issuer.currency)}</span></div><div class="reserve-small"><div><strong>${number(issuer.supply,token)}</strong><span>Outstanding ${esc(issuer.symbol)}</span></div><div><strong>${number(issuer.pending_redemptions,token)}</strong><span>Unpaid redemption claims</span></div></div><div class="reserve-caption">Issuance headroom · ${money(issuer.headroom,token)}</div><p class="reserve-validity ${issuer.reserve_current?'current':'expired'}">${issuer.reserve_current?'Test evidence expires '+timestamp(issuer.valid_until):'Current reserve evidence required before issuance'}</p>`;
  $('#redemptions').innerHTML=issuer.redemptions.length?`<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Amount</th><th>State</th><th>Action</th></tr></thead><tbody>${issuer.redemptions.map(claim=>`<tr><td class="hash">${esc(claim.id.slice(0,17))}…</td><td>${money(claim.amount,token)}</td><td><span class="pill">${claim.state==='paid'?'Simulated payout recorded':'Claim awaiting simulated payout'}</span></td><td>${claim.state==='pending'?`<button class="quiet" data-payout="${esc(claim.id)}">Record test bank payout ↗</button>`:'Test claim paid'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty-state">Redemption claims stay visible until a payout is recorded.</div>';
  for(const rail of ['bank','usdc','issuer']) $('[data-connection='+rail+']').textContent=d.enrollments.some(e=>e.rail===rail)?'DRAFT SAVED · NOT CONNECTED':'NOT CONNECTED / PREPARATION REQUIRED';
  for(const enrollment of d.enrollments) { const form=$('form[data-enroll='+enrollment.rail+']'); if(!form.contains(document.activeElement)) for(const [name,value] of Object.entries(enrollment.fields)) {const field=form.elements.namedItem(name); if(field) field.value=String(value);} }
  $('#sdk-example').textContent=`import { Hellabit } from './sdk/hellabit.mjs';
const gateway = new Hellabit({
  baseURL: ${JSON.stringify(location.origin)},
  apiKey: process.env.HELLABIT_MERCHANT_KEY
});
const workspace = ${JSON.stringify(d.workspace.id)};
// Persist one key per logical purchase. Keep it after a timeout.
const key = 'invoice-check-001';
let purchase;
try { purchase = await gateway.lookup(workspace, key); }
catch (error) {
  if (error.code !== 'operation_not_found') throw error;
  const quote = await gateway.quote(workspace, {
    asset: 'CAD', amount: 100, fee: 1, resource: 'invoice-check',
    input: ${JSON.stringify(invoiceExample)}
  });
  purchase = await gateway.reserve(workspace, {
    customer: 'agent-01', quote: quote.quote, key
  });
}
if (purchase.state === 'reserved') await gateway.run(workspace, purchase.id);
const result = await gateway.delivery(workspace, purchase.id);
// Unknown outcome? Check this purchase. Never create a replacement key.`;
  $('#key-list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Key</th><th>Authority</th><th>Status</th><th>Action</th></tr></thead><tbody>${state.keys.map(key=>`<tr><td class="hash">${key.id==='bootstrap'?'Initial workspace key':esc(key.id.slice(0,16))+'…'}</td><td>${key.role==='agent'?'Agent · '+esc(key.customer):'Merchant application'}</td><td><span class="pill">${key.revoked?'revoked':'active'}</span></td><td>${key.revoked?'Access removed':`<button class="quiet danger" data-revoke-key="${esc(key.id)}" data-role="${key.role}">Revoke key</button>`}</td></tr>`).join('')}</tbody></table></div>`;
  $$('.table-wrap table').forEach(table=>{const headings=[...table.querySelectorAll('th')].map(th=>th.textContent);table.querySelectorAll('tbody tr').forEach(row=>[...row.children].forEach((cell,index)=>cell.dataset.label=headings[index]));});
  renderJourney(); updateControls(); economics(); jump(state.view);
}
function disable(id, disabled, reason) { const element=$('#'+id); element.disabled=state.busy||disabled; if(reason) element.title=reason; else element.removeAttribute('title'); }
function updateControls() {
  $$('button:not([data-view]):not([data-jump]), select, input, textarea').forEach(element=>element.disabled=state.busy||element.dataset.policyDisabled==='true');
  if(!state.data) return;
  const a=selected(), issuer=state.data.issuer, token=state.data.assets.find(x=>x.rail==='issuer'), pending=pendingPurchase();
  for(const el of $$('#asset-tabs button'))el.disabled=state.busy||!!pending;
  $('#work-asset').disabled=state.busy||!!pending;$('#job-amount').disabled=state.busy||!!pending;$('#work-input').disabled=state.busy||!!pending;
  $('#fund').hidden=a.rail==='issuer'; $('#fund-amount').hidden=a.rail==='issuer'; $('label[for=fund-amount]').hidden=a.rail==='issuer'; $('#fund-issuer').hidden=a.rail!=='issuer';
  $('#fund-hint').textContent=a.rail==='issuer'?'Your currency is issued against reserve evidence. Prepare its identity and reserves in Issuer lab.':'Add test funds to agent-01. These test settlements do not move real money.';
  let price, issue, redeem, payout;
  try { price=amount($('#job-amount').value,a.decimals); } catch {}
  const fee=price===undefined?0:Math.min(quoteFee(a),price);
  const funded=price!==undefined&&a.available>=price&&a.rail!=='issuer';
  $('#toggle-funding').hidden=!funded;$('#budget-ready').hidden=!funded;$('#budget-ready').textContent='Budget ready · '+money(a.available,a)+' test credits available.';
  $('#fund-controls').hidden=funded&&!state.fundingOpen;
  $('#reserve').textContent=pending?'Recover original reservation':price===undefined?'Reserve sample':'Reserve sample · '+money(price,a);
  $('#price-breakdown').innerHTML=price===undefined?'<p>Enter a valid price to see the split.</p>':`<div><span>Customer pays</span><strong>${money(price,a)}</strong></div><div><span>Platform fee <small>(included)</small></span><strong id="fee-display">${money(fee,a)}</strong></div><div class="split-total"><span>Merchant receives</span><strong>${money(price-fee,a)}</strong></div>`;
  $('#reserve-hint').textContent=price===undefined?'Use a positive amount in this asset’s units.':price>a.available?'Add funds or reduce the price. Available: '+money(a.available,a)+'.':'Only delivery charges the account. Reserving holds '+money(price,a)+'.';
  if(pending)$('#reserve-hint').textContent='The previous request needs recovery. Reuse its original quote and purchase identity; no new purchase will be created.';
  disable('reserve',!pending&&(price===undefined||price>a.available),'A valid price and enough available test credits are required.');
  try { amount($('#fund-amount').value,a.decimals); disable('fund',false); } catch { disable('fund',true,'Enter a valid amount to add.'); }
  try { payout=amount($('#payout-amount').value,a.decimals); } catch {}
  const issuerAsset=a.rail==='issuer';
  $('#payout-hint').textContent=issuerAsset?'Use redemption in Issuer lab for issued tokens.':payout>a.merchant?'Only '+money(a.merchant,a)+' is available. Reduce the request by '+money(payout-a.merchant,a)+'.':'A payout holds proceeds until settlement is recorded. Refunding paid-out work requires a funded reversal.';
  $('#use-payout-available').hidden=issuerAsset||a.merchant<=0;$('#use-payout-available').textContent='Use available: '+money(a.merchant,a);
  disable('request-payout',issuerAsset||payout===undefined||payout>a.merchant||!$('#payout-recipient').value.trim(),'Choose a bank or USDC asset and an amount within merchant proceeds.');
  const locked=issuer.identity_locked||issuer.reserve>0||issuer.liabilities>0;
  for(const id of ['issuer-name','issuer-symbol','issuer-currency']) $('#'+id).disabled=state.busy||locked;
  disable('save-issuer',locked,'Currency identity is fixed once the books are in use.');
  $('#identity-hint').textContent=locked?'Identity is fixed because these books are in use. Create another workspace for a different currency.':'Choose before recording reserves. Identity becomes fixed when the books are in use.';
  try { issue=amount($('#mint-amount').value,6); } catch {}
  const current=issuer.reserve_current && issuer.valid_until>Math.floor(Date.now()/1000);
  $('#mint-hint').textContent=!current?'Record current test reserve evidence first.':issue===undefined?'Enter a positive token amount.':issue>issuer.headroom?'This exceeds the issuance headroom of '+money(issuer.headroom,token)+'.':'After issuance: '+money(issuer.headroom-issue,token)+' of headroom remains.';
  disable('mint',!current||issue===undefined||issue>issuer.headroom);
  try { redeem=amount($('#redeem-amount').value,6); } catch {}
  const wholeMinor=redeem!==undefined&&redeem%10000===0;
  $('#redeem-hint').textContent=!wholeMinor?'Use whole fiat cents: at most two decimal places.':redeem>token.available?'The redemption exceeds your available token balance.':'Available to redeem: '+money(token.available,token)+'. The cash claim remains due after burning.';
  disable('redeem',!wholeMinor||redeem>token.available);
  disable('export-journal',!state.data.journal.some(event=>event.asset===a.id));
}
function economics() {
  try {
    const volume=amount($('#economics-volume').value,0), fee=amount($('#economics-fee').value,0,true), cost=amount($('#economics-cost').value,0,true), fixed=amount($('#economics-fixed').value,2,true);
    if(volume>1_000_000_000) throw new Error('Use up to one billion calls.');
    const variable=BigInt(volume)*(BigInt(fee)-BigInt(cost)), fixedMicros=BigInt(fixed)*10000n, contribution=variable-fixedMicros;
    const fmt=value=>new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value)/1000000);
    const spread=BigInt(fee)-BigInt(cost), breakEven=spread>0n?(fixedMicros+spread-1n)/spread:null;
    $('#economics-result').innerHTML=`<strong>${contribution<0n?'−':''}$${fmt(contribution<0n?-contribution:contribution)} <small>USD monthly contribution</small></strong><span>${breakEven===null?'Each additional call loses money or has zero contribution.':Number(breakEven).toLocaleString('en')+' calls to cover the entered fixed cost.'}</span><span>Fee revenue $${fmt(BigInt(volume)*BigInt(fee))} · variable costs $${fmt(BigInt(volume)*BigInt(cost))} · fixed costs $${fmt(fixedMicros)}</span>`;
    $('#economics-result').dataset.positive=String(contribution>=0n);
  } catch { $('#economics-result').textContent='Enter nonnegative costs and a positive whole number of calls. Maximum: one billion calls.'; }
}
function download(text, name, type) { const blob=new Blob([text],{type}), url=URL.createObjectURL(blob), anchor=document.createElement('a'); anchor.href=url; anchor.download=name; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
async function copy(text, fallback) { try { await navigator.clipboard.writeText(text); notice('Copied to clipboard.'); } catch { const selection=getSelection(), range=document.createRange(); range.selectNodeContents(fallback); selection.removeAllRanges(); selection.addRange(range); notice('Select and copy the highlighted text.'); } }
document.addEventListener('click', event => {
  const button=event.target.closest('button'); if(!button||button.disabled) return;
  if(button.dataset.view||button.dataset.jump) { jump(button.dataset.view||button.dataset.jump,true); return; }
  if(button.dataset.asset) { perform(async()=>{state.asset=button.dataset.asset;remember();});return; }
  if(button.dataset.copyId){copy(button.dataset.copyId,button);return;}
  if(button.id==='review-unknown'){perform(async()=>{state.allOperations=true;jump('work',true);});return;}
  if(button.dataset.closeReconcile!==undefined) { $('#reconcile-dialog').close(); return; }
  if(button.dataset.revokeKey) { $('#revoke-id').value=button.dataset.revokeKey; $('#revoke-role').value=button.dataset.role; $('#revoke-description').textContent='Remove '+button.dataset.role+' access for '+(button.dataset.revokeKey==='bootstrap'?'the initial workspace key':button.dataset.revokeKey)+'. Other keys and the owner session stay active.'; $('#revoke-dialog').showModal(); return; }
  if(button.dataset.op) {
    const op=button.dataset.op, action=button.dataset.action;
    if(action==='resolve') { $('#reconcile-operation').value=op; $('#evidence-reference').value=''; $('#reconcile-dialog').showModal(); $('#evidence-reference').focus(); return; }
    perform(async()=>{
      if(action==='view'){const data=await api(base()+'/deliveries/'+op);state.resultOp=op;$('#result-json').textContent=JSON.stringify(data,null,2);$('#result-summary').textContent=data.result?.schema==='hellabit.invoice-total-check.v1'?'Computed invoice total: '+number(data.result.computed_total_units,{decimals:2})+' '+data.result.currency+'. '+(data.result.matches_declared_total?'The declared total matches.':'The declared total differs.')+' Test-credit charge and result were stored together.':'Synthetic result and its test-credit receipt.';$('#result-dialog').showModal();}
      else if(action==='check'){const recorded=await api(base()+'/operations/'+op);notice('Existing purchase '+op+' is '+recorded.state+'. No replacement purchase was created.');state.focusOp=op;}
      else if(action==='download') { const data=await api(base()+'/deliveries/'+op); download(JSON.stringify(data,null,2),'hellabit-report-'+op+'.json','application/json'); notice('Report downloaded for '+op+'.'); }
      else { await post('operations/'+op+'/'+action); notice({run:'Result stored. One test-credit charge was recorded for this purchase.',refund:'Credits returned. The original charge stays recorded; future result retrieval is revoked.',uncertain:'Outcome unknown. Its budget stays held for review.',release:'Reservation released. No charge was recorded.'}[action]+' Operation '+op+'.'); }
    }); return;
  }
  if(button.dataset.payout) perform(async()=>{await post('issuer/payout',{redemption:button.dataset.payout,payment_reference:uid('payout')});notice('Test redemption payout recorded. The unpaid claim and reserve position were updated.');});
  if(button.dataset.merchantPayout) perform(async()=>{await post('payouts/'+button.dataset.merchantPayout+'/confirm',{payment_reference:uid('merchant-payout')});notice('Test payout settlement recorded. No real money moved.');});
});
$('#dismiss-feedback').onclick=()=>$('#action-feedback').hidden=true;
$('#workspace-select').addEventListener('change',()=>perform(async()=>{clearKey();state.tenant=$('#workspace-select').value;state.asset='CAD';$('#integrity').textContent='';}));
$('#work-asset').onchange=()=>perform(async()=>{state.asset=$('#work-asset').value;remember();});
const openWorkspace=()=>{$('#workspace-dialog').showModal();$('#workspace-name').focus();};
$('#new-workspace').onclick=openWorkspace; $('#first-workspace').onclick=openWorkspace; $('#close-dialog').onclick=()=>$('#workspace-dialog').close();
$('#workspace-form').onsubmit=event=>{event.preventDefault();perform(async()=>{const data=await api('/api/v1/workspaces',{method:'POST',body:{name:$('#workspace-name').value,country:$('#workspace-country').value}});$('#workspace-dialog').close();clearKey();state.asset='CAD';await workspaces(data.tenant);jump('overview');notice('Workspace created. Create a merchant or agent key in Developers when you are ready.');});};
$('#fund').onclick=()=>perform(async()=>{const a=selected();await post('sandbox/fund',{customer:'agent-01',asset:a.id,amount:amount($('#fund-amount').value,a.decimals),reference:uid('fund')});notice('Test funds added to agent-01. No real money moved.');});
$('#reserve').onclick=()=>perform(async()=>{
  let original=pendingPurchase();
  if(original){
    try{const known=await post('operations/lookup',{key:original.key});clearPending();state.asset=known.asset;state.focusOp=known.id;notice('Recovered original purchase '+known.id+'. Continue that purchase below.');return;}
    catch(error){if(error.code!=='operation_not_found')throw error;}
  }else{
    const a=selected(),n=amount($('#job-amount').value,a.decimals);let input;
    try{input=JSON.parse($('#work-input').value);}catch{throw new Error('Enter valid JSON for the invoice-check input. No reservation was submitted.');}
    const q=await post('quotes',{asset:a.id,amount:n,fee:Math.min(quoteFee(a),n),resource:'invoice-check',input});
    original={key:uid('invoice'),quote:q.quote,asset:a.id,customer:'agent-01'};savePending(original);
  }
  try{const op=await post('operations',{customer:original.customer,quote:original.quote,key:original.key});clearPending();state.asset=op.asset;state.focusOp=op.id;notice('Reserved '+money(op.amount,selected())+' for '+op.id+'. Not charged. Run the check below to store its result and charge.');}
  catch(error){if(error.code==='quote_expired_or_missing')clearPending();throw error;}
});
$('#request-payout').onclick=()=>perform(async()=>{const a=selected();await post('payouts',{asset:a.id,amount:amount($('#payout-amount').value,a.decimals),recipient:$('#payout-recipient').value,key:uid('merchant-payout')});notice('Merchant proceeds held for a test payout. Record its settlement to complete it.');});
function showKey(value) { $('#one-time-key').value=value; $('#one-time-key').type='password'; $('#key-result').hidden=false; $('#reveal-key').textContent='Show key'; }
$('#merchant-key').onclick=()=>perform(async()=>{const tenant=state.tenant;const result=await post('merchant-keys');if(state.view!=='developer'||state.tenant!==tenant){await api('/api/v1/workspaces/'+tenant+'/merchant-keys/'+result.id+'/revoke',{method:'POST',body:{}});clearKey();notice('The new key was revoked because you left Developers before it was displayed.');return;}showKey(result.api_key);notice('Merchant key created. Copy it now; it is shown once.');});
$('#agent-key').onclick=()=>perform(async()=>{const tenant=state.tenant;const result=await post('agent-keys',{customer:'agent-01'});if(state.view!=='developer'||state.tenant!==tenant){await api('/api/v1/workspaces/'+tenant+'/agent-keys/'+result.id+'/revoke',{method:'POST',body:{}});clearKey();notice('The new key was revoked because you left Developers before it was displayed.');return;}showKey(result.api_key);notice('Agent key created for customer agent-01. It cannot change prices or approve settlement.');});
$('#reveal-key').onclick=()=>{const field=$('#one-time-key');field.type=field.type==='password'?'text':'password';$('#reveal-key').textContent=field.type==='password'?'Show key':'Hide key';};
$('#copy-key').onclick=async()=>{try{await navigator.clipboard.writeText($('#one-time-key').value);notice('Key copied. Store it in your server-side secret manager.');}catch{$('#one-time-key').focus();$('#one-time-key').select();notice('The one-time key is selected. Use your copy command.');}};
$('#clear-key').onclick=()=>{clearKey();notice('One-time key cleared from the page.');};
window.addEventListener('pagehide',clearKey);
$('#close-revoke').onclick=()=>$('#revoke-dialog').close();
$('#revoke-form').onsubmit=event=>{event.preventDefault();perform(async()=>{await post($('#revoke-role').value+'-keys/'+$('#revoke-id').value+'/revoke');$('#revoke-dialog').close();clearKey();notice('Key revoked. Requests using it are denied immediately.');});};
$('#copy-sdk').onclick=()=>copy($('#sdk-example').textContent,$('#sdk-example'));
$('#issuer-config').onsubmit=event=>{event.preventDefault();perform(async()=>{await post('issuer/configure',{name:$('#issuer-name').value,symbol:$('#issuer-symbol').value,currency:$('#issuer-currency').value});notice('Currency identity saved.');});};
$('#attest').onclick=()=>perform(async()=>{await post('issuer/attest',{fiat_minor_units:amount($('#reserve-amount').value,2),reference:uid('reserve')});notice('Test reserve evidence recorded. Issuance authority expires in 24 hours.');});
$('#mint').onclick=()=>perform(async()=>{await post('issuer/mint',{customer:'agent-01',amount:amount($('#mint-amount').value,6),reference:uid('issue')});notice('Test tokens issued to agent-01 within the reserve limit.');});
$('#redeem').onclick=()=>perform(async()=>{await post('issuer/redeem',{customer:'agent-01',amount:amount($('#redeem-amount').value,6),reference:uid('redeem')});notice('Tokens burned. The cash claim stays unpaid until settlement is recorded.');});
$('#reconcile-form').onsubmit=event=>{event.preventDefault();perform(async()=>{await post('operations/'+$('#reconcile-operation').value+'/'+$('#reconcile-outcome').value,{evidence_reference:$('#evidence-reference').value});$('#reconcile-dialog').close();notice('Your explicit test outcome was recorded. This is not independent settlement verification.');});};
$('#reconcile').onclick=()=>perform(async()=>{const report=await api(base()+'/reconcile');$('#integrity').textContent=report.ok?`${report.journal_count} records checked. Internal ledger balances and hashes agree. External settlement is not verified by this check.`:'The internal ledger needs investigation.';});
$$('form[data-enroll]').forEach(form=>form.onsubmit=event=>{event.preventDefault();const fields=Object.fromEntries(new FormData(form)),rail=form.dataset.enroll;if(rail==='usdc')fields.chain_id=84532;perform(async()=>{await post('enroll/'+rail,fields);notice('Preparation saved for '+rail+'. It has not been externally verified or connected.');});});
$('#export-journal').onclick=()=>{
  const a=selected(), events=state.activity.journal;
  const csvCell=value=>'"'+String(value).replaceAll('"','""')+'"';
  const rows=[['sequence','movement','asset','integer_units','decimals','utc','reference','sha256'],...events.map(event=>[event.seq,event.kind,event.asset,event.amount,a.decimals,new Date(event.created*1000).toISOString(),event.reference,event.digest])];
  download(rows.map(row=>row.map(csvCell).join(',')).join('\r\n')+'\r\n','hellabit-'+label(a)+'-visible-movements.csv','text/csv;charset=utf-8');
  notice('Exported '+events.length+' visible '+label(a)+' movements. This export is not the full ledger.');
};
for(const id of ['job-amount','fund-amount','payout-amount','payout-recipient','mint-amount','redeem-amount']) $('#'+id).addEventListener('input',updateControls);
for(const id of ['economics-volume','economics-fee','economics-cost','economics-fixed']) $('#'+id).addEventListener('input',economics);
async function loadPlan() {
  state.plan=await api('/api/v1/connection-plan');
  $('#partner-plan').innerHTML=state.plan.partners.map(partner=>`<article class="panel partner-card"><div class="partner-role">${esc(partner.role)}</div><h2>${esc(partner.name)}</h2><p>${esc(partner.reason)}</p><p class="partner-condition">${esc(partner.condition)}</p><a class="source-link" href="${esc(partner.source)}" target="_blank" rel="noopener noreferrer">Provider source ↗</a><span class="connection-status">${partner.enquiry_delivery==='queued'?'ENQUIRY QUEUED · FIT UNCONFIRMED':'ENQUIRY SENT · FIT UNCONFIRMED'}</span>${partner.enquiry_delivery==='queued'?'<p class="partner-condition">Delivery is pending after a mail-domain DNSSEC error. No duplicate enquiry has been submitted.</p>':''}</article>`).join('');
  $('#plan-reviewed').textContent='Partner research reviewed '+state.plan.reviewed_at+'. Eligibility, pricing and program approval must be confirmed with each provider.';
}
$('#nav-toggle').onclick=()=>{const open=$('#primary-nav').dataset.open!=='true';$('#primary-nav').dataset.open=String(open);$('#nav-toggle').setAttribute('aria-expanded',String(open));};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('#primary-nav').dataset.open==='true'){closeMenu();$('#nav-toggle').focus();}});
$('#all-operations').onchange=()=>perform(async()=>{state.allOperations=$('#all-operations').checked;});
$('#toggle-funding').onclick=()=>{state.fundingOpen=!state.fundingOpen;updateControls();};
$('#use-payout-available').onclick=()=>{const a=selected();$('#payout-amount').value=(a.merchant/10**a.decimals).toFixed(a.decimals);updateControls();};
function restoreResultFocus(){const target=$(`[data-op="${CSS.escape(state.resultOp||'')}"][data-action="view"]`);if(target)target.focus({preventScroll:true});}
function closeResult(){ $('#result-dialog').close();restoreResultFocus(); }
$('#close-result').onclick=closeResult;
$('#result-dialog').addEventListener('cancel',event=>{event.preventDefault();closeResult();});
$('#result-dialog').addEventListener('close',restoreResultFocus);
$('#download-diagnostics').onclick=()=>perform(async()=>{const data=await api(base()+'/diagnostics');download(JSON.stringify(data,null,2),'hellabit-redacted-diagnostics.json','application/json');notice('Downloaded runtime and internal-check diagnostics. No keys or customer inputs were included.');});
$('#today').textContent=new Intl.DateTimeFormat('en-CA',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date()).toUpperCase()+' UTC';
Promise.all([workspaces(),loadPlan()]).catch(error=>failure(error.message));
