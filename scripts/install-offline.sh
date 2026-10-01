#!/bin/bash
# ==============================================================================
# install-offline.sh - 100% Offline Installer for Raspberry Pi Zero W
# NO INTERNET REQUIRED: No apt-get, no git clone, no pip install.
# All dependencies (GPX ARMv6 binary, pyserial, bottle) are pre-bundled.
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE} FlashForge WebServer - 100% OFFLINE Installer (No WAN Needed)  ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. Root check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] This script must be run as root.${NC}"
    echo "Please run: sudo ./scripts/install-offline.sh"
    exit 1
fi

# Detect service user (e.g. pi or current sudo user)
TARGET_USER="${SUDO_USER:-pi}"
if ! id "$TARGET_USER" >/dev/null 2>&1; then
    TARGET_USER="root"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="/opt/ffcp-webserver"
SPOOL_DIR="/var/spool/ffcp"

echo -e "${GREEN}[*] Service User:${NC}     $TARGET_USER"
echo -e "${GREEN}[*] Source Directory:${NC} $REPO_DIR"
echo -e "${GREEN}[*] Destination:${NC}      $INSTALL_DIR"

# 2. Install pre-bundled GPX ARM binary
echo -e "\n${BLUE}[1/4] Installing bundled GPX binary...${NC}"
if [ -f "$REPO_DIR/offline/gpx_2.6.8-1_armhf.deb" ] && command -v dpkg >/dev/null 2>&1; then
    echo "Installing via pre-bundled Debian package..."
    dpkg -i "$REPO_DIR/offline/gpx_2.6.8-1_armhf.deb" || true
fi

# If binary not yet at /usr/local/bin/gpx, copy from offline/bin/gpx or /usr/bin/gpx
if [ ! -x "/usr/local/bin/gpx" ]; then
    if [ -x "$REPO_DIR/offline/bin/gpx" ]; then
        echo "Installing standalone binary from offline/bin/gpx..."
        cp "$REPO_DIR/offline/bin/gpx" /usr/local/bin/gpx
        chmod 755 /usr/local/bin/gpx
    elif [ -x "/usr/bin/gpx" ]; then
        ln -sf /usr/bin/gpx /usr/local/bin/gpx
    fi
fi

if [ -x "/usr/local/bin/gpx" ] || [ -x "/usr/bin/gpx" ]; then
    GPX_EXEC=$(command -v gpx || echo "/usr/local/bin/gpx")
    echo -e "${GREEN}[SUCCESS] GPX is ready at: $GPX_EXEC${NC}"
else
    echo -e "${RED}[ERROR] Failed to locate gpx binary in offline bundle.${NC}"
    exit 1
fi

# 3. Configure permissions and spool
echo -e "\n${BLUE}[2/4] Setting up permissions and spool directories...${NC}"
usermod -a -G dialout "$TARGET_USER" || true
mkdir -p "$SPOOL_DIR/queue"
chown -R "$TARGET_USER:$TARGET_USER" "$SPOOL_DIR"
chmod -R 775 "$SPOOL_DIR"

# 4. Deploy application files (includes vendored bottle.py and serial/)
echo -e "\n${BLUE}[3/4] Deploying application to $INSTALL_DIR...${NC}"
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_DIR"/* "$INSTALL_DIR/"
chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR"

# Install daemon wrapper and CLI helper
cp "$REPO_DIR/scripts/gpx-daemon.sh" /usr/local/bin/gpx-daemon.sh
chmod +x /usr/local/bin/gpx-daemon.sh

cp "$REPO_DIR/scripts/ffcp-clear-bed" /usr/local/bin/ffcp-clear-bed
chmod +x /usr/local/bin/ffcp-clear-bed

# 5. Install and enable systemd services
echo -e "\n${BLUE}[4/4] Activating Systemd Services...${NC}"
sed -i "s/^User=.*/User=$TARGET_USER/" "$REPO_DIR/systemd/ffcp-queue.service"
sed -i "s/^Group=.*/Group=$TARGET_USER/" "$REPO_DIR/systemd/ffcp-queue.service"
sed -i "s/^User=.*/User=$TARGET_USER/" "$REPO_DIR/systemd/gpx-daemon.service"
sed -i "s/^Group=.*/Group=$TARGET_USER/" "$REPO_DIR/systemd/gpx-daemon.service"

cp "$REPO_DIR/systemd/gpx-daemon.service" /etc/systemd/system/
cp "$REPO_DIR/systemd/ffcp-queue.service" /etc/systemd/system/

# Clean up any stale root-owned PTY link before restarting services
rm -f /tmp/ffcp-pty

systemctl daemon-reload
systemctl enable gpx-daemon.service ffcp-queue.service
systemctl restart gpx-daemon.service || true
sleep 1
systemctl restart ffcp-queue.service || true

echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}          OFFLINE INSTALLATION COMPLETED SUCCESSFULLY!          ${NC}"
echo -e "${GREEN}================================================================${NC}"

IP_ADDRS=$(hostname -I 2>/dev/null || echo "127.0.0.1")
echo -e "\nAccess your printer web interface on local Wi-Fi:"
for ip in $IP_ADDRS; do
    echo -e "  --> ${GREEN}http://${ip}:8080${NC}"
done
