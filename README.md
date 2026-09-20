# JEV Playground

This repository contains small local experiments that compare a conventional
LLM with TypeSafe Jev over the same prepared state. The original route decision
demo remains available alongside the Minesweeper and drone playgrounds.

## Route decision demo

The route demo is a local side-by-side comparison. A conventional LLM and
TypeSafe Jev receive the same route, place, weather, and user-intent state.
Each lane is timed until it produces a schema-valid decision.

The page is intentionally plain: one request, a compact view of the shared evidence, and two result columns. Route and place data is prepared before either timer starts.

## Playground projects

[`drone-lab/`](drone-lab/README.md) is the drone simulation project. It reuses
Drone Commander's 3D world and camera, adds Jev/LLM commands and observation
classification, and prepares a pinned TurboDrone driver for later PL-515 work.
Run `npm run dev` inside that folder to open it on port 4180. Physical flight
is disabled in this first version.

The repository also contains a standalone Minesweeper experiment in
[`minesweeper/`](minesweeper/). It has an LLM-vs-JEV comparison at `/` and a
JEV-only large-board run at `/v2/` when started with `npm run minesweeper`.
Its Python tests mock provider calls and its `.env.example` contains names only.
See [`minesweeper/README.md`](minesweeper/README.md) for the game contract,
board limits, and the session refactor roadmap.

## Run the demo

Node.js 20 or newer is the only runtime dependency; there are no npm packages to install.

```bash
npm start
```

Then open [http://127.0.0.1:4173](http://127.0.0.1:4173).

The current `.env` supplies both the Jev credential and an OpenRouter credential. The server accepts the folder's existing `openouterkey` name without exposing it to the browser. The standard names are:

```dotenv
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=ibm-granite/granite-4.2-8b
```

Keep `.env` private. Jev and OpenRouter credentials remain server-side.

## What makes the comparison fair

- Both lanes receive the same canonical JSON state, confirmed by a SHA-256 fingerprint returned with each result.
- Both must choose one route and one stop, score two dimensions for all three routes, flag all four stops, and decide whether clarification is needed.
- The conventional lane prompts the selected OpenRouter model for the shared JSON contract, then parses and validates the complete decision against the same local schema before stopping its timer.
- The model selector shows every text model in OpenRouter's public catalog. A model can still fail at request time when the API key's account guardrails or provider settings block it.
- There is no Ollama or other provider fallback.
- Jev asks 13 independent `choice`, `score`, and `noul` questions in one System One request. They are evaluated in parallel.
- The server timer includes the model request, output parsing, and local schema validation. It excludes page rendering.
- One run is a demo, not a benchmark. Repeat it under similar conditions before drawing conclusions.

The seeded San Francisco scenario is deterministic on purpose. It keeps the evidence fixed while the decision engines vary. Later, the same contract can be fed by Google Routes and Places APIs; load and freeze that state before starting the race.

## What is configured

- The repository-scoped Codex skill lives at `.agents/skills/typesafe-ai/SKILL.md`.
- `AGENTS.md` tells Codex when to use it and to consult the live docs.
- Your existing `.env` remains private. The loader maps its `JEV_API_KEY` to the official `TYPESAFE_API_KEY` variable without copying the secret.
- `.gitignore` prevents local environment files from being committed.
- `server.mjs` keeps credentials off the page and exposes the two benchmark lanes.
- `public/` contains a dependency-free HTML/CSS/JavaScript interface.

Codex detects repository skill changes automatically. If it does not appear, restart Codex. Invoke it as `$typesafe-ai` or ask Codex to “use the TypeSafe skill.”

## Verify the connection

The smoke test makes one small request containing two parallel questions:

```bash
./scripts/jev-smoke-test.sh
```

To expose the official environment variable in your current shell for SDK use:

```bash
source scripts/load-jev-env.sh
```

To validate local JavaScript syntax:

```bash
npm run check
```

## How JEV works

Send a `state` plus one or more typed `questions` to `POST https://api.typesafe.ai/v1/systemone` using model `jev-latest`. Answers return under the same question IDs.

- `choice`: select one member of a closed set; returns the selection, all option probabilities, and confidence.
- `score`: place the state on ordered, described levels; returns a probability-weighted score, the level distribution, and confidence.
- `noul`: estimate whether a condition is true; returns a probability from 0 to 1 and has no separate confidence field.

Independent questions over the same state should normally be sent together. Keep exact rules, calculations, thresholds, and side effects in code; use JEV for narrow semantic judgments. Treat probabilities as evidence to evaluate on representative data, not as guaranteed truth.

## SDK quick starts

Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install typesafe-sdk
source scripts/load-jev-env.sh
```

```python
from typesafe_sdk import Noul, TypeSafeClient

with TypeSafeClient() as client:
    result = client.system_one(
        state={"message": "The checkout is broken."},
        questions={
            "is_incident": Noul(
                instructions="Does `message` describe a product incident?"
            )
        },
    )

print(result.answers["is_incident"].noul)
```

Node.js 20+:

```bash
npm install @typesafe-ai/sdk
source scripts/load-jev-env.sh
```

```ts
import { noul, TypeSafeClient } from "@typesafe-ai/sdk";

const client = new TypeSafeClient();
const result = await client.systemOne({
  state: { message: "The checkout is broken." },
  questions: {
    isIncident: noul("Does `message` describe a product incident?"),
  },
});

console.log(result.answers.isIncident.noul);
```

## Current documentation

- [Quick start](https://docs.typesafe.ai/introduction/quickstart)
- [Question primitives](https://docs.typesafe.ai/primitives)
- [HTTP API](https://docs.typesafe.ai/api)
- [Python SDK](https://docs.typesafe.ai/sdk/python)
- [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript)
- [Patterns and cookbooks index](https://docs.typesafe.ai/llms.txt)

Keep credentials server-side in web applications. Review the live docs before relying on model names, request fields, SDK behavior, limits, or pricing.
