# Cerid AI Desktop — Installation Guide

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| macOS | 12 (Monterey) or later | 14 (Sonoma) or later |
| RAM | 8 GB | 16 GB |
| Disk Space | 10 GB free | 20 GB free |
| Processor | Intel or Apple Silicon | Apple Silicon |

## Step 1: Install Docker Desktop

Cerid AI runs its services (database, vector store, cache) inside Docker containers.

- **Apple Silicon Mac (M1/M2/M3/M4):** [Download Docker Desktop for Mac (ARM)](https://desktop.docker.com/mac/main/arm64/Docker.dmg)
- **Intel Mac:** [Download Docker Desktop for Mac (Intel)](https://desktop.docker.com/mac/main/amd64/Docker.dmg)

Open the downloaded `.dmg` file and drag Docker to your Applications folder. Launch Docker Desktop once and wait for it to finish starting (the whale icon in the menu bar should stop animating).

## Step 2: Download Cerid AI

Download the latest `.dmg` from [GitHub Releases](https://github.com/sunrunnerfire/cerid-ai/releases).

## Step 3: Install

1. Open the downloaded `Cerid AI.dmg`
2. Drag the Cerid AI icon to the Applications folder
3. Eject the disk image

## Step 4: First Launch

1. Open Cerid AI from your Applications folder
2. If Docker Desktop is not running, the app will attempt to start it automatically
3. The built-in setup wizard will guide you through:
   - Entering your LLM API key (OpenRouter recommended)
   - Starting the service containers
   - Verifying all services are healthy

After the first-run wizard completes, Cerid AI is ready to use.

## Troubleshooting

### "Cerid AI cannot be opened because it is from an unidentified developer"

This happens if the app is not yet notarized. To bypass:

1. Open **System Settings** > **Privacy & Security**
2. Scroll down to the Security section
3. Click **Open Anyway** next to the Cerid AI message

Alternatively, right-click the app in Finder and select **Open** from the context menu.

### Docker Desktop does not start

1. Open Docker Desktop manually from your Applications folder
2. Wait for the whale icon in the menu bar to stop animating
3. Then re-launch Cerid AI

### Port conflicts

Cerid AI uses the following ports by default:

| Service | Port |
|---------|------|
| React GUI | 3000 |
| MCP Server | 8888 |
| Bifrost | 8080 |
| Neo4j | 7474 / 7687 |
| ChromaDB | 8001 |
| Redis | 6379 |

If another application is using one of these ports, stop that application first or configure port overrides in `.env` using `CERID_PORT_*` variables.

### Services fail to start

Check the tray icon color:
- **Green** — all services healthy
- **Yellow** — some services degraded (click the tray icon to see details)
- **Red** — services offline

Open the app and navigate to Settings to view container logs and restart individual services.

### Insufficient memory

If containers crash or fail to start, ensure Docker Desktop has at least 4 GB of memory allocated:

1. Open Docker Desktop > Settings > Resources
2. Set Memory to 4 GB or higher (6 GB recommended)
3. Click Apply & Restart
