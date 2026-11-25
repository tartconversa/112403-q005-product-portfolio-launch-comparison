"""本地评审应用（标准库 http.server，无外部依赖）。

启动：
    python3 -m app.server --data data/sample --port 8000

路由
----
GET  /                总览
GET  /products        商品列表（带筛选）
GET  /product/<sku>   商品评审详情
GET  /resources       资源建议
GET  /revisions       修订前后对比
GET  /decisions       决定台账与复核队列
GET  /method          口径说明
GET  /api/...         JSON（供审阅与集成）
POST /decisions/new   写入决定台账
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import decisions as decision_store
from . import views
from .domain import Dataset, load_dataset
from .metrics import analyze_dataset
from .resources import build_portfolio, recommendation_summary
from .revisions import compare_revisions, dataset_at

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA = APP_DIR / "data" / "sample"
DEFAULT_LEDGER = APP_DIR / "data" / "decisions.jsonl"


class Context:
    def __init__(self, data_dir: Path, ledger_path: Path):
        self.data_dir = Path(data_dir)
        self.ledger_path = Path(ledger_path)
        self.dataset = load_dataset(self.data_dir)
        decision_store.seed_from_dataset(self.dataset, self.ledger_path)

    def reload(self) -> None:
        self.dataset = load_dataset(self.data_dir)
        decision_store.seed_from_dataset(self.dataset, self.ledger_path)

    def scoped(self, ctx: dict) -> Dataset:
        return dataset_at(self.dataset, ctx.get("as_of") or None)

    def refresh_decisions(self, scoped: Dataset) -> None:
        """把 CSV 中新增的历史决定导入台账；已有记录永不覆盖。"""
        decision_store.seed_from_dataset(scoped, self.ledger_path)


def query_context(params: dict) -> dict:
    return {
        "stage": params.get("stage", [""])[0] or "mm1",
        "audience": params.get("audience", [""])[0],
        "as_of": params.get("as_of", [""])[0],
    }


def available_cutoffs(dataset: Dataset) -> list[str]:
    """从录入日期生成可选的数据截止版本，最近的在前。"""
    dates = set()
    for record in dataset.availability:
        if record.added_on:
            dates.add(record.added_on.isoformat())
    for record in dataset.inventory:
        if record.as_of:
            dates.add(record.as_of.isoformat())
    for revision in dataset.lineage_revisions:
        if revision.changed_on:
            dates.add(revision.changed_on.isoformat())
    return sorted(dates, reverse=True)[:4]


def build_view_context(dataset: Dataset, ctx: dict, ledger: Path | None = None) -> dict:
    scoped = dataset_at(dataset, ctx.get("as_of") or None)
    result = analyze_dataset(scoped, ctx.get("audience", ""))
    analyses = result["analyses_obj"]
    recommendations = build_portfolio(scoped, analyses)
    summary = recommendation_summary(recommendations)
    records = decision_store.load_decisions(ledger)
    queue = decision_store.review_queue(scoped, records, analyses, {r.sku: r for r in recommendations})
    return {
        "dataset": scoped,
        "result": result,
        "analyses": analyses,
        "recommendations": recommendations,
        "recommendation_map": {r.sku: r for r in recommendations},
        "summary": summary,
        "records": records,
        "queue": queue,
    }


def render(handler, body: str, ctx: dict, active: str, title: str, status: int = 200) -> None:
    html = views.page(title, body, {k: v for k, v in ctx.items() if k in ("stage", "audience", "as_of")}, active)
    payload = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def send_json(handler, data, status: int = 200) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def make_handler(context: Context):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NewProductReview/1.0"

        def log_message(self, fmt, *args):  # 安静一点，保留错误
            if args and str(args[1]).startswith(("4", "5")):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        # -------------------------------------------------- routing

        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            path = parsed.path.rstrip("/") or "/"
            ctx = query_context(params)
            ctx["before"] = params.get("before", [""])[0]
            ctx["after"] = params.get("after", [""])[0]
            try:
                self.route_get(path, params, ctx)
            except Exception as exc:  # 展示错误而不是白屏
                self.render_error(exc)

        def render_error(self, exc: Exception):
            body = (f"<h1>页面无法生成</h1><p class=\"lede\">输入数据或计算过程中出现问题：</p>"
                    f"<div class=\"banner critical\">{views.e(str(exc))}</div>"
                    f"<p><a href=\"/\">返回总览</a></p>")
            render(self, body, {}, "", "错误", status=500)

        def route_get(self, path: str, params: dict, ctx: dict):
            if path.startswith("/api/"):
                return self.route_api(path, params, ctx)
            if path == "/":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                body = views.overview_body(ctx, view["dataset"], view["result"], view["recommendations"],
                                           view["queue"], view["summary"])
                body = views.filters_html(ctx, view["dataset"].audience_groups,
                                          available_cutoffs(context.dataset)) + body
                return render(self, body, ctx, "overview", "总览")
            if path == "/products":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                body = views.filters_html(ctx, view["dataset"].audience_groups,
                                          available_cutoffs(context.dataset)) + views.products_body(ctx, view)
                return render(self, body, ctx, "products", "商品列表")
            if path.startswith("/product/"):
                ctx["sku"] = path.split("/", 2)[2]
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                body = views.product_body(ctx, view["dataset"], view["result"], view["recommendation_map"], view["queue"])
                analysis = view["analyses"].get(ctx["sku"])
                title = analysis.product.name if analysis else ctx["sku"]
                return render(self, body, ctx, "products", title)
            if path == "/resources":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                body = views.resources_body(ctx, view["dataset"], view["recommendations"], view["summary"])
                return render(self, body, ctx, "resources", "资源建议")
            if path == "/revisions":
                before = ctx.get("before") or "2026-11-17"
                diff = compare_revisions(context.dataset, before,
                                         ctx.get("after") or None, ctx.get("audience", ""))
                body = views.revisions_body(ctx, context.dataset, diff)
                return render(self, body, ctx, "revisions", "修订对比")
            if path == "/decisions":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                body = views.decisions_body(ctx, view["dataset"], view["records"], view["queue"])
                return render(self, body, ctx, "decisions", "决定与复核")
            if path == "/method":
                body = views.method_body(ctx, context.dataset)
                return render(self, body, ctx, "method", "口径说明")
            self.send_error(HTTPStatus.NOT_FOUND, "页面不存在")

        def route_api(self, path: str, params: dict, ctx: dict):
            if path == "/api/rankings":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                return send_json(self, {
                    "context": ctx,
                    "warnings": view["dataset"].warnings,
                    "rankings": view["result"]["rankings"],
                    "summary": view["summary"],
                })
            if path == "/api/products":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                return send_json(self, {
                    "products": view["result"]["analyses"],
                    "warnings": view["dataset"].warnings,
                })
            if path == "/api/product":
                sku = params.get("sku", [""])[0]
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                if sku not in view["analyses"]:
                    return send_json(self, {"error": f"未登记商品 {sku}"}, 404)
                analysis = view["analyses"][sku]
                rec = view["recommendation_map"].get(sku)
                return send_json(self, {
                    "product": analysis.to_dict(),
                    "recommendation": rec.to_dict() if rec else None,
                    "decisions": [r for r in view["records"] if r.get("sku") == sku],
                })
            if path == "/api/recommendations":
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                return send_json(self, {
                    "summary": view["summary"],
                    "recommendations": [r.to_dict() for r in view["recommendations"]],
                })
            if path == "/api/revisions":
                diff = compare_revisions(context.dataset, params.get("before", [None])[0],
                                         params.get("after", [None])[0], ctx.get("audience", ""))
                return send_json(self, diff)
            if path == "/api/decisions":
                records = decision_store.load_decisions(context.ledger_path)
                view = build_view_context(context.dataset, ctx, context.ledger_path)
                return send_json(self, {"records": records, "review_queue": view["queue"]})
            return send_json(self, {"error": "未知接口"}, 404)

        # -------------------------------------------------- mutations

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            form = parse_qs(raw)
            if parsed.path.rstrip("/") == "/decisions/new":
                payload = {k: v[0] for k, v in form.items() if v}
                try:
                    record = decision_store.record_decision(context.dataset, payload, context.ledger_path)
                except decision_store.DecisionError as exc:
                    body = (f"<h1>决定未写入</h1><div class=\"banner critical\">{views.e(str(exc))}</div>"
                            f"<p><a href=\"/decisions\">返回台账</a></p>")
                    return render(self, body, {}, "decisions", "决定未写入", status=400)
                target = payload.get("return_to") or "/decisions"
                target = target if target.startswith("/") and not target.startswith("//") else "/decisions"
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", target)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND, "接口不存在")

    return Handler


def serve(data_dir: str | Path = DEFAULT_DATA, ledger_path: str | Path = DEFAULT_LEDGER,
          host: str = "127.0.0.1", port: int = 8000) -> None:
    context = Context(Path(data_dir), Path(ledger_path))
    handler = make_handler(context)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"新品评审看板已启动：http://{host}:{port}  （数据：{context.data_dir}）")
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="新品成长基准与资源评审看板")
    parser.add_argument("--data", default=os.environ.get("REVIEW_DATA", str(DEFAULT_DATA)))
    parser.add_argument("--ledger", default=os.environ.get("REVIEW_LEDGER", str(DEFAULT_LEDGER)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)
    serve(args.data, args.ledger, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
