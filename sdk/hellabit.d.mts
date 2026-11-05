export interface Quote {quote:string;asset:string;amount:number;fee:number;resource:string;input_hash:string|null;expires_in:number}
export interface Operation {id:string;asset:string;amount:number;fee:number;state:'reserved'|'uncertain'|'committed'|'released';result_hash:string|null;replayed?:boolean}
export interface PayoutIntent {
  id:string; payout:string; asset:string; amount:number; connection:string; external_key:string;
  state:'prepared'|'submitting'|'unknown'|'awaiting_settlement'|'confirmed'|'failed';
  reference:string|null; original_settled_units:number; returned_units:number; net_paid_units:number;
  effective_outcome:string; updated:number;
}
export interface PayoutQueue {
  intents:PayoutIntent[]; counts:Record<string,number>; mode:'sandbox';
  attention:{connection:string;asset:string;held_count:number;held_units:number;oldest_created:number}[];
  live_submission_implemented:false; observed_at:number;
}
export class HellabitError extends Error {code:string;status:number}
export type HellabitAuthentication = {apiKey:string;getAccessToken?:never}|{apiKey?:never;getAccessToken:(signal:AbortSignal)=>string|Promise<string>};
export class Hellabit {
  constructor(options:{baseURL:string;fetch?:typeof fetch;timeout?:number}&HellabitAuthentication);
  status(tenant:string):Promise<Record<string,unknown>>;
  quote(tenant:string,price:{asset:string;amount:number;fee:number;resource?:string;input?:Record<string,unknown>}):Promise<Quote>;
  reserve(tenant:string,operation:{customer:string;quote:string;key:string}):Promise<Operation>;
  lookup(tenant:string,key:string):Promise<Operation>;
  operation(tenant:string,id:string):Promise<Operation>;
  run(tenant:string,id:string):Promise<{operation:string;result_hash:string;replayed:boolean}>;
  delivery(tenant:string,id:string):Promise<Record<string,unknown>>;
  refund(tenant:string,id:string):Promise<{journal:number;replayed:boolean}>;
  payouts(tenant:string):Promise<PayoutQueue>;
  payout(tenant:string,intent:string):Promise<PayoutIntent>;
}
