#!/bin/bash
# =============================================================
# KvK Planner — Setup script for Unraid
# Run in Unraid web terminal (Tools → Terminal) or SSH as root
# =============================================================

set -e

GITHUB_USER="NaGaSiS"
REPO="kvk-planner"
IMAGE="ghcr.io/nagasis/kvk-planner:latest"
CONTAINER_NAME="KvK-Planner"
APPDATA="/mnt/user/appdata/kvk-planner"
TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
APPS_DIR="/boot/config/plugins/dockerMan/apps"
ICON_URL="https://raw.githubusercontent.com/${GITHUB_USER}/${REPO}/master/app/static/kvk-icon.png"
TEMPLATE_URL="https://raw.githubusercontent.com/${GITHUB_USER}/${REPO}/master/unraid-template.xml"

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

# ── 2. Remove existing container ─────────────────────────────
section "Cleanup"
for NAME in "$CONTAINER_NAME" "kvk-planner"; do
  if docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
    warn "Removing existing container: $NAME"
    docker stop "$NAME" 2>/dev/null || true
    docker rm   "$NAME" 2>/dev/null || true
    info "Removed: $NAME"
  fi
done

# ── 3. Network ───────────────────────────────────────────────
section "Docker Network"
echo "  Available networks:"
docker network ls --format "    • {{.Name}} ({{.Driver}})"
echo ""
read -p "  Network name to use [default: bridge]: " DOCKER_NETWORK
DOCKER_NETWORK=${DOCKER_NETWORK:-bridge}
info "Using network: $DOCKER_NETWORK"

# ── 4. Port ──────────────────────────────────────────────────
read -p "  Host port [default: 12348]: " HOST_PORT
HOST_PORT=${HOST_PORT:-12348}

# ── 5. Paths ─────────────────────────────────────────────────
section "Data Paths"
read -p "  Appdata base path [default: $APPDATA]: " CUSTOM_APPDATA
APPDATA=${CUSTOM_APPDATA:-$APPDATA}
mkdir -p "$APPDATA/data" "$APPDATA/uploads" "$APPDATA/logs"
info "Directories ready at $APPDATA"

# ── 6. Secrets ───────────────────────────────────────────────
section "Application Secrets"
read -p   "  ADMIN_PIN         [default: kvk2025]:               " ADMIN_PIN
read -s -p "  SECRET_KEY        [Enter = use default]:            " SECRET_KEY;        echo ""
read -s -p "  SUPERADMIN_SECRET [Enter = use default]:            " SUPERADMIN_SECRET; echo ""
read -s -p "  GROQ_API_KEY      [Enter = skip AI image analysis]: " GROQ_KEY;          echo ""

ADMIN_PIN=${ADMIN_PIN:-kvk2025}
SECRET_KEY=${SECRET_KEY:-kvk-planner-secret-2025-xK9mPqRsTuV}
SUPERADMIN_SECRET=${SUPERADMIN_SECRET:-kvksuperadmin2025}

# ── 7. Pull image ────────────────────────────────────────────
section "Pulling Image"
docker pull "$IMAGE"

# ── 8. Run container ─────────────────────────────────────────
section "Starting Container"
EXTRA_ENVS=""
[ -n "$GROQ_KEY" ] && EXTRA_ENVS="-e GROQ_API_KEY=$GROQ_KEY"

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

info "Container started"

# ── 9. Install Unraid template + apps XML ────────────────────
section "Installing Unraid Template"
TIMESTAMP=$(date +%s)
mkdir -p "$TEMPLATE_DIR" "$APPS_DIR"

