"""服务端渲染的评审看板（仅用标准库）。

信息层级：
1. 顶部筛选行（阶段 / 客群 / 数据截止）统一定义所有下方视图的口径；
2. KPI 行给总量，排名用横向条形图与表格并列；
3. 每个结论都带「为什么不可直接比较」的原因和待核项。
"""

from __future__ import annotations

import html
import re as _re
from urllib.parse import urlencode

from .domain import (
    AWAITING,
    COHORTS,
    COHORT_LABELS,
    COHORT_SHORT,
    COMPARABLE,
    COMPARABLE_WITH_CAVEAT,
    COMPARABILITY_LABELS,
    CONTENT_STAGE_LABELS,
    DECISION_APPROVED,
    DECISION_LABELS,
    NOT_COMPARABLE,
    OUT_OF_SCOPE,
    PENDING,
    REC_LABELS,
    REC_SEVERITY,
    SEVERITY_LABELS,
    Dataset,
)
from .metrics import ProductAnalysis

STATUS_CLASS = {
    COMPARABLE: "good",
    COMPARABLE_WITH_CAVEAT: "warning",
    AWAITING: "neutral",
    OUT_OF_SCOPE: "neutral",
    PENDING: "serious",
    NOT_COMPARABLE: "critical",
}
ACTION_CLASS = {"good": "good", "warning": "warning", "serious": "serious",
                "critical": "critical", "neutral": "neutral"}

CSS = r"""
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --page: #f9f9f7;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --ink-3: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,.10);
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --seq-100: #cde2fb; --seq-250: #86b6ef; --seq-400: #3987e5; --seq-550: #1c5cab;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --good-ink: #00630d; --warn-ink: #7a5200; --ser-ink: #9c421c; --crit-ink: #a32424;
  --chip: rgba(11,11,11,.04);
  --shadow: 0 1px 2px rgba(11,11,11,.04), 0 8px 24px rgba(11,11,11,.04);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface: #1a1a19; --page: #0d0d0d; --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --seq-100: #184f95; --seq-250: #256abf; --seq-400: #3987e5; --seq-550: #9ec5f4;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
    --good-ink: #57c957; --warn-ink: #fab219; --ser-ink: #ec835a; --crit-ink: #ef8c8c;
    --chip: rgba(255,255,255,.06); --shadow: 0 1px 2px rgba(0,0,0,.4);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --page: #0d0d0d; --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #898781;
  --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,.10);
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  --seq-100: #184f95; --seq-250: #256abf; --seq-400: #3987e5; --seq-550: #9ec5f4;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  --good-ink: #57c957; --warn-ink: #fab219; --ser-ink: #ec835a; --crit-ink: #ef8c8c;
  --chip: rgba(255,255,255,.06); --shadow: 0 1px 2px rgba(0,0,0,.4);
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.55 system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
a { color: inherit; text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 3px; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px 64px; }
header.top { border-bottom: 1px solid var(--border); background: var(--page);
  position: sticky; top: 0; z-index: 20; }
.top-inner { max-width: 1180px; margin: 0 auto; padding: 14px 24px; display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
.brand { font-weight: 650; letter-spacing: .01em; }
.brand small { display: block; font-weight: 400; color: var(--ink-3); font-size: 12px; letter-spacing: 0; }
nav.main { display: flex; gap: 4px; margin-left: auto; flex-wrap: wrap; }
nav.main a { padding: 7px 12px; border-radius: 8px; color: var(--ink-2); font-size: 13px; }
nav.main a:hover { background: var(--chip); text-decoration: none; }
nav.main a[aria-current="page"] { background: var(--chip); color: var(--ink); font-weight: 600; }
.theme-toggle { border: 1px solid var(--border); background: var(--surface); color: var(--ink-2);
  border-radius: 8px; padding: 6px 10px; cursor: pointer; font: inherit; font-size: 12px; }
h1 { font-size: 22px; margin: 28px 0 4px; letter-spacing: -.01em; }
h2 { font-size: 16px; margin: 0 0 4px; letter-spacing: -.005em; }
h3 { font-size: 13px; margin: 0 0 8px; color: var(--ink-2); font-weight: 600; }
p.lede { color: var(--ink-2); margin: 0 0 20px; max-width: 78ch; }
.muted { color: var(--ink-3); }
.small { font-size: 12px; }
.filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  padding: 12px 0 18px; border-bottom: 1px solid var(--border); margin-bottom: 22px; }
.filters label { font-size: 12px; color: var(--ink-3); display: flex; gap: 6px; align-items: center; }
.filters select { font: inherit; font-size: 13px; color: var(--ink); background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; }
.filters .spacer { flex: 1; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 26px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 16px 18px; box-shadow: var(--shadow); }
.tile .label { font-size: 12px; color: var(--ink-3); margin-bottom: 6px; }
.tile .value { font-size: 28px; font-weight: 620; letter-spacing: -.02em; line-height: 1.15;
  font-variant-numeric: normal; }
.tile .value small { font-size: 14px; color: var(--ink-2); font-weight: 500; }
.tile .sub { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
  padding: 20px 22px; margin-bottom: 18px; box-shadow: var(--shadow); }
.card-head { display: flex; gap: 12px; align-items: baseline; justify-content: space-between;
  margin-bottom: 14px; flex-wrap: wrap; }
.card-head .hint { font-size: 12px; color: var(--ink-3); }
.grid2 { display: grid; grid-template-columns: 1.25fr .75fr; gap: 18px; }
@media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
.chart { margin-top: 4px; }
.bar-row { display: grid; grid-template-columns: 180px 1fr 132px; gap: 12px; align-items: center;
  padding: 7px 0; border-radius: 8px; }
.bar-row:hover, .bar-row:focus-visible { background: var(--chip); outline: none; }
.bar-row .name { font-size: 13px; overflow-wrap: anywhere; }
.bar-row .name .sub { display: block; color: var(--ink-3); font-size: 11px; }
.bar-track { position: relative; height: 22px; display: flex; align-items: center; }
.bar { height: 14px; border-radius: 0 4px 4px 0; background: var(--series-1); min-width: 2px; }
.bar.not-ranked { background: repeating-linear-gradient(45deg, var(--grid) 0 4px, transparent 4px 8px);
  border: 1px solid var(--baseline); border-radius: 4px; height: 12px; }
.bar-value { font-variant-numeric: tabular-nums; font-size: 13px; color: var(--ink); text-align: right; }
.bar-value small { color: var(--ink-3); font-size: 11px; }
.axis { display: grid; grid-template-columns: 180px 1fr 132px; gap: 12px; margin-top: 6px;
  color: var(--ink-3); font-size: 11px; }
.axis .ticks { display: flex; justify-content: space-between; border-top: 1px solid var(--grid); padding-top: 4px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { font-size: 11px; font-weight: 600; color: var(--ink-3); text-transform: none; letter-spacing: .02em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tbody tr:hover { background: var(--chip); }
tr.is-pending td { color: var(--ink-2); }
.chip { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600;
  border-radius: 999px; padding: 2px 8px; border: 1px solid var(--border); background: var(--chip);
  white-space: nowrap; }
.chip .dot { width: 7px; height: 7px; border-radius: 50%; }
.chip.good .dot { background: var(--good); } .chip.warning .dot { background: var(--warning); }
.chip.serious .dot { background: var(--serious); } .chip.critical .dot { background: var(--critical); }
.chip.neutral .dot { background: var(--ink-3); }
.chip.good { color: var(--good-ink); } .chip.warning { color: var(--warn-ink); }
.chip.serious { color: var(--ser-ink); } .chip.critical { color: var(--crit-ink); }
.reasons { list-style: none; padding: 0; margin: 6px 0 0; display: grid; gap: 4px; }
.reasons li { font-size: 12px; color: var(--ink-2); display: flex; gap: 8px; align-items: flex-start; }
.sev { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--border);
  color: var(--ink-2); flex: none; margin-top: 1px; }
.sev.high { border-color: var(--critical); color: var(--crit-ink); }
.sev.medium { border-color: var(--serious); color: var(--ser-ink); }
.sev.low { border-color: var(--baseline); }
details.table-view { margin-top: 14px; border-top: 1px solid var(--grid); padding-top: 10px; }
details.table-view summary { cursor: pointer; font-size: 12px; color: var(--ink-2); }
details.table-view summary:hover { color: var(--ink); }
.product-meta { display: flex; gap: 18px; flex-wrap: wrap; color: var(--ink-2); font-size: 12px; margin-bottom: 14px; }
.product-meta div strong { display: block; color: var(--ink); font-size: 13px; font-weight: 600; }
.stage-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
@media (max-width: 900px) { .stage-grid { grid-template-columns: 1fr; } }
.stage { border: 1px solid var(--border); border-radius: 12px; padding: 14px; background: var(--surface); }
.stage h4 { margin: 0 0 2px; font-size: 13px; }
.stage .window { color: var(--ink-3); font-size: 11px; margin-bottom: 10px; }
.stage dl { display: grid; grid-template-columns: 1fr auto; gap: 5px 10px; margin: 0; font-size: 12px; }
.stage dt { color: var(--ink-3); }
.stage dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.stage .reasons { margin-top: 10px; border-top: 1px solid var(--grid); padding-top: 8px; }
.big-number { font-size: 30px; font-weight: 640; letter-spacing: -.02em; }
.rec { border-left: 3px solid var(--baseline); padding-left: 14px; }
.rec.advance { border-color: var(--good); } .rec.pause { border-color: var(--serious); }
.rec.hold, .rec.early_watch { border-color: var(--warning); }
.rec.pending { border-color: var(--serious); } .rec.blocked { border-color: var(--critical); }
.rec h3 { color: var(--ink); font-size: 14px; margin-bottom: 2px; }
.rec .guards { margin: 10px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.rec .guards li { font-size: 12px; color: var(--ink-2); border-top: 1px solid var(--grid); padding-top: 6px; line-height: 1.7; }
.rec .guards .chip { margin: 0 4px; }
.preserved { background: var(--chip); border-radius: 10px; padding: 10px 12px; margin-top: 10px; font-size: 12px; }
.preserved strong { font-variant-numeric: tabular-nums; }
.form-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-top: 10px; }
.form-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--ink-3); }
.form-row input, .form-row select, .form-row textarea {
  font: inherit; font-size: 13px; color: var(--ink); background: var(--page);
  border: 1px solid var(--border); border-radius: 8px; padding: 7px 9px; }
.form-row textarea { min-width: 260px; min-height: 38px; resize: vertical; }
button.primary { font: inherit; font-size: 13px; font-weight: 600; background: var(--series-1);
  color: #fff; border: 0; border-radius: 8px; padding: 8px 16px; cursor: pointer; }
button.primary:hover { filter: brightness(1.06); }
button.secondary { font: inherit; font-size: 13px; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 14px; cursor: pointer; }
.banner { border: 1px solid var(--border); border-left: 3px solid var(--warning); background: var(--surface);
  padding: 12px 14px; border-radius: 10px; margin-bottom: 18px; font-size: 13px; color: var(--ink-2); }
.banner.critical { border-left-color: var(--critical); }
.banner.good { border-left-color: var(--good); }
.diff { border-top: 1px solid var(--grid); padding: 12px 0; }
.diff .change { font-size: 13px; }
.diff .pair { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
.pair-table td.before { color: var(--ink-3); }
.pill-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 0; }
.timeline { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; }
.timeline li { display: grid; grid-template-columns: 110px 1fr auto; gap: 12px; font-size: 12px;
  border-top: 1px solid var(--grid); padding-top: 8px; }
.timeline .when { color: var(--ink-3); font-variant-numeric: tabular-nums; }
.empty { color: var(--ink-3); font-size: 13px; padding: 12px 0; }
.tooltip { position: fixed; z-index: 50; pointer-events: none; background: var(--ink); color: var(--page);
  font-size: 12px; padding: 7px 10px; border-radius: 8px; box-shadow: var(--shadow); max-width: 320px;
  opacity: 0; transform: translateY(4px); transition: opacity .08s ease, transform .08s ease; }
.tooltip.show { opacity: 1; transform: translateY(0); }
.tooltip .v { font-weight: 700; font-variant-numeric: tabular-nums; }
footer { color: var(--ink-3); font-size: 12px; border-top: 1px solid var(--border); margin-top: 40px; padding-top: 16px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
"""

