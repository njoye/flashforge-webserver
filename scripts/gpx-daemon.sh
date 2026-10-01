#!/bin/bash
# gpx-daemon.sh - Wrapper script for launching GPX in PTY mode
# Auto-detects FlashForge Creator Pro MightyBoard USB serial connection
# and binds to the virtual PTY port /tmp/ffcp-pty

set -e

PTY_LINK="/tmp/ffcp-pty"
MACHINE_PROFILE="r1d"  # Replicator 1 Dual / FlashForge Creator Pro
BAUDRATE="115200"

echo "[gpx-daemon] Searching for FlashForge MightyBoard serial device..."

find_mightyboard_device() {
    # 1. Search in /dev/serial/by-id/ (persistent across reboots and hub re-enumeration)
    if [ -d "/dev/serial/by-id" ]; then
        for dev in /dev/serial/by-id/*; do
            if [ -e "$dev" ]; then
                case "$dev" in
                    *MakerBot*|*Replicator*|*FlashForge*|*MightyBoard*|*FTDI*|*2303*|*ch34*)
                        echo "$dev"
                        return 0
                        ;;
                esac
            fi
        done
        # Fallback to any serial device in by-id
        for dev in /dev/serial/by-id/*; do
            if [ -e "$dev" ]; then
                echo "$dev"
                return 0
            fi
        done
    fi

    # 2. Fallback to /dev/ttyUSB0 or /dev/ttyACM0
    if [ -e "/dev/ttyUSB0" ]; then
        echo "/dev/ttyUSB0"
        return 0
    elif [ -e "/dev/ttyACM0" ]; then
        echo "/dev/ttyACM0"
        return 0
    fi

    return 1
}

TARGET_PORT=""
MAX_RETRIES=15
RETRY_COUNT=0

while [ -z "$TARGET_PORT" ] && [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if TARGET_PORT=$(find_mightyboard_device); then
        break
    fi
    echo "[gpx-daemon] Waiting for printer USB connection (attempt $((RETRY_COUNT+1))/$MAX_RETRIES)..."
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT+1))
done

if [ -z "$TARGET_PORT" ]; then
    echo "[gpx-daemon] ERROR: No MightyBoard USB serial port detected (/dev/serial/by-id/*, /dev/ttyUSB0, or /dev/ttyACM0)."
    echo "[gpx-daemon] Please verify the printer is powered on and connected via USB."
    exit 1
fi

echo "[gpx-daemon] Detected printer device: $TARGET_PORT"

# Clean up any existing virtual port symlink
if [ -L "$PTY_LINK" ] || [ -e "$PTY_LINK" ]; then
    echo "[gpx-daemon] Removing stale virtual port link: $PTY_LINK"
    rm -f "$PTY_LINK"
fi

# Ensure GPX binary exists
if ! command -v gpx >/dev/null 2>&1 && [ ! -x "/usr/local/bin/gpx" ]; then
    echo "[gpx-daemon] ERROR: gpx binary not found at /usr/local/bin/gpx."
    exit 1
fi

GPX_BIN=$(command -v gpx || echo "/usr/local/bin/gpx")

echo "[gpx-daemon] Launching GPX daemon bridge:"
echo "  Command: $GPX_BIN -m $MACHINE_PROFILE -b $BAUDRATE -D $PTY_LINK $TARGET_PORT"

# Background watchdog to grant read/write permissions on the virtual PTY port
# once GPX creates it, ensuring ffcp-queue (User=pi) can always access it
(
    for i in $(seq 1 30); do
        if [ -e "$PTY_LINK" ]; then
            chmod 666 "$PTY_LINK" 2>/dev/null || true
            break
        fi
        sleep 0.2
    done
) &

# Execute GPX with:
# -m r1d: Replicator 1 Dual (FlashForge Creator Pro MightyBoard)
# -D <path>: create named virtual pseudo-terminal port
# $TARGET_PORT: physical USB serial connection
exec "$GPX_BIN" -m "$MACHINE_PROFILE" -b "$BAUDRATE" -D "$PTY_LINK" "$TARGET_PORT"