generate_xml() {
cat << XMLEOF
<?xml version="1.0"?>
<Container version="2">
  <Name>${CONTAINER_NAME}</Name>
  <Repository>${IMAGE}</Repository>
  <Registry>https://ghcr.io</Registry>
  <Network>${DOCKER_NETWORK}</Network>
  <MyIP/>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Support>https://github.com/${GITHUB_USER}/${REPO}/issues</Support>
  <Project>https://github.com/${GITHUB_USER}/${REPO}</Project>
  <ReadMe/>
  <Overview>KvK Appointment Planner — Herramienta de coordinacion y scheduling para la fase KvK (Kingdom vs Kingdom) del juego KingShot. Permite a los jugadores registrar recursos y disponibilidad, y al administrador asignar citas de buff optimas. Incluye analisis de imagenes con IA (Groq), soporte multiidioma y consola superadmin.</Overview>
  <Category>Tools: GameServers:</Category>
  <WebUI>http://[IP]:[PORT:${HOST_PORT}]/</WebUI>
  <TemplateURL>${TEMPLATE_URL}</TemplateURL>
  <Icon>${ICON_URL}</Icon>
  <ExtraParams/>
  <PostArgs/>
  <CPUset/>
  <DateInstalled>${TIMESTAMP}</DateInstalled>
  <DonateText/>
  <DonateLink/>
  <Requires/>
  <Config Name="Web UI Port" Target="5000" Default="${HOST_PORT}" Mode="tcp" Description="Puerto de acceso a la aplicacion web KvK Planner." Type="Port" Display="always" Required="true" Mask="false">${HOST_PORT}</Config>
  <Config Name="Data Path" Target="/app/data" Default="${APPDATA}/data" Mode="rw" Description="Base de datos SQLite. IMPORTANTE: haz backup de esta carpeta." Type="Path" Display="always" Required="true" Mask="false">${APPDATA}/data</Config>
  <Config Name="Uploads Path" Target="/app/app/static/uploads" Default="${APPDATA}/uploads" Mode="rw" Description="Imagenes subidas por los jugadores." Type="Path" Display="always" Required="true" Mask="false">${APPDATA}/uploads</Config>
  <Config Name="Logs Path" Target="/app/logs" Default="${APPDATA}/logs" Mode="rw" Description="Logs de la aplicacion." Type="Path" Display="advanced" Required="false" Mask="false">${APPDATA}/logs</Config>
  <Config Name="SECRET_KEY" Target="SECRET_KEY" Default="" Mode="" Description="Clave secreta Flask para cifrar sesiones. Usa una cadena aleatoria larga." Type="Variable" Display="always" Required="true" Mask="true">${SECRET_KEY}</Config>
  <Config Name="ADMIN_PIN" Target="ADMIN_PIN" Default="kvk2025" Mode="" Description="PIN de acceso al panel de administrador de cada evento." Type="Variable" Display="always" Required="true" Mask="true">${ADMIN_PIN}</Config>
  <Config Name="SUPERADMIN_SECRET" Target="SUPERADMIN_SECRET" Default="" Mode="" Description="Token secreto para acceder a /superadmin?secret=TOKEN. Vacio = deshabilitado." Type="Variable" Display="always" Required="false" Mask="true">${SUPERADMIN_SECRET}</Config>
  <Config Name="GROQ_API_KEY" Target="GROQ_API_KEY" Default="" Mode="" Description="API Key de Groq para analisis automatico de capturas (gratis en console.groq.com)." Type="Variable" Display="always" Required="false" Mask="true">${GROQ_KEY}</Config>
  <Config Name="ENABLE_SCREENSHOT_UPLOAD" Target="ENABLE_SCREENSHOT_UPLOAD" Default="true" Mode="" Description="Activa la subida de capturas de pantalla por parte de los jugadores." Type="Variable" Display="advanced" Required="false" Mask="false">true</Config>
  <Config Name="DATABASE_PATH" Target="DATABASE_PATH" Default="/app/data/planner.db" Mode="" Description="Ruta interna a la base de datos SQLite. No cambiar." Type="Variable" Display="advanced-hide" Required="true" Mask="false">/app/data/planner.db</Config>
</Container>
XMLEOF
}

generate_xml > "$TEMPLATE_DIR/KvK-Planner.xml"
generate_xml > "$APPS_DIR/KvK-Planner.xml"

info "Template → $TEMPLATE_DIR/KvK-Planner.xml"
info "Apps XML → $APPS_DIR/KvK-Planner.xml"

# ── 10. Result ───────────────────────────────────────────────
sleep 3
echo ""
SERVER_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo -e "${GREEN}"
  echo "  ✅  KvK Planner instalado y corriendo!"
  echo -e "${NC}"
  echo "  → Web UI:        http://${SERVER_IP}:${HOST_PORT}"
  echo "  → Superadmin:    http://${SERVER_IP}:${HOST_PORT}/superadmin?secret=${SUPERADMIN_SECRET}"
  echo ""
  echo "  ℹ️  Recarga la pestaña Docker de Unraid — el contenedor tendra"
  echo "     icono, boton WebUI, Edit, Project Page, Support y Update."
  echo ""
  echo "  ℹ️  Proximas actualizaciones: git push → GitHub Actions construye"
  echo "     la imagen → Docker en Unraid: 'Check for Updates' → Update"
  echo ""
else
  error "El contenedor no arranco. Revisa: docker logs $CONTAINER_NAME"
fi
