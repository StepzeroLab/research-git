# Pending Capsule Review Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure agents list every pending capsule candidate in the user's language whenever a final response still requires review.

**Architecture:** Add matching behavioral contracts to the generated global AGENTS guidance and the packaged `rgit-capture` skill. Keep translation in the presentation layer and preserve proposal ids, capsule names, code symbols, configuration keys, file paths, and stored records.

**Tech Stack:** Python string rendering, Markdown skill instructions, pytest, isolated Codex behavior validation.

**Spec:** `docs/superpowers/specs/2026-07-19-pending-capsule-review-summary-design.md`

## Global Constraints

- No new CLI command, dependency, or storage schema.
- List every candidate as stored name plus one-line intent; include key knobs only when they affect the choice.
- A candidate count alone is not an acceptable final review summary.
- Translate explanatory prose and intent into the user's current language, but preserve stable identifiers and stored data.

---

### Task 1: Define the host-agent behavior check

- [ ] Create an isolated Git repository with a real `.rgit/` store and one
      open proposal containing multiple English-language candidates.
- [ ] Load the branch versions of the global guidance and `rgit-capture`
      skill, then ask an isolated Codex session to finish in another language
      without approving, dismissing, or rewriting the proposal.
- [ ] Verify the final response includes every candidate and key choice-relevant
      knob, preserves stable identifiers, requests the user's decision, and
      leaves the stored proposal unchanged.

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

- [ ] **Step 3: Run the existing focused tests**

Run: `python -m pytest tests/test_agent_guidance.py tests/test_installer.py tests/test_guidance_coupling.py -q`

Expected: existing guidance coupling and packaging tests pass without
hard-coding the new prose in test assertions.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`

Expected: all tests pass with no new warnings or failures.
