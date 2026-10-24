"""Official filing discovery and lossless economic-document assembly."""

from ..schema import PollingConfig, SchedulerConstraints, SourceDefinition, SourceKind

SOURCE_ID = "sec_edgar"
ADAPTER_REF = "builtin:sec_edgar@1"
ITEMS = {
    "4.02": "Non-reliance on financial statements",
    "2.04": "Triggering events accelerating financial obligations",
    "1.02": "Termination of material agreement",
    "3.02": "Unregistered sales of equity securities",
    "1.01": "Entry into material agreement",
    "1.05": "Material cybersecurity incident",
    "8.01": "Other events",
    "4.01": "Change in certifying accountant",
    "5.02": "Directors, officers and compensation",
    "2.03": "Creation of financial obligations",
    "2.05": "Costs of exit or disposal activities",
    "3.01": "Delisting notice or failure to satisfy listing standards",
    "3.03": "Material modification to rights of security holders",
    "2.06": "Material impairments",
}
FORM_TOPICS = {
    "424B5": "Prospectus supplement",
    "6-K": "Foreign issuer report",
    "6-K/A": "Amended foreign issuer report",
    "SC 13D": "Beneficial ownership report",
    "SC 13D/A": "Amended beneficial ownership report",
    "SCHEDULE 13D": "Beneficial ownership report",
    "SCHEDULE 13D/A": "Amended beneficial ownership report",
    "S-3": "Shelf registration statement",
    "F-3": "Foreign issuer shelf registration statement",
    "S-3ASR": "Automatically effective shelf registration statement",
    "F-3ASR": "Automatically effective foreign issuer shelf registration statement",
}
FORMS = {"8-K", "8-K/A", *FORM_TOPICS}


def initial_source():
    return SourceDefinition(
        source_id=SOURCE_ID,
        display_name="SEC EDGAR / Material Filings",
        kind=SourceKind.API,
        adapter_ref=ADAPTER_REF,
        entry_url="https://data.sec.gov/submissions/",
        content_language="en",
        parameter_schema={
            "type": "object",
            "properties": {"cik": {"type": "string", "pattern": "^[0-9]{1,10}$"}},
            "additionalProperties": False,
        },
        default_polling_config=PollingConfig(target_interval_seconds=30),
        scheduler_group="sec_edgar",
        scheduler_constraints=SchedulerConstraints(
            minimum_request_gap_seconds=0, max_concurrency=3
        ),
    )
