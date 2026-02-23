# ♾️ AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with Selenium, navigating to seed URLs and performing human-like behaviors (scrolling, idling, back-clicking) in randomized patterns to generate noise that confounds ad tracking and targeting.

## 🐳 Container Image

AdInfinitum is published to GitHub Container Registry (GHCR).

### Pull

```bash
docker pull ghcr.io/philiporlando/adinfinitum:latest
```

### Run

```bash
docker run --rm ghcr.io/philiporlando/adinfinitum:latest
```
