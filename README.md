# FlashForge Creator Pro — Ultra-Lightweight Print Server & Queue Manager

An ultra-lightweight, zero-bloat web print server and queue manager tailored specifically for the **FlashForge Creator Pro** (Dual Extruder, MightyBoard running Sailfish firmware) deployed on a **Raspberry Pi Zero W** (ARMv6l, single-core 1.0 GHz, 512 MB RAM) running Raspberry Pi OS.

---

## ⚠️ Fire Safety & Liability Disclaimer

> [!CAUTION]
> **IMPORTANT SAFETY NOTICE — USE ENTIRELY AT YOUR OWN RISK**
> SEE LICENSE FOR MORE INFORMATION
---

## Architecture Overview

Sailfish firmware uses binary S3G/X3G serial packets rather than standard ASCII G-code. Heavy web servers like OctoPrint or Moonraker can easily overwhelm the single-core 512 MB ARMv6 processor on a Pi Zero W. 

This project solves this by using a compiled native C `gpx` daemon running in pseudo-terminal (PTY) mode (`-D /tmp/ffcp-pty`). A lightweight Python streaming engine talks standard G-code to `/tmp/ffcp-pty`, and GPX translates it to binary X3G on the fly with practically zero CPU overhead.

```
+-------------------------------------------------------------------------+
|                         Raspberry Pi Zero W                             |
|                                                                         |
|  [ Web Browser / Mobile Device ]                                        |
|                 │ (HTTP REST API / Lightweight 1.5s Polling on :8080)    |
|                 ▼                                                       |
|  +───────────────────────────────────────────+                          |
|  | ffcp-queue.service (Python / Bottle)       |                          |
|  |  • server.py (Single-thread REST API)     |                          |
|  |  • gcode_parser.py (Zero-load metadata)   |                          |
|  |  • queue_manager.py (Spool & queue state) |                          |
|  |  • streamer.py (Serial ping-pong flow)    |                          |
|  +───────────────────────────────────────────+                          |
|                 │                                                       |
|                 │ ASCII G-code ("G1 X10 Y10\n", "M105\n")               |
|                 ▼                                                       |
|        /tmp/ffcp-pty (Bidirectional Virtual Serial PTY Symlink)         |
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
                  │ (USB Serial Cable)
                  ▼
   [ FlashForge Creator Pro MightyBoard ]
```

---

## Key Features

1. **Native C GPX PTY Bridge:**
   - On-the-fly conversion of standard G-code to Sailfish binary packets.
   - Automatically detects FlashForge MightyBoard hardware across reboots via `/dev/serial/by-id/*` or `/dev/ttyUSB0` / `/dev/ttyACM0`.
   - Exposes a bidirectional virtual serial port `/tmp/ffcp-pty`.

2. **Ultra-Lightweight Python Streaming Engine (`streamer.py`):**
   - Strictly standard library + vendored pure-Python `pyserial`.
   - Strict ping-pong flow control: sends line-by-line and awaits `ok` ACK.
   - Background `M105` status polling every 2.5s parsing `T0`, `T1`, and `Bed` temperatures.
   - Dedicated non-blocking serial mutex: guarantees that background polling never collides with long physical homing (`G28`) or manual moves.
   - Auto-reconnect: links to the virtual port automatically as soon as GPX finishes hardware handshaking.

3. **Collision Safety Gate ("Awaiting Bed Clearance"):**
   - When a print finishes, the server moves to **`Awaiting Bed Clearance`**.
   - The queue **never** auto-starts the next print on its own, preventing the nozzle from smashing into an unremoved print.
   - A human operator must press **"✓ Bed Cleared — Start Next Job"** in the UI (or run `ffcp-clear-bed` via CLI/GPIO pushbutton) to dispatch the next job.

4. **Emergency Abort:**
   - Clicking **🛑 Emergency Abort** immediately halts streaming, shuts down all heaters (`M104 T0 S0`, `M104 T1 S0`, `M140 S0`), kills extrusion motor (`M108`), lowers bed 10mm (`G91 G1 Z10 G90`), parks the toolhead (`G28 X Y`), and disables steppers (`M84`).

5. **Modern Dark-Mode Dashboard:**
   - Fast, vanilla HTML5/CSS/JavaScript with responsive layout for phone and desktop.
   - Drag-and-drop `.gcode` file upload with automatic metadata extraction (nozzle selection, target temperatures, slicer time estimation).
   - Reorderable print queue (move up, move down, direct start, delete).

---

## File Structure

