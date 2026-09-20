# Jev Flight Lab

A local drone experiment built on [Drone Commander](https://github.com/vroby65/DroneCommander). It reuses the existing 3D world, drone, camera, movement API, Blockly editor and collision handling. Our code adds Jev/LLM decisions, a control panel and a separate hardware adapter boundary.

## Run

Requires Node.js 22.12+ and Git. No npm dependencies are needed for the simulator.

```bash
cd /Users/vinothshanmugam/code/jev-playground/drone-lab
npm run setup
npm run dev
```

Open **http://127.0.0.1:4180**. The existing Route Judge app remains on port 4173.

`npm run setup` fetches the exact source revision in `upstream.json`, including its locally served libraries and assets. It refuses to overwrite a different or modified checkout. The checked-out simulator stays unchanged; the server injects our bridge when serving its page.

The start script sources `../scripts/load-jev-env.sh`, which parses the parent `.env` and maps `JEV_API_KEY` to `TYPESAFE_API_KEY`. It also accepts the existing `openouterkey` name for OpenRouter. Secrets remain on the server. Optional project settings are listed in `.env.example`; never commit `.env`. API calls use your configured accounts and may incur usage charges. Offline mode needs no model access.

## Try the demo

1. Leave **Jev** selected and run `take off`.
2. Run `move forward`, then `turn left a little`. Watch the position, heading and camera change. Example buttons fill the text box; **Run command** executes it.
3. Select **LLM** and try the same phrases. Both providers produce the same bounded command contract; latency is shown for each request. This is a functional demo, not a benchmark.
4. Click **Analyze camera frame**. A vision model describes a synthetic camera frame; Jev classifies that description. The observation never steers the drone.
5. Toggle **Simulate link loss** or click **Test collision recovery** while airborne. Restore the link before sending another command. **Hold position / Escape** cancels movement and invalidates pending decisions; **Reset** restores the starting state.
6. Try `follow me` or a multi-step instruction: this first version rejects unsupported requests. Click **Export** to save decisions, probabilities and events as JSON.

The microphone button uses your browser's speech recognition service to fill the command box. Click Run after reviewing the transcription. Availability and microphone permission depend on the browser; typed commands always work. Audio dictation has not been verified on this machine.

Four upstream scenes and the original Blockly editor are available. Blockly is an independent manual programming surface: it can exceed our model command limits. Keep it hidden when testing the Jev boundary.

## What this simulator can establish

It exercises command interpretation, discrete movements, camera observations, state changes, cancellation and basic upstream collision recovery. Its motion is kinematic; distances are **simulation units**, not calibrated PL-515 meters. It does not model motor dynamics, battery drain, radio timing, wind, real position hold or the PL-515's exact firmware. Scene terrain is procedurally varied, so reset is not a deterministic physics replay.

There is no autonomous follow-me loop or person tracker yet. The current camera pipeline classifies a vision model's description; it does not claim Jev reads raw pixels or reliably measures obstacles. Confidence thresholds are uncalibrated demo settings, not probabilities of safe flight.

## Hardware preparation

[TurboDrone](https://github.com/marshallrichards/turbodrone) lists the PLEGBLE PL-515 as tested with its `s2x` driver. Its exact source is pinned in `hardware-upstream.json`. Prepare the Python 3.12 environment with [uv](https://docs.astral.sh/uv/):

```bash
npm run setup:drone
```

This installs core dependencies from `requirements-hardware.txt` and verifies imports. Optional YOLO/Ultralytics plugins are excluded. It does not start a controller, connect Wi-Fi, or send packets to a drone. The Flight Lab hardware adapter only checks a local bridge's capabilities. Physical flight endpoints are disabled in code.

Read [the hardware handoff](docs/hardware-handoff.md) when the unit arrives. In particular, upstream documents that its Land control may immediately stop motors; simulator landing must not be mapped directly to that action.

## Develop and verify

```bash
npm run check
```

Tests cover invalid model answers, negative/unsupported requests, state freshness, cancellation, altitude limits, link loss, actual bridge dispatch against a simulated upstream API, HTTP isolation and credential loading. They make no paid API calls and never contact hardware. Browser verification additionally exercises the real upstream renderer and movement methods.

| Location | Purpose |
| --- | --- |
| `server/decision-policy.mjs` | Jev command questions, thresholds and offline phrase fixture |
| `server/models.mjs` | Server-side Jev, OpenRouter and camera observation calls |
| `shared/contract.mjs` | Allowed actions, bounded steps and deterministic checks |
| `public/sim-bridge.mjs` | Adapter to the pinned upstream simulator |
| `public/app.mjs` | UI, dictation, decision cancellation and flight log |
| `server/turbodrone.mjs` | Read-only local hardware boundary |
| `.vendor/` | Ignored upstream checkouts; reinstall from manifests |

See [simulator research](docs/simulator-selection.md), [architecture](docs/architecture.md), [verification notes](docs/verification.md), and [third-party notices](THIRD_PARTY.md).