JS = r"""
(function(){
  var tip = document.getElementById('tooltip');
  function show(el){
    if(!tip) return;
    var name = el.getAttribute('data-name') || '';
    var value = el.getAttribute('data-value') || '';
    var detail = el.getAttribute('data-detail') || '';
    tip.textContent = '';
    var v = document.createElement('div'); v.className = 'v'; v.textContent = value;
    var n = document.createElement('div'); n.textContent = name;
    tip.appendChild(v); tip.appendChild(n);
    if(detail){ var d = document.createElement('div'); d.textContent = detail; d.style.opacity='.8'; tip.appendChild(d); }
    tip.classList.add('show');
    var r = el.getBoundingClientRect();
    var left = Math.min(window.innerWidth - 340, Math.max(8, r.left));
    var top = r.bottom + 8;
    if(top > window.innerHeight - 100) top = r.top - 74;
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  }
  function hide(){ if(tip) tip.classList.remove('show'); }
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('pointerenter', function(){ show(el); });
    el.addEventListener('pointerleave', hide);
    el.addEventListener('focus', function(){ show(el); });
    el.addEventListener('blur', hide);
  });
  var root = document.documentElement;
  var stored = null;
  try { stored = localStorage.getItem('theme'); } catch(e){}
  if(stored){ root.setAttribute('data-theme', stored); }
  var btn = document.getElementById('theme-toggle');
  if(btn){
    btn.addEventListener('click', function(){
      var cur = root.getAttribute('data-theme');
      if(!cur){ cur = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
      var next = cur === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch(e){}
      btn.textContent = next === 'dark' ? '浅色' : '深色';
    });
  }
})();
"""


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value) -> str:
    if value is None:
        return "—"
    return f"¥{value:,.0f}"


def num(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def pct(value) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}"


def status_chip(status: str) -> str:
    return (f'<span class="chip {STATUS_CLASS.get(status, "neutral")}">'
            f'<span class="dot" aria-hidden="true"></span>{e(COMPARABILITY_LABELS.get(status, status))}</span>')


def action_chip(recommendation) -> str:
    cls = ACTION_CLASS.get(RECOMMENDATION_SEVERITY(recommendation), "neutral")
    return (f'<span class="chip {cls}"><span class="dot" aria-hidden="true"></span>'
            f'{e(recommendation.label)}</span>')


def RECOMMENDATION_SEVERITY(rec) -> str:
    return rec.severity if hasattr(rec, "severity") else REC_SEVERITY.get(rec.get("action"), "neutral")


def reasons_html(reasons, limit: int | None = None) -> str:
    items = reasons if limit is None else reasons[:limit]
    if not items:
        return '<div class="empty small">无附加原因</div>'
    out = ['<ul class="reasons">']
    for reason in items:
        severity = reason.severity if hasattr(reason, "severity") else reason.get("severity", "low")
        text = reason.text if hasattr(reason, "text") else reason.get("text", "")
        source = reason.source if hasattr(reason, "source") else reason.get("source", "")
        out.append(
            f'<li><span class="sev {e(severity)}">{e(SEVERITY_LABELS.get(severity, severity))}</span>'
            f'<span>{e(text)} <span class="muted small">[{e(source)}]</span></span></li>'
        )
    out.append("</ul>")
    return "".join(out)


def page(title: str, body: str, ctx: dict, active: str) -> str:
    nav_items = [("overview", "总览", "/"), ("products", "商品评审", "/products"),
                 ("resources", "资源建议", "/resources"), ("revisions", "修订对比", "/revisions"),
                 ("decisions", "决定与复核", "/decisions"), ("method", "口径说明", "/method")]
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href}"{current if key == active else ""}>{label}</a>'
        for key, label, href in nav_items
    )
    query = urlencode({k: v for k, v in ctx.items() if v})
    base = f"?{query}" if query else ""
    theme_label = "深色"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · 新品成长基准</title>
<style>{CSS}</style>
</head>
<body>
<header class="top">
  <div class="top-inner">
    <div class="brand">新品成长基准与资源评审<small>净购买 · 复购信号 · 每单位资源贡献</small></div>
    <nav class="main" aria-label="主导航">{nav}</nav>
    <button class="theme-toggle" id="theme-toggle" type="button" aria-label="切换深浅色">{theme_label}</button>
  </div>
</header>
<div class="wrap">
{body}
<footer>
  合成样例数据 · 口径见 <a href="/method">口径说明</a>。
  所有金额为合成值；「共同可售」由渠道上架、缺货与实际首卖日推导，待核数据不进入排名。
</footer>
</div>
<div id="tooltip" class="tooltip" role="status" aria-live="polite"></div>
<script>{JS}</script>
</body>
</html>"""


def filters_html(ctx: dict, datasets: list, cutoffs: list | None = None, hidden: dict | None = None) -> str:
    def options(items, selected):
        return "".join(
            f'<option value="{e(value)}"{" selected" if value == selected else ""}>{e(label)}</option>'
            for value, label in items
        )
    hidden_inputs = "".join(
        f'<input type="hidden" name="{e(k)}" value="{e(v)}">' for k, v in (hidden or {}).items()
    )
    audience_items = [("", "全部客群")] + [(g.group_id, g.label) for g in datasets]
    return f"""
<form class="filters" method="get" action="">
  {hidden_inputs}
  <label>上市阶段
    <select name="stage">{options([(c, COHORT_LABELS[c]) for c in COHORTS], ctx.get("stage"))}</select>
  </label>
  <label>客群
    <select name="audience">{options(audience_items, ctx.get("audience"))}</select>
  </label>
  <label>数据截止
    <select name="as_of">
      {options([("", "全部已录入数据")] + [(c, f"截至 {c}" + ("（含补录/修订）" if i == 0 else "")) for i, c in enumerate(cutoffs or [])], ctx.get("as_of"))}
    </select>
  </label>
  <span class="spacer"></span>
  <button class="secondary" type="submit">应用筛选</button>
