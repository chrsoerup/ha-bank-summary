"""Categorise page: uncategorised transactions grouped by merchant, one category per group.

Saving a group appends a `text_regex` rule to the user's rules.yaml (see
`categorize.append_rule`), so the choice applies to past and future transactions alike and
stays editable as a plain rule. This is also the only practical way to edit rules on HA OS
installs where the add-on's config folder isn't reachable from the File editor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from jinja2 import Environment

from .categorize import CategorizableTransaction, merchant_key
from .html_report import BASE_CSS
from .store import Store

MAX_GROUPS = 40


@dataclass
class MerchantGroup:
    key: str
    count: int = 0
    total: Decimal = Decimal(0)
    examples: list[str] = field(default_factory=list)
    last_date: str = ""


def group_uncategorised(store: Store, account_uid: str | None = None) -> list[MerchantGroup]:
    """Groups ordered by absolute amount — the first few usually cover most of the money."""
    groups: dict[str, MerchantGroup] = {}
    for row in store.uncategorised_rows(account_uid):
        txn = CategorizableTransaction.from_row(row)
        key = merchant_key(txn)
        group = groups.setdefault(key, MerchantGroup(key=key))
        group.count += 1
        group.total += txn.amount
        raw = (txn.counterparty_name or txn.remittance_text or "?").strip()
        if raw not in group.examples and len(group.examples) < 3:
            group.examples.append(raw)
        group.last_date = max(group.last_date, row["booking_date"] or "")
    return sorted(groups.values(), key=lambda g: abs(g.total), reverse=True)


TEMPLATE = Environment(autoescape=True).from_string(
    """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Categorise — Bank Summary</title>
<style>{{ css }}
.group td { vertical-align: middle; }
.examples { font-size: 12px; color: var(--text-muted); }
input[type=text] { font: inherit; font-size: 13px; width: 100%; padding: 5px 7px;
  border: 1px solid var(--axis); border-radius: 6px; background: var(--surface-1);
  color: var(--text-primary); }
button { font: inherit; font-size: 14px; padding: 7px 14px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--series-1); background: var(--series-1); color: #fff; }
.notice { background: var(--surface-1); border: 1px solid var(--border); border-left: 3px solid
  var(--good); border-radius: 6px; padding: 10px 12px; margin-bottom: 16px; font-size: 14px; }
.actions { display: flex; justify-content: space-between; align-items: center; gap: 12px;
  margin-top: 14px; font-size: 13px; color: var(--text-secondary); }
@media (max-width: 520px) {
  .group td:nth-child(2), .group th:nth-child(2) { display: none; }
}
</style>
</head>
<body>
<div class="viz-root"><div class="wrap">
<header>
  <h1>Categorise</h1>
  <nav><a href="./">← Bank Summary</a></nav>
</header>

{% if saved is not none %}
<div class="notice">
  {% if saved %}Saved {{ saved }} rule(s); {{ recategorised }} transaction(s) categorised.
  {% else %}Nothing to save — fill in a category for at least one merchant.{% endif %}
</div>
{% endif %}

<section class="card">
  {% if groups %}
  <h2>{{ total_count }} uncategorised transaction(s) from {{ groups_total }} merchant(s)</h2>
  <p class="sub">Largest amounts first. Pick or type a category for any merchant and save;
    leave the rest blank. <strong>Matches</strong> is the text a rule looks for — shorten it
    (e.g. <code>NETTO KBH</code> → <code>NETTO</code>) to catch every branch.</p>
  <form method="post" action="categorise">
  <table>
    <tr class="group"><th>Merchant</th><th class="num">Count</th><th class="num">Amount</th>
      <th style="width:28%">Matches</th><th style="width:28%">Category</th></tr>
    {% for g in groups %}
    <tr class="group">
      <td>{{ g.key }}<div class="examples">{{ g.examples|join(" · ") }}</div></td>
      <td class="num">{{ g.count }}</td>
      <td class="num">{{ fmt(g.total) }}</td>
      <td><input type="text" name="match_{{ loop.index0 }}" value="{{ g.key }}"
        aria-label="Match text for {{ g.key }}"></td>
      <td><input type="text" name="category_{{ loop.index0 }}" list="categories"
        placeholder="Category…" aria-label="Category for {{ g.key }}"></td>
    </tr>
    {% endfor %}
  </table>
  <datalist id="categories">
    {% for c in categories %}<option value="{{ c }}">{% endfor %}
  </datalist>
  <div class="actions">
    <span>{% if groups_total > groups|length %}Showing the top {{ groups|length }}; the rest
      appear here as you categorise these.{% endif %}</span>
    <button type="submit">Save rules</button>
  </div>
  </form>
  {% else %}
  <h2>Nothing uncategorised</h2>
  <p class="sub">Every transaction matches a rule. New merchants will show up here after a sync.</p>
  {% endif %}
</section>
<footer>Rules are appended to <code>rules.yaml</code> in the add-on's config folder and take
  precedence over the built-in rule set.</footer>
</div></div>
</body>
</html>
"""
)


def render_categorise_page(
    store: Store,
    categories: list[str],
    account_uid: str | None = None,
    saved: int | None = None,
    recategorised: int = 0,
) -> str:
    groups = group_uncategorised(store, account_uid)
    return TEMPLATE.render(
        css=BASE_CSS,
        groups=groups[:MAX_GROUPS],
        groups_total=len(groups),
        total_count=sum(g.count for g in groups),
        categories=categories,
        saved=saved,
        recategorised=recategorised,
        fmt=lambda v: f"{v:,.2f}",
    )
