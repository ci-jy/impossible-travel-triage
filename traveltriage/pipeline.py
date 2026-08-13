"""Shared pipeline: events -> geocode -> rule + features -> scored frame."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from .features import build_features
from .geo import geo_velocity, geocode

EVENT_COLUMNS = [
    "event_id", "user_id", "ts", "ip", "country", "region", "city", "asn", "user_agent",
    "os", "browser", "device_type", "success", "is_takeover", "in_sample",
]


def connect(memory_limit: str = "2GB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    spill = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "duckdb-spill"
    spill.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("SET threads=4")
    return con


def load_events(con: duckdb.DuckDBPyConnection, df_or_path, name: str = "events_raw") -> None:
    """Register events from a DataFrame or parquet path as table ``name``."""
    if isinstance(df_or_path, pd.DataFrame):
        df = df_or_path.copy()
        for c in EVENT_COLUMNS:
            if c not in df.columns:
                df[c] = None
        if df["event_id"].isna().all():
            df["event_id"] = range(1, len(df) + 1)
        con.register("_df_in", df)
        con.execute(
            f"""CREATE OR REPLACE TABLE {name} AS SELECT * REPLACE (
                  event_id::BIGINT AS event_id, user_id::VARCHAR AS user_id, ts::TIMESTAMP AS ts,
                  ip::VARCHAR AS ip, country::VARCHAR AS country, region::VARCHAR AS region,
                  city::VARCHAR AS city, TRY_CAST(asn AS BIGINT) AS asn, user_agent::VARCHAR AS user_agent,
                  os::VARCHAR AS os, browser::VARCHAR AS browser, device_type::VARCHAR AS device_type,
                  success::BOOLEAN AS success, is_takeover::BOOLEAN AS is_takeover,
                  in_sample::BOOLEAN AS in_sample) FROM _df_in"""
        )
        con.unregister("_df_in")
    else:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_parquet('{df_or_path}')")


def run(con: duckdb.DuckDBPyConnection, cities: str | None) -> pd.DataFrame:
    """Geocode ``events_raw``, run rule and features; return one row per successful sign-in."""
    if cities is not None:
        con.execute(f"CREATE OR REPLACE TABLE cities AS SELECT * FROM read_parquet('{cities}')")
        geocode(con, "events_raw", "cities", "events")
    else:
        geocode(con, "events_raw", None, "events")
    geo_velocity(con, "events", "rule_hits")
    build_features(con, "events", "features")
    return con.execute(
        """
        SELECT f.*, e.ts, e.city, e.region, e.geo_level, e.device_type, e.browser,
               coalesce(e.is_takeover, false) AS is_takeover,
               r.rule_fired, r.rule_distance_km, r.rule_speed_kmh, r.rule_prev_country
        FROM features f
        JOIN events e USING (event_id)
        JOIN rule_hits r USING (event_id)
        ORDER BY f.event_id
        """
    ).df()
