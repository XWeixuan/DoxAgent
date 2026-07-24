"""Build controlled Concept, Metric, Unit, and Attribute Definition catalogs."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from common import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WORK_DIR,
    clean_aliases,
    download,
    id_slug,
    runtime_normalize,
    unique_id,
    write_json,
)


FASB_TAXONOMY = "https://xbrl.fasb.org/us-gaap/2026/us-gaap-2026.zip"
ISO_CURRENCIES = (
    "https://www.six-group.com/dam/download/financial-information/data-center/"
    "iso-currrency/lists/list-one.xml"
)


PREDICATE_SPECS = """
report metric|reported;posted;recorded;disclosed results;announced results
increase|increased;rose;grew;climbed;advanced;gained;expanded
decrease|decreased;fell;declined;dropped;contracted;slid
remain unchanged|was flat;held steady;remained stable;unchanged
accelerate|accelerated;sped up;gained momentum
decelerate|decelerated;slowed;lost momentum
improve|improved;strengthened;enhanced
worsen|worsened;deteriorated;weakened
exceed estimate|beat estimates;topped expectations;came in above consensus;outperformed forecast
miss estimate|missed estimates;fell short of expectations;came in below consensus;underperformed forecast
meet estimate|met estimates;in line with expectations;matched consensus
set record|reached a record;hit an all-time high;record high
reach low|hit a low;record low;bottomed
recover|recovered;rebounded;returned to growth
turn profitable|became profitable;returned to profit;swung to profit
turn loss-making|swung to a loss;posted a loss;became unprofitable
guide metric|guided;issued guidance;provided an outlook;forecast
raise guidance|raised guidance;increased its outlook;boosted forecast
lower guidance|lowered guidance;cut its outlook;reduced forecast
reiterate guidance|reaffirmed guidance;maintained outlook;repeated forecast
withdraw guidance|withdrew guidance;suspended outlook;removed forecast
initiate guidance|introduced guidance;provided first-time outlook
narrow guidance range|narrowed guidance;tightened the range
widen guidance range|widened guidance;expanded the range
preannounce results|preannounced;issued preliminary results;previewed results
warn on results|issued a profit warning;warned;flagged weakness
declare dividend|declared a dividend;announced a dividend
raise dividend|increased dividend;boosted dividend
cut dividend|reduced dividend;lowered dividend
suspend dividend|paused dividend;suspended payout
pay dividend|paid a dividend;distributed dividend
repurchase shares|bought back shares;repurchased stock;executed buyback
authorize buyback|approved a buyback;authorized share repurchase
issue shares|issued stock;sold shares;equity issuance
offer securities|launched an offering;priced an offering;securities offering
split stock|announced a stock split;split shares
reverse split stock|announced a reverse split;consolidated shares
issue debt|issued debt;sold bonds;raised debt
refinance debt|refinanced;replaced debt;extended maturities
repay debt|repaid debt;retired debt;paid down borrowings
default on debt|defaulted;missed a debt payment
redeem securities|redeemed notes;called bonds;retired securities
raise capital|raised capital;secured financing;funded
announce acquisition|agreed to acquire;announced a takeover;buyout announcement
complete acquisition|closed the acquisition;completed the takeover
terminate acquisition|abandoned the acquisition;called off the deal
receive acquisition offer|received a bid;got a takeover offer
sell business|divested;sold a unit;disposed of business
spin off business|spun off;separated a business
merge|agreed to merge;combined with;business combination
form joint venture|created a joint venture;formed a JV
partner with|partnered with;formed a partnership;collaborated with
sign agreement|entered into an agreement;signed a deal;executed agreement
win contract|won a contract;secured an award;was selected
lose contract|lost a contract;was not selected
renew contract|renewed an agreement;extended a contract
terminate contract|ended an agreement;cancelled a contract
invest in|invested in;made an investment;committed capital
divest investment|sold its stake;exited an investment
take stake|acquired a stake;bought an interest
increase stake|raised its stake;added to its holding
reduce stake|cut its stake;trimmed its holding
launch product|launched;introduced a product;unveiled
announce product|announced a product;previewed a product
update product|released an update;upgraded a product
discontinue product|discontinued;ended a product;retired a product
recall product|recalled a product;issued a recall
approve product|approved a product;granted approval;authorized sale
reject product|rejected an application;denied approval
file application|submitted an application;filed for approval
start trial|began a trial;initiated a study
complete trial|completed a trial;finished a study
meet trial endpoint|met the endpoint;achieved study goal
miss trial endpoint|failed the endpoint;did not meet study goal
build facility|began construction;is building a facility;broke ground
open facility|opened a facility;inaugurated a site
expand facility|expanded a facility;added capacity;facility expansion
close facility|closed a facility;shut a site
sell facility|sold a facility;disposed of a plant
acquire facility|bought a facility;acquired a plant
restart facility|restarted operations;reopened a site
suspend operations|halted operations;paused production;shut down temporarily
resume operations|resumed operations;restarted production
increase production|raised output;boosted production;ramped production
decrease production|cut output;reduced production;curtailed production
start production|began production;entered production;commenced manufacturing
end production|ceased production;stopped manufacturing
ship product|shipped;began deliveries;delivered product
delay shipment|delayed deliveries;pushed back shipments
receive order|received an order;booked an order
cancel order|cancelled an order;lost an order
increase demand|demand increased;demand strengthened
decrease demand|demand fell;demand weakened
increase supply|supply increased;added supply
decrease supply|supply declined;tightened supply
raise price|increased prices;hiked prices;repriced higher
cut price|reduced prices;lowered prices;discounted
change pricing|adjusted pricing;revised prices
gain market share|took share;share increased
lose market share|ceded share;share declined
enter market|entered a market;expanded into
exit market|left a market;withdrew from a market
hire executive|hired;recruited;named an executive
appoint executive|appointed;selected;promoted to
elect director|elected to the board;joined the board
resign|resigned;stepped down;departed
retire|retired;announced retirement
terminate employee|dismissed;fired;removed
lay off employees|laid off;cut jobs;reduced headcount
add employees|hired workers;increased headcount;added jobs
restructure|restructured;launched restructuring;reorganized
file bankruptcy|filed for bankruptcy;sought bankruptcy protection
emerge from bankruptcy|exited bankruptcy;completed restructuring
liquidate|entered liquidation;wound down
investigate|opened an investigation;is probing;launched a review
sue|filed a lawsuit;sued;brought an action
settle litigation|settled;reached a settlement;resolved claims
win litigation|won the case;prevailed in court
lose litigation|lost the case;court ruled against
charge violation|charged;accused;alleged violations
fine|fined;imposed a penalty;assessed a civil penalty
approve transaction|approved the deal;cleared the transaction
block transaction|blocked the deal;challenged the transaction
grant license|issued a license;licensed;granted a permit
revoke license|revoked a license;withdrew authorization
impose restriction|restricted;imposed limits;banned
lift restriction|removed restrictions;lifted a ban
enact policy|adopted a policy;passed a law;issued a rule
propose policy|proposed a rule;introduced legislation
delay policy|postponed a rule;delayed implementation
repeal policy|repealed a rule;rescinded policy
upgrade rating|upgraded;raised its rating
downgrade rating|downgraded;cut its rating
affirm rating|affirmed;maintained its rating
initiate coverage|initiated coverage;started coverage
resume coverage|resumed coverage;reinstated coverage
drop coverage|discontinued coverage;ceased coverage
raise price target|raised its price target;increased target price
lower price target|cut its price target;reduced target price
set price target|set a price target;assigned target price
recommend buy|rated buy;recommended purchase
recommend hold|rated hold;recommended neutral
recommend sell|rated sell;recommended underperform
announce strategy|outlined a strategy;unveiled a plan
change strategy|shifted strategy;revised its plan
achieve milestone|reached a milestone;completed a milestone
delay project|delayed a project;pushed back schedule
cancel project|cancelled a project;abandoned a project
start project|started a project;launched a program
complete project|completed a project;finished a program
disclose incident|reported an incident;announced an event
suffer outage|experienced an outage;service went down
restore service|restored service;resolved the outage
suffer cyberattack|was hacked;experienced a cyberattack
remediate issue|fixed the issue;implemented remediation
reduce emissions|cut emissions;lowered carbon output
set sustainability target|set an emissions target;announced climate goal
miss sustainability target|fell short of climate target;missed ESG goal
"""


OTHER_CONCEPT_SPECS: dict[str, str] = {
    "ACCOUNTING_BASIS": """
