import json
from pathlib import Path

import pandas as pd
import pytest

from traveltriage import cli, scoring
from traveltriage.mapping import apply_mapping, load_mapping

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "entra_signins_example.csv"
MAPPING = ROOT / "mappings" / "entra_signin.json"


def test_mapping_parses_entra_export():
    raw = pd.read_csv(EXAMPLE, dtype=str)
    ev = apply_mapping(raw, load_mapping(MAPPING))
    assert {"user_id", "ts", "ip", "asn", "country", "city", "success"} <= set(ev.columns)
    assert ev["ts"].is_monotonic_increasing
    assert set(ev["country"]) == {"NO", "DE", "NL", "RO"}
    assert ev.loc[ev["city"] == "Oslo", "region"].eq("Oslo").all()
    assert (~ev["success"]).sum() == 16
    assert ev["asn"].notna().all()


def test_score_cli_on_entra_example(tmp_path):
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"weights": {t: 1.0 for t in scoring.TERMS}, "intercept": 0.0, "threshold": 9.0}))
    out = tmp_path / "alerts.csv"
    rc = cli.main(["score", "--input", str(EXAMPLE), "--mapping", str(MAPPING), "--model", str(model),
                   "--cities", str(tmp_path / "missing.parquet"), "--out", str(out)])
    assert rc == 0
    alerts = pd.read_csv(out)
    assert {"score", "verdict", "reasons", "source_id"} <= set(alerts.columns)
    # Without a cities file the rule cannot fire, but the history score still flags the takeover:
    # the two takeovers (fresh residential ASNs right after failed sign-ins) rank highest.
    top = alerts.sort_values("score").tail(2)
    assert set(top["asn"]) == {8708, 29695}
    assert all("new ASN for user" in r for r in top["reasons"])


def test_score_cli_with_fitted_model_closes_vpn_and_cloud_alerts(tmp_path):
    cities = ROOT / "data" / "cities.parquet"
    if not cities.exists():
        pytest.skip("run `python3 -m traveltriage fetch --geonames-only` for city coordinates")
    out = tmp_path / "alerts.csv"
    cli.main(["score", "--input", str(EXAMPLE), "--mapping", str(MAPPING), "--model",
              str(ROOT / "results" / "model.json"), "--cities", str(cities), "--out", str(out)])
    alerts = pd.read_csv(out)
    rule = alerts[alerts["rule_fired"]]
    assert len(rule) > 100 and set(rule["country"]) == {"DE", "NL"}
    assert (rule["verdict"] == "auto-closed rule alert").all()
    kept = alerts[alerts["verdict"] == "alert"]
    assert kept["asn"].tolist() == [8708]
