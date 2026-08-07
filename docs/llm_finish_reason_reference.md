# berserker Finish Reason Reference

## Overview

This document explains how berserker handles `finish_reason`. It covers two layers:

1. The internal normalization contract that the agent executor relies on.
2. What each provider adapter actually returns, including which providers normalize their native values and which pass them through untouched.

The executor only understands the normalized values in `VALID_FINISH_REASONS`. Any raw provider value that reaches it is logged and treated as an end of turn.

## The internal contract

`berserker.provider.base` defines the standardized finish reasons that every provider maps to (or should map to). The constants live at the top of the module.

### Finish reason constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `FINISH_STOP` | `stop` | Normal completion. The model finished naturally. |
| `FINISH_LENGTH` | `length` | The output token limit was reached and the response is truncated. |
| `FINISH_TOOL_CALLS` | `tool_calls` | The model requested tool or function calls. |
| `FINISH_CONTENT_FILTER` | `content_filter` | Output was blocked by a safety or content filter. |
| `FINISH_ERROR` | `error` | An error occurred during generation. |
| `FINISH_REFUSAL` | `refusal` | The model refused to answer because of safety or policy. |

### VALID_FINISH_REASONS

`VALID_FINISH_REASONS` is a `frozenset` of exactly those six strings. The executor checks membership against it. Any value outside the set counts as unknown.

### ChatResponse

`ChatResponse` is the unified response dataclass every provider's `chat()` returns. Its fields:

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | Response identifier from the provider. |
| `model` | `str` | The model that generated the response. |
| `content` | `str` | The generated text. |
| `tool_calls` | `Optional[List[Dict[str, Any]]]` | Native tool calls in the unified OpenAI-style shape. `None` when the model did not call tools. |
| `usage` | `Dict[str, int]` | Token usage with `prompt_tokens`, `completion_tokens`, `total_tokens`. Defaults to all zeros. |
| `finish_reason` | `str` | Defaults to `"stop"`. Should be one of the six constants above. |
| `reasoning_content` | `Optional[str]` | Thinking-mode reasoning text when present (DeepSeek V4). Passed back on assistant messages in later tool-loop requests. |

## How the executor uses finish_reason

`AgentExecutor.execute()` in `berserker.agent.executor` runs the provider `chat()` inside a tool loop. Two things decide what happens next: whether the response carries `tool_calls`, and the `finish_reason` value.

Loop continuation is driven by `response.tool_calls`. If the response has no tool calls, the executor ends the turn immediately, before it even looks at `finish_reason`. Only responses that do carry tool calls reach the finish-reason decision table.

That decision table, in `executor.py`:

| finish_reason | Executor action |
|---------------|-----------------|
| `tool_calls` | Continue the tool loop. The model wants to call tools, and the tools were already executed above. |
| `stop` | End the turn. |
| `length` | Log a truncation warning, end the turn. |
| `content_filter` | Log a "blocked by content filter" warning, end the turn. |
| `refusal` | Log a "refused to generate" warning, end the turn. |
| `error` | Log an "error during generation" error, end the turn. |
| anything not in `VALID_FINISH_REASONS` | Log `Unknown finish_reason` with the expected set, end the turn. |

Two special cases set the finish reason outside the normal flow:

- Abort. If the abort event fires before a provider call or right after one returns, `execute()` returns immediately with `finish_reason` set to `"abort"`.
- Stuck loop. If the model returns empty text plus tool calls for 20 consecutive iterations, the executor forces a stop, sets `finish_reason` to `"error"`, and appends a stuck-loop notice to the content. It does this instead of reporting a silent success.

Because the decision table only runs for responses that already have `tool_calls`, providers that never populate `tool_calls` (Mistral, Bedrock Claude, Bedrock Titan) always end turns at the presence check, and their raw finish values never reach the unknown-reason warning. Providers that do populate `tool_calls` (OpenAI, OpenAI-compatible) surface unknown values there, such as OpenAI's legacy `function_call`.

If the loop exhausts `max_tool_iterations`, the executor appends a max-steps prompt and makes one final tool-less call to force a text summary before returning.

## Streaming and finish_reason

Streaming does not carry a finish reason. Every provider `stream()` method is typed `Iterator[str]` and yields text chunks only. OpenAI, Azure, Anthropic, Google, Mistral, Bedrock, and the OpenAI-compatible base all yield deltas and never expose the terminal finish reason to the caller.

So there is no "accumulate chunks until a non-null finish_reason" logic anywhere in berserker. The older claim that streaming accumulates until a final finish reason is not true of this codebase. Streaming is a one-way text pipe. Callers that need the finish reason must use the non-streaming `chat()` path.

## Missing or null finish_reason

