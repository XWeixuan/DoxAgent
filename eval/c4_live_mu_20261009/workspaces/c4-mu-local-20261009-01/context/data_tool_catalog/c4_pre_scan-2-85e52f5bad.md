# Data MCP tool catalog

## congress_legislative_actions

- Canonical id: `congress.legislative_actions`
- Source: Congress.gov
- Availability: `available`
- Use when: Collect legislative status and action timelines.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'resource': {'type': 'string', 'enum': ['bill', 'committee-report', 'hearing']}, 'identifier': {'type': 'string', 'maxLength': 2000}, 'query': {'type': 'string', 'maxLength': 2000}, 'updated_from': {'type': 'string', 'maxLength': 2000}, 'updated_to': {'type': 'string', 'maxLength': 2000}, 'limit': {'type': 'integer', 'minimum': 1}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}}}, 'additionalProperties': False, 'required': ['resource']}`
- Fallbacks: none

## federal_register_documents

- Canonical id: `federal_register.documents`
- Source: Federal Register
- Availability: `available`
- Use when: Collect proposed/final rules and notices with formal-source links.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'document_number': {'type': 'string', 'maxLength': 2000}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}, 'minProperties': 1}}, 'additionalProperties': False, 'anyOf': [{'required': ['document_number']}, {'required': ['params']}]}`
- Fallbacks: none

## finnhub_company_news_events

- Canonical id: `finnhub.company_news_events`
- Source: Finnhub
- Availability: `available`
- Use when: Discover company events; use authoritative sources for final state values.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'from': {'type': 'string', 'maxLength': 2000}, 'to': {'type': 'string', 'maxLength': 2000}, 'limit': {'type': 'integer', 'minimum': 1}, 'include_earnings': {'type': 'boolean'}}, 'additionalProperties': False}`
- Fallbacks: none

## ir_official_feed_discovery

- Canonical id: `ir.official_feed_discovery`
- Source: Official issuer IR
- Availability: `available`
- Use when: Propose issuer IR feed configuration without persisting state.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'url': {'type': 'string', 'maxLength': 2000}, 'official_domains': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}}, 'additionalProperties': False, 'required': ['url', 'official_domains']}`
- Fallbacks: none

## ir_official_updates

- Canonical id: `ir.official_updates`
- Source: Official issuer IR
- Availability: `available`
- Use when: Collect official issuer releases and event references in read-only mode.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'url': {'type': 'string', 'maxLength': 2000}, 'official_domains': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}}, 'additionalProperties': False, 'required': ['url', 'official_domains']}`
- Fallbacks: none

## openfda_approval_milestones

- Canonical id: `openfda.approval_milestones`
- Source: openFDA
- Availability: `available`
- Use when: Collect product and regulatory approval milestones.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'dataset': {'type': 'string', 'enum': ['device/510k', 'device/pma', 'drug/drugsfda']}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}}}, 'additionalProperties': False, 'required': ['dataset']}`
- Fallbacks: none

## openfda_safety_actions

- Canonical id: `openfda.safety_actions`
- Source: openFDA
- Availability: `available`
- Use when: Collect recall, enforcement, and safety-action evidence.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'dataset': {'type': 'string', 'enum': ['device/enforcement', 'drug/enforcement', 'device/event', 'drug/event']}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}}}, 'additionalProperties': False, 'required': ['dataset']}`
- Fallbacks: none

## regulations_rulemaking_records

- Canonical id: `regulations.rulemaking_records`
- Source: Regulations.gov
- Availability: `available`
- Use when: Collect rulemaking docket, document, agency, date, and attachment evidence.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'mode': {'type': 'string', 'enum': ['documents', 'dockets']}, 'id': {'type': 'string', 'maxLength': 2000}, 'query': {'type': 'string', 'maxLength': 2000}, 'agency_id': {'type': 'string', 'maxLength': 2000}, 'posted_from': {'type': 'string', 'maxLength': 2000}, 'posted_to': {'type': 'string', 'maxLength': 2000}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}}}, 'additionalProperties': False, 'required': ['mode']}`
- Fallbacks: none

## sam_contract_opportunities

