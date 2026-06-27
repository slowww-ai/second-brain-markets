"""Rebuild the wiki index from wiki/notes/.

Two outputs, decoupled so the connection layer never depends on an API key:

  1. **Graph + temporal index** (SQLite at wiki/index.db): `links` (wikilink
     edges) and `note_meta` (per-note mtime, plus date/kind/tickers for the
     stock/connection layer). Pure stdlib — always runs, no key needed.
  2. **Embeddings** (LanceDB at wiki/lancedb/): Gemini vectors for semantic
     search. Only runs when GEMINI_API_KEY is set and google-genai/lancedb are
     installed; otherwise it's skipped with a notice (semantic /ask is then
     disabled, but /connect and --window keep working).

Incremental: only re-embeds notes whose mtime changed since the last run.
"""
from __future__ import annotations
import os, re, sqlite3, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTES = ROOT / "wiki" / "notes"
LANCE_DIR = ROOT / "wiki" / "lancedb"
DB = ROOT / "wiki" / "index.db"
MODEL = "gemini-embedding-2-preview"

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"tags:\s*\[(.*?)\]")
DATE_RE = re.compile(r"^date:\s*(.+)$", re.M)
KIND_RE = re.compile(r"^kind:\s*(.+)$", re.M)
TICKERS_RE = re.compile(r"^tickers:\s*\[(.*?)\]", re.M)
TICKER_RE = re.compile(r"^ticker:\s*(.+)$", re.M)


def extract_meta(fm: str) -> dict:
    """Pull the temporal/stock dimensions out of the frontmatter.

    Additive: notes without these fields just yield empty values, so the rest
    of the wiki (which never set them) keeps working unchanged.
    """
    date_m = DATE_RE.search(fm)
    kind_m = KIND_RE.search(fm)
    tickers: list[str] = []
    tl = TICKERS_RE.search(fm)
    if tl:
        tickers += [t.strip().strip("'\"").upper() for t in tl.group(1).split(",") if t.strip()]
    ts = TICKER_RE.search(fm)
    if ts:
        tickers.append(ts.group(1).strip().strip("'\"").upper())
    seen, uniq = set(), []
    for t in tickers:
        if t and t not in seen:
            seen.add(t); uniq.append(t)
    return {
        "date": date_m.group(1).strip().strip("'\"") if date_m else "",
        "kind": kind_m.group(1).strip().strip("'\"") if kind_m else "",
        "tickers": ",".join(uniq),
    }


def parse(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    fm = m.group(1) if m else ""
    body = text[m.end():] if m else text
    tags_m = TAG_RE.search(fm)
    tags = [t.strip().strip("'\"") for t in (tags_m.group(1).split(",") if tags_m else []) if t.strip()]
    links = LINK_RE.findall(body)
    return body, tags, links, extract_meta(fm)


def ensure_sqlite(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS links (src TEXT, dst TEXT);
        CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst);
        CREATE TABLE IF NOT EXISTS ingested (
            raw_file TEXT PRIMARY KEY,
            content_hash TEXT,
            ingested_at TEXT,
            produced_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS note_meta (
            id TEXT PRIMARY KEY,
            mtime REAL,
            date TEXT,
            kind TEXT,
            tickers TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_note_meta_date ON note_meta(date);
    """)
    have = {row[1] for row in con.execute("PRAGMA table_info(note_meta)")}
    for col in ("date", "kind", "tickers"):
        if col not in have:
            con.execute(f"ALTER TABLE note_meta ADD COLUMN {col} TEXT")


def embed(client, text: str) -> list[float]:
    resp = client.models.embed_content(model=MODEL, contents=text)
    return resp.embeddings[0].values


def open_lance_table(db, dim: int):
    import pyarrow as pa
    if "notes" in db.table_names():
        return db.open_table("notes")
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("body", pa.string()),
        pa.field("tags", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])
    return db.create_table("notes", schema=schema)


def get_embedder():
    """Return (client, lancedb_module) if embeddings are possible, else (None, None)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("reindex: GEMINI_API_KEY not set — building links + temporal index only "
              "(/connect and --window work; semantic /ask disabled).")
        return None, None
    try:
        from google import genai
        import lancedb
    except ImportError:
        print("reindex: google-genai/lancedb not installed — building links + temporal "
              "index only. Install them (and set up .venv) to enable semantic search.")
        return None, None
    return genai.Client(api_key=key), lancedb


def main() -> None:
    con = sqlite3.connect(DB)
    ensure_sqlite(con)
    cur = con.cursor()

    client, lancedb = get_embedder()
    embed_ok = client is not None

    existing: dict[str, float] = {
        row[0]: row[1] for row in cur.execute("SELECT id, mtime FROM note_meta")
        if row[1] is not None
    }
    existing_ids = {row[0] for row in cur.execute("SELECT id FROM note_meta")}
    seen: set[str] = set()
    to_upsert: list[dict] = []

    cur.execute("DELETE FROM links")  # rebuilt from scratch each run

    for p in NOTES.glob("*.md"):
        note_id = p.stem
        seen.add(note_id)
        mtime = p.stat().st_mtime
        body, tags, links, meta = parse(p)
        for dst in links:
            cur.execute("INSERT INTO links(src, dst) VALUES (?, ?)", (note_id, dst))

        # Cheap metadata sync (no API): every note, every run.
        cur.execute(
            "INSERT INTO note_meta(id, date, kind, tickers) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET date=excluded.date, kind=excluded.kind, "
            "tickers=excluded.tickers",
            (note_id, meta["date"], meta["kind"], meta["tickers"]),
        )

        if not embed_ok:
            continue
        if note_id in existing and abs(existing[note_id] - mtime) < 1e-6:
            continue
        vec = embed(client, body[:8000])
        to_upsert.append({"id": note_id, "body": body, "tags": " ".join(tags), "vector": vec})

    if embed_ok and to_upsert:
        lance = lancedb.connect(str(LANCE_DIR))
        dim = len(to_upsert[0]["vector"])
        table = open_lance_table(lance, dim)
        ids = [r["id"] for r in to_upsert]
        in_list = ", ".join(f"'{i}'" for i in ids)
        try:
            table.delete(f"id IN ({in_list})")
        except Exception:
            pass
        table.add(to_upsert)
        for r in to_upsert:
            cur.execute(
                "UPDATE note_meta SET mtime=? WHERE id=?",
                (NOTES.joinpath(r["id"] + ".md").stat().st_mtime, r["id"]),
            )

    # Remove notes deleted from disk (note_meta always; LanceDB if embedding).
    deleted_ids = [i for i in existing_ids if i not in seen]
    if embed_ok and deleted_ids:
        lance = lancedb.connect(str(LANCE_DIR))
        if "notes" in lance.table_names():
            in_list = ", ".join(f"'{i}'" for i in deleted_ids)
            lance.open_table("notes").delete(f"id IN ({in_list})")
    for old_id in deleted_ids:
        cur.execute("DELETE FROM note_meta WHERE id=?", (old_id,))

    con.commit()
    con.close()
    mode = f"embedded {len(to_upsert)}" if embed_ok else "metadata-only (no embeddings)"
    print(f"reindex: {mode}, deleted {len(deleted_ids)}, indexed {len(seen)} notes")


if __name__ == "__main__":
    main()
