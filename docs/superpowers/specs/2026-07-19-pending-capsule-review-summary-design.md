# Pending Capsule Review Summary

**Date:** 2026-07-19
**Status:** approved

## Problem

When a run ends with open research-git proposals, an agent can report only the
number of capsule candidates awaiting review. The user cannot make the required
human decision because the final response omits the candidates' concrete
contents.

The capture skill already asks the agent to show each candidate during the
normal review step, but the global guidance has no equivalent requirement for
the final response. There is also no fallback in the skill for a turn that ends
before the review decision is complete.

## Design

Use the same compact review contract in both instruction layers:

- The global AGENTS guidance requires final feedback to report open proposals.
  For every open proposal, list its stable proposal id and every candidate's
  stored name plus a one-line explanation of its intent. Include key knobs only
  when they affect the user's choice. A count alone is not sufficient.
- The `rgit-capture` skill keeps its normal interactive review flow and adds a
  final-response fallback. If any proposal remains open when the agent is about
  to finish, the agent presents the same candidate list and asks which names to
  keep.
- User-facing explanations follow the language the user is currently using,
  regardless of the language stored in the capsule. Proposal ids, capsule names,
  code symbols, configuration keys, and file paths remain unchanged.
- Translation is presentation-only. It never changes candidates stored under
  `.rgit`.

## Output Shape

```text
Pending capsule review

Proposal prop_abc:
- reranking-retrieval: Add a reranking stage before final retrieval.
- cache-fallback: Fall back to uncached retrieval when cache lookup fails.
  Key knob: fallback_timeout

Tell me which capsule names to keep. Nothing is approved until you decide.
```

For a Chinese-speaking user, the intent and surrounding prose are presented in
Chinese while `prop_abc`, `reranking-retrieval`, `cache-fallback`, and
`fallback_timeout` remain unchanged.

## Testing

- Assert that the rendered global guidance requires pending proposal details,
  rejects count-only summaries, and specifies user-language presentation with
  stable identifiers.
- Assert that the packaged capture skill contains the same final-response
  fallback and language rule.
- Keep guidance coupling and installer packaging tests passing.

## Out of Scope

- New CLI commands or output formats.
- Changes to proposal or capsule storage schemas.
- Translating or rewriting stored capsule records.
- Truncating candidate lists. The final response lists every candidate.