</form>"""


def kpi_tiles(summary: dict, analyses: dict, queue: list) -> str:
    pending = sum(1 for a in analyses.values() if a.status == PENDING and a.is_new)
    blocked = sum(1 for a in analyses.values() if a.status == NOT_COMPARABLE and a.is_new)
    tiles = [
        ("已承诺内容资源", money(summary["total_preserved"]),
         "已批准或已签约，任何建议都不挪用", None),
        ("本次建议追加", money(summary["total_suggested"]),
         "仅来自未承诺预算，需人工批准", None),
        ("待核新品", f"{pending}", "不参与简单排名", None),
        ("建议复核的历史决定", f"{len(queue)}", "批准时的依据已变化", None),
    ]
    out = '<div class="kpis">'
    for label, value, sub, _ in tiles:
        out += (f'<div class="tile"><div class="label">{e(label)}</div>'
                f'<div class="value">{e(value)}</div><div class="sub">{e(sub)}</div></div>')
    return out + "</div>"


def ranking_chart(rows: list[dict], cohort: str) -> str:
    """按品类分面。不同品类的单品销售强度没有可比性，不能共用一条轴。"""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["category"] or "未分类", []).append(row)
    out = []
    for category, items in groups.items():
        ranked = [r for r in items if r["rank_net"] is not None and r["net_per_day"] is not None]
        unranked = [r for r in items if r["rank_net"] is None]
        top = max([r["net_per_day"] for r in ranked], default=0)
        out.append(f'<h3 style="margin:16px 0 4px">{e(category)}</h3>')
        out.append('<div class="chart" role="list" aria-label="按品类分面的净购买强度排名">')
        for row in ranked:
            width = max(2.0, (row["net_per_day"] / top) * 100)
            detail = "；".join(r["text"] for r in row["reasons"][:2]) or "无附加原因"
            out.append(f"""
<div class="bar-row" role="listitem" tabindex="0" data-tip
     data-name="{e(row['name'])} · {e(row['category'])}"
     data-value="{num(row['net_per_day'], 2)} 件/渠道可售日"
     data-detail="净购买 {num(row['net'])} 件 · {e(COMPARABILITY_LABELS[row['status']])} · {e(detail)}">
  <div class="name"><a href="/product/{e(row['sku'])}?stage={e(cohort)}">{e(row['name'])}</a>
    <span class="sub">{e(row['sku'])} · 第 {row['rank_net']}/{row['peer_count']} 名</span></div>
  <div class="bar-track"><div class="bar" style="width:{width:.1f}%"></div></div>
  <div class="bar-value">{num(row['net_per_day'], 2)}<br><small>件/渠道可售日</small></div>
</div>""")
        for row in unranked:
            out.append(f"""
<div class="bar-row" role="listitem" tabindex="0" data-tip
     data-name="{e(row['name'])} · {e(row['category'])}"
     data-value="不参与名次：{e(COMPARABILITY_LABELS[row['status']])}"
     data-detail="{e('；'.join(r['text'] for r in row['reasons'][:2]) or '待核数据补齐后重算')}">
  <div class="name"><a href="/product/{e(row['sku'])}?stage={e(cohort)}">{e(row['name'])}</a>
    <span class="sub">{e(row['sku'])} · 不参与名次</span></div>
  <div class="bar-track"><div class="bar not-ranked" style="width:100%"></div></div>
  <div class="bar-value muted">{status_chip(row['status'])}</div>
</div>""")
        out.append("</div>")
        if ranked and top:
            out.append('<div class="axis"><span></span><div class="ticks">'
                       f'<span>0</span><span>{num(top / 2, 1)}</span><span>{num(top, 1)}</span>'
                       '</div><span class="small" style="text-align:right">件 / 渠道可售日</span></div>')
    if not groups:
        return '<div class="empty">当前筛选下没有新品。</div>'
    out.append('<div class="pill-row small muted">'
               '<span class="chip good"><span class="dot"></span>可直接比较</span>'
               '<span class="chip warning"><span class="dot"></span>可比，需注意</span>'
               '<span class="chip serious"><span class="dot"></span>待核，不排名</span>'
               '<span class="chip critical"><span class="dot"></span>不可直接比较</span>'
               '</div>')
    return "".join(out)


def ranking_table(rows: list[dict]) -> str:
    head = ("<thead><tr><th>商品</th><th>品类</th><th>可比性</th><th class='num'>净购买</th>"
            "<th class='num'>净购买/渠道可售日</th><th class='num'>复购代理*</th>"
            "<th class='num'>贡献/每元资源</th><th class='num'>名次</th></tr></thead>")
    body = []
    for row in rows:
        status = row["status"]
        body.append(
            f"<tr class='{'is-pending' if status == PENDING else ''}'>"
            f"<td><a href=\"/product/{e(row['sku'])}\">{e(row['name'])}</a>"
            f"<div class='muted small'>{e(row['sku'])}</div></td>"
            f"<td>{e(row['category'])}</td>"
            f"<td>{status_chip(status)}</td>"
            f"<td class='num'>{num(row['net'])}</td>"
            f"<td class='num'>{num(row['net_per_day'], 2)}</td>"
            f"<td class='num'>{pct(row.get('repeat_rate') if row.get('repeat_rate') is not None else row.get('next_repeat_rate'))}</td>"
            f"<td class='num'>{num(row['contribution_per_spend'], 2) if row['contribution_per_spend'] is not None else num(row['net_per_spend'], 3)}</td>"
            f"<td class='num'>{row['rank_net'] if row['rank_net'] is not None else '—'}</td></tr>")
    return (f"<table>{head}<tbody>{''.join(body)}</tbody></table>"
            "<p class='small muted'>* 复购代理：上市月 2 净购买 ÷ 上市月 1 净购买；月 1 视图显示其后续月份值，"
            "仅对已确认前代且两阶段可售充分的新品计算。</p>")


# ---------------------------------------------------------------- 总览


def overview_body(ctx: dict, ds: Dataset, result: dict, recommendations: list,
                  queue: list, summary: dict) -> str:
    cohort = ctx.get("stage") or "mm1"
    analyses: dict[str, ProductAnalysis] = result["analyses_obj"]
    rows = result["rankings"].get(cohort, [])
    new_rows = [r for r in rows if analyses[r["sku"]].is_new]
    body = ['<h1>新品成长基准</h1>',
            '<p class="lede">先按上市阶段与共同可售条件对齐，再比较净购买、复购信号与每单位资源的贡献。'
            '待核的谱系、长期缺货与不可核实渠道不会进入排名，也不会被当成零销售。</p>']
    body.append(scope_strip(ctx, ds, analyses, result))
    body.append(kpi_tiles(summary, analyses, queue))
    if ds.warnings:
        body.append('<div class="banner">输入关联提示：' + "<br>".join(e(w) for w in ds.warnings[:6]) + "</div>")
    body.append('<div class="grid2">')
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>{e(COHORT_LABELS[cohort])} · 净购买强度排名</h2>
  <div class="hint">同品类、同阶段、同客群内比较；分母为渠道可售日之和，缺货与未上架日已剔除。</div></div></div>
  {ranking_chart(new_rows, cohort)}
  <details class="table-view"><summary>表格视图（与图表同源）</summary>{ranking_table(new_rows)}</details>
</div>""")
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>阶段之间的变化</h2>
  <div class="hint">同一商品按月 1 → 月 2 → 月 3 的净购买/渠道可售日。</div></div></div>
  {stage_small_multiples(new_rows, analyses)}
  <p class="small muted" style="margin-top:10px">只画已结束且可比的阶段；缺数据的阶段留空，不画 0。</p>
</div>""")
    body.append("</div>")
    body.append('<div class="grid2">')
    body.append(recommendation_summary_card(recommendations, summary))
    body.append(review_queue_card(queue))
    body.append("</div>")
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>商品一览</h2><div class="hint">含前代；前代不占新品名次，但用于共同可售配对比较。</div></div></div>
  {product_overview_table(analyses)}
</div>""")
    return "".join(body)


def scope_strip(ctx: dict, ds: Dataset, analyses: dict, result: dict) -> str:
    cohort = ctx.get("stage") or "mm1"
    horizon = ""
    dates = [r.day for r in ds.daily_sales if r.day]
    if dates:
        horizon = max(dates).isoformat()
    aud = ctx.get("audience") or ""
    aud_label = "全部客群"
    if aud:
        group = next((g for g in ds.audience_groups if g.group_id == aud), None)
        aud_label = group.label if group else aud
    in_scope = sum(1 for a in analyses.values() if a.is_new and a.status != OUT_OF_SCOPE)
    pending = sum(1 for a in analyses.values() if a.is_new and a.status == PENDING)
    return f"""<div class="banner">
  <strong>当前口径</strong>：{e(COHORT_LABELS[cohort])} · {e(aud_label)} · 数据截至 {e(horizon or "未知")}。
  纳入 {in_scope} 个新品，其中 {pending} 个待核不排名。
  跨品类不共用一条轴；每张图只在同品类内比较。
</div>"""


