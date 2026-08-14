"""Plot takeover recall vs alert budget from results/eval.json -> docs/recall_vs_budget.png."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(src: str = "results/eval.json", out: str = "docs/recall_vs_budget.png") -> None:
    r = json.loads((ROOT / src).read_text())
    c = r["curve"]
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    styles = {"history_lr": ("History score (fitted weights)", "#0b6bcb", "-"),
              "history_equal": ("History score (equal weights)", "#6b9bd1", "--"),
              "asn_prefix_only": ("ASN + prefix history only", "#9aa5b1", ":")}
    for k, (label, color, ls) in styles.items():
        ax.plot(c["budgets_per_10k"], [100 * v for v in c[k]], ls, color=color, marker="o", ms=4, label=label)
    rr = r["rule"]
    lo, hi = rr["takeover_recall_ci95"]
    ax.errorbar([rr["alerts_per_10k"]], [100 * rr["takeover_recall"]],
                yerr=[[100 * (rr["takeover_recall"] - lo)], [100 * (hi - rr["takeover_recall"])]],
                fmt="s", color="#c4314b", ms=8, capsize=4, label="Geo-velocity rule")
    ax.axvline(r["budget_per_10k"], color="#888", lw=0.8, ls="--")
    ax.set_xscale("log")
    ax.set_xlabel("Alerts per 10k sign-ins (threshold set on earlier window)")
    ax.set_ylabel("Takeover sign-ins caught (%)")
    w = r["windows"]
    ax.set_title(f"Test window: {w['test_takeovers']} takeover sign-ins, {w['test_signins_sampled']:,} sampled sign-ins",
                 fontsize=10)
    ax.set_ylim(0, 100)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    (ROOT / out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(ROOT / out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
