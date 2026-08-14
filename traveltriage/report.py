"""Static docs page: rule alerts side by side with the scorer's verdict and reasons."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import scoring

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Impossible-travel alerts: rule vs history scorer</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1b1f24; }}
 h1 {{ font-size: 1.4rem; }}
 .controls {{ margin: 1rem 0; display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
 th, td {{ border-bottom: 1px solid #d0d7de; padding: 0.35rem 0.5rem; text-align: left; vertical-align: top; }}
 th {{ background: #f6f8fa; cursor: pointer; position: sticky; top: 0; }}
 .kept {{ color: #b42318; font-weight: 600; }} .closed {{ color: #1a7f37; font-weight: 600; }}
 .ato {{ background: #fff4e5; }}
 ul {{ margin: 0; padding-left: 1rem; }}
 .muted {{ color: #57606a; }}
</style>
</head>
<body>
<h1>Impossible-travel alerts: geo-velocity rule vs per-user history scorer</h1>
<p class="muted">Each row is a sign-in in the test window where the geo-velocity rule fired
(&ge;{dist:.0f} km, &gt;{speed:.0f} km/h, within {win:.0f} min). The scorer keeps an alert when its
risk score is above the threshold set for {budget:g} alerts per 10k sign-ins on the earlier calibration
window, and auto-closes it otherwise. Reason codes come from the user's own ASN, prefix, country and
device history and population priors. Highlighted rows are labelled account takeovers.
Data: RBA dataset (synthesized from a real SSO service).</p>
<div class="controls">
 <label>Verdict <select id="verdict"><option value="">all</option><option>kept</option><option>auto-closed</option></select></label>
 <label>Label <select id="label"><option value="">all</option><option>takeover</option><option>benign</option></select></label>
 <label>Search <input id="q" placeholder="ASN, country, reason..."></label>
 <span id="count" class="muted"></span>
</div>
<table id="t">
<thead><tr>
 <th data-k="ts">Time (UTC)</th><th data-k="user">User</th><th data-k="route">Rule hit</th>
 <th data-k="asn">ASN</th><th data-k="score">Score</th><th data-k="verdict">Verdict</th>
 <th data-k="label">Label</th><th>Reasons</th>
</tr></thead>
<tbody></tbody>
</table>
<script>
const ROWS = {rows};
let sortKey = "score", sortDir = -1;
const esc = s => String(s).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
function render() {{
  const v = document.getElementById("verdict").value, l = document.getElementById("label").value;
  const q = document.getElementById("q").value.toLowerCase();
  const rows = ROWS.filter(r => (!v || r.verdict === v) && (!l || r.label === l) &&
    (!q || JSON.stringify(r).toLowerCase().includes(q)));
  rows.sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
  document.querySelector("#t tbody").innerHTML = rows.map(r => `<tr class="${{r.label === "takeover" ? "ato" : ""}}">
    <td>${{esc(r.ts)}}</td><td>${{esc(r.user)}}</td><td>${{esc(r.route)}}</td><td>AS${{esc(r.asn)}}</td>
    <td>${{r.score.toFixed(2)}}</td><td class="${{r.verdict === "kept" ? "kept" : "closed"}}">${{r.verdict}}</td>
    <td>${{r.label}}</td><td><ul>${{r.reasons.map(x => `<li>${{esc(x)}}</li>`).join("")}}</ul></td></tr>`).join("");
  document.getElementById("count").textContent = `${{rows.length}} of ${{ROWS.length}} alerts`;
}}
document.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => {{
  const k = th.dataset.k; sortDir = sortKey === k ? -sortDir : -1; sortKey = k; render(); }});
["verdict", "label", "q"].forEach(id => document.getElementById(id).oninput = render);
render();
</script>
</body>
</html>
"""


def alert_rows(df: pd.DataFrame, terms: pd.DataFrame, scores: np.ndarray, model: dict,
               mask: np.ndarray) -> list[dict]:
    rows = []
    thr = model["threshold"]
    for i in np.flatnonzero(mask):
        r = df.iloc[i]
        kept = bool(scores[i] > thr)
        rows.append({
            "ts": str(r["ts"])[:19],
            "user": str(r["user_id"])[-6:],
            "route": f"{r['rule_prev_country']} -> {r['country']}, {r['rule_distance_km']:.0f} km",
            "asn": str(r["asn"]),
            "score": round(float(scores[i]), 3),
            "verdict": "kept" if kept else "auto-closed",
            "label": "takeover" if r["is_takeover"] else "benign",
            "reasons": scoring.reasons(r, terms.iloc[i], model["weights"], kept)
            + [f"score {scores[i]:.2f} {'above' if kept else 'at or below'} threshold {thr:.2f}"],
        })
    return rows


def write_alert_page(df: pd.DataFrame, terms: pd.DataFrame, scores: np.ndarray, model: dict,
                     docs: Path, test: np.ndarray, seed: int, n_closed: int = 60, n_kept: int = 40) -> Path:
    from .geo import RULE

    rng = np.random.default_rng(seed)
    rule = df["rule_fired"].to_numpy()
    ato = df["is_takeover"].to_numpy()
    pop = df["in_sample"].to_numpy()
    kept = scores > model["threshold"]
    base = rule & test & pop & ~ato
    pick = np.zeros(len(df), dtype=bool)
    for m, n in [(base & ~kept, n_closed), (base & kept, n_kept)]:
        idx = np.flatnonzero(m)
        pick[rng.choice(idx, size=min(n, len(idx)), replace=False)] = True
    pick |= rule & test & ato
    rows = alert_rows(df, terms, scores, model, pick)
    docs.mkdir(parents=True, exist_ok=True)
    page = PAGE.format(rows=json.dumps(rows).replace("</", "<\\/"), dist=RULE["min_distance_km"],
                       speed=RULE["min_speed_kmh"], win=RULE["window_minutes"], budget=model["budget_per_10k"])
    path = docs / "index.html"
    path.write_text(page)
    (docs / "example_alerts.json").write_text(json.dumps(rows, indent=1))
    print(f"wrote {path} ({len(rows)} example alerts)")
    return path