def stage_small_multiples(rows: list[dict], analyses: dict) -> str:
    if not rows:
        return '<div class="empty">当前筛选下没有新品。</div>'
    out = ['<div class="chart" role="list" aria-label="按品类分面的净购买强度排名">']
    for row in rows[:6]:
        analysis = analyses[row["sku"]]
        cells = []
        peermax = max([s.net_per_day for a in analyses.values()
                       if a.product.category == analysis.product.category
                       for s in a.stages.values() if s.net_per_day is not None] or [1])
        for cohort in COHORTS:
            stage = analysis.stages.get(cohort)
            if stage is None or stage.net_per_day is None:
                cells.append('<span class="muted small" style="min-width:52px;text-align:center">—</span>')
                continue
            value = stage.net_per_day
            width = max(2.0, min(100.0, value / peermax * 100))
            cells.append(
                f'<span style="flex:1;display:flex;flex-direction:column;gap:3px">'
                f'<span style="height:8px;width:{width:.0f}%;background:var(--series-1);border-radius:0 4px 4px 0"></span>'
                f'<span class="small muted" style="font-variant-numeric:tabular-nums">{num(value, 1)}</span></span>'
            )
        detail = "；".join(
            f"{COHORT_SHORT[c]} {num(analysis.stages[c].net_per_day, 2)}"
            for c in COHORTS if analysis.stages.get(c) and analysis.stages[c].net_per_day is not None
        ) or "无可比阶段"
        out.append(
            f'<div class="bar-row" role="listitem" tabindex="0" data-tip data-name="{e(row["name"])}" '
            f'data-value="{e(COHORT_LABELS["mm1"])} 起各阶段" data-detail="{e(detail)}" '
            f'style="grid-template-columns:150px 1fr">'
            f'<div class="name"><a href="/product/{e(row["sku"])}">{e(row["name"])}</a>'
            f'<span class="sub">{e(row["sku"])}</span></div>'
            f'<div style="display:flex;gap:10px;align-items:flex-end">{"".join(cells)}</div></div>'
        )
    out.append("</div>")
    out.append('<div class="axis" style="grid-template-columns:150px 1fr"><span></span>'
               '<div style="display:flex;gap:10px"><span style="flex:1">月 1</span><span style="flex:1">月 2</span>'
               '<span style="flex:1">月 3</span></div></div>')
    return "".join(out)


def recommendation_summary_card(recommendations: list, summary: dict) -> str:
    rows = []
    for rec in recommendations:
        if rec.action in ("pending", "blocked") and rec.preserved_total == 0:
            continue
        rows.append(
            f"<tr><td><a href=\"/product/{e(rec.sku)}\">{e(rec.name)}</a>"
            f"<div class='muted small'>{e(rec.category)}</div></td>"
            f"<td>{action_chip(rec)}</td>"
            f"<td class='num'>{money(rec.suggested_amount)}</td>"
            f"<td class='num'>{money(rec.preserved_total)}</td></tr>")
    content = (
        "<table><thead><tr><th>商品</th><th>建议</th><th class='num'>建议追加</th><th class='num'>保留承诺</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        if rows else '<div class="empty">暂无内容资源建议。</div>'
    )
    return f"""<div class="card">
  <div class="card-head"><div><h2>资源建议摘要</h2>
  <div class="hint">建议只在未承诺预算内调整；已签排期不因峰值被挤占。</div></div></div>
  {content}
  <div class="pill-row">
    {''.join(f'<span class="chip neutral"><span class="dot"></span>{e(a["label"])} {a["count"]}</span>' for a in summary["actions"])}
  </div>
  <p class="small muted" style="margin-bottom:0"><a href="/resources">查看完整建议与约束 →</a></p>
</div>"""


def review_queue_card(queue: list) -> str:
    if not queue:
        return """<div class="card"><div class="card-head"><div><h2>复核队列</h2>
        <div class="hint">已批准决定的依据发生变化时会出现在这里。</div></div></div>
        <div class="banner good">当前没有需要复核的历史决定。</div></div>"""
    items = []
    for item in queue[:5]:
        items.append(
            f'<div class="diff"><div class="change"><strong>{e(item["sku"])}</strong> · {e(item.get("plan_id") or "无排期")}'
            f' · {e(item.get("decided_on") or "")}</div>'
            f'<div class="pair">{e("；".join(item["triggers"][:3]))}</div></div>'
        )
    return f"""<div class="card">
  <div class="card-head"><div><h2>复核队列</h2>
  <div class="hint">{len(queue)} 个已批准决定在数据修订后需要复核；原依据已固化，不会被覆盖。</div></div></div>
  {''.join(items)}
  <p class="small muted" style="margin-bottom:0"><a href="/decisions">进入决定台账 →</a></p>
</div>"""


def product_overview_table(analyses: dict) -> str:
    rows = []
    for sku, analysis in analyses.items():
        if analysis.status == OUT_OF_SCOPE:
            continue
        stage = analysis.stages.get("mm1")
        rows.append(
            f"<tr><td><a href=\"/product/{e(sku)}\">{e(analysis.product.name or sku)}</a>"
            f"<div class='muted small'>{e(sku)} · {e(analysis.product.category)}</div></td>"
            f"<td>{'新品' if analysis.is_new else '前代/常青'}</td>"
            f"<td>{status_chip(analysis.status)}</td>"
            f"<td class='small'>{e(analysis.lineage_label)}</td>"
            f"<td class='num'>{num(stage.net_per_day, 2) if stage else '—'}</td>"
            f"<td class='small'>{e(analysis.product.launch.isoformat() if analysis.product.launch else '—')}</td></tr>"
        )
    return ("<table><thead><tr><th>商品</th><th>类型</th><th>可比性</th><th>谱系</th>"
            "<th class='num'>月 1 净购买/可售日</th><th>上市日</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


# ---------------------------------------------------------------- 商品评审


def product_body(ctx: dict, ds: Dataset, result: dict, recommendations: dict, queue: list) -> str:
    sku = ctx.get("sku", "")
    analysis: ProductAnalysis = result["analyses_obj"].get(sku)
    if analysis is None:
        return '<h1>未找到商品</h1><p class="lede">该 SKU 不在当前商品谱系中。</p>'
    rec = recommendations.get(sku)
    cohort = ctx.get("stage") or "mm1"
    product = analysis.product
    stage = analysis.stages.get(cohort)
    body = [f'<p class="small muted" style="margin:24px 0 -12px"><a href="/">← 返回总览</a></p>',
            f"<h1>{e(product.name or sku)}</h1>",
            f'<p class="lede">{e(product.category)} · {e(sku)} · '
            f'{"新品" if analysis.is_new else "前代/常青商品"} · 上市 {e(product.launch.isoformat() if product.launch else "未提供")}</p>']
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>可比性判断</h2>
  <div class="hint">状态由谱系、上市确认、渠道可售与版本变更共同决定；待核优先。</div></div>
  <div>{status_chip(analysis.status)}</div></div>
  <div class="product-meta">
    <div>谱系<strong>{e(analysis.lineage_label)}</strong></div>
    <div>谱系依据<strong>{e(analysis.lineage_note)}</strong></div>
    <div>适用客群<strong>{e(analysis.audience_label or "未指定")}</strong></div>
    <div>版本<strong>{e(product.version or "—")}</strong></div>
  </div>
  {comparability_breakdown(analysis)}
</div>""")
    if rec is not None:
        body.append(recommendation_card(rec, ds))
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>上市阶段指标</h2>
  <div class="hint">净购买 = 订单 − 退款；可售日来自渠道上架、缺货与实际首卖日。缺数据留空。</div></div></div>
  <div class="stage-grid">{''.join(stage_card(analysis, c) for c in COHORTS)}</div>
</div>""")
    body.append(comparison_card(analysis))
    body.append(availability_card(ds, sku))
    body.append(timeline_card(ds, analysis))
    body.append(decision_form(analysis, ds, rec))
    body.append(product_history(ds, analysis, queue))
    return "".join(body)


def comparability_breakdown(analysis: ProductAnalysis) -> str:
    """把「为什么不能直接比较」拆成阻断项与注意项，而不是一股脑列原因。"""
    hard = {"lineage-pending", "lineage-missing", "launch-unconfirmed", "missing-launch",
            "availability-unconfirmed",
            "unverified-availability", "invalid-availability", "no-availability",
            "long-stockout", "audience-mismatch", "audience-unknown", "audience-unconfirmed",
            "formula-changed-unknown", "event-unconfirmed", "event-gap", "window-mismatch",
            "refund-exceeds-units", "missing-refunds", "missing-units", "missing-sales"}
    blockers_all = [r for r in analysis.reasons
                    if r.code in hard or (r.severity == "high" and r.code != "stockout")]
    seen = set()
    blockers = []
    for reason in blockers_all:
        if reason.code in seen:
            continue
        seen.add(reason.code)
        blockers.append(reason)
    advisories = [r for r in analysis.reasons if r not in blockers_all]
    if not blockers and not advisories:
        return '<div class="banner good">没有影响可比性的原因，可直接进入同品类、同阶段排名。</div>'
    out = []
    if blockers:
        out.append("<h3>阻断项 · 补齐前不排名</h3>" + reasons_html(blockers))
    if advisories:
        out.append("<h3 style='margin-top:14px'>注意项 · 可比但需说明</h3>" + reasons_html(advisories))
    return "".join(out)


