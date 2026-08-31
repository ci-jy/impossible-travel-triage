# Scoring an Entra ID sign-in export

`mappings/entra_signin.json` maps the columns of the Entra ID portal CSV export onto the canonical schema: `Date (UTC)`, `User ID`, `IP address`, `Location` (as `City, State, Country`), `Autonomous system number`, `Status`, `User agent`, `Operating System`, `Browser`, `Request ID`.

For other exports (Graph `signIns` JSON flattened to CSV, SIEM exports), copy the file and edit it. Each canonical field names its source column.

```bash
python3 -m traveltriage fetch --out data/ --geonames-only     # city coordinates only, a few seconds
python3 scripts/make_entra_example.py                          # regenerate the example export
python3 -m traveltriage score --input examples/entra_signins_example.csv \
    --mapping mappings/entra_signin.json --model results/model.json --out alerts.csv
```

`examples/entra_signins_example.csv` is a synthetic 300-user export.

## Output

`alerts.csv` has one row per alert or rule hit, with `score`, `verdict` (`alert` or `auto-closed rule alert`), `reasons`, and the original `Request ID` as `source_id`.

`results/model.json` holds the fitted weights and threshold. `results/rule_thresholds.json` has both the geo-velocity thresholds and the score threshold, for re-implementing them in a SIEM.

The threshold was calibrated on RBA. Population priors scale with the number of users, so recalibrate `threshold` in `results/model.json` to a budget on your own tenant's history.

## Command options

`fetch`

- `--out` (default `data/`)
- `--users` random users to sample, plus all takeover users (default 200000)
- `--geonames-only` only fetch the GeoNames cities table

`evaluate`

- `--data` (default `data/`)
- `--budget-per-10k` alerts per 10k sign-ins (default 10)
- `--out` (default `results/eval.json`)
- `--calib-start`, `--test-start` window boundaries (defaults `2020-04-01`, `2020-08-01`)
- `--docs` where to write the alert page (default `docs`)

`score`

- `--input` sign-in CSV (required)
- `--mapping` column mapping JSON
- `--model` (default `results/model.json`)
- `--cities` (default `data/cities.parquet`); without it, rule hits need `lat`/`lon` columns in the input
- `--out` (default `alerts.csv`)
- `--all` write every successful sign-in, not only alerts
