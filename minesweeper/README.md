# Minesweeper playground

This is a self-contained Minesweeper experiment with two variants:

1. **LLM vs JEV** — an OpenRouter language model and a TypeSafe JEV player solve
   the same seeded board in separate lanes.
2. **JEV on large boards** — one JEV player works through a bounded frontier on
   boards up to 500×500 without sending the full grid to the model.

Both variants use the same deterministic game logic, constraint deductions,
closed candidate set, and sanitized event stream. Provider credentials stay in
the Python server and are never sent to the browser.

## Run locally

From the repository root, copy the example environment file and fill in the
keys you want to use:

```bash
cp .env.example .env
python3 -m minesweeper.server
```

From the repository root, `./minesweeper/scripts/start.sh` is an equivalent
entrypoint and forwards server flags.

Open [http://127.0.0.1:5391/](http://127.0.0.1:5391/) for the comparison or
[http://127.0.0.1:5391/v2/](http://127.0.0.1:5391/v2/) for JEV-only large-board
mode. The server also accepts `--host`, `--port`, `--html`, and `--v2-html`.

The root `package.json` provides the equivalent `npm run minesweeper` command.
No third-party Python package is required to run the game or its tests. The
provider calls use Python's standard-library HTTP client.

## Configuration

`.env.example` contains variable names only. Set at least `JEV_API_KEY` for
the JEV-only variant. Set `OPENROUTER_API_KEY` as well for the comparison
variant. `JEV_MODEL`, `OPENROUTER_MODEL`, and `PORT` are optional overrides.

The server also recognizes the legacy `openouterkey` environment name used by
the original demo. Keep the real `.env` file private; it is ignored by Git.

## How a turn works

The game computes exact deductions from revealed numbers. Proven mines are
excluded from the offered cells. If a safe cell is proven, only those cells are
offered; otherwise a bounded frontier is ordered with a code-computed risk
estimate. Each player receives the same board state and one closed choice per
turn. The server validates the selected cell before revealing it and records a
sanitized decision trace.

The first reveal is always safe. A mine ends that lane, while revealing every
non-mine cell wins it. For large boards the state omits the full grid and sends
only a compact board summary, frontier candidates, and local constraints.

The current provider prompts prefer a provably safe cell and then the lowest
computed risk when guessing. That keeps the demo stable, but it means the
comparison measures provider decisions under this shared policy; relaxing that
instruction is a separate experiment.

## Tests and checks

```bash
python3 -m unittest discover -s minesweeper -p 'test_*.py'
python3 -m compileall -q minesweeper
npm run check
```

The tests mock provider calls, so they do not spend API credits.

## Roadmap

The extracted runner preserves the current two-lane lifecycle so its behavior
remains comparable with the source demo. A later refactor will expose a direct
game session API for local and large board runs. That work is intentionally not
part of this extraction.

## Attribution

The initial Minesweeper implementation was extracted from an internal prototype.
It uses the TypeSafe System One HTTP API for JEV
decisions and the OpenRouter chat completions API for the conventional LLM
lane. See [ATTRIBUTION.md](../ATTRIBUTION.md) for project links and license
notes.
