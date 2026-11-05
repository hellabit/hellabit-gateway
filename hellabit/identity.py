"""Pinned issuer signatures authenticate identity; local grants assign permissions."""
from __future__ import annotations
import base64
import json
import re
import secrets
from urllib.parse import urlsplit
from .control import audit, ensure_schema
from .ledger import Error, digest, identifier
from .rpc import _strict_object,_invalid_constant

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_principals(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,issuer TEXT NOT NULL,subject_hash TEXT NOT NULL,
 role TEXT NOT NULL,customer TEXT,expires INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,
 rate_limit INTEGER NOT NULL,UNIQUE(issuer,subject_hash));
CREATE TABLE IF NOT EXISTS api_rate_windows(
 principal TEXT NOT NULL,window INTEGER NOT NULL,requests INTEGER NOT NULL,PRIMARY KEY(principal,window));
CREATE TABLE IF NOT EXISTS api_grants(
 principal TEXT NOT NULL,version INTEGER NOT NULL,action TEXT NOT NULL,body TEXT NOT NULL,
 created INTEGER NOT NULL,PRIMARY KEY(principal,version));
CREATE TRIGGER IF NOT EXISTS api_grant_no_update BEFORE UPDATE ON api_grants
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS api_grant_no_delete BEFORE DELETE ON api_grants
 BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

