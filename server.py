#!/usr/bin/env python3
"""
server.py - Ultra-lightweight Web Print Server and Queue Manager.
Built with Bottle for Raspberry Pi Zero W (ARMv6l, single-core 1 GHz, 512 MB RAM).
Listens on port 8080.
"""

import os
import sys
import json
import argparse
import logging

# Ensure local directory is in path for imports
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from bottle import Bottle, request, response, static_file, run

from streamer import GcodeStreamer
from queue_manager import QueueManager
from gcode_parser import inspect_gcode_file

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("server")

app = Bottle()

# Global instances initialized in main()
streamer: GcodeStreamer = None
queue_mgr: QueueManager = None
STATIC_DIR = os.path.join(BASE_DIR, "static")


# --- Static and Web UI Routes ---

@app.get('/')
def index():
    """Serve the single-page HTML interface."""
    return static_file("index.html", root=STATIC_DIR)


@app.get('/static/<filepath:path>')
def server_static(filepath):
    """Serve CSS, JS, and assets."""
    return static_file(filepath, root=STATIC_DIR)


# --- REST API Endpoints ---

@app.get('/api/status')
def api_status():
    """Return live printer status, temperatures, progress, and active job."""
    response.content_type = 'application/json'
    status_data = streamer.get_status()
    # Enrich with queue counts
    status_data["queue_summary"] = {
        "pending_count": len(queue_mgr.pending_jobs),
        "history_count": len(queue_mgr.history_jobs)
    }
    return json.dumps(status_data)


@app.get('/api/queue')
def api_queue():
    """Return active job, pending jobs list, and recent print history."""
    response.content_type = 'application/json'
    return json.dumps(queue_mgr.get_queue_data())


@app.post('/api/upload')
def api_upload():
    """Handle drag-and-drop or file picker .gcode file uploads."""
    response.content_type = 'application/json'

    upload = request.files.get('file')
    owner = request.forms.get('owner', 'Operator').strip() or 'Operator'

    if not upload:
        response.status = 400
        return json.dumps({"success": False, "message": "No file uploaded."})

    filename = upload.filename
    if not filename.lower().endswith(".gcode"):
        response.status = 400
        return json.dumps({"success": False, "message": "Only .gcode files are accepted."})

    # Save to temp location for inspection
    temp_path = os.path.join(queue_mgr.queue_dir, f"tmp_{filename}")
    try:
        upload.save(temp_path, overwrite=True)
    except Exception as e:
        logger.error("Failed to save uploaded file: %s", e)
        response.status = 500
        return json.dumps({"success": False, "message": f"Storage error: {e}"})

    # Inspect G-code header for tool, temps, and estimated time
    try:
        metadata = inspect_gcode_file(temp_path)
    except Exception as e:
        logger.error("Error inspecting G-code file: %s", e)
        metadata = {
            "tool": "Right (T0)",
            "tool_code": "T0",
            "target_temps": {"tool0": 0, "tool1": 0, "bed": 0},
            "estimated_time": "Unknown",
            "total_lines": 0,
            "file_size_bytes": os.path.getsize(temp_path)
        }

    # Add to persistent queue
    try:
        job = queue_mgr.add_job(temp_path, filename, owner, metadata)
        # Remove initial temp upload if different
        if os.path.exists(temp_path) and temp_path != job["filepath"]:
            try:
                os.remove(temp_path)
            except OSError:
                pass

        return json.dumps({
            "success": True,
            "message": f"File '{filename}' added to queue.",
            "job": job
        })
    except Exception as e:
        logger.exception("Error adding job to queue: %s", e)
        response.status = 500
        return json.dumps({"success": False, "message": f"Queue error: {e}"})


@app.post('/api/clear-bed')
def api_clear_bed():
    """
    Operator Safety Gate:
    Confirm that the prior print is removed and the build bed is clear.
    Transitions printer state to Idle and dispatches next pending job.
    """
    response.content_type = 'application/json'
    result = queue_mgr.bed_cleared()
    return json.dumps(result)


@app.post('/api/cancel')
def api_cancel():
    """Emergency abort active print."""
    response.content_type = 'application/json'
    success = queue_mgr.cancel_active_job()
    return json.dumps({
        "success": success,
        "message": "Abort sequence executed." if success else "No active print to abort."
    })


@app.post('/api/jobs/<job_id>/action')
def api_job_action(job_id):
    """Perform action on queue item (delete, up, down, start_now)."""
    response.content_type = 'application/json'
    try:
        data = request.json or {}
    except Exception:
        data = {}

    action = data.get("action", "")

    if action == "delete":
        success = queue_mgr.remove_job(job_id)
        return json.dumps({"success": success, "message": "Job removed." if success else "Job not found."})
    elif action in ("up", "down"):
        success = queue_mgr.move_job(job_id, action)
        return json.dumps({"success": success, "message": f"Job moved {action}." if success else "Cannot move job."})
    elif action == "start_now":
        success = queue_mgr.start_job_now(job_id)
        return json.dumps({"success": success, "message": "Job started." if success else "Printer busy or cannot start."})
    else:
        response.status = 400
        return json.dumps({"success": False, "message": f"Unknown action: '{action}'"})


@app.post('/api/queue/clear-history')
def api_clear_history():
    """Clear past print history."""
    response.content_type = 'application/json'
    queue_mgr.clear_history()
    return json.dumps({"success": True, "message": "History cleared."})


@app.post('/api/printer/send-gcode')
def api_send_gcode():
    """Send manual G-code command in terminal."""
    response.content_type = 'application/json'
    try:
        data = request.json or {}
    except Exception:
        data = {}
    cmd = data.get("gcode", "").strip()
    if not cmd:
        return json.dumps({"response": "No command entered."})

    res = streamer.send_manual_gcode(cmd)
    return json.dumps({"response": res})


@app.post('/api/printer/connect')
def api_connect():
    """Trigger reconnect to the virtual PTY port."""
    response.content_type = 'application/json'
    connected = streamer.connect()
    return json.dumps({
        "success": connected,
        "message": f"Connected to {streamer.port}" if connected else f"Failed to connect to {streamer.port}"
    })


def main():
    global streamer, queue_mgr

    parser = argparse.ArgumentParser(description="FlashForge Creator Pro Web Queue Server")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host IP (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--pty", default="/tmp/ffcp-pty", help="Virtual serial PTY path (default: /tmp/ffcp-pty)")
    parser.add_argument("--baudrate", type=int, default=115200, help="PTY baudrate (default: 115200)")
    parser.add_argument("--spool", default="/var/spool/ffcp", help="Spool directory (default: /var/spool/ffcp)")
    parser.add_argument("--mock", action="store_true", help="Force mock mode for local testing without printer")
    args = parser.parse_args()

    # Determine if mock mode is required (e.g. running on non-Linux or without virtual port)
    is_mock = args.mock
    if not is_mock and not os.path.exists(args.pty) and sys.platform.startswith("win"):
        logger.info("Running on Windows host without %s: enabling mock mode for testing.", args.pty)
        is_mock = True

    logger.info("Initializing FlashForge Creator Pro Web Server...")
    logger.info("  Virtual PTY Port: %s (Mock: %s)", args.pty, is_mock)
    logger.info("  Spool Directory: %s", args.spool)
    logger.info("  Listening on: http://%s:%d", args.host, args.port)

    streamer = GcodeStreamer(port=args.pty, baudrate=args.baudrate, mock_mode=is_mock)
    queue_mgr = QueueManager(streamer=streamer, spool_dir=args.spool)

    run(app, host=args.host, port=args.port, quiet=False)


if __name__ == '__main__':
    main()
