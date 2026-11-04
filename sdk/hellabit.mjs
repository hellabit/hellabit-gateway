export class HellabitError extends Error {
  constructor(code,status){super(code);this.name='HellabitError';this.code=code;this.status=status;}
}
export class Hellabit {
  #key;
  constructor({baseURL,apiKey,fetch:transport=globalThis.fetch,timeout=10000}){
    const url=new URL(baseURL);
    if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||!['https:','http:'].includes(url.protocol)||(url.protocol==='http:'&&!['127.0.0.1','localhost','[::1]'].includes(url.hostname)))throw new Error('HTTPS or loopback base URL required');
    if(typeof apiKey!=='string'||!/^hb_(?:agent_)?test_[A-Za-z0-9_-]{5,96}$/.test(apiKey))throw new Error('A workspace sandbox key is required');
    if(!Number.isInteger(timeout)||timeout<100||timeout>60000)throw new Error('Invalid request timeout');
    this.origin=url.origin;this.#key=apiKey;this.transport=transport;this.timeout=timeout;
  }
  async request(tenant,path,method='GET',body){
    if(!/^[A-Za-z0-9_.:-]{1,96}$/.test(tenant))throw new Error('Invalid workspace');
    const response=await this.transport(this.origin+'/api/v1/workspaces/'+encodeURIComponent(tenant)+path,{method,headers:{Authorization:'Bearer '+this.#key,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(this.timeout),redirect:'error',credentials:'omit'});
    const result=await response.json();if(!response.ok)throw new HellabitError(result.error||'request_failed',response.status);return result;
  }
  status(tenant){return this.request(tenant,'');}
  quote(tenant,price){return this.request(tenant,'/quotes','POST',price);}
  reserve(tenant,operation){return this.request(tenant,'/operations','POST',operation);}
  lookup(tenant,key){return this.request(tenant,'/operations/lookup','POST',{key});}
  operation(tenant,id){return this.request(tenant,'/operations/'+this.id(id));}
  run(tenant,id){return this.request(tenant,'/operations/'+this.id(id)+'/run','POST',{});}
  delivery(tenant,id){return this.request(tenant,'/deliveries/'+this.id(id));}
  refund(tenant,id){return this.request(tenant,'/operations/'+this.id(id)+'/refund','POST',{});}
  id(value){if(typeof value!=='string'||!/^op_[a-f0-9]{24}$/.test(value))throw new Error('Invalid operation');return value;}
}
