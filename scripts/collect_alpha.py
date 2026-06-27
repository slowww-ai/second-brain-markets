"""Collect dated stock observations from Alpha Vantage into wiki/raw/.

This is the *time-relative* capture path of the second brain's stock layer.
Each run pulls news (and optionally notable price moves) for the tickers in
`watchlist.txt` and writes one dated raw markdown capture per ticker. Those
captures get distilled by `/ingest` into atomic **event** notes, which link to
the timeless **entity hub** note for each ticker (`wiki/notes/<ticker>.md`).

    timeless info  →  entity hub  (notes/msft.md)      ← built via /jina-capture
    dated info     →  event leaf  (notes/2026-..-msft-...)  ← built from these captures
                          │
                   the ticker joins the two layers

Endpoints (https://www.alphavantage.co/documentation):
  - NEWS_SENTIMENT    news + per-ticker sentiment (the backbone of connections)
  - TIME_SERIES_DAILY optional: flag days with a large move as price-move events

Stdlib only (urllib) so it adds no dependency. Reads ALPHAVANTAGE_API_KEY from
the environment, falling back to the `.env` file in the project root.

Usage:
    python scripts/collect_alpha.py news                      # every watchlist ticker
    python scripts/collect_alpha.py news --ticker MSFT
    python scripts/collect_alpha.py news --from 2026-06-01 --to 2026-06-27
    python scripts/collect_alpha.py news --limit 40 --min-relevance 0.2
    python scripts/collect_alpha.py prices --ticker MSFT --threshold 0.04

Free tier: 25 requests/day, 5/minute. One NEWS_SENTIMENT call per ticker covers
a whole date range, so the default watchlist stays well within limits. The
script sleeps between calls to respect the per-minute cap.
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "wiki" / "raw"
DB = ROOT / "wiki" / "index.db"
WATCHLIST = ROOT / "watchlist.txt"
ENV = ROOT / ".env"
API = "https://www.alphavantage.co/query"

# Free tier is 5 calls/minute; 13s between calls keeps us safely under.
RATE_LIMIT_SLEEP = 13

# Non-news macro/commodity series → dated value-move events linked to a hub.
# `kind: macro` series are measured in percentage points (yields); `commodity`
# series in percent price change. Edit/extend freely.
SERIES = [
    {"hub": "gld",    "label": "Gold (USD/oz)",       "kind": "commodity", "type": "gold_silver", "symbol": "GOLD"},
    {"hub": "slv",    "label": "Silver (USD/oz)",     "kind": "commodity", "type": "gold_silver", "symbol": "SILVER"},
    {"hub": "uso",    "label": "WTI crude (USD/bbl)", "kind": "commodity", "type": "wti"},
    {"hub": "ust10y", "label": "US 10Y yield (%)",    "kind": "macro",     "type": "treasury", "maturity": "10year"},
    {"hub": "ust2y",  "label": "US 2Y yield (%)",     "kind": "macro",     "type": "treasury", "maturity": "2year"},
]


# --------------------------------------------------------------------------- #
# config / env
# --------------------------------------------------------------------------- #
def api_key() -> str:
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key and ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ALPHAVANTAGE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
                break
    if not key:
        sys.exit(
            "ALPHAVANTAGE_API_KEY not set. Get a free key at "
            "https://www.alphavantage.co/support/#api-key and add it to .env:\n"
            '  echo "ALPHAVANTAGE_API_KEY=your-key" >> .env'
        )
    return key


def load_watchlist() -> list[tuple[str, str]]:
    """Return [(ticker, name), ...] from watchlist.txt. Lines: `TICKER, Name`."""
    if not WATCHLIST.exists():
        sys.exit(f"no watchlist at {WATCHLIST} — create it, one `TICKER, Name` per line")
    out: list[tuple[str, str]] = []
    for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",", 1)]
        ticker = parts[0].upper()
        name = parts[1] if len(parts) > 1 else ticker
        if ticker:
            out.append((ticker, name))
    return out


# --------------------------------------------------------------------------- #
# http
# --------------------------------------------------------------------------- #
def get_json(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "second-brain-collector"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # Alpha Vantage signals problems via these keys instead of an HTTP error.
    for k in ("Error Message", "Information", "Note"):
        if k in data:
            raise RuntimeError(f"Alpha Vantage {k}: {data[k]}")
    return data


# --------------------------------------------------------------------------- #
# seen-URL ledger (so overlapping runs don't re-capture the same article)
# --------------------------------------------------------------------------- #
def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS av_seen "
        "(url TEXT PRIMARY KEY, ticker TEXT, seen_at TEXT)"
    )
    return con


def filter_unseen(con: sqlite3.Connection, articles: list[dict]) -> list[dict]:
    fresh = []
    for a in articles:
        url = a.get("url", "")
        if not url:
            continue
        row = con.execute("SELECT 1 FROM av_seen WHERE url = ?", (url,)).fetchone()
        if row is None:
            fresh.append(a)
    return fresh


def record_seen(con: sqlite3.Connection, articles: list[dict], ticker: str) -> None:
    now = datetime.datetime.now().isoformat(timespec="seconds")
    con.executemany(
        "INSERT OR IGNORE INTO av_seen(url, ticker, seen_at) VALUES (?, ?, ?)",
        [(a.get("url", ""), ticker, now) for a in articles if a.get("url")],
    )
    con.commit()


# --------------------------------------------------------------------------- #
# news
# --------------------------------------------------------------------------- #
def to_api_time(d: str) -> str:
    """`2026-06-01` -> `20260601T0000`."""
    return d.replace("-", "") + "T0000"


def pub_date(article: dict) -> str:
    """`time_published` is YYYYMMDDTHHMMSS -> return YYYY-MM-DD."""
    tp = article.get("time_published", "")
    if len(tp) >= 8 and tp[:8].isdigit():
        return f"{tp[0:4]}-{tp[4:6]}-{tp[6:8]}"
    return ""


def ticker_relevance(article: dict, ticker: str) -> float:
    for ts in article.get("ticker_sentiment", []):
        if ts.get("ticker", "").upper() == ticker.upper():
            try:
                return float(ts.get("relevance_score", 0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def related_tickers(article: dict, min_rel: float = 0.1) -> list[str]:
    """Every ticker the article is meaningfully *about* — these bridge hubs."""
    out = []
    for ts in article.get("ticker_sentiment", []):
        try:
            rel = float(ts.get("relevance_score", 0))
        except (TypeError, ValueError):
            rel = 0.0
        if rel >= min_rel:
            out.append(ts.get("ticker", "").upper())
    return [t for t in out if t]


def fetch_news(key: str, ticker: str, time_from: str, time_to: str, limit: int) -> list[dict]:
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "sort": "EARLIEST",
        "limit": str(limit),
        "apikey": key,
    }
    if time_from:
        params["time_from"] = to_api_time(time_from)
    if time_to:
        params["time_to"] = to_api_time(time_to)
    data = get_json(params)
    feed = data.get("feed", [])
    return feed if isinstance(feed, list) else []


def write_news_capture(
    ticker: str, name: str, articles: list[dict], time_from: str, time_to: str
) -> pathlib.Path:
    today = datetime.date.today().isoformat()
    path = RAW / f"{today}-{ticker.lower()}-news.md"
    if path.exists():  # raw/ is append-only — never overwrite a prior capture
        stamp = datetime.datetime.now().strftime("%H%M%S")
        path = RAW / f"{today}-{ticker.lower()}-news-{stamp}.md"

    span = time_from or "(latest)"
    if time_to:
        span += f" → {time_to}"
    lines = [
        "---",
        f"id: {today}-{ticker.lower()}-news",
        f"date: {today}",
        "source: alphavantage:NEWS_SENTIMENT",
        f"ticker: {ticker}",
        "kind: news-batch",
        "status: draft",
        "---",
        "",
        f"# {name} ({ticker}) — news {span}",
        "",
        f"> {len(articles)} new articles from Alpha Vantage. `/ingest` distills the "
        f"notable ones into atomic event notes, each dated by its **published** date "
        f"and linked to the [[{ticker.lower()}]] entity hub (plus any co-mentioned "
        f"ticker hubs).",
        "",
    ]
    for a in articles:
        title = a.get("title", "(untitled)").strip()
        rels = related_tickers(a)
        tick_line = ", ".join(rels) if rels else ticker
        topics = ", ".join(
            t.get("topic", "") for t in a.get("topics", []) if t.get("topic")
        )
        own = ticker_relevance(a, ticker)
        lines += [
            f"## {title}",
            "",
            f"- published: {pub_date(a)}",
            f"- tickers: {tick_line}",
            f"- overall_sentiment: {a.get('overall_sentiment_label', '')} "
            f"({a.get('overall_sentiment_score', '')})",
            f"- {ticker} relevance: {own:.2f}",
            f"- topics: {topics}",
            f"- source: {a.get('source', '')}",
            f"- url: {a.get('url', '')}",
            "",
            (a.get("summary", "") or "").strip(),
            "",
        ]
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cmd_news(args: argparse.Namespace) -> None:
    key = api_key()
    if args.ticker:
        targets = [(args.ticker.upper(), args.ticker.upper())]
    else:
        targets = load_watchlist()

    # Default to the trailing week so a bare `news` run grabs recent activity.
    time_from = args.date_from
    if not time_from and not args.date_to:
        time_from = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    time_to = args.date_to

    con = db()
    total = 0
    for i, (ticker, name) in enumerate(targets):
        if i:
            time.sleep(RATE_LIMIT_SLEEP)
        try:
            feed = fetch_news(key, ticker, time_from, time_to, args.limit)
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"  {ticker}: skipped ({e})")
            continue
        # Keep only articles genuinely about this ticker, newest-relevant first.
        kept = [a for a in feed if ticker_relevance(a, ticker) >= args.min_relevance]
        kept = filter_unseen(con, kept)
        kept.sort(key=lambda a: a.get("time_published", ""))
        kept = kept[: args.limit]
        if not kept:
            print(f"  {ticker}: no new articles")
            continue
        path = write_news_capture(ticker, name, kept, time_from, time_to)
        record_seen(con, kept, ticker)
        total += len(kept)
        print(f"  {ticker}: {len(kept)} new → {path.relative_to(ROOT)}")
    con.close()
    print(f"news: wrote {total} new articles across {len(targets)} ticker(s). Run /ingest next.")


# --------------------------------------------------------------------------- #
# prices (optional) — flag notable daily moves as events
# --------------------------------------------------------------------------- #
def cmd_prices(args: argparse.Namespace) -> None:
    key = api_key()
    if args.ticker:
        targets = [(args.ticker.upper(), args.ticker.upper())]
    else:
        targets = load_watchlist()

    date_from = args.date_from or (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    date_to = args.date_to or datetime.date.today().isoformat()

    for i, (ticker, name) in enumerate(targets):
        if i:
            time.sleep(RATE_LIMIT_SLEEP)
        try:
            data = get_json({
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": "compact",
                "apikey": key,
            })
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"  {ticker}: skipped ({e})")
            continue
        series = data.get("Time Series (Daily)", {})
        rows = sorted(series.items())  # ascending by date
        moves = []
        prev_close = None
        for day, ohlc in rows:
            if day < date_from or day > date_to:
                prev_close = _f(ohlc.get("4. close"))
                continue
            close = _f(ohlc.get("4. close"))
            if prev_close and close:
                ret = (close - prev_close) / prev_close
                if abs(ret) >= args.threshold:
                    moves.append((day, ret, prev_close, close))
            prev_close = close
        if not moves:
            print(f"  {ticker}: no moves ≥ {args.threshold:.0%}")
            continue
        path = _write_price_capture(ticker, name, moves, args.threshold)
        print(f"  {ticker}: {len(moves)} notable move(s) → {path.relative_to(ROOT)}")


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _write_price_capture(ticker, name, moves, threshold) -> pathlib.Path:
    today = datetime.date.today().isoformat()
    path = RAW / f"{today}-{ticker.lower()}-price-moves.md"
    if path.exists():
        stamp = datetime.datetime.now().strftime("%H%M%S")
        path = RAW / f"{today}-{ticker.lower()}-price-moves-{stamp}.md"
    lines = [
        "---",
        f"id: {today}-{ticker.lower()}-price-moves",
        f"date: {today}",
        "source: alphavantage:TIME_SERIES_DAILY",
        f"ticker: {ticker}",
        "kind: price-move-batch",
        "status: draft",
        "---",
        "",
        f"# {name} ({ticker}) — notable daily moves (≥ {threshold:.0%})",
        "",
        f"> `/ingest` turns each row into a dated price-move event note linked to "
        f"[[{ticker.lower()}]]. Pair these with the news in the same ±1-month window "
        f"to ask *what moved the stock*.",
        "",
        "| date | move | prev close | close |",
        "|---|---|---|---|",
    ]
    for day, ret, prev_close, close in moves:
        lines.append(f"| {day} | {ret:+.1%} | {prev_close:.2f} | {close:.2f} |")
    lines.append("")
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# series (item 3) — macro/commodity values as dated events, no news
# --------------------------------------------------------------------------- #
def fetch_series(key: str, spec: dict) -> list[tuple[str, float]]:
    """Return [(date, value), ...] ascending for one SERIES spec."""
    t = spec["type"]
    if t == "gold_silver":
        # Response shape: {"nominal": "XAUUSD", "data": [{"date","price"}]}
        data = get_json({
            "function": "GOLD_SILVER_HISTORY", "symbol": spec["symbol"],
            "interval": "daily", "apikey": key,
        })
        pts = data.get("data", [])
        out = [(p.get("date", ""), _f(p.get("price"))) for p in pts]
    elif t == "wti":
        data = get_json({"function": "WTI", "interval": "daily", "apikey": key})
        out = [(p.get("date", ""), _f(p.get("value"))) for p in data.get("data", [])]
    elif t == "treasury":
        data = get_json({
            "function": "TREASURY_YIELD", "interval": "daily",
            "maturity": spec["maturity"], "apikey": key,
        })
        out = [(p.get("date", ""), _f(p.get("value"))) for p in data.get("data", [])]
    else:
        return []
    # Drop blanks/missing ("." parses to 0.0) and sort ascending.
    return sorted([(d, v) for d, v in out if d and v], key=lambda r: r[0])


def cmd_series(args: argparse.Namespace) -> None:
    key = api_key()
    specs = SERIES
    if args.hub:
        specs = [s for s in SERIES if s["hub"] == args.hub.lower()]
        if not specs:
            sys.exit(f"unknown series hub '{args.hub}'. Known: {', '.join(s['hub'] for s in SERIES)}")

    date_from = args.date_from or (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    date_to = args.date_to or datetime.date.today().isoformat()

    for i, spec in enumerate(specs):
        if i:
            time.sleep(RATE_LIMIT_SLEEP)
        try:
            series = fetch_series(key, spec)
        except (RuntimeError, urllib.error.URLError) as e:
            print(f"  {spec['hub']}: skipped ({e})")
            continue
        moves = []
        prev = None
        for day, val in series:
            if day < date_from or day > date_to:
                prev = val
                continue
            if prev:
                if spec["kind"] == "macro":          # yields: percentage points
                    chg = val - prev
                    if abs(chg) >= args.yield_move:
                        moves.append((day, val, prev, f"{chg:+.2f}pp"))
                else:                                # prices: percent change
                    ret = (val - prev) / prev
                    if abs(ret) >= args.pct:
                        moves.append((day, val, prev, f"{ret:+.1%}"))
            prev = val
        if not moves:
            print(f"  {spec['hub']}: no notable moves in window")
            continue
        path = _write_series_capture(spec, moves, date_from, date_to)
        print(f"  {spec['hub']}: {len(moves)} move(s) → {path.relative_to(ROOT)}")
    print("series: done. Run /ingest to turn notable moves into dated event notes.")


def _write_series_capture(spec, moves, date_from, date_to) -> pathlib.Path:
    today = datetime.date.today().isoformat()
    hub = spec["hub"]
    path = RAW / f"{today}-{hub}-series.md"
    if path.exists():
        stamp = datetime.datetime.now().strftime("%H%M%S")
        path = RAW / f"{today}-{hub}-series-{stamp}.md"
    lines = [
        "---",
        f"id: {today}-{hub}-series",
        f"date: {today}",
        f"source: alphavantage:{'TREASURY_YIELD' if spec['type']=='treasury' else spec['type'].upper()}",
        f"ticker: {hub.upper()}",
        f"kind: {spec['kind']}-batch",
        "status: draft",
        "---",
        "",
        f"# {spec['label']} — notable moves {date_from} → {date_to}",
        "",
        f"> `/ingest` turns each row into a dated {spec['kind']} event note linked to "
        f"[[{hub}]]. Pair these with news in the same ±1-month window via "
        f"`/connect {hub.upper()} <YYYY-MM>` to ask what moved together.",
        "",
        "| date | value | prev | change |",
        "|---|---|---|---|",
    ]
    for day, val, prev, disp in moves:
        lines.append(f"| {day} | {val:.2f} | {prev:.2f} | {disp} |")
    lines.append("")
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pn = sub.add_parser("news", help="fetch news+sentiment for watchlist (or --ticker)")
    pn.add_argument("--ticker")
    pn.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    pn.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    pn.add_argument("--limit", type=int, default=50)
    pn.add_argument("--min-relevance", type=float, default=0.15)
    pn.set_defaults(func=cmd_news)

    pp = sub.add_parser("prices", help="flag notable daily moves as events")
    pp.add_argument("--ticker")
    pp.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    pp.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    pp.add_argument("--threshold", type=float, default=0.04, help="abs daily return, e.g. 0.04 = 4%%")
    pp.set_defaults(func=cmd_prices)

    ps = sub.add_parser("series", help="macro/commodity values (gold, oil, yields) as events")
    ps.add_argument("--hub", help="one of: " + ", ".join(s["hub"] for s in SERIES))
    ps.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ps.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ps.add_argument("--pct", type=float, default=0.03, help="price move threshold (commodities), 0.03 = 3%%")
    ps.add_argument("--yield-move", dest="yield_move", type=float, default=0.10,
                    help="yield move threshold in percentage points (treasuries), 0.10 = 10bps")
    ps.set_defaults(func=cmd_series)

    args = p.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