class Identity:
    def __init__(self, book, *, issuer, audience, public_keys):
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        parsed = urlsplit(issuer)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or not isinstance(audience,str) or not 1 <= len(audience) <= 200):
            raise Error("invalid_identity_binding",400)
        if not isinstance(public_keys,dict) or not 1 <= len(public_keys) <= 8:
            raise Error("invalid_identity_keys",400)
        self.keys={}
        for kid,pem in public_keys.items():
            identifier(kid)
            try: key=serialization.load_pem_public_key(pem)
            except (ValueError,TypeError): raise Error("invalid_identity_public_key",400) from None
            if not isinstance(key,RSAPublicKey) or key.key_size<2048:
                raise Error("identity_requires_rsa_2048_or_stronger",400)
            self.keys[kid]=key
        self.book,self.issuer,self.audience,self.jwt=book,issuer,audience,jwt
        ensure_schema(book,"identity",SCHEMA)

    def _history(self,db,row,action):
        from .ledger import canonical
        version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM api_grants WHERE principal=?",(row["id"],)).fetchone()[0]
        terms={k:row[k] for k in ("role","customer","expires","rate_limit","revoked","valid_after","grant_id")}
        db.execute("INSERT INTO api_grants VALUES(?,?,?,?,?)",(row["id"],version,action,canonical(terms),self.book.clock()))
        audit(self.book,db,row["tenant"],"api-grant-"+action,row["id"],{"version":version,**terms})
        return version

    def enroll(self, tenant, subject, role, expires, *, customer=None, rate_limit=120):
        if not isinstance(subject,str) or not 1 <= len(subject) <= 200 or any(ord(c)<32 for c in subject):
            raise Error("invalid_identity_subject",400)
        if role not in ("merchant","agent","read-only","operator"):
            raise Error("invalid_identity_role",400)
        if type(expires) is not int or not self.book.clock()<expires<=self.book.clock()+31*86400:
            raise Error("invalid_principal_expiry",400)
        if type(rate_limit) is not int or not 1<=rate_limit<=6000:raise Error("invalid_principal_rate_limit",400)
        with self.book.transaction() as db:
            self.book.tenant(db,tenant)
            if role=="agent":self.book.customer(db,tenant,customer)
            elif customer is not None:raise Error("unexpected_customer_binding",400)
            hashed=digest(subject)
            old=db.execute("SELECT * FROM api_principals WHERE issuer=? AND subject_hash=?",(self.issuer,hashed)).fetchone()
            if old:
                if any(old[k]!=v for k,v in {"tenant":tenant,"role":role,"customer":customer,
                        "expires":expires,"rate_limit":rate_limit}.items()) or old["revoked"]:
                    raise Error("principal_binding_immutable",409)
                version=db.execute("SELECT MAX(version) FROM api_grants WHERE principal=?",(old["id"],)).fetchone()[0]
                return {"id":old["id"],"version":version,"token_claims":{"hellabit_grant":old["grant_id"]},"replayed":True}
            id_="principal_"+secrets.token_hex(12)
            grant_id="grant_"+secrets.token_hex(24)
            db.execute("INSERT INTO api_principals(id,tenant,issuer,subject_hash,role,customer,expires,revoked,rate_limit,grant_id) VALUES(?,?,?,?,?,?,?,0,?,?)",
                       (id_,tenant,self.issuer,hashed,role,customer,expires,rate_limit,grant_id))
            row=db.execute("SELECT * FROM api_principals WHERE id=?",(id_,)).fetchone()
            version=self._history(db,row,"enrolled")
        return {"id":id_,"version":version,"role":role,"expires":expires,"token_claims":{"hellabit_grant":grant_id}}

    def change_grant(self,tenant,id_,action,expected_version,*,expires,role=None,customer=None,rate_limit=None):
        identifier(id_)
        if action not in ("renew","replace","reinstate") or type(expected_version) is not int:
            raise Error("invalid_grant_change",400)
        if type(expires) is not int or not self.book.clock()<expires<=self.book.clock()+31*86400:
            raise Error("invalid_principal_expiry",400)
        with self.book.transaction() as db:
            old=db.execute("SELECT * FROM api_principals WHERE tenant=? AND id=? AND issuer=?",(tenant,id_,self.issuer)).fetchone()
            if not old:raise Error("principal_not_found",404)
            current=db.execute("SELECT MAX(version) FROM api_grants WHERE principal=?",(id_,)).fetchone()[0]
            if current!=expected_version:raise Error("grant_version_conflict",409)
            if bool(old["revoked"])!=(action=="reinstate"):raise Error("explicit_reinstatement_required" if old["revoked"] else "principal_not_revoked",409)
            if action=="renew" and any(v is not None for v in (role,customer,rate_limit)):
                raise Error("renewal_cannot_change_permissions",400)
            role=old["role"] if role is None else role
            customer=old["customer"] if action=="renew" else customer
            rate_limit=old["rate_limit"] if rate_limit is None else rate_limit
            if role not in ("merchant","agent","read-only","operator") or type(rate_limit) is not int or not 1<=rate_limit<=6000:
                raise Error("invalid_grant_terms",400)
            if role=="agent":self.book.customer(db,tenant,customer)
            elif customer is not None:raise Error("unexpected_customer_binding",400)
            grant_id="grant_"+secrets.token_hex(24)
            db.execute("UPDATE api_principals SET role=?,customer=?,rate_limit=?,expires=?,revoked=0,valid_after=?,grant_id=? WHERE id=?",
                       (role,customer,rate_limit,expires,self.book.clock(),grant_id,id_))
            row=db.execute("SELECT * FROM api_principals WHERE id=?",(id_,)).fetchone()
            version=self._history(db,row,action)
        return {"id":id_,"version":version,"expires":expires,"token_claims":{"hellabit_grant":grant_id}}

    def revoke(self, tenant, id_):
        identifier(id_)
        with self.book.transaction() as db:
            row=db.execute("SELECT * FROM api_principals WHERE tenant=? AND id=?",(tenant,id_)).fetchone()
            if not row:raise Error("principal_not_found",404)
            db.execute("UPDATE api_principals SET revoked=1 WHERE id=?",(id_,))
            changed=db.execute("SELECT * FROM api_principals WHERE id=?",(id_,)).fetchone()
            version=self._history(db,changed,"revoked") if not row["revoked"] else db.execute("SELECT MAX(version) FROM api_grants WHERE principal=?",(id_,)).fetchone()[0]
        return {"revoked":True,"version":version}

    def authenticate(self, token):
        if not isinstance(token,str) or not 1<=len(token)<=8192:raise Error("unauthorized",401)
        try:
            parts=token.split(".")
            if len(parts)!=3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+",part) for part in parts):
                raise ValueError()
            # Reject ambiguous JSON before the maintained library verifies it.
            # No unverified claim is used as authority.
            for part in parts[:2]:
                value=json.loads(base64.urlsafe_b64decode(part+"="*((-len(part))%4)),
                    object_pairs_hook=_strict_object,parse_constant=_invalid_constant)
                if not isinstance(value,dict):raise ValueError()
            header=self.jwt.get_unverified_header(token)
            if (header.get("alg")!="RS256" or not isinstance(header.get("kid"),str) or header.get("kid") not in self.keys or
                    any(k in header for k in ("jku","x5u","jwk","crit"))):
                raise Error("invalid_access_token",401)
            claims=self.jwt.decode(token,self.keys[header["kid"]],algorithms=["RS256"],
                issuer=self.issuer,audience=self.audience,leeway=15,
                options={"require":["exp","iat","iss","aud","sub"]})
        except (self.jwt.PyJWTError,ValueError,TypeError,RecursionError):raise Error("invalid_access_token",401) from None
        if (type(claims["iat"]) is not int or type(claims["exp"]) is not int or
                not 0<claims["exp"]-claims["iat"]<=900 or
                not isinstance(claims["sub"],str) or not 1<=len(claims["sub"])<=200):
            raise Error("invalid_access_token",401)
        if header.get("typ")!="at+jwt" and claims.get("token_use")!="access":
            raise Error("access_token_required",401)
        with self.book.transaction() as db:
            if db.execute("SELECT 1 FROM settings WHERE key IN ('recovery_quarantine','recovery_retired')").fetchone():
                raise Error("restored_database_quarantined",423)
            row=db.execute("SELECT * FROM api_principals WHERE issuer=? AND subject_hash=?",
                           (self.issuer,digest(claims["sub"]))).fetchone()
            if not row or row["revoked"] or row["expires"]<=self.book.clock():
                raise Error("principal_not_enrolled",403)
            if claims.get("hellabit_grant")!=row["grant_id"]:
                raise Error("grant_generation_mismatch",403)
            amr=claims.get("amr",[])
            if row["role"]=="operator" and (not isinstance(amr,list) or "mfa" not in amr):
                raise Error("operator_mfa_required",403)
            if row["role"]=="operator" and (type(claims.get("auth_time")) is not int or
                    not self.book.clock()-900<=claims["auth_time"]<=self.book.clock()+15):
                raise Error("fresh_operator_authentication_required",403)
            window=self.book.clock()//60
            old=db.execute("SELECT requests FROM api_rate_windows WHERE principal=? AND window=?",(row["id"],window)).fetchone()
            if old and old[0]>=row["rate_limit"]:raise Error("request_rate_exceeded",429)
            db.execute("""INSERT INTO api_rate_windows VALUES(?,?,1)
                ON CONFLICT(principal,window) DO UPDATE SET requests=requests+1""",(row["id"],window))
            db.execute("DELETE FROM api_rate_windows WHERE rowid IN(SELECT rowid FROM api_rate_windows WHERE window<? LIMIT 100)",(window-5,))
            return {"id":row["id"],"tenant":row["tenant"],"role":row["role"],"customer":row["customer"],
                    "grant_id":row["grant_id"],"token_expires":claims["exp"],
                    "operator_auth_time":claims.get("auth_time"),
                    "identity_source":"pinned-issuer","mfa_asserted":isinstance(amr,list) and "mfa" in amr}

    def _validate_current(self,db,principal):
        if db.execute("SELECT 1 FROM settings WHERE key IN ('recovery_quarantine','recovery_retired')").fetchone():
            raise Error("restored_database_quarantined",423)
        row=db.execute("SELECT * FROM api_principals WHERE id=? AND tenant=? AND issuer=?",
                       (principal['id'],principal['tenant'],self.issuer)).fetchone()
        if (not row or row['revoked'] or row['expires']<=self.book.clock() or
                any(row[k]!=principal[k] for k in ('grant_id','role','customer'))):
            raise Error("authorization_changed",403)
        if principal['token_expires']<=self.book.clock():raise Error("access_token_expired",401)
        if row['role']=='operator' and (not principal['mfa_asserted'] or
                not self.book.clock()-900<=principal['operator_auth_time']<=self.book.clock()+15):
            raise Error("fresh_operator_authentication_required",403)

    def dispatch(self,principal,route,*args):
        """Reads are admitted here; every write rechecks under its commit lock."""
        guard=lambda db:self._validate_current(db,principal)
        with self.book.db() as db:guard(db)
        with self.book.request_authority(guard):return route(principal,*args)
