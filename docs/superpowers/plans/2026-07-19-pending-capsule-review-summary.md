# Pending Capsule Review Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure agents list every pending capsule candidate in the user's language whenever a final response still requires review.

**Architecture:** Add matching behavioral contracts to the generated global AGENTS guidance and the packaged `rgit-capture` skill. Keep translation in the presentation layer and preserve proposal ids, capsule names, code symbols, configuration keys, file paths, and stored records.

**Tech Stack:** Python string rendering, Markdown skill instructions, pytest.

**Spec:** `docs/superpowers/specs/2026-07-19-pending-capsule-review-summary-design.md`

## Global Constraints

- No new CLI command, dependency, or storage schema.
- List every candidate as stored name plus one-line intent; include key knobs only when they affect the choice.
- A candidate count alone is not an acceptable final review summary.
- Translate explanatory prose and intent into the user's current language, but preserve stable identifiers and stored data.

---

### Task 1: Lock the final-feedback contract with failing tests

**Files:**
- Modify: `tests/test_agent_guidance.py`
- Modify: `tests/test_installer.py`

**Interfaces:**
- Consumes: `agent_guidance.render_global_block()` and the packaged `rgit-capture/SKILL.md`.
- Produces: regression tests for pending-detail and user-language requirements.

- [ ] **Step 1: Add a global-guidance contract test**

Add a test that checks the rendered block for instructions covering open
proposals, proposal ids, every candidate's name and one-line intent, conditional
key knobs, the prohibition on count-only summaries, user-language presentation,
and preservation of stable identifiers.

- [ ] **Step 2: Add a capture-skill contract test**

Extend the existing packaged-skill test to require a final-response fallback
with the same candidate-detail and language behavior.

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_agent_guidance.py tests/test_installer.py -q`

Expected: the new assertions fail because neither instruction layer contains
the pending final-response contract yet.

---

### Task 2: Add the matching instruction contracts

**Files:**
- Modify: `src/rgit/agent_guidance.py`
- Modify: `src/rgit/_plugin/skills/rgit-capture/SKILL.md`

**Interfaces:**
- Consumes: pending proposal data already available from `rgit pending --json`.
- Produces: agent instructions only; no runtime API changes.

- [ ] **Step 1: Update global final-feedback guidance**

Replace the current state-only sentence with a compact rule that lists pending
proposal details, follows the user's current language, preserves identifiers,
and forbids count-only summaries.

- [ ] **Step 2: Add the capture-skill final-response fallback**

After the normal review instructions, require the same presentation whenever a
proposal remains open at the end of a response.

- [ ] **Step 3: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_agent_guidance.py tests/test_installer.py tests/test_guidance_coupling.py -q`

Expected: all tests pass.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`

Expected: all tests pass with no new warnings or failures.
