import json

import duckdb

import numpy as np
import pandas as pd

from traveltriage import evaluate


def _dataset(n_users=150, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    home = {u: (int(rng.integers(1, 6)), "NO") for u in range(n_users)}
    for u in range(n_users):
        t = pd.Timestamp("2020-02-01") + pd.Timedelta(hours=int(rng.integers(0, 48)))
        while t < pd.Timestamp("2020-12-31"):
            asn, c = home[u]
            if rng.random() < 0.05:  # carrier hop abroad
                asn, c = 9, "SE"
            rows.append((str(u), t, c, asn, True, False))
            t += pd.Timedelta(hours=float(rng.exponential(60)))
    for u in rng.choice(n_users, 12, replace=False):  # takeovers: failures, then a fresh ASN at home
        t = pd.Timestamp("2020-03-01") + pd.Timedelta(days=int(rng.integers(0, 290)))
        for k in range(4):
            rows.append((str(u), t + pd.Timedelta(minutes=k), "NO", 777, False, False))
        rows.append((str(u), t + pd.Timedelta(minutes=10), "NO", 777, True, True))
    df = pd.DataFrame(rows, columns=["user_id", "ts", "country", "asn", "success", "is_takeover"])
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    df["event_id"] = np.arange(1, len(df) + 1)
    df["ip"] = [f"10.{a}.{rng.integers(0, 3)}.{rng.integers(1, 250)}" for a in df["asn"]]
    df["region"], df["city"] = "-", "-"
    df["user_agent"], df["os"], df["browser"], df["device_type"] = "ua", "iOS 14", "Safari", "mobile"
    df["in_sample"] = True
    return df


def test_evaluate_end_to_end(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    logins = _dataset()  # noqa: F841 (read by DuckDB)
    duckdb.sql(f"COPY logins TO '{data / 'logins.parquet'}' (FORMAT parquet)")
    cities = pd.DataFrame({"country": ["NO", "SE"], "name": ["oslo", "stockholm"], "asciiname": ["oslo", "stockholm"],
                  "region": ["oslo", "stockholm"], "lat": [59.9, 59.3], "lon": [10.7, 18.1],
                  "population": [1, 1]})  # noqa: F841
    duckdb.sql(f"COPY cities TO '{data / 'cities.parquet'}' (FORMAT parquet)")
    out = tmp_path / "results" / "eval.json"
    r = evaluate.run(str(data), 100, str(out), "2020-05-01", "2020-09-01", docs=str(tmp_path / "docs"))
    saved = json.loads(out.read_text())
    assert saved["primary"] in saved["variants"]
    assert saved["windows"]["test_takeovers"] > 0
    v = saved["variants"][saved["primary"]]["at_budget"]
    lo, hi = v["takeover_recall_ci95"]
    assert lo <= v["takeover_recall"] <= hi
    # Takeovers come from a never-seen ASN after failed sign-ins; the rule cannot see them.
    assert v["takeover_recall"] > saved["rule"]["takeover_recall"]
    assert (tmp_path / "docs" / "index.html").exists()
    assert (out.parent / "model.json").exists() and (out.parent / "rule_thresholds.json").exists()
    assert r["data"]["takeover_signins_total"] == 12