def stage_card(analysis: ProductAnalysis, cohort: str) -> str:
    stage = analysis.stages.get(cohort)
    if stage is None:
        return ""
    status_chip_html = status_chip(stage.status)
    contribution = stage.contribution_per_spend
    unit_note = "元贡献/元资源" if contribution is not None else "件/元资源（缺毛利）"
    value = contribution if contribution is not None else stage.net_per_spend
    rows = [
        ("净购买", num(stage.net)),
        ("订单 / 退款", f"{num(stage.units)} / {num(stage.refunds)}"),
        ("共同可售", f"{stage.sellable_days} 自然日 · {stage.basis.channel_days} 渠道日"),
        ("退款率", pct(stage.refund_rate)),
        ("净购买/可售日", num(stage.net_per_day, 2)),
        ("每元资源贡献", f"{num(value, 2)} <span class='muted small'>{unit_note}</span>" if value is not None else "—"),
        ("复购代理", pct(stage.repeat_rate) if stage.repeat_rate is not None else "—"),
        ("名次", f"{stage.rank_net}/{stage.peer_count}" if stage.rank_net else "未入榜"),
    ]
    dl = "".join(
        f"<dt>{e(label)}</dt><dd>"
        + (value if label == "每元资源贡献" and value != "—" else e(value))
        + "</dd>"
        for label, value in rows
    )
    tooltip = (f"净购买 {num(stage.net)} · 可售 {stage.sellable_days} 天 · "
               f"每元资源贡献 {num(value, 2) if value is not None else '—'}")
    return f"""<div class="stage">
  <h4>{e(COHORT_LABELS[cohort])}</h4>
  <div class="window">{e(stage.window_start.isoformat())} → {e(stage.window_end.isoformat())}</div>
  <div style="margin-bottom:8px">{status_chip_html}</div>
  <dl data-tip tabindex="0" data-name="{e(analysis.product.name or analysis.sku)} · {e(COHORT_SHORT[cohort])}"
      data-value="{e(tooltip)}" data-detail="{e(stage.basis_note)}">{dl}</dl>
  <div class="small muted" style="margin-top:8px">{e(stage.basis_note)}</div>
  {f'<div class="small muted">{e(stage.repeat_note)}</div>' if stage.repeat_note and stage.cohort != "mm1" else ''}
  {reasons_html(stage.reasons, 4) if stage.reasons else ''}
</div>"""


def comparison_card(analysis: ProductAnalysis) -> str:
    if not analysis.comparisons:
        note = ("前代关系尚未确认，暂不做配对比较；确认后按共同可售日对齐。"
                if analysis.product.lineage_status == PENDING
                else "无已确认前代，不适用前代配对。")
        return f"""<div class="card"><div class="card-head"><div><h2>与前代的共同可售比较</h2>
        <div class="hint">按各自上市后同一阶段对齐，只取双方都可售的日期。</div></div></div>
        <div class="empty">{e(note)}</div></div>"""
    rows = []
    for comp in analysis.comparisons:
        comp_reasons = "；".join(r.text for r in comp.reasons) or comp.note
        rows.append(
            f"<tr><td>{e(COHORT_LABELS[comp.cohort])}</td>"
            f"<td>{status_chip(comp.status)}</td>"
            f"<td class='num'>{comp.common_days}</td>"
            f"<td class='num'>{num(comp.new_net)}</td>"
            f"<td class='num'>{num(comp.base_net)}</td>"
            f"<td class='num'>{num(comp.new_per_day, 2)}</td>"
            f"<td class='num'>{num(comp.base_per_day, 2)}</td>"
            f"<td class='num'>{num(comp.ratio, 2) if comp.ratio is not None else '—'}</td>"
            f"<td class='small'>{e(comp_reasons)}</td></tr>")
    return f"""<div class="card">
  <div class="card-head"><div><h2>与前代的共同可售比较</h2>
  <div class="hint">同一「上市后第 N 天」窗口，仅使用双方都可售的日期；不可比时不给倍率。</div></div></div>
  <table><thead><tr><th>阶段</th><th>可比性</th><th class='num'>共同可售日</th>
    <th class='num'>新品净购买</th><th class='num'>前代净购买</th>
    <th class='num'>新品/日</th><th class='num'>前代/日</th><th class='num'>倍率</th><th>原因</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
</div>"""


def availability_card(ds: Dataset, sku: str) -> str:
    from .domain import build_states

    states = build_states(ds, sku)
    if not states:
        return """<div class="card"><div class="card-head"><div><h2>渠道可售</h2></div></div>
        <div class="banner critical">没有渠道上架记录；该商品不按零销售参与排名。</div></div>"""
    rows = []
    for state in states:
        span = (f"{state.start.isoformat()} → {state.end.isoformat()}" if state.start and state.end else "—")
        flags = []
        if state.oos:
            flags.append("缺货 " + "、".join(f"{s.isoformat()}→{e_.isoformat()}" for s, e_ in state.oos))
        if state.late_listing_days:
            flags.append(f"晚开卖 {state.late_listing_days} 天")
        if state.unverified:
            flags.append("待核区间 " + "、".join(f"{s.isoformat()}→{e_.isoformat()}" for s, e_ in state.unverified))
        rows.append(
            f"<tr><td>{e(state.channel or '未标渠道')}</td><td class='small'>{e(span)}</td>"
            f"<td class='num'>{state.derived_days}</td>"
            f"<td class='num'>{state.declared_days if state.declared_days is not None else '—'}</td>"
            f"<td>{status_chip(PENDING if state.pending else COMPARABLE)}</td>"
            f"<td class='small'>{e(state.confirmed_by or '未署名')}</td>"
            f"<td class='small'>{e('；'.join(flags) or '正常在售')}</td>"
            f"<td class='small muted'>{e('；'.join(state.notes))}</td></tr>")
    return f"""<div class="card">
  <div class="card-head"><div><h2>渠道可售</h2>
  <div class="hint">同一渠道取最新录入记录；声明天数与推导不一致时按待核处理。</div></div></div>
  <table><thead><tr><th>渠道</th><th>上架区间</th><th class='num'>可售天数</th><th class='num'>声明天数</th>
    <th>状态</th><th>确认人</th><th>已确认的不可售</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</div>"""


def timeline_card(ds: Dataset, analysis: ProductAnalysis) -> str:
    items = []
    for event in ds.events:
        if event.sku != analysis.sku:
            continue
        kind = {"packaging": "包装", "formula": "配方"}.get(event.event_type, event.event_type)
        state = "已确认" if event.confirmed else "待核"
        items.append(f"<li><span class='when'>{e(event.effective_on.isoformat() if event.effective_on else '—')}</span>"
                     f"<span>{e(kind)}变更 {e(event.from_version)} → {e(event.to_version)}"
                     f"{f'，断档 {event.gap_days} 天' if event.gap_days else ''}</span>"
                     f"<span>{status_chip(COMPARABLE if event.confirmed else PENDING)}</span></li>")
    for plan in ds.plans_for(analysis.sku):
        decision = ds.approved_decision_for(plan.plan_id)
        label = "已签排期" + ("（有批准决定）" if decision else "")
        items.append(f"<li><span class='when'>{e(plan.start.isoformat() if plan.start else '—')}</span>"
                     f"<span>{e(label)} · {e(plan.plan_id)} · {e(CONTENT_STAGE_LABELS.get(plan.stage, plan.stage))} · "
                     f"{e(plan.channel)} · {money(plan.amount)}</span>"
                     f"<span class='chip neutral'><span class='dot'></span>{e(plan.status or '未标')}</span></li>")
    for revision in ds.lineage_revisions:
        if revision.sku != analysis.sku:
            continue
        items.append(f"<li><span class='when'>{e(revision.changed_on.isoformat() if revision.changed_on else '—')}</span>"
                     f"<span>谱系修订：{e(revision.from_predecessor or '未填')} → {e(revision.to_predecessor or '无')} · "
                     f"{e(revision.decided_by)}</span><span class='chip neutral'><span class='dot'></span>修订</span></li>")
    def _when(value):
        match = _re.search(r"<span class='when'>([^<]*)</span>", value)
        return match.group(1) if match else ""
    items.sort(key=_when)
    content = "".join(items) if items else '<li class="empty">暂无变更、排期或修订记录。</li>'
    return f"""<div class="card">
  <div class="card-head"><div><h2>变更与投入时间线</h2>
  <div class="hint">版本变更、内容排期与谱系修订；窗口内的变更会让阶段结论降级。</div></div></div>
  <ul class="timeline">{content}</ul>
</div>"""


