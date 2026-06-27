---
description: Collect dated stock news/sentiment from Alpha Vantage into wiki/raw/
---

Your job is to pull **time-relative** stock data (news + sentiment, optionally
notable price moves) from Alpha Vantage for the watchlist, then hand off to
`/ingest`. This is the dated **event** layer; the timeless company profiles
(business model, supply chain) come from `/jina-capture` into **entity hub**
notes. The ticker joins the two.

Use `.venv/bin/python` if it exists, otherwise `python3` (the collector is
stdlib-only, so either works; `/ingest`'s reindex still needs the venv).

Arguments (all optional): a ticker (e.g. `MSFT`), and/or a date range like
`from 2026-06-01 to 2026-06-27`. With no args, collect news for the whole
watchlist over the last 7 days.

Steps:

1. **Make sure entity hubs exist** so the event→hub links won't dangle:
   ```
   python scripts/ingest.py ensure-entities
   ```
   Report any newly created stubs — those still need real content via
   `/jina-capture` + `/ingest`.

2. **Collect news.** Build the command from the arguments:
   ```
   python scripts/collect_alpha.py news [--ticker T] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
   ```
   - No ticker arg → omit `--ticker` (collects the whole watchlist; the script
     sleeps between calls to respect the free-tier 5/min limit).
   - If the user mentioned price moves, also run:
     `python scripts/collect_alpha.py prices [--ticker T] [--threshold 0.04]`
   - For macro/commodity values (gold, silver, oil, 10y/2y yields), run:
     `python scripts/collect_alpha.py series [--hub gld|slv|uso|ust10y|ust2y]`
     These write `commodity-batch` / `macro-batch` captures linked to those hubs.

3. **Handle errors gracefully.** If the script reports a missing
   `ALPHAVANTAGE_API_KEY`, tell the user to add it to `.env`
   (`ALPHAVANTAGE_API_KEY=...`, free key at alphavantage.co/support/#api-key)
   and stop. If a ticker is rate-limited or returns nothing, note it and
   continue with the others.

4. **Report** what landed in `wiki/raw/`: how many new articles per ticker, and
   which raw files were written. Mention if everything was already seen (the URL
   ledger skips duplicates across overlapping runs).

5. **Hand off.** Tell the user the captures are in `wiki/raw/`, then offer to run
   `/ingest` to distill them into atomic dated event notes linked to the entity
   hubs. Do not auto-run `/ingest` unless the user asks.

Keep it lean: collection only writes raw captures — no notes are created or
linked here. That happens in `/ingest`, and relationship discovery happens in
`/connect`.
