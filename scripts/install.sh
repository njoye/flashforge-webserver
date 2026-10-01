#!/bin/bash
# ==============================================================================
# install.sh - Automated Deployment Script for FlashForge Creator Pro Print Server
# Target Platform: Raspberry Pi Zero W (ARMv6l, single-core 1 GHz, 512 MB RAM)
# OS: Raspberry Pi OS (Debian-based)
# ==============================================================================

set -e

# ANSI Color codes for clean output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================================${NC}"
echo -e "${BLUE}    FlashForge Creator Pro Web Server - Pi Zero W Installer     ${NC}"
echo -e "${BLUE}================================================================${NC}"

# 1. Root Check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] This installation script must be run as root.${NC}"
    echo "Please execute: sudo ./scripts/install.sh"
    exit 1
fi

# Detect actual non-root user (e.g. pi)
TARGET_USER="${SUDO_USER:-pi}"
if ! id "$TARGET_USER" >/dev/null 2>&1; then
    TARGET_USER="root"
fi
echo -e "${GREEN}[*] Target Service User:${NC} $TARGET_USER"

# Determine base directory of repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="/opt/ffcp-webserver"
SPOOL_DIR="/var/spool/ffcp"

echo -e "${GREEN}[*] Repository source:${NC} $REPO_DIR"
echo -e "${GREEN}[*] Target install dir:${NC} $INSTALL_DIR"
echo -e "${GREEN}[*] Spool directory:${NC}    $SPOOL_DIR"

# 2. System Hardware Check
ARCH=$(uname -m)
echo -e "${GREEN}[*] Detected CPU Architecture:${NC} $ARCH"
if [[ "$ARCH" != "armv6l" && "$ARCH" != "armv7l" && "$ARCH" != "aarch64" && "$ARCH" != "x86_64" ]]; then
    echo -e "${YELLOW}[WARN] Unexpected architecture: $ARCH. Proceeding anyway.${NC}"
fi

# 3. Install Required System Dependencies
echo -e "\n${BLUE}[1/6] Updating APT and installing build essentials...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    build-essential \
    autoconf \
    automake \
    git \
    python3 \
    python3-serial \
    curl

# 4. Clone, Compile and Install GPX Natively
echo -e "\n${BLUE}[2/6] Building Mark Lombard's GPX natively for ARM...${NC}"
BUILD_TMP="/tmp/gpx_compile"
rm -rf "$BUILD_TMP"
mkdir -p "$BUILD_TMP"

echo "Cloning GPX repository (https://github.com/markwal/GPX.git)..."
git clone --depth 1 https://github.com/markwal/GPX.git "$BUILD_TMP"

cd "$BUILD_TMP"
echo "Configuring and compiling GPX..."
if [ -f "./configure" ]; then
    ./configure --prefix=/usr/local
else
    autoreconf -i
    ./configure --prefix=/usr/local
fi

make -j1 # Single-core build to prevent out-of-memory on 512MB RAM
make install

# Verify GPX binary
if [ -x "/usr/local/bin/gpx" ]; then
    GPX_VER=$(/usr/local/bin/gpx -? 2>&1 | head -n 2 | tr '\n' ' ')
    echo -e "${GREEN}[SUCCESS] Installed GPX to /usr/local/bin/gpx (${GPX_VER})${NC}"
else
    echo -e "${RED}[ERROR] GPX compilation failed or binary not found.${NC}"
    exit 1
fi
rm -rf "$BUILD_TMP"

# 5. Configure User Permissions and Spool Directories
echo -e "\n${BLUE}[3/6] Configuring directories and permissions...${NC}"

# Add user to dialout group so serial ports can be accessed without sudo
usermod -a -G dialout "$TARGET_USER"
echo -e "${GREEN}[*] Added $TARGET_USER to 'dialout' group.${NC}"

# Create spool directories
mkdir -p "$SPOOL_DIR/queue"
chown -R "$TARGET_USER:$TARGET_USER" "$SPOOL_DIR"
chmod -R 775 "$SPOOL_DIR"
echo -e "${GREEN}[*] Spool directory configured at $SPOOL_DIR with ownership $TARGET_USER.${NC}"

# 6. Install Application Files
echo -e "\n${BLUE}[4/6] Installing Web Queue Server to $INSTALL_DIR...${NC}"
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_DIR"/* "$INSTALL_DIR/"
chown -R "$TARGET_USER:$TARGET_USER" "$INSTALL_DIR"

# Install daemon wrapper and CLI bed clear tool
cp "$REPO_DIR/scripts/gpx-daemon.sh" /usr/local/bin/gpx-daemon.sh
chmod +x /usr/local/bin/gpx-daemon.sh

cp "$REPO_DIR/scripts/ffcp-clear-bed" /usr/local/bin/ffcp-clear-bed
chmod +x /usr/local/bin/ffcp-clear-bed

# 7. Configure and Install Systemd Services
echo -e "\n${BLUE}[5/6] Installing Systemd Services...${NC}"

# Update user in ffcp-queue.service
sed -i "s/^User=.*/User=$TARGET_USER/" "$REPO_DIR/systemd/ffcp-queue.service"
sed -i "s/^Group=.*/Group=$TARGET_USER/" "$REPO_DIR/systemd/ffcp-queue.service"

cp "$REPO_DIR/systemd/gpx-daemon.service" /etc/systemd/system/gpx-daemon.service
cp "$REPO_DIR/systemd/ffcp-queue.service" /etc/systemd/system/ffcp-queue.service

systemctl daemon-reload

echo "Enabling services on boot..."
systemctl enable gpx-daemon.service
systemctl enable ffcp-queue.service

# 8. Start Services
echo -e "\n${BLUE}[6/6] Starting Services...${NC}"
systemctl restart gpx-daemon.service || true
sleep 2
systemctl restart ffcp-queue.service || true

# 9. Verification & Summary
echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}                 INSTALLATION COMPLETED                         ${NC}"
echo -e "${GREEN}================================================================${NC}"

IP_ADDRS=$(hostname -I | xargs)

echo -e "\n${BLUE}How to Access:${NC}"
for ip in $IP_ADDRS; do
    echo -e "  Web Interface: ${GREEN}http://${ip}:8080${NC}"
done

echo -e "\n${BLUE}Service Status:${NC}"
echo "  gpx-daemon: $(systemctl is-active gpx-daemon.service || echo 'inactive')"
echo "  ffcp-queue: $(systemctl is-active ffcp-queue.service || echo 'inactive')"

echo -e "\n${BLUE}Useful Commands:${NC}"
echo "  View Web Server Logs:   journalctl -u ffcp-queue.service -f"
echo "  View GPX Bridge Logs:   journalctl -u gpx-daemon.service -f"
echo "  Verify Virtual PTY:     ls -l /tmp/ffcp-pty"
echo "  Clear Bed (CLI button): ffcp-clear-bed"
echo -e "\n${GREEN}Ready for printing on FlashForge Creator Pro!${NC}"
