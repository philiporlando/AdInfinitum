# ♾️ AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with [Selenium](https://www.selenium.dev/), navigating seed URLs and performing randomized human-like behaviors to confound ad tracking and targeting systems.


---

## Container Image

The official image is published to GitHub Container Registry (GHCR): 

```bash
docker run --rm --name adinfinitum ghcr.io/philiporlando/adinfinitum:latest
```

## Docker Compose

```yaml
services:
  adinfinitum:
    image: ghcr.io/philiporlando/adinfinitum:latest
    container_name: adinfinitum
    restart: unless-stopped
```