- Canonical id: `sam.contract_opportunities`
- Source: SAM.gov
- Availability: `available`
- Use when: Collect solicitations and award notices; records are not recognized revenue.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'posted_from': {'type': 'string', 'maxLength': 2000}, 'posted_to': {'type': 'string', 'maxLength': 2000}, 'query': {'type': 'string', 'maxLength': 2000}, 'q': {'type': 'string', 'maxLength': 2000}, 'limit': {'type': 'integer', 'minimum': 1}, 'params': {'type': 'object', 'maxProperties': 40, 'additionalProperties': {'type': ['string', 'number', 'integer', 'boolean', 'array', 'null']}}}, 'additionalProperties': False, 'required': ['posted_from', 'posted_to']}`
- Fallbacks: none

## sec_filing_content

- Canonical id: `sec.filing_content`
- Source: SEC EDGAR
- Availability: `available`
- Use when: Retrieve filing/exhibit evidence without converting narrative text into a state.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'cik': {'type': 'string', 'maxLength': 2000}, 'form': {'type': 'string', 'maxLength': 2000}, 'accession': {'type': 'string', 'maxLength': 2000}, 'primary_document': {'type': 'string', 'maxLength': 2000}, 'sections': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}}, 'additionalProperties': False}`
- Fallbacks: sec.filing_sections

## sec_issuer_filings

- Canonical id: `sec.issuer_filings`
- Source: SEC EDGAR
- Availability: `available`
- Use when: Locate authoritative filings by form, accession, filing date, report date, and optional exhibit inventory.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'cik': {'type': 'string', 'maxLength': 2000}, 'forms': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}, 'limit': {'type': 'integer', 'minimum': 1}, 'limit_per_form': {'type': 'boolean'}, 'include_exhibits': {'type': 'boolean'}}, 'additionalProperties': False}`
- Fallbacks: none

## sec_management_disclosures

- Canonical id: `sec.management_disclosures`
- Source: SEC EDGAR
- Availability: `available`
- Use when: Collect management guidance, outlook, KPI, and MD&A evidence.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'cik': {'type': 'string', 'maxLength': 2000}, 'form': {'type': 'string', 'maxLength': 2000}, 'accession': {'type': 'string', 'maxLength': 2000}, 'primary_document': {'type': 'string', 'maxLength': 2000}, 'sections': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}}, 'additionalProperties': False}`
- Fallbacks: sec.filing_content

## sec_material_contracts_projects

- Canonical id: `sec.material_contracts_projects`
- Source: SEC EDGAR
- Availability: `available`
- Use when: Collect public contract, order, financing, termination, and project evidence.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'cik': {'type': 'string', 'maxLength': 2000}, 'form': {'type': 'string', 'maxLength': 2000}, 'accession': {'type': 'string', 'maxLength': 2000}, 'primary_document': {'type': 'string', 'maxLength': 2000}, 'sections': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}}, 'additionalProperties': False}`
- Fallbacks: sec.filing_content

## usaspending_award_detail

- Canonical id: `usaspending.award_detail`
- Source: USAspending.gov
- Availability: `available`
- Use when: Collect authoritative award amount, agency, recipient, period, and description.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'generated_internal_id': {'type': 'string', 'maxLength': 2000}, 'award_id': {'type': 'string', 'maxLength': 2000}}, 'additionalProperties': False, 'anyOf': [{'required': ['generated_internal_id']}, {'required': ['award_id']}]}`
- Fallbacks: none

## usaspending_award_search

- Canonical id: `usaspending.award_search`
- Source: USAspending.gov
- Availability: `available`
- Use when: Collect public contract/grant awards by recipient, agency, NAICS, and period.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'filters': {'type': 'object', 'properties': {'award_type_codes': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 100}, 'time_period': {'type': 'array', 'items': {'type': 'object', 'properties': {'start_date': {'type': 'string', 'format': 'date'}, 'end_date': {'type': 'string', 'format': 'date'}}, 'required': ['start_date', 'end_date'], 'additionalProperties': False}, 'minItems': 1, 'maxItems': 20}, 'recipient_search_text': {'type': 'array', 'items': {'type': 'string', 'maxLength': 300}, 'maxItems': 20}, 'agencies': {'type': 'array', 'maxItems': 20}, 'naics_codes': {'type': 'object'}, 'psc_codes': {'type': 'object'}}, 'required': ['award_type_codes', 'time_period'], 'additionalProperties': True, 'maxProperties': 40}, 'fields': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 100}, 'page': {'type': 'integer', 'minimum': 1}, 'limit': {'type': 'integer', 'minimum': 1}, 'sort': {'type': 'string', 'maxLength': 2000}, 'order': {'type': 'string', 'maxLength': 2000}}, 'additionalProperties': False, 'required': ['filters']}`
- Fallbacks: none
