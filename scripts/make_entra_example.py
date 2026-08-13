"""Generate examples/entra_signins_example.csv: a realistic Entra ID sign-in export.

Six users over three weeks. It contains the usual impossible-travel false
positives (mobile carrier egress geolocated to another country, a VPN exit in
Frankfurt, a cloud desktop in Amsterdam) and one takeover from a fresh
residential IP in the victim's own country that the geo-velocity rule cannot see.
All identifiers are random; IPs come from documentation-style ranges.
"""

from __future__ import annotations

import csv
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

random.seed(11)
OUT = Path(__file__).resolve().parents[1] / "examples" / "entra_signins_example.csv"

HOME = ("Oslo, Oslo, NO", 2119, "84.208.{}.{}")
CARRIER = ("Stockholm, Stockholm, SE", 3301, "78.67.{}.{}")
VPN = ("Frankfurt am Main, Hesse, DE", 9009, "185.156.{}.{}")
CLOUD = ("Amsterdam, North Holland, NL", 8075, "20.61.{}.{}")
FRESH = ("Bergen, Vestland, NO", 29695, "81.167.{}.{}")
UAS = {
    "win": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0 Safari/537.36",
            "Windows10", "Chrome 118.0"),
    "ios": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            "Ios 17", "Mobile Safari"),
    "avd": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0 Safari/537.36 Edg/118.0",
            "Windows10", "Edge 118.0"),
    "linux": ("Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0", "Linux", "Firefox 119.0"),
}


def ip(fmt: str, sub: int | None = None) -> str:
    return fmt.format(sub if sub is not None else random.randint(1, 250), random.randint(2, 250))


def row(user: str, upn: str, t: datetime, place, device: str, status: str = "Success", sub: int | None = None):
    ua, os_, br = UAS[device]
    return {
        "Date (UTC)": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "Request ID": str(uuid.UUID(int=random.getrandbits(128))),
        "User ID": user, "Username": upn, "IP address": ip(place[2], sub), "Location": place[0],
        "Autonomous system number": place[1], "Status": status, "User agent": ua,
        "Operating System": os_, "Browser": br, "Client app": "Browser",
    }


def main() -> None:
    start = datetime(2024, 3, 4, 7, 0)
    users = [(str(uuid.UUID(int=random.getrandbits(128))), f"user{i}@example.org") for i in range(6)]
    rows = []
    for d in range(21):
        day = start + timedelta(days=d)
        for i, (u, upn) in enumerate(users):
            if day.weekday() >= 5 and i % 2:
                continue
            t = day + timedelta(minutes=random.randint(0, 90))
            rows.append(row(u, upn, t, HOME, "win", sub=10 + i))
            if i in (0, 1):  # phone on the carrier network, egress geolocated to Stockholm
                rows.append(row(u, upn, t + timedelta(minutes=random.randint(5, 60)), CARRIER, "ios"))
            if i == 2 and d % 3 == 0:  # VPN egress in Frankfurt
                rows.append(row(u, upn, t + timedelta(minutes=random.randint(10, 50)), VPN, "win"))
            if i == 3 and d % 2 == 0:  # cloud desktop in Amsterdam
                rows.append(row(u, upn, t + timedelta(minutes=random.randint(10, 50)), CLOUD, "avd"))
    # Takeover of user4: password spraying, then a success from a fresh residential IP in Norway.
    victim, vupn = users[4]
    t0 = start + timedelta(days=17, hours=13)
    for k in range(6):
        rows.append(row(victim, vupn, t0 + timedelta(minutes=3 * k), FRESH, "linux", status="Failure", sub=77))
    rows.append(row(victim, vupn, t0 + timedelta(minutes=25), FRESH, "linux", sub=77))
    rows.sort(key=lambda r: r["Date (UTC)"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} sign-ins)")


if __name__ == "__main__":
    main()
