"""Semantic + structural search over the wiki.

Vector search uses LanceDB at wiki/lancedb/ (table `notes`).
Backlinks and temporal/stock metadata live in wiki/index.db.

Usage:
    python scripts/search.py "<query>"             # semantic search
    python scripts/search.py --tag <tag>           # substring filter on tags
    python scripts/search.py --backlinks <note>    # notes linking to <note>
    python scripts/search.py --window <anchor>     # notes within ±N days of anchor
        [--days 30] [--ticker MSFT]

`--window` is the "connect things within a month" engine. The anchor can be a
date (2026-06-25), a month (2026-06), or a note id. Results are dated notes in
the window, ranked by how many tickers they share with the anchor — the
candidate connections you then confirm with `/connect`. It reads only SQLite,
so it needs no API key.

Semantic search and --tag require GEMINI_API_KEY (and google-genai / lancedb).
"""
from __future__ import annotations
import os, sys, sqlite3, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANCE_DIR = ROOT / "wiki" / "lancedb"
DB = ROOT / "wiki" / "index.db"
MODEL = "gemini-embedding-2-preview"

# Kinds that count as dated market observations for the window engine.
MARKET_KINDS = {"news", "price-move", "commodity", "macro", "earnings"}


def embed_query(q: str) -> list[float]:
    try:
        from google import genai
    except ImportError:
        sys.exit("google-genai not installed. Run: pip install google-genai")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY not set")
    client = genai.Client(api_key=key)
    resp = client.models.embed_content(model=MODEL, contents=q)
    return resp.embeddings[0].values


def _lance():
    try:
        import lancedb
    except ImportError:
        sys.exit("lancedb not installed. Run: pip install lancedb pyarrow")
    return lancedb.connect(str(LANCE_DIR))


def semantic(query: str, limit: int = 10) -> None:
    qvec = embed_query(query)
    db = _lance()
    if "notes" not in db.table_names():
        print("no notes indexed yet — run scripts/reindex.py")
        return
    table = db.open_table("notes")
    results = table.search(qvec).limit(limit).to_list()
    for r in results:
        score = r.get("_distance", 0.0)
        snippet = (r.get("body") or "").strip().replace("\n", " ")[:160]
        print(f"{score:.3f}  {r['id']}\n  {snippet}\n")


def by_tag(tag: str) -> None:
    db = _lance()
    if "notes" not in db.table_names():
        return
    table = db.open_table("notes")
    for r in table.search().where(f"tags LIKE '%{tag}%'").limit(1000).to_list():
        print(r["id"])


def backlinks(note: str) -> None:
    con = sqlite3.connect(DB)
    for row in con.execute("SELECT src FROM links WHERE dst = ?", (note,)):
        print(row[0])


# --------------------------------------------------------------------------- #
# temporal window — the "connect within a month" engine
# --------------------------------------------------------------------------- #
def _month_bounds(ym: str) -> tuple[str, str]:
    y, m = (int(x) for x in ym.split("-"))
    first = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    last = nxt - datetime.timedelta(days=1)
    return first.isoformat(), last.isoformat()


def _resolve_anchor(con: sqlite3.Connection, anchor: str):
    """Return (lo_center_date, hi_center_date, anchor_tickers, anchor_id)."""
    if len(anchor) == 10 and anchor[4] == "-" and anchor[7] == "-":  # YYYY-MM-DD
        return anchor, anchor, set(), None
    if len(anchor) == 7 and anchor[4] == "-":                        # YYYY-MM
        lo, hi = _month_bounds(anchor)
        return lo, hi, set(), None
    row = con.execute(
        "SELECT date, tickers FROM note_meta WHERE id = ?", (anchor,)
    ).fetchone()
    if not row or not row[0]:
        sys.exit(f"anchor '{anchor}' is not a date (YYYY-MM-DD), month (YYYY-MM), or a dated note id")
    tickers = {t for t in (row[1] or "").split(",") if t}
    return row[0], row[0], tickers, anchor


def window(anchor: str, days: int = 30, ticker: str = "") -> None:
    con = sqlite3.connect(DB)
    lo_c, hi_c, anchor_tickers, anchor_id = _resolve_anchor(con, anchor)
    if ticker:
        anchor_tickers = {ticker.upper()}

    lo = (datetime.date.fromisoformat(lo_c) - datetime.timedelta(days=days)).isoformat()
    hi = (datetime.date.fromisoformat(hi_c) + datetime.timedelta(days=days)).isoformat()

    rows = con.execute(
        "SELECT id, date, kind, tickers FROM note_meta "
        "WHERE date != '' AND date IS NOT NULL AND date BETWEEN ? AND ? "
        "ORDER BY date",
        (lo, hi),
    ).fetchall()

    results = []
    for nid, date, kind, tickers in rows:
        if nid == anchor_id:
            continue
        tset = {t for t in (tickers or "").split(",") if t}
        # Skip non-market prose (no tickers and not a market kind), e.g. example
        # notes that merely happen to fall in the date range.
        if not tset and (kind or "") not in MARKET_KINDS:
            continue
        if ticker and ticker.upper() not in tset:
            continue
        shared = anchor_tickers & tset
        results.append((len(shared), date, nid, kind or "", tset, shared))

    # Most ticker overlap first, then chronological.
    results.sort(key=lambda r: (-r[0], r[1]))

    span = f"{lo_c}" if lo_c == hi_c else f"{lo_c}…{hi_c}"
    hdr = f"window: {span} ±{days}d"
    if anchor_tickers:
        hdr += f"  anchor tickers: {','.join(sorted(anchor_tickers))}"
    print(hdr)
    if not results:
        print("  (no dated notes in this window yet — collect & ingest some events first)")
        return
    for shared_n, date, nid, kind, tset, shared in results:
        tick = ",".join(sorted(tset)) if tset else "-"
        overlap = f"⋂{shared_n} {{{','.join(sorted(shared))}}}" if shared_n else ""
        print(f"  {date}  {kind:<16} [{tick}]  {overlap}")
        print(f"      {nid}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    if sys.argv[1] == "--tag":
        by_tag(sys.argv[2])
    elif sys.argv[1] == "--backlinks":
        backlinks(sys.argv[2])
    elif sys.argv[1] == "--window":
        rest = sys.argv[2:]
        if not rest:
            print(__doc__); return 1
        anchor = rest[0]
        days, ticker = 30, ""
        i = 1
        while i < len(rest):
            if rest[i] == "--days" and i + 1 < len(rest):
                days = int(rest[i + 1]); i += 2
            elif rest[i] == "--ticker" and i + 1 < len(rest):
                ticker = rest[i + 1]; i += 2
            else:
                i += 1
        window(anchor, days, ticker)
    else:
        semantic(" ".join(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
