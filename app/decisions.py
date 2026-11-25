"""评审决定台账：每次批准/驳回都固化当时的依据，后续修订不覆盖历史。

存储为 JSON 行（``data/decisions.jsonl``）——追加写、可读、可 diff，也能直接进版本库。
首次批准前会重新计算当时的证据快照（阶段、共同可售、排名、约束），
之后即使谱系或库存被修订，这条记录仍保留原依据，并进入复核队列提示复核。
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from .domain import DECISION_APPROVED, DECISION_LABELS, DECISION_REJECTED, Dataset
from .metrics import analyze_dataset
from .resources import build_portfolio

_LOCK = threading.Lock()
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "decisions.jsonl"


class DecisionError(ValueError):
    pass


def _path(path: str | Path | None = None) -> Path:
    return Path(path) if path else DEFAULT_PATH


def load_decisions(path: str | Path | None = None) -> list[dict]:
    target = _path(path)
    if not target.exists():
        return []
    records = []
    with target.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"decision_id": f"corrupt-{line_no}", "status": "corrupt",
                                "raw": line, "note": "记录损坏，请人工核查"})
    return records


def append_decision(record: dict, path: str | Path | None = None) -> dict:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def evidence_snapshot(dataset: Dataset, sku: str, audience: str = "") -> dict:
    result = analyze_dataset(dataset, audience)
    analyses = result["analyses_obj"]
    analysis = analyses.get(sku)
    if analysis is None:
        raise DecisionError(f"未登记商品 {sku}")
    recommendations = {r.sku: r for r in build_portfolio(dataset, analyses)}
    recommendation = recommendations.get(sku)
    stages = []
    for cohort in ("mm1", "mm2", "mm3"):
        stage = analysis.stages.get(cohort)
        if stage is None:
            continue
        stages.append({
            "cohort": cohort,
            "status": stage.status,
            "window": [stage.window_start.isoformat(), stage.window_end.isoformat()],
            "sellable_days": stage.sellable_days,
            "window_days": stage.basis.window_days,
            "net": stage.net,
            "net_per_day": stage.net_per_day,
            "net_per_spend": stage.net_per_spend,
            "contribution_per_spend": stage.contribution_per_spend,
            "repeat_rate": stage.repeat_rate,
            "rank_net": stage.rank_net,
            "peer_count": stage.peer_count,
            "reasons": [r.to_dict() for r in stage.reasons],
        })
    return {
        "sku": sku,
        "name": analysis.product.name or sku,
        "lineage_label": analysis.lineage_label,
        "lineage_status": analysis.product.lineage_status,
        "predecessor": analysis.product.predecessor,
        "product_status": analysis.status,
        "audience": audience,
        "audience_label": analysis.audience_label,
        "stages": stages,
        "recommendation": recommendation.to_dict() if recommendation else None,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "data_as_of": max(
            [r.as_of for r in dataset.inventory if r.as_of] or [date.today()]
        ).isoformat(),
        "warnings": dataset.warnings,
        "source_files": dataset.sources,
    }


def record_decision(dataset: Dataset, payload: dict, path: str | Path | None = None) -> dict:
    sku = (payload.get("sku") or "").strip()
    plan_id = (payload.get("plan_id") or "").strip()
    status = (payload.get("status") or "").strip().lower()
    if not sku:
        raise DecisionError("缺少 sku")
    if status not in (DECISION_APPROVED, DECISION_REJECTED):
        raise DecisionError("status 必须为 approved 或 rejected")
    plan = next((p for p in dataset.content_plans if p.plan_id == plan_id), None)
    if plan_id and plan is None:
        raise DecisionError(f"未登记内容排期 {plan_id}")
    if plan is not None and plan.sku != sku:
        raise DecisionError(f"排期 {plan_id} 属于 {plan.sku}，与 {sku} 不一致")
    basis = evidence_snapshot(dataset, sku, (payload.get("audience") or "").strip())
    record = {
        "decision_id": payload.get("decision_id") or f"DEC-{date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        "decided_at": datetime.now().isoformat(timespec="seconds"),
        "decided_on": (payload.get("decided_on") or date.today().isoformat()),
        "actor": (payload.get("actor") or "未署名").strip(),
        "status": status,
        "status_label": DECISION_LABELS[status],
        "sku": sku,
        "plan_id": plan_id,
        "plan_amount": float(payload.get("plan_amount") or (plan.amount if plan else 0.0)),
        "selected_audience": basis["audience"],
        "note": (payload.get("note") or "").strip(),
        "basis": basis,
        "basis_version": "v1",
    }
    return append_decision(record, path)


def seed_from_dataset(dataset: Dataset, path: str | Path | None = None) -> list[dict]:
    """把输入里的历史决定（plan_decisions.csv）导入台账，已存在的 decision_id 跳过。

    历史决定只在首次导入时补入；台账里的记录一旦写入就不再被 CSV 覆盖，
    这样后续修订不会改写当时的依据。
    """
    existing = {r.get("decision_id") for r in load_decisions(path)}
    imported = []
    for decision in dataset.decisions:
        if not decision.decision_id or decision.decision_id in existing:
            continue
        record = {
            "decision_id": decision.decision_id,
            "decided_at": f"{decision.decided_on.isoformat() if decision.decided_on else '1970-01-01'}T00:00:00",
            "decided_on": decision.decided_on.isoformat() if decision.decided_on else "",
            "actor": decision.actor or "未署名",
            "status": decision.status,
            "status_label": DECISION_LABELS.get(decision.status, decision.status),
            "sku": decision.sku,
            "plan_id": decision.plan_id,
            "plan_amount": decision.plan_amount,
            "selected_audience": decision.selected_audience,
            "note": decision.note,
            "basis": decision.basis,
            "basis_version": decision.basis.get("basis_version", "v1") if decision.basis else "v1",
            "imported_from": dataset.sources.get("plan_decisions", "plan_decisions.csv"),
        }
        append_decision(record, path)
        imported.append(record)
    return imported


def latest_decisions(records: list[dict]) -> dict[str, dict]:
    """同一 plan_id / sku 取最新一条。"""
    latest: dict[str, dict] = {}
    for record in records:
        key = record.get("plan_id") or record.get("sku")
        current = latest.get(key)
        if current is None or (record.get("decided_at") or "") >= (current.get("decided_at") or ""):
            latest[key] = record
    return latest


def review_queue(dataset: Dataset, records: list[dict], analyses=None, recommendations=None) -> list[dict]:
    """已批准决定的依据在今天是否仍成立；不成立则进复核队列。"""
    result = analyze_dataset(dataset)
    analyses = analyses or result["analyses_obj"]
    recommendations = recommendations or {r.sku: r for r in build_portfolio(dataset, analyses)}
    queue = []
    for record in records:
        if record.get("status") != DECISION_APPROVED:
            continue
        sku = record.get("sku")
        analysis = analyses.get(sku)
        if analysis is None:
            queue.append({
                "decision_id": record.get("decision_id"), "sku": sku, "plan_id": record.get("plan_id"),
                "reason": "商品已不在当前谱系中", "severity": "high",
                "decided_on": record.get("decided_on"), "basis": record.get("basis", {}),
            })
            continue
        triggers = []
        basis = record.get("basis") or {}
        captured = {s["cohort"]: s for s in basis.get("stages", [])}
        for cohort, old in captured.items():
            new = analysis.stages.get(cohort)
            if new is None:
                continue
            if old.get("status") != new.status:
                triggers.append(f"{cohort} 可比性 {old.get('status')} → {new.status}")
            if old.get("net") != new.net:
                triggers.append(f"{cohort} 净购买 {old.get('net')} → {new.net}")
            if old.get("sellable_days") != new.sellable_days:
                triggers.append(f"{cohort} 可售日 {old.get('sellable_days')} → {new.sellable_days}")
            if old.get("rank_net") != new.rank_net:
                triggers.append(f"{cohort} 名次 {old.get('rank_net')} → {new.rank_net}")
        old_rec = basis.get("recommendation") or {}
        new_rec = recommendations.get(sku)
        if new_rec is not None:
            if old_rec.get("action") != new_rec.action:
                triggers.append(f"资源建议 {old_rec.get('label', old_rec.get('action'))} → {new_rec.label}")
            if old_rec.get("suggested_amount") != new_rec.suggested_amount:
                triggers.append(f"建议额度 {old_rec.get('suggested_amount')} → {new_rec.suggested_amount}")
        if analysis.product.lineage_status != basis.get("lineage_status"):
            triggers.append("谱系状态已修订")
        if analysis.product.predecessor != basis.get("predecessor"):
            triggers.append("前代关系已修订")
        if triggers:
            queue.append({
                "decision_id": record.get("decision_id"),
                "sku": sku,
                "plan_id": record.get("plan_id"),
                "plan_amount": record.get("plan_amount", 0),
                "decided_on": record.get("decided_on"),
                "actor": record.get("actor"),
                "triggers": triggers,
                "severity": "high" if any("谱系" in t or "名次" in t for t in triggers) else "medium",
                "basis": basis,
                "status": record.get("status"),
            })
    queue.sort(key=lambda item: ({"high": 0, "medium": 1}.get(item["severity"], 2), item.get("decided_on") or ""))
    return queue


def decision_history(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: r.get("decided_at") or "", reverse=True)
