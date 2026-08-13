"""Download the RBA login dataset and GeoNames cities, then sample users in DuckDB.

The full RBA file holds 33M login attempts (about 9 GB as CSV). We keep every
user that has at least one labelled account takeover plus a deterministic
random sample of users, so the working set fits comfortably in memory. Users in
the random sample are flagged with ``in_sample`` so alert rates can be measured
on an unbiased population.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import duckdb

RBA_URL = "https://zenodo.org/api/records/6782156/files/rba-dataset.zip/content"
RBA_SIZE = 1093700330
CITIES_URL = "https://download.geonames.org/export/dump/cities1000.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"


def _download(url: str, dest: Path, expected_size: int | None = None) -> Path:
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        print(f"  cached {dest}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "traveltriage/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(part, "wb") as fh:
        shutil.copyfileobj(resp, fh, length=1 << 20)
    part.replace(dest)
    return dest


def _connect(memory_limit: str = "2GB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    tmp = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / "duckdb-spill"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET temp_directory='{tmp}'")
    con.execute("SET threads=4")
    con.execute("SET preserve_insertion_order=false")
    return con


def fetch_geonames(raw: Path, out: Path) -> None:
    cities_zip = _download(CITIES_URL, raw / "cities1000.zip")
    admin1 = _download(ADMIN1_URL, raw / "admin1CodesASCII.txt")
    with zipfile.ZipFile(cities_zip) as zf:
        zf.extract("cities1000.txt", raw)
    con = _connect()
    con.execute(
        f"""
        COPY (
          SELECT c.country_code AS country, lower(c.name) AS name, lower(c.asciiname) AS asciiname,
                 lower(coalesce(a.name, '')) AS region, c.lat, c.lon, c.population
          FROM read_csv('{raw / "cities1000.txt"}', delim='\t', header=false, quote='',
                        columns={{'geonameid':'BIGINT','name':'VARCHAR','asciiname':'VARCHAR',
                                 'alternatenames':'VARCHAR','lat':'DOUBLE','lon':'DOUBLE',
                                 'feature_class':'VARCHAR','feature_code':'VARCHAR',
                                 'country_code':'VARCHAR','cc2':'VARCHAR','admin1':'VARCHAR',
                                 'admin2':'VARCHAR','admin3':'VARCHAR','admin4':'VARCHAR',
                                 'population':'BIGINT','elevation':'VARCHAR','dem':'VARCHAR',
                                 'timezone':'VARCHAR','modified':'VARCHAR'}}) c
          LEFT JOIN read_csv('{admin1}', delim='\t', header=false, quote='',
                        columns={{'code':'VARCHAR','name':'VARCHAR','asciiname':'VARCHAR','gid':'BIGINT'}}) a
            ON a.code = c.country_code || '.' || c.admin1
        ) TO '{out / "cities.parquet"}' (FORMAT parquet)
        """
    )
    n = con.execute(f"SELECT count(*) FROM '{out / 'cities.parquet'}'").fetchone()[0]
    print(f"  wrote {out / 'cities.parquet'} ({n:,} cities)")


def sample_rba(csv_path: Path, out: Path, users: int, seed: int = 7) -> None:
    con = _connect()
    src = f"""read_csv('{csv_path}', header=true, auto_detect=true)"""
    print("  pass 1: listing users and takeover labels")
    con.execute(
        f"""
        CREATE TABLE users AS
        SELECT "User ID" AS user_id, bool_or("Is Account Takeover") AS has_ato
        FROM {src} GROUP BY 1
        """
    )
    con.execute(
        f"""
        CREATE TABLE keep AS
        WITH ranked AS (
          SELECT user_id, has_ato,
                 row_number() OVER (ORDER BY hash(user_id::VARCHAR || '-{seed}')) AS r
          FROM users
        )
        SELECT user_id, r <= {users} AS in_sample, has_ato FROM ranked
        WHERE r <= {users} OR has_ato
        """
    )
    total, ato = con.execute("SELECT count(*), count(*) FILTER (WHERE has_ato) FROM users").fetchone()
    print(f"  {total:,} users in source, {ato:,} with a labelled takeover")
    print("  pass 2: extracting sampled users' login attempts")
    con.execute(
        f"""
        COPY (
          SELECT row_number() OVER (ORDER BY s."Login Timestamp", s."User ID") AS event_id,
                 s."User ID"::VARCHAR AS user_id,
                 s."Login Timestamp"::TIMESTAMP AS ts,
                 s."IP Address" AS ip,
                 s."Country" AS country,
                 s."Region" AS region,
                 s."City" AS city,
                 s."ASN"::BIGINT AS asn,
                 s."User Agent String" AS user_agent,
                 s."OS Name and Version" AS os,
                 s."Browser Name and Version" AS browser,
                 s."Device Type" AS device_type,
                 s."Login Successful"::BOOLEAN AS success,
                 s."Is Account Takeover"::BOOLEAN AS is_takeover,
                 k.in_sample
          FROM {src} s JOIN keep k ON s."User ID" = k.user_id
        ) TO '{out / "logins.parquet"}' (FORMAT parquet)
        """
    )
    n, nu, nt = con.execute(
        f"""SELECT count(*), count(DISTINCT user_id), count(*) FILTER (WHERE is_takeover)
            FROM '{out / "logins.parquet"}'"""
    ).fetchone()
    print(f"  wrote {out / 'logins.parquet'}: {n:,} attempts, {nu:,} users, {nt:,} takeover logins")


def run(out: str, users: int, keep_csv: bool = False) -> None:
    out_dir = Path(out)
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    print("GeoNames cities")
    fetch_geonames(raw, out_dir)
    print("RBA dataset")
    zpath = _download(RBA_URL, raw / "rba-dataset.zip", RBA_SIZE)
    work = Path(tempfile.mkdtemp(prefix="rba-"))
    try:
        with zipfile.ZipFile(zpath) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            print(f"  extracting {name} to {work}")
            with zf.open(name) as src, open(work / "rba.csv", "wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 22)
        sample_rba(work / "rba.csv", out_dir, users)
    finally:
        shutil.rmtree(work, ignore_errors=True)
