# Public / Regulatory Semantic Tools Real Acceptance

- Date: 2026-08-08 (Asia/Shanghai)
- Scope: 15 non-derived public-contract, regulatory, company-disclosure and issuer-IR tools.
- Method: each invocation went through `default_real_tool_registry()` with an explicit
  allowlist. Requests were read-only; no LLM call was made. The table intentionally
  excludes API keys, raw response bodies, award identifiers and document content.
- Result: **13 succeeded, 2 partial, 0 failed**. The two partial SEC calls faithfully
  report absent sections in the selected current filing; they do not create an empty
  state or infer contract/guidance facts.

## Invocation evidence

| Tool | Non-sensitive argument outline | Status / error.code | Summary and bounded output | Elapsed |
|---|---|---|---|---:|
| `sec.issuer_filings` | Apple CIK; 10-K; limit 1 | `succeeded` / — | filing index, 1 filing; keys `cik/company/filings/source_coordinates` | 0.75s |
| `sec.company_financials` | Apple CIK; `Revenues` concept | `succeeded` / — | XBRL concept view; keys `key_facts/fact_pages/fact_directory` | 1.77s |
| `sec.filing_content` | Apple CIK; latest 10-K; Item 1 | `succeeded` / — | original-filing coordinates, 1 section | 1.63s |
| `sec.material_contracts_projects` | Apple CIK; default 8-K contract Items | `partial` / `sec_sections_not_found` | filing retrieved; 0 matching Items in that filing | 1.06s |
| `sec.management_disclosures` | Apple CIK; default 8-K Items 2.02/7.01/8.01 | `partial` / `sec_partial_sections` | filing retrieved; 1 matching section, remainder absent | <0.01s |
| `usaspending.award_search` | 2025 one-week window; procurement award-type family; limit 1 | `succeeded` / — | 1 award result and page metadata | 2.42s |
| `usaspending.award_detail` | generated internal identifier selected from preceding result | `succeeded` / — | 1 official award-detail record | 1.89s |
| `sam.contract_opportunities` | historical one-week posted-date window; limit 1 | `succeeded` / — | 1 official opportunity record | 1.56s |
| `regulations.rulemaking_records` | documents; semiconductor search; page size 5 | `succeeded` / — | 5 official rulemaking documents | 1.95s |
| `federal_register.documents` | list endpoint; page size 1 | `succeeded` / — | 20 records returned by provider (provider default pagination) | 1.31s |
| `congress.legislative_actions` | bill collection; limit 1 | `succeeded` / — | 1 official legislative record | 1.83s |
| `openfda.approval_milestones` | device 510(k); limit 1 | `succeeded` / — | 1 approval record | 2.30s |
| `openfda.safety_actions` | device enforcement; limit 1 | `succeeded` / — | 1 safety/enforcement record | 2.30s |
| `ir.official_feed_discovery` | Apple official IR HTTPS page; `apple.com` allowlist | `succeeded` / — | read-only candidate references and URLs; no state write | 1.31s |
| `ir.official_updates` | same allowlisted official IR page | `succeeded` / — | read-only official update-reference URLs; no state write | <0.01s |

All successful record tools returned `source_coordinates`. SEC content tools also
returned accession, primary-document and section-coordinate metadata rather than
promoting narrative text to a Document 1/2 value.

## Issues found and corrected during acceptance

1. **USAspending detail chaining:** the public award number returned by a search is
   not the detail endpoint identifier. The search tool now includes
   `generated_internal_id` in its default fields, so the caller can pass that field
   to `usaspending.award_detail` without a second ungoverned lookup.
2. **Regulations.gov pagination:** the API rejects `page[size] < 5`. The tool now
   returns an explicit `invalid_input` before making that invalid request.
3. **Issuer IR availability:** Apple IR returned 403 to the default HTTP client but
   accepts a conventional explicit HTML user agent. The IR clients now use that
   header, remain HTTPS/domain-allowlisted and read-only, and expose selected
   official update-reference links as structured `candidate_urls` / `updates`.

## Acceptance boundaries

- SEC partial results are expected source outcomes, not implementation failures:
  filing-section availability varies by issuer and filing. The caller must select a
  relevant accession when a particular contract or earnings disclosure is required.
- Federal Register accepted the request but returned its provider-default 20 rows
  despite the supplied `per_page=1`; this is recorded as an endpoint pagination
  behavior to normalize later, not silently treated as one record.
- This validates retrieval, error semantics and provenance only. No derived indicator,
  State Value, expectation, or Document 2 Gap was calculated.
