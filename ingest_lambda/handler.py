"""
Bitcoin price ingestion Lambda.

Runs on a 15-minute EventBridge schedule:
  1. Fetches BTC/USD price + 24h stats from CoinGecko (free, no API key).
  2. Writes a timestamped record to DynamoDB.
  3. Queries the last 7 days of data and regenerates the S3 plot.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import boto3
import matplotlib
matplotlib.use("Agg")  # headless backend — must come before pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
from boto3.dynamodb.conditions import Key

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration (injected by Terraform as environment variables)
# ---------------------------------------------------------------------------
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COIN_ID = "bitcoin"
CURRENCY = "usd"

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "btc-tracker-prices")
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
S3_PLOT_KEY = os.environ.get("S3_PLOT_KEY", "latest.png")
REGION = os.environ.get("APP_AWS_REGION", os.environ.get("AWS_REGION", "us-east-1"))

# ---------------------------------------------------------------------------
# AWS clients (module-level for connection reuse across warm invocations)
# ---------------------------------------------------------------------------
dynamodb = boto3.resource("dynamodb", region_name=REGION)
s3_client = boto3.client("s3", region_name=REGION)


# ---------------------------------------------------------------------------
# Step 1 — fetch price from CoinGecko
# ---------------------------------------------------------------------------

def fetch_bitcoin_price() -> dict:
    """
    Fetch current BTC/USD price and 24-hour stats from the CoinGecko v3 API.
    Returns a dict with price_usd, price_change_24h, market_cap_usd, volume_24h.
    Raises on any network or parsing failure.
    """
    params = {
        "ids": COIN_ID,
        "vs_currencies": CURRENCY,
        "include_24hr_change": "true",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
    }

    logger.info("Fetching BTC price from CoinGecko API (url=%s, params=%s)", COINGECKO_URL, params)

    try:
        resp = requests.get(COINGECKO_URL, params=params, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("CoinGecko request timed out after 10 seconds")
        raise
    except requests.exceptions.HTTPError as exc:
        logger.error(
            "CoinGecko returned HTTP %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise
    except requests.exceptions.RequestException as exc:
        logger.error("Network error fetching BTC price: %s", str(exc))
        raise

    try:
        payload = resp.json()
        logger.info("CoinGecko raw response: %s", json.dumps(payload))

        coin_data = payload.get(COIN_ID)
        if not coin_data:
            raise ValueError(f"CoinGecko response missing key '{COIN_ID}': {payload}")

        price = float(coin_data[f"{CURRENCY}"])
        change_24h = float(coin_data.get(f"{CURRENCY}_24h_change", 0.0))
        market_cap = float(coin_data.get(f"{CURRENCY}_market_cap", 0.0))
        volume_24h = float(coin_data.get(f"{CURRENCY}_24h_vol", 0.0))

    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Failed to parse CoinGecko response: %s", str(exc), exc_info=True)
        raise

    logger.info(
        "BTC price fetched: price=$%.2f  24h_change=%.2f%%  market_cap=$%.0f  volume_24h=$%.0f",
        price, change_24h, market_cap, volume_24h,
    )
    return {
        "price_usd": price,
        "price_change_24h": change_24h,
        "market_cap_usd": market_cap,
        "volume_24h": volume_24h,
    }


# ---------------------------------------------------------------------------
# Step 2 — write record to DynamoDB
# ---------------------------------------------------------------------------

def _safe_decimal(value: float) -> Decimal:
    """Convert a float to Decimal, clamping NaN/Inf to 0 to avoid DynamoDB errors."""
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            logger.warning("Non-finite value %s replaced with 0", value)
            return Decimal("0")
        return d
    except InvalidOperation:
        logger.warning("Could not convert %r to Decimal, using 0", value)
        return Decimal("0")


def store_price_record(price_data: dict) -> dict:
    """Write a timestamped price record to DynamoDB. Raises on failure."""
    table = dynamodb.Table(TABLE_NAME)
    now_ts = int(time.time())
    fetch_time = datetime.now(timezone.utc).isoformat()
    ttl_ts = now_ts + 30 * 24 * 3600  # expire after 30 days

    item = {
        "coin": COIN_ID,
        "timestamp": now_ts,
        "price_usd": _safe_decimal(price_data["price_usd"]),
        "price_change_24h": _safe_decimal(round(price_data["price_change_24h"], 4)),
        "market_cap_usd": _safe_decimal(price_data["market_cap_usd"]),
        "volume_24h": _safe_decimal(price_data["volume_24h"]),
        "fetch_time": fetch_time,
        "ttl": ttl_ts,
    }

    logger.info(
        "Writing DynamoDB record to table '%s': coin=%s timestamp=%d price=$%.2f",
        TABLE_NAME, COIN_ID, now_ts, price_data["price_usd"],
    )

    try:
        table.put_item(Item=item)
        logger.info("DynamoDB PutItem succeeded for timestamp %d", now_ts)
    except Exception as exc:
        logger.error("DynamoDB PutItem failed: %s", str(exc), exc_info=True)
        raise

    return item


# ---------------------------------------------------------------------------
# Step 3 — query recent records for plotting
# ---------------------------------------------------------------------------

def query_recent_prices(hours: int = 168) -> list:
    """
    Query DynamoDB for BTC records in the last `hours` hours.
    Returns a list of items sorted ascending by timestamp.
    Raises on DynamoDB failure.
    """
    table = dynamodb.Table(TABLE_NAME)
    cutoff = int(time.time()) - hours * 3600

    logger.info(
        "Querying DynamoDB table '%s' for records since timestamp %d (%d hours back)",
        TABLE_NAME, cutoff, hours,
    )

    try:
        response = table.query(
            KeyConditionExpression=Key("coin").eq(COIN_ID) & Key("timestamp").gte(cutoff),
            ScanIndexForward=True,
        )
        items = response.get("Items", [])
        logger.info("DynamoDB query returned %d records", len(items))
        return items
    except Exception as exc:
        logger.error("DynamoDB Query failed: %s", str(exc), exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Step 4 — generate chart and upload to S3
# ---------------------------------------------------------------------------

def generate_and_upload_plot(items: list) -> str:
    """
    Build a BTC price line chart from `items` and upload as a PNG to S3.
    Returns the public HTTPS URL of the uploaded image.
    Raises on matplotlib or S3 failure.
    """
    if not items:
        logger.warning("No items provided for plot generation — skipping")
        return ""

    logger.info("Generating price chart for %d data points", len(items))

    try:
        timestamps = [
            datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc)
            for item in items
        ]
        prices = [float(item["price_usd"]) for item in items]

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(timestamps, prices, color="#F7931A", linewidth=2, label="BTC/USD")
        ax.fill_between(timestamps, prices, alpha=0.12, color="#F7931A")

        # Latest-price annotation
        if prices:
            ax.annotate(
                f"Latest: ${prices[-1]:,.0f}",
                xy=(timestamps[-1], prices[-1]),
                xytext=(-90, 20),
                textcoords="offset points",
                fontsize=10,
                fontweight="bold",
                color="#F7931A",
                arrowprops=dict(arrowstyle="->", color="#F7931A"),
            )

        ax.set_title("Bitcoin (BTC) Price — Last 30 Days (USD)", fontsize=15, fontweight="bold")
        ax.set_xlabel("Date / Time (UTC)", fontsize=11)
        ax.set_ylabel("Price (USD)", fontsize=11)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.xticks(rotation=40, ha="right")
        ax.grid(True, alpha=0.3, linestyle="--")
        ax.legend(loc="upper left")
        plt.tight_layout()

        tmp_path = "/tmp/btc_plot.png"
        plt.savefig(tmp_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Plot saved to %s", tmp_path)

    except Exception as exc:
        logger.error("matplotlib plot generation failed: %s", str(exc), exc_info=True)
        raise

    try:
        logger.info("Uploading plot to s3://%s/%s", S3_BUCKET, S3_PLOT_KEY)
        with open(tmp_path, "rb") as fh:
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=S3_PLOT_KEY,
                Body=fh,
                ContentType="image/png",
                CacheControl="no-cache, max-age=0",
            )
        plot_url = f"https://{S3_BUCKET}.s3.{REGION}.amazonaws.com/{S3_PLOT_KEY}"
        logger.info("Plot uploaded successfully: %s", plot_url)
        return plot_url
    except Exception as exc:
        logger.error("S3 PutObject failed: %s", str(exc), exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Main handler invoked by EventBridge every 15 minutes.
    Steps run sequentially; a failure in price fetching aborts the run.
    Failures in store/plot are logged but do not block each other.
    """
    logger.info("=== Bitcoin ingest Lambda invoked ===")
    logger.info("Event: %s", json.dumps(event, default=str))

    results = {
        "price_fetched": False,
        "record_stored": False,
        "plot_updated": False,
        "errors": [],
    }

    # --- Step 1: fetch ---
    try:
        price_data = fetch_bitcoin_price()
        results["price_fetched"] = True
        results["price_usd"] = price_data["price_usd"]
    except Exception as exc:
        msg = f"Price fetch failed: {exc}"
        logger.error(msg)
        results["errors"].append(msg)
        # Cannot proceed without data
        logger.info("Aborting run due to fetch failure. Results: %s", results)
        return {"statusCode": 500, "body": json.dumps(results)}

    # --- Step 2: store ---
    try:
        store_price_record(price_data)
        results["record_stored"] = True
    except Exception as exc:
        msg = f"DynamoDB store failed: {exc}"
        logger.error(msg)
        results["errors"].append(msg)
        # Continue — still try to update the plot from existing data

    # --- Step 3: regenerate plot ---
    try:
        if not S3_BUCKET:
            raise EnvironmentError("S3_BUCKET_NAME environment variable is not set")
        recent_items = query_recent_prices(hours=720)  # 30 days
        if recent_items:
            plot_url = generate_and_upload_plot(recent_items)
            results["plot_updated"] = True
            results["plot_url"] = plot_url
        else:
            logger.warning("No items found for plot — table may be empty on first run")
            results["errors"].append("No data found for plot generation")
    except Exception as exc:
        msg = f"Plot update failed: {exc}"
        logger.error(msg)
        results["errors"].append(msg)

    status_code = 200 if (results["price_fetched"] and results["record_stored"]) else 207
    logger.info("=== Run complete. status=%d results=%s ===", status_code, json.dumps(results))
    return {"statusCode": status_code, "body": json.dumps(results)}
