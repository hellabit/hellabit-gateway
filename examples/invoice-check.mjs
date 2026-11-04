import { Hellabit, HellabitError } from '../sdk/hellabit.mjs';

// Set these through your normal scoped secret manager, not command arguments.
const gateway=new Hellabit({baseURL:process.env.HELLABIT_URL,apiKey:process.env.HELLABIT_MERCHANT_KEY});
const workspace=process.env.HELLABIT_WORKSPACE;
const key=process.env.HELLABIT_PURCHASE_KEY||'invoice-check-example-001';
const input={currency:'CAD',items:[{quantity:2,unit_amount_units:1500},{quantity:1,unit_amount_units:500}],tax_units:175,declared_total_units:3675};
try {
  let purchase;
  try { purchase=await gateway.lookup(workspace,key); }
  catch(error) {
    if(!(error instanceof HellabitError)||error.code!=='operation_not_found')throw error;
    const quote=await gateway.quote(workspace,{asset:'CAD',amount:100,fee:1,resource:'invoice-check',input});
    purchase=await gateway.reserve(workspace,{customer:'agent-01',quote:quote.quote,key});
  }
  if(purchase.state==='reserved')await gateway.run(workspace,purchase.id);
  else if(purchase.state!=='committed')throw new HellabitError('purchase_requires_observed_outcome',409);
  const result=await gateway.delivery(workspace,purchase.id);
  console.log(JSON.stringify({purchase:purchase.id,result},null,2));
} catch(error) {
  console.error(JSON.stringify({error:error.code||'request_outcome_unavailable',recovery:'Keep the original purchase key. Inspect that purchase before creating another.'}));
  process.exitCode=1;
}
