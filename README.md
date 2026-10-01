# FlashForge Creator Pro — Ultra-Lightweight Print Server & Queue Manager

An ultra-lightweight, zero-bloat 3D print server and queue manager tailored specifically for the **FlashForge Creator Pro** (Dual Extruder, MightyBoard running Sailfish firmware) deployed on a **Raspberry Pi Zero W** (ARMv6l, single-core 1.0 GHz, 512 MB RAM) running Raspberry Pi OS.

---

## Architecture Overview

Because Sailfish firmware uses binary S3G/X3G packets rather than ASCII G-code, this server uses a C-compiled `gpx` daemon running in pseudo-terminal (PTY) mode to translate serial communication on the fly with zero overhead.

```
+-------------------------------------------------------------------------+
|                         Raspberry Pi Zero W                             |
|                                                                         |
|  [ Web Browser / Mobile Phone ]                                         |
|                 │ (HTTP / WebSocket-less Polling on Port 8080)          |
|                 ▼                                                       |
|  +───────────────────────────────────────────+                          |
|  | ffcp-queue.service (Python / Bottle)       |                          |
|  |  • server.py (REST API & Web UI)          |                          |
|  |  • gcode_parser.py (Header metadata)       |                          |
|  |  • queue_manager.py (Spool & Queue)       |                          |
|  |  • streamer.py (Ping-Pong Flow Control)   |                          |
|  +───────────────────────────────────────────+                          |
|                 │                                                       |
|                 │ ASCII G-code ("G1 X10 Y10\n", "M105\n")               |
|                 ▼                                                       |
|        /tmp/ffcp-pty (Virtual Serial PTY Symlink)                       |
|                 ▲                                                       |
|                 │ ASCII responses ("ok T:205 /205 B:60 /60\n")          |
|  +───────────────────────────────────────────+                          |
|  | gpx-daemon.service (C Native Daemon)      |                          |
|  |  • Machine Profile: r1d (Replicator 1 D)  |                          |
|  |  • Bi-directional S3G/X3G Packetizer      |                          |
|  +───────────────────────────────────────────+                          |
|                 │                                                       |
|                 ▼ (115200 Baud S3G/X3G Binary Packets)                  |
|        /dev/ttyUSB0 (or /dev/serial/by-id/*)                            |
+─────────────────┼───────────────────────────────────────────────────────+
                  │ (USB Serial)
                  ▼
   [ FlashForge Creator Pro MightyBoard ]
```

---

## Key Features

1. **Native C GPX PTY Bridge:**
   - Translates ASCII G-code to Sailfish binary packets on the fly.
   - Automatically detects MightyBoard USB hardware across reboots using `/dev/serial/by-id/*` or `/dev/ttyUSB0`.
   - Exposes a bidirectional virtual serial port `/tmp/ffcp-pty`.

2. **Ultra-Lightweight Python Streaming Engine (`streamer.py`):**
   - Strictly uses Python standard library + `pyserial`.
   - Strict ping-pong flow control: sends line-by-line and awaits `ok` ACK.
   - Low-frequency background `M105` status polling (every 2.5s) to parse `T0`, `T1`, and `B` actual/target temperatures.
   - Graceful cancel/abort: flushes buffers, stops extrusion, kills heaters (`M104 T0 S0`, `M104 T1 S0`, `M140 S0`), lowers bed 10mm (`G91 G1 Z10 G90`), homes XY (`G28 X Y`), and disables steppers (`M84`).

3. **Collision Safety Gate ("Bed Cleared - Start Next Job"):**
   - When a job finishes, the printer enters `Awaiting Bed Clearance`.
   - The queue **never** automatically starts the next job on its own.
   - A human operator must press **"Bed Cleared - Start Next Job"** in the UI or run `ffcp-clear-bed` to confirm the build plate is cleared before the next queued job is dispatched.

