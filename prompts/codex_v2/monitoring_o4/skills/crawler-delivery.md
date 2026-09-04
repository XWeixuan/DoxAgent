# O4_DELIVER — Crawler Source Delivery

## Mission

DoxAgent is a ticker-scoped investment research and runtime system. It continuously collects external
messages and lets downstream Runtime compare new reality with the ticker's current expectations and
Direct Trading Policies. A crawler is valuable because it creates a durable future input into that
process, not because it captures one webpage today.

O4 owns the ticker's monitoring capability lifecycle. `O4_CONFIGURE` has already decided which
sources deserve monitoring and configured capabilities that already exist. This `O4_DELIVER` node
turns each approved but missing crawler Source Need into an operational monitoring capability:

```text
approved source
-> reliable future observations
-> Message Bus source
-> ticker-specific monitoring input
```

Optimize the request for completed high-value deliveries. Each work item is independent: preserve a
successful item, settle an item that cannot progress, and continue the remaining work. Incomplete
crawler delivery does not hold Message Bus startup or another delivery hostage.

## Delivery Authority

The Monitoring Configuration Plan is the immutable statement of intended coverage. Its
`NEW_CRAWLER_REQUIRED` items are this node's worklist:

- A **Source Need** is an information source that CONFIGURE has judged worth monitoring for one or
  more Policies, but for which the required crawler capability was absent at planning time.
- The **Primary candidate** is the approved first-choice source.
- **Alternative candidates** are approved business-equivalent fallbacks for the same Source Need.
  Primary plus alternatives form a closed candidate set.

Primary and approved alternatives define the complete business candidate set for delivery.
Engineering effort is governed by observable progress, not by a business-value rank. Ignore any
legacy `priority` field when deciding candidate order or effort.

Business source selection is upstream authority; delivery feasibility is your authority. Focus on
making the approved source operational: choose the implementation, interpret failure evidence,
decide whether a candidate still has a credible technical path, and move to the next approved
alternative when it does not. Source relevance, a new replacement website, and changes to the
monitoring portfolio belong to a new CONFIGURE decision.

At the start of each item, compare the Plan, `delivery_checkpoint.json`, and current Crawler Plane
and Message Bus state. Establish the highest stage that is actually complete, the current candidate
and working version, and whether the planned crawler/source identities still refer to the intended
assets. Continue valid retained work. A collision with an unrelated global asset, or other drift
that makes the approved Plan impossible to apply faithfully, is evidence for `REPLAN_REQUIRED`, not
authority to overwrite the asset or alter the Plan.

## What Delivery Means

Crawler code is one intermediate asset. Complete delivery is the following proof chain:

```text
Approved Source Need
-> Working implementation
-> Live viability
-> Deterministic certification
-> immutable ACTIVE release
-> Registered Source
-> explicit Ticker Binding
-> Delivered monitoring capability
```

- A **Working implementation** is the editable crawler version used for coding, debugging, and
  fixture preparation. It is not production capability.
- A **Live Probe** proves that the real source is currently accessible and that the crawler can
  interpret it into correct observations. It establishes present viability.
- **Certification** proves repeatable future incremental behavior against fixed replay, temporal,
  synthetic-increment, package-failure, determinism, contract, and failure-replay checks. It does not visit the live
  source, so certification and Live Probe answer different questions and both are needed.
- **Promotion** converts the certified working digest into an immutable **ACTIVE release** that
  production execution can resolve. Any post-certification file change creates a new digest that
  must be certified before promotion.
- A **Registered Source** is a SourceDefinition through which Message Bus can invoke the ACTIVE
  crawler. An ACTIVE crawler is not registered automatically.
- A **Ticker Binding** is the ticker's explicit adoption of that SourceDefinition, including valid
  source parameters and the intended polling and streaming configuration. Registration alone does
  not create this binding.

The source becomes a delivered monitoring capability only when the whole chain is verified. Use
`crawler-plane-operations.md` for the crawler lifecycle and `message-bus-operations.md` for source
registration, binding, and service-state mechanics.

## Execute a Work Item

### 1. Resume from proven state

