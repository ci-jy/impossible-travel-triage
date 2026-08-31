# Impossible-travel alert triage

A score built from each user's own sign-in history caught 30 of 55 account takeovers on the RBA login dataset. The usual geo-velocity "impossible travel" rule caught 3 of them, at roughly the same number of alerts. The rule mostly can't help it: 52 of the 55 takeovers had no sign-in by the same user in the previous 90 minutes, so there was no earlier location to compute a travel speed from.

That turned the project around. I started out trying to auto-close the rule's false alarms (VPN exits, mobile carriers that geolocate to a far-off hub, cloud desktops), and that part works too: the score could close 97.7% of the 1,061 rule alerts in the test window, each with a reason like `ASN AS12392 seen 227x before for user`. But it also closed 2 of the 3 takeovers the rule did catch. The bigger win was the takeovers the rule never saw.

| Detector (test window, 55 takeovers) | Caught | Alerts per 10k sign-ins |
|---|---|---|
| Geo-velocity rule (500 km, 800 km/h, 90 min) | 3 | 34.8 |
| History score, budget of 10 per 10k | 16 | 11.7 |
| History score, at the rule's alert rate | 30 | 28.4 |

![Takeover recall vs alert budget: history score vs geo-velocity rule](docs/recall_vs_budget.png)

Full tables with confidence intervals are in [docs/results.md](docs/results.md).

## How the score works

For every successful sign-in, DuckDB window functions count how often that user has signed in before from the same ASN, the same /24 prefix, country, user agent and OS, plus failed sign-ins on the account in the last 24 hours. Each of those becomes a surprisal term: how unexpected is this value for this user? A brand new user has no history, so the counts are smoothed with how common the value is across all sampled users. A first login from the national mobile carrier is boring; a first login from an ASN nobody uses is not.

The terms get non-negative weights from a logistic regression, and the alert threshold is a budget (N alerts per 10k sign-ins) set on an earlier time window and then checked on a later one. There is also an equal-weight version that uses no labels at all. It did slightly better on the test window (20 of 55 vs 16 at the 10 per 10k budget), which tells you how little the fitted weights add with this few labels.

The hard part was look-ahead. It is very easy for a window frame to include the current event or one with the same timestamp, and then every number is too good. The frames end 1 ms before each event, and a Hypothesis property test shuffles and drops everything after a random cut time and checks that no feature, rule flag or score before the cut changes. Widening the frame makes it fail.

The dataset is 33M login attempts, about 9 GB as CSV, so `fetch` makes two DuckDB passes and keeps a random sample of users plus every user with a labelled takeover. Alert rates and population counts only use the random sample, otherwise the over-sampled attackers would make their own infrastructure look common. More detail is in [docs/method.md](docs/method.md).

## Running it

```bash
pip install -r requirements.txt
python3 -m traveltriage fetch --out data/ --users 200000
python3 -m traveltriage evaluate --data data/ --budget-per-10k 10 --out results/eval.json
python3 -m pytest -q
```

`evaluate` also writes [docs/index.html](docs/index.html), a page of rule alerts next to the scorer's keep or auto-close verdict. To score an Entra ID sign-in export instead, there is a column mapping and a synthetic example export; see [docs/entra-mapping.md](docs/entra-mapping.md).

## Known issues

- Only 55 takeover sign-ins in the test window, so recall intervals are about ±12 points wide. The gap to the rule is clear, the gap between score variants is not.
- I changed the protocol once after seeing test results. The first fitted model was unconstrained, learned negative weights and reached 12.7% recall; the sign constraint and choosing the variant on calibration data came afterwards. The equal-weight score didn't change between runs.
- RBA cities come from synthetic IPs, and only 52% of sign-ins matched a GeoNames city. Its takeovers mostly come from new countries, so the weights lean on country novelty, and a takeover from a fresh residential ASN in the victim's own country scores below the threshold (user5 in the example export).
- Thresholds don't transfer between tenants, since the priors scale with user count. Recalibrate on your own history before trusting the budget.

Data: the [RBA dataset](https://zenodo.org/record/6782156) by Wiefling et al. (CC BY 4.0) and [GeoNames](https://www.geonames.org/) cities (CC BY 4.0). Code is MIT licensed.

Project period: 2026-08-13 to 2026-09-02.
