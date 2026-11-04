export interface Quote {quote:string;asset:string;amount:number;fee:number;resource:string;input_hash:string|null;expires_in:number}
export interface Operation {id:string;asset:string;amount:number;fee:number;state:'reserved'|'uncertain'|'committed'|'released';result_hash:string|null;replayed?:boolean}
export class HellabitError extends Error {code:string;status:number}
export class Hellabit {
  constructor(options:{baseURL:string;apiKey:string;fetch?:typeof fetch;timeout?:number});
  status(tenant:string):Promise<Record<string,unknown>>;
  quote(tenant:string,price:{asset:string;amount:number;fee:number;resource?:string;input?:Record<string,unknown>}):Promise<Quote>;
  reserve(tenant:string,operation:{customer:string;quote:string;key:string}):Promise<Operation>;
  lookup(tenant:string,key:string):Promise<Operation>;
  operation(tenant:string,id:string):Promise<Operation>;
  run(tenant:string,id:string):Promise<{operation:string;result_hash:string;replayed:boolean}>;
  delivery(tenant:string,id:string):Promise<Record<string,unknown>>;
  refund(tenant:string,id:string):Promise<{journal:number;replayed:boolean}>;
}
