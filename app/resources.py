"""内容资源建议：守住已签排期，不被单一早期峰值挤占。

建议不是自动分配预算，而是给商品负责人一份可复核的判断：

* 已批准 / 已签约（``signed``、``approved``、``committed``）的内容排期一律保留，
  任何建议都不会减少、挪用或挤掉它们。
* 追加只发生在：上市月 2 之后仍有净购买与复购延续、且每单位资源贡献不低于同品类
  可比中位数、并且没有被库存或竞品排期约束卡住。
* 只有上市月 1 有数据的商品，最多给「仅早期信号」，不追加。
* 前代库存偏高、内容排期已签满、同类新品密集上市，都会压缩可追加额度。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .domain import (
    AWAITING,
    OUT_OF_SCOPE,
    COHORT_LABELS,
    COMPARABLE,
    COMPARABLE_WITH_CAVEAT,
    CONTENT_STAGE_LABELS,
    Dataset,
    REC_ADVANCE,
    REC_BLOCKED,
    REC_EARLY_WATCH,
    REC_HOLD,
    REC_LABELS,
    REC_PAUSE,
    REC_OUT_OF_SCOPE,
    REC_PENDING,
    REC_RESERVE,
    REC_SEVERITY,
)
from .metrics import ProductAnalysis

DAY = timedelta(days=1)

REPEAT_FLOOR = 0.80            # 复购代理低于 0.8 视为未延续
EARLY_PEAK_FLOOR = 0.60        # 月 2 净购买不足月 1 的 60%，视为单峰回落
PEER_INVENTORY_WEEKS_GUARD = 6  # 前代库存覆盖超过 6 周，压缩新品追加
OWN_INVENTORY_WEEKS_GUARD = 4   # 新品自身库存不足 4 周，暂缓追加
COMPETITOR_CROWD = 3            # 同品类 90 天内有 3 个及以上新品，竞争拥挤
CATEGORY_BUDGET = 300000.0      # 单品类追加预算上限（合成值，可配置）
DEFAULT_BASE_INCREMENT = 20000.0


@dataclass
class Guard:
    kind: str
    label: str
    detail: str
    effect: str                 # preserve / cap / pause / watch / info
    amount_cap: float | None = None
    binding: bool = False       # 是否实际压低了最终建议额度


@dataclass
class Recommendation:
    sku: str
    name: str
    category: str
    action: str
    headline: str
    rationale: list[str]
    evidence: dict
    preserved_plans: list[dict]
    preserved_total: float
    suggested_amount: float
    guards: list[Guard]
    review_needed: bool = False
    review_reason: str = ""

    @property
    def label(self) -> str:
        return REC_LABELS[self.action]

    @property
    def severity(self) -> str:
        return REC_SEVERITY[self.action]

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "name": self.name,
            "category": self.category,
            "action": self.action,
            "label": self.label,
            "severity": self.severity,
            "headline": self.headline,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "preserved_plans": self.preserved_plans,
            "preserved_total": self.preserved_total,
            "suggested_amount": self.suggested_amount,
            "guards": [
                {"kind": g.kind, "label": g.label, "detail": g.detail,
                 "effect": g.effect, "amount_cap": g.amount_cap, "binding": g.binding}
                for g in self.guards
            ],
            "review_needed": self.review_needed,
            "review_reason": self.review_reason,
        }


def _inventory_for(dataset: Dataset, sku: str) -> list:
    """同一渠道只取最新的库存快照，避免旧快照与新补录叠加。"""
    latest: dict[str, object] = {}
    for record in dataset.inventory:
        if record.sku != sku:
            continue
        key = record.channel or "__all__"
        current = latest.get(key)
        if current is None or (record.as_of or date.min) >= (current.as_of or date.min):
            latest[key] = record
    return list(latest.values())


def _weeks_of_cover(records) -> float | None:
    values = [r.weeks_of_cover for r in records if r.weeks_of_cover is not None]
    if values:
        return max(values)
    return None


def _peer_median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _competitor_count(dataset: Dataset, sku: str, launch: date | None,
                      analyses: dict[str, ProductAnalysis]) -> int:
    product = dataset.products[sku]
    if launch is None:
        return 0
    count = 0
    for other in analyses.values():
        if other.sku == sku or not other.is_new:
            continue
        if other.product.category != product.category:
            continue
        if other.product.launch is None:
            continue
        if abs((other.product.launch - launch).days) <= 90:
            count += 1
    return count


def _plan_label(plan) -> str:
    stage = CONTENT_STAGE_LABELS.get(plan.stage, plan.stage or "未标阶段")
    return f"{plan.plan_id} · {plan.channel or '未标渠道'} · {stage} · {plan.amount:,.0f}"


def build_recommendation(dataset: Dataset, analysis: ProductAnalysis,
                         analyses: dict[str, ProductAnalysis]) -> Recommendation:
    product = analysis.product
    plans = dataset.plans_for(analysis.sku)
    committed = [p for p in plans if dataset.plan_committed(p)]
    preserved = []
    for p in committed:
        decision = dataset.approved_decision_for(p.plan_id)
        if p.committed:
            commitment = "已签约 / 已批准排期"
        else:
            commitment = f"经决定 {decision.decision_id} 批准"
        preserved.append({"plan_id": p.plan_id, "channel": p.channel, "stage": p.stage,
                          "amount": p.amount, "status": p.status, "note": p.note,
                          "commitment": commitment})
    preserved_total = sum(p.amount for p in committed)
    guards: list[Guard] = []
    rationale: list[str] = []

    mm2 = analysis.stages.get("mm2")
    mm1 = analysis.stages.get("mm1")
    evidence_stage = mm2 if mm2 and mm2.net is not None else mm1
    evidence = {
        "stage": evidence_stage.cohort if evidence_stage else None,
        "stage_label": COHORT_LABELS[evidence_stage.cohort] if evidence_stage else "",
        "net": evidence_stage.net if evidence_stage else None,
        "net_per_day": evidence_stage.net_per_day if evidence_stage else None,
        "repeat_rate": mm2.repeat_rate if mm2 else None,
        "contribution_per_spend": evidence_stage.contribution_per_spend if evidence_stage else None,
        "net_per_spend": evidence_stage.net_per_spend if evidence_stage else None,
        "rank_net": evidence_stage.rank_net if evidence_stage else None,
        "peer_count": evidence_stage.peer_count if evidence_stage else 0,
        "rank_efficiency": evidence_stage.rank_efficiency if evidence_stage else None,
        "committed_total": preserved_total,
        "proposed_total": sum(p.amount for p in plans if p.status in ("proposed", "reviewing", "pending")),
    }

    if analysis.status == OUT_OF_SCOPE:
        action = REC_OUT_OF_SCOPE
        headline = "不在所选客群，本次评审不排名、不追加"
        rationale.append("该商品不属于当前筛选的客群；这不是数据待核，换成对应客群即可参与评审。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期保持不动。")
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards)

    if analysis.status == "pending":
        action = REC_PENDING
        headline = "数据待核，暂不参与排名或追加"
        rationale.append("存在未核实的谱系、上市日期、可售区间或退款数据；先补数据再评审。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期保持不动。")
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards, True, "待核数据补齐后重算")

    if analysis.status == "not_comparable":
        action = REC_BLOCKED
        headline = "不可直接比较，暂不追加"
        rationale.append("共同可售条件不成立或存在长期缺货/停产，排名结论不可用。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期保持不动，只做履约复核。")
        top = [r.text for r in analysis.reasons if r.severity == "high"][:2]
        rationale.extend(top)
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards, True,
                              "共同可售或可比性条件不成立，需人工判断")

    # 单峰保护：只有月 1 有数据，或月 2 明显回落，不追加
    if mm2 is None or mm2.net is None or mm2.status == AWAITING:
        action = REC_EARLY_WATCH if mm1 and mm1.net is not None else REC_HOLD
        headline = "只有上市月 1 数据，仅作观察，不追加"
        rationale.append("单一早期峰值不足以追加资源；需等上市月 2 的延续与复购信号。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期继续履约，不因早期峰值调整。")
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards)

    repeat = mm2.repeat_rate
    if repeat is None:
        action = REC_HOLD
        headline = "复购信号不可用，暂缓追加"
        rationale.append(mm2.repeat_note or "相邻阶段可售条件不足，复购代理不可用。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期保持不动。")
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards, True, "复购信号待补")

    peak_ratio = (mm2.net / mm1.net) if (mm1 and mm1.net and mm2.net is not None) else None
    mm2.early_peak_ratio = round(peak_ratio, 3) if peak_ratio is not None else None
    evidence["early_peak_ratio"] = mm2.early_peak_ratio
    if peak_ratio is not None and peak_ratio < EARLY_PEAK_FLOOR:
        action = REC_EARLY_WATCH
        headline = f"单峰回落明显（月 2 仅为月 1 的 {peak_ratio:.0%}），不追加"
        rationale.append("上市月 1 的峰值未延续到月 2；单一早期峰值不构成追加依据。")
        if preserved_total:
            rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期保持不动。")
        return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                              action, headline, rationale, evidence, preserved,
                              preserved_total, 0.0, guards, True,
                              "峰值未延续，复核是否是一次性流量")

    peer_stage = [a for a in analyses.values()
                  if a.is_new and a.product.category == product.category and a.sku != analysis.sku]
    rankable_peer_status = (COMPARABLE, COMPARABLE_WITH_CAVEAT)
    peer_efficiency = [a.stages["mm2"].contribution_per_spend for a in peer_stage
                       if a.stages.get("mm2") and a.stages["mm2"].contribution_per_spend is not None
                       and a.stages["mm2"].status in rankable_peer_status]
    peer_net = [a.stages["mm2"].net_per_day for a in peer_stage
                if a.stages.get("mm2") and a.stages["mm2"].net_per_day is not None
                and a.stages["mm2"].status in rankable_peer_status]
    evidence["peer_count_benchmark"] = len(peer_efficiency)
    median_eff = _peer_median(peer_efficiency)
    median_net = _peer_median(peer_net)
    efficiency = mm2.contribution_per_spend
    evidence["peer_median_efficiency"] = median_eff
    evidence["peer_median_net_per_day"] = median_net

    if repeat < REPEAT_FLOOR:
        action = REC_PAUSE
        headline = f"复购延续偏弱（{repeat:.0%}），暂缓追加"
        rationale.append("上市月 2 净购买相对月 1 低于 80%，更像一次性尝新，不是持续需求。")
    elif efficiency is None:
        action = REC_HOLD
        headline = "缺少单位毛利或资源投入，无法判断资源贡献"
        rationale.append("补齐单位毛利与内容资源投入后再比较每单位资源的贡献。")
        guards.append(Guard("missing-efficiency", "缺少效率口径",
                            "没有单位毛利或内容投入，无法计算每单位资源贡献；不做绝对值比较", "watch", None))
    elif median_eff is None:
        action = REC_HOLD
        headline = "同品类暂无可比对照，无法确认资源效率领先"
        rationale.append("同品类没有其他可直接比较的新品；缺少对照时不应仅凭自身绝对值得出追加结论。")
        guards.append(Guard("no-peer", "无同品类对照",
                            "同品类暂无其他可比新品，每单位资源贡献没有相对基准；本阶段只做观察", "watch", None))
    elif len(peer_efficiency) < 2:
        action = REC_HOLD
        headline = "同品类可比对照不足 2 个，暂不追加"
        rationale.append("同品类只有 1 个可比新品，无法形成稳定基准；待更多同类新品进入可比阶段再评审。")
        guards.append(Guard("thin-peer", "对照过少",
                            f"同品类仅 {len(peer_efficiency)} 个可比对照；单个对照不足以支撑资源重配", "watch", None))
    elif efficiency < median_eff:
        action = REC_HOLD
        headline = "每单位资源贡献低于同品类中位数，暂不追加"
        rationale.append(f"本品每元资源贡献 {efficiency:.2f}，低于同品类可比中位数 {median_eff:.2f}。")
    elif median_net is not None and (mm2.net_per_day or 0) < median_net:
        action = REC_HOLD
        headline = "净购买延续低于同品类中位数，暂不追加"
        rationale.append("即便复购尚可，按月 2 净购买/可售日仍不占优，追加的边际回报不确定。")
    else:
        action = REC_ADVANCE
        headline = "复购与资源贡献均达标，可小幅追加"
        rationale.append(f"上市月 2 复购代理 {repeat:.0%}，每元资源贡献 {efficiency:.2f}，不低于同品类中位数。")

    # ---- 约束条件（全部不触碰已承诺资源）
    base = DEFAULT_BASE_INCREMENT
    cap = base
    if median_eff and efficiency >= median_eff:
        cap = base * min(1.5, 0.75 + efficiency / (median_eff * 2))
    guards.append(Guard("baseline", "增量基准", f"同品类常规追加基准 {base:,.0f} 元", "info", None))

    # 前代库存
    pred_sku = product.lineage if product.lineage_confirmed else ""
    pred_records = _inventory_for(dataset, pred_sku) if pred_sku else []
    pred_weeks = _weeks_of_cover(pred_records)
    pred_on_hand = sum(r.on_hand or 0 for r in pred_records)
    if pred_sku and pred_weeks is not None and pred_weeks >= PEER_INVENTORY_WEEKS_GUARD:
        reduction = 0.5 if pred_weeks < 10 else 0.0
        before = cap
        guards.append(Guard("predecessor-inventory", "前代库存",
                            f"{pred_sku} 仍有约 {pred_weeks:.1f} 周库存（在手 {pred_on_hand:,} 件），"
                            f"加推新品会与清库存抢同一批客群", "cap" if reduction else "pause",
                            cap * reduction, binding=cap * reduction < before))
        cap *= reduction
        if reduction:
            rationale.append(f"前代 {pred_sku} 库存仍高，追加额度按压减后的 {int(reduction * 100)}% 执行。")
        else:
            rationale.append(f"前代 {pred_sku} 库存超过 10 周，暂停本品追加以免与清货抢客群。")
    elif pred_sku and pred_on_hand:
        guards.append(Guard("predecessor-inventory", "前代库存",
                            f"前代 {pred_sku} 在手 {pred_on_hand:,} 件，未达库存警戒线", "info", None))

    # 新品自身可售库存
    own_records = _inventory_for(dataset, analysis.sku)
    own_weeks = _weeks_of_cover(own_records)
    if own_weeks is not None and own_weeks < OWN_INVENTORY_WEEKS_GUARD:
        guards.append(Guard("own-inventory", "新品库存",
                            f"新品自身可售库存仅约 {own_weeks:.1f} 周，追加内容可能带来缺货", "pause", 0,
                            binding=cap > 0))
        cap = 0
        action = REC_HOLD
        headline = "库存不足，追加内容会放大缺货风险"

    # 已签排期与排期拥挤
    signed_total = preserved_total
    category_committed = sum(
        p.amount for other in analyses.values() if other.product.category == product.category
        for p in dataset.plans_for(other.sku) if dataset.plan_committed(p)
    )
    if signed_total:
        guards.append(Guard("signed-schedule", "已签内容排期",
                            f"已承诺内容 {len(committed)} 项、{signed_total:,.0f} 元"
                            f"（含已批准决定）；这些排期不因新峰值被替换", "preserve", None))
    # 同渠道排期重叠：下一阶段已排满则不再叠加
    if mm2.window_start and mm2.window_end:
        overlap = [p for p in committed
                   if p.start and p.end and p.start <= mm2.window_end and p.end >= mm2.window_start]
        if overlap:
            before = cap
            guards.append(Guard("schedule-overlap", "排期重叠",
                                f"上市月 2 已有 {len(overlap)} 项已签内容，追加只能占用空档", "cap",
                                cap * 0.5, binding=cap * 0.5 < before))
            cap *= 0.5
            rationale.append("上市月 2 已有已签内容排期，新增资源只能占用剩余空档。")

    # 同类新品竞争
    competitors = _competitor_count(dataset, analysis.sku, product.launch, analyses)
    evidence["competitor_new_launches_90d"] = competitors
    if competitors >= COMPETITOR_CROWD:
        before = cap
        guards.append(Guard("competitor-crowding", "同类新品竞争",
                            f"同品类 90 天内有 {competitors} 个新品并行，流量更贵", "cap",
                            cap * 0.5, binding=cap * 0.5 < before))
        cap *= 0.5
        rationale.append(f"同品类 90 天内有 {competitors} 个新品竞争，追加额度减半。")
    elif competitors == 2:
        before = cap
        guards.append(Guard("competitor-crowding", "同类新品竞争",
                            "同品类 90 天内有 2 个新品并行", "cap", cap * 0.75,
                            binding=cap * 0.75 < before))
        cap *= 0.75

    if evidence["proposed_total"]:
        guards.append(Guard("proposed-schedule", "待评审排期",
                            f"另有 {len([p for p in plans if p.status in ('proposed', 'reviewing', 'pending')])} 项"
                            f"未签排期共 {evidence['proposed_total']:,.0f} 元待评审；建议额度是上限，"
                            f"不代表整笔待评审排期获批", "info", None))
    # 品类预算
    room = max(0.0, CATEGORY_BUDGET - category_committed)
    if cap > room:
        guards.append(Guard("category-budget", "品类预算",
                            f"品类已承诺 {category_committed:,.0f} 元，剩余 {room:,.0f} 元可划拨", "cap", room,
                            binding=True))
        cap = room
    else:
        guards.append(Guard("category-budget", "品类预算",
                            f"品类已承诺 {category_committed:,.0f} 元，剩余 {room:,.0f} 元，未触及上限", "info", None))
    suggested = round(max(0.0, cap), -2) if action == REC_ADVANCE else 0.0
    if action == REC_ADVANCE and suggested == 0:
        action = REC_HOLD
        headline = "约束后无可追加额度，保留现有承诺"
        rationale.append("库存、排期与品类预算约束后，没有安全的追加空间。")

    if preserved_total:
        rationale.append(f"已承诺的 {preserved_total:,.0f} 元内容排期全部保留；建议额度只来自未承诺预算。")
    else:
        guards.append(Guard("no-commitment", "无已签排期",
                            "该商品尚无已批准内容排期，建议额度需从零开始审批", "info", None))
    return Recommendation(analysis.sku, product.name or analysis.sku, product.category,
                          action, headline, rationale, evidence, preserved,
                          preserved_total, suggested, guards)


def build_portfolio(dataset: Dataset, analyses: dict[str, ProductAnalysis]) -> list[Recommendation]:
    return [build_recommendation(dataset, a, analyses) for a in analyses.values()
            if a.is_new or a.product.lineage]


def recommendation_summary(recommendations: list[Recommendation]) -> dict:
    total_preserved = sum(r.preserved_total for r in recommendations)
    total_suggested = sum(r.suggested_amount for r in recommendations)
    counts: dict[str, int] = {}
    for r in recommendations:
        counts[r.action] = counts.get(r.action, 0) + 1
    return {
        "total_preserved": total_preserved,
        "total_suggested": total_suggested,
        "counts": counts,
        "actions": [{"action": a, "label": REC_LABELS[a], "count": counts.get(a, 0)}
                    for a in (REC_ADVANCE, REC_HOLD, REC_PAUSE, REC_EARLY_WATCH,
                              REC_RESERVE, REC_BLOCKED, REC_PENDING) if counts.get(a)],
    }
