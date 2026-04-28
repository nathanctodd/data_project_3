# Sub-project 2 — Integration API

A Chalice (API Gateway + Lambda) app that exposes the Bitcoin price data collected by the ingestion pipeline.

## Architecture

```
API Gateway + Lambda (Chalice)
    ├─> GET /         → about + resource list (Discord bot discovery)
    ├─> GET /current  → most recent BTC price from DynamoDB
    ├─> GET /trend    → 24-hour statistics from DynamoDB
    └─> GET /plot     → public S3 URL of the 30-day price chart
```

## Resources

| Endpoint | Returns |
|---|---|
| `GET /` | `{ "about": "...", "resources": ["current", "trend", "plot"] }` |
| `GET /current` | Latest BTC price + 24h change as a sentence |
| `GET /trend` | 24h open/close/range/avg/pct-change summary |
| `GET /plot` | Public HTTPS URL of `latest.png` in S3 |

## Configuration

`.chalice/config.json` — set these environment variables to match your Terraform outputs:

| Variable | Value |
|---|---|
| `DYNAMODB_TABLE_NAME` | `btc-tracker-prices` |
| `S3_BUCKET_NAME` | from `terraform output s3_plots_bucket` |
| `S3_PLOT_KEY` | `latest.png` |

## Deployment

```bash
# Automatically reads terraform outputs and deploys:
./scripts/deploy_api.sh

# Or manually:
pip install -r requirements.txt
chalice deploy --stage dev
```
