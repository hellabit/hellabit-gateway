from pathlib import Path
import tempfile
import unittest
import fcntl
import os
from contextlib import contextmanager
from hellabit.ledger import Ledger, Error
from hellabit.indexer import EventIndexer
from hellabit.rails import TRANSFER

def h(value): return "0x" + format(value, "064x")
FROM, TO, TOKEN = "0x"+"11"*20, "0x"+"22"*20, "0x"+"33"*20
def event(height): return {"tx_hash":h(height+1000),"log_index":0,"from":FROM,"to":TO,"amount":100}

class IndexerTests(unittest.TestCase):
    def test_finality_advance_between_read_and_write_cannot_be_rewound(self):
        self.ingest(10);self.ingest(11)
        self.index.finalize(self.tenant,"watched-usdc",10,h(10))
        original=self.book.transaction
        @contextmanager
        def racing_transaction():
            self.book.transaction=original
            self.index.finalize(self.tenant,"watched-usdc",11,h(11))
            with original() as db:yield db
        self.book.transaction=racing_transaction
        with self.assertRaisesRegex(Error,"finalized_history_contradiction"):
            self.index.rewind(self.tenant,"watched-usdc",10,h(10))
        state=self.index.state(self.tenant,"watched-usdc")
        self.assertEqual(state["cursor"],11);self.assertEqual(state["finalized"],11)
        self.assertEqual(state["halted"],"finalized_history_contradiction")
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc")),2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = Ledger(Path(self.tmp.name) / "ledger.sqlite3")
        self.tenant = self.book.create_tenant("Chain Merchant")["tenant"]
        self.index = EventIndexer(self.book)
        self.index.bind(self.tenant, "watched-usdc", 8453, TOKEN, 10)

    def tearDown(self): self.tmp.cleanup()

    def ingest(self, n): return self.index.ingest(self.tenant,"watched-usdc",n,h(n),h(n-1),[event(n)])

    def test_finalized_page_walks_beyond_one_thousand_at_the_original_boundary(self):
        binding='watched-usdc'
        logs=[{**event(10),'tx_hash':h(2000+i),'log_index':i} for i in range(1000)]
        self.index.ingest(self.tenant,binding,10,h(10),h(9),logs)
        self.ingest(11)
        self.index.finalize(self.tenant,binding,11,h(11))
        with self.assertRaisesRegex(Error,'chain_event_page_required'):
            self.index.events(self.tenant,binding)
        first=self.index.event_page(self.tenant,binding)
        self.assertEqual(len(first['events']),1000)
        self.assertFalse(first['complete'])
        self.ingest(12)
        self.index.finalize(self.tenant,binding,12,h(12))
        second=self.index.event_page(self.tenant,binding,cursor=first['next_cursor'])
        self.assertEqual([row['height'] for row in second['events']],[11])
        self.assertEqual(second['through_block'],11)
        self.assertTrue(second['complete'])

    def test_idempotent_block_and_log_conflicts_preserve_original(self):
        self.ingest(10)
        self.assertTrue(self.ingest(10)["replayed"])
        changed = event(10); changed["amount"] = 101
        with self.assertRaisesRegex(Error,"chain_event_conflict"):
            self.index.ingest(self.tenant,"watched-usdc",10,h(10),h(9),[changed])
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc",finalized_only=False)),1)

    def test_gap_parent_and_duplicate_log_rejected(self):
        with self.assertRaisesRegex(Error,"chain_gap_requires_backfill"): self.ingest(11)
        self.ingest(10)
        with self.assertRaisesRegex(Error,"chain_parent_mismatch"):
            self.index.ingest(self.tenant,"watched-usdc",11,h(11),h(99),[event(11)])
        with self.assertRaisesRegex(Error,"duplicate_chain_log"):
            self.index.ingest(self.tenant,"watched-usdc",11,h(11),h(10),[event(11),event(11)])

    def test_tentative_rewind_retains_history_and_new_chain_events(self):
        for n in [10,11,12]:self.ingest(n)
        self.index.finalize(self.tenant,"watched-usdc",10,h(10))
        self.index.rewind(self.tenant,"watched-usdc",10,h(10))
        self.index.ingest(self.tenant,"watched-usdc",11,h(111),h(10),[event(111)])
        with self.book.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_events").fetchone()[0],4)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chain_events WHERE canonical=0").fetchone()[0],2)
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc")),1)
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc",finalized_only=False)),2)
        self.assertEqual(self.book.reconcile(self.tenant)["journal_count"],0)

    def test_reusing_old_hash_cannot_change_retracted_event_bytes(self):
        self.ingest(10);self.ingest(11)
        self.index.rewind(self.tenant,"watched-usdc",10,h(10))
        changed = event(11);changed["amount"]=999
        with self.assertRaisesRegex(Error,"chain_event_conflict"):
            self.index.ingest(self.tenant,"watched-usdc",11,h(11),h(10),[changed])

    def test_finalized_rewrite_halts_without_erasing_canonical_history(self):
        self.ingest(10);self.ingest(11)
        self.index.finalize(self.tenant,"watched-usdc",11,h(11))
        with self.assertRaisesRegex(Error,"finalized_history_contradiction"):
            self.index.rewind(self.tenant,"watched-usdc",10,h(10))
        self.assertEqual(self.index.state(self.tenant,"watched-usdc")["cursor"],11)
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc")),2)
        with self.assertRaisesRegex(Error,"chain_projection_halted"):self.ingest(12)

    def test_binding_is_immutable_and_tenant_scoped(self):
        with self.assertRaisesRegex(Error,"chain_binding_immutable"):
            self.index.bind(self.tenant,"watched-usdc",1,TOKEN,10)
        other = self.book.create_tenant("Other Merchant")["tenant"]
        with self.assertRaisesRegex(Error,"chain_binding_not_found"):
            self.index.state(other,"watched-usdc")

    def test_competing_indexer_stops_before_network_io(self):
        lock=self.book.path.with_name(self.book.path.name+".indexer-watched-usdc.lock")
        fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            def forbidden(*_):raise AssertionError("A second writer must not contact RPC")
            with self.assertRaisesRegex(Error,"indexer_writer_busy"):
                self.index.sync(self.tenant,"watched-usdc",forbidden)
        finally:os.close(fd)

    def rpc(self, method, params):
        if method=="eth_chainId":return hex(8453)
        if method=="eth_blockNumber":return hex(12)
        if method=="eth_getBlockByNumber":
            n=11 if params[0]=="finalized" else int(params[0],16)
            return {"number":hex(n),"hash":h(n),"parentHash":h(n-1)}
        if method=="eth_getLogs":
            n=int(params[0]["fromBlock"],16)
            return [{"transactionHash":h(n+1000),"logIndex":"0x0","blockNumber":hex(n),
                     "blockHash":h(n),"address":TOKEN,"removed":False,
                     "topics":[TRANSFER,"0x"+FROM[2:].rjust(64,"0"),"0x"+TO[2:].rjust(64,"0")],
                     "data":h(100)}]
        raise AssertionError(method)

    def test_sync_backfills_bounded_blocks_and_uses_finalized_tag(self):
        first=self.index.sync(self.tenant,"watched-usdc",self.rpc,max_blocks=1)
        self.assertEqual(first["cursor"],10);self.assertEqual(first["finalized"],10)
        final=self.index.sync(self.tenant,"watched-usdc",self.rpc,max_blocks=3)
        self.assertEqual(final["cursor"],12);self.assertEqual(final["finalized"],11)
        self.assertEqual(len(self.index.events(self.tenant,"watched-usdc")),2)
        self.assertEqual(final["financial_postings"],0)

    def test_rpc_wrong_network_and_log_contract_cannot_advance_cursor(self):
        with self.assertRaisesRegex(Error,"chain_mismatch"):
            self.index.sync(self.tenant,"watched-usdc",lambda method,params:hex(1))
        def bad(method,params):
            result=self.rpc(method,params)
            if method=="eth_getLogs":result[0]["address"]=FROM
            return result
        with self.assertRaisesRegex(Error,"rpc_log_binding_mismatch"):
            self.index.sync(self.tenant,"watched-usdc",bad)
        self.assertEqual(self.index.state(self.tenant,"watched-usdc")["cursor"],9)