GAAP|generally accepted accounting principles;reported GAAP
non-GAAP|non gaap;adjusted;underlying;non-IFRS
IFRS|international financial reporting standards
statutory|statutory basis;reported statutory
cash basis|cash accounting
accrual basis|accrual accounting
constant currency|currency neutral;FX neutral
organic|organic basis;excluding acquisitions
pro forma|pro-forma;as if combined
reported basis|as reported;reported
core basis|core results;core earnings
normalized basis|normalized results
""",
    "COMPARISON_BASIS": """
year over year|YoY;y/y;versus prior year;annual comparison
quarter over quarter|QoQ;q/q;sequentially
month over month|MoM;m/m
week over week|WoW;w/w
period over period|PoP;period-on-period
versus guidance|against guidance;compared with outlook
versus consensus|against consensus;compared with estimates;Street comparison
versus prior guidance|compared with previous outlook
constant currency comparison|FX-neutral comparison
reported comparison|as-reported comparison
organic comparison|like-for-like;LFL
same store comparison|comparable store;comp sales
trailing twelve months|TTM;LTM;last twelve months
year to date|YTD;year-to-date
quarter to date|QTD;quarter-to-date
month to date|MTD;month-to-date
since inception|from inception
versus baseline|against baseline;relative to base case
""",
    "GUIDANCE_ACTION": """
initiate guidance|introduce outlook;first-time guidance
raise guidance|increase outlook;boost forecast
lower guidance|cut outlook;reduce forecast
reiterate guidance|reaffirm outlook;maintain guidance
withdraw guidance|suspend outlook;remove guidance
narrow guidance|tighten range;narrow outlook
widen guidance|expand range;widen outlook
update guidance|revise outlook;adjust forecast
extend guidance horizon|add future period outlook
quantify guidance|provide numeric outlook
qualify guidance|provide qualitative outlook
preannounce|issue preliminary results
""",
    "ANALYST_ACTION": """
initiate coverage|start coverage
resume coverage|reinstate coverage
drop coverage|discontinue coverage
upgrade|raise rating
downgrade|lower rating
reiterate|maintain rating;affirm view
raise price target|increase target price
lower price target|cut target price
set price target|assign target price
add to focus list|add to conviction list
remove from focus list|remove from conviction list
raise estimate|increase estimate
lower estimate|cut estimate
publish note|issue research report
place under review|rating under review
""",
    "LIFECYCLE_STAGE": """
