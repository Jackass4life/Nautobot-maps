# Development Environment — Real Nautobot

This directory provides a **real Nautobot 3.x** stack for local development
and CI integration testing, replacing the lightweight mock used in `demo/`.

## Components

| Service         | Image                                | Port  |
|-----------------|--------------------------------------|-------|
| PostgreSQL 15   | `postgres:15`                        | —     |
| Redis 7         | `redis:7-alpine`                     | —     |
| Nautobot 3.x    | `ghcr.io/nautobot/nautobot:3.0.9`    | 8080  |
| nautobot-maps   | built from repo `Dockerfile`         | 5000  |

## Quick Start

```bash
# 1. Start the Nautobot stack (postgres + redis + nautobot)
docker compose -f development/docker-compose.yml up -d --wait

# 2. Seed test data (locations, devices, ASNs, …)
python development/seed_nautobot.py

# 3. (Optional) Start nautobot-maps alongside Nautobot
docker compose -f development/docker-compose.yml --profile full up -d

# 4. Run integration tests against the real Nautobot
NAUTOBOT_LIVE_URL=http://localhost:8080 \
NAUTOBOT_LIVE_TOKEN=aaaa-bbbb-cccc-dddd-eeee \
python -m pytest tests/test_nautobot_live.py -v
```

## Default Credentials

| Item            | Value                          |
|-----------------|--------------------------------|
| Admin user      | `admin`                        |
| Admin password  | `admin`                        |
| API token       | `aaaa-bbbb-cccc-dddd-eeee`     |
| Nautobot URL    | `http://localhost:8080`        |

> **⚠️  These credentials are for local development only.**
> Never use them in production.

## Seeded Data

The `seed_nautobot.py` script populates the same data that the
`demo/mock_nautobot.py` mock server returns:

- **4 tenants** — Acme Corp, Nordic Net, EuroIX, DataCenter GmbH
- **4 location types** — Data Center, PoP, Office, Internet Exchange
- **8 European locations** with GPS coordinates
- **14 network devices** across those locations
- **9 ASNs** in the private range

## Tear Down

```bash
docker compose -f development/docker-compose.yml --profile full down -v
```

The `-v` flag removes the PostgreSQL data volume so you get a clean state
next time.

## CI Integration

The GitHub Actions workflow (`.github/workflows/ci.yml`) includes an
`integration-nautobot` job that automatically:

1. Starts the real Nautobot stack
2. Seeds it with test data
3. Runs the live integration tests (`tests/test_nautobot_live.py`)

This runs on every push / PR to `main`, in parallel with the existing
mock-based test matrix.
