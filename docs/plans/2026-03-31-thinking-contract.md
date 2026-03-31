# Thinking Contract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace provider-level thinking booleans with a verified per-model contract so thinking controls are either enforced upstream or rejected.

**Architecture:** Add a thinking-contract resolver to the provider/config layer, consume it in both CLI launch selection and proxy routing, and back it with targeted unit tests. The proxy remains the single runtime enforcement point, while the launcher prevents unsafe sessions from starting.

**Tech Stack:** Python, unittest, LiteLLM proxy translation layer

---

### Task 1: Add failing tests for contract resolution and launch hard-fail

**Files:**
- Modify: `gateway/test_cli.py`

**Step 1: Write the failing tests**

Add tests that cover:

- a configured `chatgpt/...` model resolving to a verified contract
- a configured `openai/...` model resolving to a verified contract
- `--thinking high` hard-failing when the selected model has no verified contract

**Step 2: Run test to verify it fails**

Run: `python -m unittest gateway.test_cli`

Expected: FAIL because no contract resolver exists and launcher still keys off `supports_thinking`.

### Task 2: Add failing proxy tests for enforced request mapping

**Files:**
- Create: `gateway/test_proxy.py`

**Step 1: Write the failing tests**

Add tests that cover:

- Anthropic request for configured `chatgpt/...` model injects `reasoning_effort`
- Anthropic request for configured `openai/...` model injects `reasoning_effort`
- request with `x-thinking-effort` for unverified model is rejected

**Step 2: Run test to verify it fails**

Run: `python -m unittest gateway.test_proxy`

Expected: FAIL because proxy routing has no strict thinking contract enforcement.

### Task 3: Implement provider and config contract resolution

**Files:**
- Modify: `gateway/providers/base.py`
- Modify: `gateway/providers/openai.py`
- Modify: `gateway/providers/minimax.py`
- Modify: `gateway/providers/zhipu.py`
- Modify: `gateway/providers/ollama.py`
- Modify: `gateway/config.py`

**Step 1: Write minimal implementation**

Add a provider-facing resolver that inspects configured model data and returns a verified contract or `None`. Use route-family-based matching so future models inherit support automatically when they use a verified upstream route family.

**Step 2: Run tests**

Run: `python -m unittest gateway.test_cli`

Expected: contract and hard-fail tests pass.

### Task 4: Implement CLI and proxy enforcement

**Files:**
- Modify: `gateway/cli.py`
- Modify: `gateway/proxy.py`

**Step 1: Write minimal implementation**

Update the launcher to resolve contracts from configured model entries and hard-fail unsupported `--thinking`. Update the proxy to cache contract data per model, inject upstream `reasoning_effort` for verified contracts, and reject thinking-bearing requests for unverified models.

**Step 2: Run tests**

Run: `python -m unittest gateway.test_cli gateway.test_proxy`

Expected: all tests pass.

### Task 5: Update docs

**Files:**
- Modify: `README.md`

**Step 1: Document behavior**

Describe that thinking controls are only offered and accepted for models with a verified upstream thinking contract, and that unsupported models hard-fail instead of degrading silently.

**Step 2: Run full verification**

Run: `python -m unittest gateway.test_cli gateway.test_proxy`

Expected: PASS
