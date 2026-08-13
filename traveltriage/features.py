"""Time-ordered, no-look-ahead per-user history features in DuckDB SQL.

Every count uses a window frame that ends 1 ms *before* the event, so an
event's features depend only on events strictly earlier in time. Events at the
same timestamp never see each other.

Two kinds of counts:

* per-user: how often this user signed in successfully before, in total and
  with the same ASN, /24 (or /48) prefix, country, user agent and OS;
* population: how many distinct sampled users had already signed in from this
  ASN / prefix / country / user agent / OS, plus how many distinct users had
  failed sign-ins from this IP address (password spraying).

Population counts use only ``in_sample`` users, the unbiased random sample, so
over-sampled takeover users cannot inflate them.
"""

from __future__ import annotations

import duckdb

CATEGORICAL = ["asn", "prefix", "country", "ua", "os"]

PRIOR = "RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
DAY_MS = 86_400_000


def _base_sql(events: str) -> str:
    return f"""
        SELECT event_id, user_id, epoch_ms(ts) AS ts_ms, success, coalesce(in_sample, true) AS in_sample,
               coalesce(asn::VARCHAR, '?') AS asn,
               CASE WHEN ip IS NULL THEN '?'
                    WHEN contains(ip, ':') THEN array_to_string(string_split(ip, ':')[1:3], ':') || '::/48'
                    ELSE coalesce(regexp_extract(ip, '^(\\d+\\.\\d+\\.\\d+)\\.', 1), ip) || '.0/24' END AS prefix,
               coalesce(country, '?') AS country,
               coalesce(user_agent, '?') AS ua,
               coalesce(os, '?') AS os,
               coalesce(ip, '?') AS ip
        FROM {events}
    """


def build_features(con: duckdb.DuckDBPyConnection, events: str, out: str) -> None:
    """Write table ``out``: one row per successful sign-in with history counts."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE _b AS {_base_sql(events)}")

    user_windows = [
        f"coalesce(sum(success::INT) OVER (PARTITION BY user_id ORDER BY ts_ms {PRIOR}), 0) AS u_n",
        f"""coalesce(sum((NOT success)::INT) OVER (PARTITION BY user_id ORDER BY ts_ms
              RANGE BETWEEN {DAY_MS} PRECEDING AND 1 PRECEDING), 0) AS u_fail_24h""",
    ] + [
        f"coalesce(sum(success::INT) OVER (PARTITION BY user_id, {k} ORDER BY ts_ms {PRIOR}), 0) AS u_{k}"
        for k in CATEGORICAL
    ]
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE _u AS
            SELECT event_id, success, {", ".join(user_windows)} FROM _b"""
    )

    def pop_count(key: str, name: str, where: str) -> None:
        # Distinct users per key value strictly before each event: emit a +1
        # marker at each (key, user) first-seen time and take a running sum.
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _p_{name} AS
            WITH fs AS (
              SELECT {key} AS k, user_id, min(ts_ms) AS ts_ms FROM _b WHERE {where} GROUP BY 1, 2
            ),
            u AS (
              SELECT k, ts_ms, 1 AS w, NULL::BIGINT AS event_id FROM fs
              UNION ALL
              SELECT {key} AS k, ts_ms, 0 AS w, event_id FROM _b WHERE success
            )
            SELECT event_id, coalesce(sum(w) OVER (PARTITION BY k ORDER BY ts_ms {PRIOR}), 0) AS {name}
            FROM u
            """
        )
        con.execute(f"DELETE FROM _p_{name} WHERE event_id IS NULL")

    pop_count("'all'", "p_users", "success AND in_sample")
    for k in CATEGORICAL:
        pop_count(k, f"p_{k}", "success AND in_sample")
    pop_count("ip", "p_ip_fail_users", "NOT success AND in_sample")

    joins = "\n".join(
        f"JOIN _p_{n} USING (event_id)" for n in ["p_users", "p_ip_fail_users"] + [f"p_{k}" for k in CATEGORICAL]
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {out} AS
        SELECT b.event_id, b.user_id, b.ts_ms, b.in_sample, b.asn, b.prefix, b.country, b.ua, b.os, b.ip,
               u.* EXCLUDE (event_id, success),
               p_users, p_ip_fail_users, {", ".join(f"p_{k}" for k in CATEGORICAL)}
        FROM _b b JOIN _u u USING (event_id)
        {joins}
        WHERE b.success
        ORDER BY b.event_id
        """
    )
    for t in ["_b", "_u", "_p_p_users", "_p_p_ip_fail_users"] + [f"_p_p_{k}" for k in CATEGORICAL]:
        con.execute(f"DROP TABLE IF EXISTS {t}")
