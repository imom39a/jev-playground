# Open-source simulator research

Checked 2026-09-20. The immediate objective is a local, visible demo of language → Jev/LLM decision → simulated flight, with camera observations and a later PL-515 adapter.

| Project | What already exists | Fit for this project |
| --- | --- | --- |
| [Drone Commander](https://github.com/vroby65/DroneCommander) | Browser 3D scenes, a drone camera, movement commands, Blockly, photos/video and collision handling; MIT license | **Installed.** Closest existing interactive playground. A small project with simplified motion, so we pin its code and test our bridge. |
| [RotorPy](https://github.com/spencerfolk/rotorpy) | Python multirotor simulation with dynamics, controllers, sensors and camera support | Strong next step when we need flight dynamics and perception experiments. More setup and less ready-made command UI. |
| [gym-pybullet-drones](https://github.com/learnsyslab/gym-pybullet-drones) | PyBullet drone environments, control and reinforcement-learning examples; documented Apple Silicon setup | Good for controller/RL work. Its models and Python training stack exceed what the first demo needs. |
| [Webots Mavic 2 Pro example](https://cyberbotics.com/doc/guide/mavic-2-pro) | A full robotics simulator and an existing quadcopter example | Suitable later if a native robotics workbench is useful. It represents another drone, not our PL-515. |
| [PX4 + Gazebo](https://docs.px4.io/main/en/sim_gazebo_gz/) | Flight-stack software-in-the-loop simulation | Best when targeting a PX4 autopilot. The toy drone does not expose a PX4 interface. |

No reviewed option supplied a verified PL-515 digital twin. We chose Drone Commander for the workflow demo, not for predicting physical flight performance. No custom flight physics or renderer was written.

The real-device counterpart is [TurboDrone](https://github.com/marshallrichards/turbodrone), whose compatibility table identifies PL-515 support through `s2x`. This is community reverse engineering, not a vendor SDK. A unit/firmware check is still needed.

Installed revisions:

- Drone Commander: `7c21099f8e1ff9c9d6a9afceb0a7a7b156f8dab6`.
- TurboDrone: `2c09cf864e7c26d2f5e674a11d3d87cc308c3a83`.

The simulator checkout is pristine. Runtime compatibility fixes in our bridge refresh world matrices before height queries and fit the existing canvas to its host panel. Balanced graphics uses pixel ratio 1 to avoid a camera-target artifact in the bundled Three.js. These are adapter fixes, not a new physics implementation.
