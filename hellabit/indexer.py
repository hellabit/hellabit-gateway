"""A contract-scoped event projection with preserved history and finalized fences."""
from __future__ import annotations
import re
import fcntl
import os
import stat
from .control import audit, ensure_schema
from .ledger import Error, canonical, digest, identifier
from .rails import TRANSFER

MAX_BLOCK_EVENTS = 4096
MAX_LOG_RESPONSE_BYTES = 4_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_bindings(
 id TEXT PRIMARY KEY,tenant TEXT NOT NULL,chain_id INTEGER NOT NULL,contract TEXT NOT NULL,
 start_block INTEGER NOT NULL,cursor INTEGER NOT NULL,cursor_hash TEXT,
 finalized INTEGER NOT NULL DEFAULT -1,halted TEXT,updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chain_blocks(
 binding TEXT NOT NULL,height INTEGER NOT NULL,hash TEXT NOT NULL,parent TEXT NOT NULL,
 canonical INTEGER NOT NULL,finalized INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(binding,hash));
CREATE UNIQUE INDEX IF NOT EXISTS canonical_chain_height ON chain_blocks(binding,height) WHERE canonical=1;
CREATE TABLE IF NOT EXISTS chain_events(
 binding TEXT NOT NULL,block_hash TEXT NOT NULL,tx_hash TEXT NOT NULL,log_index INTEGER NOT NULL,
 body TEXT NOT NULL,proof_hash TEXT NOT NULL,canonical INTEGER NOT NULL,finalized INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(binding,block_hash,tx_hash,log_index));
"""

def hash32(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise Error("invalid_chain_hash", 400)
    return value.lower()

def evm_address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise Error("invalid_evm_address", 400)
    return value.lower()

def integer(value, *, maximum=2**63 - 1):
    if type(value) is not int or not 0 <= value <= maximum: raise Error("invalid_chain_integer", 400)
    return value

class EventIndexer:
    def __init__(self, book):
        self.book = book
        ensure_schema(book, "indexer", SCHEMA)

    def bind(self, tenant, binding, chain_id, contract, start_block=0):
        identifier(binding); integer(chain_id); integer(start_block)
        if chain_id == 0: raise Error("invalid_chain_binding", 400)
        contract = evm_address(contract)
        with self.book.transaction() as db:
            self.book.tenant(db, tenant)
            old = db.execute("SELECT * FROM chain_bindings WHERE id=?", (binding,)).fetchone()
            if old:
                if any(old[k] != v for k, v in {"tenant": tenant, "chain_id": chain_id,
                        "contract": contract, "start_block": start_block}.items()):
                    raise Error("chain_binding_immutable", 409)
                return dict(old)
            db.execute("""INSERT INTO chain_bindings
                (id,tenant,chain_id,contract,start_block,cursor,updated) VALUES(?,?,?,?,?,?,?)""",
                (binding, tenant, chain_id, contract, start_block, start_block - 1, self.book.clock()))
            audit(self.book, db, tenant, "chain-bound", binding, {"chain_id": chain_id, "start_block": start_block})
        return self.state(tenant, binding)

    def _binding(self, db, tenant, binding):
        row = db.execute("SELECT * FROM chain_bindings WHERE id=? AND tenant=?", (binding, tenant)).fetchone()
        if not row: raise Error("chain_binding_not_found", 404)
        return dict(row)

    def state(self, tenant, binding):
        with self.book.db() as db: return self._binding(db, tenant, binding)

    def _halt(self, tenant, binding, reason):
        with self.book.transaction() as db:
            self._binding(db, tenant, binding)
            db.execute("UPDATE chain_bindings SET halted=?,updated=? WHERE id=?", (reason, self.book.clock(), binding))
            audit(self.book, db, tenant, "chain-halted", binding, {"reason": reason})

    def ingest(self, tenant, binding, height, block_hash, parent_hash, events):
        integer(height); block_hash, parent_hash = hash32(block_hash), hash32(parent_hash)
        if not isinstance(events, list) or len(events) > MAX_BLOCK_EVENTS: raise Error("chain_event_limit", 400)
        normalized, ids = [], set()
        for event in events:
            if not isinstance(event, dict) or set(event) != {"tx_hash", "log_index", "from", "to", "amount"}:
                raise Error("invalid_chain_event", 400)
            item = {"tx_hash": hash32(event["tx_hash"]), "log_index": integer(event["log_index"]),
                    "from": evm_address(event["from"]), "to": evm_address(event["to"]),
                    "amount": integer(event["amount"], maximum=2**256 - 1)}
            identity = item["tx_hash"], item["log_index"]
            if identity in ids: raise Error("duplicate_chain_log", 400)
            ids.add(identity); normalized.append(item)
        normalized.sort(key=lambda e: (e["log_index"], e["tx_hash"]))
        with self.book.transaction() as db:
            state = self._binding(db, tenant, binding)
            if state["halted"]: raise Error("chain_projection_halted", 423)
            old = db.execute("SELECT * FROM chain_blocks WHERE binding=? AND hash=?", (binding, block_hash)).fetchone()
            if old:
                prior = [r[0] for r in db.execute(
                    "SELECT proof_hash FROM chain_events WHERE binding=? AND block_hash=? ORDER BY log_index,tx_hash",
                    (binding, block_hash))]
                if old["height"] != height or old["parent"] != parent_hash or prior != [digest(e) for e in normalized]:
                    raise Error("chain_event_conflict", 409)
                if old["canonical"]:
                    return {"replayed": True, "cursor": state["cursor"]}
            if height != state["cursor"] + 1: raise Error("chain_gap_requires_backfill", 409)
            if state["cursor_hash"] and parent_hash != state["cursor_hash"]:
                raise Error("chain_parent_mismatch", 409)
            db.execute("""INSERT INTO chain_blocks VALUES(?,?,?,?,1,0)
                ON CONFLICT(binding,hash) DO UPDATE SET canonical=1""", (binding, height, block_hash, parent_hash))
            for event in normalized:
                db.execute("""INSERT INTO chain_events VALUES(?,?,?,?,?,?,1,0)
                    ON CONFLICT(binding,block_hash,tx_hash,log_index) DO UPDATE SET canonical=1""",
                    (binding, block_hash, event["tx_hash"], event["log_index"], canonical(event), digest(event)))
            db.execute("UPDATE chain_bindings SET cursor=?,cursor_hash=?,updated=? WHERE id=?",
                       (height, block_hash, self.book.clock(), binding))
        return {"replayed": False, "cursor": height, "events": len(normalized), "financial_postings": 0}

    def rewind(self, tenant, binding, height, block_hash):
        bad=False
        with self.book.transaction() as db:
            state = self._binding(db, tenant, binding)
            if state["halted"]: raise Error("chain_projection_halted", 423)
            if type(height) is not int or height < state["start_block"]-1:raise Error("invalid_rewind",400)
            if height<state["finalized"]:
                bad=True
                db.execute("UPDATE chain_bindings SET halted='finalized_history_contradiction',updated=? WHERE id=?",(self.book.clock(),binding))
                audit(self.book,db,tenant,"chain-halted",binding,{"reason":"finalized_history_contradiction"})
            if not bad:
                self._rewind_tx(db,tenant,binding,height,block_hash,state)
        if bad:raise Error("finalized_history_contradiction",423)

    def _rewind_tx(self,db,tenant,binding,height,block_hash,state):
        if height > state["cursor"]: raise Error("invalid_rewind", 400)
        expected = db.execute("SELECT hash FROM chain_blocks WHERE binding=? AND height=? AND canonical=1",
                              (binding, height)).fetchone()
        if height >= state["start_block"]:
            if not expected or expected["hash"] != hash32(block_hash): raise Error("unknown_rewind_ancestor", 409)
        elif block_hash is not None:
            raise Error("invalid_rewind", 400)
        db.execute("""UPDATE chain_events SET canonical=0 WHERE binding=? AND block_hash IN
            (SELECT hash FROM chain_blocks WHERE binding=? AND height>?)""", (binding, binding, height))
        db.execute("UPDATE chain_blocks SET canonical=0 WHERE binding=? AND height>?", (binding, height))
        db.execute("UPDATE chain_bindings SET cursor=?,cursor_hash=?,updated=? WHERE id=?",
                   (height, block_hash, self.book.clock(), binding))
        audit(self.book, db, tenant, "chain-rewound", binding, {"height": height})

    def finalize(self, tenant, binding, height, block_hash):
        integer(height); block_hash = hash32(block_hash)
        bad = False
        with self.book.transaction() as db:
            state = self._binding(db, tenant, binding)
            if state["halted"]: raise Error("chain_projection_halted", 423)
            row = db.execute("SELECT hash FROM chain_blocks WHERE binding=? AND height=? AND canonical=1",
                             (binding, height)).fetchone()
            if not row or row["hash"] != block_hash:
                bad = True
                db.execute("UPDATE chain_bindings SET halted='finalized_history_contradiction',updated=? WHERE id=?",(self.book.clock(),binding))
                audit(self.book,db,tenant,"chain-halted",binding,{"reason":"finalized_history_contradiction"})
            elif height < state["finalized"]:
                raise Error("finality_cannot_regress", 409)
            else:
                db.execute("UPDATE chain_blocks SET finalized=1 WHERE binding=? AND height<=? AND canonical=1", (binding, height))
                db.execute("""UPDATE chain_events SET finalized=1 WHERE binding=? AND canonical=1 AND block_hash IN
                    (SELECT hash FROM chain_blocks WHERE binding=? AND height<=? AND canonical=1)""", (binding, binding, height))
                db.execute("UPDATE chain_bindings SET finalized=?,updated=? WHERE id=?", (height, self.book.clock(), binding))
        if bad:
            raise Error("finalized_history_contradiction", 423)

    def events(self, tenant, binding, *, finalized_only=True):
        with self.book.db() as db:
            self._binding(db, tenant, binding)
            rows=db.execute("""SELECT * FROM chain_events WHERE binding=? AND canonical=1
                AND (?=0 OR finalized=1) ORDER BY rowid LIMIT 1001""", (binding, int(finalized_only))).fetchall()
            if len(rows)>1000:raise Error('chain_event_page_required',409)
            return [dict(row) for row in rows]

    def event_page(self,tenant,binding,*,cursor=None,limit=1000):
        """Read a bounded page at a fixed finalized block, with a stable order."""
        if type(limit) is not int or not 1<=limit<=1000:
            raise Error('invalid_event_page_limit',400)
        with self.book.db() as db:
            db.execute('BEGIN')
            state=self._binding(db,tenant,binding)
            through=state['finalized']
            position=(-1,-1,'')
            if cursor is not None:
                try:
                    anchor,height,log,transaction=cursor.split('/')
                    through,height,log=(integer(int(value)) for value in (anchor,height,log))
                    position=(height,log,hash32(transaction))
                except (AttributeError,ValueError,Error):
                    raise Error('invalid_chain_event_cursor',400) from None
                if height>through or through>state['finalized']:
                    raise Error('invalid_chain_event_cursor',400)
            rows=db.execute("""SELECT e.*,b.height FROM chain_blocks b JOIN chain_events e
                ON e.binding=b.binding AND e.block_hash=b.hash
                WHERE b.binding=? AND b.canonical=1 AND b.finalized=1 AND e.canonical=1 AND e.finalized=1
                  AND b.height<=? AND (b.height,e.log_index,e.tx_hash)>(?,?,?)
                ORDER BY b.height,e.log_index,e.tx_hash LIMIT ?""",
                (binding,through,*position,limit+1)).fetchall()
            page=[dict(row) for row in rows[:limit]]
            last=page[-1] if page else None
            next_cursor=(f"{through}/{last['height']}/{last['log_index']}/{last['tx_hash']}"
                         if len(rows)>limit else None)
            return {'events':page,'through_block':through,'next_cursor':next_cursor,
                    'complete':next_cursor is None,'halted':state['halted']}

    def sync(self, tenant, binding, rpc, max_blocks=20):
        identifier(binding)
        self.state(tenant, binding)
        lock = self.book.path.with_name(self.book.path.name + ".indexer-" + binding + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise Error("unsafe_indexer_lock", 403)
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Error("indexer_writer_busy", 423) from None
            return self._sync(tenant, binding, rpc, max_blocks)
        finally:
            os.close(fd)

    def _sync(self, tenant, binding, rpc, max_blocks=20):
        if type(max_blocks) is not int or not 1 <= max_blocks <= 100: raise Error("invalid_sync_limit", 400)
        state = self.state(tenant, binding)
        if state["halted"]: raise Error("chain_projection_halted", 423)
        if int(rpc("eth_chainId", []), 16) != state["chain_id"]: raise Error("chain_mismatch", 403)
        if state["cursor_hash"]:
            current = rpc("eth_getBlockByNumber", [hex(state["cursor"]), False])
            if not current or current.get("hash", "").lower() != state["cursor_hash"]:
                ancestor = state["cursor"] - 1
                found = False
                for _ in range(64):
                    if ancestor < state["start_block"]:
                        if state["finalized"] >= state["start_block"]: break
                        self.rewind(tenant, binding, state["start_block"] - 1, None); found = True; break
                    with self.book.db() as db:
                        old = db.execute("SELECT hash FROM chain_blocks WHERE binding=? AND height=? AND canonical=1",
                                         (binding, ancestor)).fetchone()
                    remote = rpc("eth_getBlockByNumber", [hex(ancestor), False])
                    if old and remote and old["hash"] == remote.get("hash", "").lower():
                        self.rewind(tenant, binding, ancestor, old["hash"]); found = True; break
                    if ancestor <= state["finalized"]: break
                    ancestor -= 1
                if not found:
                    self._halt(tenant, binding, "reorg_requires_independent_review")
                    raise Error("chain_projection_halted", 423)
                state = self.state(tenant, binding)
        tip = int(rpc("eth_blockNumber", []), 16)
        target = min(tip, state["cursor"] + max_blocks)
        for height in range(state["cursor"] + 1, target + 1):
            block = rpc("eth_getBlockByNumber", [hex(height), False])
            if not isinstance(block, dict) or int(block.get("number", "-1"), 16) != height:
                raise Error("rpc_block_missing", 503)
            block_hash = hash32(block.get("hash"))
            logs = rpc("eth_getLogs", [{"address": state["contract"], "blockHash": block_hash,
                                       "topics": [TRANSFER]}])
            if not isinstance(logs, list) or len(logs) > MAX_BLOCK_EVENTS: raise Error("chain_event_limit", 400)
            events = []
            for log in logs:
                if not isinstance(log, dict):
                    raise Error("rpc_log_binding_mismatch", 403)
                topics = log.get("topics")
                if (log.get("removed") or log.get("address", "").lower() != state["contract"] or
                        log.get("blockHash", "").lower() != block_hash or
                        int(log.get("blockNumber", "-1"), 16) != height or
                        not isinstance(topics, list) or len(topics) != 3 or topics[0] != TRANSFER or
                        any(not re.fullmatch(r"0x0{24}[0-9a-fA-F]{40}", t) for t in topics[1:]) or
                        not re.fullmatch(r"0x[0-9a-fA-F]{64}", log.get("data", ""))):
                    raise Error("rpc_log_binding_mismatch", 403)
                events.append({"tx_hash": log["transactionHash"], "log_index": int(log["logIndex"], 16),
                               "from": "0x" + topics[1][-40:], "to": "0x" + topics[2][-40:],
                               "amount": int(log["data"], 16)})
            self.ingest(tenant, binding, height, block_hash, block["parentHash"], events)
        final = rpc("eth_getBlockByNumber", ["finalized", False])
        cursor = self.state(tenant, binding)["cursor"]
        if final and state["start_block"] <= int(final["number"], 16) <= cursor:
            self.finalize(tenant, binding, int(final["number"], 16), final["hash"])
        elif final and int(final["number"], 16) > cursor and cursor >= state["start_block"]:
            with self.book.db() as db:
                row = db.execute("SELECT hash FROM chain_blocks WHERE binding=? AND height=? AND canonical=1",
                                 (binding, cursor)).fetchone()
            # Bind the processed prefix to a freshly read canonical block as well.
            check = rpc("eth_getBlockByNumber", [hex(cursor), False])
            if not row or not check or check.get("hash", "").lower() != row["hash"]:
                raise Error("chain_parent_mismatch", 409)
            self.finalize(tenant, binding, cursor, row["hash"])
        return {**self.state(tenant, binding), "financial_postings": 0}