def recommendation_card(rec, ds: Dataset) -> str:
    guard_bits = []
    for g in rec.guards:
        if g.effect == "preserve":
            tag = "<span class='chip good'><span class='dot'></span>锁定保留</span>"
        elif g.effect == "info":
            tag = "<span class='chip neutral'><span class='dot'></span>参考</span>"
        elif g.binding:
            tag = ("<span class='chip warning'><span class='dot'></span>压低额度</span>"
                   + (f"<span class='small muted'> → {money(g.amount_cap)}</span>" if g.amount_cap is not None else ""))
        else:
            tag = "<span class='chip neutral'><span class='dot'></span>未触顶</span>"
        guard_bits.append(f"<li><strong>{e(g.label)}</strong> {tag} {e(g.detail)}</li>")
    guard_items = "".join(guard_bits) or '<li class="muted">无额外约束记录。</li>'
    preserved_bits = []
    for p in rec.preserved_plans:
        bit = (f"<li><strong>{e(p['plan_id'])}</strong> · {e(p['channel'])} · "
               f"{e(CONTENT_STAGE_LABELS.get(p['stage'], p['stage']))} · {money(p['amount'])} · "
               f"{e(p.get('commitment') or p['status'])}")
        if p.get("note"):
            bit += f" — {e(p['note'])}"
        bit += "</li>"
        preserved_bits.append(bit)
    preserved_rows = "".join(preserved_bits) or '<li class="muted">该商品尚无已承诺内容排期。</li>'
    evidence_rows = [
        ("依据阶段", COHORT_LABELS.get(rec.evidence.get("stage") or "", "—")),
        ("净购买", num(rec.evidence.get("net"))),
        ("净购买/可售日", num(rec.evidence.get("net_per_day"), 2)),
        ("复购代理", pct(rec.evidence.get("repeat_rate"))),
        ("每元资源贡献", num(rec.evidence.get("contribution_per_spend"), 2)
         if rec.evidence.get("contribution_per_spend") is not None else "—"),
        ("同品类中位贡献", num(rec.evidence.get("peer_median_efficiency"), 2)),
    ]
    evidence_html = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in evidence_rows)
    rationale = "".join(f"<li>{e(text)}</li>" for text in rec.rationale)
    return f"""<div class="card rec {e(rec.action)}">
  <div class="card-head"><div><h2>资源建议 · {e(rec.label)}</h2>
  <div class="hint">{e(rec.headline)}</div></div>
  <div>{action_chip(rec)}</div></div>
  <div class="grid2">
    <div>
      <h3>判断依据</h3>
      <ul class="reasons">{rationale}</ul>
      <h3 style="margin-top:14px">约束条件（不触碰已承诺资源）</h3>
      <ul class="guards">{guard_items}</ul>
    </div>
    <div>
      <h3>建议额度</h3>
      <div class="big-number">{money(rec.suggested_amount)}</div>
      <div class="small muted">仅可从未承诺预算中划拨，需人工批准。</div>
      <dl class="stage" style="margin-top:14px;display:grid;grid-template-columns:1fr auto;gap:5px 10px;font-size:12px">
        {evidence_html}
      </dl>
      <div class="preserved"><strong>保留承诺 {money(rec.preserved_total)}</strong>
        <ul class="reasons" style="margin-top:6px">{preserved_rows}</ul>
      </div>
    </div>
  </div>
</div>"""


def decision_form(analysis: ProductAnalysis, ds: Dataset, rec) -> str:
    plans = ds.plans_for(analysis.sku)
    options = "".join(
        f'<option value="{e(p.plan_id)}">{e(p.plan_id)} · {e(p.channel)} · {money(p.amount)}</option>'
        for p in plans
    ) or '<option value="">（该商品无登记排期）</option>'
    audiences = "".join(
        f'<option value="{e(g.group_id)}"{" selected" if g.group_id == analysis.product.audience else ""}>{e(g.label)}</option>'
        for g in ds.audience_groups
    )
    return f"""<div class="card" id="decide">
  <div class="card-head"><div><h2>记录评审决定</h2>
  <div class="hint">提交时会把当前阶段、共同可售、排名与约束一并写入台账；之后修订不会改写这条依据。</div></div></div>
  <form method="post" action="/decisions/new">
    <input type="hidden" name="sku" value="{e(analysis.sku)}">
    <input type="hidden" name="return_to" value="/product/{e(analysis.sku)}">
    <div class="form-row">
      <label>内容排期<select name="plan_id">{options}</select></label>
      <label>决定<select name="status">
        <option value="approved">批准</option><option value="rejected">驳回</option></select></label>
      <label>评审人<input name="actor" placeholder="姓名（品类）" required></label>
      <label>客群<select name="audience"><option value="">不限定</option>{audiences}</select></label>
    </div>
    <div class="form-row">
      <label style="flex:1">说明<textarea name="note" placeholder="记录例外、承诺或复核要求"></textarea></label>
      <button class="primary" type="submit">写入决定台账</button>
    </div>
  </form>
</div>"""


def product_history(ds: Dataset, analysis: ProductAnalysis, queue: list) -> str:
    """商品页只展示与该商品相关的复核提示；完整台账在 /decisions。"""
    items = [q for q in queue if q.get("sku") == analysis.sku]
    if not items:
        return ""
    rows = []
    for item in items:
        rows.append(
            f"<div class='diff'><div class='change'><strong>{e(item.get('decision_id'))}</strong>"
            f" · 批准于 {e(item.get('decided_on') or '')} · {e(item.get('plan_id') or '无排期')}</div>"
            f"<div class='pair'>{e('；'.join(item.get('triggers', [])[:4]))}</div></div>")
    return f"""<div class="card">
  <div class="card-head"><div><h2>该商品的复核提示</h2>
  <div class="hint">已批准计划的依据出现变化；原计划保留，结论另记。</div></div></div>
  {''.join(rows)}
  <p class="small muted" style="margin-bottom:0"><a href="/decisions">查看完整复核队列 →</a></p>
</div>"""


# ---------------------------------------------------------------- 资源建议页


def resources_body(ctx: dict, ds: Dataset, recommendations: list, summary: dict) -> str:
    body = ['<h1>内容资源建议</h1>',
            '<p class="lede">建议只回答「是否追加、最多追加多少」，不自动改排期。'
            '已批准或已签约的内容一律保留；只有复购延续、资源贡献与库存/排期/竞争约束都成立时才给追加。'
            '单一早期峰值只会得到观察，不会挤掉已承诺资源。</p>']
    preserved = summary["total_preserved"]
    body.append(f"""<div class="kpis">
  <div class="tile"><div class="label">已承诺内容资源</div><div class="value">{money(preserved)}</div>
    <div class="sub">已批准 / 已签约，建议不削减</div></div>
  <div class="tile"><div class="label">本次建议追加</div><div class="value">{money(summary["total_suggested"])}</div>
    <div class="sub">仅在未承诺预算内</div></div>
  <div class="tile"><div class="label">建议复核</div><div class="value">{summary["counts"].get("hold", 0)}</div>
    <div class="sub">依据不足或约束叠加</div></div>
</div>""")
    groups = {}
    for rec in recommendations:
        groups.setdefault(rec.action, []).append(rec)
    order = ["advance", "hold", "early_watch", "pause", "pending", "blocked"]
    for action in order:
        recs = groups.get(action, [])
        if not recs:
            continue
        cards = "".join(recommendation_card(rec, ds) for rec in recs)
        body.append(f'<div class="section-label"><h2 style="margin:26px 0 10px">{e(REC_LABELS[action])} '
                    f'<span class="muted small">· {len(recs)} 个商品</span></h2></div>{cards}')
    body.append("""<div class="card"><div class="card-head"><div><h2>规则说明</h2></div></div>
  <ul class="reasons">
    <li><span class="sev low">规则</span><span>复购代理 &lt; 80% 或月 2/月 1 &lt; 60%：暂缓或仅观察。</span></li>
    <li><span class="sev low">规则</span><span>每元资源贡献低于同品类可比中位数：进入复核队列，不追加。</span></li>
    <li><span class="sev low">规则</span><span>前代库存 ≥ 6 周：按 50% 压缩；≥ 10 周：暂停追加。新品自身库存 &lt; 4 周：暂停。</span></li>
    <li><span class="sev low">规则</span><span>已签排期占用的档期不再叠加；同类新品 90 天内 ≥ 3 个：追加额度减半。</span></li>
    <li><span class="sev low">规则</span><span>待核或不可直接比较的商品：不排名、不追加，已承诺排期保留。</span></li>
  </ul></div>""")
    return "".join(body)


# ---------------------------------------------------------------- 修订对比页


