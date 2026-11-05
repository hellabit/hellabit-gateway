-- Exact empty schema created by released Hellabit 0.4.0; synthetic compatibility fixture.
CREATE TABLE agent_keys(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,subject TEXT NOT NULL,
 key_hash TEXT NOT NULL UNIQUE,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE api_principals(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,issuer TEXT NOT NULL,subject_hash TEXT NOT NULL,
 role TEXT NOT NULL,customer TEXT,expires INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,
 rate_limit INTEGER NOT NULL,UNIQUE(issuer,subject_hash));
CREATE TABLE api_rate_windows(
 principal TEXT NOT NULL,window INTEGER NOT NULL,requests INTEGER NOT NULL,PRIMARY KEY(principal,window));
CREATE TABLE assets(tenant TEXT NOT NULL,id TEXT NOT NULL,symbol TEXT NOT NULL,
 decimals INTEGER NOT NULL,rail TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE control_audit(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,kind TEXT NOT NULL,
 subject TEXT NOT NULL,body TEXT NOT NULL,previous TEXT NOT NULL,digest TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE customers(tenant TEXT NOT NULL,id TEXT NOT NULL,
 PRIMARY KEY(tenant,id),FOREIGN KEY(tenant) REFERENCES tenants(id));
CREATE TABLE deliveries(tenant TEXT NOT NULL,operation TEXT NOT NULL,body TEXT NOT NULL,
 result_hash TEXT NOT NULL,PRIMARY KEY(tenant,operation));
CREATE TABLE enrollments(tenant TEXT NOT NULL,rail TEXT NOT NULL,body TEXT NOT NULL,
 updated INTEGER NOT NULL,PRIMARY KEY(tenant,rail));
CREATE TABLE execution_intents(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,payout TEXT NOT NULL,rail TEXT NOT NULL,
 asset TEXT NOT NULL,amount INTEGER NOT NULL,recipient TEXT NOT NULL,
 external_key TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('prepared','submitting','unknown','confirmed','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,lease_hash TEXT,lease_until INTEGER,
 reference TEXT,evidence_hash TEXT,last_error TEXT,created INTEGER NOT NULL,updated INTEGER NOT NULL,
 UNIQUE(tenant,payout),FOREIGN KEY(payout) REFERENCES payouts(id));
CREATE TABLE execution_observations(
 tenant TEXT NOT NULL,intent TEXT NOT NULL,proof_hash TEXT NOT NULL,body TEXT NOT NULL,
 source TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(tenant,intent,proof_hash));
CREATE TABLE issuer(tenant TEXT PRIMARY KEY,name TEXT NOT NULL,symbol TEXT NOT NULL,
 currency TEXT NOT NULL,reserve INTEGER NOT NULL DEFAULT 0,valid_until INTEGER NOT NULL DEFAULT 0);
CREATE TABLE issuer_requests(tenant TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,
 request_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE journals(seq INTEGER PRIMARY KEY AUTOINCREMENT,tenant TEXT NOT NULL,
 kind TEXT NOT NULL,reference TEXT NOT NULL,asset TEXT NOT NULL,body TEXT NOT NULL,
 previous TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,created INTEGER NOT NULL,
 UNIQUE(tenant,kind,reference));
CREATE TABLE merchant_keys(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,
 key_hash TEXT NOT NULL UNIQUE,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE operations(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,customer TEXT NOT NULL,
 asset TEXT NOT NULL,quote TEXT NOT NULL,amount INTEGER NOT NULL,fee INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','uncertain','committed','released')),
 request_hash TEXT NOT NULL,key_hash TEXT NOT NULL,result_hash TEXT,created INTEGER NOT NULL,
 UNIQUE(tenant,key_hash));
CREATE TABLE payouts(tenant TEXT NOT NULL,id TEXT PRIMARY KEY,asset TEXT NOT NULL,
 amount INTEGER NOT NULL,source TEXT NOT NULL,recipient TEXT NOT NULL,state TEXT NOT NULL,
 key_hash TEXT NOT NULL,request_hash TEXT NOT NULL,payment_reference TEXT,UNIQUE(tenant,key_hash));
CREATE TABLE postings(journal INTEGER NOT NULL,account TEXT NOT NULL,delta INTEGER NOT NULL CHECK(delta<>0),
 FOREIGN KEY(journal) REFERENCES journals(seq));
CREATE TABLE quotes(tenant TEXT NOT NULL,id TEXT NOT NULL,asset TEXT NOT NULL,
 resource TEXT NOT NULL,amount INTEGER NOT NULL,fee INTEGER NOT NULL,expires INTEGER NOT NULL,
 work_input TEXT,input_hash TEXT,
 PRIMARY KEY(tenant,id));
CREATE TABLE rail_events(tenant TEXT NOT NULL,rail TEXT NOT NULL,reference TEXT NOT NULL,
 purpose TEXT NOT NULL,request_hash TEXT NOT NULL,PRIMARY KEY(tenant,rail,reference));
CREATE TABLE redemptions(tenant TEXT NOT NULL,id TEXT NOT NULL,customer TEXT NOT NULL,
 amount INTEGER NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','paid')),payment_reference TEXT,
 request_hash TEXT NOT NULL,PRIMARY KEY(tenant,id));
CREATE TABLE reserve_proofs(tenant TEXT NOT NULL,reference TEXT NOT NULL,request_hash TEXT NOT NULL,
 units INTEGER NOT NULL,PRIMARY KEY(tenant,reference));
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE settlements(tenant TEXT NOT NULL,rail TEXT NOT NULL,reference TEXT NOT NULL,
 request_hash TEXT NOT NULL,journal INTEGER NOT NULL,PRIMARY KEY(tenant,rail,reference));
CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,country TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,created INTEGER NOT NULL,bootstrap_revoked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX posting_account ON postings(account,journal);
CREATE TRIGGER control_audit_no_delete BEFORE DELETE ON control_audit
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER control_audit_no_update BEFORE UPDATE ON control_audit
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER intent_terms_immutable
 BEFORE UPDATE OF tenant,payout,rail,asset,amount,recipient,external_key,request_hash ON execution_intents
 BEGIN SELECT RAISE(ABORT,'intent_terms_are_immutable'); END;
CREATE TRIGGER journal_no_delete BEFORE DELETE ON journals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER journal_no_update BEFORE UPDATE ON journals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER observation_no_delete BEFORE DELETE ON execution_observations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER observation_no_update BEFORE UPDATE ON execution_observations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER operation_terms_immutable BEFORE UPDATE OF tenant,customer,asset,quote,amount,fee,request_hash,key_hash ON operations
 BEGIN SELECT RAISE(ABORT,'operation_terms_are_immutable'); END;
CREATE TRIGGER posting_no_delete BEFORE DELETE ON postings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER posting_no_update BEFORE UPDATE ON postings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER quote_no_delete BEFORE DELETE ON quotes BEGIN SELECT RAISE(ABORT,'price_is_immutable'); END;
CREATE TRIGGER quote_no_update BEFORE UPDATE ON quotes BEGIN SELECT RAISE(ABORT,'price_is_immutable'); END;
