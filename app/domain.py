"""评审域模型：商品谱系、可售日历、销售与资源投入记录。

只依赖 Python 标准库。核心约定
------------------------------
* 没有数据的窗口不会被当成 0；会返回 ``None`` 并说明待核原因。
* 渠道记录里的 ``days`` 是「可售天数」。如果它与按日历推导的可售天数不一致，
  且无法用缺货/上架日期解释，则该渠道区间标记为待核，不按满月参与排名。
* ``from``/``to`` 是渠道上架边界；``listed`` 是实际首卖日；``oos_from``/``oos_to``
  是明确缺货区间。只给缺货天数而没有日期时无法判断哪几天可售，同样待核。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

DAY = timedelta(days=1)

# ---------------------------------------------------------------- 枚举与标签

LINEAGE_CONFIRMED = "confirmed"
LINEAGE_PENDING = "pending"
LINEAGE_NONE = "none"
LINEAGE_LABELS = {LINEAGE_CONFIRMED: "已确认", LINEAGE_PENDING: "待核", LINEAGE_NONE: "无前代"}

COMPARABLE = "comparable"
COMPARABLE_WITH_CAVEAT = "comparable_with_caveat"
NOT_COMPARABLE = "not_comparable"
AWAITING = "awaiting"          # 阶段尚未结束/尚未开始，不是待核
OUT_OF_SCOPE = "out_of_scope"  # 不在所选客群，整体移出本次评审
PENDING = "pending"
COMPARABILITY_LABELS = {
    COMPARABLE: "可直接比较",
    COMPARABLE_WITH_CAVEAT: "可比，需注意原因",
    NOT_COMPARABLE: "不可直接比较",
    AWAITING: "阶段未结束",
    OUT_OF_SCOPE: "不在所选客群",
    PENDING: "待核",
}
COMPARABILITY_RANK = {COMPARABLE: 0, COMPARABLE_WITH_CAVEAT: 1, AWAITING: 2,
                      OUT_OF_SCOPE: 3, PENDING: 4, NOT_COMPARABLE: 5}

COHORTS = ("mm1", "mm2", "mm3")
COHORT_LABELS = {"mm1": "上市月 1（0–29 天）", "mm2": "上市月 2（30–59 天）", "mm3": "上市月 3（60–89 天）"}
COHORT_SHORT = {"mm1": "月 1", "mm2": "月 2", "mm3": "月 3"}
STAGE_WINDOW_LABELS = {"first-30d": "上市前 30 天", "second-30d": "上市 31–60 天", "beyond-60d": "上市 61 天以后"}
WINDOW_TO_COHORT = {"first-30d": "mm1", "second-30d": "mm2", "beyond-60d": "mm3"}

CONTENT_EARLY = "early"
CONTENT_SUSTAIN = "sustain"
CONTENT_STAGE_LABELS = {"early": "上市拉新期", "sustain": "稳定复购期", "other": "其他阶段"}

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_LABELS = {DECISION_APPROVED: "已批准", DECISION_REJECTED: "已驳回"}

COMMITTED_PLAN_STATUS = {"signed", "approved", "committed"}
PIPELINE_PLAN_STATUS = {"proposed", "reviewing", "pending"}

REC_RESERVE = "reserve"
REC_HOLD = "hold"
REC_PAUSE = "pause"
REC_EARLY_WATCH = "early_watch"
REC_ADVANCE = "advance"
REC_BLOCKED = "blocked"
REC_PENDING = "pending"
REC_OUT_OF_SCOPE = "out_of_scope"
REC_LABELS = {
    REC_RESERVE: "保留承诺",
    REC_HOLD: "复核队列",
    REC_PAUSE: "暂缓追加",
    REC_EARLY_WATCH: "仅早期信号",
    REC_ADVANCE: "可小幅追加",
    REC_BLOCKED: "不可比较，不排名",
    REC_PENDING: "待核，不排名",
    REC_OUT_OF_SCOPE: "不在所选客群",
}
REC_SEVERITY = {REC_RESERVE: "neutral", REC_HOLD: "warning", REC_PAUSE: "serious",
                REC_EARLY_WATCH: "warning", REC_ADVANCE: "good", REC_BLOCKED: "critical",
                REC_PENDING: "warning", REC_OUT_OF_SCOPE: "neutral"}

SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


class InputError(ValueError):
    """输入文件或字段不可用。"""


# ---------------------------------------------------------------- 解析工具


def parse_date(text: str | None, field_name: str = "date") -> date | None:
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise InputError(f"无法解析日期 {field_name}={text!r}，应为 YYYY-MM-DD")


def parse_int(text: str | None, field_name: str = "value") -> int | None:
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if text == "":
        return None
    try:
        return int(float(text)) if float(text).is_integer() else _bad_int(text, field_name)
    except ValueError as exc:
        raise InputError(f"无法解析数值 {field_name}={text!r}") from exc


def _bad_int(text, field_name):
    raise InputError(f"{field_name} 需要整数，收到 {text!r}")


def parse_float(text: str | None, field_name: str = "value") -> float | None:
    if text is None or str(text).strip() == "":
        return None
    try:
        return float(str(text).strip().replace(",", ""))
    except ValueError as exc:
        raise InputError(f"无法解析数值 {field_name}={text!r}") from exc


def parse_bool(text: str | None) -> bool:
    return (text or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def days_between(start: date, end: date) -> int:
    return (end - start).days + 1


def date_range(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += DAY


def merge_spans(spans: list[tuple[date, date]]) -> list[tuple[date, date]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + DAY:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def span_days(spans: list[tuple[date, date]]) -> int:
    return sum(days_between(s, e) for s, e in merge_spans(spans))


def period_label(start: date | None, end: date | None) -> str:
    if not start or not end:
        return "未提供区间"
    return f"{start.isoformat()} → {end.isoformat()}"


# ---------------------------------------------------------------- 数据类


@dataclass
class Product:
    sku: str
    name: str = ""
    category: str = ""
    predecessor: str = ""
    variant_of: str = ""
    version: str = ""
    launch: date | None = None
    launch_status: str = "confirmed"
    launch_confirmed_by: str = ""
    lineage_status: str = LINEAGE_PENDING
    lineage_reason: str = ""
    audience: str = ""
    audience_confirmed_by: str = ""
    unit_margin: float | None = None
    is_new_flag: bool | None = None
    discontinue_on: date | None = None
    note: str = ""
    lineage_revision: object | None = None
    # 原始申报值（谱系修订前），用于「按截止日期重放」
    declared_predecessor: str = ""
    declared_lineage_status: str = ""
    declared_lineage_reason: str = ""
    declared_audience: str = ""

    @property
    def lineage(self) -> str:
        return self.predecessor or self.variant_of

    @property
    def lineage_confirmed(self) -> bool:
        return self.lineage_status == LINEAGE_CONFIRMED and bool(self.lineage)

    @property
    def is_new(self) -> bool:
        if self.is_new_flag is not None:
            return self.is_new_flag
        if self.predecessor or self.variant_of:
            return True
        return bool(self.version) and self.version.lower() not in ("v1", "1", "")


@dataclass
class AvailabilityRecord:
    sku: str
    channel: str
    start: date | None
    end: date | None
    listed: date | None = None
    sold_days_declared: int | None = None
    reason: str = ""
    stock_status: str = "in_stock"
    oos_start: date | None = None
    oos_end: date | None = None
    oos_days: int | None = None
    notes: str = ""
    source: str = ""
    confirmed_by: str = ""
    added_on: date | None = None


@dataclass
class PerformanceRecord:
    """聚合窗口销售（无按日数据时的回退口径）。"""

    sku: str
    window: str
    start: date | None
    end: date | None
    units: int | None
    refunds: int | None
    spend: float | None = None
    channel: str = ""
    refund_window_days: int = 30


@dataclass
class DailySale:
    sku: str
    day: date
    units: int
    refunds: int = 0
    spend: float | None = None
    channel: str = ""


@dataclass
class InventoryRecord:
    sku: str
    channel: str = ""
    on_hand: int | None = None
    in_transit: int | None = None
    weeks_of_cover: float | None = None
    as_of: date | None = None
    note: str = ""


@dataclass
class EventRecord:
    sku: str
    event_type: str          # formula / packaging / other
    from_version: str = ""
    to_version: str = ""
    effective_on: date | None = None
    gap_days: int = 0
    changed: str = ""        # formulation / packaging / both / unknown
    confirmed: bool = True
    note: str = ""


@dataclass
class AudienceGroup:
    group_id: str
    label: str
    skus: tuple[str, ...]


@dataclass
class ContentPlan:
    plan_id: str
    sku: str
    stage: str = ""
    channel: str = ""
    amount: float = 0.0
    start: date | None = None
    end: date | None = None
    status: str = ""
    signed_on: date | None = None
    note: str = ""

    @property
    def committed(self) -> bool:
        return self.status in COMMITTED_PLAN_STATUS


@dataclass
class LineageRevision:
    sku: str
    changed_on: date | None
    from_predecessor: str
    to_predecessor: str
    decided_by: str = ""
    audience: str = ""
    note: str = ""
    revision_id: str = ""


@dataclass
class PlanDecision:
    decision_id: str
    plan_id: str
    sku: str
    status: str
    decided_on: date | None
    selected_audience: str = ""
    basis: dict = field(default_factory=dict)
    actor: str = ""
    note: str = ""
    plan_amount: float = 0.0


@dataclass
class Dataset:
    products: dict[str, Product]
    availability: list[AvailabilityRecord]
    performance: list[PerformanceRecord]
    daily_sales: list[DailySale]
    inventory: list[InventoryRecord]
    events: list[EventRecord]
    audience_groups: list[AudienceGroup]
    content_plans: list[ContentPlan]
    lineage_revisions: list[LineageRevision]
    decisions: list[PlanDecision]
    warnings: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def availability_for(self, sku: str) -> list[AvailabilityRecord]:
        """同一渠道有多条记录时，取 latest 录入（added_on 最大）的一条。

        这样「补录/修订渠道可售记录」不会与旧记录叠加，修订前后的差异也能被
        ``revisions.dataset_at`` 还原。
        """
        records = [r for r in self.availability if r.sku == sku]
        latest: dict[str, AvailabilityRecord] = {}
        for record in records:
            key = record.channel or "__no_channel__"
            current = latest.get(key)
            if current is None:
                latest[key] = record
                continue
            current_key = (current.added_on or date.min, current.start or date.min)
            new_key = (record.added_on or date.min, record.start or date.min)
            if new_key >= current_key:
                latest[key] = record
        return sorted(latest.values(), key=lambda r: (r.channel or "", r.start or date.min))

    def daily_for(self, sku: str) -> list[DailySale]:
        return [r for r in self.daily_sales if r.sku == sku]

    def plans_for(self, sku: str) -> list[ContentPlan]:
        return [p for p in self.content_plans if p.sku == sku]

    def approved_decision_for(self, plan_id: str) -> PlanDecision | None:
        for decision in self.decisions:
            if decision.plan_id == plan_id and decision.status == DECISION_APPROVED:
                return decision
        return None

    def plan_committed(self, plan: ContentPlan) -> bool:
        """内容排期是否已构成承诺：签约/批准状态，或已有批准的评审决定。"""
        return plan.committed or self.approved_decision_for(plan.plan_id) is not None


# ---------------------------------------------------------------- 读取

HEADER_HINTS = {
    "products": ["sku", "category"],
    "availability": ["sku", "channel"],
    "performance": ["sku", "window"],
    "sales_daily": ["sku", "date", "units"],
    "inventory": ["sku"],
    "events": ["sku", "event_type"],
    "audience_groups": ["group_id", "skus"],
    "content_plans": ["plan_id", "sku"],
    "lineage_revisions": ["sku", "to_predecessor"],
    "plan_decisions": ["decision_id", "sku"],
}


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames is None:
            raise InputError(f"{path.name} 缺少表头")
        hints = HEADER_HINTS.get(path.stem)
        if hints and not all(h in reader.fieldnames for h in hints):
            raise InputError(f"{path.name} 缺少必需列 {hints}，实际列 {reader.fieldnames}")
        return [row for row in reader if any((v or "").strip() for v in row.values())]


def _apply_revisions(products: dict[str, Product], revisions: list[LineageRevision],
                     warnings: list[str]) -> None:
    for revision in revisions:
        product = products.get(revision.sku)
        if product is None:
            warnings.append(f"lineage_revisions.csv 引用了未登记商品 {revision.sku}")
            continue
        product.predecessor = revision.to_predecessor
        product.lineage_status = LINEAGE_CONFIRMED if revision.to_predecessor else LINEAGE_NONE
        product.lineage_reason = revision.note or "谱系修订"
        if revision.audience:
            product.audience = revision.audience
        product.lineage_revision = revision


def load_dataset(directory: str | Path) -> Dataset:
    directory = Path(directory)
    warnings: list[str] = []
    product_rows = read_csv_rows(directory / "products.csv")
    if not product_rows:
        raise InputError("products.csv 为空或不存在")
    products: dict[str, Product] = {}
    for row in product_rows:
        sku = (row.get("sku") or "").strip()
        if not sku:
            raise InputError("products.csv 存在空 sku")
        if sku in products:
            raise InputError(f"products.csv sku 重复：{sku}")
        lineage_status = _norm(row.get("lineage_status"))
        prev = (row.get("predecessor") or "").strip()
        if lineage_status and lineage_status not in LINEAGE_LABELS:
            raise InputError(f"{sku}: 未知 lineage_status={lineage_status!r}")
        if not lineage_status:
            lineage_status = LINEAGE_CONFIRMED if prev else LINEAGE_NONE
        launch_status = _norm(row.get("launch_status")) or "confirmed"
        if launch_status not in {"confirmed", "provisional"}:
            raise InputError(f"{sku}: launch_status 应为 confirmed/provisional")
        products[sku] = Product(
            sku=sku,
            name=(row.get("name") or "").strip(),
            category=(row.get("category") or "").strip(),
            predecessor=prev,
            variant_of=(row.get("variant_of") or "").strip(),
            version=(row.get("version") or "").strip(),
            launch=parse_date(row.get("launch"), f"{sku}.launch"),
            launch_status=launch_status,
            launch_confirmed_by=(row.get("launch_confirmed_by") or "").strip(),
            lineage_status=lineage_status,
            lineage_reason=(row.get("lineage_reason") or "").strip(),
            audience=(row.get("audience") or "").strip(),
            audience_confirmed_by=(row.get("audience_confirmed_by") or "").strip(),
            unit_margin=parse_float(row.get("unit_margin"), f"{sku}.unit_margin"),
            is_new_flag=(parse_bool(row["is_new"]) if "is_new" in row and (row.get("is_new") or "").strip() else None),
            discontinue_on=parse_date(row.get("discontinue_on"), f"{sku}.discontinue_on"),
            note=(row.get("note") or "").strip(),
        )

    availability = [
        AvailabilityRecord(
            sku=(row.get("sku") or "").strip(),
            channel=(row.get("channel") or "").strip(),
            start=parse_date(row.get("from"), "availability.from"),
            end=parse_date(row.get("to"), "availability.to"),
            listed=parse_date(row.get("listed"), "availability.listed"),
            sold_days_declared=parse_int(row.get("days"), "availability.days"),
            reason=(row.get("reason") or "").strip(),
            stock_status=_norm(row.get("stock_status")) or "in_stock",
            oos_start=parse_date(row.get("oos_from"), "availability.oos_from"),
            oos_end=parse_date(row.get("oos_to"), "availability.oos_to"),
            oos_days=parse_int(row.get("oos_days"), "availability.oos_days"),
            notes=(row.get("notes") or "").strip(),
            source=(row.get("source") or "").strip(),
            confirmed_by=(row.get("confirmed_by") or "").strip(),
            added_on=parse_date(row.get("added_on"), "availability.added_on"),
        )
        for row in read_csv_rows(directory / "availability.csv")
    ]

    performance = [
        PerformanceRecord(
            sku=(row.get("sku") or "").strip(),
            window=(row.get("window") or "").strip(),
            start=parse_date(row.get("from"), "performance.from"),
            end=parse_date(row.get("to"), "performance.to"),
            units=parse_int(row.get("units"), "performance.units"),
            refunds=parse_int(row.get("refunds"), "performance.refunds"),
            spend=parse_float(row.get("spend"), "performance.spend"),
            channel=(row.get("channel") or "").strip(),
            refund_window_days=parse_int(row.get("refund_window_days"), "performance.refund_window_days") or 30,
        )
        for row in read_csv_rows(directory / "performance.csv")
    ]

    daily_sales = [
        DailySale(
            sku=(row.get("sku") or "").strip(),
            day=parse_date(row.get("date"), "sales_daily.date"),
            units=parse_int(row.get("units"), "sales_daily.units") or 0,
            refunds=parse_int(row.get("refunds"), "sales_daily.refunds") or 0,
            spend=parse_float(row.get("spend"), "sales_daily.spend"),
            channel=(row.get("channel") or "").strip(),
        )
        for row in read_csv_rows(directory / "sales_daily.csv")
    ]

    inventory = [
        InventoryRecord(
            sku=(row.get("sku") or "").strip(),
            channel=(row.get("channel") or "").strip(),
            on_hand=parse_int(row.get("on_hand"), "inventory.on_hand"),
            in_transit=parse_int(row.get("in_transit"), "inventory.in_transit"),
            weeks_of_cover=parse_float(row.get("weeks_of_cover"), "inventory.weeks_of_cover"),
            as_of=parse_date(row.get("as_of"), "inventory.as_of"),
            note=(row.get("note") or "").strip(),
        )
        for row in read_csv_rows(directory / "inventory.csv")
    ]

    events = [
        EventRecord(
            sku=(row.get("sku") or "").strip(),
            event_type=_norm(row.get("event_type")),
            from_version=(row.get("from_version") or "").strip(),
            to_version=(row.get("to_version") or "").strip(),
            effective_on=parse_date(row.get("effective_on"), "events.effective_on"),
            gap_days=parse_int(row.get("gap_days"), "events.gap_days") or 0,
            changed=_norm(row.get("changed")),
            confirmed=parse_bool(row.get("confirmed")) if "confirmed" in row else True,
            note=(row.get("note") or "").strip(),
        )
        for row in read_csv_rows(directory / "events.csv")
    ]

    groups = []
    for row in read_csv_rows(directory / "audience_groups.csv"):
        groups.append(AudienceGroup(
            group_id=(row.get("group_id") or "").strip(),
            label=(row.get("label") or "").strip(),
            skus=tuple(s.strip() for s in (row.get("skus") or "").split(";") if s.strip()),
        ))

    plans = [
        ContentPlan(
            plan_id=(row.get("plan_id") or "").strip(),
            sku=(row.get("sku") or "").strip(),
            stage=(row.get("stage") or "").strip(),
            channel=(row.get("channel") or "").strip(),
            amount=parse_float(row.get("amount"), "content_plans.amount") or 0.0,
            start=parse_date(row.get("start"), "content_plans.start"),
            end=parse_date(row.get("end"), "content_plans.end"),
            status=_norm(row.get("status")),
            signed_on=parse_date(row.get("signed_on"), "content_plans.signed_on"),
            note=(row.get("note") or "").strip(),
        )
        for row in read_csv_rows(directory / "content_plans.csv")
    ]

    revisions = [
        LineageRevision(
            sku=(row.get("sku") or "").strip(),
            changed_on=parse_date(row.get("changed_on"), "lineage_revisions.changed_on"),
            from_predecessor=(row.get("from_predecessor") or "").strip(),
            to_predecessor=(row.get("to_predecessor") or "").strip(),
            decided_by=(row.get("decided_by") or "").strip(),
            audience=(row.get("audience") or "").strip(),
            note=(row.get("note") or "").strip(),
            revision_id=(row.get("revision_id") or "").strip(),
        )
        for row in read_csv_rows(directory / "lineage_revisions.csv")
    ]

    decisions = []
    for row in read_csv_rows(directory / "plan_decisions.csv"):
        raw = (row.get("basis_json") or "").strip()
        try:
            basis = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise InputError(f"plan_decisions.basis_json 不是合法 JSON") from exc
        decisions.append(PlanDecision(
            decision_id=(row.get("decision_id") or "").strip(),
            plan_id=(row.get("plan_id") or "").strip(),
            sku=(row.get("sku") or "").strip(),
            status=_norm(row.get("status")),
            decided_on=parse_date(row.get("decided_on"), "plan_decisions.decided_on"),
            selected_audience=(row.get("selected_audience") or "").strip(),
            basis=basis,
            actor=(row.get("actor") or "").strip(),
            note=(row.get("note") or "").strip(),
            plan_amount=parse_float(row.get("plan_amount"), "plan_decisions.plan_amount") or 0.0,
        ))

    # 关联：未知 sku 告警并剔除该行，不当作 0 销售
    known = set(products)
    def drop_unknown(rows, label):
        kept = []
        for row in rows:
            if row.sku not in known:
                warnings.append(f"{label}.csv 引用了未登记商品 {row.sku!r}，该行已忽略")
            else:
                kept.append(row)
        return kept

    availability = drop_unknown(availability, "availability")
    performance = drop_unknown(performance, "performance")
    daily_sales = drop_unknown(daily_sales, "sales_daily")
    inventory = drop_unknown(inventory, "inventory")
    events = drop_unknown(events, "events")
    plans = drop_unknown(plans, "content_plans")
    decisions = drop_unknown(decisions, "plan_decisions")
    for group in groups:
        unknown = [s for s in group.skus if s not in known]
        if unknown:
            warnings.append(f"audience_groups.csv 的 {group.group_id} 引用未知商品 {', '.join(unknown)}")
    for product in products.values():
        linked = product.lineage
        if linked and linked not in products:
            warnings.append(f"{product.sku} 的谱系指向未登记商品 {linked}，按待核处理")
            product.lineage_status = LINEAGE_PENDING
            product.lineage_reason = product.lineage_reason or f"指向未登记商品 {linked}"

    revisions = [r for r in revisions if r.sku in known]
    for product in products.values():
        product.declared_predecessor = product.predecessor
        product.declared_lineage_status = product.lineage_status
        product.declared_lineage_reason = product.lineage_reason
        product.declared_audience = product.audience
    _apply_revisions(products, revisions, warnings)

    return Dataset(
        products=products,
        availability=availability,
        performance=performance,
        daily_sales=daily_sales,
        inventory=inventory,
        events=events,
        audience_groups=groups,
        content_plans=plans,
        lineage_revisions=revisions,
        decisions=decisions,
        warnings=warnings,
        sources={name: str(directory / f"{name}.csv")
                 for name in HEADER_HINTS if (directory / f"{name}.csv").exists()},
    )


# ---------------------------------------------------------------- 可售日历


@dataclass
class ChannelState:
    channel: str
    start: date | None
    end: date | None
    reason: str
    active: set = field(default_factory=set)
    oos: list[tuple[date, date]] = field(default_factory=list)
    unverified: list[tuple[date, date]] = field(default_factory=list)
    late_listing_days: int = 0
    declared_days: int | None = None
    derived_days: int = 0
    status: str = "ok"           # ok / pending / invalid
    notes: list[str] = field(default_factory=list)
    source: str = ""
    confirmed_by: str = ""

    @property
    def pending(self) -> bool:
        return self.status != "ok"

    @property
    def window_days(self) -> int:
        if not (self.start and self.end) or self.end < self.start:
            return 0
        return days_between(self.start, self.end)

    @property
    def excluded(self) -> list[tuple[date, date]]:
        """窗口内所有非「在售」的日期，用于展示。"""
        if not (self.start and self.end):
            return []
        excluded = []
        for day in date_range(self.start, self.end):
            if day not in self.active:
                excluded.append((day, day))
        return merge_spans(excluded)

    def sellable_on(self, day: date) -> bool:
        return day in self.active


def build_channel_state(record: AvailabilityRecord) -> ChannelState:
    notes = []
    if not record.start or not record.end:
        return ChannelState(record.channel, record.start, record.end, record.reason,
                            status="invalid", notes=["缺少渠道上架起止日期"],
                            source=record.source, confirmed_by=record.confirmed_by)
    if record.end < record.start:
        return ChannelState(record.channel, record.start, record.end, record.reason,
                            status="invalid", notes=["渠道结束日期早于开始日期"],
                            source=record.source, confirmed_by=record.confirmed_by)
    state = ChannelState(record.channel, record.start, record.end, record.reason,
                         source=record.source, confirmed_by=record.confirmed_by)
    state.active = set(date_range(record.start, record.end))
    state.declared_days = record.sold_days_declared

    if record.listed and record.listed > record.start:
        state.late_listing_days = (record.listed - record.start).days
        for day in date_range(record.start, record.listed - DAY):
            state.active.discard(day)
        notes.append(f"实际首卖日 {record.listed.isoformat()}，比上架边界晚 {state.late_listing_days} 天；已从可售日剔除")

    reason = _norm(record.reason)
    if record.stock_status in {"unknown", "unverified", "待核"} or reason in {"unknown", "未说明", "n/a"}:
        state.unverified.append((record.start, record.end))
        state.status = "pending"
        notes.append("库存状态未核实，整个上架窗口不可视为在售")

    if record.oos_start or record.oos_end:
        oos_start = record.oos_start or record.start
        oos_end = record.oos_end or record.end
        if oos_end < oos_start:
            state.unverified.append((record.start, record.end))
            state.status = "pending"
            notes.append("缺货区间结束早于开始，按待核处理")
        else:
            for day in date_range(oos_start, oos_end):
                state.active.discard(day)
            state.oos.append((oos_start, oos_end))
    elif record.oos_days:
        state.unverified.append((record.start, record.end))
        state.status = "pending"
        notes.append(f"只提供缺货 {record.oos_days} 天，没有具体日期，无法确定哪几天可售")

    if state.active and record.sold_days_declared is not None:
        state.derived_days = len(state.active)
        if record.sold_days_declared != state.derived_days:
            state.status = "pending"
            notes.append(
                f"声明可售 {record.sold_days_declared} 天，按日历与已确认缺货/上架日推导为 "
                f"{state.derived_days} 天；差额无法核实，按待核处理"
            )
            if not state.unverified:
                state.unverified.append((record.start, record.end))
    else:
        state.derived_days = len(state.active)

    if not state.active and state.status == "ok":
        state.status = "pending"
        state.unverified.append((record.start, record.end))
        notes.append("窗口内没有可确认的在售日")
    state.notes.extend(notes)
    return state


def build_states(dataset: Dataset, sku: str) -> list[ChannelState]:
    return [build_channel_state(r) for r in dataset.availability_for(sku)]


def sellable_days(dataset: Dataset, sku: str, start: date, end: date) -> set:
    """窗口内至少一个渠道可售的日期。任一渠道待核不自动视为可售。"""
    states = build_states(dataset, sku)
    days = set()
    for day in date_range(start, end):
        for state in states:
            if state.start and state.end and state.start <= day <= state.end and day in state.active:
                days.add(day)
                break
    return days


def channel_days(dataset: Dataset, sku: str, start: date, end: date) -> dict:
    """窗口内各渠道可售天数与覆盖。"""
    result = {}
    for state in build_states(dataset, sku):
        if state.start is None or state.end is None:
            continue
        lo, hi = max(start, state.start), min(end, state.end)
        count = sum(1 for day in date_range(lo, hi) if day in state.active) if lo <= hi else 0
        result[state.channel or "未标渠道"] = {
            "days": count,
            "pending": state.pending,
            "notes": state.notes,
            "window": [state.start.isoformat(), state.end.isoformat()],
        }
    return result


@dataclass
class WindowBasis:
    start: date
    end: date
    window_days: int
    sellable_days: set          # 至少一个渠道可售的自然日（并集）
    sellable_count: int
    coverage: float
    channel_breakdown: dict
    pending_reasons: list[str]
    states: list[ChannelState]
    channel_days: int = 0       # 渠道可售日之和（每个渠道每天计一次）
    channel_day_map: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.sellable_count > 0 and not self.pending_reasons


def window_basis(dataset: Dataset, sku: str, start: date, end: date) -> WindowBasis:
    states = build_states(dataset, sku)
    pending = []
    for state in states:
        if state.pending:
            pending.append(f"{state.channel or '未标渠道'}：{'；'.join(state.notes)}")
    days = sellable_days(dataset, sku, start, end)
    breakdown = channel_days(dataset, sku, start, end)
    window = days_between(start, end)
    return WindowBasis(
        start=start, end=end, window_days=window,
        sellable_days=days, sellable_count=len(days),
        coverage=round(len(days) / window, 3) if window else 0.0,
        channel_breakdown=breakdown,
        pending_reasons=pending, states=states,
        channel_days=sum(item["days"] for item in breakdown.values()),
        channel_day_map={channel: item["days"] for channel, item in breakdown.items()},
    )


def cohort_windows(launch: date) -> dict[str, tuple[date, date]]:
    return {
        "mm1": (launch, launch + timedelta(days=29)),
        "mm2": (launch + timedelta(days=30), launch + timedelta(days=59)),
        "mm3": (launch + timedelta(days=60), launch + timedelta(days=89)),
    }


def match_window(record: PerformanceRecord, launch: date | None) -> str:
    window = (record.window or "").strip().lower()
    if window in WINDOW_TO_COHORT:
        cohort = WINDOW_TO_COHORT[window]
        if launch and record.start and record.end:
            if (record.start, record.end) != cohort_windows(launch)[cohort]:
                return "mismatch"
        return cohort
    if record.start and launch:
        offset = (record.start - launch).days
        if offset < 0:
            return "mismatch"
        return "mm1" if offset < 30 else "mm2" if offset < 60 else "mm3"
    return "unknown"
