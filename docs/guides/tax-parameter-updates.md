# Yearly Tax Parameter Updates

The deterministic tax estimate
(`services/financial-engine/src/family_cfo_financial_engine/tax_estimate.py`)
hard-codes one tax year's parameters. Tax law changes every year, so this
file must be refreshed annually — **every parameter from a primary source,
never a blog table** (the M80 cross-check caught a secondary source publishing
a wrong head-of-household bracket that the IRS document contradicts).

The engine enforces the deadline itself: once the calendar year passes
`TAX_YEAR`, every estimate carries a `STALE TAX PARAMETERS` warning in its
assumptions (visible on the Income & Tax card and in the advisor's tool
output) until this update is done.

## When

Late October through December, once the IRS publishes the next year's
inflation adjustments (typically announced in October). California's tables
arrive latest — late December.

## Checklist

1. **Federal brackets and standard deduction** — find the new Revenue
   Procedure ("tax inflation adjustments for tax year N") via
   <https://www.irs.gov/newsroom> and use the PDF itself
   (`irs.gov/pub/irs-drop/rp-YY-NN.pdf`):
   - §4.01 rate tables: Table 1 (married filing jointly) → `_BRACKETS["married_joint"]`,
     Table 2 (heads of households) → `_BRACKETS["head_of_household"]`,
     Table 3 (unmarried individuals) → `_BRACKETS["single"]`.
     **Careful:** HoH thresholds are NOT identical to single (e.g., 2026:
     $201,750 vs $201,775; $256,200 vs $256,225).
   - §"Standard Deduction" → `_STANDARD_DEDUCTION`.
2. **Social Security wage base** — SSA's contribution and benefit base page
   <https://www.ssa.gov/oact/cola/cbb.html> (announced with the COLA each
   October) → `_SS_WAGE_BASE`.
3. **Medicare** — 1.45% + 0.9% additional over $200k/$250k are statutory
   (IRC §3101(b)(2)), **not indexed**; verify Congress hasn't changed them,
   otherwise leave as is.
4. **California** — FTB indexed parameters (rates and standard deduction,
   published late December for the just-ended year at
   <https://www.ftb.ca.gov>): update `_CA_SINGLE_BRACKETS` and
   `_CA_STANDARD_DEDUCTION`, and the year named in the note inside
   `_california_tax_minor`. CA tables lag the federal year by one — use the
   latest published and say so in the note.
5. **Massachusetts** — the DOR's next-year Form 1-ES/2-ES instructions
   (search mass.gov for "Form 2-ES <year>") state the indexed 4% surtax
   threshold → `_MA_SURTAX_THRESHOLD`. The 5% rate, personal exemptions
   ($4,400/$8,800/$6,800), and the $2,000-per-person FICA deduction are
   statutory and have been stable — verify, don't assume. Note: mass.gov
   blocks non-browser fetches; open the pages in a browser if scripted
   fetching fails.
6. **All other states + DC** (`_STATE_TABLE`) — re-transcribe from the Tax
   Foundation's annual "State Individual Income Tax Rates and Brackets"
   compilation (published each February at
   <https://taxfoundation.org/data/all/state/state-income-tax-rates/>).
   Many states phase in rate cuts every January, so assume most entries
   change. Watch the mechanism per state: some list personal exemptions as
   income offsets (fold into `ded`), others as flat CREDITS (`credit` —
   AR/DE/IA/NE/OR/UT today).
7. **No-wage-tax states** — confirm `NO_WAGE_TAX_STATES` is still accurate
   (states do occasionally add or repeal income taxes).
8. Bump `TAX_YEAR`, update the source citations in the module header
   comment, and update the hand-computed expectations in
   `services/financial-engine/tests/test_tax_estimate.py` (the comments show
   the arithmetic to redo) and any API tests that assert dollar amounts.
9. Run both suites (`services/financial-engine`, `apps/api`), deploy, and
   record the refresh in `docs/specs/12-implementation-tasks.md`.

## Verification discipline

- Quote every threshold from the primary PDF, not from news articles or tax
  sites — they disagree with each other and with the IRS.
- Keep one hand-computed test per filing status region that changed, with
  the arithmetic in a comment, so the next person can re-derive it.
