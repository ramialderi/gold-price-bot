#!/usr/bin/env python3
"""
gold_alert.py
--------------
Telegram bot that notifies whenever the gold spot price changes —
built for local jewelers (الصاغة) who need to know as soon as the
price per gram moves, not just when it crosses a fixed target.

Data source: xaus.com's free XAU/USD spot API (no API key required).
Gold spot price is always quoted for pure (24k) gold; this script
converts it to the karats jewelers actually price against (21k is the
common Middle East retail standard, alongside 18k and 22k).

Unlike price_alert.py (which is stateless — one check, one maybe-alert),
this script needs to remember the LAST price it saw to detect a change.
That state is kept in a small JSON file (gold_state.json) that the
GitHub Actions workflow commits back to the repo after every run — see
.github/workflows/gold-alert.yml.

Usage:
    python gold_alert.py --karats 21,24 --state gold_state.json

    # Only alert if the price moved by at least 0.2%:
    python gold_alert.py --karats 21 --min-change-percent 0.2

Requires two environment variables (GitHub Actions secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

GOLD_API_URL = "https://xaus.com/api/v1/spot"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Karat purity as a fraction of pure (24k) gold.
KARAT_FACTORS = {24: 1.0, 22: 22 / 24, 21: 21 / 24, 18: 18 / 24}


def fetch_price_per_gram_24k_usd() -> float:
    """Return the current spot price of pure (24k) gold, per gram, in USD."""
    resp = requests.get(GOLD_API_URL, params={"currency": "USD", "unit": "gram", "compact": 1}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if "per_gram_usd" not in data:
        raise ValueError(f"Unexpected API response shape, no 'per_gram_usd' field: {data}")
    return float(data["per_gram_usd"])


def karat_prices(price_24k_per_gram: float, karats) -> dict:
    return {k: round(price_24k_per_gram * KARAT_FACTORS[k], 3) for k in karats}


def load_state(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, price_24k: float):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "price_24k_usd_per_gram": price_24k,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        }, f, ensure_ascii=False, indent=2)


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API.format(token=token)
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()


def format_message(karats, prices, previous_price_24k=None, current_price_24k=None) -> str:
    lines = ["🟡 تحديث سعر الذهب (سبوت)"]
    if previous_price_24k is not None:
        diff = current_price_24k - previous_price_24k
        pct = (diff / previous_price_24k) * 100 if previous_price_24k else 0
        arrow = "⬆️" if diff > 0 else ("⬇️" if diff < 0 else "➖")
        lines.append(f"{arrow} تغيّر السعر: {diff:+.3f}$ للغرام ({pct:+.2f}%)")
    else:
        lines.append("(أول قراءة — بداية المراقبة)")

    lines.append("")
    lines.append("السعر بالدولار لكل غرام:")
    for k in sorted(karats, reverse=True):
        lines.append(f"  عيار {k}: {prices[k]:.3f}$")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Gold spot price change-alert bot for Telegram.")
    parser.add_argument("--karats", default="21,24",
                         help="Comma-separated karats to report, e.g. '21,24' or '18,21,22,24'")
    parser.add_argument("--min-change-percent", type=float, default=0.0,
                         help="Only alert if price moved by at least this percent (default: 0 = any change)")
    parser.add_argument("--state", default="gold_state.json", help="Path to the state file")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print what would be sent instead of calling Telegram")
    args = parser.parse_args()

    karats = [int(k.strip()) for k in args.karats.split(",") if k.strip()]
    for k in karats:
        if k not in KARAT_FACTORS:
            print(f"Error: unsupported karat {k}. Supported: {sorted(KARAT_FACTORS)}", file=sys.stderr)
            sys.exit(1)

    current_24k = fetch_price_per_gram_24k_usd()
    prices = karat_prices(current_24k, karats)

    state = load_state(args.state)
    previous_24k = state["price_24k_usd_per_gram"] if state else None

    should_alert = False
    if previous_24k is None:
        should_alert = True  # first run: confirm monitoring started
    else:
        pct_change = abs(current_24k - previous_24k) / previous_24k * 100 if previous_24k else 0
        should_alert = pct_change >= args.min_change_percent and current_24k != previous_24k

    print(f"24k price: {current_24k:.3f} USD/g | previous: {previous_24k} | should_alert={should_alert}")
    for k in sorted(karats, reverse=True):
        print(f"  karat {k}: {prices[k]:.3f} USD/g")

    if not should_alert:
        return  # nothing changed (or below threshold): don't touch the state file or alert

    save_state(args.state, current_24k)

    message = format_message(karats, prices, previous_price_24k=previous_24k, current_price_24k=current_24k)

    if args.dry_run:
        print("--dry-run set, not sending. Message would be:")
        print(message)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set.", file=sys.stderr)
        sys.exit(1)

    send_telegram_message(token, chat_id, message)
    print("Alert sent.")


if __name__ == "__main__":
    main()