Non-streaming responses always end up with a concrete value because each adapter supplies a default:

| Provider | Fallback when null |
|----------|--------------------|
| OpenAI / Azure | `choice.finish_reason or "stop"` |
| Anthropic | `"stop"` for any unmapped `stop_reason` |
| Google Gemini | `"stop"` for any unmatched finish reason |
| Mistral | `"stop"` when `choice.finish_reason` is `None` |
| Bedrock Claude | `response_body.get("stop_reason", "stop")` |
| Bedrock Titan | `result.get("completionReason", "stop")` |
| OpenAI-compatible | `choice.finish_reason if choice.finish_reason else "stop"` |

## Per-provider mapping

### OpenAI / Azure OpenAI: passed through

Both `OpenAIProvider` and the Azure providers take `choice.finish_reason` verbatim, falling back to `"stop"` when it is missing. No normalization happens. The value can be any of:

| Native value | Meaning | berserker status |
|--------------|---------|------------------|
| `stop` | Natural stop point or a provided stop sequence. | Valid, ends the turn. |
| `length` | The requested max tokens was reached. | Valid, ends the turn with a truncation warning. |
| `tool_calls` | The model requested one or more tools. | Valid, continues the tool loop. |
| `content_filter` | Output omitted due to the content filters. | Valid, ends the turn with a filter warning. |
| `function_call` | Legacy function-call finish (deprecated). | Not in `VALID_FINISH_REASONS`. With tool calls present it logs `Unknown finish_reason` and ends the turn. |

Notes:

- `openai.py` shares a `_map_response` helper that also extracts native `tool_calls`.
- The Azure adapter registered by the provider registry (`berserker.provider.azure`) passes `finish_reason` through the same way but does not populate `ChatResponse.tool_calls`, so even a `tool_calls` finish reason ends the turn there.

### Anthropic: normalized

The Anthropic adapter maps the SDK's `stop_reason` to the internal contract:

| Native `stop_reason` | berserker value |
|----------------------|-----------------|
| `end_turn` | `stop` |
| `max_tokens` | `length` |
| `tool_use` | `tool_calls` |
| `stop_sequence` | `stop` |
| `refusal` | `refusal` |
| `model_context_window_exceeded` | `length` |
| anything else, including `pause_turn` | `stop` |

Two honest caveats:

- `pause_turn` is not handled. It falls through the `else` branch and becomes `stop`, so a paused turn is treated as a normal completion.
- `model_context_window_exceeded` is mapped to `length`. That is a mismatch: the value is an input-context error, not an output-token-limit stop. When the same failure arrives in an HTTP error body instead of a stop reason, `_handle_error` detects the context-window text and raises `ContextLengthExceeded`, which the executor handles by compacting and retrying. The two paths therefore behave very differently.

### Google Gemini: normalized by substring

The Gemini adapter upper-cases the SDK finish reason and runs substring checks in a fixed order:

