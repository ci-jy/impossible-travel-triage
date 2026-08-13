"""City-level geocoding against GeoNames and the geo-velocity ("impossible travel") rule."""

from __future__ import annotations

import duckdb

# Published geo-velocity rule logic: two successful sign-ins by the same user,
# at least 500 km apart, within 90 minutes, implying more than 800 km/h.
RULE = {"min_distance_km": 500.0, "min_speed_kmh": 800.0, "window_minutes": 90.0}

HAVERSINE_SQL = """
    2 * 6371.0 * asin(sqrt(
        pow(sin(radians({lat2} - {lat1}) / 2), 2)
        + cos(radians({lat1})) * cos(radians({lat2})) * pow(sin(radians({lon2} - {lon1}) / 2), 2)
    ))
"""


def haversine_sql(lat1: str, lon1: str, lat2: str, lon2: str) -> str:
    return HAVERSINE_SQL.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2)


def geocode(con: duckdb.DuckDBPyConnection, events: str, cities: str | None, out: str) -> None:
    """Add lat/lon/geo_level to ``events``, writing table ``out``.

    Cities are matched on (country, city name), preferring a match in the same
    region and then the most populous candidate. Sign-ins with no city fall
    back to the population-weighted centroid of their country, which is what
    most IP-geolocation products report for country-only records.
    Rows that already carry ``lat``/``lon`` keep them.
    """
    cols = {r[0] for r in con.execute(f"DESCRIBE {events}").fetchall()}
    has_coords = {"lat", "lon"} <= cols
    if cities is None:
        if not has_coords:
            raise ValueError("no cities table and the input has no lat/lon columns")
        con.execute(
            f"""CREATE OR REPLACE TABLE {out} AS
                SELECT *, CASE WHEN lat IS NULL THEN 'none' ELSE 'input' END AS geo_level FROM {events}"""
        )
        return
    base = f"(SELECT * EXCLUDE (lat, lon) FROM {events})" if has_coords else events
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _country_centroid AS
        SELECT country, sum(lat * population) / sum(population) AS lat,
               sum(lon * population) / sum(population) AS lon
        FROM {cities} WHERE population > 0 GROUP BY country
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _city_match AS
        WITH keys AS (
          SELECT DISTINCT country, lower(city) AS city, lower(coalesce(region, '')) AS region
          FROM {base} WHERE city IS NOT NULL AND city NOT IN ('-', '')
        ),
        cand AS (
          SELECT k.country, k.city, k.region, c.lat, c.lon,
                 row_number() OVER (PARTITION BY k.country, k.city, k.region
                                    ORDER BY (c.region = k.region) DESC, c.population DESC) AS r
          FROM keys k JOIN {cities} c
            ON c.country = k.country AND (c.name = k.city OR c.asciiname = k.city)
        )
        SELECT country, city, region, lat, lon FROM cand WHERE r = 1
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {out} AS
        SELECT e.*,
               coalesce(m.lat, cc.lat) AS lat,
               coalesce(m.lon, cc.lon) AS lon,
               CASE WHEN m.lat IS NOT NULL THEN 'city'
                    WHEN cc.lat IS NOT NULL THEN 'country' ELSE 'none' END AS geo_level
        FROM {base} e
        LEFT JOIN _city_match m
          ON m.country = e.country AND m.city = lower(e.city) AND m.region = lower(coalesce(e.region, ''))
        LEFT JOIN _country_centroid cc ON cc.country = e.country
        """
    )
    if has_coords:
        con.execute(
            f"""CREATE OR REPLACE TABLE {out} AS
                SELECT o.* REPLACE (coalesce(i.lat, o.lat) AS lat, coalesce(i.lon, o.lon) AS lon)
                FROM {out} o JOIN {events} i USING (event_id)"""
        )


def geo_velocity(con: duckdb.DuckDBPyConnection, events: str, out: str, rule: dict = RULE) -> None:
    """Flag successful sign-ins that break the geo-velocity rule.

    For each successful sign-in we look back over the same user's successful
    sign-ins in the previous ``window_minutes`` and fire when any of them is at
    least ``min_distance_km`` away at an implied speed above ``min_speed_kmh``.
    Writes ``out`` with one row per successful sign-in: event_id, rule_fired,
    rule_distance_km, rule_speed_kmh, rule_prev_country.
    """
    win_ms = int(rule["window_minutes"] * 60_000)
    dist = haversine_sql("p.lat", "p.lon", "e.lat", "e.lon")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {out} AS
        WITH s AS (
          SELECT event_id, user_id, epoch_ms(ts) AS ts_ms, lat, lon, country
          FROM {events} WHERE success AND lat IS NOT NULL
        ),
        pairs AS (
          SELECT e.event_id, {dist} AS d_km,
                 greatest(e.ts_ms - p.ts_ms, 1) / 3600000.0 AS dt_h, p.country AS prev_country
          FROM s e JOIN s p
            ON p.user_id = e.user_id AND p.ts_ms < e.ts_ms AND p.ts_ms >= e.ts_ms - {win_ms}
        ),
        hits AS (
          SELECT event_id, max(d_km) AS d_km, max(d_km / dt_h) AS speed, arg_max(prev_country, d_km) AS prev_country
          FROM pairs
          WHERE d_km >= {rule["min_distance_km"]} AND d_km / dt_h > {rule["min_speed_kmh"]}
          GROUP BY event_id
        )
        SELECT e.event_id, h.event_id IS NOT NULL AS rule_fired,
               h.d_km AS rule_distance_km, h.speed AS rule_speed_kmh, h.prev_country AS rule_prev_country
        FROM {events} e LEFT JOIN hits h USING (event_id)
        WHERE e.success
        """
    )
