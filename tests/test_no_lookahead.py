"""Leak test: an event's features and score depend only on strictly earlier events."""

import numpy as np
import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from traveltriage import scoring

from .conftest import run_frame, synthetic_events

FEATURE_COLS = [
    "u_n", "u_fail_24h", "u_asn", "u_prefix", "u_country", "u_ua", "u_os",
    "p_users", "p_ip_fail_users", "p_asn", "p_prefix", "p_country", "p_ua", "p_os", "rule_fired",
]
EQUAL = {t: 1.0 for t in scoring.TERMS}


def _scores(frame: pd.DataFrame) -> pd.DataFrame:
    terms = scoring.term_matrix(frame)
    frame = frame.assign(score=scoring.score(terms, EQUAL))
    return frame.set_index("event_id")[FEATURE_COLS + ["score"]]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 10_000), cut=st.floats(0.1, 0.9), perm_seed=st.integers(0, 10_000),
       drop=st.booleans())
def test_shuffling_future_events_never_changes_past_scores(seed, cut, perm_seed, drop):
    df = synthetic_events(seed)
    t_cut = df["ts"].quantile(cut)
    past = df["ts"] <= t_cut
    future = df[~past].copy()
    rng = np.random.default_rng(perm_seed)
    # Shuffle every attribute of future events independently across future rows,
    # then shuffle the row order of the whole frame.
    for col in ["user_id", "ip", "country", "asn", "user_agent", "os", "success", "in_sample", "lat", "lon"]:
        future[col] = rng.permutation(future[col].to_numpy())
    if drop and len(future) > 1:
        future = future.iloc[: len(future) // 2]
    altered = pd.concat([df[past], future]).sample(frac=1.0, random_state=perm_seed)

    base = _scores(run_frame(df))
    alt = _scores(run_frame(altered))
    keep = df.loc[past & df["success"], "event_id"]
    pd.testing.assert_frame_equal(base.loc[keep], alt.loc[keep], check_dtype=False)


def test_same_timestamp_events_do_not_see_each_other():
    df = synthetic_events(1, n=4, users=1)
    df["ts"] = pd.Timestamp("2020-03-01 12:00")
    df["success"] = True
    out = run_frame(df)
    assert (out["u_n"] == 0).all()
    assert (out["p_users"] == 0).all()
