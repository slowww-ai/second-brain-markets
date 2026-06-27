---
id: ust2y
date: 2026-06-27
source: stub
tags: [entity, macro, rates]
ticker: UST2Y
kind: macro
status: stub
---

# US 2-Year Treasury Yield (UST2Y)

> **Macro hub** — the 2-year US Treasury yield, the market's read on the near-term
> Fed path. Fed real values by `collect_alpha.py series` (TREASURY_YIELD,
> maturity 2year); add commentary via `/jina-capture` + `/ingest`.

## What it is
The most policy-sensitive Treasury point — it tracks expectations for the Fed
funds rate over ~2 years. The **2s10s spread** ([[ust10y]] minus this) is a
classic recession signal when inverted.

## Why it moves
_TBD — Fed guidance, CPI/jobs surprises, recession odds._

## Event timeline
Dated yield-move events link here (backlinks). Pair with [[ust10y]] for the
curve and with risk assets ([[qqq]], [[spy]], [[vixy]]) via
`/connect UST2Y <YYYY-MM>`.
