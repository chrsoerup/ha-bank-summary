"""Visual monthly report: stat tiles, inline-SVG charts and tables, rendered live from the DB.

Server-side SVG keeps it dependency-free (no JS bundle, no CDN) so it renders inside HA's
Ingress iframe and on a phone alike. Hover tooltips are native SVG `<title>`s; every charted
value is also in a table, so nothing depends on hovering. Colours are the dataviz skill's
validated categorical slots 1–2 (light + dark), checked with its palette validator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jinja2 import Environment
from markupsafe import Markup, escape

from .report import aggregate_month, month_context, prev_month
from .store import Store

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
TREND_MONTHS = 12


def _fmt(value: Decimal | float) -> str:
    return f"{value:,.2f}"


def _compact(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _nice_ticks(max_value: float, target: int = 4) -> list[float]:
    """0 plus clean round ticks (1/2/2.5/5 × 10ⁿ) covering max_value."""
    if max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    count = math.ceil(max_value / step)
    return [i * step for i in range(count + 1)]


def _rounded_right_bar(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Horizontal bar: square at the baseline (left), 4px rounded data-end (right)."""
    r = min(r, w, h / 2)
    return (
        f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z"
    )


def _rounded_top_column(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Vertical column: square at the baseline (bottom), 4px rounded data-end (top)."""
    r = min(r, h, w / 2)
    return (
        f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z"
    )


@dataclass
class Tile:
    label: str
    value: str  # whole units; the currency is rendered as a smaller suffix
    delta: str  # e.g. "▲ 12% vs. August", or "" when there is nothing to compare against
    tone: str  # "good" | "bad" | "neutral" — direction × whether up is good


def _tile(
    label: str, current: Decimal, previous: Decimal, up_is_good: bool, prev_name: str, cur: str
) -> Tile:
    value = f"{current:,.0f}"
    if previous == 0:
        return Tile(label, value, "", "neutral")
    change = current - previous
    pct = change / abs(previous) * 100
    if abs(pct) < 0.5:
        return Tile(label, value, f"≈ same as {prev_name}", "neutral")
    arrow = "▲" if change > 0 else "▼"
    good = (change > 0) == up_is_good
    return Tile(label, value, f"{arrow} {abs(pct):.0f}% vs. {prev_name}", "good" if good else "bad")


def _category_chart(
    spending: list[tuple[str, Decimal, Decimal]], currency: str
) -> Markup:
    """Horizontal bars: this month's spend per category, with last month as a tick marker."""
    if not spending:
        return Markup("")
    label_w, value_w, width, row_h, bar_h = 170, 130, 680, 34, 18
    plot_w = width - label_w - value_w
    max_value = max(max(cur, prev) for _, cur, prev in spending) or Decimal(1)
    height = row_h * len(spending) + 8
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" '
        f'aria-label="Spending by category, this month vs. previous month">'
    ]
    for i, (name, cur, prev) in enumerate(spending):
        y = i * row_h + 4
        bar_y = y + (row_h - bar_h) / 2
        w = float(cur / max_value) * plot_w
        prev_x = label_w + float(prev / max_value) * plot_w
        tip = f"{name}: {_fmt(cur)} {currency} (previous month {_fmt(prev)} {currency})"
        parts.append(f'<g class="mark"><title>{escape(tip)}</title>')
        parts.append(f'<rect x="0" y="{y}" width="{width}" height="{row_h}" fill="transparent"/>')
        parts.append(
            f'<text x="{label_w - 12}" y="{y + row_h / 2}" class="label" text-anchor="end" '
            f'dominant-baseline="middle">{escape(name)}</text>'
        )
        if w > 0:
            parts.append(
                f'<path d="{_rounded_right_bar(label_w, bar_y, max(w, 2), bar_h)}" '
                f'class="s1"/>'
            )
        if prev > 0:
            parts.append(
                f'<rect x="{prev_x - 1:.1f}" y="{bar_y - 4}" width="2" height="{bar_h + 8}" '
                f'rx="1" class="marker"/>'
            )
        parts.append(
            f'<text x="{label_w + max(w, float(prev / max_value) * plot_w) + 10:.1f}" '
            f'y="{y + row_h / 2}" class="value" dominant-baseline="middle">'
            f"{_fmt(cur)}</text></g>"
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def _trend_chart(points: list[dict[str, Any]], currency: str) -> Markup:
    """Grouped columns per month: income (slot 1) and expenditure (slot 2), one shared axis."""
    if not points:
        return Markup("")
    width, height = 680, 260
    left, right, top, bottom = 52, 8, 12, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    max_value = max(max(p["income"], p["expenditure"]) for p in points)
    ticks = _nice_ticks(max_value)
    y_max = ticks[-1]

    def y_of(v: float) -> float:
        return top + plot_h - (v / y_max) * plot_h

    band = plot_w / len(points)
    col_w = min(16.0, (band - 10) / 2)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" class="chart" '
        f'aria-label="Income and expenditure per month">'
    ]
    for t in ticks:
        y = y_of(t)
        parts.append(
            f'<line x1="{left}" x2="{width - right}" y1="{y:.1f}" y2="{y:.1f}" '
            f'class="{"axis" if t == 0 else "grid"}"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y:.1f}" class="tick" text-anchor="end" '
            f'dominant-baseline="middle">{_compact(t)}</text>'
        )
    for i, p in enumerate(points):
        cx = left + band * i + band / 2
        tip = (
            f"{p['label_long']}: income {_fmt(p['income'])} {currency}, "
            f"expenditure {_fmt(p['expenditure'])} {currency}, net {_fmt(p['net'])} {currency}"
        )
        parts.append(f'<g class="mark{" current" if p["current"] else ""}">')
        parts.append(f"<title>{escape(tip)}</title>")
        parts.append(
            f'<rect x="{cx - band / 2:.1f}" y="{top}" width="{band:.1f}" height="{plot_h}" '
            f'fill="transparent"/>'
        )
        for value, cls, x in (
            (p["income"], "s1", cx - col_w - 1),  # 2px surface gap between the pair
            (p["expenditure"], "s2", cx + 1),
        ):
            h = plot_h - (y_of(value) - top)
            if h > 0:
                parts.append(
                    f'<path d="{_rounded_top_column(x, y_of(value), col_w, max(h, 2))}" '
                    f'class="{cls}"/>'
                )
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 10}" class="tick{" strong" if p["current"] else ""}"'
            f' text-anchor="middle">{escape(p["label"])}</text></g>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def _trend_points(
    store: Store, year: int, month: int, account_uid: str | None
) -> list[dict[str, Any]]:
    months = []
    y, m = year, month
    for _ in range(TREND_MONTHS):
        months.append((y, m))
        y, m = prev_month(y, m)
    points: list[dict[str, Any]] = []
    for y, m in reversed(months):
        agg = aggregate_month(store, y, m, account_uid=account_uid)
        if not agg.rows and not points:
            continue  # skip leading months from before the account was linked
        points.append(
            {
                "label": MONTH_NAMES[m - 1][:3] + (f" {y % 100:02d}" if m == 1 else ""),
                "label_long": f"{MONTH_NAMES[m - 1]} {y}",
                "income": float(agg.income),
                "expenditure": float(agg.expenditure),
                "net": float(agg.net),
                "current": (y, m) == (year, month),
            }
        )
    return points


