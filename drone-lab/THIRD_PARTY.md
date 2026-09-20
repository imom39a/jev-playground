# Third-party software

- **Drone Commander**, by the upstream contributors: [repository](https://github.com/vroby65/DroneCommander), MIT license. See `.vendor/drone-commander/LICENSE` and its bundled library notices. Revision is recorded in `upstream.json`.
- **TurboDrone**, by the upstream contributors: [repository](https://github.com/marshallrichards/turbodrone), Apache License 2.0. See `.vendor/turbodrone/LICENSE` and `.vendor/turbodrone/NOTICE`. Revision is recorded in `hardware-upstream.json`.
- TurboDrone's Python dependencies retain their respective licenses; resolved versions are recorded in `requirements-hardware.txt`.

Setup downloads these projects from their official repositories and preserves their contents and notices. They are not committed as original Flight Lab code. Retain their license files and any bundled component notices when distributing a copy. Runtime integration code lives outside the upstream checkouts.
