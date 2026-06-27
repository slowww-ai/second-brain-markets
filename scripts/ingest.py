"""Helpers for the ingest workflow.

Usage:
    python scripts/ingest.py stamp <file>               # add frontmatter if missing
    python scripts/ingest.py list-new                   # list raw/ files not yet ingested
    python scripts/ingest.py record <raw> <note1,note2> # mark a raw file as ingested
    python scripts/ingest.py hash <file>                # print content hash
    python scripts/ingest.py ensure-entities [T1,T2]    # create missing entity hub notes

The ledger lives in wiki/index.db -> `ingested` table. A raw file is considered
"new" if its content hash isn't in the ledger, so renames and edits are handled
correctly.

`ensure-entities` creates a stub entity hub note (`wiki/notes/<ticker>.md`,
kind: entity) for any watchlist ticker that doesn't have one yet, so the
`[[ticker]]` links from dated event notes never dangle. Fill a stub with real
business-model content via `/jina-capture` + `/ingest`.
"""
from __future__ import annotations
import sys, re, datetime, pathlib, hashlib, sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "wiki" / "raw"
NOTES = ROOT / "wiki" / "notes"
DB = ROOT / "wiki" / "index.db"
WATCHLIST = ROOT / "watchlist.txt"


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:50] or "untitled"


def content_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingested (
            raw_file TEXT PRIMARY KEY,
            content_hash TEXT,
            ingested_at TEXT,
            produced_notes TEXT
        );
    """)
    return con


def stamp(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        print(f"already stamped: {path.name}")
        return
    today = datetime.date.today().isoformat()
    slug = slugify(path.stem)
    fm = (
        f"---\n"
        f"id: {today}-{slug}\n"
        f"date: {today}\n"
        f"source: unknown\n"
        f"status: draft\n"
        f"---\n\n"
    )
    path.write_text(fm + text, encoding="utf-8")
    print(f"stamped: {path.name}")


def list_new() -> None:
    con = connect()
    ledger: dict[str, str] = {
        row[0]: row[1] for row in con.execute("SELECT raw_file, content_hash FROM ingested")
    }
    for r in sorted(RAW.glob("*")):
        if r.is_dir() or r.name.startswith("."):
            continue
        # Only text markdown files are directly ingestible; images/pdfs need
        # a companion .md (handled by /ingest).
        if r.suffix.lower() != ".md":
            companion = r.with_suffix(r.suffix + ".md")
            sibling = r.with_suffix(".md")
            if not companion.exists() and not sibling.exists():
                print(f"{r.name}  (needs companion .md)")
            continue
        h = content_hash(r)
        if ledger.get(r.name) != h:
            status = "new" if r.name not in ledger else "changed"
            print(f"{r.name}  ({status})")


def watchlist_names() -> dict[str, str]:
    names: dict[str, str] = {}
    if WATCHLIST.exists():
        for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",", 1)]
            ticker = parts[0].upper()
            if ticker:
                names[ticker] = parts[1] if len(parts) > 1 else ticker
    return names


def entity_stub(ticker: str, name: str) -> str:
    # No `date:` — hubs are timeless, so they must NOT land in the temporal
    # window. Dated event notes carry the dates; hubs accumulate them as backlinks.
    return (
        f"---\n"
        f"id: {ticker.lower()}\n"
        f"source: stub\n"
        f"tags: [entity]\n"
        f"ticker: {ticker}\n"
        f"kind: entity\n"
        f"status: stub\n"
        f"---\n\n"
        f"# {name} ({ticker})\n\n"
        f"> **Entity hub** — the timeless profile of this company. Currently a stub.\n"
        f"> Fill it via `/jina-capture <company overview url>` then `/ingest`, or "
        f"write directly.\n\n"
        f"## Business model\n_TBD — what they make/sell, how they earn._\n\n"
        f"## Supply chain & key relationships\n"
        f"_TBD — suppliers, customers, partners, competitors (link other "
        f"[[hubs]] here)._\n\n"
        f"## Why it moves\n_TBD — the drivers that show up in the news._\n\n"
        f"## Event timeline\n"
        f"Dated event notes that link here show up in the backlinks panel — that "
        f"is this company's timeline. Use `/connect {ticker} <YYYY-MM>` to surface "
        f"relationships within a month.\n"
    )


def ensure_entities(arg: str) -> None:
    names = watchlist_names()
    if arg:
        tickers = [t.strip().upper() for t in arg.split(",") if t.strip()]
    else:
        tickers = list(names.keys())
    if not tickers:
        sys.exit("no tickers given and watchlist.txt is empty")
    NOTES.mkdir(parents=True, exist_ok=True)
    created, existing = [], []
    for t in tickers:
        path = NOTES / f"{t.lower()}.md"
        if path.exists():
            existing.append(t)
            continue
        path.write_text(entity_stub(t, names.get(t, t)), encoding="utf-8")
        created.append(t)
    if created:
        print(f"created stub hub(s): {', '.join(created)} — fill via /jina-capture + /ingest")
    if existing:
        print(f"already present: {', '.join(existing)}")


def record(raw_name: str, produced: str) -> None:
    path = RAW / raw_name
    if not path.exists():
        sys.exit(f"no such raw file: {raw_name}")
    con = connect()
    con.execute(
        "INSERT OR REPLACE INTO ingested(raw_file, content_hash, ingested_at, produced_notes) "
        "VALUES (?, ?, ?, ?)",
        (raw_name, content_hash(path), datetime.datetime.now().isoformat(timespec="seconds"), produced),
    )
    con.commit()
    con.close()
    print(f"recorded: {raw_name} -> {produced}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd = sys.argv[1]
    if cmd == "stamp":
        for p in sys.argv[2:]:
            stamp(pathlib.Path(p))
    elif cmd == "list-new":
        list_new()
    elif cmd == "record":
        record(sys.argv[2], sys.argv[3])
    elif cmd == "ensure-entities":
        ensure_entities(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "hash":
        print(content_hash(pathlib.Path(sys.argv[2])))
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
