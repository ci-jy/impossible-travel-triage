import numpy as np
import pandas as pd
import pytest

from traveltriage import pipeline

COORDS = {"NO": (59.9, 10.7), "US": (38.9, -77.0), "SE": (59.3, 18.1), "DE": (52.5, 13.4)}


def synthetic_events(seed: int = 0, n: int = 150, users: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    country = rng.choice(list(COORDS), n)
    df = pd.DataFrame(
        {
            "user_id": rng.integers(0, users, n).astype(str),
            "ts": pd.Timestamp("2020-03-01") + pd.to_timedelta(np.sort(rng.integers(0, 5 * 86400, n)), unit="s"),
            "ip": [f"10.{rng.integers(0, 3)}.{rng.integers(0, 4)}.{rng.integers(1, 255)}" for _ in range(n)],
            "country": country,
            "region": "-",
            "city": "-",
            "asn": rng.choice([100, 200, 300, 400, 500], n),
            "user_agent": rng.choice(["ua-a", "ua-b", "ua-c"], n),
            "os": rng.choice(["iOS 14", "Windows 10"], n),
            "success": rng.random(n) > 0.25,
            "is_takeover": False,
            "in_sample": rng.random(n) > 0.2,
        }
    )
    df["lat"] = [COORDS[c][0] for c in country]
    df["lon"] = [COORDS[c][1] for c in country]
    df["event_id"] = np.arange(1, n + 1)
    return df


def run_frame(df: pd.DataFrame) -> pd.DataFrame:
    con = pipeline.connect("512MB")
    pipeline.load_events(con, df)
    out = pipeline.run(con, None)
    con.close()
    return out


@pytest.fixture
def events():
    return synthetic_events()
