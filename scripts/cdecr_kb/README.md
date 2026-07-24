# CDECR v2 catalog builder

This directory contains the reproducible, standard-library-only builders for
the twelve JSON arrays in `src/cdecr/catalogs/v2`. Runtime loading is outside
the scope of this build and is intentionally unchanged.

## Working directory and build order

The default working directory is `D:\cdecr-kb-work`. Downloads, resumable
per-CIK summaries, and validation reports stay there rather than in Git.

Run the stages from the repository root in this order:

```powershell
python scripts/cdecr_kb/build_core.py
python scripts/cdecr_kb/build_vocab.py
python scripts/cdecr_kb/build_places.py
python scripts/cdecr_kb/build_persons.py
python scripts/cdecr_kb/build_sec_facts.py --workers 8 --rate 5
python scripts/cdecr_kb/enrich_sec_metrics.py
python scripts/cdecr_kb/build_named_objects.py
python scripts/cdecr_kb/build_artifacts.py --workers 8 --rate 5
python scripts/cdecr_kb/validate_catalogs.py
```

Field Resolution v2 的确定性后处理在完整目录构建后执行：

```powershell
python scripts/cdecr_kb/optimize_field_resolution.py
python scripts/cdecr_kb/validate_catalogs.py
```

它补齐核心 Metric、Predicate snake_case/action aliases、Fiscal Period
标准表面，并为每个有可用财历模板的 issuer 保留未来两个财年。

`build_sec_facts.py` and `build_artifacts.py` write one compact state file per
CIK and can be rerun safely. They request no more than the configured global
rate. Company Facts and submissions source responses are processed immediately
and are not retained.

If EPA has already been processed and only Wikidata enrichment is pending:

```powershell
python scripts/cdecr_kb/build_named_objects.py --reuse-existing-epa
```

## Scale policy

- Company, active listed Instrument, mutual fund, RIA, active bank, and active
  broker-dealer records are retained in full after source-level filtering.
- Place contains countries, first-level administrative areas, `cities500`, and
  US administrative/populated places with population at least 100. English and
  abbreviation aliases are capped at 20 per object.
- FASB metrics exclude abstracts, axes, domains, members, tables, text blocks,
  policies, and disclosures. SEC custom tags use only the latest four available
  quarters and retain the 750 most-used candidates before deduplication.
- Fiscal periods retain at most ten actual fiscal years plus two forward years
  derived by a 364-day shift. Only 10-K, 10-Q, 20-F, and 40-F facts participate.
- SEC Person data retains five years of officers/directors and excludes ordinary
  ten-percent owners. Repeated single-token aliases are removed globally.
- EPA owner matches are diversified to at most 100 facilities per owner and a
  global deterministic 50,000-record cap. A separate deterministic sample of
  25,000 unmatched industrial facilities is retained. Wikidata has a separate
  cap for each allowed Named Object kind.
- Artifact retains five years of target SEC forms. High-volume 8-K/6-K records
  are capped at 5 per company in the catalog; all compact candidates remain
  available when choosing synthetic earnings-release dates.

These limits are business rules, not emergency truncation. They bias the
runtime catalog toward objects likely to occur in financial news and filings.

## ID and kind decisions

- Company is grouped by SEC CIK. Its ID uses the deterministic primary ticker;
  additional share-class tickers remain aliases and separate Instruments.
- Institution uses a normalized canonical-name slug. Slug collisions receive a
  stable eight-character SHA-1 suffix.
- Person uses full name; same-name collisions receive an organization/stable
  suffix through the same collision mechanism.
- Instrument uses ticker. Non-ticker macro assets use a fixed curated ID.
- Place uses name, then country/admin context, then geonameId only when needed.
- Named Object uses `<KIND>_<NAME>` and a stable source suffix on collision.
- Fiscal Period uses `<COMPANY_ID>_FYyyyy[_Qn]`.
- Filing Artifact uses company, form, filing date, and accession suffix.
  Synthetic earnings Artifact uses company and fiscal-period token.

Artifact kinds emitted by this build are `SEC_FILING` and
`EARNINGS_RELEASE`. The validator also reserves `PRESS_RELEASE`,
`ANALYST_REPORT`, `AGREEMENT`, and `REPORT` for later compatible additions.

`Unit.kind` is intentionally present and limited to `CURRENCY`, `SCALE`,
`RATIO`, and `UNIT`.

## Sources

- SEC company ticker, mutual-fund ticker, Company Facts, submissions, insider
  transactions, active broker-dealer, registered investment-adviser, and
  financial-statement datasets.
- Nasdaq Trader listed and other-listed symbol directories.
- FDIC BankFind institutions CSV.
- GeoNames country, admin1, cities500, US, and alternate-names downloads
  (GeoNames data is licensed under CC BY 4.0).
- EPA Facility Registry Service National Single CSV.
- FASB 2026 US GAAP Taxonomy, used under the project's confirmed private,
  non-profit use boundary.
- SIX/ISO 4217 list-one XML for active currencies.
- Wikidata SPARQL results for bounded Institution, Person, and Named Object
  categories (Wikidata structured data is CC0).

The current SEC monthly RIA ZIP is used instead of the IARD
`firm_compilation.zip`: the latter currently contains schemas, examples, and a
guide, but no complete firm records.
