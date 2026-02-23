# AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with Selenium, navigating to seed URLs and performing human-like behaviors (scrolling, idling, back-clicking) in randomized patterns to generate noise that confounds ad tracking and targeting.

**Use case:** Contribute to a distributed effort to obfuscate personal ad profiles at scale.