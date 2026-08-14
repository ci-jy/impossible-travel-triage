"""Compare the history scorer with the geo-velocity rule on a later time window.

Windows (successful sign-ins only):

* warm-up: history accumulates, nothing is scored;
* calibration: score weights are fitted and the alert threshold is set so the
  scorer fires ``budget`` alerts per 10k sign-ins of randomly sampled users;
* test: everything reported in ``eval.json`` comes from this window.

Alert rates use only the random user sample (``in_sample``); recall uses every
labelled takeover sign-in. Confidence intervals come from a bootstrap that
resamples users, so several takeover sign-ins on one account move together.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.metrics import average_precision_score

from . import pipeline, report, scoring
from .geo import RULE

BUDGET_CURVE = [1, 2, 5, 10, 20, 50, 100, 200]
N_BOOT = 1000


def threshold_for_budget(scores: np.ndarray, budget_per_10k: float) -> float:
    """Score threshold so that ``budget_per_10k`` of every 10k sign-ins alert (score > t)."""
    q = 1.0 - budget_per_10k / 10_000.0
    return float(np.quantile(scores, q, method="higher")) if len(scores) else float("inf")


def _user_codes(users: pd.Series) -> tuple[np.ndarray, int]:
    codes, uniq = pd.factorize(users)
    return codes, len(uniq)


def cluster_bootstrap(stat_fn, groups: np.ndarray, n_groups: int, rng: np.random.Generator, n: int = N_BOOT):
    """Bootstrap over groups. ``stat_fn(weights)`` gets per-group resample counts."""
    vals = []
    for _ in range(n):
        w = np.bincount(rng.integers(0, n_groups, n_groups), minlength=n_groups)
        vals.append(stat_fn(w))
    vals = np.asarray(vals, dtype=float)
    if np.isnan(vals).all():
        return [float("nan"), float("nan")]
    return [float(np.nanpercentile(vals, 2.5)), float(np.nanpercentile(vals, 97.5))]


def ratio_ci(num: np.ndarray, den: np.ndarray, groups: np.ndarray, n_groups: int,
             rng: np.random.Generator) -> list[float]:
    """95% bootstrap CI of sum(num)/sum(den), resampling groups (users)."""
    gn = np.bincount(groups, weights=num.astype(float), minlength=n_groups)
    gd = np.bincount(groups, weights=den.astype(float), minlength=n_groups)

    def stat(w):
        d = w @ gd
        return (w @ gn) / d if d else np.nan

    return cluster_bootstrap(stat, groups, n_groups, rng)


def _metric_block(alert_pop: np.ndarray, pop_users: np.ndarray, n_pop_users: int,
                  alert_ato: np.ndarray, ato_users: np.ndarray, n_ato_users: int,
                  rng: np.random.Generator) -> dict:
    lo, hi = ratio_ci(alert_pop, np.ones(len(alert_pop)), pop_users, n_pop_users, rng)
    return {
        "alerts_per_10k": float(10_000 * alert_pop.mean()) if len(alert_pop) else 0.0,
        "alerts_per_10k_ci95": [10_000 * lo, 10_000 * hi],
        "takeover_recall": float(alert_ato.mean()) if len(alert_ato) else float("nan"),
        "takeover_recall_ci95": ratio_ci(alert_ato, np.ones(len(alert_ato)), ato_users, n_ato_users, rng),
        "takeovers_caught": int(alert_ato.sum()),
        "_alert_ato": alert_ato,
    }


def fit_weights(terms: pd.DataFrame, y: np.ndarray, l2: float = 1.0) -> tuple[dict, float]:
    """Class-balanced logistic regression with non-negative term weights.

    Every term measures surprise or attack pressure, so a negative weight would
    mean "more unusual is safer" and would make reason codes meaningless. The
    sign constraint also stops the fit from leaning on terms that happen to be
    absent among the few labelled takeovers.
    """
    X = terms[scoring.TERMS].to_numpy(dtype=float)
    sw = np.where(y == 1, 0.5 / max(y.sum(), 1), 0.5 / max((1 - y).sum(), 1))

    def loss(theta):
        w, b = theta[:-1], theta[-1]
        z = X @ w + b
        p = expit(z)
        nll = -(sw * (y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12))).sum()
        grad_z = sw * (p - y)
        reg = 0.5 * 1e-3 * l2 * (w @ w)
        return nll + reg, np.r_[X.T @ grad_z + 1e-3 * l2 * w, grad_z.sum()]

    k = X.shape[1]
    res = minimize(loss, np.r_[np.full(k, 0.1), 0.0], jac=True, method="L-BFGS-B",
                   bounds=[(0, None)] * k + [(None, None)])
    w, b = res.x[:-1], res.x[-1]
    return dict(zip(scoring.TERMS, map(float, w))), float(b)


def run(data: str, budget: float, out: str, calib_start: str, test_start: str,
        docs: str = "docs", seed: int = 7) -> dict:
    data_dir = Path(data)
    con = pipeline.connect("3GB")
    pipeline.load_events(con, str(data_dir / "logins.parquet"))
    cities = data_dir / "cities.parquet"
    print("computing rule hits and history features in DuckDB")
    df = pipeline.run(con, str(cities) if cities.exists() else None)
    geo_mix = con.execute("SELECT geo_level, count(*) FROM events WHERE success GROUP BY 1").fetchall()
    con.close()
    terms = scoring.term_matrix(df)

    calib_start_ts, test_start_ts = pd.Timestamp(calib_start), pd.Timestamp(test_start)
    calib = (df["ts"] >= calib_start_ts) & (df["ts"] < test_start_ts)
    test = df["ts"] >= test_start_ts
    pop, ato = df["in_sample"].to_numpy(), df["is_takeover"].to_numpy()

    rng = np.random.default_rng(seed)

    def fit_on(window: pd.Series) -> tuple[dict, float]:
        neg = np.flatnonzero(window & pop & ~ato)
        neg = rng.choice(neg, size=min(len(neg), 300_000), replace=False)
        pos = np.flatnonzero(window & ato)
        idx = np.concatenate([neg, pos])
        return fit_weights(terms.iloc[idx], np.r_[np.zeros(len(neg)), np.ones(len(pos))])

    lr_w, lr_b = fit_on(calib)
    pos = np.flatnonzero(calib & ato)
    # Model selection inside the calibration window: fit on its first half, compare
    # variants on its second half. The test window is never consulted.
    calib_mid = calib_start_ts + (test_start_ts - calib_start_ts) / 2
    sel_fit = calib & (df["ts"] < calib_mid)
    sel_eval = (calib & (df["ts"] >= calib_mid)).to_numpy()
    sel_w, sel_b = fit_on(sel_fit)

    variants = {
        "history_lr": ("History score, weights fitted on calibration window", lr_w, lr_b),
        "history_equal": ("History score, equal weights (no labels used)", {t: 1.0 for t in scoring.TERMS}, 0.0),
        "asn_prefix_only": ("ASN + IP-prefix history only, equal weights",
                            {"s_asn": 1.0, "s_prefix": 1.0}, 0.0),
    }
    scores = {k: scoring.score(terms, w, b) for k, (_, w, b) in variants.items()}
    sel_scores = dict(scores, history_lr=scoring.score(terms, sel_w, sel_b))
    sp, sa = sel_eval & pop, sel_eval & ato
    calib_recall = {k: float((v[sa] > threshold_for_budget(v[sp], budget)).mean()) for k, v in sel_scores.items()}
    primary = max(calib_recall, key=lambda k: (calib_recall[k], -list(variants).index(k)))

    tp = (test & pop).to_numpy()
    ta = (test & ato).to_numpy()
    pop_codes, n_pop = _user_codes(df.loc[tp, "user_id"])
    ato_codes, n_ato = _user_codes(df.loc[ta, "user_id"])
    rule = df["rule_fired"].to_numpy()

    def block(alert: np.ndarray) -> dict:
        return _metric_block(alert[tp], pop_codes, n_pop, alert[ta], ato_codes, n_ato, rng)

    rule_block = block(rule)
    rule_calib_rate = float(10_000 * rule[(calib & pop).to_numpy()].mean())

    results: dict = {
        "data": {
            "source": "RBA dataset (Wiefling et al., Zenodo 6782156), sampled users",
            "successful_signins": int(len(df)),
            "users": int(df["user_id"].nunique()),
            "sampled_users": int(df.loc[pop, "user_id"].nunique()),
            "takeover_signins_total": int(ato.sum()),
            "takeover_users_total": int(df.loc[ato, "user_id"].nunique()),
            "geocoding": {k: int(v) for k, v in geo_mix},
        },
        "windows": {
            "warmup": [str(df["ts"].min()), calib_start],
            "calibration": [calib_start, test_start],
            "test": [test_start, str(df["ts"].max())],
            "calibration_signins_sampled": int((calib & pop).sum()),
            "calibration_takeovers": int(len(pos)),
            "selection_split": str(calib_mid),
            "selection_takeovers": int(sa.sum()),
            "test_signins_sampled": int(tp.sum()),
            "test_takeovers": int(ta.sum()),
            "test_takeover_users": int(n_ato),
        },
        "budget_per_10k": budget,
        "rule": {"definition": RULE, "calibration_alerts_per_10k": rule_calib_rate,
                 "test_average_precision": float(average_precision_score(ato[tp | ta], rule[tp | ta])),
                 **{k: v for k, v in rule_block.items() if not k.startswith("_")}},
        "primary": primary,
        "variants": {},
        "curve": {"budgets_per_10k": BUDGET_CURVE},
    }

    def recall_diff_ci(a_ato: np.ndarray, b_ato: np.ndarray) -> list[float]:
        d = a_ato.astype(float) - b_ato.astype(float)
        return ratio_ci(d, np.ones(len(d)), ato_codes, n_ato, rng)

    for name, (desc, w, b) in variants.items():
        s = scores[name]
        thr = threshold_for_budget(s[(calib & pop).to_numpy()], budget)
        alert = s > thr
        blk = block(alert)
        thr_rule = threshold_for_budget(s[(calib & pop).to_numpy()], rule_calib_rate)
        at_rule = block(s > thr_rule)
        # Triage: rule alerts the scorer would auto-close (score at or below threshold).
        closed = rule & ~alert
        rule_pop = rule & tp
        triage = {
            "rule_alerts_sampled": int(rule_pop.sum()),
            "auto_closed_share": float(closed[rule_pop].mean()) if rule_pop.any() else float("nan"),
            "auto_closed_share_ci95": ratio_ci(closed[tp] & rule[tp], rule[tp], pop_codes, n_pop, rng),
            "rule_caught_takeovers": int((rule & ta).sum()),
            "takeovers_lost_by_auto_close": int((closed & ta).sum()),
        }
        results["variants"][name] = {
            "description": desc,
            "selection_recall_at_budget": calib_recall[name],
            "weights": w,
            "intercept": b,
            "threshold": thr,
            "at_budget": {k: v for k, v in blk.items() if not k.startswith("_")},
            "recall_minus_rule_ci95": recall_diff_ci(blk["_alert_ato"], rule_block["_alert_ato"]),
            "at_rule_alert_rate": {k: v for k, v in at_rule.items() if not k.startswith("_")},
            "triage": triage,
            # Ranking quality over all test sign-ins of sampled users plus every takeover.
            "test_average_precision": float(average_precision_score(ato[tp | ta], s[tp | ta])),
        }
        curve = []
        for bb in BUDGET_CURVE:
            t_b = threshold_for_budget(s[(calib & pop).to_numpy()], bb)
            curve.append(float((s[ta] > t_b).mean()) if ta.any() else float("nan"))
        results["curve"][name] = curve

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"wrote {out_path}")

    pv = variants[primary]
    model = {
        "variant": primary,
        "alpha": scoring.ALPHA,
        "weights": pv[1],
        "intercept": pv[2],
        "threshold": results["variants"][primary]["threshold"],
        "budget_per_10k": budget,
        "calibration_window": [calib_start, test_start],
    }
    (out_path.parent / "model.json").write_text(json.dumps(model, indent=2))
    (out_path.parent / "rule_thresholds.json").write_text(json.dumps({
        "geo_velocity_rule": RULE,
        "history_score": {"threshold": model["threshold"], "budget_per_10k": budget,
                          "weights": model["weights"], "intercept": model["intercept"]},
    }, indent=2))

    report.write_alert_page(df, terms, scores[primary], model, Path(docs), test.to_numpy(), seed)
    _print_summary(results)
    return results


def _print_summary(r: dict) -> None:
    w = r["windows"]
    print(f"test window: {w['test_signins_sampled']:,} sampled sign-ins, {w['test_takeovers']} takeover sign-ins "
          f"on {w['test_takeover_users']} users")
    rr = r["rule"]
    print(f"  geo-velocity rule: recall {rr['takeover_recall']:.1%} {rr['takeover_recall_ci95']}, "
          f"{rr['alerts_per_10k']:.1f} alerts/10k")
    for k, v in r["variants"].items():
        a = v["at_budget"]
        print(f"  {k}: recall {a['takeover_recall']:.1%} {a['takeover_recall_ci95']}, "
              f"{a['alerts_per_10k']:.1f} alerts/10k, auto-closes {v['triage']['auto_closed_share']:.1%} "
              f"of rule alerts, loses {v['triage']['takeovers_lost_by_auto_close']}"
              f"/{v['triage']['rule_caught_takeovers']} rule-caught takeovers")
