"""Owner-defined purchase authority, enforced in the reservation transaction."""
from .control import audit
from .ledger import Error,digest,identifier,units

SCHEMA="""
CREATE TABLE agent_policies(tenant TEXT NOT NULL,principal TEXT NOT NULL,customer TEXT NOT NULL,
 asset TEXT NOT NULL,resource TEXT NOT NULL,recipient TEXT NOT NULL,per_purchase INTEGER NOT NULL,
 daily INTEGER NOT NULL,approval_above INTEGER NOT NULL,expires INTEGER NOT NULL,version INTEGER NOT NULL,
 PRIMARY KEY(tenant,principal));
CREATE TABLE agent_daily_usage(tenant TEXT NOT NULL,principal TEXT NOT NULL,asset TEXT NOT NULL,
 day INTEGER NOT NULL,authorized_units INTEGER NOT NULL,PRIMARY KEY(tenant,principal,asset,day));
CREATE TABLE agent_approvals(tenant TEXT NOT NULL,principal TEXT NOT NULL,version INTEGER NOT NULL,
 authority TEXT NOT NULL,quote TEXT NOT NULL,key_hash TEXT NOT NULL,expires INTEGER NOT NULL,operation TEXT,
 PRIMARY KEY(tenant,principal,version,authority,quote,key_hash));
CREATE TABLE agent_reservations(tenant TEXT NOT NULL,operation TEXT NOT NULL,principal TEXT NOT NULL,
 version INTEGER NOT NULL,authority TEXT NOT NULL,asset TEXT NOT NULL,day INTEGER NOT NULL,amount INTEGER NOT NULL,
 PRIMARY KEY(tenant,operation));
CREATE TRIGGER agent_reservation_no_update BEFORE UPDATE ON agent_reservations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER agent_reservation_no_delete BEFORE DELETE ON agent_reservations
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

class AgentPolicy:
    def __init__(self,book):self.book=book

    def _subject(self,db,tenant,principal):
        key=db.execute('SELECT subject FROM agent_keys WHERE tenant=? AND id=? AND revoked=0',(tenant,principal)).fetchone()
        if key:return key[0],principal
        grant=db.execute("SELECT customer,grant_id FROM api_principals WHERE tenant=? AND id=? AND role='agent' AND revoked=0 AND expires>?",(tenant,principal,self.book.clock())).fetchone()
        if grant:return grant[0],grant[1]
        raise Error('active_agent_principal_required',403)

    def configure(self,tenant,principal,terms,expected_version):
        identifier(principal)
        if not isinstance(terms,dict) or set(terms)!={'asset','resource','recipient','per_purchase','daily','approval_above','expires'}:
            raise Error('invalid_agent_policy_fields',400)
        identifier(terms['asset']);identifier(terms['resource'])
        for key in ('per_purchase','daily','approval_above'):units(terms[key],zero=key=='approval_above')
        if (terms['recipient']!='workspace-merchant' or not 0<=terms['approval_above']<=terms['per_purchase']<=terms['daily']
                or type(terms['expires']) is not int or not self.book.clock()<terms['expires']<=self.book.clock()+365*86400
                or type(expected_version) is not int or expected_version<0):
            raise Error('invalid_agent_policy_terms',400)
        with self.book.transaction() as db:
            self.book.assert_payment_writes(db)
            customer,_=self._subject(db,tenant,principal);self.book.asset(db,tenant,terms['asset'])
            old=db.execute('SELECT version FROM agent_policies WHERE tenant=? AND principal=?',(tenant,principal)).fetchone()
            if (old[0] if old else 0)!=expected_version:raise Error('agent_policy_version_conflict')
            version=expected_version+1
            db.execute('INSERT OR REPLACE INTO agent_policies VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (tenant,principal,customer,*(terms[k] for k in ('asset','resource','recipient','per_purchase','daily','approval_above','expires')),version))
            audit(self.book,db,tenant,'agent-policy-set',principal,{'version':version,**terms})
        return {'principal':principal,'version':version,'policy':terms}

    def _current(self,db,tenant,principal):
        policy=db.execute('SELECT * FROM agent_policies WHERE tenant=? AND principal=?',(tenant,principal)).fetchone()
        if not policy or policy['expires']<=self.book.clock():raise Error('agent_spending_policy_required',403)
        return policy

    @staticmethod
    def _quote(policy,quote):
        if quote['asset']!=policy['asset'] or quote['resource']!=policy['resource'] or policy['recipient']!='workspace-merchant':
            raise Error('agent_purchase_scope_forbidden',403)
        if quote['amount']>policy['per_purchase']:raise Error('agent_purchase_limit_exceeded',403)

    def approve(self,tenant,principal,quote,key):
        identifier(principal);identifier(quote);identifier(key)
        with self.book.transaction() as db:
            self.book.assert_payment_writes(db);customer,authority=self._subject(db,tenant,principal)
            policy=self._current(db,tenant,principal)
            if customer!=policy['customer']:raise Error('agent_policy_customer_changed',403)
            terms=db.execute('SELECT * FROM quotes WHERE tenant=? AND id=?',(tenant,quote)).fetchone()
            if not terms or terms['expires']<=self.book.clock():raise Error('quote_expired_or_missing')
            self._quote(policy,terms)
            expiry=min(terms['expires'],policy['expires'],self.book.clock()+300)
            scope=(tenant,principal,policy['version'],authority,quote,digest(key))
            old=db.execute('SELECT expires,operation FROM agent_approvals WHERE tenant=? AND principal=? AND version=? AND authority=? AND quote=? AND key_hash=?',scope).fetchone()
            if old:return {'approved':True,'expires':old['expires'],'used':bool(old['operation']),'replayed':True}
            db.execute('INSERT INTO agent_approvals VALUES(?,?,?,?,?,?,?,NULL)',(*scope,expiry))
            audit(self.book,db,tenant,'agent-purchase-approved',principal,{'quote':quote,'version':policy['version'],'amount':terms['amount'],'expires':expiry})
            return {'approved':True,'expires':expiry,'used':False}

    def reserve(self,db,tenant,actor,quote,key_hash,operation):
        principal=actor['id'];policy=self._current(db,tenant,principal)
        authority=actor.get('grant_id',principal)
        if actor['customer']!=policy['customer']:raise Error('agent_policy_customer_changed',403)
        self._quote(policy,quote)
        day=self.book.clock()//86400;amount=quote['amount']
        scope=(tenant,principal,quote['asset'],day)
        old=db.execute('SELECT authorized_units FROM agent_daily_usage WHERE tenant=? AND principal=? AND asset=? AND day=?',scope).fetchone()
        used=old[0] if old else 0
        if used+amount>policy['daily']:raise Error('agent_daily_limit_exceeded',403)
        if amount>policy['approval_above']:
            changed=db.execute('''UPDATE agent_approvals SET operation=? WHERE tenant=? AND principal=? AND version=?
                AND authority=? AND quote=? AND key_hash=? AND operation IS NULL AND expires>?''',
                (operation,tenant,principal,policy['version'],authority,quote['id'],key_hash,self.book.clock())).rowcount
            if changed!=1:raise Error('exact_agent_purchase_approval_required',403)
        db.execute('INSERT OR REPLACE INTO agent_daily_usage VALUES(?,?,?,?,?)',(*scope,used+amount))
        db.execute('INSERT INTO agent_reservations VALUES(?,?,?,?,?,?,?,?)',
            (tenant,operation,principal,policy['version'],authority,quote['asset'],day,amount))

    def assert_reservation(self,db,tenant,actor,operation,*,executing=False):
        row=db.execute('SELECT principal,version,authority FROM agent_reservations WHERE tenant=? AND operation=?',(tenant,operation)).fetchone()
        if not row or row['principal']!=actor['id']:raise Error('agent_reservation_not_owned',403)
        if executing:
            policy=self._current(db,tenant,actor['id'])
            if policy['version']!=row['version'] or policy['customer']!=actor['customer'] or row['authority']!=actor.get('grant_id',actor['id']):
                raise Error('agent_reservation_authority_changed',403)
