# AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container ad infinitum, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with [Selenium](https://www.selenium.dev/), navigating seed URLs and performing randomized human-like behaviors to confound ad tracking and targeting systems.

## Usage

### Docker

```bash
docker run --rm --name adinfinitum --memory=2g --shm-size=1g \
  ghcr.io/philiporlando/adinfinitum:latest
```

### Docker Compose

```yaml
services:
  adinfinitum:
    image: ghcr.io/philiporlando/adinfinitum:latest
    container_name: adinfinitum
    restart: unless-stopped
    shm_size: "1gb"
    deploy:
      resources:
        limits:
          memory: 2G
```

### Memory

Firefox on ad-heavy pages regularly uses close to 1GB. If the container's memory limit is too low, the kernel kills Firefox's content processes and the logs fill with `Failed to decode response from marionette` or `WebDriver session does not exist` errors followed by browser restarts. Give the container at least 2GB. To check whether that is what's happening, run:

```bash
docker exec adinfinitum grep oom_kill /sys/fs/cgroup/memory.events
```

A non-zero count means the memory limit needs to be raised.

### Custom URLs

To browse your own list of sites, mount a JSON array of URLs over `/app/urls.json`:

```bash
docker run --rm --name adinfinitum --memory=2g --shm-size=1g \
  --mount type=bind,source="$PWD/urls.json",destination=/app/urls.json \
  ghcr.io/philiporlando/adinfinitum:latest
```
