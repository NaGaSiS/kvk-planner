#!/bin/bash
# =============================================================
# KvK Planner — Setup script for Unraid
# Run this in the Unraid web terminal or SSH as root
# =============================================================

set -e

GITHUB_USER="NaGaSiS"
IMAGE="ghcr.io/nagasis/kvk-planner:latest"
APPDATA="/mnt/user/appdata/kvk-planner"
PORT="12348"

# --- Colours ---
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "================================================="
echo "   KvK Planner — Unraid Setup"
echo "================================================="
echo ""

# 1. Ask for GitHub token (read:packages scope)
read -s -p "GitHub Personal Access Token (read:packages): " GH_TOKEN
echo ""
[ -z "$GH_TOKEN" ] && error "Token cannot be empty"

# 2. Login to ghcr.io
info "Logging in to GitHub Container Registry..."
echo "$GH_TOKEN" | docker login ghcr.io -u "$GITHUB_USER" --password-stdin \
  || error "docker login failed — check the token and try again"

# 3. Persist credentials across reboots (saves to USB flash)
info "Persisting credentials for next boot..."
mkdir -p /boot/config/docker-auth
cp /root/.docker/config.json /boot/config/docker-auth/config.json
BOOT_LINE='mkdir -p /root/.docker && cp /boot/config/docker-auth/config.json /root/.docker/config.json'
grep -qF "$BOOT_LINE" /boot/config/go 2>/dev/null || echo "$BOOT_LINE" >> /boot/config/go
info "Credentials saved to /boot/config/docker-auth/config.json"

# 4. Create persistent directories
info "Creating appdata directories..."
mkdir -p "$APPDATA/data" "$APPDATA/uploads" "$APPDATA/logs"

# 5. Prompt for secrets
echo ""
echo "--- Configure your secrets ---"
read -p  "ADMIN_PIN         [default: kvk2025]:           " ADMIN_PIN
read -s -p "SECRET_KEY        [press Enter to keep default]: " SECRET_KEY; echo ""
read -s -p "SUPERADMIN_SECRET [press Enter to keep default]: " SUPERADMIN_SECRET; echo ""
read -s -p "GROQ_API_KEY      [press Enter to skip]:          " GROQ_KEY; echo ""
read -s -p "GEMINI_API_KEY    [press Enter to skip]:          " GEMINI_KEY; echo ""

ADMIN_PIN=${ADMIN_PIN:-kvk2025}
SECRET_KEY=${SECRET_KEY:-kvk-planner-secret-2025-xK9mPqRsTuV}
SUPERADMIN_SECRET=${SUPERADMIN_SECRET:-kvksuperadmin2025}

# 6. Pull image
info "Pulling latest image from ghcr.io..."
docker pull "$IMAGE"

# 7. Stop and remove old container if exists
if docker ps -a --format '{{.Names}}' | grep -q "^kvk-planner$"; then
  warn "Removing existing kvk-planner container..."
  docker stop kvk-planner 2>/dev/null; docker rm kvk-planner 2>/dev/null
fi

# 8. Run container
info "Starting kvk-planner container..."
docker run -d \
  --name kvk-planner \
  --restart unless-stopped \
  -p "${PORT}:5000" \
  -v "${APPDATA}/data:/app/data" \
  -v "${APPDATA}/uploads:/app/app/static/uploads" \
  -v "${APPDATA}/logs:/app/logs" \
  -e DATABASE_PATH=/app/data/planner.db \
  -e SECRET_KEY="$SECRET_KEY" \
  -e ADMIN_PIN="$ADMIN_PIN" \
  -e SUPERADMIN_SECRET="$SUPERADMIN_SECRET" \
  ${GROQ_KEY:+-e GROQ_API_KEY="$GROQ_KEY"} \
  ${GEMINI_KEY:+-e GEMINI_API_KEY="$GEMINI_KEY"} \
  -e ENABLE_SCREENSHOT_UPLOAD=true \
  "$IMAGE"

# 9. Check it started
sleep 3
if docker ps --format '{{.Names}}' | grep -q "^kvk-planner$"; then
  echo ""
  info "✅ KvK Planner is running!"
  echo ""
  # Get server IP
  SERVER_IP=$(ip route get 1 | awk '{print $7; exit}')
  echo -e "   ${GREEN}→ http://${SERVER_IP}:${PORT}${NC}"
  echo ""
else
  error "Container failed to start. Run: docker logs kvk-planner"
fi