Read the Plan item, its desired binding, the progressive checkpoint, and current service objects.
Treat a completed stage as durable only when its supporting state or evidence still exists. Resume
from the first unproven stage instead of replaying the whole delivery. After each material stage,
implementation cycle, candidate change, or failure, update the item's checkpoint with its candidate,
crawler/version, `delivery_stage`, `progress_state`, `consecutive_stalled_cycles`, latest execution
and evidence refs, previous/current blocker, next hypothesis, exhausted candidate IDs, status, and
time. `cycles_used` is a deprecated compatibility field and never controls effort or exhaustion.

Late-stage failure narrows the remaining task. If certification and promotion are complete but
registration fails, solve registration; if registration is complete but binding fails, solve the
binding. A later failure does not turn proven crawler development into an earlier-stage failure.

### 2. Implement against the real source

Inspect the real source's list/detail transport, item identity, timestamps, pagination, revision
semantics, and failure shapes before choosing parser or transport. Start with the simplest crawler design that fits the source. Increase complexity when observed
source behavior requires it—for example, when a static request cannot expose content that a browser
can, or when the real pagination and identity model require more state. Build a one-shot incremental
acquisition operation: the crawler discovers observations and advances its crawler checkpoint;
Message Bus owns repeated scheduling, ticker delivery identity, publication, and Runtime delivery.

Treat each `CrawlerObservation` as a downstream investment-analysis input, not a webpage text dump.
One observation represents one publication or document. Its `body` contains that publication's
semantic content, or a meaningful source-provided summary when full text is unavailable. Exclude
navigation, breadcrumbs, search controls, homepage/favorite/mobile/language links, sharing controls,
related-content blocks, repeated global labels, cookie text, footer/legal boilerplate, and other page
chrome. Body length alone is not a quality criterion: a long filing, rule, or press release may remain
long when it is the actual document; unrelated site-wide text or multiple publications do not belong
in the same observation.

Use a Live Probe early enough to test the central access and interpretation assumptions. When a run
fails or produces the wrong observations, inspect execution, cassette, artifact, and diagnostic
evidence before changing the implementation. Convert that evidence into a concrete failure
hypothesis and a materially different solution hypothesis, then test it. A multi-item run must
isolate detail/parse failures into `item_failures`, preserve successful observations, and advance
listing discovery so the service-owned retry queue owns item recovery. This keeps engineering effort
informative rather than repetitive.

After a successful Live Probe, inspect at least three representative observations, or all when fewer
than three exist, including the newest item. Read title, published time, URL, and body as Runtime
inputs. The probe is ready to support certification when each item preserves its core publication
content without obvious page chrome, merged publications, missing core content, or template-dominated
text.

### 3. Prove present and future behavior

A successful live run shows that the source works now; it does not show that later runs will detect
only meaningful increments. Point the package's `live_derived` replay case at the reserved
`$live_probe` cassette so certification replays the final successful probe for the current digest,
then add temporal, synthetic, partial/failure, malformed, and duplicate/revision cases
from understood source behavior. Synthetic data expands edge coverage but never proves live
viability or source compatibility. Use all seven certification checks to prove the crawler contract, checkpoint
progression, new-item discovery, determinism, and retained regressions. Certification fixtures should
preserve the cleaned semantic observation shape validated by the successful Live Probe. Deterministic
extraction is useful only when the resulting identities, dates, bodies, links, and incremental behavior
remain valid business inputs for the Source Need.

### 4. Convert the asset into ticker capability

After the final Live Probe and certification pass, keep the certified files unchanged, promote that
version, and verify it is ACTIVE. Register the corresponding Message Bus SourceDefinition and keep
its parameter schema semantically aligned with the crawler version. Apply the Plan's desired ticker
binding with complete valid source parameters and intended polling/streaming settings. Reread the
source, ticker configuration, and status to confirm the actual state.

A successful mutation proves only the control-plane change it reports. If the ticker is not running,
an active window has not opened, a deployment flag is off, or the scheduler has not yet produced its
first poll, record that current operating constraint without claiming a production poll occurred.
When the release, registration, binding, parameters, and gates are correctly delivered, such a
constraint does not erase the completed capability.

## Progress, Stagnation and Alternatives

An implementation cycle turns current evidence into a specific failure hypothesis and a concrete
solution hypothesis, then tests it against the real delivery chain. Judge the next step by what the
cycle learned and changed:

- **Material progress** occurs when the work reaches a later delivery stage, removes or isolates a
  blocking failure, narrows the root cause with new evidence, produces materially better
  observations, or validates a materially different implementation path.
