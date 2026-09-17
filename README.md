# Gold Price Alert Bot (للصاغة)

A Telegram bot that notifies as soon as the **gold spot price actually
changes** — built for local jewelers (الصاغة) who need to reprice
quickly, not just when a fixed target is crossed.

Data source: [xaus.com](https://xaus.com/api/) — free XAU/USD spot
price API, no API key required. Gold is always quoted for pure (24k)
gold; this script converts that into the karats jewelers actually
price against (21k is the common Middle East retail standard, plus
18k, 22k, 24k).

## How it's different from a simple threshold bot

A threshold bot (like the crypto `price_alert.py` example) is
**stateless** — one check, compare to a fixed number, maybe alert.

This bot needs **memory** — it has to know what the price was *last
time* to detect a *change*. That memory is a small file,
`gold_state.json`, which the GitHub Actions workflow commits back to
the repo after every run that finds a real price change. That also
means the file's git history doubles as a free price-change log —
`git log -p gold_state.json` shows every recorded move with its
timestamp.

## Setup

Same Telegram setup as the crypto bot — if you already did this for
`price-alert-bot`, you can reuse the **same bot and chat ID**, or
create a separate bot if you'd rather keep this feed distinct.

1. Message **[@BotFather](https://t.me/BotFather)** on Telegram,
   `/newbot`, get a token.
2. Start a chat with the bot (or add it to a group), send it any
   message.
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the
   `"chat":{"id": ...}` value.
4. Add repository secrets (Settings → Secrets and variables → Actions):

| Secret name          | Value                     |
|------------------------|---------------------------|
| `TELEGRAM_BOT_TOKEN`  | The token from BotFather  |
| `TELEGRAM_CHAT_ID`    | The chat ID from step 3   |

No secret for the gold price itself — the API needs no key.

## Running it

- **Manually**: Actions → Gold Price Alert → Run workflow. Set
  `karats` (e.g. `21,24` or `18,21,22`) and `min_change_percent`
  (`0` = alert on any change at all, as requested; raise it, e.g.
  `0.2`, if it turns out too chatty once running for real).
- **Automatically**: every 15 minutes once it's on the default branch.

### Local testing (no Telegram, no state commit)

```bash
pip install -r requirements.txt
python gold_alert.py --karats 21,24 --dry-run
```

Run it twice in a row locally — the first run has no previous price to
compare to and always alerts ("أول قراءة"); the second run will only
alert if the live price actually moved between the two calls.

## Sample message

```
🟡 تحديث سعر الذهب (سبوت)
⬆️ تغيّر السعر: +1.334$ للغرام (+1.05%)

السعر بالدولار لكل غرام:
  عيار 24: 128.834$
  عيار 21: 112.730$
```

## Known limitations (worth being upfront about with a client)

- **Prices are in USD per gram**, not Syrian pounds. Converting to SYP
  needs a live exchange-rate source too, and reliable ones for the
  Syrian market (vs. the parallel/black-market rate jewelers actually
  use) aren't a solved problem the way gold-spot APIs are. This is a
  natural v2 feature once you've picked a rate source with the client.
- **Every price tick, not just "market-moving" ones**: with
  `min_change_percent 0`, even a $0.01 wobble triggers a message. Real
  usage will tell you fast whether that's the right amount of noise —
  `min_change_percent` exists specifically to dial it down without
  changing any code.
- Spot price ≠ what a local shop actually charges (that includes
  markup/making charges — مصنعية). This bot reports the raw benchmark
  jewelers price against, not a finished retail price.

## License

MIT
