"""Risk score and reason codes from history features.

Each categorical attribute (ASN, IP prefix, country, user agent, OS) becomes a
surprisal term: how unexpected this value is for this user, given how often
they used it before, smoothed toward how common it is across the population.

    p_pop  = (users seen on value + 1) / (users seen + 2)
    P      = (user count on value + ALPHA * p_pop) / (user prior sign-ins + ALPHA)
    term   = -log P

A cold-start user on a carrier ASN that half the population uses scores low;
a user with 40 sign-ins on one ASN who appears on a never-seen hosting ASN
scores high. Two attack-pressure terms count recent failed sign-ins on the
account and distinct users with failed sign-ins from the same IP.

The score is a weighted sum of terms. Weights are either equal (the
unsupervised history score) or fitted with logistic regression on an earlier
time window.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import CATEGORICAL

ALPHA = 1.0
TERMS = [f"s_{k}" for k in CATEGORICAL] + ["s_fail_user", "s_ip_spray"]

LABELS = {
    "asn": "ASN",
    "prefix": "IP prefix",
    "country": "country",
    "ua": "user agent",
    "os": "OS",
}


def term_matrix(f: pd.DataFrame) -> pd.DataFrame:
    """Compute surprisal terms from a features frame."""
    out = pd.DataFrame(index=f.index)
    n = f["u_n"].to_numpy(dtype=float)
    tot = f["p_users"].to_numpy(dtype=float)
    for k in CATEGORICAL:
        p_pop = (f[f"p_{k}"].to_numpy(dtype=float) + 1.0) / (tot + 2.0)
        p = (f[f"u_{k}"].to_numpy(dtype=float) + ALPHA * p_pop) / (n + ALPHA)
        out[f"s_{k}"] = -np.log(p)
    out["s_fail_user"] = np.log1p(f["u_fail_24h"].to_numpy(dtype=float))
    out["s_ip_spray"] = np.log1p(f["p_ip_fail_users"].to_numpy(dtype=float))
    return out


def score(terms: pd.DataFrame, weights: dict[str, float], intercept: float = 0.0) -> np.ndarray:
    w = np.array([weights.get(t, 0.0) for t in TERMS])
    return terms[TERMS].to_numpy() @ w + intercept


def _fmt_value(k: str, v: str) -> str:
    if k == "asn":
        return f"AS{v}"
    if k == "ua":
        return (v[:40] + "...") if len(v) > 40 else v
    return v


def reasons(row: pd.Series, terms: pd.Series, weights: dict[str, float], kept: bool, top: int = 3) -> list[str]:
    """Plain-language reason codes for one sign-in.

    For kept alerts we list the largest contributions to the score; for
    auto-closed alerts we list the strongest signs of familiarity.
    """
    contrib = {t: weights.get(t, 0.0) * terms[t] for t in TERMS}
    out: list[str] = []
    order = sorted(contrib, key=contrib.get, reverse=kept)
    for t in order:
        if len(out) >= top:
            break
        key = t[2:]
        if key in CATEGORICAL:
            label, val = LABELS[key], _fmt_value(key, str(row[key]))
            seen, pop, tot = int(row[f"u_{key}"]), int(row[f"p_{key}"]), int(row["p_users"])
            if kept and seen == 0:
                share = f"{pop:,} of {tot:,} sampled users" if tot else "no population history"
                out.append(f"new {label} for user: {val} ({share})")
            elif not kept and seen > 0:
                out.append(f"{label} {val} seen {seen}x before for user")
            elif not kept and int(row["u_n"]) == 0 and tot and pop / tot >= 0.01:
                out.append(f"first sign-in; {label} {val} common ({pop / tot:.0%} of sampled users)")
        elif t == "s_fail_user" and kept and row["u_fail_24h"] > 0:
            out.append(f"{int(row['u_fail_24h'])} failed sign-ins on account in prior 24h")
        elif t == "s_ip_spray" and kept and row["p_ip_fail_users"] > 0:
            out.append(f"IP {row['ip']} had failed sign-ins on {int(row['p_ip_fail_users'])} other users")
        elif t == "s_fail_user" and not kept and row["u_fail_24h"] == 0:
            out.append("no failed sign-ins on account in prior 24h")
    if not out:
        out.append("score below alert threshold")
    return out
