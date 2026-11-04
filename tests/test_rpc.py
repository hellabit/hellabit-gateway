import json
import ssl
import unittest
from unittest.mock import patch
from hellabit.ledger import Error
from hellabit.rpc import ReadOnlyRpc, PinnedHTTPSConnection

class RpcTests(unittest.TestCase):
    def setUp(self):
        self.payload = None
        self.status, self.raw, self.response_id = 200, None, None
        self.result = "0x2105"
        self.context = None
        self.connections = 0
        owner = self
        class Response:
            status = 200
            def getheader(self, name): return None
            def read(self, limit):
                return owner.raw if owner.raw is not None else json.dumps(
                    {"jsonrpc":"2.0","id":owner.payload["id"] if owner.response_id is None else owner.response_id,
                     "result":owner.result}).encode()
        class Connection:
            def __init__(self, host, address, timeout, context):
                owner.connections += 1; owner.context = context; owner.pinned_address = address
            def request(self, method, path, body, headers): owner.payload = json.loads(body)
            def getresponse(self):
                result = Response(); result.status = owner.status
                return result
            def close(self): pass
        self.dns = patch("hellabit.rpc.socket.getaddrinfo", return_value=[(2,1,6,"",("8.8.8.8",443))])
        self.connection = patch("hellabit.rpc.PinnedHTTPSConnection", Connection)
        self.dns.start(); self.connection.start()
        self.addCleanup(self.dns.stop); self.addCleanup(self.connection.stop)

    def client(self, **kwargs):
        return ReadOnlyRpc("https://rpc.example/private-endpoint-key", allowed_hosts={"rpc.example"},
                           chain_id=8453, **kwargs)

    def test_correct_chain_uses_checked_tls_and_a_pinned_global_address(self):
        client = self.client()
        self.assertEqual(client("eth_chainId", []), "0x2105")
        self.assertEqual(self.pinned_address, "8.8.8.8")
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(self.context.check_hostname)
        self.assertNotIn("private-endpoint-key", str(client.health()))

    def test_mutation_methods_rejected_before_transport(self):
        with self.assertRaisesRegex(Error, "rpc_write_method_forbidden"):
            self.client()("eth_sendRawTransaction", ["secret-signature"])
        self.assertEqual(self.connections, 0)

    def test_private_mixed_and_link_local_resolution_are_rejected(self):
        for ip in ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "::ffff:127.0.0.1"]:
            with self.subTest(ip=ip), patch("hellabit.rpc.socket.getaddrinfo",
                                          return_value=[(2,1,6,"",(ip,443)),(2,1,6,"",("8.8.8.8",443))]):
                with self.assertRaisesRegex(Error, "rpc_nonpublic_address_rejected"):
                    self.client()("eth_chainId", [])
        self.assertEqual(self.connections, 0)

    def test_network_mismatch_redirect_and_bad_response_identity_fail_closed(self):
        self.result = "0x1"
        with self.assertRaisesRegex(Error, "chain_mismatch"): self.client()("eth_chainId", [])
        self.status = 302
        with self.assertRaisesRegex(Error, "rpc_redirect_rejected"): self.client()("eth_blockNumber", [])
        self.status, self.response_id = 200, True
        with self.assertRaisesRegex(Error, "invalid_rpc_response"): self.client()("eth_blockNumber", [])

    def test_duplicate_keys_nonfinite_and_oversized_response_are_rejected(self):
        for raw in [b'{"jsonrpc":"2.0","id":1,"id":1,"result":"0x1"}',
                    b'{"jsonrpc":"2.0","id":1,"result":NaN}']:
            self.raw = raw
            with self.assertRaisesRegex(Error, "invalid_rpc_response"): self.client()("eth_blockNumber", [])
        self.raw = b"x" * 1025
        with self.assertRaisesRegex(Error, "rpc_response_too_large"): self.client(max_bytes=1024)("eth_blockNumber", [])

    def test_bounded_retry_then_circuit_prevents_repeated_outage_requests(self):
        self.status = 503
        client = self.client()
        for _ in range(2):
            with self.assertRaisesRegex(Error, "rpc_transport_unavailable"): client("eth_blockNumber", [])
        calls = self.connections
        with self.assertRaisesRegex(Error, "rpc_circuit_open"): client("eth_blockNumber", [])
        self.assertEqual(self.connections, calls)

    def test_endpoint_enrollment_rejects_http_auth_query_and_unapproved_host(self):
        for url in ["http://rpc.example/", "https://user:password@rpc.example/",
                    "https://rpc.example/?secret=key", "https://unapproved.example/"]:
            with self.subTest(url=url), self.assertRaises(Error):
                ReadOnlyRpc(url, allowed_hosts={"rpc.example"}, chain_id=8453)