def revisions_body(ctx: dict, ds: Dataset, diff: dict) -> str:
    before = ctx.get("before") or "2026-11-17"
    after = ctx.get("after") or ""
    body = ['<h1>修订前后：排名与建议怎么变</h1>',
            '<p class="lede">按录入日期回放到指定的数据截止版本，比较同一评审口径下的排名、可比性与资源建议。'
            '已经批准的投入计划不因重算而移动，只进入复核队列。</p>']
    body.append(f"""<form class="filters" method="get" action="/revisions">
  <label>修订前截止<input type="date" name="before" value="{e(before)}"></label>
  <label>修订后截止<input type="date" name="after" value="{e(after)}" placeholder="留空表示全部数据"></label>
  <span class="spacer"></span>
  <button class="secondary" type="submit">对比</button>
</form>""")
    changed = diff["has_changes"]
    body.append('<div class="banner%s">%s</div>' % (
        "" if changed else " good",
        "本次对比有 %d 项排名变化、%d 项建议变化。" % (len(diff["rank_changes"]), len(diff["recommendation_changes"]))
        if changed else "两个截止版本之间没有影响排名或建议的变化。"))
    body.append("""<div class="kpis">
  <div class="tile"><div class="label">修订前 · 建议追加</div><div class="value">%s</div>
    <div class="sub">承诺保留 %s</div></div>
  <div class="tile"><div class="label">修订后 · 建议追加</div><div class="value">%s</div>
    <div class="sub">承诺保留 %s</div></div>
</div>""" % (money(diff["summary_before"]["total_suggested"]), money(diff["summary_before"]["total_preserved"]),
             money(diff["summary_after"]["total_suggested"]), money(diff["summary_after"]["total_preserved"])))
    body.append('<div class="card"><div class="card-head"><div><h2>排名与可比性变化</h2>'
                '<div class="hint">按阶段与商品逐项比较；新增/移除表示该阶段是否参与排名。</div></div></div>')
    if diff["rank_changes"]:
        rows = "".join(rank_change_row(change) for change in diff["rank_changes"])
        body.append(f"<table class='pair-table'><thead><tr><th>商品</th><th>阶段</th><th>修订前</th>"
                    f"<th>修订后</th><th>变化</th></tr></thead><tbody>{rows}</tbody></table>")
    else:
        body.append('<div class="empty">排名没有变化。</div>')
    body.append("</div>")
    body.append('<div class="card"><div class="card-head"><div><h2>资源建议变化</h2>'
                '<div class="hint">已承诺金额不会因重算减少；变化只影响未承诺部分与复核提示。</div></div></div>')
    if diff["recommendation_changes"]:
        rows = "".join(rec_change_row(change) for change in diff["recommendation_changes"])
        body.append(f"<table class='pair-table'><thead><tr><th>商品</th><th>修订前</th><th>修订后</th><th>变化</th>"
                    f"</tr></thead><tbody>{rows}</tbody></table>")
    else:
        body.append('<div class="empty">资源建议没有变化。</div>')
    body.append("</div>")
    body.append(revision_log_card(ds))
    return "".join(body)


def _row_cells(row: dict | None) -> str:
    if row is None:
        return "<td class='muted'>—</td><td class='muted'>—</td><td class='muted'>—</td>"
    status = row["status"]
    return (f"<td>{status_chip(status)}</td>"
            f"<td class='num'>{num(row['net_per_day'], 2)}</td>"
            f"<td class='num'>{row['rank_net'] if row['rank_net'] is not None else '未入榜'}</td>")


def rank_change_row(change: dict) -> str:
    return (f"<tr><td><a href=\"/product/{e(change['sku'])}\">{e(change['sku'])}</a></td>"
            f"<td>{e(COHORT_LABELS.get(change['cohort'], change['cohort']))}</td>"
            f"<td class='before'>{status_chip(change['before']['status']) if change['before'] else '—'} "
            f"<span class='small muted'>{num(change['before']['net_per_day'], 2) if change['before'] else '—'} / "
            f"{change['before']['rank_net'] if change['before'] and change['before']['rank_net'] else '未入榜'}</span></td>"
            f"<td>{status_chip(change['after']['status']) if change['after'] else '—'} "
            f"<span class='small muted'>{num(change['after']['net_per_day'], 2) if change['after'] else '—'} / "
            f"{change['after']['rank_net'] if change['after'] and change['after']['rank_net'] else '未入榜'}</span></td>"
            f"<td class='small'>{e(change['change'])}</td></tr>")


def rec_change_row(change: dict) -> str:
    old, new = change["before"], change["after"]
    return (f"<tr><td><a href=\"/product/{e(change['sku'])}\">{e(change['sku'])}</a></td>"
            f"<td class='before'>{e(old['label']) if old else '—'}<div class='small muted'>"
            f"{money(old['suggested_amount']) if old else '—'} · 保留 {money(old['preserved_total']) if old else '—'}</div></td>"
            f"<td>{e(new['label']) if new else '—'}<div class='small muted'>"
            f"{money(new['suggested_amount']) if new else '—'} · 保留 {money(new['preserved_total']) if new else '—'}</div></td>"
            f"<td class='small'>{e(change['change'])}</td></tr>")


def revision_log_card(ds: Dataset) -> str:
    if not ds.lineage_revisions:
        return ""
    rows = []
    for revision in ds.lineage_revisions:
        rows.append(
            f"<tr><td>{e(revision.changed_on.isoformat() if revision.changed_on else '—')}</td>"
            f"<td>{e(revision.sku)}</td>"
            f"<td>{e(revision.from_predecessor or '未填')} → {e(revision.to_predecessor or '无')}</td>"
            f"<td>{e(revision.audience or '—')}</td><td>{e(revision.decided_by)}</td>"
            f"<td class='small'>{e(revision.note)}</td></tr>")
    return f"""<div class="card">
  <div class="card-head"><div><h2>已登记的谱系修订</h2>
  <div class="hint">修订本身也带日期：对比时只使用截止日期前已发生的修订。</div></div></div>
  <table><thead><tr><th>生效日</th><th>商品</th><th>前代变更</th><th>客群</th><th>确认人</th><th>说明</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
</div>"""


# ---------------------------------------------------------------- 决定与复核页


def decisions_body(ctx: dict, ds: Dataset, records: list, queue: list) -> str:
    body = ['<h1>决定台账与复核队列</h1>',
            '<p class="lede">批准时冻结的依据不会被后续修订覆盖。数据、谱系或建议发生变化时，'
            '相关决定进入复核队列，但原投入计划仍保留。</p>']
    if queue:
        cards = []
        for item in queue:
            basis = item.get("basis") or {}
            stages = basis.get("stages") or []
            detail = "".join(
                f"<li><span class='sev low'>{e(s['cohort'])}</span><span>"
                f"净购买 {num(s.get('net'))} · 可售 {s.get('sellable_days')} 天 · 名次 {s.get('rank_net') or '未入榜'}"
                f" · {e(COMPARABILITY_LABELS.get(s.get('status'), s.get('status', '')))}</span></li>"
                for s in stages
            ) or '<li class="muted">当时没有可用的阶段指标。</li>'
            rec = basis.get("recommendation") or {}
            triggers = "".join(f"<li><span class='sev high'>变化</span><span>{e(t)}</span></li>" for t in item.get("triggers", []))
            cards.append(f"""<div class="card rec warning">
  <div class="card-head"><div><h2>{e(item.get('decision_id'))} · {e(item.get('sku'))}</h2>
  <div class="hint">批准于 {e(item.get('decided_on') or '—')} · {e(item.get('actor') or '未署名')} · 排期 {e(item.get('plan_id') or '—')} ·
  金额 {money(item.get('plan_amount'))}</div></div>
  <div><span class="chip warning"><span class="dot"></span>需复核 · {e(SEVERITY_LABELS.get(item.get('severity'), item.get('severity')))}</span></div></div>
  <div class="grid2">
    <div><h3>当时依据（已固化）</h3><ul class="reasons">{detail}</ul>
      <p class="small muted">当时建议：{e(rec.get('label') or '—')} · 建议额度 {money(rec.get('suggested_amount'))}
      · 承诺保留 {money(rec.get('preserved_total'))}</p></div>
    <div><h3>触发复核的变化</h3><ul class="reasons">{triggers}</ul>
      <p class="small muted">原计划继续有效；复核结论另记一条决定，不覆盖本条。</p></div>
  </div>
</div>""")
        body.append("".join(cards))
    else:
        body.append('<div class="banner good">没有需要复核的已批准决定。</div>')
    body.append(decision_history_card(records))
    return "".join(body)


