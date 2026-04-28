# DS5220 Data Project 3 — Bitcoin Price Tracker

## Sub-projects

| Sub-project | Link |
|---|---|
| Data Ingestion Pipeline | [ingestion/](ingestion/) |
| Integration API | [api/](api/) |

---

## Data Source

**CoinGecko Free API** — fetches Bitcoin (BTC) price in USD plus 24-hour change percentage, market cap, and trading volume. No API key required.

The source was chosen because Bitcoin price is a continuously changing, publicly available dataset well suited to a time-series pipeline. The free CoinGecko API is reliable, requires no credentials, and returns all needed fields in a single request.

---

## Ingestion Cadence & Storage Schema

**Cadence:** every **15 minutes** via an EventBridge scheduled rule.

**DynamoDB table:** `btc-tracker-prices`

| Attribute | Type | Description |
|---|---|---|
| `coin` | String (PK) | Partition key — always `"bitcoin"` |
| `timestamp` | Number (SK) | Unix timestamp (seconds) of the sample |
| `price_usd` | Number | BTC price in USD at sample time |
| `price_change_24h` | Number | 24-hour percentage price change |
| `market_cap_usd` | Number | Market cap in USD |
| `volume_24h` | Number | 24-hour trading volume in USD |
| `fetch_time` | String | ISO-8601 datetime string (human-readable) |
| `ttl` | Number | DynamoDB TTL — records auto-expire after 30 days |

---

## API Resources

Base URL: `https://yz45whdmub.execute-api.us-east-1.amazonaws.com/api/`

### `GET /`
Returns the project description and list of available resources for Discord bot discovery.

### `GET /current`
Returns the most recently ingested BTC price as a human-readable sentence, including the 24-hour change direction and percentage, and the sample timestamp.

Example:
> Bitcoin (BTC) is trading at $95,432.00 USD (+2.14% up in the past 24h). Sampled at: 2025-04-28T20:00:00+00:00

### `GET /trend`
Returns a 24-hour statistical summary computed over all samples in the last 24 hours: open price, current price, net percentage change, price range, and average.

Example:
> Bitcoin 24h trend (96 samples): Open $93,200.00 → Now $95,432.00 (+2.39%). Range: $92,800.00–$96,100.00 | Avg: $94,850.00.

### `GET /plot`
Returns the public HTTPS URL of a PNG chart showing BTC price over the last 30 days. The chart is regenerated and uploaded to S3 on every ingestion cycle (every 15 minutes), so the URL is stable and always points to fresh data.

Example:
> https://btc-tracker-plots-043623260141.s3.us-east-1.amazonaws.com/latest.png

---

## Architecture

```
EventBridge (rate 15 min)
    └─> Lambda: btc-tracker-ingest
            ├─> CoinGecko API  (fetch price)
            ├─> DynamoDB       (store record)
            └─> S3             (regenerate chart → latest.png)

API Gateway + Lambda (Chalice)
    ├─> GET /         → about + resources
    ├─> GET /current  → read latest DynamoDB record
    ├─> GET /trend    → query last 24h records, compute stats
    └─> GET /plot     → return stable S3 URL
```

---

## Deployment

### Prerequisites
- AWS CLI configured with credentials
- Terraform >= 1.3
- Python 3.11+, pip
- `pip install chalice boto3`

### 1 — Deploy infrastructure
```bash
cd terraform
terraform init
terraform apply
```

### 2 — Deploy Chalice API
```bash
./scripts/deploy_api.sh
```
This script reads the Terraform outputs, patches `.chalice/config.json`, and runs `chalice deploy`.

### 3 — Register with the course Discord bot
```
/register btctracker ygu6ax https://yz45whdmub.execute-api.us-east-1.amazonaws.com/api/
```

---

## Stretch Goals

- **TTL on DynamoDB items** — records auto-expire after 30 days so the table doesn't grow unbounded.
- **Render-on-write plot** — the ingest Lambda regenerates the chart at a fixed S3 key so the `/plot` endpoint is a cheap URL lookup with no Lambda cold-start overhead.
- **Stable plot URL** — the chart key never changes (`latest.png`), so the Discord bot can embed it directly.
