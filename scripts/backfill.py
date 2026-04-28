"""
backfill.py — Load 30 days of historical BTC price data into DynamoDB.

Uses the CoinGecko /coins/{id}/market_chart endpoint (free, no API key).
Run this once after `terraform apply` to seed the table so the plot looks good
before the 15-minute ingest schedule has had time to accumulate data.

Usage:
    python3 scripts/backfill.py

The script reads TABLE_NAME and AWS_REGION from environment variables if set,
otherwise falls back to the defaults that match terraform.tfvars.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
import requests
from boto3.dynamodb.conditions import Key

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
COIN_ID = "bitcoin"
DAYS = 30

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "btc-tracker-prices")
REGION = os.environ.get("AWS_REGION", "us-east-1")
LAMBDA_NAME = os.environ.get("LAMBDA_FUNCTION_NAME", "btc-tracker-ingest")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_decimal(value: float) -> Decimal:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def fetch_historical_data(days: int = 30) -> list[dict]:
    """
    Fetch `days` of hourly BTC/USD price, market cap, and volume from CoinGecko.
    Returns a list of dicts ready to write to DynamoDB.
    """
    params = {"vs_currency": "usd", "days": days}
    logger.info("Fetching %d days of historical BTC data from CoinGecko...", days)

    try:
        resp = requests.get(COINGECKO_CHART_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("CoinGecko request timed out")
        raise
    except requests.exceptions.HTTPError as exc:
        logger.error("CoinGecko HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise
    except requests.exceptions.RequestException as exc:
        logger.error("Network error: %s", exc)
        raise

    try:
        payload = resp.json()
        prices = payload["prices"]           # [[ts_ms, price], ...]
        market_caps = payload["market_caps"] # [[ts_ms, mcap], ...]
        volumes = payload["total_volumes"]   # [[ts_ms, vol], ...]
    except (KeyError, ValueError) as exc:
        logger.error("Unexpected CoinGecko response shape: %s", exc)
        raise

    logger.info("Received %d price points from CoinGecko", len(prices))

    # Zip the three arrays by index (CoinGecko returns them aligned)
    records = []
    for i, (ts_ms, price) in enumerate(prices):
        ts_sec = int(ts_ms / 1000)
        mcap = market_caps[i][1] if i < len(market_caps) else 0.0
        vol  = volumes[i][1]     if i < len(volumes)     else 0.0
        fetch_time = datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()

        records.append({
            "coin": COIN_ID,
            "timestamp": ts_sec,
            "price_usd": _safe_decimal(price),
            "price_change_24h": Decimal("0"),   # not available in bulk history
            "market_cap_usd": _safe_decimal(mcap),
            "volume_24h": _safe_decimal(vol),
            "fetch_time": fetch_time,
            "ttl": ts_sec + 35 * 24 * 3600,    # keep historical records a bit longer
        })

    return records


def write_records(records: list[dict]) -> tuple[int, int]:
    """
    Batch-write records to DynamoDB, skipping any that already exist.
    Returns (written, skipped) counts.
    """
    table = dynamodb.Table(TABLE_NAME)
    written = 0
    skipped = 0
    errors = 0

    logger.info("Checking for existing records and writing %d items to '%s'...", len(records), TABLE_NAME)

    # Fetch existing timestamps in one query to avoid per-item GetItem calls
    try:
        existing_resp = table.query(
            KeyConditionExpression=Key("coin").eq(COIN_ID),
            ProjectionExpression="#ts",
            ExpressionAttributeNames={"#ts": "timestamp"},
        )
        existing_ts = {int(item["timestamp"]) for item in existing_resp.get("Items", [])}
        logger.info("Found %d existing records in table — will skip duplicates", len(existing_ts))
    except Exception as exc:
        logger.warning("Could not pre-fetch existing timestamps (%s) — will write all records", exc)
        existing_ts = set()

    # DynamoDB batch_writer handles 25-item batches and retries automatically
    try:
        with table.batch_writer() as batch:
            for record in records:
                ts = int(record["timestamp"])
                if ts in existing_ts:
                    skipped += 1
                    continue
                try:
                    batch.put_item(Item=record)
                    written += 1
                    if written % 50 == 0:
                        logger.info("  ... written %d / %d", written, len(records) - skipped)
                except Exception as exc:
                    logger.error("Failed to write record ts=%d: %s", ts, exc)
                    errors += 1
    except Exception as exc:
        logger.error("Batch write session failed: %s", exc, exc_info=True)
        raise

    logger.info("Batch write complete: written=%d  skipped=%d  errors=%d", written, skipped, errors)
    return written, skipped


def trigger_plot_regeneration():
    """Invoke the ingest Lambda so it regenerates the plot from the newly seeded data."""
    logger.info("Invoking '%s' to regenerate the S3 plot...", LAMBDA_NAME)
    try:
        response = lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=b'{"source": "backfill"}',
        )
        status = response.get("StatusCode")
        payload = response["Payload"].read().decode()
        if status == 200:
            logger.info("Lambda invocation succeeded: %s", payload)
        else:
            logger.warning("Lambda returned status %s: %s", status, payload)
    except Exception as exc:
        logger.error("Could not invoke Lambda '%s': %s", LAMBDA_NAME, exc)
        logger.info("You can regenerate the plot manually:")
        logger.info(
            "  aws lambda invoke --function-name %s --payload '{}' "
            "--cli-binary-format raw-in-base64-out /tmp/out.json",
            LAMBDA_NAME,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=== BTC DynamoDB Backfill ===")
    logger.info("Table : %s", TABLE_NAME)
    logger.info("Region: %s", REGION)

    # 1 — fetch
    try:
        records = fetch_historical_data(days=DAYS)
    except Exception as exc:
        logger.error("Aborting: could not fetch historical data (%s)", exc)
        sys.exit(1)

    if not records:
        logger.error("No records returned from CoinGecko — aborting")
        sys.exit(1)

    logger.info(
        "Date range: %s  →  %s",
        records[0]["fetch_time"],
        records[-1]["fetch_time"],
    )

    # 2 — write
    try:
        written, skipped = write_records(records)
    except Exception as exc:
        logger.error("Aborting: batch write failed (%s)", exc)
        sys.exit(1)

    logger.info("Backfill complete: %d new records written, %d duplicates skipped", written, skipped)

    # 3 — regenerate plot
    trigger_plot_regeneration()

    logger.info("=== Done. The plot should be updated within ~30 seconds. ===")


if __name__ == "__main__":
    main()