TEMPLATE = Environment(autoescape=True).from_string(
    """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bank summary — {{ month_name }} {{ year }}</title>
<style>
.viz-root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --border: rgba(11,11,11,0.10);
  --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7;
  --series-1: #2a78d6; --series-2: #eb6834;
  --good: #006300; --bad: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --border: rgba(255,255,255,0.10);
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --axis: #383835;
    --series-1: #3987e5; --series-2: #d95926;
    --good: #0ca30c; --bad: #e66767;
  }
}
* { box-sizing: border-box; }
body { margin: 0; }
.viz-root {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page); color: var(--text-primary);
  padding: 24px; min-height: 100vh; line-height: 1.4;
}
.wrap { max-width: 760px; margin: 0 auto; }
a { color: var(--series-1); }
header { display: flex; justify-content: space-between; align-items: baseline;
  flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
header h1 { font-size: 22px; font-weight: 600; margin: 0; }
header nav { font-size: 14px; color: var(--text-secondary); }
header nav a { margin-left: 12px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px;
  padding: 20px; margin-bottom: 16px; }
.card h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.card .sub { font-size: 13px; color: var(--text-secondary); margin: 0 0 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px;
  margin-bottom: 16px; }
.tiles .card { margin: 0; }
.tile-label { font-size: 13px; color: var(--text-secondary); }
.tile-value { font-size: 24px; font-weight: 600; margin: 4px 0; white-space: nowrap; }
.tile.hero .tile-value { font-size: 30px; }
.tile-value .unit { font-size: 14px; font-weight: 400; color: var(--text-secondary); }
.delta { font-size: 13px; }
.delta.good { color: var(--good); } .delta.bad { color: var(--bad); }
.delta.neutral { color: var(--text-muted); }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--text-secondary);
  margin-bottom: 8px; }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin-right: 6px; vertical-align: -1px; background: var(--series-1); }
.legend .k2::before { background: var(--series-2); }
.legend .tick-key::before { width: 2px; height: 14px; border-radius: 1px;
  background: var(--text-secondary); vertical-align: -3px; }
.chart { width: 100%; height: auto; display: block; overflow: visible; }
.chart .s1 { fill: var(--series-1); } .chart .s2 { fill: var(--series-2); }
.chart .marker { fill: var(--text-secondary); stroke: var(--surface-1); stroke-width: 2;
  paint-order: stroke; }
.chart .grid { stroke: var(--grid); stroke-width: 1; }
.chart .axis { stroke: var(--axis); stroke-width: 1; }
.chart text { font-family: inherit; }
.chart .label { font-size: 13px; fill: var(--text-primary); }
.chart .value { font-size: 12px; fill: var(--text-secondary); font-variant-numeric: tabular-nums; }
.chart .tick { font-size: 11px; fill: var(--text-muted); font-variant-numeric: tabular-nums; }
.chart .tick.strong { fill: var(--text-primary); font-weight: 600; }
.chart .mark { transition: opacity 120ms; }
.chart:hover .mark { opacity: 0.45; }
.chart:hover .mark:hover { opacity: 1; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: var(--text-secondary);
  border-bottom: 1px solid var(--axis); padding: 6px 8px; }
td { border-bottom: 1px solid var(--grid); padding: 6px 8px; vertical-align: top; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
details { margin-top: 12px; font-size: 13px; }
summary { cursor: pointer; color: var(--text-secondary); }
pre { background: var(--page); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px; overflow-x: auto; font-size: 12px; margin: 4px 0 10px; }
footer { font-size: 12px; color: var(--text-muted); margin-top: 8px; }
.empty { color: var(--text-muted); font-size: 13px; }
@media (max-width: 520px) {
  .viz-root { padding: 12px; } .card { padding: 14px; }
  .chart .label { font-size: 22px; } .chart .value { font-size: 20px; }
  .chart .tick { font-size: 19px; }
}
</style>
</head>
<body>
<div class="viz-root"><div class="wrap">
<header>
  <h1>{{ month_name }} {{ year }}</h1>
  <nav>
    {% if prev_link %}<a href="{{ prev_link }}">← {{ prev_name }}</a>{% endif %}
    {% if next_link %}<a href="{{ next_link }}">{{ next_name }} →</a>{% endif %}
    <a href="../">All reports</a>
  </nav>
</header>

<div class="tiles">
{% for t in tiles %}
  <div class="card tile{% if loop.last %} hero{% endif %}">
    <div class="tile-label">{{ t.label }}</div>
    <div class="tile-value">{{ t.value }} <span class="unit">{{ currency }}</span></div>
    <div class="delta {{ t.tone }}">{{ t.delta or "&nbsp;"|safe }}</div>
  </div>
{% endfor %}
</div>

<section class="card">
  <h2>Spending by category</h2>
  <p class="sub">{{ currency }}, largest first. Hover a bar for details.</p>
  {% if spending %}
  <div class="legend">
    <span>{{ month_name }}</span><span class="tick-key">{{ prev_name }}</span>
  </div>
  {{ category_chart }}
  <details><summary>Show as table</summary>
  <table>
    <tr><th>Category</th><th class="num">Amount</th><th class="num">Share</th>
      <th class="num">Δ vs. {{ prev_name }}</th><th class="num">Δ vs. 3-mo avg</th></tr>
    {% for c in categories %}
    <tr><td>{{ c.category }}</td><td class="num">{{ fmt(c.amount) }}</td>
      <td class="num">{{ c.share }}</td><td class="num">{{ c.delta_prev }}</td>
      <td class="num">{{ c.delta_avg }}</td></tr>
    {% endfor %}
  </table>
  </details>
  {% else %}<p class="empty">No spending this month.</p>{% endif %}
</section>

<section class="card">
  <h2>Income and expenditure</h2>
  <p class="sub">{{ currency }} per month, last {{ trend|length }} month(s).</p>
  <div class="legend"><span>Income</span><span class="k2">Expenditure</span></div>
  {{ trend_chart }}
  <details><summary>Show as table</summary>
  <table>
    <tr><th>Month</th><th class="num">Income</th><th class="num">Expenditure</th>
      <th class="num">Net</th></tr>
    {% for p in trend|reverse %}
    <tr><td>{{ p.label_long }}</td><td class="num">{{ fmt(p.income) }}</td>
      <td class="num">{{ fmt(p.expenditure) }}</td><td class="num">{{ fmt(p.net) }}</td></tr>
    {% endfor %}
  </table>
  </details>
</section>

<section class="card">
  <h2>Largest expenditures</h2>
  {% if top_expenditures %}
  <table>
    <tr><th>Date</th><th>Counterparty</th><th class="num">Amount</th></tr>
    {% for t in top_expenditures %}
    <tr><td class="num" style="text-align:left">{{ t.booking_date }}</td>
      <td>{{ t.counterparty_name or t.remittance_text or "—" }}</td>
      <td class="num">{{ fmt(-t.amount) }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<p class="empty">None.</p>{% endif %}
</section>

<section class="card">
  <h2>Uncategorised ({{ uncategorised|length }})</h2>
  {% if uncategorised %}
  <p class="sub">Add a rule to <code>rules.yaml</code> to categorise similar transactions.</p>
  <table>
    <tr><th>Date</th><th>Counterparty</th><th class="num">Amount</th></tr>
    {% for u in uncategorised %}
    <tr><td>{{ u.booking_date }}</td><td>{{ u.counterparty_name or u.remittance_text or "?" }}</td>
      <td class="num">{{ fmt(u.amount) }}</td></tr>
    {% endfor %}
  </table>
  <details><summary>Suggested rule stubs</summary>
  {% for u in uncategorised %}<pre>{{ u.stub }}</pre>{% endfor %}
  </details>
  {% else %}<p class="empty">None — nice.</p>{% endif %}
</section>

<footer>
  Synced {{ synced_at or "never" }} · Consent expires {{ consent_expires or "unknown" }} ·
  <a href="../reports/{{ period }}.md">Markdown version</a>
</footer>
</div></div>
</body>
</html>
"""
)


