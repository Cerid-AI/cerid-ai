# Cerid AI Desktop — Installation Guide

## System Requirements

### macOS

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| macOS | 12 (Monterey) or later | 14 (Sonoma) or later |
| RAM | 8 GB | 16 GB |
| Disk Space | 10 GB free | 20 GB free |
| Processor | Intel or Apple Silicon | Apple Silicon |

### Linux (Ubuntu/Debian)

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Ubuntu | 22.04 LTS | 24.04 LTS |
| RAM | 8 GB | 16 GB |
| Disk Space | 10 GB free | 20 GB free |

### Windows

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Windows | 10 (21H2) with WSL2 | 11 with WSL2 |
| RAM | 8 GB | 16 GB |
| Disk Space | 15 GB free | 25 GB free |

## Step 1: Install Docker Desktop

Cerid AI runs its services (database, vector store, cache) inside Docker containers.

- **Apple Silicon Mac (M1/M2/M3/M4):** [Download Docker Desktop for Mac (ARM)](https://desktop.docker.com/mac/main/arm64/Docker.dmg)
- **Intel Mac:** [Download Docker Desktop for Mac (Intel)](https://desktop.docker.com/mac/main/amd64/Docker.dmg)

Open the downloaded `.dmg` file and drag Docker to your Applications folder. Launch Docker Desktop once and wait for it to finish starting (the whale icon in the menu bar should stop animating).

## Step 2: Download Cerid AI

Download the latest `.dmg` from [GitHub Releases](https://github.com/Cerid-AI/cerid-ai/releases).

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

---

## Windows Installation (Docker Desktop + WSL2)

### Step 1: Install WSL2

Open PowerShell as Administrator and run:

```powershell
wsl --install
```

Reboot when prompted. After reboot, a Ubuntu terminal will open to create your Linux user.

### Step 2: Install Docker Desktop for Windows

Download and install [Docker Desktop for Windows (AMD64)](https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe).

After installation, open Docker Desktop and enable WSL2 integration:

1. Go to **Settings > General** and ensure "Use the WSL 2 based engine" is checked
2. Go to **Settings > Resources > WSL Integration** and enable integration with your Ubuntu distro
3. Click **Apply & Restart**

### Step 3: Clone and Setup (in WSL2 terminal)

All remaining commands must be run in a WSL2 terminal (Ubuntu), not PowerShell:

```bash
git clone git@github.com:Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai
bash scripts/setup.sh
```

The setup wizard will guide you through API key configuration and starting the stack.

> **Important:** Clone the repo inside WSL2 (`~/cerid-ai`), not on the Windows filesystem (`/mnt/c/...`). Docker volume performance is significantly better on the native WSL2 filesystem.

---

## Linux Installation (Ubuntu/Debian)

### Step 1: Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in for the group change to take effect.

### Step 2: Install Docker Compose v2

Docker Compose v2 is included with modern Docker Engine installs. Verify:

```bash
docker compose version
```

If missing, install the plugin:

```bash
sudo apt update && sudo apt install docker-compose-plugin
```

### Step 3: Clone and Setup

```bash
git clone git@github.com:Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai
bash scripts/setup.sh
```

---

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
