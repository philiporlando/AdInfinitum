# ♾️ AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with Selenium, navigating to seed URLs and performing human-like behaviors (scrolling, idling, back-clicking) in randomized patterns to generate noise that confounds ad tracking and targeting.

---

## Container Image

The official image is published to GitHub Container Registry (GHCR): 

```bash
docker run --rm ghcr.io/philiporlando/adinfinitum:latest
```

## Docker Compose

```yaml
services:
  adinfinitum:
    image: ghcr.io/philiporlando/adinfinitum:latest
    container_name: adinfinitum
    restart: unless-stopped
```