| Native finish reason | Match rule | berserker value |
|----------------------|------------|-----------------|
| `STOP` | contains `STOP` | `stop` |
| `MAX_TOKENS` and anything with `MAX` or `LENGTH` | contains `MAX` or `LENGTH` | `length` |
| `SAFETY`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MODEL_ARMOR` | contains any of `SAFETY`, `BLOCKLIST`, `PROHIBITED`, `SPII`, `ARMOR` | `content_filter` |
| `TOOL_CALLS`, `FUNCTION_CALL` | contains `TOOL` or `FUNCTION` | `tool_calls` |
| `RECITATION` | contains `RECITATION` | `content_filter` |
| `MALFORMED_FUNCTION_CALL` | would match `MALFORMED`, but see limitation below | unreachable |
| `FINISH_REASON_UNSPECIFIED`, `OTHER` | contains `UNSPECIFIED` or `OTHER` | `stop` |
| anything else | no rule matched | `stop` |

Known limitation: the `MALFORMED` rule is unreachable in practice. `MALFORMED_FUNCTION_CALL` contains `FUNCTION`, and the `TOOL`/`FUNCTION` check runs first, so a malformed function call maps to `tool_calls` (and then produces no usable tool call), never to `error`.

### Mistral: passed through

The Mistral adapter copies `choice.finish_reason` verbatim, defaulting to `"stop"` when null. Native values:

| Native value | Meaning | berserker status |
|--------------|---------|------------------|
| `stop` | Natural completion or stop sequence. | Valid. |
| `length` | Token limit reached. | Valid. |
| `model_length` | The model's maximum context length reached. | Not in `VALID_FINISH_REASONS`. |
| `error` | An error occurred during generation. | Valid. |
| `tool_calls` | The model called one or more tools. | Valid. |

The Mistral adapter does not extract native `tool_calls`, so a Mistral response always ends the turn at the executor's tool-calls presence check. In practice `model_length` never reaches the decision table. If a raw unknown value did reach it, which requires tool calls to be present, it would log `Unknown finish_reason` and end the turn.

### Amazon Bedrock: passed through, unnormalized

Both Bedrock paths take the native value verbatim and never map it to the internal contract.

Claude models use `stop_reason`:

| Native `stop_reason` | Meaning |
|----------------------|---------|
| `end_turn` | Natural completion. |
| `max_tokens` | Output token limit reached. |
| `tool_use` | The model requested a tool. |
| `stop_sequence` | A custom stop sequence was hit. |
| `pause_turn`, `refusal`, `model_context_window_exceeded`, others | Passed through raw. |

Titan models use `completionReason`:

| Native `completionReason` | Meaning |
|---------------------------|---------|
| `COMPLETE` | Generation finished normally. |
| `DONE` | Generation finished. |
| `ERROR` | An error occurred. |
| `CONTENT_FILTERED` | Output blocked by a content filter. |

None of these are in `VALID_FINISH_REASONS`. Note the casing: `ERROR` and `CONTENT_FILTERED`, not `error` and `content_filter`. The Bedrock adapter also does not populate `ChatResponse.tool_calls`, so every Bedrock turn ends at the executor's tool-calls presence check with the raw value preserved in the returned dict.

### OpenAI-compatible providers: passed through, with tool-markup recovery

Groq, xAI, OpenRouter, Together, Cerebras, DeepInfra, Perplexity, Venice, and GitLab all use `OpenAICompatibleProvider`. The registry also maps `deepseek`, `bailian`, and `bailian_coding` to the same class. All of them:

- Pass `choice.finish_reason` through unchanged, defaulting to `"stop"` when empty.
- Can force `tool_calls`. When the API returns tool-call markup as plain content instead of native `tool_calls` (DeepSeek DSML, Qwen XML, and similar leaks), the provider parses it back into `tool_calls` and sets `finish_reason = "tool_calls"` regardless of what the API reported.

This is separate from the executor-level text-mode tool-call guard, which watches for `DSML`, `<invoke ...>`, and similar markers in content and retries once with a corrective nudge before failing explicitly.

## Cross-provider quick reference

| Concept | OpenAI / Azure | Anthropic | Gemini | Mistral | Bedrock Claude | Bedrock Titan |
|---------|----------------|-----------|--------|---------|----------------|---------------|
| Natural completion | `stop` | `end_turn` to `stop` | `STOP` to `stop` | `stop` | `end_turn` (raw) | `COMPLETE`, `DONE` (raw) |
| Token limit | `length` | `max_tokens` to `length` | `MAX_TOKENS` to `length` | `length`, `model_length` | `max_tokens` (raw) | (none) |
| Tool calls | `tool_calls` | `tool_use` to `tool_calls` | `TOOL_CALLS`, `FUNCTION_CALL` to `tool_calls` | `tool_calls` | `tool_use` (raw) | (none) |
| Content filter | `content_filter` | (none) | `SAFETY`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MODEL_ARMOR`, `RECITATION` to `content_filter` | (none) | (none) | `CONTENT_FILTERED` (raw) |
| Refusal | (none) | `refusal` to `refusal` | (none) | (none) | `refusal` (raw) | (none) |
| Error | (none) | (none) | `MALFORMED` to `error` (unreachable) | `error` | (none) | `ERROR` (raw) |

## Errors are mostly exceptions, not finish reasons

Most providers signal failures by raising instead of setting `finish_reason`. `AuthenticationError`, `RateLimitError`, `APIError`, `ModelNotFoundError`, and `ContextLengthExceeded` bubble out of `chat()` and are handled by the retry decorator or the executor. Only Mistral (`error`) and Bedrock Titan (`ERROR`) carry error conditions inside the finish value itself.

`ContextLengthExceeded` is the one the executor actively reacts to. It compacts the message history and retries, up to two retries, before re-raising.

## Documentation sources

- OpenAI / Azure: the chat completions object reference, `choices.finish_reason`. https://platform.openai.com/docs/api-reference/chat/object#chat/object-choices-finish_reason
- Anthropic: the Messages API, `stop_reason`. https://docs.anthropic.com/en/api/messages
- Google Gemini: the `FinishReason` enum. https://ai.google.dev/api/rest/v1beta/FinishReason
- Mistral: the chat completions API, `finish_reason`. https://docs.mistral.ai/platform/api/

Where the older reference listed native values only, this document keeps those tables but labels each mapping as normalized or passed through, and calls out the discrepancies between the raw values and berserker's internal contract.