- **STALLED** means the same failure remains at essentially the same layer, no meaningful new
  evidence narrows its cause, and no materially different implementation hypothesis is validated.

A failed execution can still make material progress. Continue the current approved candidate while
progress is being made or a credible materially different implementation hypothesis remains. Reset
the consecutive-stagnation count whenever material progress occurs. A candidate may be abandoned for
engineering stagnation only after four consecutive STALLED implementation cycles.

When declaring `PROGRESSING`, record a new execution/probe/certification/artifact/cassette reference,
or a later independently verifiable delivery stage. Unsupported prose does not reset the stagnation
counter. `STALLED` increments only when the stage and blocker layer have not moved and the new run
does not narrow the root cause. `INFEASIBLE` requires decisive evidence and may immediately exhaust
the current candidate.

`INFEASIBLE` is different from stagnation and can move immediately to the next approved candidate
when evidence establishes that the target content does not exist on the source, the required public
surface no longer exists, an unavailable external access prerequisite is structurally necessary, or
the source reality otherwise makes faithful delivery impossible.

```text
material progress or a credible different hypothesis
-> continue the current candidate

four consecutive STALLED cycles, or proven INFEASIBLE
-> preserve evidence and try the next approved alternative

all approved candidates exhausted
-> settle the Source Need as undelivered or requiring a new Plan, as the evidence warrants
```

Codex request attempts, wall-clock duration, and orchestrator retries are execution mechanics. They
do not consume business effort or justify declaring a Source Need failed. Once an item reaches a
supported outcome, preserve its state and continue the remaining Plan items in Plan order; one
unresolved item does not block independent approved deliveries.

## Outcomes

Use the output contract's terminal status that describes what actually exists:

- `COMPLETED`: Live viability is proven; certification has no failed check (`failure_replay` may be
  `NOT_APPLICABLE` only when `regression_count=0`); the exact certified
  version is ACTIVE; an aligned SourceDefinition is registered; the ticker binding and its parameters
  are explicitly present; and the resulting state has been reread. Record any separate current
  operating constraint in `constraints`.
- `FAILED`: the Source Need remains valid, but every approved candidate has either been proven
  infeasible or reached four consecutive STALLED cycles without renewed material progress, and no
  reliable delivery exists. Preserve the highest durable stage and the evidence that explains the
  result.
- `REPLAN_REQUIRED`: crawler difficulty is not the core problem; a source, candidate equivalence,
  target-content, or global identity assumption in the immutable Plan no longer matches reality and
  leaves no faithful approved delivery path, so a new CONFIGURE decision is needed.
- `HUMAN_INTERVENTION_REQUIRED`: delivery depends on a specific external prerequisite that the Agent
  cannot create with its current coding, workspace, network, Crawler Plane, or Message Bus authority,
  such as account registration, purchase, MFA, or administrator-granted credentials. State that
  concrete action in `human_request`.

A 403, anti-bot behavior, selector or browser failure, timeout, code defect, or certification failure
is engineering evidence. Use it to form the next implementation hypothesis or determine whether the
candidate has become STALLED or INFEASIBLE; difficulty alone does not make it a human prerequisite.
`PENDING` and `IN_PROGRESS` describe progressive
checkpoints, not the final disposition of a work item returned by this node.

## Settlement

The Configuration Plan records what should exist. The Delivery Settlement records what actually
exists now; it is a Plan execution receipt, not a coding diary. Return exactly one settlement item
for every—and only—Plan item whose resolution is `NEW_CRAWLER_REQUIRED`, preserving the Plan's
`plan_id`, `plan_version`, ticker, and Source Need identities.

For each item, use only the fields in `output_schema.json` to identify the final candidate, crawler
version, certification run, registered source, binding, terminal status, current constraints, and any
required human action. Evidence should make the highest durable stage and final outcome independently
understandable—for example, the relevant execution/probe, certification, ACTIVE release, source, and
binding verification or the decisive failure evidence—without becoming a chronological development
log. The summary should state aggregate delivery reality and degradation plainly.

Preserve completed items and partial durable stages in the workspace checkpoint before returning the
strict `DeliverySettlement` JSON. The settlement closes this delivery attempt while the immutable
Plan remains the authority for intended monitoring coverage.
