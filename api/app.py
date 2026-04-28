"""
Bitcoin Tracker — Integration API (Chalice)

Resources
---------
GET /          → about + resource list (Discord bot discovery)
GET /current   → most recent BTC price
GET /trend     → 24-hour price statistics
GET /plot      → public URL of the 7-day price chart in S3
"""

import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from chalice import Chalice

# ---------------------------------------------------------------------------
# App + logging
# ---------------------------------------------------------------------------
app = Chalice(app_name="btc-tracker-api")
app.log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration — values injected via .chalice/config.json environment_variables
# ---------------------------------------------------------------------------
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "btc-tracker-prices")
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
S3_PLOT_KEY = os.environ.get("S3_PLOT_KEY", "latest.png")
REGION = os.environ.get("AWS_REGION", "us-east-1")
COIN_ID = "bitcoin"

dynamodb = boto3.resource("dynamodb", region_name=REGION)


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _to_float(value) -> float:
    """Safely convert a DynamoDB Decimal (or any numeric) to float."""
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_latest_record() -> dict | None:
    """Return the single most-recent record for COIN_ID, or None if the table is empty."""
    table = dynamodb.Table(TABLE_NAME)
    app.log.info("Querying DynamoDB table '%s' for latest record (coin=%s)", TABLE_NAME, COIN_ID)

    try:
        response = table.query(
            KeyConditionExpression=Key("coin").eq(COIN_ID),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            app.log.warning("DynamoDB returned 0 items for coin=%s — table may be empty", COIN_ID)
            return None
        app.log.info(
            "Latest record: timestamp=%s price=$%.2f",
            items[0].get("timestamp"),
            _to_float(items[0].get("price_usd", 0)),
        )
        return items[0]
    except Exception as exc:
        app.log.error("DynamoDB query (latest) failed: %s", str(exc), exc_info=True)
        raise


def get_recent_records(hours: int = 24) -> list:
    """Return all records for COIN_ID in the last `hours` hours, sorted ascending."""
    table = dynamodb.Table(TABLE_NAME)
    cutoff = int(time.time()) - hours * 3600

    app.log.info(
        "Querying DynamoDB table '%s' for records in last %d hours (cutoff ts=%d)",
        TABLE_NAME, hours, cutoff,
    )

    try:
        response = table.query(
            KeyConditionExpression=Key("coin").eq(COIN_ID) & Key("timestamp").gte(cutoff),
            ScanIndexForward=True,
        )
        items = response.get("Items", [])
        app.log.info("DynamoDB returned %d records for last %dh window", len(items), hours)
        return items
    except Exception as exc:
        app.log.error("DynamoDB query (recent %dh) failed: %s", hours, str(exc), exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Zone apex — returns project description and resource names for the Discord bot."""
    app.log.info("GET / called")
    return {
        "about": (
            "Tracks Bitcoin (BTC) price in USD every 15 minutes via CoinGecko. "
            "Provides current price, 24-hour trend statistics, and a 7-day price chart."
        ),
        "resources": ["current", "trend", "plot"],
    }


@app.route("/current")
def current():
    """Return the most recently ingested BTC price as a human-readable string."""
    app.log.info("GET /current called")
    try:
        record = get_latest_record()
        if record is None:
            return {"response": "No data available yet — check back after the first ingestion cycle."}

        price = _to_float(record["price_usd"])
        change = _to_float(record.get("price_change_24h", 0.0))
        fetch_time = record.get("fetch_time", "unknown")

        sign = "+" if change >= 0 else ""
        direction = "up" if change >= 0 else "down"

        response = (
            f"Bitcoin (BTC) is trading at ${price:,.2f} USD "
            f"({sign}{change:.2f}% {direction} in the past 24h). "
            f"Sampled at: {fetch_time}"
        )
        app.log.info("Returning current price: $%.2f (24h change: %.2f%%)", price, change)
        return {"response": response}

    except Exception as exc:
        app.log.error("Error in GET /current: %s", str(exc), exc_info=True)
        return {"response": f"Error retrieving current price: {exc}"}


@app.route("/trend")
def trend():
    """Return 24-hour price statistics: open, close, min, max, average, and net change."""
    app.log.info("GET /trend called")
    try:
        items = get_recent_records(hours=24)
        if not items:
            return {
                "response": (
                    "Not enough data for trend analysis yet. "
                    "Check back after a few ingestion cycles have run."
                )
            }

        prices = [_to_float(item["price_usd"]) for item in items]
        n = len(prices)
        first_price = prices[0]
        last_price = prices[-1]
        delta = last_price - first_price
        pct_change = (delta / first_price) * 100 if first_price else 0.0
        min_price = min(prices)
        max_price = max(prices)
        avg_price = sum(prices) / n

        sign = "+" if delta >= 0 else ""
        response = (
            f"Bitcoin 24h trend ({n} samples): "
            f"Open ${first_price:,.2f} → Now ${last_price:,.2f} "
            f"({sign}{pct_change:.2f}%). "
            f"Range: ${min_price:,.2f}–${max_price:,.2f} | Avg: ${avg_price:,.2f}."
        )
        app.log.info(
            "Trend computed over %d samples: open=$%.2f now=$%.2f pct=%.2f%%",
            n, first_price, last_price, pct_change,
        )
        return {"response": response}

    except Exception as exc:
        app.log.error("Error in GET /trend: %s", str(exc), exc_info=True)
        return {"response": f"Error computing trend: {exc}"}


@app.route("/plot")
def plot():
    """Return the public S3 URL of the latest 7-day BTC price chart (rendered on write)."""
    app.log.info("GET /plot called")

    if not S3_BUCKET:
        app.log.error("S3_BUCKET_NAME environment variable is not configured")
        return {"response": "Plot not configured (missing S3_BUCKET_NAME env var)."}

    plot_url = f"https://{S3_BUCKET}.s3.{REGION}.amazonaws.com/{S3_PLOT_KEY}"
    app.log.info("Returning plot URL: %s", plot_url)
    return {"response": plot_url}
