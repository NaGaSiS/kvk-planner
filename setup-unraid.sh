#!/bin/bash
# =============================================================
# KvK Planner — Setup script for Unraid
# Run in Unraid web terminal (Tools → Terminal) or SSH as root
# =============================================================

set -e

GITHUB_USER="NaGaSiS"
IMAGE="ghcr.io/nagasis/kvk-planner:latest"
CONTAINER_NAME="KvK-Planner"
APPDATA="/mnt/user/appdata/kvk-planner"
TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
APPS_DIR="/boot/config/plugins/dockerMan/apps"

# --- Colours ---
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
section() { echo -e "\n${CYAN}--- $1 ---${NC}"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║    KvK Planner — Unraid Installer     ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

# ── 1. GitHub token ──────────────────────────────────────────
section "GitHub Container Registry"
read -s -p "  GitHub Personal Access Token (read:packages): " GH_TOKEN; echo ""
[ -z "$GH_TOKEN" ] && error "Token cannot be empty"

info "Logging in to ghcr.io..."
echo "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin \
  || error "Login failed — check the token"

info "Persisting credentials across reboots..."
mkdir -p /boot/config/docker-auth
cp /root/.docker/config.json /boot/config/docker-auth/config.json
BOOT_LINE='mkdir -p /root/.docker && cp /boot/config/docker-auth/config.json /root/.docker/config.json'
grep -qF "$BOOT_LINE" /boot/config/go 2>/dev/null || echo "$BOOT_LINE" >> /boot/config/go

# ── 2. Remove existing container if present ──────────────────
section "Cleanup"
if docker ps -a --format '{{.Names}}' | grep -qi "^${CONTAINER_NAME}$\|^kvk-planner$"; then
  warn "Found existing container — removing it..."
  docker stop "$CONTAINER_NAME" 2>/dev/null || docker stop kvk-planner 2>/dev/null || true
  docker rm   "$CONTAINER_NAME" 2>/dev/null || docker rm   kvk-planner 2>/dev/null || true
  info "Old container removed"
else
  info "No existing container found"
fi

# ── 3. Network ───────────────────────────────────────────────
section "Docker Network"
echo "  Available networks:"
docker network ls --format "    • {{.Name}} ({{.Driver}})" | grep -v "^$"
echo ""
read -p "  Network name to use [default: bridge]: " DOCKER_NETWORK
DOCKER_NETWORK=${DOCKER_NETWORK:-bridge}
info "Using network: $DOCKER_NETWORK"

# ── 4. Port ──────────────────────────────────────────────────
read -p "  Host port [default: 12348]: " HOST_PORT
HOST_PORT=${HOST_PORT:-12348}

# ── 5. Paths ─────────────────────────────────────────────────
section "Data Paths"
read -p "  Appdata path [default: $APPDATA]: " CUSTOM_APPDATA
APPDATA=${CUSTOM_APPDATA:-$APPDATA}
mkdir -p "$APPDATA/data" "$APPDATA/uploads" "$APPDATA/logs"
info "Directories created at $APPDATA"

# ── 6. Secrets ───────────────────────────────────────────────
section "Application Secrets"
read -p  "  ADMIN_PIN         [default: kvk2025]:                  " ADMIN_PIN
read -s -p "  SECRET_KEY        [Enter = keep current]:              " SECRET_KEY;        echo ""
read -s -p "  SUPERADMIN_SECRET [Enter = keep current]:              " SUPERADMIN_SECRET; echo ""
read -s -p "  GROQ_API_KEY      [Enter = skip AI image analysis]:    " GROQ_KEY;          echo ""

ADMIN_PIN=${ADMIN_PIN:-kvk2025}
SECRET_KEY=${SECRET_KEY:-kvk-planner-secret-2025-xK9mPqRsTuV}
SUPERADMIN_SECRET=${SUPERADMIN_SECRET:-kvksuperadmin2025}

# ── 7. Pull image ────────────────────────────────────────────
section "Pulling Image"
docker pull "$IMAGE"

# ── 8. Build docker run flags ────────────────────────────────
EXTRA_ENVS=""
[ -n "$GROQ_KEY" ] && EXTRA_ENVS="$EXTRA_ENVS -e GROQ_API_KEY=$GROQ_KEY"

# ── 9. Run container ─────────────────────────────────────────
section "Starting Container"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --network "$DOCKER_NETWORK" \
  -p "${HOST_PORT}:5000" \
  -v "${APPDATA}/data:/app/data" \
  -v "${APPDATA}/uploads:/app/app/static/uploads" \
  -v "${APPDATA}/logs:/app/logs" \
  -e DATABASE_PATH=/app/data/planner.db \
  -e SECRET_KEY="$SECRET_KEY" \
  -e ADMIN_PIN="$ADMIN_PIN" \
  -e SUPERADMIN_SECRET="$SUPERADMIN_SECRET" \
  -e ENABLE_SCREENSHOT_UPLOAD=true \
  $EXTRA_ENVS \
  "$IMAGE"

# ── 10. Install Unraid template XML ──────────────────────────
section "Installing Unraid Template"
TIMESTAMP=$(date +%s)
SERVER_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')

mkdir -p "$TEMPLATE_DIR" "$APPS_DIR"

# Template XML (for "Add Container" → My Templates)
cat > "$TEMPLATE_DIR/KvK-Planner.xml" << XMLEOF
<?xml version="1.0"?>
<Container version="2">
  <Name>${CONTAINER_NAME}</Name>
  <Repository>${IMAGE}</Repository>
  <Registry>https://ghcr.io</Registry>
  <Network>${DOCKER_NETWORK}</Network>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Support>https://github.com/${GITHUB_USER}/kvk-planner</Support>
  <Overview>KvK Appointment Planner — Herramienta de coordinacion para KingShot KvK. Asigna citas de buff, analiza recursos con IA y coordina alianzas.</Overview>
  <Category>Tools:</Category>
  <WebUI>http://[IP]:[PORT:${HOST_PORT}]/</WebUI>
  <Icon>https://raw.githubusercontent.com/${GITHUB_USER}/kvk-planner/master/app/static/favicon.svg</Icon>
  <ExtraParams/>
  <DateInstalled>${TIMESTAMP}</DateInstalled>
  <Config Name="Web UI Port" Target="5000" Default="${HOST_PORT}" Mode="tcp" Description="Puerto de acceso a la app" Type="Port" Display="always" Required="true" Mask="false">${HOST_PORT}</Config>
  <Config Name="Data (Base de datos)" Target="/app/data" Default="${APPDATA}/data" Mode="rw" Description="SQLite database. Haz backup de esta carpeta." Type="Path" Display="always" Required="true" Mask="false">${APPDATA}/data</Config>
  <Config Name="Uploads (Capturas jugadores)" Target="/app/app/static/uploads" Default="${APPDATA}/uploads" Mode="rw" Description="Imagenes subidas por jugadores." Type="Path" Display="always" Required="true" Mask="false">${APPDATA}/uploads</Config>
  <Config Name="Logs" Target="/app/logs" Default="${APPDATA}/logs" Mode="rw" Description="Logs de la aplicacion." Type="Path" Display="advanced" Required="false" Mask="false">${APPDATA}/logs</Config>
  <Config Name="SECRET_KEY" Target="SECRET_KEY" Default="" Mode="" Description="Clave secreta Flask para sesiones." Type="Variable" Display="always" Required="true" Mask="true">${SECRET_KEY}</Config>
  <Config Name="ADMIN_PIN" Target="ADMIN_PIN" Default="kvk2025" Mode="" Description="PIN de acceso al panel de admin." Type="Variable" Display="always" Required="true" Mask="true">${ADMIN_PIN}</Config>
  <Config Name="SUPERADMIN_SECRET" Target="SUPERADMIN_SECRET" Default="" Mode="" Description="Token para /superadmin?secret=..." Type="Variable" Display="always" Required="false" Mask="true">${SUPERADMIN_SECRET}</Config>
  <Config Name="GROQ_API_KEY" Target="GROQ_API_KEY" Default="" Mode="" Description="API Key de Groq para analisis de imagenes (gratis en console.groq.com)." Type="Variable" Display="always" Required="false" Mask="true">${GROQ_KEY}</Config>
  <Config Name="ENABLE_SCREENSHOT_UPLOAD" Target="ENABLE_SCREENSHOT_UPLOAD" Default="true" Mode="" Description="Activar subida de capturas." Type="Variable" Display="advanced" Required="false" Mask="false">true</Config>
  <Config Name="DATABASE_PATH" Target="DATABASE_PATH" Default="/app/data/planner.db" Mode="" Description="Ruta interna DB." Type="Variable" Display="advanced-hide" Required="true" Mask="false">/app/data/planner.db</Config>
</Container>
XMLEOF

# Apps XML (makes Unraid show it as template-managed with full UI)
cp "$TEMPLATE_DIR/KvK-Planner.xml" "$APPS_DIR/KvK-Planner.xml"

info "Template installed → $TEMPLATE_DIR/KvK-Planner.xml"
info "Apps config installed → $APPS_DIR/KvK-Planner.xml"

# ── 11. Result ───────────────────────────────────────────────
sleep 3
echo ""
if docker ps --format '{{.Names}}' | grep -qi "^${CONTAINER_NAME}$"; then
  echo -e "${GREEN}"
  echo "  ✅  KvK Planner is running!"
  echo -e "${NC}"
  echo "  → Web UI:       http://${SERVER_IP}:${HOST_PORT}"
  echo "  → Superadmin:   http://${SERVER_IP}:${HOST_PORT}/superadmin?secret=${SUPERADMIN_SECRET}"
  echo ""
  echo "  ℹ️  Recarga la pestaña Docker en Unraid para ver el contenedor con su plantilla."
  echo "  ℹ️  Para actualizar: Docker → KvK-Planner → 'Check for Updates'"
  echo ""
else
  error "Container failed to start. Run: docker logs $CONTAINER_NAME"
fi
