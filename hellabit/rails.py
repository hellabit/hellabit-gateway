"""Backend-only settlement verification. No account discovery or transaction submission.

Sandbox evidence uses an independent per-database shared key. It cannot be
promoted to production evidence. Bank access needs a separately enrolled
institution connector. The USDC adapter reads an injected trusted RPC transport;
it never accepts a receipt supplied by a customer HTTP request.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import secrets
from .ledger import Error, canonical, digest, identifier, units, USDC_TEST

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BASE_SEPOLIA = {"chain_id": 84532, "asset": USDC_TEST, "token": USDC_TEST.split(":")[-1], "confirmations": 12,
                "source": "https://developers.circle.com/stablecoins/usdc-contract-addresses"}


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or int(value, 16) == 0:
        raise Error("invalid_evm_address", 400)
    return value.lower()


class SandboxBank:
    mode = "sandbox"

    def __init__(self, ledger):
        self.ledger = ledger

    def _key(self):
        with self.ledger.db() as db:
            return bytes.fromhex(db.execute("SELECT value FROM settings WHERE key='sandbox_rail_key'").fetchone()[0])

    def event(self, tenant, customer, asset, amount, reference, direction="credit"):
        units(amount); identifier(reference)
        if asset not in ("CAD", "USD"):
            raise Error("bank_currency_not_supported", 400)
        if direction not in ("credit", "debit"):
            raise Error("invalid_bank_direction", 400)
        body = {"mode": "sandbox", "bank": "private-test-bank", "workspace": tenant, "customer": customer, "direction": direction,
                "asset": asset, "amount": amount, "reference": reference, "status": "settled"}
        return {"body": body, "mac": hmac.new(self._key(), canonical(body).encode(), hashlib.sha256).hexdigest()}

    def verify(self, envelope, tenant, customer, asset, amount, direction="credit"):
        if not isinstance(envelope, dict) or set(envelope) != {"body", "mac"}:
            raise Error("invalid_bank_evidence", 400)
        body = envelope["body"]
        expected_mac = hmac.new(self._key(), canonical(body).encode(), hashlib.sha256).hexdigest()
        if not isinstance(envelope["mac"], str) or not hmac.compare_digest(expected_mac, envelope["mac"]):
            raise Error("bank_evidence_authentication_failed", 403)
        expected = {"mode": "sandbox", "bank": "private-test-bank", "workspace": tenant, "customer": customer, "direction": direction,
                    "asset": asset, "amount": amount, "status": "settled", "reference": body.get("reference")}
        if body != expected or asset not in ("CAD", "USD"):
            raise Error("bank_evidence_binding_mismatch", 403)
        identifier(body["reference"]); units(amount)
        return {"reference": body["reference"], "proof_hash": digest(envelope)}

    def fund(self, tenant, customer, asset, amount, reference):
        proof = self.verify(self.event(tenant, customer, asset, amount, reference), tenant, customer, asset, amount)
        return self.ledger.credit_settlement(tenant, customer, asset, amount, "bank", proof["reference"], proof["proof_hash"])


class UsdcReceiptVerifier:
    """Native token identity + receipt inclusion + canonical block + confirmations.

    The caller enrolls the RPC transport and expected payer/recipient. This is
    receipt verification, not transaction signing, finality insurance or x402.
    """
    def __init__(self, rpc, *, chain_id, token, confirmations=12):
        if type(chain_id) is not int or chain_id <= 0 or type(confirmations) is not int or confirmations < 1:
            raise Error("invalid_chain_binding", 400)
        self.rpc, self.chain_id, self.token, self.confirmations = rpc, chain_id, address(token), confirmations

    def verify(self, transaction_hash, *, payer, recipient, amount, log_index=0):
        if not isinstance(transaction_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", transaction_hash):
            raise Error("invalid_transaction_hash", 400)
        payer, recipient = address(payer), address(recipient); units(amount)
        if type(log_index) is not int or log_index < 0:
            raise Error("invalid_log_index", 400)
        if int(self.rpc("eth_chainId", []), 16) != self.chain_id:
            raise Error("chain_mismatch", 403)
        receipt = self.rpc("eth_getTransactionReceipt", [transaction_hash])
        if not receipt:
            raise Error("settlement_pending", 202)
        if receipt.get("transactionHash", "").lower() != transaction_hash.lower() or int(receipt.get("status", "0x0"), 16) != 1:
            raise Error("transaction_not_successful", 403)
        block_number = int(receipt["blockNumber"], 16)
        block = self.rpc("eth_getBlockByNumber", [hex(block_number), False])
        if not block or block.get("hash") != receipt.get("blockHash"):
            raise Error("receipt_not_canonical", 409)
        tip = int(self.rpc("eth_blockNumber", []), 16)
        if tip - block_number + 1 < self.confirmations:
            raise Error("settlement_pending", 202)
        topic_address = lambda a: "0x" + a[2:].rjust(64, "0")
        matched = [x for x in receipt.get("logs", []) if int(x.get("logIndex", "-1"), 16) == log_index]
        if len(matched) != 1:
            raise Error("transfer_log_missing", 403)
        log = matched[0]
        if log.get("removed") or log.get("address", "").lower() != self.token or log.get("topics") != [TRANSFER, topic_address(payer), topic_address(recipient)] or int(log.get("data", "0x0"), 16) != amount:
            raise Error("transfer_binding_mismatch", 403)
        return {"reference": f"{self.chain_id}:{transaction_hash[2:].lower()}:{log_index}",
                "proof_hash": digest({"chain": self.chain_id, "token": self.token, "receipt": receipt, "log_index": log_index}),
                "confirmations": tip - block_number + 1}


class LiveRail:
    """Explicit enrollment boundary; no fallback, secret store or public egress."""
    def __init__(self, kind):
        if kind not in ("bank", "usdc", "issuer"):
            raise Error("unknown_rail", 400)
        self.kind = kind

    def submit(self, *_args, **_kwargs):
        raise Error("live_connection_not_enrolled", 423)


def sandbox_usdc_funding(ledger, tenant, customer, amount, reference):
    """Private sandbox transfer proof. Deliberately no USDC/network claim."""
    units(amount); identifier(reference)
    return ledger.credit_settlement(tenant, customer, USDC_TEST, amount, "usdc", reference,
        digest({"mode": "sandbox", "kind": "simulated-usdc-transfer", "workspace": tenant,
                "customer": customer, "amount": amount, "reference": reference}))
