"""可解释的指标计算：净购买、复购代理、资源贡献与可比性状态。

口径原则
--------
* 分母是「共同可售日」，不是自然日。没有销售数据或可售日为 0 时结果为 ``None``。
* 缺失、未核实、无法排除的数据不会被当成 0；它们让该阶段进入待核。
* 复购是阶段净销售延续的代理指标，不是用户级复购；两阶段都必须可售充分。
* 资源贡献优先用毛利额（净购买 × 单位毛利）；缺单位毛利时用净购买/资源，
  并明确标注「不含毛利」，不能与毛利口径混排。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .domain import (
    AWAITING,
    COHORTS,
    COHORT_LABELS,
    COMPARABLE,
    COMPARABLE_WITH_CAVEAT,
    COMPARABILITY_LABELS,
    COMPARABILITY_RANK,
    LINEAGE_CONFIRMED,
    LINEAGE_NONE,
    LINEAGE_PENDING,
    NOT_COMPARABLE,
    OUT_OF_SCOPE,
    PENDING,
    Dataset,
    Product,
    WindowBasis,
    build_states,
    cohort_windows,
    days_between,
    match_window,
    period_label,
    window_basis,
)

DAY = timedelta(days=1)

MIN_COVERAGE_COMPARABLE = 0.80   # 低于 80% 可售覆盖需注意
MIN_COVERAGE_USABLE = 0.50       # 低于 50% 不可用于阶段排名
LONG_STOCKOUT_SHARE = 0.30       # 单窗口缺货超过 30% 视为长期缺货
REPEAT_MIN_DAYS = 20
REPEAT_MAX_REFUND_RATE = 0.35


def _r(value, digits=2):
    return None if value is None else round(value, digits)


def _ratio(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


@dataclass
class Reason:
    code: str
    severity: str
    text: str
    source: str = ""

    def to_dict(self):
        return {"code": self.code, "severity": self.severity, "text": self.text, "source": self.source}


@dataclass
class StageMetrics:
    sku: str
    cohort: str
    window_start: date
    window_end: date
    basis: WindowBasis
    source: str                      # daily / aggregate / none
    units: int | None = None
    refunds: int | None = None
    net: int | None = None
    spend: float | None = None
    net_per_day: float | None = None
    sellable_days: int = 0
    refund_rate: float | None = None
    gross_contribution: float | None = None
    contribution_per_spend: float | None = None
    net_per_spend: float | None = None
    repeat_rate: float | None = None
    repeat_note: str = ""
    early_peak_ratio: float | None = None
    status: str = PENDING
    reasons: list[Reason] = field(default_factory=list)
    pending_reasons: list[str] = field(default_factory=list)
    rank_net: int | None = None
    rank_efficiency: int | None = None
    peer_count: int = 0
    sales_on_closed_days: int = 0
    basis_note: str = ""

    @property
    def comparable(self) -> bool:
        return self.status in (COMPARABLE, COMPARABLE_WITH_CAVEAT)

    def to_dict(self):
        return {
            "sku": self.sku,
            "cohort": self.cohort,
            "cohort_label": COHORT_LABELS[self.cohort],
            "window": period_label(self.window_start, self.window_end),
            "source": self.source,
            "units": self.units,
            "refunds": self.refunds,
            "net": self.net,
            "net_per_day": self.net_per_day,
            "spend": self.spend,
            "sellable_days": self.sellable_days,
            "channel_days": self.basis.channel_days,
            "window_days": self.basis.window_days,
            "coverage": self.basis.coverage,
            "refund_rate": self.refund_rate,
            "gross_contribution": self.gross_contribution,
            "contribution_per_spend": self.contribution_per_spend,
            "net_per_spend": self.net_per_spend,
            "repeat_rate": self.repeat_rate,
            "repeat_note": self.repeat_note,
            "early_peak_ratio": self.early_peak_ratio,
            "status": self.status,
            "status_label": COMPARABILITY_LABELS[self.status],
            "reasons": [r.to_dict() for r in self.reasons],
            "pending_reasons": self.pending_reasons,
            "rank_net": self.rank_net,
            "rank_efficiency": self.rank_efficiency,
            "peer_count": self.peer_count,
            "sales_on_closed_days": self.sales_on_closed_days,
            "basis_note": self.basis_note,
            "channel_breakdown": self.basis.channel_breakdown,
        }


@dataclass
class PairComparison:
    new_sku: str
    base_sku: str
    cohort: str
    common_days: int
    new_net: int | None
    base_net: int | None
    new_per_day: float | None
    base_per_day: float | None
    ratio: float | None
    status: str
    reasons: list[Reason]
    note: str = ""

    def to_dict(self):
        return {
            "new_sku": self.new_sku,
            "base_sku": self.base_sku,
            "cohort": self.cohort,
            "common_days": self.common_days,
            "new_net": self.new_net,
            "base_net": self.base_net,
            "new_per_day": self.new_per_day,
            "base_per_day": self.base_per_day,
            "ratio": self.ratio,
            "status": self.status,
            "status_label": COMPARABILITY_LABELS[self.status],
            "reasons": [r.to_dict() for r in self.reasons],
            "note": self.note,
        }


@dataclass
class ProductAnalysis:
    sku: str
    product: Product
    is_new: bool
    audience_label: str
    lineage_label: str
    lineage_note: str
    stages: dict[str, StageMetrics]
    reasons: list[Reason]
    status: str
    comparisons: list[PairComparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def stage_list(self):
        return [self.stages[c] for c in COHORTS if c in self.stages]

    def stage(self, cohort):
        return self.stages.get(cohort)

    def to_dict(self):
        return {
            "sku": self.sku,
            "name": self.product.name or self.sku,
            "unit_margin": self.product.unit_margin,
            "category": self.product.category,
            "version": self.product.version,
            "launch": self.product.launch.isoformat() if self.product.launch else None,
            "is_new": self.is_new,
            "predecessor": self.product.predecessor,
            "lineage_status": self.product.lineage_status,
            "lineage_label": self.lineage_label,
            "lineage_note": self.lineage_note,
            "audience_label": self.audience_label,
            "status": self.status,
            "status_label": COMPARABILITY_LABELS[self.status],
            "reasons": [r.to_dict() for r in self.reasons],
            "warnings": self.warnings,
            "stages": [self.stages[c].to_dict() for c in COHORTS if c in self.stages],
            "comparisons": [c.to_dict() for c in self.comparisons],
        }


# ---------------------------------------------------------------- 销售归集


def _daily_totals(dataset: Dataset, sku: str, offsets: set[int]) -> dict | None:
    product = dataset.products[sku]
    if product.launch is None:
        return None
    records = dataset.daily_for(sku)
    if not records:
        return None
    units = refunds = 0
    spend = 0.0
    any_spend = False
    seen = set()
    for rec in records:
        if rec.day is None:
            continue
        offset = (rec.day - product.launch).days
        if offset not in offsets:
            continue
        units += rec.units
        refunds += rec.refunds
        if rec.spend is not None:
            spend += rec.spend
            any_spend = True
        seen.add(rec.day)
    return {"units": units, "refunds": refunds, "spend": spend if any_spend else None,
            "days_with_sales": len(seen)}


def _aggregate_record(dataset: Dataset, sku: str, cohort: str):
    product = dataset.products[sku]
    candidates = [r for r in dataset.performance if r.sku == sku]
    matches = [r for r in candidates if match_window(r, product.launch) == cohort]
    return matches


def _stage_source(dataset: Dataset, sku: str) -> str:
    if dataset.daily_for(sku):
        return "daily"
    if [r for r in dataset.performance if r.sku == sku]:
        return "aggregate"
    return "none"


def _availability_reasons(basis: WindowBasis, product: Product) -> tuple[list[Reason], list[str], str]:
    reasons: list[Reason] = []
    pending: list[str] = []
    status = COMPARABLE
    if not basis.states:
        return [Reason("no-availability", "high", "没有渠道上架记录，无法确认可售条件", "availability.csv")], \
            ["无渠道上架记录"], PENDING
    for state in basis.states:
        if state.status == "invalid":
            pending.append(f"{state.channel or '未标渠道'}：{'；'.join(state.notes)}")
            reasons.append(Reason("invalid-availability", "high",
                                  f"{state.channel or '未标渠道'} 可售记录无效：{'；'.join(state.notes)}",
                                  "availability.csv"))
        elif state.status == "pending":
            pending.append(f"{state.channel or '未标渠道'} 待核")
            reasons.append(Reason("unverified-availability", "high",
                                  f"{state.channel or '未标渠道'} 可售区间待核：{'；'.join(state.notes)}",
                                  "availability.csv"))
        if state.oos:
            spans = "、".join(period_label(s, e) for s, e in state.oos[:3])
            window_oos = sum(days_between(max(lo, basis.start), min(hi, basis.end))
                             for lo, hi in state.oos
                             if max(lo, basis.start) <= min(hi, basis.end))
            oos_share = window_oos / basis.window_days if basis.window_days else 0
            reasons.append(Reason("stockout", "medium",
                                  f"{state.channel or '未标渠道'} 缺货 {spans}，已从可售分母剔除",
                                  "availability.csv"))
            if oos_share >= LONG_STOCKOUT_SHARE:
                pending.append(f"{state.channel or '未标渠道'} 长期缺货")
                reasons.append(Reason("long-stockout", "high",
                                      f"该渠道窗口内缺货占 {oos_share:.0%}，长期缺货区间无法确认，阶段留作待核",
                                      "availability.csv"))
                status = PENDING
        if state.late_listing_days:
            reasons.append(Reason("late-listing", "medium",
                                  f"{state.channel or '未标渠道'} 实际首卖比上架边界晚 {state.late_listing_days} 天",
                                  "availability.csv"))
            status = combine_status(status, COMPARABLE_WITH_CAVEAT)
    if len(basis.channel_breakdown) <= 1 and len(basis.states) == 1:
        reasons.append(Reason("single-channel", "low", "仅单一渠道上架，渠道覆盖与多渠品不同", "availability.csv"))
        status = combine_status(status, COMPARABLE_WITH_CAVEAT)
    if basis.window_days and basis.coverage < MIN_COVERAGE_USABLE:
        pending.append("可售覆盖不足 50%")
        reasons.append(Reason("low-coverage", "high",
                              f"窗口内仅 {basis.sellable_count}/{basis.window_days} 天可售，不足以按阶段排名",
                              "availability.csv"))
        status = PENDING
    elif basis.window_days and basis.coverage < MIN_COVERAGE_COMPARABLE:
        reasons.append(Reason("partial-coverage", "medium",
                              f"窗口内 {basis.sellable_count}/{basis.window_days} 天可售，净购买已按可售日归一",
                              "availability.csv"))
        status = combine_status(status, COMPARABLE_WITH_CAVEAT)
    if product.discontinue_on and basis.end >= product.discontinue_on:
        reasons.append(Reason("discontinued", "medium",
                              f"商品于 {product.discontinue_on.isoformat()} 停产，窗口后段销售不具延续性",
                              "products.csv"))
        status = combine_status(status, COMPARABLE_WITH_CAVEAT)
    return reasons, pending, status


def _event_reasons(dataset: Dataset, product: Product, start: date, end: date) -> tuple[list[Reason], bool]:
    reasons, pending = [], False
    for event in dataset.events:
        if event.sku != product.sku:
            continue
        if event.effective_on and not (start <= event.effective_on <= end):
            continue
        kind = {"packaging": "包装", "formula": "配方"}.get(event.event_type, event.event_type or "变更")
        if not event.confirmed:
            pending = True
            reasons.append(Reason("event-unconfirmed", "high",
                                  f"{kind}变更已登记但未核实（{event.from_version or '?'} → {event.to_version or '?'}），"
                                  f"窗口内销售不具备同质条件", "events.csv"))
        else:
            reasons.append(Reason("event-confirmed", "medium",
                                  f"窗口内发生{kind}变更，前后客流不可直接合并", "events.csv"))
        if event.gap_days:
            pending = True
            reasons.append(Reason("event-gap", "high",
                                  f"{kind}变更造成 {event.gap_days} 天断档，窗口销售不连续", "events.csv"))
        if event.event_type == "formula" and event.changed in ("unknown", ""):
            pending = True
            reasons.append(Reason("formula-changed-unknown", "high",
                                  "配方变更内容未确认，是否改变适用客群未知", "events.csv"))
    return reasons, pending


def _product_gate(dataset: Dataset, product: Product, audience: str) -> tuple[list[Reason], list[str], str]:
    reasons: list[Reason] = []
    pending: list[str] = []
    status = COMPARABLE
    if any(not r.confirmed_by for r in dataset.availability_for(product.sku)) and product.is_new:
        pending.append("渠道可售未经运营确认")
        reasons.append(Reason("availability-unconfirmed", "high",
                              "至少一条渠道可售记录没有渠道运营确认人，实际可售期间未经确认",
                              "availability.csv"))
    if product.launch is None:
        pending.append("缺少上市日期")
        reasons.append(Reason("missing-launch", "high", "缺少上市日期，无法划分上市阶段", "products.csv"))
    elif product.launch_status != "confirmed":
        pending.append("上市日期待确认")
        reasons.append(Reason("launch-unconfirmed", "high",
                              f"上市日期 {product.launch.isoformat()} 尚未确认，阶段边界不可靠", "products.csv"))
    if product.lineage_status == LINEAGE_PENDING and product.lineage:
        pending.append("谱系待核")
        reasons.append(Reason("lineage-pending", "high",
                              f"与前代 {product.lineage} 的承接关系待品类经理确认", "products.csv"))
    elif product.lineage_status == LINEAGE_PENDING and not product.lineage:
        pending.append("谱系未填")
        reasons.append(Reason("lineage-missing", "high",
                              "新品未填写前代或标注无前代，无法判断承接关系", "products.csv"))
    if audience:
        group = next((g for g in dataset.audience_groups if g.group_id == audience), None)
        if group is None:
            pending.append(f"客群 {audience} 未登记")
            reasons.append(Reason("audience-unknown", "high", f"客群 {audience} 未登记", "audience_groups.csv"))
        elif product.sku not in group.skus:
            reasons.append(Reason("audience-mismatch", "high",
                                  f"不在选定客群「{group.label}」内，已移出本次排名；"
                                  f"如需比较请换用同客群商品（这与数据待核不同）",
                                  "audience_groups.csv"))
            status = OUT_OF_SCOPE
    elif product.audience and not product.audience_confirmed_by:
        pending.append("适用客群未确认")
        reasons.append(Reason("audience-unconfirmed", "high",
                              f"商品标注适用客群 {product.audience}，但无品类经理确认", "products.csv"))
    if pending:
        status = PENDING
    return reasons, pending, status


def data_horizon(dataset: Dataset) -> date:
    """数据覆盖到的最后一天；用于区分「阶段未结束」与「阶段缺数据」。"""
    candidates = [r.day for r in dataset.daily_sales if r.day]
    candidates += [r.end for r in dataset.performance if r.end]
    candidates += [r.as_of for r in dataset.inventory if r.as_of]
    return max(candidates) if candidates else date.today()


def combine_status(*statuses: str) -> str:
    """待核优先，其次不可比，再次需注意；没有任何问题才是可直接比较。"""
    if PENDING in statuses:
        return PENDING
    if NOT_COMPARABLE in statuses:
        return NOT_COMPARABLE
    if COMPARABLE_WITH_CAVEAT in statuses:
        return COMPARABLE_WITH_CAVEAT
    return COMPARABLE


def _source_note(source: str) -> str:
    return {
        "daily": "按日销售，已按共同可售日归集",
        "aggregate": "仅窗口级汇总，无法逐日裁剪；净购买按窗口可售天数归一，需注意口径",
        "none": "无销售数据",
    }[source]


def compute_stage(dataset: Dataset, product: Product, cohort: str, audience: str) -> StageMetrics:
    product_gate, product_pending, product_status = _product_gate(dataset, product, audience)
    horizon = data_horizon(dataset)
    if product.launch is None:
        today = date.today()
        basis = WindowBasis(today, today, 1, set(), 0, 0.0, {},
                            ["缺少上市日期，无法建立阶段窗口"], build_states(dataset, product.sku))
        return StageMetrics(product.sku, cohort, today, today, basis, "none",
                            status=PENDING, reasons=product_gate, pending_reasons=product_pending,
                            repeat_note="", basis_note="缺少上市日期")

    start, end = cohort_windows(product.launch)[cohort]
    basis = window_basis(dataset, product.sku, start, end)
    availability_reasons, availability_pending, availability_status = _availability_reasons(basis, product)
    event_reasons, event_pending = _event_reasons(dataset, product, start, end)
    reasons = product_gate + availability_reasons + event_reasons
    pending = product_pending + availability_pending
    if event_pending:
        pending.append("版本变更待核")
    if product_status == OUT_OF_SCOPE:
        status = OUT_OF_SCOPE
    else:
        status = combine_status(product_status, availability_status,
                                PENDING if event_pending else COMPARABLE)

    source = _stage_source(dataset, product.sku)
    offsets = set(range((start - product.launch).days, (end - product.launch).days + 1))
    if start > horizon:
        return StageMetrics(
            sku=product.sku, cohort=cohort, window_start=start, window_end=end,
            basis=basis, source=source, status=AWAITING,
            reasons=reasons + [Reason("stage-future", "low",
                                      f"{COHORT_LABELS[cohort]}尚未开始（数据覆盖到 {horizon.isoformat()}）",
                                      "performance.csv")],
            pending_reasons=[], basis_note="阶段尚未开始", sellable_days=basis.sellable_count,
        )
    if source == "none" and end > horizon:
        return StageMetrics(
            sku=product.sku, cohort=cohort, window_start=start, window_end=end,
            basis=basis, source=source, status=AWAITING,
            reasons=reasons + [Reason("stage-open", "low",
                                      f"{COHORT_LABELS[cohort]}将在 {end.isoformat()} 结束，尚无完整数据",
                                      "performance.csv")],
            pending_reasons=[], basis_note="阶段尚未结束", sellable_days=basis.sellable_count,
        )
    metrics = StageMetrics(
        sku=product.sku, cohort=cohort, window_start=start, window_end=end,
        basis=basis, source=source, status=status,
        reasons=reasons, pending_reasons=list(pending),
        basis_note=_source_note(source), sellable_days=basis.sellable_count,
    )

    if not basis.sellable_count:
        if end > horizon:
            metrics.status = AWAITING
            metrics.basis_note = "阶段尚未结束，可售数据不完整"
        else:
            metrics.status = PENDING
            metrics.pending_reasons.append("窗口内没有可确认的可售日")
        return metrics

    if source == "daily":
        totals = _daily_totals(dataset, product.sku, offsets)
        if totals is None:
            metrics.status = PENDING
            metrics.pending_reasons.append("按日销售不可用")
            return metrics
        units, refunds, spend = totals["units"], totals["refunds"], totals["spend"]
        if spend is None:
            metrics.reasons.append(Reason("missing-spend", "medium",
                                          "按日销售未提供内容资源投入，无法计算资源贡献",
                                          "sales_daily.csv"))
            metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
        # 非可售日的销售：计入净购买（确实发生），但把口径冲突写明
        product_launch = product.launch
        on_closed = 0
        for rec in dataset.daily_for(product.sku):
            if rec.day is None:
                continue
            offset = (rec.day - product_launch).days
            if offset not in offsets:
                continue
            day = product_launch + timedelta(days=offset)
            if day not in basis.sellable_days:
                on_closed += rec.units
        metrics.sales_on_closed_days = on_closed
        if on_closed:
            metrics.reasons.append(Reason("sales-on-closed-days", "medium",
                                          f"{on_closed} 件销售发生在未确认可售日，可售日历与订单数据冲突",
                                          "sales_daily.csv"))
            metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
        metrics.units, metrics.refunds, metrics.spend = units, refunds, spend
    elif source == "aggregate":
        matches = _aggregate_record(dataset, product.sku, cohort)
        if not matches:
            if end > horizon:
                metrics.status = AWAITING
                metrics.basis_note = "阶段尚未结束"
                return metrics
            metrics.pending_reasons.append("无该阶段销售记录")
            metrics.reasons.append(Reason("missing-window", "medium",
                                          f"{COHORT_LABELS[cohort]}无销售记录", "performance.csv"))
            return metrics
        if len(matches) > 1 and len({m.channel for m in matches}) > 1:
            metrics.reasons.append(Reason("channel-split", "medium",
                                          "同一阶段存在多渠道汇总行，但缺少按日数据，无法按渠道核对",
                                          "performance.csv"))
            metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
        units = sum(m.units for m in matches if m.units is not None)
        refunds = sum(m.refunds for m in matches if m.refunds is not None)
        spend = sum(m.spend for m in matches if m.spend is not None)
        if any(m.units is None for m in matches):
            metrics.pending_reasons.append("部分渠道缺少订单量")
            metrics.reasons.append(Reason("missing-units", "high", "部分销售汇总缺少订单量", "performance.csv"))
            metrics.status = PENDING
            return metrics
        if any(m.refunds is None for m in matches):
            metrics.pending_reasons.append("退款缺失，不能当作 0")
            metrics.reasons.append(Reason("missing-refunds", "high",
                                          "退款数据缺失，不能把退款当作 0，净购买待核", "performance.csv"))
            metrics.status = PENDING
            return metrics
        mismatch = [m for m in matches if m.start and m.end and
                    (m.start, m.end) != (start, end)]
        if mismatch:
            metrics.pending_reasons.append("销售窗口与阶段不一致")
            metrics.reasons.append(Reason("window-mismatch", "high",
                                          f"销售记录 {period_label(mismatch[0].start, mismatch[0].end)} "
                                          f"与阶段 {period_label(start, end)} 不一致", "performance.csv"))
            metrics.status = PENDING
            return metrics
        metrics.units, metrics.refunds, metrics.spend = units, refunds, spend
        if basis.window_days and basis.sellable_count < basis.window_days:
            metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
    else:
        metrics.pending_reasons.append("无销售数据")
        metrics.reasons.append(Reason("missing-sales", "high", "没有该商品的销售记录", "performance.csv"))
        metrics.status = PENDING
        return metrics

    # 净购买
    if metrics.units is None:
        metrics.status = PENDING
        metrics.pending_reasons.append("缺少订单量")
        return metrics
    if metrics.refunds is None:
        metrics.status = PENDING
        metrics.pending_reasons.append("缺少退款数据")
        return metrics
    if metrics.refunds > metrics.units:
        metrics.reasons.append(Reason("refund-exceeds-units", "high",
                                      f"退款 {metrics.refunds} 超过订单 {metrics.units}，可能为跨期退款，净购买不可信",
                                      "performance.csv"))
        metrics.status = PENDING
        metrics.pending_reasons.append("退款超过订单量，净购买待核")
        return metrics
    net = metrics.units - metrics.refunds
    metrics.net = net
    denominator = basis.channel_days or basis.sellable_count
    metrics.net_per_day = _r(_ratio(net, denominator), 2)
    if basis.channel_days and basis.channel_days != basis.sellable_count:
        metrics.basis_note += (f"；净购买/可售日 以渠道可售日之和 {basis.channel_days} 为分母"
                               f"（自然日并集为 {basis.sellable_count} 天）")
    metrics.refund_rate = _r(_ratio(metrics.refunds, metrics.units), 4)
    metrics.net_per_spend = _r(_ratio(net, metrics.spend), 4)
    if product.unit_margin is not None and metrics.spend:
        metrics.gross_contribution = _r(net * product.unit_margin, 2)
        metrics.contribution_per_spend = _r(_ratio(metrics.gross_contribution, metrics.spend), 3)
    if metrics.spend is None:
        metrics.reasons.append(Reason("missing-spend", "medium",
                                      "缺少内容资源投入，无法计算资源贡献", "performance.csv"))
        metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
    if metrics.refund_rate is not None and metrics.refund_rate >= REPEAT_MAX_REFUND_RATE:
        metrics.reasons.append(Reason("high-refund", "medium",
                                      f"退款率 {metrics.refund_rate:.0%}，净购买与复购信号都可能被退货掩盖",
                                      "performance.csv"))
        metrics.status = combine_status(metrics.status, COMPARABLE_WITH_CAVEAT)
    return metrics


EARLY_WINDOW_ELAPSED_DAYS = 14


def stage_is_closed(stage: StageMetrics, reference: date | None = None) -> bool:
    """阶段是否已经结束、结论可冻结。未结束的早期阶段结论会随后续数据变化。"""
    reference = reference or date.today()
    return stage.window_end < reference


def compute_product(dataset: Dataset, sku: str, audience: str = "") -> ProductAnalysis:
    product = dataset.products[sku]
    product_gate, product_pending, _ = _product_gate(dataset, product, audience)
    stages = {cohort: compute_stage(dataset, product, cohort, audience) for cohort in COHORTS}
    reasons = list(product_gate)
    # 商品级不重复阶段级原因，只保留阶段共性
    seen = {(r.code, r.text) for r in reasons}
    status = PENDING if product_pending else COMPARABLE
    if any(r.code == "audience-mismatch" for r in product_gate):
        status = OUT_OF_SCOPE
    for stage in stages.values():
        for reason in stage.reasons:
            if (reason.code, reason.text) not in seen and reason.code in {
                    "event-unconfirmed", "event-gap", "formula-changed-unknown",
                    "stockout", "long-stockout", "late-listing", "lineage-pending", "lineage-missing",
                    "launch-unconfirmed", "missing-launch", "no-availability", "low-coverage",
                    "unverified-availability", "invalid-availability", "availability-unconfirmed",
                    "audience-mismatch",
                    "audience-unconfirmed", "audience-unknown", "window-mismatch",
                    "refund-exceeds-units", "missing-refunds", "missing-units", "missing-sales"}:
                reasons.append(reason)
                seen.add((reason.code, reason.text))
        if stage.status not in (AWAITING, OUT_OF_SCOPE):
            status = combine_status(status, stage.status)
    if product.lineage_status == LINEAGE_CONFIRMED:
        lineage_label = f"已确认 · 前代 {product.lineage}"
        lineage_note = "品类经理已确认承接关系"
    elif product.lineage_status == LINEAGE_NONE and product.lineage:
        if product.variant_of:
            lineage_label = f"衍生规格 · 主商品 {product.variant_of}"
            lineage_note = "同系列衍生规格，不按前代承接关系做配对比较"
        else:
            lineage_label = f"组合/关联 · {product.lineage}"
            lineage_note = "登记为组合或关联关系，不按前代承接比较"
    elif product.lineage_status == LINEAGE_NONE:
        if product.is_new:
            lineage_label = "未填前代"
            lineage_note = "新品未填写前代或标注无前代，品类经理需补登"
        else:
            lineage_label = "无前代"
            lineage_note = "独立商品，无前代承接"
    else:
        lineage_label = f"待核 · {product.lineage or '未填'}"
        lineage_note = product.lineage_reason or ("品类经理尚未确认承接关系" if product.lineage
                                                  else "未填写前代或标注无前代")
    audience_label = ""
    if audience:
        group = next((g for g in dataset.audience_groups if g.group_id == audience), None)
        audience_label = group.label if group else audience
    elif product.audience:
        audience_label = product.audience
    if product_pending:
        status = PENDING
    return ProductAnalysis(
        sku=sku, product=product, is_new=product.is_new,
        audience_label=audience_label, lineage_label=lineage_label, lineage_note=lineage_note,
        stages=stages, reasons=reasons, status=status,
        warnings=[w for w in dataset.warnings if sku in w],
    )


# ---------------------------------------------------------------- 复购


def attach_repeat(dataset: Dataset, analysis: ProductAnalysis) -> None:
    mm1, mm2 = analysis.stages.get("mm1"), analysis.stages.get("mm2")
    if mm1 is not None and mm1.net is not None:
        mm1.repeat_note = "上市月 1 为基准月，不计算复购代理；复购在月 2 给出"
    if not analysis.is_new:
        if mm2:
            mm2.repeat_note = "仅新品计算复购信号"
        return
    if mm1 is None or mm2 is None:
        return
    note = "复购代理 = 上市月 2 净购买 ÷ 上市月 1 净购买；非用户级复购"
    if mm1.net is None or mm2.net is None:
        mm2.repeat_note = "相邻阶段净购买待核，复购暂不计算"
        return
    if mm1.sellable_days < REPEAT_MIN_DAYS or mm2.sellable_days < REPEAT_MIN_DAYS:
        mm2.repeat_note = (f"相邻阶段共同可售不足 {REPEAT_MIN_DAYS} 天，复购暂不计算")
        mm2.reasons.append(Reason("repeat-coverage", "medium",
                                  "相邻阶段可售覆盖不足，复购代理不可靠", "availability.csv"))
        mm2.status = combine_status(mm2.status, COMPARABLE_WITH_CAVEAT)
        return
    if mm1.units is None or mm2.units is None:
        mm2.repeat_note = "缺少订单量，复购暂不计算"
        return
    rate = mm2.net / mm1.net if mm1.net else None
    if rate is None:
        mm2.repeat_note = "上市月 1 净购买为 0，复购比率无定义"
        return
    mm2.repeat_rate = _r(rate, 3)
    mm2.repeat_note = note
    if mm2.refund_rate is not None and mm2.refund_rate >= REPEAT_MAX_REFUND_RATE:
        mm2.repeat_note += "；退款率偏高，复购信号需谨慎"
    if mm1.status == PENDING or mm2.status == PENDING:
        mm2.repeat_note += "；存在待核数据，复购仅作参考"


# ---------------------------------------------------------------- 共同可售配对比较


def compare_pair(dataset: Dataset, new_sku: str, base_sku: str, cohort: str = "mm1") -> PairComparison:
    new, base = dataset.products[new_sku], dataset.products[base_sku]
    reasons: list[Reason] = []
    if not new.launch or not base.launch:
        return PairComparison(new_sku, base_sku, cohort, 0, None, None, None, None, None,
                              PENDING, [Reason("missing-launch", "high", "任一方缺少上市日期，无法按上市阶段对齐", "products.csv")])
    if new.lineage and new.lineage != base_sku:
        reasons.append(Reason("lineage-mismatch", "high",
                              f"{new_sku} 的已确认前代为 {new.lineage}，不是 {base_sku}", "products.csv"))
    start_off, end_off = cohort_windows(new.launch)[cohort]
    offsets = range((start_off - new.launch).days, (end_off - new.launch).days + 1)
    new_states = build_states(dataset, new_sku)
    base_states = build_states(dataset, base_sku)
    new_days, base_days = set(), set()
    for offset in offsets:
        new_day = new.launch + timedelta(days=offset)
        base_day = base.launch + timedelta(days=offset)
        if any(s.start and s.end and s.start <= new_day <= s.end and new_day in s.active for s in new_states):
            new_days.add(offset)
        if any(s.start and s.end and s.start <= base_day <= s.end and base_day in s.active for s in base_states):
            base_days.add(offset)
    common = sorted(new_days & base_days)
    if any(s.status != "ok" for s in new_states + base_states):
        reasons.append(Reason("unverified-availability", "high", "至少一方存在待核可售区间，共同可售日不可靠", "availability.csv"))
    source_new = _stage_source(dataset, new_sku)
    source_base = _stage_source(dataset, base_sku)
    if source_new != "daily" or source_base != "daily":
        reasons.append(Reason("aggregate-source", "high",
                              "缺少按日销售，无法把两品裁剪到同一批共同可售日", "sales_daily.csv"))
    if not common:
        reasons.append(Reason("no-common-days", "high", "按上市阶段对齐后没有共同可售日", "availability.csv"))
    if any(r.severity == "high" for r in reasons):
        status = PENDING if any(r.code == "unverified-availability" for r in reasons) else NOT_COMPARABLE
        return PairComparison(new_sku, base_sku, cohort, len(common), None, None, None, None, None,
                              status, reasons, "共同可售条件不成立，不做配对比较")
    new_totals = _daily_totals(dataset, new_sku, set(common))
    base_totals = _daily_totals(dataset, base_sku, set(common))
    if new_totals is None or base_totals is None:
        return PairComparison(new_sku, base_sku, cohort, len(common), None, None, None, None, None,
                              PENDING, reasons + [Reason("missing-daily", "high", "缺少按日销售", "sales_daily.csv")])
    new_net = new_totals["units"] - new_totals["refunds"]
    base_net = base_totals["units"] - base_totals["refunds"]
    new_per = _r(new_net / len(common), 2) if common else None
    base_per = _r(base_net / len(common), 2) if common else None
    ratio = _r(new_net / base_net, 3) if base_net else None
    status = COMPARABLE_WITH_CAVEAT if reasons else COMPARABLE
    offset_start = (start_off - new.launch).days + 1
    offset_end = (end_off - new.launch).days + 1
    note = (f"按各自上市后第 {offset_start}–{offset_end} 天对齐，"
            f"仅取双方都可售的 {len(common)} 天")
    return PairComparison(new_sku, base_sku, cohort, len(common), new_net, base_net,
                          new_per, base_per, ratio, status, reasons, note)


def attach_comparisons(dataset: Dataset, analysis: ProductAnalysis) -> None:
    if not analysis.product.lineage_confirmed:
        return
    predecessor = dataset.products.get(analysis.product.lineage)
    if predecessor is None:
        return
    analysis.comparisons = [compare_pair(dataset, analysis.sku, predecessor.sku, cohort)
                            for cohort in COHORTS]


# ---------------------------------------------------------------- 排序


def rank_analyses(dataset: Dataset, analyses: dict[str, ProductAnalysis], cohort: str,
                  audience: str = "") -> list[dict]:
    """同品类、同阶段、同客群内排序；待核/不可比不占名次。"""
    groups: dict[str, list[ProductAnalysis]] = {}
    for analysis in analyses.values():
        if not analysis.is_new:
            continue
        stage = analysis.stages.get(cohort)
        if stage is None or stage.status == OUT_OF_SCOPE or analysis.status == OUT_OF_SCOPE:
            continue
        group = next((g for g in dataset.audience_groups if g.group_id == audience), None) if audience else None
        if group is not None and analysis.sku not in group.skus:
            continue
        groups.setdefault(analysis.product.category or "未分类", []).append(analysis)
    rows = []
    for category, peers in groups.items():
        rankable = []
        for analysis in peers:
            stage = analysis.stages[cohort]
            stage.rank_net = stage.rank_net or None
            if stage.status in (COMPARABLE, COMPARABLE_WITH_CAVEAT) and stage.net_per_day is not None:
                rankable.append(analysis)
        rankable.sort(key=lambda a: (a.stages[cohort].net_per_day or 0), reverse=True)
        count = len(rankable)
        for index, analysis in enumerate(rankable, start=1):
            stage = analysis.stages[cohort]
            stage.rank_net = index
            stage.peer_count = count
        efficiency = [a for a in rankable if a.stages[cohort].contribution_per_spend is not None]
        efficiency.sort(key=lambda a: a.stages[cohort].contribution_per_spend, reverse=True)
        for index, analysis in enumerate(efficiency, start=1):
            analysis.stages[cohort].rank_efficiency = index
        for analysis in peers:
            stage = analysis.stages[cohort]
            if analysis not in rankable:
                stage.peer_count = count
            rows.append({
                "sku": analysis.sku, "name": analysis.product.name or analysis.sku,
                "category": category, "cohort": cohort,
                "status": stage.status, "status_label": COMPARABILITY_LABELS[stage.status],
                "net": stage.net, "net_per_day": stage.net_per_day,
                "repeat_rate": stage.repeat_rate,
                "next_repeat_rate": (analysis.stages["mm2"].repeat_rate
                                     if cohort == "mm1" and analysis.stages.get("mm2") else None),
                "contribution_per_spend": stage.contribution_per_spend,
                "net_per_spend": stage.net_per_spend,
                "rank_net": stage.rank_net, "rank_efficiency": stage.rank_efficiency,
                "peer_count": stage.peer_count,
                "reasons": [r.to_dict() for r in stage.reasons],
                "pending_reasons": stage.pending_reasons,
            })
    rows.sort(key=lambda r: (r["category"], r["cohort"],
                             COMPARABILITY_RANK.get(r["status"], 9),
                             r["rank_net"] if r["rank_net"] is not None else 999,
                             -(r["net_per_day"] or 0)))
    return rows


def analyze_dataset(dataset: Dataset, audience: str = "", revisions: list[str] | None = None) -> dict:
    analyses = {sku: compute_product(dataset, sku, audience) for sku in sorted(dataset.products)}
    for analysis in analyses.values():
        attach_repeat(dataset, analysis)
        attach_comparisons(dataset, analysis)
    snapshots = {}
    for cohort in COHORTS:
        snapshots[cohort] = rank_analyses(dataset, analyses, cohort, audience)
    revision_map = {r.revision_id or f"{r.sku}:{r.changed_on}": r for r in dataset.lineage_revisions}
    for analysis in analyses.values():
        if getattr(analysis.product, "lineage_revision", None):
            revision = analysis.product.lineage_revision
            analysis.lineage_note += f"；依据 {revision.changed_on.isoformat() if revision.changed_on else '未标日期'} 的谱系修订"
    return {
        "audience": audience,
        "analyses": {sku: a.to_dict() for sku, a in analyses.items()},
        "analyses_obj": analyses,
        "rankings": snapshots,
        "generated_on": date.today().isoformat(),
        "warnings": dataset.warnings,
        "revisions": [
            {
                "revision_id": r.revision_id or f"{r.sku}:{r.changed_on}",
                "sku": r.sku,
                "changed_on": r.changed_on.isoformat() if r.changed_on else None,
                "from_predecessor": r.from_predecessor,
                "to_predecessor": r.to_predecessor,
                "decided_by": r.decided_by,
                "audience": r.audience,
                "note": r.note,
                "applied": bool(r.to_predecessor)
                and dataset.products[r.sku].predecessor == r.to_predecessor
                and r in dataset.lineage_revisions,
            }
            for r in dataset.lineage_revisions
        ],
    }
