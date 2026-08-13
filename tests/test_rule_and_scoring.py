import numpy as np
import pandas as pd

from traveltriage import scoring
from traveltriage.evaluate import threshold_for_budget

from .conftest import COORDS, run_frame


def _events(rows):
    df = pd.DataFrame(rows, columns=["user_id", "ts", "country", "asn", "success"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["lat"] = [COORDS[c][0] for c in df["country"]]
    df["lon"] = [COORDS[c][1] for c in df["country"]]
    df["ip"] = "10.0.0.1"
    df["user_agent"], df["os"], df["in_sample"], df["is_takeover"] = "ua", "os", True, False
    return df


def test_rule_fires_on_fast_long_hop_only():
    df = _events([
        ("a", "2020-01-01 10:00", "NO", 1, True),
        ("a", "2020-01-01 10:30", "US", 2, True),   # ~6200 km in 30 min -> fires
        ("a", "2020-01-01 13:00", "NO", 1, True),   # outside the 90-minute window
        ("a", "2020-01-01 13:20", "SE", 1, True),   # ~420 km -> below 500 km
        ("b", "2020-01-01 10:10", "US", 3, True),   # other user, no history
        ("a", "2020-01-01 14:00", "US", 2, False),  # failed sign-ins are not scored
    ])
    out = run_frame(df).set_index("event_id")
    assert out["rule_fired"].tolist() == [False, True, False, False, False]
    assert 6000 < out.loc[2, "rule_distance_km"] < 6600


def test_familiar_asn_scores_lower_than_new_asn():
    rows = [("a", f"2020-01-0{d} 09:00", "NO", 7, True) for d in range(1, 9)]
    rows += [("a", "2020-01-09 09:00", "NO", 7, True), ("a", "2020-01-09 09:05", "NO", 99, True)]
    out = run_frame(_events(rows))
    terms = scoring.term_matrix(out)
    assert terms["s_asn"].iloc[-1] > 3 * terms["s_asn"].iloc[-2]
    w = {t: 1.0 for t in scoring.TERMS}
    r_new = scoring.reasons(out.iloc[-1], terms.iloc[-1], w, kept=True)
    r_old = scoring.reasons(out.iloc[-2], terms.iloc[-2], w, kept=False)
    assert any(r.startswith("new ASN for user: AS99") for r in r_new)
    assert r_old[0] == "ASN AS7 seen 8x before for user"


def test_cold_start_prior_uses_population():
    # Many sampled users sign in from AS1; a brand-new user on AS1 should score
    # lower than a brand-new user on an ASN nobody has used.
    rows = [(f"u{i}", f"2020-01-01 0{i}:00", "NO", 1, True) for i in range(8)]
    rows += [("new1", "2020-01-02 09:00", "NO", 1, True), ("new2", "2020-01-02 09:00", "NO", 555, True)]
    out = run_frame(_events(rows))
    terms = scoring.term_matrix(out)
    assert terms["s_asn"].iloc[-2] < terms["s_asn"].iloc[-1]


def test_threshold_matches_budget():
    s = np.arange(100_000, dtype=float)
    t = threshold_for_budget(s, 10)
    assert (s > t).sum() == 99  # 10 per 10k of 100k = 100, minus the tie at the quantile