def render_month_html(
    store: Store,
    year: int,
    month: int,
    currency: str = "DKK",
    account_uid: str | None = None,
    has_month: Any = None,
) -> str:
    """`has_month(year, month) -> bool` decides whether prev/next links are shown."""
    ctx = month_context(store, year, month, currency=currency, account_uid=account_uid)
    py, pm = prev_month(year, month)
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    prev_name, next_name = MONTH_NAMES[pm - 1], MONTH_NAMES[nm - 1]

    tiles = [
        _tile("Income", ctx["income"], ctx["prev_income"], True, prev_name, currency),
        _tile("Expenditure", ctx["expenditure"], ctx["prev_expenditure"], False, prev_name,
              currency),
        _tile("Net", ctx["net"], ctx["prev_net"], True, prev_name, currency),
    ]

    previous = ctx["previous_categories"]
    spending = sorted(
        (
            (c.category, -c.amount, max(-previous.get(c.category, Decimal(0)), Decimal(0)))
            for c in ctx["categories"]
            if c.amount < 0
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    trend = _trend_points(store, year, month, account_uid)

    return TEMPLATE.render(
        **ctx,
        month_name=MONTH_NAMES[month - 1],
        prev_name=prev_name,
        next_name=next_name,
        prev_link=f"{py:04d}-{pm:02d}" if has_month and has_month(py, pm) else None,
        next_link=f"{ny:04d}-{nm:02d}" if has_month and has_month(ny, nm) else None,
        tiles=tiles,
        spending=spending,
        category_chart=_category_chart(spending, currency),
        trend=trend,
        trend_chart=_trend_chart(trend, currency),
        fmt=_fmt,
    )
