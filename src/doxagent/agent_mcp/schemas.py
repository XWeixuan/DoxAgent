"""Closed inputs for the four read tools."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Period = Literal[
    "CURRENT_TRADING_DAY", "PREVIOUS_TRADING_DAY", "TRADING_DAYS_7", "TRADING_DAYS_30", "ALL"
]
Result = Literal[
    "POLICY_HIT",
    "POLICY_RECALLED",
    "ARCHIVE",
    "EVENT_DISCOVERY",
    "FIRST_ROUND_TRADE",
    "POLICY_RELATED_EVENT",
    "SUSPECTED_DUPLICATE_HIT",
    "TRADE_INTENT",
    "TRADE_EXECUTION",
    "TRADE_NOT_EXECUTED",
    "FAILURE",
]


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    period: Period | None = None
    view_id: str | None = Field(default=None, min_length=1, max_length=512)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=20, ge=1, le=20)


class OverviewInput(ReadInput):
    section: Literal["summary", "sources", "cost", "policy"] = "summary"
    ticker: str | None = Field(default=None, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
    scope: Literal["API", "CODEX"] = "API"
    ticker_selector: str = Field(default="ALL", pattern=r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
    shell_id: str = Field(default="ALL", min_length=1, max_length=256)

    @model_validator(mode="after")
    def scope_fields(self):
        if self.section in {"sources", "policy"} and not self.ticker:
            raise ValueError("ticker is required for sources/policy")
        if self.section == "summary" and (self.ticker or self.period == "ALL"):
            raise ValueError("summary uses all tickers and bounded Overview periods")
        if self.section in {"cost", "policy"} and self.cursor:
            raise ValueError("KPI sections do not have pages")
        return self


class CasesInput(ReadInput):
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
    result: Result | None = None
    source_id: str | None = Field(default=None, min_length=1, max_length=256)


class SearchInput(CasesInput):
    q: str = Field(min_length=1, max_length=200)

    @field_validator("q")
    @classmethod
    def nonempty_query(cls, value):
        if not value.strip():
            raise ValueError("q must contain a title search term")
        return value.strip()


class DetailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
    case_id: str = Field(min_length=1, max_length=256)
    view_id: str | None = Field(default=None, min_length=1, max_length=512)
    part: Literal[
        "main",
        "w1",
        "w2",
        "w3",
        "watch_pin",
        "attempts",
        "messages",
        "candidates",
        "executions",
        "content",
    ] = "main"
    node: Literal["W1", "W2", "W3"] | None = None
    originating_node: Literal["W1_R3", "W3"] | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    content_handle: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def part_fields(self):
        if (self.part == "attempts") != (self.node is not None):
            raise ValueError("node is required only for attempts")
        if self.originating_node is not None and self.part != "candidates":
            raise ValueError("originating_node is only for candidates")
        if (self.part == "content") != (self.content_handle is not None):
            raise ValueError("content_handle is required only for content")
        if self.cursor and self.part in {"main", "w1", "w2", "w3", "watch_pin"}:
            raise ValueError("this part is not paginated")
        return self


TOOLS = {
    "doxagent_get_overview": (
        OverviewInput,
        "Read existing operational KPIs across DoxAgent. Start with section=summary; "
        "sources/cost/policy are bounded, on-demand KPI sections. "
        "No Case records or message bodies.",
    ),
    "doxagent_list_cases": (
        CasesInput,
        "Read one ticker's recent Case rows, one page at a time. "
        "Existing period/result/source filters only. No bodies or reasoning.",
    ),
    "doxagent_search_cases": (
        SearchInput,
        "Search one ticker's Case MESSAGE TITLES by literal Unicode substring. "
        "Combine existing period/result/source filters. "
        "No body, reasoning, regex or semantic search.",
    ),
    "doxagent_get_case_detail": (
        DetailInput,
        "Investigate exactly one Case. Main and named sections match the frontend; "
        "read attempts/messages/candidates/executions by page and content_handle by chunk. "
        "Never bulk-read Cases or follow research links.",
    ),
}
