"""A disposable recovery journey. No real account, network or funds are used."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hellabit.ledger import Ledger,canonical
from hellabit.rails import SandboxBank
from hellabit.payouts import Payouts
from hellabit.operations import Operations
from hellabit.fixtures import SandboxProvider

with tempfile.TemporaryDirectory(prefix="hellabit-recovery-example-") as tmp:
    book=Ledger(Path(tmp)/"ledger.sqlite3")
    tenant=book.create_tenant("Recovery example")["tenant"]
    SandboxBank(book).fund(tenant,"agent-01","CAD",1000,"opening-test-credit")
    quote=book.create_quote(tenant,"CAD",1000,10)
    work=book.reserve(tenant,"agent-01",quote["quote"],"original-purchase")
    book.run_report(tenant,work["id"])
    payout=Payouts(book).request(tenant,"CAD",50,"synthetic-recipient","original-payout")
    def lose_reply():raise TimeoutError("Accepted upstream; reply lost")
    provider=SandboxProvider(Path(tmp)/"independent-provider.sqlite3",after_accept=lose_reply)
    lost=Operations(book).process_one(tenant,provider)["intent"]
    assert lost["state"]=="unknown"
    recovered=Operations(Ledger(book.path)).reconcile(tenant,payout["intent"],provider)
    assert recovered["state"]=="confirmed" and provider.accepted_count()==1
    assert book.reconcile(tenant)["ok"]
    print(canonical({"mode":"sandbox","reply_lost_state":lost["state"],"recovered_state":recovered["state"],
                     "provider_payments":provider.accepted_count(),"live_funds":False}))
