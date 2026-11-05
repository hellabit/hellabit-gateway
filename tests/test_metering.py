from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
from hellabit.ledger import Ledger,Error
from hellabit.metering import UsageMeter

def stamp(month):return int(datetime(2026,month,15,tzinfo=timezone.utc).timestamp())

class MeterTests(unittest.TestCase):
    def test_running_totals_are_constant_work_and_invoice_close_checks_the_event_history(self):
        statements=[];original=self.book.db
        def traced():
            db=original();db.set_trace_callback(statements.append);return db
        self.book.db=traced
        for n in range(100):self.meter.record(self.tenant,"managed","batch-"+str(n),10)
        self.assertFalse(any("SUM(micro)" in sql for sql in statements))
        self.assertEqual(self.meter.preview(self.tenant,"managed")["usage_micro"],1000000)
        with self.book.db() as db:db.execute("UPDATE usage_totals SET micro=micro+1")
        self.now=stamp(12)
        with self.assertRaisesRegex(Error,"usage_total_mismatch"):self.meter.close(self.tenant,"managed","2026-11")
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=stamp(11)
        self.book=Ledger(Path(self.tmp.name)/"ledger.sqlite3",clock=lambda:self.now)
        self.tenant=self.book.create_tenant("Meter Merchant")["tenant"]
        self.meter=UsageMeter(self.book)
        self.meter.configure(self.tenant,"managed","USD",1000)

    def tearDown(self):self.tmp.cleanup()

    def test_ten_tenth_cent_events_become_one_native_cent(self):
        for n in range(10):self.meter.record(self.tenant,"managed","event-"+str(n))
        preview=self.meter.preview(self.tenant,"managed")
        self.assertEqual(preview["usage_minor"],1)
        self.assertEqual(preview["closing_carry_micro"],0)
        self.assertFalse(preview["collection_enabled"])
        self.assertEqual(self.book.reconcile(self.tenant)["journal_count"],0)

    def test_fractional_remainder_survives_months_without_double_billing(self):
        self.meter.configure(self.tenant,"fractional","CAD",3500,base_minor=499)
        for n in range(3):self.meter.record(self.tenant,"fractional","nov-"+str(n))
        self.now=stamp(12)
        november=self.meter.close(self.tenant,"fractional","2026-11")
        self.assertEqual((november["due_minor"],november["closing_carry_micro"]),(500,500))
        self.meter.record(self.tenant,"fractional","dec",quantity=3)
        self.now=int(datetime(2027,1,15,tzinfo=timezone.utc).timestamp())
        december=self.meter.close(self.tenant,"fractional","2026-12")
        self.assertEqual((december["usage_minor"],december["closing_carry_micro"]),(1,1000))
        self.assertEqual((november["usage_minor"]+december["usage_minor"])*10000+december["closing_carry_micro"],21000)
        self.assertTrue(self.meter.close(self.tenant,"fractional","2026-12")["replayed"])

    def test_event_replay_recovers_its_original_month_after_clock_changes(self):
        first=self.meter.record(self.tenant,"managed","original")
        self.now=stamp(12)
        replay=self.meter.record(self.tenant,"managed","original")
        self.assertEqual(first["period"],replay["period"]);self.assertTrue(replay["replayed"])
        with self.assertRaisesRegex(Error,"usage_idempotency_conflict"):
            self.meter.record(self.tenant,"managed","original",quantity=2)

    def test_close_requires_finished_period_and_cannot_skip_unbilled_usage(self):
        self.meter.record(self.tenant,"managed","november")
        with self.assertRaisesRegex(Error,"billing_period_still_open"):
            self.meter.close(self.tenant,"managed","2026-11")
        self.now=int(datetime(2027,1,15,tzinfo=timezone.utc).timestamp())
        with self.assertRaisesRegex(Error,"earlier_usage_period_must_close_first"):
            self.meter.close(self.tenant,"managed","2026-12")

    def test_closed_month_rejects_new_backdated_usage_but_returns_old_identity(self):
        self.meter.record(self.tenant,"managed","original")
        self.now=stamp(12);self.meter.close(self.tenant,"managed","2026-11")
        self.now=stamp(11)
        with self.assertRaisesRegex(Error,"billing_period_already_closed"):
            self.meter.record(self.tenant,"managed","new-backdated")
        self.assertTrue(self.meter.record(self.tenant,"managed","original")["replayed"])

    def test_currency_terms_and_tenant_scope_are_enforced(self):
        with self.assertRaisesRegex(Error,"billing_plan_immutable"):
            self.meter.configure(self.tenant,"managed","CAD",1000)
        other=self.book.create_tenant("Other Merchant")["tenant"]
        with self.assertRaisesRegex(Error,"billing_plan_not_found"):
            self.meter.record(other,"managed","same-event")
        for quantity in [True,0,-1,1.5,1000001]:
            with self.assertRaises(Error):self.meter.record(self.tenant,"managed","bad",quantity)
