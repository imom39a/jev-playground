# Verification — 2026-09-20

Environment: macOS Apple Silicon, Node.js 25.9, isolated Python 3.12 environment, Codex in-app browser. This is a record of functional checks, not a flight certification or model benchmark.

## Automated and setup checks

`npm run check`: **20 tests passed**, including JavaScript syntax, bounded dispatch, malformed answers, freshness, hold cancellation, link loss, scene-load blocking, local HTTP behavior, disabled physical endpoints and dotenv loading without shell execution.

`npm run setup`: exact Drone Commander revision verified; checkout pristine.

`npm run setup:drone`: exact TurboDrone revision verified; checkout pristine; 22 locked Python packages installed and checked. Core protocol modules and the web server import successfully. The web-server lifespan, controller, video service, webcam and drone sockets were not started.

## Browser and live model checks

| Check | Observed result |
| --- | --- |
| Jev `take off` | `takeoff` executed; simulator altitude rose from 1.7 to 10 units. One request took 404 ms. |
| Jev `move forward` | `forward` executed; X changed from 0 to −5 at heading 0. One request took 364 ms. |
| Jev `follow me` | Live API returned `no_match` with clarification required. No movement dispatched. |
| LLM `turn left a little` | A small turn executed; heading changed from 0 to 15 degrees. One request took 962 ms. |
| Camera → vision → Jev | Synthetic frame displayed; description classified as open terrain, with 2% reported person probability. Observation did not alter flight state. |
| Injected link loss | Movement request rejected; position remained −5 / 0. |
| Hold during LLM inference | Pending result was cancelled; position and heading remained unchanged. |
| Injected collision | Upstream emergency recovery ran and reached the ground at 1.7 units. |
| Scene selection | Flight field and urban track rendered with their own assets; terrain and camera preview displayed correctly. |
| Blockly | Existing editor and toolbar open and close through the lab panel. |
| Hardware status | Reports bridge offline and flight disabled. Simulator remains available. |

The first live movement check was unnecessarily blocked because the clarification rubric treated an omitted distance as ambiguous. The rubric now explicitly names the default step behavior; subsequent movement checks passed. Thresholds were not loosened to force acceptance.

Not yet verified: spoken audio transcription, exact PL-515 firmware compatibility, camera reception from the physical drone, RC control, physical landing/disconnect behavior, autonomous tracking, and real flight dynamics. The failure button tests the upstream recovery path by injection; it is not a complete obstacle-collision coverage test.
