# Results

All numbers come from the test window in `results/eval.json`. 95% intervals are from a bootstrap that resamples users (1,000 draws).

## Data split

The RBA source has 4,304,857 users. `fetch --users 200000` keeps a random sample of 200,000 users plus every user with a labelled takeover (138 users, 140 successful takeover sign-ins).

Successful sign-ins are split into three windows (boundaries set by `--calib-start` and `--test-start`):

- warm-up: history only, nothing is scored
- calibration: 182,628 sampled sign-ins, 52 takeovers; weights and thresholds are set here
- test: 305,076 sampled sign-ins, 55 takeover sign-ins on 54 users

## At a budget of 10 alerts per 10k sign-ins

Threshold set on the calibration window.

| Detector | Caught | Recall (95% CI) | Alerts per 10k (95% CI) | Average precision |
|---|---|---|---|---|
| Geo-velocity rule (500 km, 800 km/h, 90 min) | 3 / 55 | 5.5% (0.0 to 12.5%) | 34.8 (22.5 to 56.4) | 0.0003 |
| History score, fitted weights (primary) | 16 / 55 | 29.1% (17.9 to 41.8%) | 11.7 (10.2 to 13.4) | 0.032 |
| History score, equal weights (no labels) | 20 / 55 | 36.4% (23.6 to 49.1%) | 12.8 (10.7 to 15.1) | 0.034 |
| ASN + IP-prefix history only | 16 / 55 | 29.1% (18.2 to 41.1%) | 14.7 (12.6 to 17.4) | 0.023 |

Primary score recall minus rule recall: +23.6 points (95% CI +10.7 to +36.4). The realised rate is 11.7 for a target of 10 because of drift between windows.

## At the rule's own alert rate

Threshold set to the rule's 30.8 alerts per 10k on the calibration window.

| Detector | Caught | Recall (95% CI) | Alerts per 10k |
|---|---|---|---|
| Geo-velocity rule | 3 / 55 | 5.5% (0.0 to 12.5%) | 34.8 |
| History score, fitted weights | 30 / 55 | 54.5% (40.7 to 67.3%) | 28.4 |
| History score, equal weights | 33 / 55 | 60.0% (47.3 to 72.7%) | 36.4 |

## Triage of rule alerts

Rule alerts on sampled users in the test window. Auto-close means the score is at or below the 10 per 10k threshold.

| Score | Rule alerts | Auto-closed (95% CI) | Takeovers the rule caught | Lost by auto-closing |
|---|---|---|---|---|
| Fitted weights | 1,061 | 97.7% (97.1 to 98.7%) | 3 | 2 |
| Equal weights | 1,061 | 98.1% (97.0 to 99.6%) | 3 | 1 |

## Why the rule misses takeovers

Of the 55 takeover sign-ins in the test window:

- 52 had no successful sign-in by the same user in the preceding 90 minutes, so the rule has nothing to compare against
- 12 had no prior sign-in at all
- 48 came from a country the user had never signed in from
- 50 came from an ASN the user had never signed in from

## Choosing the primary variant

Fitted weights are fit on the first half of the calibration window, and all variants are compared on its second half (26 takeovers). The fitted variant won there (6/26 vs 5/26 for equal weights), so it is reported as primary even though equal weights did better on the test window. With this few labels the two are statistically indistinguishable.
