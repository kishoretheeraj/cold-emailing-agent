# Form D Funding Signal — Design

**Status:** Draft for review
**Date:** 2026-08-19

Sub-project 4 of 4 (see `2026-08-19-email-trust-and-voice-design.md` "Deferred work").
Adds a recently-raised-funding signal to `company_intel`, sourced from SEC Form D.

**Goal:** Tag target companies with their most recent private funding round so outreach
can be prioritized toward companies that just raised (and are therefore more likely to be
hiring).

**Not a targeting change.** Like the H-1B gate, this is decision support layered onto the
contact list Kishore already maintains. It never selects, ranks, or emails anyone on its
own.

---

## Why Form D

SEC Form D is the exempt-offering filing required within 15 days of a first sale in a
private raise. The DERA quarterly data sets publish it as structured TSV. Free, official,
no API key, no ToS ambiguity. This was chosen over Crunchbase (free API discontinued),
OpenCorporates (~500 calls/month then £2,250/yr), and every LinkedIn-adjacent enrichment
vendor (already ruled out in sub-project 1).

---

## Verified facts (checked against real 2025Q4 data, not assumed)

Everything below was confirmed by downloading and parsing the real file. The numbers are
from `2025Q4_d.zip` (14,637 submissions).

**1. The download path prefix drifts between quarters.** On the index page
`https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets`, the newest quarter
is under `/files/datastandardsinnovation/data/form-d-data-sets/` while older quarters are
under `/files/structureddata/data/form-d-data-sets/`. **Link discovery must scrape the
index page; the prefix must never be hardcoded.** This is the same failure mode as the DOL
LCA link bug (commit `611777d`), and is the single most likely thing to silently break.

**2. Three TSVs join on `ACCESSIONNUMBER`:**
- `FORMDSUBMISSION.tsv` — `FILING_DATE`, `SUBMISSIONTYPE`, `TESTORLIVE`
- `ISSUERS.tsv` — `ENTITYNAME`, `CIK`, `IS_PRIMARYISSUER_FLAG`, `ENTITYTYPE`
- `OFFERING.tsv` — `TOTALAMOUNTSOLD`, `TOTALOFFERINGAMOUNT`, `INDUSTRYGROUPTYPE`,
  `ISPOOLEDINVESTMENTFUNDTYPE`

**3. `IS_PRIMARYISSUER_FLAG` is `YES`/`NO`, not `Y`/`N`.** A `== "Y"` test silently drops
every row.

**4. `FILING_DATE` is `DD-MON-YYYY`** (e.g. `31-DEC-2025`), not ISO. Must be parsed, and
parse failures must degrade to "no date" rather than raising.

**5. Pooled investment funds are the majority and must be excluded — via two independent
signals.** 9,527 of 14,637 offerings (65%) set `ISPOOLEDINVESTMENTFUNDTYPE = "true"`.
These are VC/PE funds raising their own capital, not operating companies that hire.
**The boolean alone is insufficient:** a further 222 rows leave it blank but declare
`INDUSTRYGROUPTYPE = "Pooled Investment Fund"`. Both checks are required.

**6. After filtering, the signal is real.** 4,327 operating-company raises with a positive
amount in one quarter, including `Databricks, Inc. — $4,082,050,250 on 31-DEC-2025`.

**7. `SUBMISSIONTYPE` is `D` (new, 9,302) or `D/A` (amendment, 5,335).** Amendments are
legitimate updates to a prior raise, not separate rounds. Keeping the latest filing per
issuer handles both without double-counting.

---

## Architecture

New module `ingest_form_d.py`, shaped like `ingest_oflc_lca.py`: pure parsing and
aggregation functions plus a thin `run()` orchestrator, so the logic is testable without
network access.

```
discover_form_d_urls()      scrape index page -> [(quarter_label, absolute_url)]
parse_form_d_quarter(dir)   join 3 TSVs -> filtered issuer/amount/date records
fold_issuer(acc, record)    accumulate latest-raise-per-normalized-issuer
build_rows_for_upsert(acc)  -> rows for employer-style upsert
run(quarters_back=N)        orchestrate; best-effort per quarter
```

**Filtering (all must pass):** `TESTORLIVE == "LIVE"`, `IS_PRIMARYISSUER_FLAG == "YES"`,
not pooled by either signal, and `TOTALAMOUNTSOLD` parses to a positive integer.

**Aggregation:** one row per normalized issuer name, keeping the raise with the latest
`FILING_DATE`. Ties break on larger amount.

**Matching:** issuer names resolve to `company_intel` rows through the existing
`entity_resolution.normalize()`/`resolve()`, the same machinery the H-1B gate already uses
and has calibrated. No second matcher.

**Governance — mirrors the visa gate exactly:** a company with no Form D match is
`unknown`, never "did not raise." Absence from Form D means not observed: the company may
have raised through a route that does not file Form D, or under a different legal entity
name. The UI must never render a missing match as a negative. Automated code writes only
"observed a raise" or "unknown."

---

## Schema

New nullable columns on `company_intel`:

```sql
ALTER TABLE company_intel
  ADD COLUMN last_funding_date        DATE,
  ADD COLUMN last_funding_amount      BIGINT,
  ADD COLUMN last_funding_source      TEXT,     -- 'sec_form_d'
  ADD COLUMN last_funding_checked_at  TIMESTAMPTZ;
```

All nullable, no default, no backfill. Existing rows keep working unchanged.

**The migration is written but deliberately NOT applied in this change,** and the
ingestion is deliberately NOT wired into `visa_intel_ingest.yml` yet. Tests mock Supabase,
so a green suite would not prove the columns exist — shipping the workflow step before the
migration is applied would schedule a job that fails against the real schema. Applying the
migration is a human step; the workflow wiring follows it.

---

## Testing

`tests/test_ingest_form_d.py`, following `tests/test_ingest_oflc_lca.py`'s pattern:
constructed TSV fixtures written to a tmp dir, no network.

Cases: the `YES`/`NO` primary-issuer flag; `DD-MON-YYYY` date parsing including a
malformed date; both pooled-fund exclusion signals independently (the boolean, and the
industry-group string with the boolean blank); amendment (`D/A`) superseding an earlier
filing for the same issuer; non-numeric and zero/negative amounts skipped; latest-filing
selection and the tie-break; link discovery tolerating both observed path prefixes; and a
never-raises sweep proving a malformed quarter degrades to no-signal rather than failing
the run.

---

## Rollout

Additive and inert until three things happen, in order: the migration is applied, the
matcher is run, and the workflow step is added. Until then `ingest_form_d.py` is dead code
that only its tests exercise. Nothing about the daily agent changes.
