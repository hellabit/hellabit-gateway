import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from hellabit.ledger import Ledger, Error
from hellabit.server import Server,Handler


class HttpTests(unittest.TestCase):
    def test_ops_response_holds_one_snapshot_across_settlement_and_return(self):
        from hellabit.fixtures import SandboxProvider
        from hellabit.operations import Operations
        from hellabit.payouts import Payouts
        from hellabit.rails import SandboxBank
        SandboxBank(self.book).fund(self.tenant,'agent-01','CAD',500,'snapshot-funding')
        q=self.book.create_quote(self.tenant,'CAD',500,0)['quote']
        self.book.run_report(self.tenant,self.book.reserve(self.tenant,'agent-01',q,'snapshot-work')['id'])
        payout=Payouts(self.book).request(self.tenant,'CAD',100,'merchant','snapshot-payment')
        ops=Operations(self.book);provider=SandboxProvider.for_ledger(self.book)
        entered,release=threading.Event(),threading.Event();response=[];original=Operations.public
        def pause(ops,row,db):
            if threading.current_thread() is not threading.main_thread() and row['state']=='prepared' and not entered.is_set():
                entered.set();self.assertTrue(release.wait(5))
            return original(ops,row,db)
        def read():response.append(self.request('GET',self.base+'/ops',headers=self.owner))
        with patch.object(Operations,'public',pause):
            thread=threading.Thread(target=read);thread.start()
            try:
                self.assertTrue(entered.wait(3))
                ops.process_one(self.tenant,provider)
                key=ops.get(self.tenant,payout['intent'])['external_key'];provider.return_payment(key,'full-return',100)
                ops.reconcile(self.tenant,payout['intent'],provider)
            finally:release.set();thread.join(5)
        self.assertEqual(response[0][0],200)
        before=json.loads(response[0][2]);item=before['intents'][0]
        self.assertEqual((item['state'],item['returned_units'],item['net_paid_units'],before['counts']['prepared']),('prepared',0,0,1))
        current=json.loads(self.request('GET',self.base+'/ops',headers=self.owner)[2])['intents'][0]
        self.assertEqual((current['original_settled_units'],current['returned_units'],current['net_paid_units']),(100,100,0))

    def test_redemption_lookup_does_not_depend_on_visible_page(self):
        self.post('issuer/attest',{'fiat_minor_units':10000,'reference':'reserve-before-claims'})
        self.post('issuer/mint',{'customer':'agent-01','amount':1000000,'reference':'mint-before-claims'})
        for i in range(21):self.assertEqual(self.post('issuer/redeem',{'customer':'agent-01','amount':10000,'reference':'claim-'+str(i)})[0],200)
        self.assertEqual(self.post('issuer/payout',{'redemption':'claim-0','payment_reference':'oldest-claim-debit'})[0],200)
        self.assertEqual(self.post('issuer/payout',{'redemption':'missing-claim','payment_reference':'no-such-debit'})[0],404)

    def test_key_revocation_during_upload_or_after_admission_prevents_reservation(self):
        from hellabit.rails import SandboxBank
        SandboxBank(self.book).fund(self.tenant,'agent-01','CAD',500,'upload-guard-funding')
        quote=self.book.create_quote(self.tenant,'CAD',100,1)['quote']
        for phase in ('upload','financial-transaction'):
            key=self.book.issue_merchant_key(self.tenant)
            def revoke():
                thread=threading.Thread(target=lambda:self.book.revoke_merchant_key(self.tenant,key['id']))
                thread.start();thread.join(timeout=3);self.assertFalse(thread.is_alive())
            original_body=Handler._body;original_reserve=self.book.reserve
            def body(handler):revoke();return original_body(handler)
            def reserve(*args,**kwargs):revoke();return original_reserve(*args,**kwargs)
            context=patch.object(Handler,'_body',body) if phase=='upload' else patch.object(self.book,'reserve',side_effect=reserve)
            with context:
                status,_,_=self.request('POST',self.base+'/operations',{'customer':'agent-01','quote':quote,'key':'revoked-'+phase},{'Authorization':'Bearer '+key['api_key']})
            self.assertEqual(status,401)
        with self.book.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = Ledger(Path(self.tmp.name) / "ledger.sqlite3")
        created = self.book.create_tenant("Example Merchant")
        self.tenant, self.key = created["tenant"], created["api_key"]
        self.server = Server(self.book)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        status, headers, html = self.request("GET", "/")
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = re.search(r'name="hellabit-csrf" content="([^"]+)"', html.decode()).group(1)
        self.owner = {"Cookie": self.cookie, "Origin": self.origin, "X-Hellabit-CSRF": self.csrf}
        self.bearer = {"Authorization": "Bearer " + self.key}
        self.base = "/api/v1/workspaces/" + self.tenant

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3); self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        outgoing = {**(headers or {})}
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        if data is not None: outgoing.setdefault("Content-Type", "application/json")
        connection.request(method, path, data, outgoing)
        response = connection.getresponse(); content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close(); return result

    def post(self, tail, body, owner=True):
        status, _, raw = self.request("POST", self.base + "/" + tail, body, self.owner if owner else self.bearer)
        return status, json.loads(raw)

    def test_anonymous_and_cross_workspace_requests_are_denied(self):
        self.assertEqual(self.request("GET", self.base)[0], 401)
        other = self.book.create_tenant("Other Merchant")["tenant"]
        self.assertEqual(self.request("GET", "/api/v1/workspaces/" + other, headers=self.bearer)[0], 403)
        self.assertEqual(self.request("GET", self.base, headers=self.bearer)[0], 200)

    def test_interrupted_payout_reply_recovers_original_payment_once_through_console_api(self):
        self.assertEqual(self.post("sandbox/fund",{"customer":"agent-01","asset":"CAD","amount":1000,"reference":"recovery-fund"})[0],200)
        quote=self.post("quotes",{"asset":"CAD","amount":1000,"fee":10})[1]
        op=self.post("operations",{"customer":"agent-01","quote":quote["quote"],"key":"recovery-work"})[1]
        self.post("operations/"+op["id"]+"/run",{})
        payout=self.post("payouts",{"asset":"CAD","amount":50,"recipient":"synthetic-merchant","key":"recovery-payout"})[1]
        route="ops/"+payout["intent"]
        interrupted=self.post(route+"/interrupt",{})[1]
        self.assertEqual(interrupted["intent"]["state"],"unknown")
        observed=json.loads(self.request("GET",self.base+"/readiness",headers=self.owner)[2])
        self.assertEqual(observed["machine_observations"]["unknown_or_overdue_submissions"],1)
        self.assertEqual(self.post("payouts/"+payout["id"]+"/confirm",{"payment_reference":"replacement"})[0],409)
        self.assertEqual(self.post(route+"/reconcile",{})[1]["state"],"confirmed")
        self.assertEqual(self.post(route+"/reconcile",{})[1]["state"],"confirmed")
        with self.book.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE kind='payout-paid'").fetchone()[0],1)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_recovery_and_readiness_are_owner_only_and_cannot_be_self_approved(self):
        for route in ("ops","readiness","readiness/packet"):
            self.assertEqual(self.request("GET",self.base+"/"+route,headers=self.bearer)[0],403)
        evidence={"control":"entity","reference":"reported-entity","document_hash":"a"*64,"expires":self.book.clock()+3600}
        self.assertEqual(self.post("readiness/evidence",evidence,owner=False)[0],403)
        self.assertEqual(self.post("readiness/evidence",{**evidence,"verified":True})[0],400)
        self.assertFalse(self.post("readiness/evidence",evidence)[1]["externally_verified"])
        packet=json.loads(self.request("GET",self.base+"/readiness/packet",headers=self.owner)[2])
        self.assertFalse(packet["operator_identity_included"])
        self.assertNotIn(self.tenant,json.dumps(packet));self.assertNotIn(self.key,json.dumps(packet))
        self.assertEqual(self.post("readiness/recovery-drill",{},owner=False)[0],403)
        drill=self.post("readiness/recovery-drill",{})
        self.assertEqual(drill[0],200);self.assertTrue(drill[1]["encrypted"])
        self.assertFalse(drill[1]["production_key_custody_established"])

    def test_key_management_is_owner_only_and_revocation_takes_effect_immediately(self):
        self.assertEqual(self.request('GET', self.base+'/keys', headers=self.bearer)[0], 403)
        key = self.post('merchant-keys', {})[1]
        self.assertEqual(self.post('merchant-keys/'+key['id']+'/revoke', {}, owner=False)[0], 403)
        self.assertEqual(self.post('merchant-keys/'+key['id']+'/revoke', {})[0], 200)
        auth = {'Authorization':'Bearer '+key['api_key']}
        self.assertEqual(self.request('GET', self.base, headers=auth)[0], 401)
        self.assertEqual(self.post('merchant-keys/bootstrap/revoke', {})[0], 200)
        self.assertEqual(self.request('GET', self.base, headers=self.bearer)[0], 401)
        self.assertEqual(self.request('GET', self.base, headers=self.owner)[0], 200)

    def test_lookup_uses_original_purchase_key_and_preserves_agent_customer_scope(self):
        self.book.add_customer(self.tenant, 'another-agent')
        self.book.issue_agent_key(self.tenant, 'another-agent')
        self.post('sandbox/fund', {'customer':'agent-01','asset':'CAD','amount':100,'reference':'lookup-fund'})
        quote = self.post('quotes', {'asset':'CAD','amount':100,'fee':1})[1]
        op = self.post('operations', {'customer':'agent-01','quote':quote['quote'],'key':'same-purchase'})[1]
        key = self.book.issue_agent_key(self.tenant, 'agent-01')
        self.bearer = {'Authorization':'Bearer '+key['api_key']}
        self.assertEqual(self.post('operations/lookup', {'key':'same-purchase'}, owner=False)[1]['id'], op['id'])
        key = self.book.issue_agent_key(self.tenant, 'another-agent')
        self.bearer = {'Authorization':'Bearer '+key['api_key']}
        self.assertEqual(self.post('operations/lookup', {'key':'same-purchase'}, owner=False)[0], 403)
        self.assertEqual(self.post('operations/lookup', {'key':'missing-purchase'}, owner=False)[0], 404)

    def test_connection_plan_is_owner_only_and_cannot_activate_payments(self):
        self.assertEqual(self.request('GET','/api/v1/connection-plan')[0], 403)
        status, _, raw = self.request('GET','/api/v1/connection-plan',headers=self.owner)
        self.assertEqual(status,200)
        plan=json.loads(raw)
        self.assertFalse(plan['live_enabled'])
        self.assertTrue(all(x['state']=='not-configured' and not x['account_connected'] for x in plan['partners']))

    def test_diagnostics_exclude_identity_keys_inputs_and_external_claims(self):
        status, _, raw=self.request('GET',self.base+'/diagnostics',headers=self.owner)
        self.assertEqual(status,200);data=json.loads(raw)
        self.assertIn('sqlite',data['runtime'])
        self.assertNotIn(self.key,raw.decode());self.assertNotIn(self.tenant,raw.decode())
        self.assertFalse(data['live_payments_enabled']);self.assertFalse(data['personal_data_included'])
        self.assertEqual(self.request('GET',self.base+'/diagnostics',headers=self.bearer)[0],403)

    def test_quoted_input_cannot_be_replaced_at_execution(self):
        from test_purchases import INPUT
        self.post('sandbox/fund',{'customer':'agent-01','asset':'CAD','amount':100,'reference':'invoice-fund'})
        q=self.post('quotes',{'asset':'CAD','amount':100,'fee':1,'resource':'invoice-check','input':INPUT})[1]
        op=self.post('operations',{'customer':'agent-01','quote':q['quote'],'key':'one-invoice'})[1]['id']
        self.assertEqual(self.post('operations/'+op+'/run',{'input':{'changed':True}})[0],400)
        self.assertEqual(self.post('operations/'+op+'/run',{})[0],200)
        status,_,raw=self.request('GET',self.base+'/deliveries/'+op,headers=self.bearer)
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['result']['computed_total_units'],3675)

    def test_agent_cannot_browse_workspace_activity(self):
        key=self.book.issue_agent_key(self.tenant,'agent-01')
        self.assertEqual(self.request('GET',self.base+'/activity/all',headers={'Authorization':'Bearer '+key['api_key']})[0],403)

    def test_host_origin_csrf_and_tenant_binding(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "attacker.invalid"})[0], 403)
        bad = {**self.owner, "Origin": "https://attacker.invalid"}
        self.assertEqual(self.request("POST", self.base + "/customers", {"customer": "other"}, bad)[0], 403)
        bad = {**self.owner, "X-Hellabit-CSRF": "wrong"}
        self.assertEqual(self.request("POST", self.base + "/customers", {"customer": "other"}, bad)[0], 403)

    def test_bearer_cannot_forge_settlement_or_issuer_authority(self):
        self.assertEqual(self.post("sandbox/fund", {"customer": "agent-01", "asset": "CAD", "amount": 100, "reference": "fund-1"}, owner=False)[0], 403)
        self.assertEqual(self.post("issuer/mint", {"customer": "agent-01", "amount": 1, "reference": "mint-1"}, owner=False)[0], 403)
        self.assertEqual(self.post("sandbox/fund", {"customer": "agent-01", "asset": "CAD", "amount": 100, "reference": "fund-1", "live": True})[0], 400)

    def test_exact_json_and_bounded_payload(self):
        path = self.base + "/customers"
        self.assertEqual(self.request("POST", path, headers=self.owner, raw=b'{"customer":"a","customer":"b"}')[0], 400)
        self.assertEqual(self.request("POST", path, headers=self.owner, raw=b'{"customer":NaN}')[0], 400)
        self.assertEqual(self.request("POST", path, headers=self.owner, raw=b" " * 9000)[0], 413)
        self.assertEqual(self.request("GET", self.base + "?token=secret", headers=self.bearer)[0], 400)

    def test_full_paid_report_journey_and_refund_revocation(self):
        self.assertEqual(self.post("sandbox/fund", {"customer": "agent-01", "asset": "CAD", "amount": 1000, "reference": "fund-1"})[0], 200)
        status, quote = self.post("quotes", {"asset": "CAD", "amount": 100, "fee": 1}, owner=False)
        self.assertEqual(status, 200)
        status, operation = self.post("operations", {"customer": "agent-01", "quote": quote["quote"], "key": "one-job"}, owner=False)
        self.assertEqual(status, 200); op = operation["id"]
        self.assertEqual(self.post("operations/" + op + "/run", {}, owner=False)[0], 200)
        self.assertTrue(self.post("operations/" + op + "/run", {}, owner=False)[1]["replayed"])
        self.assertEqual(self.request("GET", self.base + "/deliveries/" + op, headers=self.bearer)[0], 200)
        self.assertEqual(self.post("operations/" + op + "/refund", {}, owner=False)[0], 200)
        self.assertEqual(self.request("GET", self.base + "/deliveries/" + op, headers=self.bearer)[0], 403)
        self.assertTrue(self.book.reconcile(self.tenant)["ok"])

    def test_three_enrollment_profiles_are_distinct_and_do_not_activate(self):
        examples = {"bank": {"institution_reference": "bank-01", "account_reference": "account-01", "currency": "CAD"},
                    "usdc": {"receiver": "0x" + "1" * 40, "signer_reference": "signer-01", "chain_id": 84532},
                    "issuer": {"legal_operator_reference": "issuer-01", "reserve_custodian_reference": "custodian-01"}}
        for rail, body in examples.items():
            status, result = self.post("enroll/" + rail, body)
            self.assertEqual(status, 200); self.assertFalse(result["live_enabled"])
        self.assertEqual(len(self.book.snapshot(self.tenant)["enrollments"]), 3)
        self.assertFalse(self.book.snapshot(self.tenant)["live_payments_enabled"])
        self.assertEqual(self.post("enroll/usdc", {**examples["usdc"], "chain_id": 8453})[0], 400)

    def test_security_headers_and_private_static_boundary(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/.private/ledger.sqlite3")[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/AGENTS.md")[0], 400)

    def test_agent_key_cannot_change_prices_or_spend_another_customer_balance(self):
        self.book.add_customer(self.tenant, "other-customer")
        key = self.book.issue_agent_key(self.tenant, "agent-01")
        self.bearer = {"Authorization": "Bearer " + key["api_key"]}
        self.assertEqual(self.post("quotes", {"asset": "CAD", "amount": 1, "fee": 0}, owner=False)[0], 403)
        q = self.book.create_quote(self.tenant, "CAD", 1, 0)["quote"]
        self.assertEqual(self.post("operations", {"customer": "other-customer", "quote": q, "key": "steal"}, owner=False)[0], 403)
        status, _, raw = self.request("GET", self.base, headers=self.bearer)
        data = json.loads(raw); self.assertEqual(status, 200)
        self.assertNotIn("journal", data); self.assertNotIn("issuer", data)
        self.assertEqual(data["customer"], "agent-01")

    def test_agent_can_run_only_its_reference_job_and_revocation_is_immediate(self):
        self.post("sandbox/fund", {"customer": "agent-01", "asset": "CAD", "amount": 100, "reference": "fund-agent"})
        q = self.book.create_quote(self.tenant, "CAD", 100, 1)["quote"]
        key = self.book.issue_agent_key(self.tenant, "agent-01")
        from hellabit.agent_policy import AgentPolicy
        AgentPolicy(self.book).configure(self.tenant,key['id'],{'asset':'CAD','resource':'report','recipient':'workspace-merchant','per_purchase':100,'daily':100,'approval_above':100,'expires':self.book.clock()+3600},0)
        self.bearer = {"Authorization": "Bearer " + key["api_key"]}
        status, op = self.post("operations", {"customer": "agent-01", "quote": q, "key": "own-job"}, owner=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.post("operations/" + op["id"] + "/complete", {"result_hash": "a" * 64}, owner=False)[0], 403)
        self.assertEqual(self.post("operations/" + op["id"] + "/run", {}, owner=False)[0], 200)
        self.book.revoke_agent_key(self.tenant, key["id"])
        self.assertEqual(self.request("GET", self.base, headers=self.bearer)[0], 401)

    def test_owner_can_issue_merchant_key_without_granting_settlement_authority(self):
        status, key = self.post("merchant-keys", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.book.principal(key["api_key"])["role"], "merchant")
        self.bearer = {"Authorization": "Bearer " + key["api_key"]}
        self.assertEqual(self.post("merchant-keys", {}, owner=False)[0], 403)

    def test_one_service_writer_per_database(self):
        with self.assertRaises(BlockingIOError): Server(self.book)


if __name__ == "__main__": unittest.main()
