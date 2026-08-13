"""Command-line interface: fetch, evaluate, score."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


def _score(args: argparse.Namespace) -> int:
    from . import pipeline, scoring
    from .mapping import apply_mapping, load_mapping

    model = json.loads(Path(args.model).read_text())
    raw = pd.read_csv(args.input, dtype=str, keep_default_na=False, na_values=[""])
    events = apply_mapping(raw, load_mapping(args.mapping)) if args.mapping else raw
    con = pipeline.connect("1GB")
    pipeline.load_events(con, events)
    cities = args.cities if args.cities and Path(args.cities).exists() else None
    if cities is None and not {"lat", "lon"} <= set(events.columns):
        print(f"warning: no cities file at {args.cities}; run `python3 -m traveltriage fetch --geonames-only`"
              " for city coordinates. Rule hits need coordinates.", file=sys.stderr)
        events = events.assign(lat=None, lon=None)
        pipeline.load_events(con, events)
    df = pipeline.run(con, cities)
    con.close()
    terms = scoring.term_matrix(df)
    s = scoring.score(terms, model["weights"], model.get("intercept", 0.0))
    alert = s > model["threshold"]
    keep = alert | df["rule_fired"].to_numpy() if not args.all else slice(None)
    out = df.loc[keep, ["event_id", "ts", "user_id", "ip", "country", "city", "asn", "rule_fired",
                        "rule_distance_km"]].copy()
    out["score"] = s[keep].round(3)
    out["alert"] = alert[keep]
    out["verdict"] = [
        "alert" if a else ("auto-closed rule alert" if r else "no alert")
        for a, r in zip(out["alert"], out["rule_fired"])
    ]
    out["reasons"] = [
        "; ".join(scoring.reasons(df.loc[i], terms.loc[i], model["weights"], bool(alert[df.index.get_loc(i)])))
        for i in out.index
    ]
    if "source_id" in events.columns:
        out.insert(1, "source_id", events.set_index(events.index + 1).loc[out["event_id"], "source_id"].to_numpy())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    n_rule = int(df["rule_fired"].sum())
    n_closed = int((df["rule_fired"].to_numpy() & ~alert).sum())
    print(f"scored {len(df):,} successful sign-ins: {int(alert.sum())} alerts; "
          f"geo-velocity rule fired {n_rule}, {n_closed} auto-closed -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="traveltriage", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download RBA + GeoNames and sample users")
    f.add_argument("--out", default="data/")
    f.add_argument("--users", type=int, default=200_000, help="random users to sample (plus all takeover users)")
    f.add_argument("--geonames-only", action="store_true", help="only fetch the GeoNames cities table")

    e = sub.add_parser("evaluate", help="compare the scorer with the geo-velocity rule on a later window")
    e.add_argument("--data", default="data/")
    e.add_argument("--budget-per-10k", type=float, default=10.0)
    e.add_argument("--out", default="results/eval.json")
    e.add_argument("--calib-start", default="2020-06-01")
    e.add_argument("--test-start", default="2020-10-01")
    e.add_argument("--docs", default="docs")

    s = sub.add_parser("score", help="score a sign-in CSV; write alerts with score and reason codes")
    s.add_argument("--input", required=True)
    s.add_argument("--mapping", help="column mapping JSON, e.g. mappings/entra_signin.json")
    s.add_argument("--model", default="results/model.json")
    s.add_argument("--cities", default="data/cities.parquet")
    s.add_argument("--out", default="alerts.csv")
    s.add_argument("--all", action="store_true", help="write every successful sign-in, not only alerts")

    args = p.parse_args(argv)
    if args.cmd == "fetch":
        from . import fetch

        if args.geonames_only:
            out = Path(args.out)
            fetch.fetch_geonames(out / "raw", out)
        else:
            fetch.run(args.out, args.users)
        return 0
    if args.cmd == "evaluate":
        from . import evaluate

        evaluate.run(args.data, args.budget_per_10k, args.out, args.calib_start, args.test_start, args.docs)
        return 0
    return _score(args)


if __name__ == "__main__":
    raise SystemExit(main())
