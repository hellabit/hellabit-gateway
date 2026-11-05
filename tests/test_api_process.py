from datetime import datetime,timedelta,timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import selectors
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
from hellabit.ledger import Ledger
from hellabit.identity import Identity

ROOT=Path(__file__).resolve().parents[1]

class ApiProcessTests(unittest.TestCase):
    def test_real_tls_process_authentication_and_owner_route_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);now=datetime.now(timezone.utc)
            ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            signer=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"Hellabit synthetic test CA")])
            ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
                .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
                .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,None,None),critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),critical=False)
                .sign(ca_key,hashes.SHA256()))
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"localhost")])
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
                .public_key(server_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,None,None),critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),critical=False)
                .sign(ca_key,hashes.SHA256()))
            for name,data in [("ca.pem",ca.public_bytes(serialization.Encoding.PEM)),
                              ("server.pem",cert.public_bytes(serialization.Encoding.PEM)),
                              ("server-key.pem",server_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())),
                              ("issuer.pem",signer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))]:
                path=root/name;path.write_bytes(data);path.chmod(0o600)
            book=Ledger(root/"ledger.sqlite3");tenant=book.create_tenant("TLS Merchant")["tenant"]
            public=(root/"issuer.pem").read_bytes()
            grant=Identity(book,issuer="https://issuer.example.invalid",audience="hellabit-api",public_keys={"key-1":public}).enroll(
                tenant,"merchant","merchant",int(time.time())+3600)
            code="""
import asyncio,json,socket,sys,uvicorn
from pathlib import Path
from hellabit.api import Api
from hellabit.identity import Identity
from hellabit.ledger import Ledger
root=Path(sys.argv[1]);book=Ledger(root/'ledger.sqlite3')
identity=Identity(book,issuer='https://issuer.example.invalid',audience='hellabit-api',public_keys={'key-1':(root/'issuer.pem').read_bytes()})
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(32);port=sock.getsockname()[1]
app=Api(book,identity,allowed_hosts={'127.0.0.1:'+str(port)})
config=uvicorn.Config(app,ssl_certfile=str(root/'server.pem'),ssl_keyfile=str(root/'server-key.pem'),
 proxy_headers=False,access_log=False,server_header=False,log_config=None)
server=uvicorn.Server(config)
print(json.dumps({'port':port}),flush=True)
asyncio.run(server.serve(sockets=[sock]))
"""
            env={"PATH":os.defpath,"PYTHONPATH":str(ROOT),"LANG":"en_US.UTF-8","PYTHONDONTWRITEBYTECODE":"1"}
            child=subprocess.Popen([sys.executable,"-c",code,str(root)],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(10),"TLS API did not start")
                line=child.stdout.readline();selector.close()
                self.assertTrue(line,"TLS API exited before binding")
                port=json.loads(line)["port"]
                context=ssl.create_default_context(cafile=str(root/"ca.pem"))
                def call(method,path,body=None,headers=None,ctx=context):
                    connection=http.client.HTTPSConnection("127.0.0.1",port,context=ctx,timeout=8)
                    try:
                        data=json.dumps(body) if body is not None else None
                        outgoing={**(headers or {})}
                        if data is not None:outgoing["Content-Type"]="application/json"
                        connection.request(method,path,data,outgoing)
                        response=connection.getresponse();return response.status,json.loads(response.read())
                    finally:connection.close()
                self.assertEqual(call("GET","/api/health")[0],200)
                with self.assertRaises(ssl.SSLCertVerificationError):
                    call("GET","/api/health",ctx=ssl.create_default_context())
                epoch=int(time.time())
                token=jwt.encode({"hellabit_grant":grant["token_claims"]["hellabit_grant"],"iss":"https://issuer.example.invalid","aud":"hellabit-api",
                    "sub":"merchant","iat":epoch-1,"exp":epoch+300},signer,algorithm="RS256",
                    headers={"kid":"key-1","typ":"at+jwt"})
                headers={"Authorization":"Bearer "+token}
                status,result=call("GET","/api/v1/workspaces/"+tenant,headers=headers)
                self.assertEqual(status,200);self.assertIn("assets",result)
                self.assertEqual(call("POST","/api/v1/workspaces/"+tenant+"/sandbox/fund",{"amount":100},headers)[0],403)
                self.assertEqual(call("GET","/api/v1/workspaces/"+tenant,headers={**headers,"Host":"unapproved.example"})[0],403)
                self.assertEqual(book.reconcile(tenant)["journal_count"],0)
            finally:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                child.stdout.close();child.stderr.close()
