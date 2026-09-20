# PL-515 handoff

The hardware has not been connected or tested in this project. [TurboDrone's compatibility table](https://github.com/marshallrichards/turbodrone#hardware) reports the PLEGBLE PL-515 as tested using `s2x`. Confirm the delivered model and companion app before using that assumption.

## Prepared locally

- The driver source is pinned by `hardware-upstream.json` and installed under `.vendor/turbodrone`.
- Python 3.12 core dependencies are locked in `requirements-hardware.txt`; `npm run setup:drone` creates `.venv` and checks imports without instantiating a controller.
- `server/turbodrone.mjs` checks `GET /capabilities` on a local bridge and reports its `/mjpeg` camera address. The dashboard currently displays only connection status; real video still needs to be wired into the observation pipeline.
- Physical execution always throws. There is no environment variable or UI toggle that enables it.

## First device session

1. Record the label/model, companion app, firmware information if available, and observed Wi-Fi SSID. Keep personal network information out of committed notes.
2. With the propellers removed, verify power and the manufacturer's normal app/video connection. This establishes whether the unit works before adding our software.
3. Review the pinned `s2x` configuration against that unit. Upstream defaults are drone address `172.16.10.1`, control UDP port `8080`, video UDP port `8888`. Treat them as driver defaults, not discovered device facts.
4. Connect the computer to the drone's Wi-Fi and validate camera reception. Internet inference needs a second working network path, such as Ethernet or a compatible second adapter; the drone Wi-Fi may have no internet. Offline command classification remains available.
5. Validate the RC axes and stop/land behavior on the bench before implementing any powered-flight adapter. Upstream explicitly says **Land may stop the motors immediately**. Never equate that with the simulator's smooth descent. Do not infer altitude/position telemetry from the camera or joystick values.
6. Implement a separate hardware adapter with a manual arm step, short bounded RC pulses, an independent watchdog, tested shutdown behavior and manual override. Keep inference outside the control-timing loop. Only then progress to a small contained flight test.

Starting the upstream web server is itself an active device operation: its lifespan starts the controller and video service. It is deliberately not part of the simulator start script. After the bench setup has been reviewed, the upstream entry point is `web_server:app`, with `DRONE_TYPE=s2x` and `PLUGINS_ENABLED=false`, served on loopback port 8000. Its separate frontend is unnecessary for today's simulator.

The upstream `debug` video mode opens the computer's webcam; it is not a synthetic simulator and was not started.

## Next demo increments

Start with camera-only classification and a visible observation log. Then add a manually armed single-command flight path. A follow-me demo comes later: target detection/tracking, lost-target behavior, bounded tracking commands and independent stop controls must be implemented and tested first.

A 3D printer can later make a desk cradle for camera experiments or a test fixture sized to the delivered drone. Do not assume extra printed payload is within its lift capability.