def decision_history_card(records: list) -> str:
    if not records:
        return """<div class="card"><div class="card-head"><div><h2>全部决定</h2></div></div>
        <div class="empty">台账里还没有决定。可在商品页提交评审决定。</div></div>"""
    rows = []
    for record in sorted(records, key=lambda r: r.get("decided_at") or "", reverse=True):
        basis = record.get("basis") or {}
        rec = basis.get("recommendation") or {}
        status = record.get("status")
        chip = (f'<span class="chip good"><span class="dot"></span>{e(DECISION_LABELS.get(status, status))}</span>'
                if status == DECISION_APPROVED else
                f'<span class="chip critical"><span class="dot"></span>{e(DECISION_LABELS.get(status, status))}</span>')
        rows.append(
            f"<tr><td>{e(record.get('decision_id'))}</td>"
            f"<td><a href=\"/product/{e(record.get('sku'))}\">{e(record.get('sku'))}</a></td>"
            f"<td>{e(record.get('plan_id') or '—')}</td><td>{chip}</td>"
            f"<td class='num'>{money(record.get('plan_amount'))}</td>"
            f"<td>{e(record.get('decided_on') or record.get('decided_at', '')[:10])}</td>"
            f"<td>{e(record.get('actor') or '—')}</td>"
            f"<td class='small'>{e(rec.get('label') or basis.get('lineage_label') or '—')}</td>"
            f"<td class='small'>{e(record.get('note') or '')}</td></tr>")
    return f"""<div class="card">
  <div class="card-head"><div><h2>全部决定</h2>
  <div class="hint">每条记录保存了决定时的指标与建议快照（basis 字段）。</div></div></div>
  <table><thead><tr><th>决定号</th><th>商品</th><th>排期</th><th>决定</th><th class='num'>金额</th>
    <th>日期</th><th>评审人</th><th>当时建议</th><th>说明</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
</div>"""


# ---------------------------------------------------------------- 口径说明


def data_quality_card(ds: Dataset) -> str:
    issues = []
    for record in ds.availability:
        if not record.confirmed_by:
            issues.append(f"availability.csv · {record.sku}/{record.channel} 缺少渠道确认人")
    for product in ds.products.values():
        if product.is_new and product.launch_status == "confirmed" and not product.launch_confirmed_by:
            issues.append(f"products.csv · {product.sku} 上市日期无确认人")
        if product.audience and not product.audience_confirmed_by:
            issues.append(f"products.csv · {product.sku} 适用客群无确认人")
    for event in ds.events:
        if not event.confirmed:
            issues.append(f"events.csv · {event.sku} {event.event_type} 变更未核实")
    for record in ds.performance:
        if record.units is not None and record.refunds is None:
            issues.append(f"performance.csv · {record.sku} {record.window} 缺少退款")
    if not issues:
        return ('<div class="card"><h2>数据完整性自查</h2>'
                '<div class="banner good">当前输入未发现缺少确认人或缺失字段。</div></div>')
    items = "".join(f"<li><span class='sev medium'>待核</span><span>{e(text)}</span></li>" for text in issues[:20])
    more = f"<p class='small muted'>另有 {len(issues) - 20} 条未列出。</p>" if len(issues) > 20 else ""
    return f"""<div class="card"><h2>数据完整性自查</h2>
  <p class="small muted">以下输入问题会直接让相关商品进入待核；它们不代表零销售。</p>
  <ul class="reasons">{items}</ul>{more}</div>"""


def method_body(ctx: dict, ds: Dataset) -> str:
    return f"""<h1>口径与边界</h1>
<p class="lede">这个工具不追求「排出一二三」，而是让每个名次和资源建议都能追溯到可售条件、数据来源和未确认项。</p>
<div class="card"><h2>1. 上市阶段</h2>
<ul class="reasons">
  <li><span class="sev low">口径</span><span>上市月 1 = 上市日起 0–29 天；月 2 = 30–59 天；月 3 = 60–89 天。跨商品比较一律按「上市后第 N 天」对齐，不按自然月。</span></li>
  <li><span class="sev low">依据</span><span>上市日期需品类经理确认；`launch_status=provisional` 时阶段结论进入待核。</span></li>
</ul></div>
<div class="card"><h2>2. 共同可售</h2>
<ul class="reasons">
  <li><span class="sev low">口径</span><span>窗口内可售日 = 渠道上架区间 − 缺货区间 − 未到实际首卖日 − 待核区间。多渠商品用「渠道可售日之和」作强度分母，另报自然日并集。</span></li>
  <li><span class="sev low">口径</span><span>声明天数与按日历推导不一致、只给缺货天数不给日期、库存状态未核实：该渠道按待核处理，不按满月在售。</span></li>
  <li><span class="sev low">规则</span><span>窗口可售覆盖 &lt; 50%：阶段待核；50–80%：可比但需注意；单窗口缺货 ≥ 30%：长期缺货，留待核实。</span></li>
</ul></div>
<div class="card"><h2>3. 净购买、复购与资源贡献</h2>
<ul class="reasons">
  <li><span class="sev low">口径</span><span>净购买 = 订单 − 退款。退款缺失不按 0；退款超过订单或跨期异常时待核。</span></li>
  <li><span class="sev low">口径</span><span>复购信号 = 月 2 净购买 ÷ 月 1 净购买。这是阶段延续代理，不是用户级复购；相邻阶段任一可售不足 20 天则不计算。</span></li>
  <li><span class="sev low">口径</span><span>每单位资源贡献优先 = 净购买 × 单位毛利 ÷ 内容投入；缺单位毛利时退回「净购买/元资源」并单独标注，不能与毛利口径混排。</span></li>
</ul></div>
<div class="card"><h2>4. 可比性与排名</h2>
<ul class="reasons">
  <li><span class="sev low">规则</span><span>只在同品类、同阶段、同客群内排序。待核与不可直接比较的商品不占名次，但仍在页面显示原因。</span></li>
  <li><span class="sev low">规则</span><span>谱系待核、上市未确认、渠道可售未核实、销售窗口与阶段不符：任一成立即待核。</span></li>
  <li><span class="sev low">规则</span><span>与前代的比较使用共同可售日：两品各自上市后同一偏移日都可在售的日期；任一渠道待核则不做配对比较。</span></li>
</ul></div>
<div class="card"><h2>5. 资源建议</h2>
<ul class="reasons">
  <li><span class="sev low">规则</span><span>已批准 / 已签约排期永不减、永不挪用；建议额度只来自未承诺预算。</span></li>
  <li><span class="sev low">规则</span><span>只凭月 1 峰值的商品不追加；月 2 相对月 1 &lt; 60% 视为单峰回落。</span></li>
  <li><span class="sev low">规则</span><span>前代库存高、已签排期密集、同类新品 90 天内 ≥ 3 个、品类预算不足都会压缩额度。</span></li>
</ul></div>
{data_quality_card(ds)}
<div class="card"><h2>6. 修订与复核</h2>
<ul class="reasons">
  <li><span class="sev low">口径</span><span>渠道记录按 `added_on`、库存按 `as_of`、谱系修订按 `changed_on` 回放；同一渠道取最新一条。</span></li>
  <li><span class="sev low">规则</span><span>批准决定固化当时的整份证据快照；之后任何指标、谱系或建议变化都会进入复核队列，原计划保留。</span></li>
  <li><span class="sev low">数据</span><span>当前输入文件：{e('、'.join(sorted(ds.sources)))}。所有值为合成样例。</span></li>
</ul></div>"""


# ---------------------------------------------------------------- 商品列表


def products_body(ctx: dict, view: dict) -> str:
    analyses = view["analyses"]
    result = view["result"]
    cohort = ctx.get("stage") or "mm1"
    body = ['<h1>商品评审</h1>',
            '<p class="lede">按商品查看阶段指标、共同可售依据与不可比原因。前代也列出，便于查看承接关系与配对比较。</p>']
    rows = []
    for sku, analysis in analyses.items():
        if analysis.status == OUT_OF_SCOPE:
            continue
        stage = analysis.stages.get(cohort)
        rec = view["recommendation_map"].get(sku)
        reasons = "；".join(r.text for r in analysis.reasons[:2])
        action_cell = action_chip(rec) if rec else "<span class='muted'>—</span>"
        rows.append(
            f"<tr><td><a href=\"/product/{e(sku)}?stage={e(cohort)}\">{e(analysis.product.name or sku)}</a>"
            f"<div class='muted small'>{e(sku)}</div></td>"
            f"<td>{'新品' if analysis.is_new else '前代'}</td>"
            f"<td>{status_chip(analysis.status)}</td>"
            f"<td class='small'>{e(analysis.lineage_label)}</td>"
            f"<td class='num'>{num(stage.net) if stage else '—'}</td>"
            f"<td class='num'>{num(stage.net_per_day, 2) if stage else '—'}</td>"
            f"<td class='num'>{pct(stage.repeat_rate if stage and stage.repeat_rate is not None else (analysis.stages.get('mm2').repeat_rate if analysis.stages.get('mm2') else None))}</td>"
            f"<td>{action_cell}</td>"
            f"<td class='small'>{e(reasons or '无附加原因')}</td></tr>")
    body.append(f"""<div class="card">
  <div class="card-head"><div><h2>{e(COHORT_LABELS[cohort])} · 全部商品</h2>
  <div class="hint">待核商品保留在列表中，但不占名次。</div></div></div>
  <table><thead><tr><th>商品</th><th>类型</th><th>可比性</th><th>谱系</th>
    <th class='num'>净购买</th><th class='num'>净购买/可售日</th><th class='num'>复购代理</th>
    <th>资源建议</th><th>主要不可比原因</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</div>""")
    return "".join(body)
