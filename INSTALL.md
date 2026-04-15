# Cerid AI — Installation Guide

Cerid AI runs as a set of Docker containers (database, vector store, cache,
LLM gateway, API, web UI). Installation is the same on every platform:

1. Install Docker
2. Clone this repository
3. Run `bash scripts/setup.sh`

The setup script checks prerequisites, prompts for an LLM API key, brings up
all containers, and verifies every service is healthy.

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

---

## macOS Installation

### Step 1: Install Docker Desktop

Docker Desktop provides the container runtime Cerid AI depends on.

- **Homebrew (recommended):**

  ```bash
  brew install --cask docker
  ```

- **Manual install:**
  - Apple Silicon (M1/M2/M3/M4): [Docker Desktop for Mac (ARM)](https://desktop.docker.com/mac/main/arm64/Docker.dmg)
  - Intel Mac: [Docker Desktop for Mac (Intel)](https://desktop.docker.com/mac/main/amd64/Docker.dmg)

Launch Docker Desktop once so it finishes first-run initialization (the whale
icon in the menu bar will stop animating when it is ready).

### Step 2: Clone the repository

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai
```

### Step 3: Run the setup script

```bash
bash scripts/setup.sh
```

The script will:

- Verify Docker is running and has enough resources.
- Prompt for your LLM API key (OpenRouter recommended; see `docs/PROVIDERS.md`
  for alternatives).
- Start the infrastructure, Bifrost, MCP server, and web UI in the correct
  order.
- Run a post-startup reachability check on every service.

When it finishes, open <http://localhost:3000> in your browser.

---

## Linux Installation (Ubuntu/Debian)

### Step 1: Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

Log out and back in so the new group membership takes effect.

### Step 2: Install Docker Compose v2

Docker Compose v2 is bundled with modern Docker Engine installs. Verify:

```bash
docker compose version
```

If missing, install the plugin:

```bash
sudo apt update && sudo apt install docker-compose-plugin
```

### Step 3: Clone and setup

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai
bash scripts/setup.sh
```

---

## Windows Installation (Docker Desktop + WSL2)

### Step 1: Install WSL2

Open PowerShell as Administrator and run:

```powershell
wsl --install
```

Reboot when prompted. After reboot, a Ubuntu terminal will open to create
your Linux user.

### Step 2: Install Docker Desktop for Windows

Download and install [Docker Desktop for Windows (AMD64)](https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe).

After installation, open Docker Desktop and enable WSL2 integration:

1. **Settings > General:** ensure "Use the WSL 2 based engine" is checked.
2. **Settings > Resources > WSL Integration:** enable integration with your
   Ubuntu distro.
3. Click **Apply & Restart**.

### Step 3: Clone and setup (in WSL2 terminal)

All remaining commands must be run in a WSL2 (Ubuntu) terminal, **not**
PowerShell:

```bash
git clone https://github.com/Cerid-AI/cerid-ai.git ~/cerid-ai
cd ~/cerid-ai
bash scripts/setup.sh
```

> **Important:** Clone the repo inside WSL2 (`~/cerid-ai`), not on the
> Windows filesystem (`/mnt/c/...`). Docker volume performance is
> significantly better on the native WSL2 filesystem.

---

## Troubleshooting

### Docker Desktop does not start (macOS)

1. Open Docker Desktop manually from your Applications folder.
2. Wait for the whale icon in the menu bar to stop animating.
3. Re-run `bash scripts/setup.sh`.

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

If another application is using one of these ports, stop that application
first or configure port overrides in `.env` using the `CERID_PORT_*`
variables (see `.env.example`).

### Services fail to start

Re-run the validation script to pinpoint the failure:

```bash
./scripts/validate-env.sh
```

For a one-line container status check:

```bash
./scripts/validate-env.sh --quick
```

Container logs are available via:

```bash
docker compose logs --tail=200 <service>
```

### Insufficient memory

If containers crash or fail to start, ensure Docker Desktop has at least 4 GB
of memory allocated:

1. Open Docker Desktop > **Settings > Resources**.
2. Set Memory to 4 GB or higher (6 GB recommended).
3. Click **Apply & Restart**.
