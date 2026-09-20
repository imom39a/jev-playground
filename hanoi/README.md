# Standalone Tower of Hanoi

This folder contains two local comparison views served by the standard-library
Python server on port `5491`:

- `/` compares the selected OpenRouter model with JEV.
- `/v2/` compares the selected OpenRouter model with the same model plus a JEV
  score and at most one repair turn.

```bash
npm run hanoi
# http://127.0.0.1:5491/
# http://127.0.0.1:5491/v2/
```

The session accepts one to ten disks, a default all-A tower or a deterministic
seeded reachable mid-state, legal single-disk moves, bounded recent history,
cycle hints, and configurable participant counts and time limits. Each lane
has pause, resume, end, a bounded event feed, decision traces, and an SSE
projection stream.

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
title header to providers. If keys are absent, bounded local legal choices keep
the page inspectable and mark those traces with the provider error.

Run focused tests with:

```bash
npm run hanoi:test
```