4. **Sleek Single-Page Web App:**
   - Zero-dependency HTML5 / Vanilla JS / Vanilla CSS dark-mode dashboard.
   - Drag-and-drop `.gcode` file upload with automatic header inspection (detects `T0` right, `T1` left, or both; target temperatures; slicer estimated time).
   - Live Status Card with real-time temperatures, animated state badges, progress bar, and lines/bytes counters.
   - Reorderable print queue (move up, move down, direct start, remove).

---

## File Structure

```
flashforge-webserver/
├── server.py                  # Main Bottle web server & REST API (port 8080)
├── streamer.py                # Serial G-code ping-pong streaming & abort engine
├── queue_manager.py           # Persistent queue manager & safety gate
├── gcode_parser.py            # Streaming G-code header metadata inspector
├── bottle.py                  # Vendored zero-dependency WSGI microframework
├── static/
│   ├── index.html             # Responsive dashboard layout
│   ├── app.css                # Polished dark-mode styling
│   └── app.js                 # Vanilla JS client (live polling & drag-and-drop)
├── scripts/
│   ├── install.sh             # 1-step automated installer for Raspberry Pi OS
│   ├── gpx-daemon.sh          # MightyBoard auto-detection & GPX launcher
│   └── ffcp-clear-bed         # CLI helper / GPIO hook for Bed Cleared button
├── systemd/
│   ├── gpx-daemon.service     # Systemd unit for GPX PTY bridge
│   └── ffcp-queue.service     # Systemd unit for Web Queue server
├── examples/
│   └── test_cube.gcode        # Test print with dual-extruder comments
└── tests/
    └── test_components.py     # Automated unit & integration test suite
```

---

## Offline Deployment (Local Wi-Fi Without Internet)

If your Pi Zero W is connected to a local Wi-Fi network without internet access (WAN), **you do not need apt-get, git clone, or pip!** All dependencies are pre-bundled in this repository:
- **`bottle.py`**: Vendored single-file micro-framework.
- **`serial/`**: Vendored pure Python `pyserial` package.
- **`offline/bin/gpx`**: Pre-compiled native ARMv6 binary for Raspberry Pi Zero W.
- **`offline/gpx_2.6.8-1_armhf.deb`**: Official Raspbian package.

