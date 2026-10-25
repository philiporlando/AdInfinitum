# AdInfinitum

Runs the [AdNauseam](https://adnauseam.io/) browser extension in a Docker container ad infinitum, automatically browsing the web to poison advertising profiles through simulated clicks.

The tool orchestrates a headless Firefox instance with [Selenium](https://www.selenium.dev/), navigating seed URLs and performing randomized human-like behaviors to confound ad tracking and targeting systems.

## Usage

### Docker

```bash
docker run --rm --name adinfinitum --memory=2g --shm-size=1g --dns 1.1.1.1 --dns 8.8.8.8 \
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
    dns:
      - 1.1.1.1
      - 8.8.8.8
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

### No ads collected

It's normal for the vault to show zeros for the first few sessions, and AdNauseam doesn't click every ad it collects. If `ads collected` stays at 0 over a longer run, ads probably aren't loading at all. The usual cause is DNS-level ad blocking on your network (Pi-hole, AdGuard Home, router filtering), which is why the examples above set public DNS servers. To check:

```bash
docker exec adinfinitum getent hosts securepubads.g.doubleclick.net
```

If that returns `0.0.0.0`, `::`, or nothing, ad domains are being blocked before AdNauseam can see them.

### Custom URLs

To browse your own list of sites, mount a JSON array of URLs over `/app/urls.json`:

```bash
docker run --rm --name adinfinitum --memory=2g --shm-size=1g --dns 1.1.1.1 --dns 8.8.8.8 \
  --mount type=bind,source="$PWD/urls.json",destination=/app/urls.json \
  ghcr.io/philiporlando/adinfinitum:latest
```