```
flashforge-webserver/
├── server.py                  # Main Bottle web server & REST API (port 8080)
├── streamer.py                # Serial G-code ping-pong streaming & abort engine
├── queue_manager.py           # Persistent queue manager & safety gate
├── gcode_parser.py            # Streaming G-code header metadata inspector
├── bottle.py                  # Vendored zero-dependency WSGI microframework
├── serial/                    # Vendored pure-Python pyserial 3.5 package
├── static/
│   ├── index.html             # Responsive dashboard layout
│   ├── app.css                # Polished dark-mode styling
│   └── app.js                 # Vanilla JS client (live polling & drag-and-drop)
├── scripts/
│   ├── gpx-daemon.sh          # MightyBoard auto-detection & GPX launcher
│   ├── install-offline.sh     # 100% offline installer (no internet needed)
│   ├── install.sh             # Online compiler installer (for WAN-connected Pis)
│   └── ffcp-clear-bed         # CLI helper / GPIO hook for Bed Cleared button
├── systemd/
│   ├── gpx-daemon.service     # Systemd service for GPX PTY bridge
│   └── ffcp-queue.service     # Systemd service for Web Queue server
├── offline/
│   ├── bin/gpx                # Pre-compiled ARMv6 native binary for Pi Zero W
│   └── gpx_2.6.8-1_armhf.deb  # Raspbian ARMv6 deb package
├── deploy-to-pi.ps1           # 1-Click transfer & install script from Windows
├── examples/
│   └── test_cube.gcode        # Test print fixture
└── tests/
    └── test_components.py     # Automated unit & integration test suite
```

---

## Installation & Deployment

### Option A: 1-Click Deployment from Windows (Recommended)

If your Pi Zero W is connected to your local Wi-Fi:

1. Open PowerShell in this folder.
2. Run the deployment script:
   ```powershell
   .\deploy-to-pi.ps1 -PiIP <PI_IP_ADDRESS> -PiUser <USERNAME>
   ```
   *(e.g., `.\deploy-to-pi.ps1 -PiIP 192.168.1.100 -PiUser pi`)*

This script:
- Bundles the application files into a clean archive (excluding `.git` overhead).
- Transfers the bundle over Wi-Fi in ~1-2 seconds.
- Automatically executes the 100% offline installer on the Pi over SSH.

---

### Option B: Offline Installation on the Pi (No Internet Required)

All runtime dependencies (`bottle.py`, pure-Python `pyserial`, and pre-compiled ARMv6 `gpx`) are bundled in this repository.

1. Copy this project folder to the Pi (via `scp`, USB drive, or SD card).
2. SSH into your Pi:
   ```bash
   cd ~/flashforge-webserver
   chmod +x scripts/*.sh
   sudo ./scripts/install-offline.sh
   ```
   The offline installer finishes in under 10 seconds without making any external network requests.

---

### Option C: Online Installation (When Internet Access is Available)

If your Pi is connected to the internet and you wish to compile GPX natively from source:
```bash
cd ~/flashforge-webserver
chmod +x scripts/*.sh
sudo ./scripts/install.sh
```

---

## Operations & Service Management

### Check Server Status
```bash
# Check web server status
sudo systemctl status ffcp-queue.service

# Check GPX daemon status
sudo systemctl status gpx-daemon.service
```

### Live Log Streaming
```bash
# View web server logs
journalctl -u ffcp-queue.service -f

# View GPX serial bridge logs
journalctl -u gpx-daemon.service -f
```

### Restart Services
```bash
sudo systemctl restart gpx-daemon.service ffcp-queue.service
```

### Test API from Terminal
```bash
curl -s http://localhost:8080/api/status | python3 -m json.tool
```

---

## Slicing Instructions for FlashForge Creator Pro

When exporting `.gcode` from your slicer (**PrusaSlicer**, **OrcaSlicer**, **FlashPrint**, or **Cura**):

1. **Printer Profile:** Select **FlashForge Creator Pro** or **MakerBot Replicator 1 Dual**.
2. **Extruder Selection:**
   - **Right Extruder = `T0`** (standard default nozzle)
   - **Left Extruder = `T1`**
3. **Format:** Export as standard `.gcode`. The server and GPX engine handle the binary S3G/X3G translation automatically.
4. **Print:** Drag the `.gcode` file directly into `http://<PI_IP>:8080` and click **▶ Start Now**.

---

## REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | `GET` | Web Dashboard user interface. |
| `GET /api/status` | `GET` | Live state (`Offline`, `Idle`, `Heating`, `Printing`, `Awaiting Bed Clearance`), temperatures, active job, and progress. |
| `GET /api/queue` | `GET` | Active print, pending queue list, and recent print history. |
| `POST /api/upload` | `POST` | Upload `.gcode` multipart file. Automatically parses header metadata. |
| `POST /api/clear-bed` | `POST` | Confirm bed clearance; releases safety gate and starts next queued job. |
| `POST /api/cancel` | `POST` | Emergency abort: shuts down heaters, drops bed, parks toolhead, and disables steppers. |
| `POST /api/jobs/<id>/action` | `POST` | Manage queued job (`start_now`, `up`, `down`, `delete`). |
| `POST /api/printer/send-gcode` | `POST` | Send manual G-code command (e.g. `M115`, `G28 X Y`). |
| `POST /api/printer/connect` | `POST` | Re-trigger connection to `/tmp/ffcp-pty`. |

---

## License

This project is licensed under the **MIT License** with an explicit **Fire & Thermal Safety Disclaimer**. See the [LICENSE](LICENSE) file for the full text.