### Option A: 1-Click Push from Windows (Easiest)
From your Windows PC in PowerShell, run:
```powershell
.\deploy-to-pi.ps1 -PiIP 192.168.1.50 -PiUser pi
```
*(Replace `192.168.1.50` with your Pi's local IP or `raspberrypi.local`)*.

This will automatically:
1. Copy all project files across your local Wi-Fi via `scp`.
2. Run the offline installer on the Pi over SSH.
3. Start the services and verify the web dashboard.

---

### Option B: Manual Local Wi-Fi Transfer (SCP / SFTP / USB)

1. **Transfer the folder to the Pi:**
   From your PC terminal:
   ```bash
   scp -r . pi@<pi-ip>:~/flashforge-webserver
   ```
   *(Or copy the folder onto a USB drive / MicroSD card partition)*.

2. **Run the 100% Offline Installer on the Pi:**
   ```bash
   ssh pi@<pi-ip>
   cd ~/flashforge-webserver
   sudo ./scripts/install-offline.sh
   ```
   The offline installer finishes in under 10 seconds without attempting any internet connections.

---

## Online Installation (When Internet Access is Available)

If your Pi has active WAN/Internet access, you can also run the standard online installer:
```bash
sudo ./scripts/install.sh
```

## Manual Step-by-Step Setup

If you prefer installing manually without running `install.sh`:

### 1. Build and Install GPX
```bash
sudo apt-get update
sudo apt-get install -y build-essential autoconf automake git python3 python3-serial curl

# Clone and compile GPX
git clone --depth 1 https://github.com/markwal/GPX.git /tmp/gpx
cd /tmp/gpx
./configure --prefix=/usr/local
make -j1
sudo make install
/usr/local/bin/gpx -?
```

### 2. Configure Permissions & Spool
```bash
sudo usermod -a -G dialout pi
sudo mkdir -p /var/spool/ffcp/queue
sudo chown -R pi:pi /var/spool/ffcp
sudo chmod -R 775 /var/spool/ffcp
```

### 3. Deploy Application Files
```bash
sudo mkdir -p /opt/ffcp-webserver
sudo cp -r . /opt/ffcp-webserver/
sudo chown -R pi:pi /opt/ffcp-webserver

sudo cp scripts/gpx-daemon.sh /usr/local/bin/gpx-daemon.sh
sudo chmod +x /usr/local/bin/gpx-daemon.sh

sudo cp scripts/ffcp-clear-bed /usr/local/bin/ffcp-clear-bed
sudo chmod +x /usr/local/bin/ffcp-clear-bed
```

### 4. Install & Enable Systemd Services
```bash
sudo cp systemd/gpx-daemon.service /etc/systemd/system/
sudo cp systemd/ffcp-queue.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gpx-daemon.service ffcp-queue.service
sudo systemctl start gpx-daemon.service
sudo systemctl start ffcp-queue.service
```

---

## Verification & Operations

### 1. Verify the GPX Virtual PTY Bridge
Check that GPX has created the virtual port symlink:
```bash
ls -l /tmp/ffcp-pty
```
Expected output:
```
lrwxrwxrwx 1 root root 10 Sep 30 16:30 /tmp/ffcp-pty -> /dev/pts/1
```

Test serial communication directly with Python or minicom:
```bash
python3 -c "import serial; s = serial.Serial('/tmp/ffcp-pty', 115200, timeout=2); s.write(b'M105\n'); print(s.readline())"
```
Expected output:
```
b'ok T:25.0 /0.0 B:24.0 /0.0 T0:25.0 /0.0 T1:25.0 /0.0 @:0 B@:0\n'
```

### 2. Access the Web Interface
Open any browser on your phone, tablet, or PC on the same Wi-Fi network:
```
http://<pi-ip-address>:8080
```
(Find your IP with `hostname -I`).

### 3. Monitor Logs with `journalctl`
```bash
# Monitor the Web Queue Server
journalctl -u ffcp-queue.service -f

# Monitor the GPX Serial Translation Daemon
journalctl -u gpx-daemon.service -f
```

---

## Safety Gate & Physical Pushbutton Hook

When a print completes:
1. The print server transitions the state to **`Awaiting Bed Clearance`**.
2. Even if pending prints exist in the queue, **the printer will NOT move**.
3. Clear the printed object from the bed.
4. Click **"✓ Bed Cleared — Start Next Job"** in the web interface.

### Connecting a Physical Pushbutton (GPIO):
You can wire a momentary tactile pushbutton between **GPIO 26** and **GND** on the Pi Zero W.
Run this lightweight background listener:
```python
#!/usr/bin/env python3
import subprocess, time
import RPi.GPIO as GPIO

BUTTON_PIN = 26
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

while True:
    GPIO.wait_for_edge(BUTTON_PIN, GPIO.FALLING, bouncetime=500)
    subprocess.run(["/usr/local/bin/ffcp-clear-bed"])
    time.sleep(1)
```

---

## REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/status` | `GET` | Live state (`Idle`, `Heating`, `Printing`, `Awaiting Bed Clearance`), temperatures, and print progress. |
| `GET /api/queue` | `GET` | Active job, list of pending jobs, and recent history. |
| `POST /api/upload` | `POST` | Upload `.gcode` multipart file. Parses header and adds to queue. |
| `POST /api/clear-bed` | `POST` | Confirm bed clearance; dispatches next job. |
| `POST /api/cancel` | `POST` | Emergency abort active print. |
| `POST /api/jobs/<id>/action` | `POST` | Manage queue (`delete`, `up`, `down`, `start_now`). |
| `POST /api/printer/send-gcode` | `POST` | Send manual G-code command in terminal. |
| `POST /api/printer/connect` | `POST` | Reconnect virtual serial port. |
