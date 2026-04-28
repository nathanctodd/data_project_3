# Sub-project 1 — Data Ingestion Pipeline

Continuously fetches Bitcoin (BTC) price data from the CoinGecko API every 15 minutes and stores it in DynamoDB. After each write it regenerates a 30-day price chart and uploads it to a public S3 bucket.

## Architecture

```
EventBridge (rate 15 min)
    └─> Lambda: btc-tracker-ingest
            ├─> CoinGecko API  (fetch current price)
            ├─> DynamoDB       (store timestamped record)
            └─> S3             (regenerate latest.png chart)
```

## Components

| Path | Description |
|---|---|
| `terraform/` | All AWS infrastructure — DynamoDB, S3 buckets, IAM, Lambda, EventBridge rule |
| `lambda/handler.py` | Lambda function: fetch → store → plot |
| `lambda/requirements.txt` | Python dependencies (`requests`, `matplotlib`) |

## DynamoDB Schema

**Table:** `btc-tracker-prices`

| Attribute | Type | Description |
|---|---|---|
| `coin` | String (PK) | Partition key — always `"bitcoin"` |
| `timestamp` | Number (SK) | Unix timestamp (seconds) of the sample |
| `price_usd` | Number | BTC price in USD |
| `price_change_24h` | Number | 24-hour percentage price change |
| `market_cap_usd` | Number | Market cap in USD |
| `volume_24h` | Number | 24-hour trading volume in USD |
| `fetch_time` | String | ISO-8601 datetime (human-readable) |
| `ttl` | Number | Auto-expires after 30 days |

## Deployment

```bash
cd terraform
terraform init
terraform apply
```

To seed 30 days of historical data immediately after first deploy:

```bash
python3 ../scripts/backfill.py
```
