# Attribution and licenses

The Minesweeper playground was extracted from an internal Minesweeper
prototype. Its game rules, bounded candidate state,
provider adapters, server lifecycle, and browser views remain attributable to
that source project and its author.

The playground calls these external services at runtime:

- [TypeSafe System One / JEV](https://docs.typesafe.ai/) for structured cell
  choices.
- [OpenRouter](https://openrouter.ai/docs) for the conventional LLM lane.

Those services and their SDKs/API terms apply to their use. This repository's
original source is released under the Apache License, Version 2.0; see
[LICENSE](LICENSE).
