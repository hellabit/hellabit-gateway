import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
import threading
import os
import subprocess
import sys
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from hellabit.api import Api
from hellabit.identity import Identity
from hellabit.ledger import Ledger,Error,digest
from hellabit.rails import SandboxBank

ISSUER,AUDIENCE="https://identity.example.invalid","hellabit-api"

class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_policy_gates_concurrent_budgets_and_exact_approvals(self):
        root=Path(__file__).resolve().parents[1];base='/api/v1/workspaces/'+self.tenant
        for asset in ('CAD','USD'):SandboxBank(self.book).fund(self.tenant,'agent-01',asset,500,'agent-funding')
        grant=self.identity.enroll(self.tenant,'budget-agent','agent',int(time.time())+3600,customer='agent-01')
        token=self.token('budget-agent')
        quote=lambda amount,asset='CAD':self.book.create_quote(self.tenant,asset,amount,1)['quote']
        async def purchase(q,key):return await self.request('POST',base+'/operations',{'customer':'agent-01','quote':q,'key':key},token=token)
        q=quote(60)
        self.assertEqual((await purchase(q,'before-policy'))[1]['error'],'agent_spending_policy_required')
        def cli(*args):
            result=subprocess.run([sys.executable,str(root/'scripts/operate.py'),'--state',str(self.book.path),'--tenant',self.tenant,'agent-policy',args[0],'--principal',grant['id'],*args[1:]],capture_output=True,text=True,timeout=10,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'})
            self.assertEqual(result.returncode,0,result.stderr+result.stdout);return json.loads(result.stdout)['result']
        terms={'asset':'CAD','resource':'report','recipient':'workspace-merchant','per_purchase':100,'daily':100,'approval_above':70,'expires':int(time.time())+1800}
        path=Path(self.tmp.name)/'agent-policy.json';path.write_text(json.dumps(terms));path.chmod(0o600)
        self.assertEqual(cli('set','--file',str(path),'--expected-version','0')['version'],1)
        self.assertEqual((await purchase(quote(10,'USD'),'wrong-asset'))[1]['error'],'agent_purchase_scope_forbidden')
        responses=await asyncio.gather(purchase(q,'race-a'),purchase(q,'race-b'))
        self.assertEqual(sorted(r[0] for r in responses),[200,403]);self.assertIn('agent_daily_limit_exceeded',[r[1].get('error')for r in responses])
        terms['approval_above']=20;path.write_text(json.dumps(terms))
        cli('set','--file',str(path),'--expected-version','1')
        q=quote(30)
        self.assertEqual((await purchase(q,'approved-command'))[1]['error'],'exact_agent_purchase_approval_required')
        cli('approve','--quote',q,'--key','approved-command')
        self.assertEqual((await purchase(q,'substituted-command'))[1]['error'],'exact_agent_purchase_approval_required')
        status,row=await purchase(q,'approved-command');self.assertEqual(status,200)
        self.assertTrue((await purchase(q,'approved-command'))[1]['replayed'])
        # Agent cannot spend a merchant's reservation or execute a superseded one.
        unowned=self.book.reserve(self.tenant,'agent-01',quote(1),'merchant-reservation')
        self.assertEqual((await self.request('POST',base+'/operations/'+unowned['id']+'/run',{},token=token))[1]['error'],'agent_reservation_not_owned')
        cli('set','--file',str(path),'--expected-version','2')
        self.assertEqual((await self.request('POST',base+'/operations/'+row['id']+'/run',{},token=token))[1]['error'],'agent_reservation_authority_changed')
        self.assertTrue(self.book.reconcile(self.tenant)['ok'])

    async def test_revocation_after_route_admission_is_rechecked_inside_financial_transaction(self):
        base='/api/v1/workspaces/'+self.tenant
        SandboxBank(self.book).fund(self.tenant,'agent-01','CAD',100,'commit-guard-funding')
        quote=self.book.create_quote(self.tenant,'CAD',100,1)['quote']
        grant=self.identity.enroll(self.tenant,'commit-merchant','merchant',int(time.time())+3600)
        original=self.app.route
        def revoke_then_route(*arguments):
            thread=threading.Thread(target=lambda:self.identity.revoke(self.tenant,grant['id']))
            thread.start();thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            return original(*arguments)
        with patch.object(self.app,'route',side_effect=revoke_then_route):
            status,_=await self.request('POST',base+'/operations',{'customer':'agent-01','quote':quote,'key':'commit-guard'},token=self.token('commit-merchant'))
        self.assertEqual(status,403)
        with self.book.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)

    async def test_accepted_clock_skew_never_transfers_old_read_authority_to_new_role(self):
        now=int(time.time());base='/api/v1/workspaces/'+self.tenant
        grant=self.identity.enroll(self.tenant,'skewed-reader','read-only',now+3600)
        old_tokens=[self.token('skewed-reader',iat=now+offset) for offset in (-14,0,10,14)]
        for token in old_tokens:self.assertEqual((await self.request('GET',base,token=token))[0],200)
        revoked=self.identity.revoke(self.tenant,grant['id'])
        self.identity.change_grant(self.tenant,grant['id'],'reinstate',revoked['version'],expires=now+3600,role='merchant')
        for token in old_tokens:
            status,result=await self.request('POST',base+'/quotes',{'asset':'CAD','amount':100,'fee':1},token=token)
            self.assertEqual((status,result['error']),(403,'grant_generation_mismatch'))
        self.assertEqual((await self.request('POST',base+'/quotes',{'asset':'CAD','amount':100,'fee':1},token=self.token('skewed-reader')))[0],200)

    async def test_revocation_during_body_upload_prevents_a_new_financial_reservation(self):
        base='/api/v1/workspaces/'+self.tenant
        SandboxBank(self.book).fund(self.tenant,'agent-01','CAD',100,'fund-before-upload')
        quote=self.book.create_quote(self.tenant,'CAD',100,1)['quote']
        grant=self.identity.enroll(self.tenant,'uploading-merchant','merchant',int(time.time())+3600)
        data=json.dumps({'customer':'agent-01','quote':quote,'key':'revoked-during-upload'}).encode()
        scope={'type':'http','method':'POST','path':base+'/operations','query_string':b'',
               'headers':[(b'host',b'api.example.invalid'),(b'authorization',('Bearer '+self.token('uploading-merchant')).encode()),
                          (b'content-type',b'application/json'),(b'content-length',str(len(data)).encode())]}
        async def receive():
            self.identity.revoke(self.tenant,grant['id'])
            return {'type':'http.request','body':data,'more_body':False}
        sent=[]
        async def send(message):sent.append(message)
        await self.app(scope,receive,send)
        self.assertEqual(sent[0]['status'],403)
        with self.book.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)
            self.assertEqual(self.book._balance(db,self.tenant,'CAD','customer:agent-01'),100)

    async def test_grants_renew_after_expiry_and_reinstatement_requires_a_fresh_token(self):
        now=int(time.time())
        grant=self.identity.enroll(self.tenant,"long-lived-customer","merchant",now+10)
        old_token=self.token("long-lived-customer",iat=now+10)
        self.book.clock=lambda:now+32*86400
        renewed=self.identity.change_grant(self.tenant,grant["id"],"renew",grant["version"],expires=now+33*86400)
        self.assertEqual(renewed["version"],2)
        with self.assertRaisesRegex(Error,"grant_version_conflict"):
            self.identity.change_grant(self.tenant,grant["id"],"renew",1,expires=now+33*86400)
        self.book.clock=lambda:now
        revoked=self.identity.revoke(self.tenant,grant["id"])
        with self.assertRaisesRegex(Error,"explicit_reinstatement_required"):
            self.identity.change_grant(self.tenant,grant["id"],"renew",revoked["version"],expires=now+100)
        restored=self.identity.change_grant(self.tenant,grant["id"],"reinstate",revoked["version"],expires=now+100)
        base="/api/v1/workspaces/"+self.tenant
        self.assertEqual((await self.request("GET",base,token=old_token))[0],403)
        self.assertEqual((await self.request("GET",base,token=self.token("long-lived-customer")))[0],200)

    async def test_internal_errors_have_a_safe_correlated_record(self):
        secret="synthetic-sensitive-message-never-log"
        with patch.object(self.app,"route",side_effect=RuntimeError(secret)),self.assertLogs("hellabit.api",level="ERROR") as logs:
            status,result=await self.request("GET","/api/v1/workspaces/"+self.tenant)
        self.assertEqual(status,500);self.assertEqual(result["error"],"internal_error")
        self.assertIn(result["correlation_id"],logs.output[0]);self.assertNotIn(secret,logs.output[0])
        self.assertNotIn(self.tenant,logs.output[0])

    async def test_authenticated_readiness_distinguishes_quarantine_from_liveness(self):
        route="/api/v1/workspaces/"+self.tenant+"/ready"
        self.assertEqual((await self.request("GET",route))[0],200)
        with self.book.db() as db:db.execute("INSERT INTO settings VALUES('recovery_quarantine','test')")
        self.assertEqual((await self.request("GET",route))[0],423)
        self.assertEqual((await self.request("GET","/api/health",token=False))[1]["check"],"liveness")

    async def test_slow_chunked_body_has_one_total_deadline(self):
        chunks=0
        async def receive():
            nonlocal chunks
            await asyncio.sleep(0.03);chunks+=1
            return {"type":"http.request","body":b"{" if chunks==1 else b"}","more_body":chunks==1}
        scope={"type":"http","method":"POST","path":"/api/v1/workspaces/"+self.tenant+"/quotes",
            "query_string":b"","headers":[(b"host",b"api.example.invalid"),
                (b"authorization",("Bearer "+self.token()).encode()),(b"content-type",b"application/json"),(b"content-length",b"2")]}
        sent=[]
        async def send(message):sent.append(message)
        with patch("hellabit.api.BODY_TIMEOUT",0.04):await self.app(scope,receive,send)
        self.assertEqual(sent[0]["status"],408)
        self.assertEqual(json.loads(sent[1]["body"])["error"],"request_body_timeout")

    async def test_read_requests_with_a_body_are_rejected_before_health_or_state_access(self):
        self.assertEqual((await self.request("GET","/api/health",token=False,extra=[(b"content-length",b"1")]))[0],400)

    async def test_signed_duplicate_claims_and_stale_operator_authentication_are_rejected(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        now=int(time.time());base="/api/v1/workspaces/"+self.tenant
        header=b'{"alg":"RS256","kid":"key-1","typ":"at+jwt"}'
        payload=('{"iss":"'+ISSUER+'","aud":"'+AUDIENCE+'","sub":"merchant-subject",'
                 '"sub":"merchant-subject","iat":'+str(now-1)+',"exp":'+str(now+300)+'}').encode()
        encode=lambda b:base64.urlsafe_b64encode(b).rstrip(b"=")
        message=encode(header)+b"."+encode(payload)
        signature=self.signer.sign(message,padding.PKCS1v15(),hashes.SHA256())
        token=(message+b"."+encode(signature)).decode()
        self.assertEqual((await self.request("GET",base,token=token))[0],401)
        self.identity.enroll(self.tenant,"operator-subject","operator",now+3600)
        for auth_time in (None,now-901,now+60):
            self.assertEqual((await self.request("GET",base+"/ops",token=self.token("operator-subject",amr=["mfa"],auth_time=auth_time)))[0],403)
        self.assertEqual((await self.request("GET",base+"/ops",token=self.token("operator-subject",amr=["mfa"])))[0],200)

    @classmethod
    def setUpClass(cls):
        cls.signer=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.public=cls.signer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3")
        self.tenant=self.book.create_tenant("API Merchant")["tenant"]
        self.other=self.book.create_tenant("Other API Merchant")["tenant"]
        self.identity=Identity(self.book,issuer=ISSUER,audience=AUDIENCE,public_keys={"key-1":self.public})
        self.identity.enroll(self.tenant,"merchant-subject","merchant",int(time.time())+3600)
        self.app=Api(self.book,self.identity,allowed_hosts={"api.example.invalid"})

    def tearDown(self):self.tmp.cleanup()

    def token(self,subject="merchant-subject",**claims):
        now=int(time.time())
        with self.book.db() as db:
            row=db.execute("SELECT grant_id FROM api_principals WHERE issuer=? AND subject_hash=?",(ISSUER,digest(subject))).fetchone()
        payload={"hellabit_grant":row[0] if row else "unbound","iss":ISSUER,"aud":AUDIENCE,"sub":subject,"iat":now-1,"exp":now+300,"auth_time":now,**claims}
        return jwt.encode(payload,self.signer,algorithm="RS256",headers={"kid":"key-1","typ":"at+jwt"})

    async def request(self,method,path,body=None,token=None,extra=None,raw=None):
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else b""
        headers=[(b"host",b"api.example.invalid")]
        if token is not False:headers.append((b"authorization",("Bearer "+(token or self.token())).encode()))
        if method=="POST":headers.extend([(b"content-type",b"application/json"),(b"content-length",str(len(data)).encode())])
        headers.extend(extra or [])
        scope={"type":"http","method":method,"path":path,"raw_path":path.encode(),"query_string":b"","headers":headers}
        async def receive():return {"type":"http.request","body":data,"more_body":False}
        sent=[]
        async def send(message):sent.append(message)
        await self.app(scope,receive,send)
        return sent[0]["status"],json.loads(sent[1]["body"])

    async def test_health_exposes_no_credentials_or_financial_approval(self):
        status,result=await self.request("GET","/api/health",token=False)
        self.assertEqual(status,200);self.assertFalse(result["live_payments_enabled"])
        self.assertNotIn(self.tenant,str(result))

    async def test_host_bindings_cannot_be_a_string_wildcard_or_invalid_port(self):
        for hosts in ("api.example.invalid",["*.example.invalid"],["api.example.invalid:99999"],["api.example.invalid:0"]):
            with self.subTest(hosts=hosts):
                with self.assertRaisesRegex(Error,"invalid_api_host_binding"):Api(self.book,self.identity,allowed_hosts=hosts)

    async def test_local_subject_grants_override_forged_tenant_and_role_claims(self):
        token=self.token(tenant=self.other,role="operator",admin=True)
        self.assertEqual((await self.request("GET","/api/v1/workspaces/"+self.tenant,token=token))[0],200)
        self.assertEqual((await self.request("GET","/api/v1/workspaces/"+self.other,token=token))[0],403)
        self.assertEqual((await self.request("GET","/api/v1/workspaces/"+self.tenant+"/ops",token=token))[0],403)

    async def test_wrong_issuer_audience_expiry_or_unknown_subject_are_rejected(self):
        for token in [self.token(iss="https://wrong.example"),self.token(aud="other-api"),
                      self.token(exp=int(time.time())-60,iat=int(time.time())-300),self.token("not-enrolled")]:
            with self.subTest():
                status,_=await self.request("GET","/api/v1/workspaces/"+self.tenant,token=token)
                self.assertIn(status,(401,403))

    async def test_key_substitution_and_remote_key_header_are_rejected(self):
        now=int(time.time())
        claims={"iss":ISSUER,"aud":AUDIENCE,"sub":"merchant-subject","iat":now-1,"exp":now+100}
        symmetric=jwt.encode(claims,b"synthetic-hmac-key-not-an-rsa-key"*2,algorithm="HS256",headers={"kid":"key-1"})
        remote=jwt.encode(claims,self.signer,algorithm="RS256",headers={"kid":"key-1","jku":"http://127.0.0.1/keys","typ":"at+jwt"})
        for token in [symmetric,remote]:
            self.assertEqual((await self.request("GET","/api/v1/workspaces/"+self.tenant,token=token))[0],401)

    async def test_operator_needs_signed_mfa_assertion_and_read_only_cannot_write(self):
        self.identity.enroll(self.tenant,"operator-subject","operator",int(time.time())+3600)
        self.identity.enroll(self.tenant,"reader-subject","read-only",int(time.time())+3600)
        base="/api/v1/workspaces/"+self.tenant
        self.assertEqual((await self.request("GET",base+"/ops",token=self.token("operator-subject")))[0],403)
        self.assertEqual((await self.request("GET",base+"/ops",token=self.token("operator-subject",amr=["mfa"])))[0],200)
        self.assertEqual((await self.request("POST",base+"/quotes",{"asset":"CAD","amount":100,"fee":1},
                                             token=self.token("reader-subject")))[0],403)

    async def test_subject_revocation_and_persistent_rate_limit_apply_immediately(self):
        grant=self.identity.enroll(self.tenant,"limited","merchant",int(time.time())+3600,rate_limit=2)
        token=self.token("limited");base="/api/v1/workspaces/"+self.tenant
        self.assertEqual((await self.request("GET",base,token=token))[0],200)
        self.assertEqual((await self.request("GET",base,token=token))[0],200)
        self.assertEqual((await self.request("GET",base,token=token))[0],429)
        self.identity.revoke(self.tenant,grant["id"])
        self.assertEqual((await self.request("GET",base,token=token))[0],403)

    async def test_purchase_result_journey_works_without_exposing_owner_controls(self):
        base="/api/v1/workspaces/"+self.tenant
        SandboxBank(self.book).fund(self.tenant,"agent-01","CAD",100,"private-fixture-funding")
        status,quote=await self.request("POST",base+"/quotes",{"asset":"CAD","amount":100,"fee":1})
        self.assertEqual(status,200)
        _,op=await self.request("POST",base+"/operations",{"customer":"agent-01","quote":quote["quote"],"key":"api-purchase"})
        self.assertEqual((await self.request("POST",base+"/operations/"+op["id"]+"/run",{}))[0],200)
        self.assertEqual((await self.request("GET",base+"/deliveries/"+op["id"]))[0],200)
        self.assertEqual((await self.request("POST",base+"/sandbox/fund",{"amount":100}))[0],403)
        self.assertEqual((await self.request("POST",base+"/merchant-keys",{}))[0],403)
        self.assertEqual((await self.request("POST",base+"/issuer/mint",{}))[0],403)
        self.assertEqual((await self.request("POST",base+"/operations/"+op["id"]+"/refund",{}))[0],200)
        self.assertEqual((await self.request("GET",base+"/deliveries/"+op["id"]))[0],403)

    async def test_browser_ambient_credentials_duplicate_headers_and_duplicate_json_are_denied(self):
        base="/api/v1/workspaces/"+self.tenant
        for headers in [[(b"origin",b"https://untrusted.example")],[(b"cookie",b"ambient=1")],
                        [(b"host",b"second.example")]]:
            self.assertEqual((await self.request("GET",base,extra=headers))[0],403 if len(headers)==1 and headers[0][0]!=b"host" else 400)
        self.assertEqual((await self.request("POST",base+"/quotes",raw=b'{"asset":"CAD","asset":"USD"}'))[0],400)

    async def test_agent_identity_cannot_spend_or_read_another_customer(self):
        self.book.add_customer(self.tenant,"other-customer")
        self.identity.enroll(self.tenant,"agent-subject","agent",int(time.time())+3600,customer="agent-01")
        base="/api/v1/workspaces/"+self.tenant
        SandboxBank(self.book).fund(self.tenant,"other-customer","CAD",100,"other-funding")
        quote=self.book.create_quote(self.tenant,"CAD",100,1)["quote"]
        op=self.book.reserve(self.tenant,"other-customer",quote,"other-purchase")
        token=self.token("agent-subject")
        self.assertEqual((await self.request("GET",base+"/operations/"+op["id"],token=token))[0],403)
        self.assertEqual((await self.request("POST",base+"/operations",
            {"customer":"other-customer","quote":quote,"key":"forbidden"},token=token))[0],403)
