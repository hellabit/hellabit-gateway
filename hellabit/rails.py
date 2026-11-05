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
        observed,expires=body.get('observed_at'),body.get('valid_until')
        if body.get('schema')=='hellabit.reserve-deposit.v1' and customer=='issuer-reserve':
            if type(observed) is not int or type(expires) is not int or not 0<expires-observed<=86400:
                raise Error('invalid_bank_observation_time',403)
            expected.update({'schema':body['schema'],'observed_at':observed,'valid_until':expires})
        if body != expected or asset not in ("CAD", "USD"):
            raise Error("bank_evidence_binding_mismatch", 403)
        identifier(body["reference"]); units(amount)
        return {"reference": body["reference"], "proof_hash": digest(envelope),
                "observed_at":observed,"valid_until":expires}

    def deposit_event(self,tenant,customer,asset,amount,reference):
        if customer!='issuer-reserve':raise Error('reserve_deposit_purpose_required',400)
        body=self.event(tenant,customer,asset,amount,reference)['body']
        observed = self.ledger.clock()
        body.update({'schema':'hellabit.reserve-deposit.v1','observed_at':observed,
                     'valid_until':observed+86400})
        return {'body':body,'mac':hmac.new(self._key(),canonical(body).encode(),hashlib.sha256).hexdigest()}

    def balance_event(self,tenant,asset,balance,reference):
        with self.ledger.transaction() as db:
            return self._balance_event(db,tenant,asset,balance,reference)

    def _balance_event(self,db,tenant,asset,balance,reference):
        units(balance,zero=True);identifier(reference)
        if asset not in ('CAD','USD'):raise Error('bank_currency_not_supported',400)
        account='reserve:'+tenant
        self.ledger.tenant(db,tenant);self.ledger.assert_payment_writes(db)
        old=db.execute('SELECT sequence FROM bank_balance_sequence WHERE tenant=? AND account=?',(tenant,account)).fetchone()
        sequence=(old[0] if old else 0)+1
        db.execute('INSERT OR REPLACE INTO bank_balance_sequence VALUES(?,?,?)',(tenant,account,sequence))
        observed=self.ledger.clock()
        body={'schema':'hellabit.reserve-balance.v1','mode':'sandbox','bank':'private-test-bank',
              'workspace':tenant,'account':account,'asset':asset,'balance':balance,'reference':reference,
              'observed_at':observed,'valid_until':observed+86400,'sequence':sequence}
        return {'body':body,'mac':hmac.new(self._key(),canonical(body).encode(),hashlib.sha256).hexdigest()}

    def verify_balance(self,envelope,tenant,asset,balance,*,fresh=True):
        if not isinstance(envelope,dict) or set(envelope)!={'body','mac'} or not isinstance(envelope['body'],dict):
            raise Error('invalid_balance_evidence',403)
        body=envelope['body'];expected={'schema','mode','bank','workspace','account','asset','balance',
                                      'reference','observed_at','valid_until','sequence'}
        if set(body)!=expected or not isinstance(envelope['mac'],str) or not hmac.compare_digest(
                hmac.new(self._key(),canonical(body).encode(),hashlib.sha256).hexdigest(),envelope['mac']):
            raise Error('balance_evidence_authentication_failed',403)
        if (body['schema']!='hellabit.reserve-balance.v1' or body['mode']!='sandbox' or body['bank']!='private-test-bank'
                or body['workspace']!=tenant or body['account']!='reserve:'+tenant or body['asset']!=asset
                or type(body['balance']) is not int or body['balance']!=balance):
            raise Error('reserve_balance_mismatch',409)
        identifier(body['reference'])
        if (any(type(body[k]) is not int for k in ('observed_at','valid_until','sequence')) or body['sequence']<1
                or not 0<body['valid_until']-body['observed_at']<=86400):
            raise Error('invalid_balance_observation_time',403)
        if fresh and (not self.ledger.clock()-300<=body['observed_at']<=self.ledger.clock()+15 or body['valid_until']<=self.ledger.clock()):
            raise Error('balance_observation_stale',403)
        return {**body,'proof_hash':digest(envelope)}

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
