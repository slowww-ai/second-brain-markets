---
id: ust10y
date: 2026-06-27
source: stub
tags: [entity, macro, rates]
ticker: UST10Y
kind: macro
status: stub
---

# US 10-Year Treasury Yield (UST10Y)

> **Macro hub** — the 10-year US Treasury yield, the market's main discount rate.
> Fed real values by `collect_alpha.py series` (TREASURY_YIELD, maturity 10year);
> add commentary via `/jina-capture` + `/ingest`.

## What it is
The benchmark long-term US government borrowing cost. Rising 10Y → higher
discount rate → pressure on long-duration / growth equities (see [[qqq]],
[[tsla]], [[pltr]]); often inverse to [[gld]].

## Why it moves
_TBD — growth, inflation expectations, Fed policy, Treasury supply._

## Event timeline
Dated yield-move events link here (backlinks). Compare with [[ust2y]] for the
2s10s curve, and use `/connect UST10Y <YYYY-MM>` to relate yield moves to
equity and commodity moves in the same month.
