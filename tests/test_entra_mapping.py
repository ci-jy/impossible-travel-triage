import json
from pathlib import Path

import pandas as pd

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
    assert set(ev["country"]) == {"NO", "SE", "DE", "NL"}
    assert ev.loc[ev["city"] == "Oslo", "region"].eq("Oslo").all()
    assert (~ev["success"]).sum() == 6
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
    # a fresh residential ASN in the victim's own country, right after failed sign-ins.
    top = alerts.sort_values("score").iloc[-1]
    assert top["asn"] == 29695
    assert top["reasons"].startswith("new ASN for user: AS29695")
