"""Column mapping from sign-in exports (e.g. Entra ID) to the canonical event schema."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

CANONICAL = ["event_id", "user_id", "ts", "ip", "country", "region", "city", "asn",
             "user_agent", "os", "browser", "device_type", "lat", "lon"]


def load_mapping(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _parse_asn(v):
    if pd.isna(v):
        return None
    s = str(v).strip().upper().removeprefix("AS")
    return int(s) if s.isdigit() else None


def apply_mapping(raw: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Rename and normalise columns of a raw export into canonical events.

    Mapping keys (see ``mappings/entra_signin.json``):
      columns:        canonical name -> export column name
      location:       optional export column holding "City, State, Country"
      success_column / success_values: which rows are successful sign-ins
      takeover_column: optional label column (for back-testing)
    """
    cols = mapping["columns"]
    missing = [c for c in cols.values() if c not in raw.columns]
    if missing:
        raise ValueError(f"export is missing mapped columns: {missing}")
    out = pd.DataFrame({k: raw[v] for k, v in cols.items() if k in CANONICAL})
    loc = mapping.get("location")
    if loc:
        parts = raw[loc].fillna("").astype(str).str.split(",", expand=True).reindex(columns=range(3))
        parts = parts.apply(lambda s: s.str.strip()).replace({"": None})
        out["city"], out["region"], out["country"] = parts[0], parts[1], parts[2]
    sc = mapping.get("success_column")
    if sc:
        ok = {str(v).lower() for v in mapping.get("success_values", ["success"])}
        out["success"] = raw[sc].astype(str).str.strip().str.lower().isin(ok)
    else:
        out["success"] = True
    tc = mapping.get("takeover_column")
    out["is_takeover"] = raw[tc].astype(str).str.lower().isin({"true", "1", "yes"}) if tc else False
    out["ts"] = pd.to_datetime(out["ts"], utc=True, format=mapping.get("ts_format")).dt.tz_localize(None)
    if "asn" in out:
        out["asn"] = out["asn"].map(_parse_asn).astype("Int64")
    if "event_id" in out and not pd.api.types.is_integer_dtype(out["event_id"]):
        out["source_id"] = out["event_id"]
        out["event_id"] = None
    out["in_sample"] = True
    return out.sort_values("ts", kind="stable").reset_index(drop=True)
