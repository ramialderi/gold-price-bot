
#!/usr/bin/env python3
"""
gold_alert.py
--------------
Telegram bot that notifies whenever the gold spot price changes.

Primary price source:
    xaus.com

Fallback price source:
    gold-api.com

Both sources ultimately provide the 24k gold spot price. The script
normalizes the result to USD per gram before calculating 18k/21k/22k/24k.

The script:
- Retries temporary API failures.
- Falls back to a second gold-price API if the primary fails.
- Never overwrites the saved state when no valid price was obtained.
- Sends a Telegram notification when the price changes.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests


# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------

PRIMARY_GOLD_API_URL = "https://xaus.com/api/v1/spot"

# Gold API returns XAU price in USD per troy ounce.
FALLBACK_GOLD_API_URL = "https://api.gold-api.com/price/XAU"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# One troy ounce = 31.1034768 grams.
TROY_OUNCE_IN_GRAMS = 31.1034768

MAX_API_ATTEMPTS = 4
API_TIMEOUT = 15

RETRYABLE_STATUS_CODES = {
    429,
    500,
    502,
    503,
    504,
}


# ---------------------------------------------------------------------------
# Karat purity
# ---------------------------------------------------------------------------

KARAT_FACTORS = {
    24: 1.0,
    22: 22 / 24,
    21: 21 / 24,
    18: 18 / 24,
}


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def get_with_retries(url: str, params=None):
    """
    GET a URL with retries for temporary HTTP/network failures.

    Returns the successful requests.Response.

    Raises:
        RuntimeError: if all attempts fail.
        requests.HTTPError: for non-retryable HTTP errors.
    """

    last_error = None

    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=API_TIMEOUT,
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(
                    f"HTTP {response.status_code}"
                )

                if attempt < MAX_API_ATTEMPTS:
                    wait_seconds = 2 ** (attempt - 1)

                    print(
                        f"API returned HTTP {response.status_code}; "
                        f"retrying in {wait_seconds}s "
                        f"(attempt {attempt}/{MAX_API_ATTEMPTS})...",
                        file=sys.stderr,
                    )

                    time.sleep(wait_seconds)
                    continue

                raise last_error

            response.raise_for_status()
            return response

        except requests.Timeout as exc:
            last_error = exc

            if attempt < MAX_API_ATTEMPTS:
                wait_seconds = 2 ** (attempt - 1)

                print(
                    f"API request timed out; "
                    f"retrying in {wait_seconds}s "
                    f"(attempt {attempt}/{MAX_API_ATTEMPTS})...",
                    file=sys.stderr,
                )

                time.sleep(wait_seconds)
                continue

        except requests.ConnectionError as exc:
            last_error = exc

            if attempt < MAX_API_ATTEMPTS:
                wait_seconds = 2 ** (attempt - 1)

                print(
                    f"API connection failed; "
                    f"retrying in {wait_seconds}s "
                    f"(attempt {attempt}/{MAX_API_ATTEMPTS})...",
                    file=sys.stderr,
                )

                time.sleep(wait_seconds)
                continue

        except requests.HTTPError:
            # Non-retryable HTTP error.
            raise

    raise RuntimeError(
        f"Request failed after {MAX_API_ATTEMPTS} attempts: "
        f"{last_error}"
    )


# ---------------------------------------------------------------------------
# Primary source: XAUS
# ---------------------------------------------------------------------------

def fetch_from_xaus() -> float:
    """
    Fetch 24k gold price from XAUS.

    Returns:
        USD per gram.
    """

    params = {
        "currency": "USD",
        "unit": "gram",
        "compact": 1,
    }

    response = get_with_retries(
        PRIMARY_GOLD_API_URL,
        params=params,
    )

    data = response.json()

    if "per_gram_usd" not in data:
        raise ValueError(
            "XAUS response is missing 'per_gram_usd': "
            f"{data}"
        )

    price = float(data["per_gram_usd"])

    if price <= 0:
        raise ValueError(
            f"XAUS returned invalid price: {price}"
        )

    return price


# ---------------------------------------------------------------------------
# Fallback source: Gold API
# ---------------------------------------------------------------------------

def fetch_from_gold_api() -> float:
    """
    Fetch 24k gold price from Gold API.

    Gold API returns the XAU price in USD per troy ounce.

    Converts:
        USD/troy ounce -> USD/gram

    Returns:
        USD per gram.
    """

    response = get_with_retries(
        FALLBACK_GOLD_API_URL
    )

    data = response.json()

    if "price" not in data:
        raise ValueError(
            "Gold API response is missing 'price': "
            f"{data}"
        )

    price_per_troy_ounce = float(data["price"])

    if price_per_troy_ounce <= 0:
        raise ValueError(
            f"Gold API returned invalid price: "
            f"{price_per_troy_ounce}"
        )

    price_per_gram = (
        price_per_troy_ounce / TROY_OUNCE_IN_GRAMS
    )

    if price_per_gram <= 0:
        raise ValueError(
            f"Converted gold price is invalid: "
            f"{price_per_gram}"
        )

    return price_per_gram


# ---------------------------------------------------------------------------
# Combined price fetch
# ---------------------------------------------------------------------------

def fetch_price_per_gram_24k_usd() -> float:
    """
    Fetch 24k gold price per gram.

    Uses XAUS first. If XAUS fails, automatically falls back
    to Gold API.
    """

    print("Fetching gold price from primary source: XAUS...")

    try:
        price = fetch_from_xaus()

        print(
            f"Primary source OK: "
            f"{price:.3f} USD/g"
        )

        return price

    except Exception as primary_error:
        print(
            f"Primary source failed: {primary_error}",
            file=sys.stderr,
        )

    print(
        "Trying fallback gold-price source: Gold API..."
    )

    try:
        price = fetch_from_gold_api()

        print(
            f"Fallback source OK: "
            f"{price:.3f} USD/g"
        )

        return price

    except Exception as fallback_error:
        raise RuntimeError(
            "Both gold-price APIs failed.\n"
            f"XAUS error: {primary_error}\n"
            f"Gold API error: {fallback_error}"
        ) from fallback_error


# ---------------------------------------------------------------------------
# Price calculations
# ---------------------------------------------------------------------------

def karat_prices(
    price_24k_per_gram: float,
    karats,
) -> dict:
    """Calculate prices for requested karats."""

    return {
        k: round(
            price_24k_per_gram * KARAT_FACTORS[k],
            3,
        )
        for k in karats
    }


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state(path: str):
    if not os.path.exists(path):
        return None

    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, price_24k: float):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "price_24k_usd_per_gram": price_24k,
                "checked_at_utc": (
                    datetime.now(timezone.utc)
                    .isoformat()
                ),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
) -> None:

    url = TELEGRAM_API.format(token=token)

    response = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_message(
    karats,
    prices,
    previous_price_24k=None,
    current_price_24k=None,
) -> str:

    lines = [
        "🟡 تحديث سعر الذهب (سبوت)"
    ]

    if previous_price_24k is not None:

        diff = (
            current_price_24k
            - previous_price_24k
        )

        pct = (
            (diff / previous_price_24k) * 100
            if previous_price_24k
            else 0
        )

        arrow = (
            "⬆️"
            if diff > 0
            else (
                "⬇️"
                if diff < 0
                else "➖"
            )
        )

        lines.append(
            f"{arrow} تغيّر السعر: "
            f"{diff:+.3f}$ للغرام "
            f"({pct:+.2f}%)"
        )

    else:
        lines.append(
            "(أول قراءة — بداية المراقبة)"
        )

    lines.append("")
    lines.append(
        "السعر بالدولار لكل غرام:"
    )

    for k in sorted(
        karats,
        reverse=True,
    ):
        lines.append(
            f"  عيار {k}: "
            f"{prices[k]:.3f}$"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Gold spot price change-alert "
            "bot for Telegram."
        )
    )

    parser.add_argument(
        "--karats",
        default="21,24",
        help=(
            "Comma-separated karats to report, "
            "e.g. '21,24' or '18,21,22,24'"
        ),
    )

    parser.add_argument(
        "--min-change-percent",
        type=float,
        default=0.0,
        help=(
            "Only alert if price moved by at least "
            "this percent (default: 0 = any change)"
        ),
    )

    parser.add_argument(
        "--state",
        default="gold_state.json",
        help="Path to the state file",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print what would be sent instead "
            "of calling Telegram"
        ),
    )

    args = parser.parse_args()

    # ---------------------------------------------------------------
    # Validate karats
    # ---------------------------------------------------------------

    karats = [
        int(k.strip())
        for k in args.karats.split(",")
        if k.strip()
    ]

    for k in karats:
        if k not in KARAT_FACTORS:
            print(
                f"Error: unsupported karat {k}. "
                f"Supported: {sorted(KARAT_FACTORS)}",
                file=sys.stderr,
            )
            sys.exit(1)

    # ---------------------------------------------------------------
    # Fetch gold price
    # ---------------------------------------------------------------

    try:
        current_24k = (
            fetch_price_per_gram_24k_usd()
        )

    except Exception as exc:

        print(
            f"ERROR: Unable to obtain gold price.",
            file=sys.stderr,
        )

        print(
            str(exc),
            file=sys.stderr,
        )

        # Important:
        # Do NOT modify gold_state.json when both APIs fail.
        sys.exit(1)

    prices = karat_prices(
        current_24k,
        karats,
    )

    # ---------------------------------------------------------------
    # Load previous state
    # ---------------------------------------------------------------

    state = load_state(args.state)

    previous_24k = (
        state["price_24k_usd_per_gram"]
        if state
        else None
    )

    # ---------------------------------------------------------------
    # Determine whether to alert
    # ---------------------------------------------------------------

    should_alert = False

    if previous_24k is None:

        # First successful reading.
        should_alert = True

    else:

        pct_change = (
            abs(
                current_24k
                - previous_24k
            )
            / previous_24k
            * 100
            if previous_24k
            else 0
        )

        should_alert = (
            pct_change
            >= args.min_change_percent
            and current_24k != previous_24k
        )

    # ---------------------------------------------------------------
    # Console output
    # ---------------------------------------------------------------

    print(
        f"24k price: "
        f"{current_24k:.3f} USD/g | "
        f"previous: {previous_24k} | "
        f"should_alert={should_alert}"
    )

    for k in sorted(
        karats,
        reverse=True,
    ):
        print(
            f"  karat {k}: "
            f"{prices[k]:.3f} USD/g"
        )

    # ---------------------------------------------------------------
    # No price change
    # ---------------------------------------------------------------

    if not should_alert:
        print(
            "Price change is below the alert threshold. "
            "No notification sent."
        )
        return

    # ---------------------------------------------------------------
    # Save successful price
    # ---------------------------------------------------------------

    save_state(
        args.state,
        current_24k,
    )

    # ---------------------------------------------------------------
    # Build Telegram message
    # ---------------------------------------------------------------

    message = format_message(
        karats,
        prices,
        previous_price_24k=previous_24k,
        current_price_24k=current_24k,
    )

    # ---------------------------------------------------------------
    # Dry run
    # ---------------------------------------------------------------

    if args.dry_run:

        print(
            "--dry-run set, not sending. "
            "Message would be:"
        )

        print(message)

        return

    # ---------------------------------------------------------------
    # Telegram credentials
    # ---------------------------------------------------------------

    token = os.environ.get(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.environ.get(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:

        print(
            "Error: TELEGRAM_BOT_TOKEN "
            "and/or TELEGRAM_CHAT_ID not set.",
            file=sys.stderr,
        )

        sys.exit(1)

    # ---------------------------------------------------------------
    # Send alert
    # ---------------------------------------------------------------

    send_telegram_message(
        token,
        chat_id,
        message,
    )

    print("Alert sent.")


if __name__ == "__main__":
    main()
