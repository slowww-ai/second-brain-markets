---
description: Surface and confirm relationships among dated stock notes within a month window
---

This is the heart of the stock layer: **find connections among things that
happened close in time, and record the real ones.** You propose candidate
relationships grounded in the timeless entity knowledge; the user confirms the
ones worth keeping; you write them into the graph as `[[wikilinks]]` with a
one-line rationale. The graph grows by *confirmed* relationships, not noise.

Arguments: an anchor — a ticker + month (`MSFT 2026-06`), a date
(`2026-06-18`), or a note id — and optionally `±N days` (default 30).

Steps:

1. **Pull the candidate cluster** (SQLite only, no API key needed):
   ```
   python scripts/search.py --window <anchor> [--days 30] [--ticker T]
   ```
   - `MSFT 2026-06` → anchor `2026-06`, pass `--ticker MSFT`.
   - a bare date or note id → pass it directly as the anchor.
   This lists dated notes in the window, ranked by how many tickers they share
   with the anchor (`⋂N {TICKERS}`).

2. **Load the context that gives the connections meaning.** Read the **entity
   hub** note(s) (`wiki/notes/<ticker>.md`) for the tickers involved — their
   business model, supply chain, and "why it moves" sections are what let you
   judge whether two dated events are actually related. Then read the candidate
   **event** notes themselves.

3. **Reason about relationships.** For each plausible pair (or small group), ask:
   does the entity knowledge explain a link? Examples of real links worth
   surfacing:
   - **Shared driver** — two co-mentioned tickers move on the same news (a
     supplier's capacity, a rate decision, a sector shock).
   - **Cause→effect in time** — a news event precedes a price move on the same
     ticker within days.
   - **Cross-company** — a customer/supplier/competitor relationship in the hubs
     turns a one-ticker event into a two-ticker story.
   Ground every rationale in the actual note text or hub content. **Never invent
   a connection that the notes don't support** — say "no strong link" when
   that's the honest read. Signal over volume.

4. **Present a short ranked list** of candidate connections. For each: the notes
   involved, a one-line rationale, and a confidence (high/medium/low). Cap it —
   a handful of strong candidates beats a flood.

5. **Confirm, then write.** Ask which to keep (or, if the user says "apply the
   high-confidence ones", do those). For each confirmed connection:
   - Add a `[[wikilink]]` between the related notes under a `## Connections`
     heading (create it if absent), each with its one-line "why".
   - Keep edits small and reversible; don't rewrite note bodies.
   - The reindex hook runs automatically after the notes are saved.

6. **Close the loop.** Summarize what you linked and suggest the next thread to
   pull — an unexplained price move, a ticker that keeps co-occurring, a hub
   still missing its business model. The point is the ongoing hunt: one
   relationship at a time.
