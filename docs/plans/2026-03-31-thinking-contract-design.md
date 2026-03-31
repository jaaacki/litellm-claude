# Thinking Contract Design

## Goal

Make `proclaude.sh` thinking levels trustworthy by turning them into a verified provider contract with strict failure semantics. If the selected model cannot be proven to honor thinking control, launch must fail and the proxy must reject thinking-bearing requests for that model.

## Current Problem

The current launcher prompts off a provider-level boolean, `supports_thinking`, but the proxy only converts `x-thinking-effort` into `reasoning_effort` on one route family: Anthropic-to-OpenAI translation for configured `openai/...` models. That leaves `chatgpt/...` OpenAI models and future route families in an ambiguous state where the UI implies control but the request path may silently ignore it.

## Chosen Approach

Use a provider contract instead of a boolean.

Each provider will expose a resolver that inspects the configured model entry and returns one of:

- a verified thinking contract, including the exact upstream mapping strategy
- no contract, meaning thinking is unsupported or unverified

Models inherit support automatically when their configured backend route matches a verified contract. Unknown or unmapped route families are treated as unsupported.

## Contract Shape

The resolved contract should be based on the configured launch model, not only the provider name. It needs enough information for both CLI and proxy enforcement:

- provider name
- route family, derived from configured backend model string and parameters
- mapping strategy, for example `openai_chat_reasoning_effort`
- allowed user values: `low`, `medium`, `high`

## Enforcement

### Launch

`launch claude` will resolve the selected model's thinking contract before prompting:

- if the model has a verified contract, prompt for thinking and allow `--thinking`
- if `--thinking` is passed for a model without a verified contract, hard fail
- if the model lacks a verified contract, do not show the thinking prompt

### Proxy

The proxy will load the same resolved contract per configured model at startup:

- if a request includes `x-thinking-effort` and the model has a verified contract, inject the exact upstream field required by that contract
- if a request includes `x-thinking-effort` and the model has no verified contract, reject the request instead of degrading silently

This keeps the launcher and runtime behavior aligned.

## Route Families Covered Now

- `openai/...` OpenAI-compatible chat routes that honor `reasoning_effort`
- `chatgpt/...` OpenAI ChatGPT routes that also honor `reasoning_effort`

These route families cover the current OpenAI, MiniMax, and Z.AI configuration model styles. Future models inherit support only if their provider resolver maps them onto one of these verified route families or a newly added verified strategy.

## Testing

Tests must prove the trust boundary:

- contract resolution for current route families
- launcher hard failure for unsupported or unmapped models when `--thinking` is used
- proxy request translation injects `reasoning_effort` for both `openai/...` and `chatgpt/...`
- proxy rejects `x-thinking-effort` for models without a verified contract