planned|proposed;in planning
announced|unveiled;publicly announced
development|in development;being developed
permitting|in permitting;awaiting permit
approved|authorized;cleared
under construction|being built;construction stage
testing|in testing;trial stage
pilot|pilot stage;demonstration phase
pre-commercial|precommercial;before commercialization
commercial|commercial stage;in market
operational|in operation;operating
ramp-up|ramping;scale-up
suspended|paused;on hold
delayed|postponed;behind schedule
cancelled|canceled;abandoned
completed|finished;delivered
closed|shut;decommissioned
retired|end of life;sunset
bankrupt|in bankruptcy
liquidation|winding down
""",
    "RATING": """
strong buy|conviction buy;top pick
buy|outperform;overweight;accumulate
hold|neutral;market perform;equal weight;sector perform
sell|underperform;underweight;reduce
strong sell|conviction sell
positive|positive outlook
negative|negative outlook
stable|stable outlook
investment grade|IG
high yield|speculative grade;junk
AAA|triple A
AA|double A
A|single A
BBB|triple B
BB|double B
B|single B
CCC|triple C
CC|double C
C|single C
D|default rating
watch positive|positive watch
watch negative|negative watch
not rated|NR
""",
}


KPI_SPECS = """
ADJUSTED_EBITDA|Adjusted EBITDA|adjusted earnings before interest taxes depreciation and amortization
EBITDA|EBITDA|earnings before interest taxes depreciation and amortization
EBIT|EBIT|earnings before interest and taxes
FREE_CASH_FLOW|Free cash flow|FCF;free cashflow
FREE_CASH_FLOW_MARGIN|Free cash flow margin|FCF margin
ADJUSTED_FREE_CASH_FLOW|Adjusted free cash flow|adjusted FCF
NET_DEBT|Net debt|debt net of cash
NET_DEBT_TO_EBITDA|Net debt to EBITDA|net leverage ratio
GROSS_LEVERAGE|Gross leverage|gross debt leverage
LIQUIDITY|Liquidity|available liquidity
BOOKINGS|Bookings|orders booked
BILLINGS|Billings|customer billings
BACKLOG|Backlog|order backlog
REMAINING_PERFORMANCE_OBLIGATIONS|Remaining performance obligations|RPO;contracted backlog
ANNUAL_RECURRING_REVENUE|Annual recurring revenue|ARR
MONTHLY_RECURRING_REVENUE|Monthly recurring revenue|MRR
ANNUAL_CONTRACT_VALUE|Annual contract value|ACV
TOTAL_CONTRACT_VALUE|Total contract value|TCV
NET_REVENUE_RETENTION|Net revenue retention|NRR;net dollar retention
GROSS_REVENUE_RETENTION|Gross revenue retention|GRR
CUSTOMER_RETENTION_RATE|Customer retention rate|retention rate
CHURN_RATE|Churn rate|customer churn;revenue churn
CUSTOMER_COUNT|Customer count|number of customers
PAID_CUSTOMERS|Paid customers|paying customers
ACTIVE_USERS|Active users|active user count
MONTHLY_ACTIVE_USERS|Monthly active users|MAU
DAILY_ACTIVE_USERS|Daily active users|DAU
WEEKLY_ACTIVE_USERS|Weekly active users|WAU
SUBSCRIBERS|Subscribers|subscriber count
SUBSCRIBER_ADDITIONS|Subscriber additions|net adds;subscriber net additions
AVERAGE_REVENUE_PER_USER|Average revenue per user|ARPU
AVERAGE_REVENUE_PER_ACCOUNT|Average revenue per account|ARPA
AVERAGE_ORDER_VALUE|Average order value|AOV
GROSS_MERCHANDISE_VALUE|Gross merchandise value|GMV
GROSS_PAYMENT_VOLUME|Gross payment volume|GPV
TOTAL_PAYMENT_VOLUME|Total payment volume|TPV
TRANSACTION_VOLUME|Transaction volume|transactions processed
TAKE_RATE|Take rate|monetization rate
SAME_STORE_SALES|Same-store sales|comparable store sales;comp sales
SAME_STORE_SALES_GROWTH|Same-store sales growth|comparable sales growth
STORE_COUNT|Store count|number of stores
NEW_STORE_OPENINGS|New store openings|stores opened
OCCUPANCY_RATE|Occupancy rate|utilization of space
REVPAR|Revenue per available room|RevPAR
ADR_HOTEL|Average daily room rate|hotel ADR
LOAD_FACTOR|Load factor|passenger load factor
REVENUE_PASSENGER_MILES|Revenue passenger miles|RPM
AVAILABLE_SEAT_MILES|Available seat miles|ASM
YIELD_PER_PASSENGER_MILE|Passenger yield|yield per RPM
DELIVERIES|Deliveries|units delivered
PRODUCTION_VOLUME|Production volume|output volume;units produced
SHIPMENTS|Shipments|units shipped
BIT_SHIPMENTS|Bit shipments|memory bit shipments
WAFER_STARTS|Wafer starts|wafer starts per month;WSPM
UTILIZATION_RATE|Utilization rate|capacity utilization
PRODUCTION_CAPACITY|Production capacity|manufacturing capacity
YIELD_RATE|Manufacturing yield|production yield
DATA_CENTER_REVENUE|Data center revenue|datacenter revenue
CLOUD_REVENUE|Cloud revenue|cloud sales
ADVERTISING_REVENUE|Advertising revenue|ad revenue
SUBSCRIPTION_REVENUE|Subscription revenue|recurring subscription sales
SERVICES_REVENUE|Services revenue|service sales
PRODUCT_REVENUE|Product revenue|product sales
LICENSE_REVENUE|License revenue|licensing revenue
SEGMENT_REVENUE|Segment revenue|business unit revenue
ORGANIC_REVENUE_GROWTH|Organic revenue growth|organic sales growth
CONSTANT_CURRENCY_REVENUE_GROWTH|Constant-currency revenue growth|FX-neutral sales growth
ADJUSTED_REVENUE|Adjusted revenue|non-GAAP revenue
ADJUSTED_GROSS_PROFIT|Adjusted gross profit|non-GAAP gross profit
ADJUSTED_GROSS_MARGIN|Adjusted gross margin|non-GAAP gross margin
ADJUSTED_OPERATING_INCOME|Adjusted operating income|non-GAAP operating income
ADJUSTED_OPERATING_MARGIN|Adjusted operating margin|non-GAAP operating margin
ADJUSTED_NET_INCOME|Adjusted net income|non-GAAP net income
ADJUSTED_EPS|Adjusted earnings per share|adjusted EPS;non-GAAP EPS
CASH_EPS|Cash earnings per share|cash EPS
FUNDS_FROM_OPERATIONS|Funds from operations|FFO
ADJUSTED_FUNDS_FROM_OPERATIONS|Adjusted funds from operations|AFFO
NET_OPERATING_INCOME_REIT|Net operating income|property NOI;REIT NOI
NET_INTEREST_MARGIN|Net interest margin|NIM
NET_INTEREST_INCOME|Net interest income|NII
EFFICIENCY_RATIO|Efficiency ratio|bank efficiency ratio
COMMON_EQUITY_TIER_1_RATIO|Common equity tier 1 ratio|CET1 ratio
TIER_1_CAPITAL_RATIO|Tier 1 capital ratio|tier one ratio
TOTAL_CAPITAL_RATIO|Total capital ratio|regulatory capital ratio
LOAN_TO_DEPOSIT_RATIO|Loan-to-deposit ratio|LDR
NET_CHARGE_OFF_RATE|Net charge-off rate|NCO rate
NONPERFORMING_LOAN_RATIO|Nonperforming loan ratio|NPL ratio
ASSETS_UNDER_MANAGEMENT|Assets under management|AUM
ASSETS_UNDER_ADVISEMENT|Assets under advisement|AUA
NET_FLOWS|Net flows|net inflows;net outflows
COMBINED_RATIO|Combined ratio|insurance combined ratio
LOSS_RATIO|Loss ratio|insurance loss ratio
PREMIUM_GROWTH|Premium growth|insurance premium growth
PROVED_RESERVES|Proved reserves|1P reserves
PRODUCTION_RATE|Production rate|daily production
UNIT_PRODUCTION_COST|Unit production cost|cost per unit
BREAKEVEN_PRICE|Breakeven price|break-even price
RESERVE_REPLACEMENT_RATIO|Reserve replacement ratio|RRR
HASH_RATE|Hash rate|hashrate
POWER_CAPACITY|Power capacity|installed capacity
RENEWABLE_CAPACITY|Renewable capacity|clean energy capacity
EMISSIONS|Greenhouse gas emissions|GHG emissions;carbon emissions
EMISSIONS_INTENSITY|Emissions intensity|carbon intensity
EMPLOYEE_COUNT|Employee count|headcount;number of employees
VOLUNTARY_TURNOVER|Voluntary turnover|employee attrition
PRICE_TARGET|Price target|target price;PT
MARKET_SHARE|Market share|share of market
TAM|Total addressable market|TAM
SERVICEABLE_ADDRESSABLE_MARKET|Serviceable addressable market|SAM
"""


ATTRIBUTE_SPECS: list[tuple[str, list[str], str, str]] = [
    ("company", ["issuer", "corporation", "corporate_subject"], "COMPANY", "HARD"),
    ("counterparty", ["other_party", "transaction_party"], "COMPANY", "HARD"),
    ("acquirer", ["buyer", "bidder"], "COMPANY", "HARD"),
    ("target_company", ["target", "acquisition_target"], "COMPANY", "HARD"),
    ("seller", ["vendor", "divestor"], "COMPANY", "HARD"),
    ("partner", ["business_partner", "joint_venture_partner"], "COMPANY", "SOFT"),
    ("institution", ["organization", "agency"], "INSTITUTION", "HARD"),
    ("analyst_institution", ["brokerage", "research_firm"], "INSTITUTION", "HARD"),
    ("regulator", ["regulatory_body", "authority"], "INSTITUTION", "HARD"),
    ("exchange", ["listing_venue", "market_operator"], "INSTITUTION", "HARD"),
    ("source_institution", ["publisher", "media_source"], "INSTITUTION", "SOFT"),
    ("person", ["individual", "speaker"], "PERSON", "HARD"),
    ("executive", ["officer", "management_person"], "PERSON", "HARD"),
    ("analyst", ["research_analyst"], "PERSON", "HARD"),
    ("official", ["government_official", "regulatory_official"], "PERSON", "HARD"),
    ("spokesperson", ["representative"], "PERSON", "SOFT"),
    ("instrument", ["security", "financial_asset"], "INSTRUMENT", "HARD"),
    ("stock", ["shares", "equity_security"], "INSTRUMENT", "HARD"),
    ("index", ["market_index", "benchmark"], "INSTRUMENT", "HARD"),
    ("fund", ["ETF", "mutual_fund"], "INSTRUMENT", "HARD"),
    ("bond", ["note", "debt_security"], "INSTRUMENT", "HARD"),
    ("commodity", ["raw_material"], "INSTRUMENT", "HARD"),
    ("facility", ["plant", "fab", "site", "factory"], "NAMED_OBJECT", "HARD"),
    ("product", ["offering", "device", "drug"], "NAMED_OBJECT", "HARD"),
    ("product_family", ["portfolio", "product_line"], "NAMED_OBJECT", "HARD"),
    ("project", ["initiative", "development_project"], "NAMED_OBJECT", "HARD"),
    ("asset", ["physical_asset", "property"], "NAMED_OBJECT", "HARD"),
    ("technology", ["platform", "process", "architecture"], "NAMED_OBJECT", "HARD"),
    ("program", ["scheme", "campaign"], "NAMED_OBJECT", "HARD"),
    ("location", ["place", "site_location"], "PLACE", "HARD"),
    ("country", ["nation"], "PLACE", "HARD"),
    ("region", ["area", "territory"], "PLACE", "HARD"),
    ("city", ["municipality"], "PLACE", "HARD"),
    ("jurisdiction", ["legal_jurisdiction"], "PLACE", "HARD"),
    ("market", ["geographic_market", "end_market"], "PLACE", "SOFT"),
    ("metric", ["measure", "KPI", "financial_metric"], "METRIC", "HARD"),
    ("reported_metric", ["actual_metric"], "METRIC", "HARD"),
    ("guidance_metric", ["outlook_metric"], "METRIC", "HARD"),
    ("comparison_metric", ["benchmark_metric"], "METRIC", "SOFT"),
    ("value", ["amount", "reported_value"], "QUANTITY", "HARD"),
    ("range", ["value_range", "guidance_range"], "QUANTITY", "HARD"),
    ("change", ["delta", "movement"], "QUANTITY", "HARD"),
    ("price", ["transaction_price", "share_price"], "QUANTITY", "HARD"),
    ("price_target", ["target_price", "PT"], "QUANTITY", "HARD"),
    ("ownership_stake", ["stake", "interest"], "QUANTITY", "HARD"),
    ("capacity", ["production_capacity", "output_capacity"], "QUANTITY", "HARD"),
    ("headcount", ["employee_count", "jobs"], "QUANTITY", "HARD"),
    ("predicate", ["event_action", "normalized_action"], "CONCEPT", "HARD"),
    ("accounting_basis", ["basis", "reporting_basis"], "CONCEPT", "HARD"),
    ("comparison_basis", ["comparison", "comp_basis"], "CONCEPT", "HARD"),
    ("guidance_action", ["outlook_action"], "CONCEPT", "HARD"),
    ("analyst_action", ["research_action"], "CONCEPT", "HARD"),
    ("lifecycle_stage", ["stage", "status"], "CONCEPT", "HARD"),
    ("rating", ["recommendation", "credit_rating"], "CONCEPT", "HARD"),
    ("artifact", ["document", "source_document"], "ARTIFACT", "SOFT"),
    ("filing", ["SEC_filing", "regulatory_filing"], "ARTIFACT", "HARD"),
    ("earnings_release", ["results_release", "quarterly_release"], "ARTIFACT", "HARD"),
    ("report", ["analyst_report", "research_note"], "ARTIFACT", "SOFT"),
    ("agreement", ["contract_document", "deal_document"], "ARTIFACT", "SOFT"),
    ("reason", ["cause", "driver", "rationale"], "LITERAL", "CLAIM"),
    ("impact", ["effect", "consequence"], "LITERAL", "CLAIM"),
    ("risk", ["risk_factor", "uncertainty"], "LITERAL", "CLAIM"),
    ("condition", ["precondition", "contingency"], "LITERAL", "CLAIM"),
    ("assumption", ["model_assumption"], "LITERAL", "CLAIM"),
    ("strategy", ["strategic_plan", "approach"], "LITERAL", "CLAIM"),
    ("outlook", ["management_view", "forward_view"], "LITERAL", "CLAIM"),
    ("commentary", ["management_comment", "statement"], "LITERAL", "CLAIM"),
    ("segment", ["business_segment", "division"], "LITERAL", "SOFT"),
    ("customer", ["client", "buyer_name"], "LITERAL", "CLAIM"),
    ("supplier", ["vendor_name", "provider"], "LITERAL", "CLAIM"),
    ("industry", ["sector", "vertical"], "LITERAL", "SOFT"),
    ("form_type", ["filing_type", "SEC_form"], "LITERAL", "HARD"),
    ("accession", ["accession_number", "filing_id"], "LITERAL", "HARD"),
    ("fiscal_period", ["reporting_period", "reference_period"], "LITERAL", "HARD"),
    ("date", ["event_date", "effective_date"], "LITERAL", "HARD"),
    ("deadline", ["due_date", "target_date"], "LITERAL", "SOFT"),
    ("duration", ["term", "tenor"], "LITERAL", "SOFT"),
]


UNIT_SPECS: list[tuple[str, str, float | int, str, list[str]]] = [
    ("THOUSAND", "thousand", 1_000, "SCALE", ["K", "k", "thousand"]),
    ("MILLION", "million", 1_000_000, "SCALE", ["M", "mm", "mn", "million"]),
    ("BILLION", "billion", 1_000_000_000, "SCALE", ["B", "bn", "billion"]),
    ("TRILLION", "trillion", 1_000_000_000_000, "SCALE", ["T", "tn", "trillion"]),
    ("PERCENT", "percent", 1, "RATIO", ["%", "percent", "pct"]),
    ("PERCENTAGE_POINT", "percentage point", 1, "RATIO", ["percentage points", "ppt", "pp"]),
    ("BASIS_POINT", "basis point", 0.0001, "RATIO", ["basis points", "bp", "bps"]),
    ("MULTIPLE", "multiple", 1, "RATIO", ["x", "times", "turns"]),
    ("PURE", "pure number", 1, "UNIT", ["pure", "count", "number"]),
    ("SHARE", "share", 1, "UNIT", ["shares", "common shares"]),
    ("USD_PER_SHARE", "US dollars per share", 1, "UNIT", ["USD/share", "$/share", "dollars per share"]),
    ("PERSON", "person", 1, "UNIT", ["people", "employee", "employees", "worker", "workers"]),
    ("SUBSCRIBER", "subscriber", 1, "UNIT", ["subscribers", "subscription"]),
    ("CUSTOMER", "customer", 1, "UNIT", ["customers", "client", "clients"]),
    ("USER", "user", 1, "UNIT", ["users", "account", "accounts"]),
    ("UNIT_COUNT", "unit", 1, "UNIT", ["units", "item", "items"]),
    ("VEHICLE", "vehicle", 1, "UNIT", ["vehicles", "cars", "automobiles"]),
    ("BARREL", "barrel", 1, "UNIT", ["barrels", "bbl", "bbls"]),
    ("BARREL_PER_DAY", "barrel per day", 1, "UNIT", ["barrels per day", "bpd", "boe/d"]),
    ("TON", "ton", 1, "UNIT", ["tons", "short ton"]),
    ("METRIC_TON", "metric ton", 1, "UNIT", ["metric tons", "tonne", "tonnes", "t"]),
    ("KILOGRAM", "kilogram", 1, "UNIT", ["kg", "kilograms"]),
    ("POUND", "pound", 1, "UNIT", ["lb", "lbs", "pounds"]),
    ("OUNCE", "ounce", 1, "UNIT", ["oz", "ounces"]),
    ("TROY_OUNCE", "troy ounce", 1, "UNIT", ["troy ounces", "oz t"]),
    ("LITER", "liter", 1, "UNIT", ["liters", "litre", "litres", "L"]),
    ("GALLON", "gallon", 1, "UNIT", ["gallons", "gal"]),
    ("METER", "meter", 1, "UNIT", ["meters", "metre", "metres", "m"]),
    ("SQUARE_FOOT", "square foot", 1, "UNIT", ["square feet", "sq ft", "ft2"]),
    ("SQUARE_METER", "square meter", 1, "UNIT", ["square meters", "sq m", "m2"]),
    ("MILE", "mile", 1, "UNIT", ["miles", "mi"]),
    ("KILOMETER", "kilometer", 1, "UNIT", ["kilometers", "kilometre", "km"]),
    ("MEGAWATT", "megawatt", 1, "UNIT", ["MW", "megawatts"]),
    ("GIGAWATT", "gigawatt", 1, "UNIT", ["GW", "gigawatts"]),
    ("KILOWATT_HOUR", "kilowatt-hour", 1, "UNIT", ["kWh", "kilowatt hours"]),
    ("MEGAWATT_HOUR", "megawatt-hour", 1, "UNIT", ["MWh", "megawatt hours"]),
    ("GIGAWATT_HOUR", "gigawatt-hour", 1, "UNIT", ["GWh", "gigawatt hours"]),
    ("BYTE", "byte", 1, "UNIT", ["bytes"]),
    ("GIGABYTE", "gigabyte", 1, "UNIT", ["GB", "gigabytes"]),
    ("TERABYTE", "terabyte", 1, "UNIT", ["TB", "terabytes"]),
    ("PETABYTE", "petabyte", 1, "UNIT", ["PB", "petabytes"]),
    ("SECOND", "second", 1, "UNIT", ["seconds", "sec"]),
    ("MINUTE", "minute", 1, "UNIT", ["minutes", "min"]),
    ("HOUR", "hour", 1, "UNIT", ["hours", "hr"]),
    ("DAY", "day", 1, "UNIT", ["days"]),
    ("MONTH", "month", 1, "UNIT", ["months"]),
    ("YEAR", "year", 1, "UNIT", ["years", "yr"]),
]

CURRENCY_ALIASES: dict[str, list[str]] = {
    "USD": ["$", "US$", "USD", "dollar", "dollars", "US dollar", "U.S. dollar"],
    "EUR": ["€", "EUR", "euro", "euros"],
    "GBP": ["£", "GBP", "pound sterling", "sterling"],
    "JPY": ["¥", "JPY", "yen", "Japanese yen"],
    "CNY": ["CN¥", "CNY", "RMB", "renminbi", "yuan", "Chinese yuan"],
    "HKD": ["HK$", "HKD", "Hong Kong dollar"],
    "CAD": ["C$", "CAD", "Canadian dollar"],
    "AUD": ["A$", "AUD", "Australian dollar"],
    "NZD": ["NZ$", "NZD", "New Zealand dollar"],
    "CHF": ["CHF", "Swiss franc"],
    "INR": ["₹", "INR", "Indian rupee"],
    "KRW": ["₩", "KRW", "South Korean won", "Korean won"],
    "SGD": ["S$", "SGD", "Singapore dollar"],
    "TWD": ["NT$", "TWD", "New Taiwan dollar", "Taiwan dollar"],
    "BRL": ["R$", "BRL", "Brazilian real"],
    "MXN": ["MX$", "MXN", "Mexican peso"],
    "RUB": ["₽", "RUB", "Russian ruble", "rouble"],
    "SEK": ["SEK", "Swedish krona"],
    "NOK": ["NOK", "Norwegian krone"],
    "DKK": ["DKK", "Danish krone"],
    "ZAR": ["ZAR", "South African rand", "rand"],
    "SAR": ["SAR", "Saudi riyal"],
    "AED": ["AED", "UAE dirham", "dirham"],
    "ILS": ["₪", "ILS", "Israeli new shekel", "shekel"],
    "TRY": ["₺", "TRY", "Turkish lira"],
    "PLN": ["PLN", "Polish zloty", "złoty"],
}


def _parse_specs(raw: str) -> Iterable[tuple[str, list[str]]]:
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, aliases = line.partition("|")
        yield name.strip(), [item.strip() for item in aliases.split(";") if item.strip()]


def build_concepts(output: Path) -> list[dict[str, Any]]:
    concepts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for kind, specs in [("PREDICATE", PREDICATE_SPECS), *OTHER_CONCEPT_SPECS.items()]:
        for name, aliases in _parse_specs(specs):
            concept_id = f"{kind}_{id_slug(name)}"
            if concept_id in seen_ids:
                continue
            seen_ids.add(concept_id)
            concepts.append({"id": concept_id, "name": name, "kind": kind, "aliases": clean_aliases(name, aliases)})
    concepts.sort(key=lambda item: (item["kind"], item["id"]))
    write_json(output / "concepts.json", concepts)
    return concepts


def _fasb_files(path: Path) -> tuple[bytes, bytes]:
    with zipfile.ZipFile(path) as archive:
        xsd_name = next(name for name in archive.namelist() if name.endswith("/elts/us-gaap-2026.xsd"))
        label_name = next(name for name in archive.namelist() if name.endswith("/elts/us-gaap-lab-2026.xml"))
        return archive.read(xsd_name), archive.read(label_name)


def _fasb_labels(label_xml: bytes) -> dict[str, list[str]]:
    root = ET.fromstring(label_xml)
    xlink = "{http://www.w3.org/1999/xlink}"
    link = "{http://www.xbrl.org/2003/linkbase}"
    resources: dict[str, str] = {}
    locators: dict[str, str] = {}
    labels: dict[str, list[str]] = defaultdict(list)
    for node in root.iter():
        if node.tag == f"{link}loc":
            href = node.attrib.get(f"{xlink}href", "")
            locators[node.attrib.get(f"{xlink}label", "")] = href.rsplit("#us-gaap_", 1)[-1]
        elif node.tag == f"{link}label" and node.text:
            role = node.attrib.get(f"{xlink}role", "")
            if role.endswith(("/label", "/terseLabel", "/verboseLabel")):
                resources[node.attrib.get(f"{xlink}label", "")] = " ".join(node.text.split())
    for arc in root.iter(f"{link}labelArc"):
        tag = locators.get(arc.attrib.get(f"{xlink}from", ""))
        label = resources.get(arc.attrib.get(f"{xlink}to", ""))
        if tag and label and "deprecated" not in label.casefold():
            labels[tag].append(label)
    return labels


def _split_camel(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return " ".join(value.split())


COMMON_GAAP_IDS: dict[str, str] = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "REVENUE",
    "NetIncomeLoss": "NET_INCOME_GAAP",
    "EarningsPerShareDiluted": "EPS_GAAP",
    "EarningsPerShareBasic": "EPS_BASIC_GAAP",
    "GrossProfit": "GROSS_PROFIT",
    "GrossProfitPercent": "GROSS_MARGIN",
    "OperatingIncomeLoss": "OPERATING_INCOME_GAAP",
    "OperatingIncomeLossPercent": "OPERATING_MARGIN",
    "ProfitLoss": "PROFIT_LOSS_GAAP",
    "Assets": "TOTAL_ASSETS",
    "Liabilities": "TOTAL_LIABILITIES",
    "StockholdersEquity": "STOCKHOLDERS_EQUITY",
    "CashAndCashEquivalentsAtCarryingValue": "CASH_AND_CASH_EQUIVALENTS",
    "LongTermDebtCurrent": "CURRENT_LONG_TERM_DEBT",
    "LongTermDebtNoncurrent": "LONG_TERM_DEBT",
    "NetCashProvidedByUsedInOperatingActivities": "OPERATING_CASH_FLOW",
    "NetCashProvidedByUsedInInvestingActivities": "INVESTING_CASH_FLOW",
    "NetCashProvidedByUsedInFinancingActivities": "FINANCING_CASH_FLOW",
    "PaymentsToAcquirePropertyPlantAndEquipment": "CAPEX",
    "ResearchAndDevelopmentExpense": "RESEARCH_AND_DEVELOPMENT_EXPENSE",
    "SellingGeneralAndAdministrativeExpense": "SGA_EXPENSE",
}


def build_metrics(downloads: Path, output: Path) -> list[dict[str, Any]]:
    taxonomy = download(FASB_TAXONOMY, downloads / "us-gaap-2026.zip")
    xsd_bytes, label_bytes = _fasb_files(taxonomy)
    labels = _fasb_labels(label_bytes)
    root = ET.fromstring(xsd_bytes)
    metrics: list[dict[str, Any]] = []
    used_ids: dict[str, str] = {}
    excluded_suffixes = ("Axis", "Domain", "Member", "Abstract", "Table", "TextBlock", "Policy", "Disclosure")
    numeric_markers = ("monetaryItemType", "sharesItemType", "percentItemType", "pureItemType", "perShareItemType", "integerItemType", "decimalItemType")
    for element in root.iter("{http://www.w3.org/2001/XMLSchema}element"):
        tag = element.attrib.get("name", "")
        item_type = element.attrib.get("type", "")
        if not tag or element.attrib.get("abstract") == "true" or tag.endswith(excluded_suffixes):
            continue
        if not any(marker in item_type for marker in numeric_markers):
            continue
        available_labels = labels.get(tag, [])
        name = next((label for label in available_labels if "[" not in label and "(" not in label), None)
        name = name or (available_labels[0] if available_labels else _split_camel(tag))
        metric_id = COMMON_GAAP_IDS.get(tag) or unique_id("US_GAAP", tag, tag, used_ids)
        aliases = clean_aliases(name, [tag, f"us-gaap:{tag}", _split_camel(tag), *available_labels], limit=12)
        metrics.append({"id": metric_id, "name": name, "aliases": aliases})

    existing_ids = {item["id"] for item in metrics}
    for line in KPI_SPECS.strip().splitlines():
        metric_id, name, aliases = [part.strip() for part in line.split("|", 2)]
        alias_list = [item.strip() for item in aliases.split(";") if item.strip()]
        if metric_id in existing_ids:
            target = next(item for item in metrics if item["id"] == metric_id)
            target["aliases"] = clean_aliases(target["name"], [*target["aliases"], name, *alias_list], limit=20)
        else:
            metrics.append({"id": metric_id, "name": name, "aliases": clean_aliases(name, alias_list, limit=20)})
            existing_ids.add(metric_id)
    # Required hard-identity distinctions that do not come from GAAP taxonomy.
    for metric_id, name, aliases in [
        ("NET_INCOME_NON_GAAP", "Non-GAAP net income", ["adjusted net income", "non gaap net income"]),
        ("EPS_NON_GAAP", "Non-GAAP earnings per share", ["adjusted EPS", "non gaap EPS"]),
        ("UNKNOWN_METRIC", "Unknown metric", []),
    ]:
        if metric_id not in existing_ids:
            metrics.append({"id": metric_id, "name": name, "aliases": clean_aliases(name, aliases)})
    metrics.sort(key=lambda item: item["id"])
    write_json(output / "metrics.json", metrics)
    return metrics


def build_units(downloads: Path, output: Path) -> list[dict[str, Any]]:
    iso_path = download(ISO_CURRENCIES, downloads / "iso-list-one.xml")
    root = ET.parse(iso_path).getroot()
    currency_names: dict[str, set[str]] = defaultdict(set)
    for row in root.findall("./CcyTbl/CcyNtry"):
        code = (row.findtext("Ccy") or "").strip()
        name = (row.findtext("CcyNm") or "").strip()
        if code and name:
            currency_names[code].add(name)
    units: list[dict[str, Any]] = []
    for code, names in sorted(currency_names.items()):
        display = sorted(names, key=lambda value: (len(value), value))[0]
        aliases = [code, *sorted(names), *CURRENCY_ALIASES.get(code, [])]
        units.append({"id": code, "name": display, "multiplier": 1, "kind": "CURRENCY", "aliases": clean_aliases(display, aliases, limit=20)})
    for unit_id, name, multiplier, kind, aliases in UNIT_SPECS:
        units.append({"id": unit_id, "name": name, "multiplier": multiplier, "kind": kind, "aliases": clean_aliases(name, aliases, limit=20)})
    units.sort(key=lambda item: (item["kind"], item["id"]))
    write_json(output / "units.json", units)
    return units


def build_attributes(output: Path) -> list[dict[str, Any]]:
    attributes = [
        {"key": key, "aliases": clean_aliases(key, aliases), "target": target, "use": use}
        for key, aliases, target, use in ATTRIBUTE_SPECS
    ]
    attributes.sort(key=lambda item: item["key"])
    write_json(output / "attributes.json", attributes)
    return attributes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    concepts = build_concepts(args.output_dir)
    metrics = build_metrics(args.work_dir / "downloads", args.output_dir)
    units = build_units(args.work_dir / "downloads", args.output_dir)
    attributes = build_attributes(args.output_dir)
    print(json.dumps({"concepts": len(concepts), "metrics": len(metrics), "units": len(units), "attributes": len(attributes)}))


if __name__ == "__main__":
    main()
