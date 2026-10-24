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
}
FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "424B5"}


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
