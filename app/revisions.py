"""修订前后对比：排名、可比性与资源建议如何随谱系/库存修订变化。

每次评审可以带一个「截止版本」（``as_of``）：只使用在该日期或之前已录入的
记录（``added_on`` / ``changed_on`` / ``decided_on``）与截止日之前已知的谱系修订。
这样团队能回答「补入库存记录后，排名和建议变了什么」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .domain import Dataset, LineageRevision, Product
from .metrics import analyze_dataset
from .resources import build_portfolio, recommendation_summary


def _rev_id(revision: LineageRevision) -> str:
    return revision.revision_id or f"{revision.sku}:{revision.changed_on.isoformat() if revision.changed_on else '未标日期'}"


def dataset_at(dataset: Dataset, as_of: str | None) -> Dataset:
    """返回只包含 ``as_of`` 之前已录入信息的浅拷贝数据集。"""
    if not as_of:
        return dataset
    cutoff = date.fromisoformat(as_of)

    def known(day: date | None) -> bool:
        return day is None or day <= cutoff

    # 渠道记录按录入日截止；同一渠道的旧记录会被新记录替换，与 Dataset.availability_for 一致。
    availability = []
    latest_by_channel: dict[tuple[str, str], object] = {}
    for record in sorted(dataset.availability, key=lambda r: (r.added_on or date.min, r.start or date.min)):
        if not known(record.added_on):
            continue
        latest_by_channel[(record.sku, record.channel)] = record
    availability = list(latest_by_channel.values())
    inventory = [r for r in dataset.inventory if known(r.as_of)]
    revisions = [r for r in dataset.lineage_revisions if known(r.changed_on)]
    # 逐步重放截止日前的谱系修订
    products = {
        sku: Product(**{k: v for k, v in product.__dict__.items() if k != "lineage_revision"})
        for sku, product in dataset.products.items()
    }
    for product in products.values():
        product.predecessor = product.declared_predecessor
        product.lineage_status = product.declared_lineage_status or (
            "confirmed" if product.declared_predecessor else "none")
        product.lineage_reason = product.declared_lineage_reason
        if product.declared_audience:
            product.audience = product.declared_audience
        product.lineage_revision = None
    for revision in sorted(revisions, key=lambda r: (r.changed_on or date.min, _rev_id(r))):
        product = products.get(revision.sku)
        if product is None:
            continue
        product.predecessor = revision.to_predecessor
        product.lineage_status = "confirmed" if revision.to_predecessor else "none"
        product.lineage_reason = revision.note or "谱系修订"
        if revision.audience:
            product.audience = revision.audience
        product.lineage_revision = revision
    daily = dataset.daily_sales
    performance = dataset.performance

    clone = Dataset(
        products=products,
        availability=availability,
        performance=performance,
        daily_sales=daily,
        inventory=inventory,
        events=dataset.events,
        audience_groups=dataset.audience_groups,
        content_plans=dataset.content_plans,
        lineage_revisions=revisions,
        decisions=[d for d in dataset.decisions if known(d.decided_on)],
        warnings=dataset.warnings,
        sources=dataset.sources,
    )
    return clone


def _snapshot(dataset: Dataset, audience: str = "") -> dict:
    result = analyze_dataset(dataset, audience)
    analyses = result["analyses_obj"]
    recommendations = build_portfolio(dataset, analyses)
    return {
        "rankings": result["rankings"],
        "analyses": result["analyses"],
        "recommendations": [r.to_dict() for r in recommendations],
        "summary": recommendation_summary(recommendations),
    }


def _row_key(row: dict) -> tuple[str, str]:
    return row["cohort"], row["sku"]


def diff_snapshots(before: dict, after: dict) -> dict:
    rank_changes = []
    before_map = {_row_key(r): r for rows in before["rankings"].values() for r in rows}
    after_map = {_row_key(r): r for rows in after["rankings"].values() for r in rows}
    for key in sorted(set(before_map) | set(after_map)):
        old = before_map.get(key)
        new = after_map.get(key)
        if old is None or new is None:
            rank_changes.append({
                "sku": key[1], "cohort": key[0],
                "kind": "added" if old is None else "removed",
                "before": old, "after": new,
                "change": "新增参与排名" if old is None else "不再参与排名",
            })
            continue
        if old["rank_net"] != new["rank_net"] or old["status"] != new["status"] or old["net_per_day"] != new["net_per_day"]:
            rank_changes.append({
                "sku": key[1], "cohort": key[0], "kind": "changed",
                "before": old, "after": new,
                "change": _rank_change_text(old, new),
            })
    rec_changes = []
    before_recs = {r["sku"]: r for r in before["recommendations"]}
    after_recs = {r["sku"]: r for r in after["recommendations"]}
    for sku in sorted(set(before_recs) | set(after_recs)):
        old = before_recs.get(sku)
        new = after_recs.get(sku)
        if old is None or new is None:
            rec_changes.append({"sku": sku, "kind": "added" if old is None else "removed",
                                "before": old, "after": new,
                                "change": "新增建议" if old is None else "移出建议"})
            continue
        if (old["action"] != new["action"] or old["suggested_amount"] != new["suggested_amount"]
                or old["preserved_total"] != new["preserved_total"]):
            rec_changes.append({
                "sku": sku, "kind": "changed", "before": old, "after": new,
                "change": f"{old['label']} → {new['label']}；建议额度 "
                          f"{old['suggested_amount']:,.0f} → {new['suggested_amount']:,.0f} 元；"
                          f"承诺保留 {new['preserved_total']:,.0f} 元",
            })
    return {
        "rank_changes": rank_changes,
        "recommendation_changes": rec_changes,
        "summary_before": before["summary"],
        "summary_after": after["summary"],
        "has_changes": bool(rank_changes or rec_changes),
    }


def _rank_change_text(old: dict, new: dict) -> str:
    parts = []
    if old["status"] != new["status"]:
        parts.append(f"可比性 {old['status_label']} → {new['status_label']}")
    if old["rank_net"] != new["rank_net"]:
        parts.append(f"净购买名次 {old['rank_net'] or '未入榜'} → {new['rank_net'] or '未入榜'}")
    if old["net_per_day"] != new["net_per_day"]:
        before = f"{old['net_per_day']:.2f}" if old["net_per_day"] is not None else "—"
        after = f"{new['net_per_day']:.2f}" if new["net_per_day"] is not None else "—"
        parts.append(f"净购买/可售日 {before} → {after}")
    return "；".join(parts)


def compare_revisions(dataset: Dataset, before_as_of: str | None, after_as_of: str | None,
                      audience: str = "") -> dict:
    before = _snapshot(dataset_at(dataset, before_as_of), audience)
    after = _snapshot(dataset_at(dataset, after_as_of), audience)
    diff = diff_snapshots(before, after)
    diff["before_as_of"] = before_as_of
    diff["after_as_of"] = after_as_of
    return diff
