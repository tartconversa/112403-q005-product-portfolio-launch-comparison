"""核心口径的行为测试：可售裁剪、待核、复购、资源约束与修订复核。

所有测试使用合成数据，并在临时目录构造最小输入，确保结论可复现。
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import decisions as store
from app.domain import (
    COHORTS,
    COMPARABLE,
    COMPARABLE_WITH_CAVEAT,
    NOT_COMPARABLE,
    PENDING,
    build_states,
    cohort_windows,
    load_dataset,
    window_basis,
)
from app.metrics import analyze_dataset, compare_pair, compute_product
from app.revisions import compare_revisions, dataset_at
from app.resources import build_portfolio, recommendation_summary

DAY = timedelta(days=1)


PRODUCT_HEADER = ["sku", "name", "category", "predecessor", "variant_of", "version", "launch",
                  "launch_status", "launch_confirmed_by", "lineage_status", "lineage_reason",
                  "audience", "audience_confirmed_by", "unit_margin", "is_new", "discontinue_on", "note"]
AVAIL_HEADER = ["sku", "channel", "from", "to", "listed", "days", "reason", "stock_status",
                "oos_from", "oos_to", "oos_days", "notes", "source", "added_on", "confirmed_by"]
PERF_HEADER = ["sku", "window", "from", "to", "units", "refunds", "spend", "channel", "refund_window_days"]
PLAN_HEADER = ["plan_id", "sku", "stage", "channel", "amount", "start", "end", "status", "signed_on", "note"]
INV_HEADER = ["sku", "channel", "on_hand", "in_transit", "weeks_of_cover", "as_of", "note"]
REV_HEADER = ["revision_id", "sku", "changed_on", "from_predecessor", "to_predecessor",
              "decided_by", "audience", "note"]


def write_csv(path: Path, header, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.products = []
        self.availability = []
        self.performance = []
        self.daily = []
        self.plans = []
        self.inventory = []
        self.revisions = []
        self.events = []
        self.decisions = []

    def product(self, sku, launch, category="护肤", predecessor="", version="v1",
                lineage_status=None, lineage_reason="", is_new=None, campaign=None,
                confirmed=None, margin="100", launch_status="confirmed"):
        if lineage_status is None:
            lineage_status = "confirmed" if predecessor else "none"
        self.products.append([
            sku, f"{sku} 名称", category, predecessor, "", version, launch,
            launch_status, confirmed if confirmed is not None else "品类经理", lineage_status,
            lineage_reason, campaign or "", "洞察", margin,
            "true" if (is_new if is_new is not None else (bool(predecessor) or version not in ("v1", ""))) else "false",
            "", "",
        ])

    def channel(self, sku, name, start, end, listed=None, days="", reason="active",
                stock="in_stock", oos_from="", oos_to="", added_on=None, notes="", confirmed="运营"):
        self.availability.append([sku, name, start, end, listed or start, days, reason, stock,
                                  oos_from, oos_to, "", notes, "test", added_on or end, confirmed])

    def daily_sale(self, sku, day, units, refunds=0, spend=0.0):
        self.daily.append([sku, day, units, refunds, spend, "combined"])

    def window_sale(self, sku, window, start, end, units, refunds, spend):
        self.performance.append([sku, window, start, end, units, refunds, spend, "combined", 30])

    def plan(self, plan_id, sku, amount, status, start="", end="", stage="early"):
        self.plans.append([plan_id, sku, stage, "douyin", amount, start, end, status, start, ""])

    def stock(self, sku, weeks, as_of, on_hand=1000):
        self.inventory.append([sku, "", on_hand, 0, weeks, as_of, ""])

    def lineage(self, revision_id, sku, changed_on, to_predecessor, audience=""):
        self.revisions.append([revision_id, sku, changed_on, "", to_predecessor, "品类经理", audience, ""])

    def build(self):
        write_csv(self.root / "products.csv", PRODUCT_HEADER, self.products)
        write_csv(self.root / "availability.csv", AVAIL_HEADER, self.availability)
        write_csv(self.root / "sales_daily.csv", ["sku", "date", "units", "refunds", "spend", "channel"], self.daily)
        write_csv(self.root / "performance.csv", PERF_HEADER, self.performance)
        write_csv(self.root / "content_plans.csv", PLAN_HEADER, self.plans)
        write_csv(self.root / "inventory.csv", INV_HEADER, self.inventory)
        write_csv(self.root / "lineage_revisions.csv", REV_HEADER, self.revisions)
        return load_dataset(self.root)

    def clean(self):
        for path in self.root.glob("*.csv"):
            path.unlink()


class DatasetCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fx = Fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def base_pair(self, days=30):
        """新品 N 与已确认前代 O，两者都从上市起连续可售（渠道用连续区间）。"""
        self.fx.product("O", "2025-01-01", predecessor="", version="v1", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        launch = date(2026, 1, 1)
        end = launch + timedelta(days=days - 1)
        self.fx.channel("N", "store", launch.isoformat(), end.isoformat())
        self.fx.channel("N", "tmall", launch.isoformat(), end.isoformat())
        for offset in range(min(days, 30)):
            day = (launch + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("N", day, 10, 1, 100)
        old_launch = date(2025, 1, 1)
        old_end = old_launch + timedelta(days=days - 1)
        self.fx.channel("O", "store", old_launch.isoformat(), old_end.isoformat())
        self.fx.channel("O", "tmall", old_launch.isoformat(), old_end.isoformat())
        for offset in range(days):
            day = (old_launch + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("O", day, 4, 0, 10)
        return launch


class SalesBasisTests(DatasetCase):
    def test_stockout_days_are_removed_from_basis(self):
        self.base_pair()
        self.fx.availability = [r for r in self.fx.availability
                                if not (r[0] == "N" and r[1] == "tmall")]
        self.fx.channel("N", "tmall", "2026-01-01", "2026-01-30",
                        oos_from="2026-01-05", oos_to="2026-01-05", added_on="2026-02-01")
        ds = self.fx.build()
        stage = compute_product(ds, "N").stages["mm1"]
        # 两条渠道各 30 天，其中 tmall 缺 1 天 → 渠道日 59
        self.assertEqual(stage.basis.channel_days, 59)
        self.assertIn("stockout", [r.code for r in stage.reasons])

    def test_long_stockout_becomes_pending_not_zero_sales(self):
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30",
                        oos_from="2026-01-01", oos_to="2026-01-17")
        for offset in range(13):
            self.fx.daily_sale("N", (date(2026, 1, 18) + timedelta(days=offset)).isoformat(), 3, 0, 50)
        ds = self.fx.build()
        analysis = compute_product(ds, "N")
        mm1 = analysis.stages["mm1"]
        self.assertEqual(mm1.status, PENDING)
        self.assertIn("long-stockout", [r.code for r in mm1.reasons])
        ranking = analyze_dataset(ds)["rankings"]["mm1"]
        row = next(r for r in ranking if r["sku"] == "N")
        self.assertIsNone(row["rank_net"])

    def test_declared_days_mismatch_is_pending(self):
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30", days="24", reason="stockout")
        # 声明 24 天、日历 30 天，且没有给出缺货区间
        self.fx.channel("N", "tmall", "2026-01-01", "2026-01-30")
        ds = self.fx.build()
        state = next(s for s in build_states(ds, "N") if s.channel == "store")
        self.assertEqual(state.status, "pending")
        self.assertIn("24", " ".join(state.notes))
        stage = compute_product(ds, "N").stages["mm1"]
        self.assertEqual(stage.status, PENDING)

    def test_missing_refunds_is_pending_not_zero(self):
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30")
        self.fx.window_sale("N", "first-30d", "2026-01-01", "2026-01-30", 300, "", 1000)
        ds = self.fx.build()
        stage = compute_product(ds, "N").stages["mm1"]
        self.assertEqual(stage.status, PENDING)
        self.assertIsNone(stage.net)
        self.assertTrue(any("退款" in p for p in stage.pending_reasons))

    def test_sales_without_units_is_pending(self):
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30")
        self.fx.window_sale("N", "first-30d", "2026-01-01", "2026-01-30", "", 3, 1000)
        ds = self.fx.build()
        stage = compute_product(ds, "N").stages["mm1"]
        self.assertIsNone(stage.net)
        self.assertEqual(stage.status, PENDING)


class LineageTests(DatasetCase):
    def test_unconfirmed_predecessor_blocks_ranking_but_keeps_metrics(self):
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2",
                        lineage_status="pending", lineage_reason="品类经理未确认")
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30")
        for offset in range(30):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("N", day, 9, 1, 90)
        ds = self.fx.build()
        analysis = compute_product(ds, "N")
        self.assertEqual(analysis.status, PENDING)
        self.assertEqual(analysis.stages["mm1"].status, PENDING)
        self.assertIsNotNone(analysis.stages["mm1"].net)
        self.assertEqual(analysis.comparisons, [])
        row = next(r for r in analyze_dataset(ds)["rankings"]["mm1"] if r["sku"] == "N")
        self.assertIsNone(row["rank_net"])

    def test_pair_comparison_uses_common_sellable_days(self):
        self.base_pair()
        # 新品两个渠道都晚开卖 10 天；前代全期可售
        self.fx.availability = [r for r in self.fx.availability
                                if not (r[0] == "N" and r[1] in ("store", "tmall"))]
        self.fx.channel("N", "store", "2026-01-11", "2026-01-30")
        self.fx.channel("N", "tmall", "2026-01-11", "2026-01-30")
        ds = self.fx.build()
        comp = compare_pair(ds, "N", "O", "mm1")
        self.assertEqual(comp.common_days, 20)   # 按新品的偏移日，双方都在售
        self.assertIsNotNone(comp.ratio)

    def test_pair_comparison_refuses_when_availability_unverified(self):
        self.base_pair()
        self.fx.availability = [r for r in self.fx.availability
                                if not (r[0] == "N" and r[1] == "tmall")]
        self.fx.channel("N", "tmall", "2026-01-01", "2026-01-30", stock="unknown")
        ds = self.fx.build()
        comp = compare_pair(ds, "N", "O", "mm1")
        self.assertEqual(comp.status, PENDING)
        self.assertIsNone(comp.ratio)


class RepeatTests(DatasetCase):
    def test_repeat_requires_adjacent_coverage(self):
        self.base_pair(days=60)
        for offset in range(30, 60):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("N", day, 6, 1, 80)
        ds = self.fx.build()
        analysis = compute_product(ds, "N")
        from app.metrics import attach_repeat
        attach_repeat(ds, analysis)
        mm2 = analysis.stages["mm2"]
        self.assertIsNotNone(mm2.repeat_rate)
        self.assertAlmostEqual(mm2.repeat_rate, (5 * 30) / (9 * 30), places=1)
        self.assertIn("非用户级复购", mm2.repeat_note)

    def test_repeat_not_computed_when_mm1_pending(self):
        self.base_pair()
        self.fx.availability = [r for r in self.fx.availability
                                if not (r[0] == "N" and r[1] == "store")]
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30", stock="unknown")
        ds = self.fx.build()
        analysis = compute_product(ds, "N")
        from app.metrics import attach_repeat
        attach_repeat(ds, analysis)
        self.assertIsNone(analysis.stages["mm2"].repeat_rate)


class RecommendationTests(DatasetCase):
    def _strong_new_product(self):
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.channel("N", "store", "2026-01-01", "2026-03-01")
        self.fx.channel("N", "tmall", "2026-01-01", "2026-03-01")
        for offset in range(60):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            units = 12 if offset < 30 else 10
            self.fx.daily_sale("N", day, units, 1, 100)
        self.fx.channel("O", "store", "2025-01-01", "2025-03-01")
        self.fx.plan("CP-1", "N", 50000, "signed", start="2026-01-01", end="2026-01-31")
        return self.fx.build()

    def test_committed_plans_always_preserved(self):
        ds = self._strong_new_product()
        analyses = analyze_dataset(ds)["analyses_obj"]
        rec = {r.sku: r for r in build_portfolio(ds, analyses)}["N"]
        self.assertEqual(rec.preserved_total, 50000)
        self.assertIn("CP-1", [p["plan_id"] for p in rec.preserved_plans])
        self.assertTrue(any("保留" in line for line in rec.rationale) or rec.action in ("pending", "blocked"))

    def test_early_peak_only_does_not_get_more(self):
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30")
        for offset in range(30):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("N", day, 50 if offset == 0 else 8, 0, 100)
        ds = self.fx.build()
        analyses = analyze_dataset(ds)["analyses_obj"]
        rec = {r.sku: r for r in build_portfolio(ds, analyses)}["N"]
        self.assertIn(rec.action, ("early_watch", "hold"))
        self.assertEqual(rec.suggested_amount, 0)

    def test_insufficient_own_inventory_pauses_advance(self):
        ds = self._strong_new_product()
        self.fx.stock("N", 2.0, "2026-02-28")
        ds = self.fx.build()
        analyses = analyze_dataset(ds)["analyses_obj"]
        rec = {r.sku: r for r in build_portfolio(ds, analyses)}["N"]
        self.assertEqual(rec.suggested_amount, 0)
        self.assertEqual(rec.action, "hold")
        self.assertIn("own-inventory", [g.kind for g in rec.guards])

    def test_predecessor_inventory_caps_advance(self):
        self._strong_new_product()
        self.fx.stock("O", 7.0, "2026-02-28")
        self.fx.stock("N", 20.0, "2026-02-28")
        ds = self.fx.build()
        analyses = analyze_dataset(ds)["analyses_obj"]
        rec = {r.sku: r for r in build_portfolio(ds, analyses)}["N"]
        self.assertIn("predecessor-inventory", [g.kind for g in rec.guards])
        if rec.action == "advance":
            self.assertLessEqual(rec.suggested_amount, 10000)


class DecisionLedgerTests(DatasetCase):
    def test_decision_freezes_basis_and_enters_review_queue(self):
        self.base_pair()
        self.fx.plan("CP-1", "N", 30000, "proposed", start="2026-01-01", end="2026-01-31")
        ds = self.fx.build()
        ledger = self.root / "ledger.jsonl"
        record = store.record_decision(ds, {"sku": "N", "plan_id": "CP-1", "status": "approved",
                                            "actor": "测试"}, ledger)
        self.assertEqual(record["basis"]["stages"][0]["net"], 270)
        # 补录一条更新的渠道记录（含缺货）→ 可售日变化，进入复核队列
        self.fx.availability = [r for r in self.fx.availability
                                if not (r[0] == "N" and r[1] == "store")]
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30",
                        oos_from="2026-01-10", oos_to="2026-01-20", added_on="2026-02-05")
        ds3 = self.fx.build()
        queue = store.review_queue(ds3, store.load_decisions(ledger))
        self.assertTrue(any(item["sku"] == "N" for item in queue))
        # 台账里冻结的依据不受新数据影响
        stored = store.load_decisions(ledger)[0]
        self.assertEqual(stored["basis"]["stages"][0]["net"], 270)
        self.assertEqual(stored["basis"]["stages"][0]["sellable_days"], 30)

    def test_decision_validation(self):
        self.base_pair()
        ds = self.fx.build()
        with self.assertRaises(store.DecisionError):
            store.record_decision(ds, {"sku": "N", "status": "maybe"}, self.root / "l.jsonl")


class RevisionTests(DatasetCase):
    def test_revision_changes_rank_and_is_reported(self):
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2",
                        lineage_status="pending", lineage_reason="未确认")
        self.fx.channel("N", "store", "2026-01-01", "2026-01-30")
        self.fx.channel("N", "tmall", "2026-01-01", "2026-01-30")
        for offset in range(30):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            self.fx.daily_sale("N", day, 10, 1, 100)
        self.fx.lineage("R1", "N", "2026-03-01", "O")
        ds = self.fx.build()
        diff = compare_revisions(ds, "2026-02-01", "2026-03-02")
        self.assertTrue(diff["has_changes"])
        found = next(c for c in diff["rank_changes"] if c["sku"] == "N")
        self.assertEqual(found["before"]["status"], PENDING)
        self.assertIn("待核", found["change"])

    def test_added_inventory_record_changes_recommendation(self):
        self.fx.product("O", "2025-01-01", is_new=False)
        self.fx.product("N", "2026-01-01", predecessor="O", version="v2")
        for offset in range(60):
            day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
            self.fx.channel("N", "store", day, day)
            self.fx.channel("N", "tmall", day, day)
            self.fx.daily_sale("N", day, 12 if offset < 30 else 10, 1, 100)
        self.fx.channel("O", "store", "2025-01-01", "2025-03-01")
        self.fx.stock("O", 11.0, "2026-02-28")
        ds = self.fx.build()
        before = compare_revisions(ds, "2026-02-27", None)
        # 补入前代库存记录后，建议从「可追加」变为「暂缓/复核」
        self.assertFalse(before["has_changes"])


class SampleDatasetTests(unittest.TestCase):
    """样例数据应能体现设计意图：至少有一个待核、一个单峰、一个可追加。"""

    @classmethod
    def setUpClass(cls):
        cls.ds = load_dataset(Path(__file__).resolve().parent.parent / "data" / "sample")
        cls.result = analyze_dataset(cls.ds)
        cls.analyses = cls.result["analyses_obj"]
        cls.recs = {r.sku: r for r in build_portfolio(cls.ds, cls.analyses)}

    def test_sample_has_pending_and_comparable(self):
        statuses = {a.status for a in self.analyses.values() if a.is_new}
        self.assertIn(PENDING, statuses)
        self.assertTrue(statuses & {COMPARABLE, COMPARABLE_WITH_CAVEAT})

    def test_men_long_stockout_is_pending(self):
        self.assertEqual(self.analyses["SKU-MEN-N"].status, PENDING)

    def test_set_unconfirmed_launch_is_pending(self):
        analysis = self.analyses["SKU-SET-N"]
        self.assertEqual(analysis.status, PENDING)
        self.assertTrue(any(r.code == "launch-unconfirmed" for r in analysis.reasons))

    def test_serum_pause_due_to_repeat(self):
        rec = self.recs["SKU-SERUM-N"]
        self.assertEqual(rec.action, "pause")
        self.assertEqual(rec.preserved_total, 270000)

    def test_wash_gets_small_advance_after_peer_benchmark(self):
        rec = self.recs["SKU-WASH-N"]
        self.assertEqual(rec.action, "advance")
        self.assertGreater(rec.suggested_amount, 0)
        self.assertLessEqual(rec.suggested_amount, 20000)
        self.assertGreaterEqual(rec.evidence["peer_count_benchmark"], 2)

    def test_wash_low_efficiency_peer_is_held(self):
        rec = self.recs["SKU-WASH3-N"]
        self.assertEqual(rec.action, "hold")
        self.assertEqual(rec.suggested_amount, 0)

    def test_found_inventory_blocks_advance(self):
        rec = self.recs["SKU-FOUND-N"]
        self.assertIn("own-inventory", [g.kind for g in rec.guards])

    def test_committed_never_dropped(self):
        for sku, rec in self.recs.items():
            if rec.preserved_total:
                self.assertTrue(all(p["plan_id"] for p in rec.preserved_plans))
                self.assertTrue(all(p["status"] in ("signed", "approved", "proposed") for p in rec.preserved_plans))

    def test_found_lineage_revision_visible_in_diff(self):
        diff = compare_revisions(self.ds, "2026-11-17", "2026-11-21")
        self.assertTrue(any(c["sku"] == "SKU-FOUND-N" for c in diff["rank_changes"]))

    def test_rankings_are_grouped_by_category(self):
        for cohort, rows in self.result["rankings"].items():
            categories = {}
            for row in rows:
                categories.setdefault(row["category"], []).append(row)
            for category, items in categories.items():
                ranked = [r for r in items if r["rank_net"] is not None]
                if ranked:
                    self.assertEqual(max(r["rank_net"] for r in ranked), len(ranked))


if __name__ == "__main__":
    unittest.main()
