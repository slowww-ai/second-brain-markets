# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Second Brain

A personal wiki built from raw captured markdown. Claude Code helps ingest, link, index, and synthesize.

## Folder layout

- `wiki/raw/` — append-only dumps. **Never edit or delete files here.** Anything captured goes here first: clipped articles, meeting notes, transcripts, half-formed thoughts.
- `wiki/notes/` — the distilled wiki. Interlinked markdown notes derived from `raw/`. Safe to edit and rewrite.
- `wiki/outputs/` — generated deliverables (briefs, memos, docs, decks). Disposable.
- `scripts/` — Python helpers (`ingest.py`, `reindex.py`, `search.py`, `dashboard.py`).
- `wiki/lancedb/` — LanceDB vector store (table `notes`) holding Gemini embeddings. Used for semantic search.
- `wiki/index.db` — SQLite side-table for `links`, `ingested` ledger, and `note_meta` (mtime tracking).

## Storage split

The index is deliberately split across two stores. Vectors live in **LanceDB** (`wiki/lancedb/`) for on-disk ANN search without loading every embedding into memory. Graph and ledger metadata lives in **SQLite** (`wiki/index.db`): `links` (wikilink edges, rebuilt every reindex), `ingested` (raw-file ledger keyed by filename, with content hash + produced notes), and `note_meta` (per-note mtime for incremental reindex, plus `date`/`kind`/`tickers` for the stock layer's temporal queries). When changing the schema in one store, check whether the other needs a matching change.

## Embeddings & search

Semantic search uses the Gemini `gemini-embedding-2-preview` model via `google-genai`, stored in LanceDB for on-disk vector search (no loading all embeddings into memory). `scripts/reindex.py` only re-embeds notes whose mtime changed.

`GEMINI_API_KEY` is read from `os.environ` directly — the scripts do **not** auto-load `.env`. Either export the key in your shell, or run with:

```bash
set -a; source .env; set +a
python scripts/reindex.py
```

## Stock layer

The wiki doubles as a stock-relationship graph. The whole idea is to gather
interconnected things and discover their relationships over time — so the graph
gains a **time spine** without changing the core notes/links/embeddings model.

**Two layers of notes, joined by ticker.** The ticker is the foreign key.

| Layer | What | Source | Frontmatter |
|---|---|---|---|
| **Hub** (timeless) | A profile that rarely changes. **Entity hub** = a company/asset (filename = lowercase ticker, `notes/msft.md`, `notes/gld.md`). **Theme hub** = a concept ("AI capex", "rate cycle"). **Macro hub** = a series (`notes/ust10y.md`). One per thing. | `/jina-capture`, hand-written | `kind: entity\|theme\|macro`, `ticker:` (entities/macros) |
| **Event leaf** (time-relative) | A dated observation — a news item, a notable price/yield move, earnings. Atomic, one per item. | `/collect` (Alpha Vantage) | `kind: news\|price-move\|macro\|commodity\|earnings`, `date:`, `tickers: [..]`, `sentiment:` |

An event leaf links to its hub(s) by ticker (`[[msft]]`), so each hub accumulates
a **timeline** in its backlinks, and a co-mentioned event (`tickers: [MSFT, NVDA]`)
bridges two hubs — revealing a relationship whose *meaning* the hub notes explain.

**Hubs generalize beyond tickers.** Not everything is a company. A `/jina-capture`
of a macro thesis becomes a **theme hub** (`kind: theme`); tickers and events link
to it, and its backlinks become your evidence file for that thesis. Anything you
capture connects through the same two mechanisms — explicit `[[wikilinks]]` (added
at ingest) and semantic embeddings (LanceDB) — so arbitrary captures are never a
silo. Assets **not on Alpha Vantage** (e.g. Korea-listed LS Electric) live as
hubs too; gather their news via `/jina-capture` instead of `/collect`.

**New frontmatter fields** (all additive — notes that omit them are unaffected):
`tickers` / `ticker`, `kind`, `sentiment`. `date` was always there but is now
indexed and treated as first-class.

**Pieces:**

- `scripts/collect_alpha.py` — stdlib-only Alpha Vantage collector. Subcommands:
  `news` (NEWS_SENTIMENT), `prices` (notable equity moves via TIME_SERIES_DAILY),
  and `series` (non-news macro/commodity values → dated events: gold/silver via
  GOLD_SILVER_HISTORY, oil via WTI, 10y/2y yields via TREASURY_YIELD; configured
  in the `SERIES` list in the script, linked to the `gld`/`slv`/`uso`/`ust10y`/`ust2y`
  hubs). Reads `watchlist.txt`, writes dated captures to `wiki/raw/`, dedups news
  by URL via the `av_seen` table in `index.db`. Needs `ALPHAVANTAGE_API_KEY`
  (free tier: 25 calls/day, 5/min — one news call per ticker covers a date range).
- `watchlist.txt` (project root) — symbols to track, one `TICKER, Name` per line.
  Use US-listed tickers / ETF proxies (gold→GLD, Nasdaq→QQQ, oil→USO, VIX→VIXY).
- `scripts/ingest.py ensure-entities [T1,T2]` — creates stub entity hubs for
  tickers missing one (defaults to the whole watchlist).
- `scripts/search.py --window <anchor> [--days 30] [--ticker T]` — the
  "connect within a month" engine. Anchor = date / `YYYY-MM` / note-id. Returns
  dated notes in the window ranked by ticker overlap. SQLite only, no API key.
- `/collect`, `/connect` — the capture and relationship-discovery commands.

**The loop:** `/jina-capture` company overviews → entity hubs · `/collect` →
dated event captures · `/ingest` → atomic event notes linked to hubs ·
`/connect` → confirm relationships within a month, one at a time.

## Slash commands

The workflow is driven by skill-backed slash commands:

| Command | Purpose |
|---|---|
| `/jina-capture <url>` | Fetch a URL via `r.jina.ai` and save it to `wiki/raw/` as a stamped markdown file. |
| `/collect [ticker] [from..to]` | Pull dated stock news/sentiment (plus optional price moves and macro/commodity series) from Alpha Vantage for the watchlist into `wiki/raw/`. See **Stock layer** below. |
| `/connect <ticker month \| date \| note-id>` | Surface and confirm relationships among dated stock notes within a ±1-month window. The relationship-discovery loop. |
| `/ingest` | Distill new files in `wiki/raw/` into `wiki/notes/`, update the ledger, and reindex. |
| `/ask <question>` | Search `wiki/notes/` semantically and answer in chat, citing notes with `[[wikilinks]]`. No file output. |
| `/brief <topic>` | Gather relevant notes via semantic search and write a brief into `wiki/outputs/`. Default format is markdown; HTML, docx, and pdf are supported on request. |
| `/garden` | Weekly cleanup — orphans, duplicates, broken wikilinks, stale drafts. |
| `/shortwrite` | Derive a SHORT angled piece from a long source note via `shortwrite.html` — a two-pane canvas (left = long source, read-only; right = short editable draft). Applies the SNS hook playbook. |

## Dashboard

`scripts/dashboard.py` serves a tiny status page for the second brain showing how many raw files are unprocessed (new/changed since last ingest, plus non-markdown captures still missing a companion `.md`):

```bash
python scripts/dashboard.py            # serve on http://localhost:3100
python scripts/dashboard.py --port 8000
```

## Conventions

### Frontmatter (every file in `raw/` and `notes/`)

```yaml
---
id: 2026-04-09-short-slug
date: 2026-04-09
source: <url | meeting | thought | book:title | etc>
tags: [tag1, tag2]
status: draft | stable
---
```

### Linking

- Use `[[wikilinks]]` to reference other notes in `wiki/notes/` by filename (without `.md`).
- Every note should have at least one backlink once ingested. Orphans get flagged during `/garden`.
- Prefer many small focused notes over few large ones.

### Non-text captures (images, PDFs, audio)

Raw captures don't have to be markdown. Images (`.png`, `.jpg`), PDFs, and transcripts are all welcome in `wiki/raw/`. But the scripts (`search.py`, `reindex.py`) only understand text, so during `/ingest` Claude must convert every non-text capture into a companion markdown file so it becomes searchable.

Rules:

- For an image `raw/<name>.png`, create a sibling `raw/<name>.md` containing:
  - standard frontmatter (source should note it's an image)
  - a description of what the image shows
  - a full transcription of any visible text, tables, or diagrams
  - a reference line like `image: <name>.png`
- For a PDF, do the same: `raw/<name>.pdf` → `raw/<name>.md` with extracted text, key tables, and a reference to the original. Note: `/jina-capture` works on PDF URLs too (`r.jina.ai` extracts the text), so you can skip the manual transcription step by capturing the PDF's URL directly.
- Never delete or modify the original image/PDF. The companion `.md` is what gets distilled into `wiki/notes/`.
- When creating the note in `wiki/notes/`, embed the original with `![](../raw/<name>.png)` so it renders inline.

### Note granularity

- One concept per note. If a note covers two ideas, split it.
- Raw dumps can be any length; notes should usually be under ~300 lines.

### Personal thoughts and multilingual captures

- Personal thoughts and reflections belong in `wiki/raw/` with `source: thought` in the frontmatter. They get ingested the same way as articles — Claude distills them into linked notes.
- When the user speaks or writes in a non-English language (e.g., Korean), preserve the original verbatim in the raw capture under a clear section heading (e.g., `## Original (Korean)`), then add structured English headings, section labels, and department/term translations alongside. The English layer makes the content searchable by LanceDB; the original layer guarantees nothing is lost in translation.

## Workflow

1. **Capture** — drop files into `wiki/raw/` freely, or use `/jina-capture <url>` to fetch web articles. No curation.
2. **Ingest** — run `/ingest` to process new raw files into `notes/`.
3. **Retrieve** — run `/ask <question>` for quick answers from existing notes, or `/brief <topic>` for a longer written synthesis into `wiki/outputs/`.
4. **Garden** — run `/garden` weekly to merge duplicates, fix orphans, clean tags.

## Python environment

Always use a local virtual environment for this project. Never install packages globally or with `--break-system-packages`.

**Use `python3.13`.** The system `python3` may not have wheels available for `google-genai` and other dependencies — installs will fail with "No matching distribution found".

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

When running scripts, activate `.venv` first (or call `.venv/bin/python scripts/<name>.py` directly). If `.venv` doesn't exist yet, create it before running anything.

## Rules for Claude

- **Never modify or delete existing files in `wiki/raw/`.** Treat it as append-only — creating new raw files is fine and expected (captures, thoughts, companion `.md` for PDFs/images), but never edit or remove what's already there.
- When ingesting, prefer appending to an existing note over creating a new one if the concepts overlap.
- Always update frontmatter and add `[[wikilinks]]` when writing to `notes/`.
- After any write to `wiki/notes/`, the reindex hook will run automatically — don't run it manually unless it fails.
- When producing deliverables, write them to `wiki/outputs/` and cite source notes.

## The ingest ledger contract

`/ingest` **must** call `python scripts/ingest.py record <raw-filename> <note1,note2,...>` after distilling each raw file. The ledger lives in `wiki/index.db.ingested` and is the *only* thing `list-new` consults — without `record`, the same file appears as "new" on every run.

`list-new` keys on **SHA-256 of file contents** (first 16 hex chars), not on mtime or filename references. Consequences:

- A file re-saved without changes is silently skipped — safe.
- Any byte change re-flags the file as `(changed)`, even a whitespace edit.
- Renaming a raw file appears as a new entry; the old ledger row becomes orphaned. Avoid renames in `wiki/raw/` (also forbidden by the immutability rule).
