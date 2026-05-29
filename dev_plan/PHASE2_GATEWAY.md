# Phase 2 Model Gateway

## Scope

Phase 2 implements DoxAgent's model access boundary. It does not implement
agents, workflows, tool calling, Blackboard Service persistence, or real API
smoke tests.

## Gateway Boundary

All future model calls should enter through `ModelGateway` and the async
`ModelClient` protocol. Provider SDK details stay inside provider adapters.

The standard response contains:

- `text` for normal model text;
- `structured` for parsed JSON or provider-native structured output;
- `raw` for provider response preservation;
- `usage` for token accounting hints;
- `audit` for provider/model/latency/retry/fallback metadata;
- `error` for normalized gateway errors.

Gateway audit summaries are observability aids only. They are not Blackboard
Commit Log records and must not be treated as business state.

## Provider Strategy

Phase 2 includes:

- `MockModelClient` for offline tests and future workflow fixtures;
- `OpenAIModelClient` for `AsyncOpenAI.responses.create`;
- `AnthropicModelClient` for `AsyncAnthropic.messages.create`;
- centralized LangSmith wrapping through `wrap_provider_client`.

Tests use fake SDK clients and do not perform network calls or read API keys.

## Fallback and Errors

The gateway retries only retryable normalized errors. Non-retryable errors
return immediately. Fallback clients are attempted only after retryable primary
failure.

When JSON output is requested, the gateway prefers existing `structured` data
and otherwise parses response text. Invalid JSON is a non-retryable gateway
error.
