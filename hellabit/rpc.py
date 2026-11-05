"""Bounded, read-only JSON-RPC over certificate-checked, address-pinned HTTPS."""
from __future__ import annotations
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from .ledger import Error, canonical

READ_METHODS = frozenset(("eth_chainId", "eth_blockNumber", "eth_getBlockByNumber",
                         "eth_getBlockByHash", "eth_getTransactionReceipt", "eth_getLogs",
                         "eth_getBalance", "eth_getTransactionCount", "eth_call"))
_RESOLVERS=threading.BoundedSemaphore(4)

class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, context):
        super().__init__(host, port=443, timeout=timeout, context=context)
        self._pinned_address = address

    def connect(self):
        sock = socket.create_connection((self._pinned_address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host,do_handshake_on_connect=False)
            self.sock.do_handshake()
        except BaseException:
            sock.close()
            raise

def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("Duplicate JSON member")
        result[key] = value
    return result

def _invalid_constant(_value): raise ValueError("Non-finite JSON")

class ReadOnlyRpc:
    def __init__(self, endpoint, *, allowed_hosts, chain_id, timeout=5, max_bytes=2_000_000,
                 retries=1, clock=time.time):
        try:
            url = urlsplit(endpoint)
            port = url.port
            host = url.hostname.encode("idna").decode().lower() if url.hostname else None
        except (ValueError, TypeError): raise Error("invalid_rpc_binding", 400) from None
        if (url.scheme != "https" or not url.hostname or url.username or url.password or
                url.query or url.fragment or port not in (None, 443)):
            raise Error("invalid_rpc_binding", 400)
        if (not isinstance(allowed_hosts,(list,tuple,set,frozenset)) or not allowed_hosts or
                any(not isinstance(h,str) for h in allowed_hosts)):
            raise Error("invalid_rpc_binding",400)
        if host not in {h.lower() for h in allowed_hosts}:
            raise Error("rpc_host_not_enrolled", 403)
        if type(chain_id) is not int or chain_id <= 0:
            raise Error("invalid_chain_binding", 400)
        if (type(timeout) not in (int, float) or not 0 < timeout <= 30 or
                type(max_bytes) is not int or not 1024 <= max_bytes <= 4_000_000 or
                type(retries) is not int or not 0 <= retries <= 2):
            raise Error("invalid_rpc_limits", 400)
        self.host, self.path, self.chain_id = host, url.path or "/", chain_id
        self.timeout, self.max_bytes, self.retries, self.clock = timeout, max_bytes, retries, clock
        self._lock, self._counter = threading.Lock(), 0
        self.failures, self.open_until, self.last_success = 0, 0, None

    def _address(self,deadline):
        # System DNS has no portable cancellation API. Bound both its caller's
        # wall time and the number of outstanding daemon resolvers.
        remaining=deadline-time.monotonic()
        if remaining<=0 or not _RESOLVERS.acquire(timeout=max(0,remaining)):
            raise Error("rpc_deadline_exceeded",503)
        ready=threading.Event();result=[]
        def resolve():
            try:result.append(socket.getaddrinfo(self.host,443,type=socket.SOCK_STREAM))
            except OSError:result.append(None)
            finally:_RESOLVERS.release();ready.set()
        threading.Thread(target=resolve,daemon=True).start()
        if not ready.wait(max(0,deadline-time.monotonic())):raise Error("rpc_deadline_exceeded",503)
        try:
            records=result[0]
            if records is None:raise OSError()
            addresses = sorted({r[4][0] for r in records})
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise Error("rpc_nonpublic_address_rejected", 403)
            return addresses[0]
        except (OSError, ValueError): raise Error("rpc_transport_unavailable", 503) from None

    def _once(self, method, params, id_,deadline):
        address = self._address(deadline)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        remaining=deadline-time.monotonic()
        if remaining<=0:raise Error("rpc_deadline_exceeded",503)
        connection = PinnedHTTPSConnection(self.host, address, remaining, context)
        expired=threading.Event();active_socket=[None]
        def abort():
            expired.set()
            sock=getattr(connection,"sock",None) or active_socket[0]
            if sock is not None:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            connection.close()
        timer=threading.Timer(remaining,abort);timer.daemon=True;timer.start()
        try:
            connection.request("POST", self.path,
                canonical({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}).encode(),
                {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Hellabit-ReadOnly-RPC"})
            active_socket[0]=getattr(connection,"sock",None)
            response = connection.getresponse()
            if time.monotonic()>=deadline:raise Error("rpc_deadline_exceeded",503)
            if 300 <= response.status < 400: raise Error("rpc_redirect_rejected", 403)
            if response.status in (401, 403): raise Error("rpc_access_denied", 403)
            if response.status == 429 or response.status >= 500: raise Error("rpc_transport_unavailable", 503)
            if response.status != 200: raise Error("rpc_provider_rejected", 502)
            length = response.getheader("Content-Length")
            if length is not None:
                try: size = int(length)
                except ValueError: raise Error("invalid_rpc_response", 502) from None
                if size < 0 or size > self.max_bytes: raise Error("rpc_response_too_large", 502)
            data = response.read(self.max_bytes + 1)
            if expired.is_set() or time.monotonic()>=deadline:raise Error("rpc_deadline_exceeded",503)
            if len(data) > self.max_bytes: raise Error("rpc_response_too_large", 502)
            try:
                payload = json.loads(data, object_pairs_hook=_strict_object, parse_constant=_invalid_constant)
            except (ValueError, UnicodeError,RecursionError): raise Error("invalid_rpc_response", 502) from None
            if (not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or
                    type(payload.get("id")) is not int or payload["id"] != id_ or
                    ("result" in payload) == ("error" in payload)):
                raise Error("invalid_rpc_response", 502)
            if "error" in payload: raise Error("rpc_provider_error", 502)
            return payload["result"]
        except (OSError, ssl.SSLError, http.client.HTTPException,AttributeError,ValueError):
            if expired.is_set() or time.monotonic()>=deadline:raise Error("rpc_deadline_exceeded",503) from None
            raise Error("rpc_transport_unavailable", 503) from None
        finally:
            timer.cancel()
            connection.close()

    def __call__(self, method, params):
        if method not in READ_METHODS: raise Error("rpc_write_method_forbidden", 403)
        try:size=len(canonical(params))
        except (ValueError,TypeError,RecursionError):raise Error("invalid_rpc_parameters",400) from None
        if not isinstance(params, list) or len(params) > 10 or size > 32768:
            raise Error("invalid_rpc_parameters", 400)
        with self._lock:
            if self.open_until > self.clock(): raise Error("rpc_circuit_open", 503)
            self._counter += 1
            id_ = self._counter
        deadline=time.monotonic()+self.timeout
        for attempt in range(self.retries + 1):
            try:
                result = self._once(method, params, id_,deadline)
                if method == "eth_chainId":
                    try: correct = int(result, 16) == self.chain_id
                    except (TypeError, ValueError): correct = False
                    if not correct: raise Error("chain_mismatch", 403)
                with self._lock:
                    self.failures, self.open_until, self.last_success = 0, 0, self.clock()
                return result
            except Error as error:
                with self._lock:
                    self.failures += 1
                    if self.failures >= 3: self.open_until = self.clock() + 30
                delay=0.05*(attempt+1)
                if error.status != 503 or attempt == self.retries or time.monotonic()+delay>=deadline: raise
                time.sleep(delay)

    def health(self):
        return {"origin": "https://" + self.host, "chain_id": self.chain_id,
                "read_only": True, "last_success": self.last_success,
                "circuit_open": self.open_until > self.clock(), "failures": self.failures,
                "endpoint_path_and_credentials_included": False}
