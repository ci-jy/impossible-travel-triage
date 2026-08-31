# Method

## Data

`fetch` downloads the [RBA login dataset](https://zenodo.org/record/6782156) (33M login attempts synthesized from a real SSO service, about 9 GB as CSV) and the GeoNames `cities1000` and `admin1CodesASCII` tables. In two DuckDB passes it keeps every user with a labelled takeover plus a deterministic hash-based random sample of users (`--users`).

Only the random sample is used for alert rates and population counts. Takeover users are over-represented in the sample, and counting them would make attacker infrastructure look common and alert rates look low.

## Geo-velocity rule

For each successful sign-in, look back over the same user's successful sign-ins in the previous 90 minutes and fire if one is at least 500 km away at an implied speed above 800 km/h. Failed sign-ins are ignored.

Cities are matched to GeoNames by country, name and region (52% of RBA sign-ins). Sign-ins without a city fall back to the population-weighted country centroid.

## History features

DuckDB window functions with frames ending 1 ms before the event (`RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`), so each sign-in only sees strictly earlier events:

- per user: prior successful sign-ins in total, and on the same ASN, /24 prefix (/48 for IPv6), country, user agent and OS
- per account: failed sign-ins in the prior 24 hours
- population: distinct sampled users already seen on the same ASN, prefix, country, user agent and OS (using first-seen markers per value and user), and distinct users with failed sign-ins from the same IP

## Score

Each attribute becomes a surprisal term:

```
-log((user count + α·p_pop) / (user sign-ins + α))
```

where `p_pop` is the smoothed share of sampled users already seen on that value. A cold-start user on a carrier ASN that a quarter of the population uses scores low; an established user on a never-seen hosting ASN scores high.

Terms used: `s_asn`, `s_prefix`, `s_country`, `s_ua`, `s_os`, `s_fail_user`, `s_ip_spray`.

Weights come from a class-balanced logistic loss with a small L2 penalty, solved with SciPy L-BFGS-B with every weight constrained to be non-negative. A negative weight would mean "more unusual is safer" and would make the reason codes misleading. The equal-weight variant uses no labels.

## Thresholds

The threshold is the score quantile that gives the requested alerts per 10k sign-ins (`--budget-per-10k`) on the calibration window. Everything reported is measured on the later test window.

## Reason codes

Kept alerts list the largest score contributions, for example `new ASN for user: AS3280 (1 of 126,402 sampled users)` or `8 failed sign-ins on account in prior 24h`. Auto-closed rule alerts list the familiarity that closed them, for example `ASN AS12392 seen 227x before for user`.

## Tests

```bash
python3 -m pytest -q
```

- `tests/test_no_lookahead.py`: Hypothesis property test. Builds random sign-in histories, shuffles every attribute of events after a random cut time, drops some, shuffles row order, and checks that all features, the rule flag and the score up to the cut are unchanged. A second test checks that events with the same timestamp never see each other.
- `tests/test_rule_and_scoring.py`: rule geometry, familiar vs new ASN scoring and reason codes, cold-start priors, budget thresholds.
- `tests/test_evaluate_e2e.py`: the full `evaluate` pipeline on a synthetic dataset.
- `tests/test_entra_mapping.py`: the Entra column mapping and the `score` command on the example export.
