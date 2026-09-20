# Standalone Tower of Hanoi

This folder contains two local comparison views served by the standard-library
Python server on port `5491`:

- `/` compares the selected OpenRouter model with JEV.
- `/v2/` compares the selected OpenRouter model with the same model plus a JEV
  verification score and at most one repair turn. A proposal mutates the board
  only when its score reaches the configured threshold. If both proposals are
  rejected, the event records a veto with no action; rejected labels are not
  offered again until the board state changes.

```bash
npm run hanoi
# http://127.0.0.1:5491/
# http://127.0.0.1:5491/v2/
```

The session accepts one to ten disks, a default all-A tower or a deterministic
seeded reachable mid-state, legal single-disk moves, bounded recent history,
cycle hints, and a configurable time limit. Each lane has exactly one
participant, plus pause, resume, end, a bounded event feed, decision traces,
and an SSE projection stream. Solver-count API fields are rejected unless they
are `1`.

Each provider activation includes the authoritative board, recent moves,
bounded prior decisions, and per-choice facts for successor-board visits,
recent repetition, and immediate reversal. These are observations rather than
a built-in solution. The participant still chooses every move and decides when
to claim completion.

Completion is participant-owned. A participant posts `post_completion_claim`,
which records the current work revision and opens a review round. Other
participants can `endorse`, `challenge`, or `defer`; the claimant counts as one
approval and the strict majority is fixed when the claim opens. A single
participant therefore self-completes. A legal move increments the work
revision and clears the claim. Reaching the target board alone never changes
the outcome.

Provider access is server-side. The loader accepts `OPENROUTER_API_KEY`,
`OPENROUTER_KEY`, or the existing `openouterkey` alias, and `JEV_API_KEY` or
`TYPESAFE_API_KEY`. No key is sent to the browser. The server sends no custom
title header to providers. Provider failures are shown in the decision trace
and stop that lane without submitting a local fallback move.

Run focused tests with:

```bash
npm run hanoi:test
```
