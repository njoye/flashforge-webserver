#!/usr/bin/env python3
"""
streamer.py - Ultra-lightweight serial streaming engine for FlashForge Creator Pro.
Optimized for Raspberry Pi Zero W (ARMv6l, 1 GHz, 512 MB RAM).
Communicates with the GPX daemon via the virtual PTY port (/tmp/ffcp-pty).

Key features:
1. Ping-Pong Flow Control: sends G-code line-by-line and awaits 'ok' ACK.
2. Background Temperature Polling: periodically queries M105 and parses T0, T1, and Bed temperatures.
3. Safe Graceful Abort: halts extrusion, flushes buffers, shuts down heaters (M104 T0 S0, M104 T1 S0, M140 S0),
   drops bed, parks toolhead, and disables steppers.
4. Physical Safety Gate: transitions to 'Awaiting Bed Clearance' after every completed job until cleared by operator.
5. Mock Mode Support: allows full end-to-end testing when hardware/PTY is absent.
"""

import os
import re
import sys
import time
import threading
import logging
from typing import Optional, Dict, Any, Callable

# Configure concise logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("streamer")

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    logger.warning("pyserial not installed in current environment; PTY streaming will fallback to mock mode.")

# Regex patterns for parsing GPX / RepRap temperature responses
# e.g.: "ok T:210.0 /210.0 B:60.0 /60.0 T0:210.0 /210.0 T1:25.0 /0.0 @:0 B@:0"
RE_TEMP_T = re.compile(r'\bT:(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?', re.IGNORECASE)
RE_TEMP_T0 = re.compile(r'\bT0:(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?', re.IGNORECASE)
RE_TEMP_T1 = re.compile(r'\bT1:(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?', re.IGNORECASE)
RE_TEMP_B = re.compile(r'\bB:(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?', re.IGNORECASE)

# States
STATE_IDLE = "Idle"
STATE_HEATING = "Heating"
STATE_PRINTING = "Printing"
STATE_AWAITING_BED_CLEARANCE = "Awaiting Bed Clearance"
STATE_CANCELLED = "Cancelled"
STATE_ERROR = "Error"
STATE_OFFLINE = "Offline"


class GcodeStreamer:
    """
    Manages connection to the GPX virtual PTY port and executes ping-pong streaming
    of G-code files to the FlashForge Creator Pro.
    """

    def __init__(self, port: str = "/tmp/ffcp-pty", baudrate: int = 115200, mock_mode: bool = False):
        self.port = port
        self.baudrate = baudrate
        self.mock_mode = mock_mode or not SERIAL_AVAILABLE

        self.ser: Optional[Any] = None
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()
        self._state = STATE_IDLE if self.mock_mode else STATE_OFFLINE
        self._status_message = "Ready" if self.mock_mode else "Printer offline (waiting for connection)"

        # Temperatures (None indicates disconnected / no reading received yet)
        self.temps = {
            "tool0": {"actual": 25.0 if self.mock_mode else None, "target": 0.0},
            "tool1": {"actual": 25.0 if self.mock_mode else None, "target": 0.0},
            "bed":   {"actual": 24.0 if self.mock_mode else None, "target": 0.0}
        }

        # Active Job State
        self.active_job: Optional[Dict[str, Any]] = None
        self.lines_sent = 0
        self.total_lines = 0
        self.bytes_sent = 0
        self.total_bytes = 0
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

        # Control flags
        self._abort_requested = threading.Event()
        self._pause_requested = threading.Event()
        self._is_streaming = False

        # Threads
        self._stream_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_poll = threading.Event()

        # Callbacks
        self.on_state_change: Optional[Callable[[str], None]] = None
        self.on_job_completed: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_job_cancelled: Optional[Callable[[Dict[str, Any]], None]] = None

        # Try initial connection
        if not self.mock_mode:
            self.connect()

        # Start periodic status polling
        self._start_poll_thread()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _set_state(self, new_state: str, message: str = ""):
        with self._lock:
            old_state = self._state
            self._state = new_state
            if message:
                self._status_message = message
        if old_state != new_state:
            logger.info("State transition: %s -> %s (%s)", old_state, new_state, message)
            if self.on_state_change:
                try:
                    self.on_state_change(new_state)
                except Exception as e:
                    logger.error("Error in on_state_change callback: %s", e)

    def connect(self) -> bool:
        """Connect to the virtual PTY port created by GPX."""
        if self.mock_mode:
            self._set_state(STATE_IDLE, "Mock mode active")
            return True

        with self._lock:
            if self.ser and self.ser.is_open:
                return True

            if not os.path.exists(self.port):
                logger.warning("Virtual port %s does not exist yet. Is gpx-daemon running?", self.port)
                self._state = STATE_OFFLINE
                self._status_message = f"Port {self.port} not found (gpx-daemon waiting)"
                return False

            try:
                # Open with standard timeout (0.5s)
                self.ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=0.5, write_timeout=1.0)
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                logger.info("Connected to virtual PTY port %s", self.port)
                self._state = STATE_IDLE
                self._status_message = "Connected to GPX bridge"
                return True
            except Exception as e:
                logger.error("Failed to open virtual port %s: %s", self.port, e)
                self.ser = None
                self._state = STATE_OFFLINE
                self._status_message = f"Connection error: {e}"
                return False

    def disconnect(self):
        """Disconnect from serial port."""
        with self._lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            if not self.mock_mode:
                self._state = STATE_OFFLINE
                self._status_message = "Disconnected"
                for k in self.temps:
                    self.temps[k]["actual"] = None

    def _start_poll_thread(self):
        """Start low-frequency temperature polling thread."""
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, name="TempPoller", daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        """Poll M105 temperature query periodically every 2.5 seconds."""
        while not self._stop_poll.is_set():
            time.sleep(2.5)

            with self._lock:
                current_st = self._state
                is_streaming = self._is_streaming

            # Auto-reconnect if offline and GPX virtual port becomes available
            if current_st == STATE_OFFLINE and os.path.exists(self.port):
                logger.info("Virtual port %s appeared. Auto-connecting...", self.port)
                if self.connect():
                    with self._lock:
                        current_st = self._state

            # Only poll in Idle or Awaiting Bed Clearance to avoid interfering with streaming
            # (During streaming, M105 is interleaved inside the ping-pong loop)
            if not is_streaming and current_st in (STATE_IDLE, STATE_HEATING, STATE_AWAITING_BED_CLEARANCE):
                self._send_status_query()

    def _send_status_query(self):
        """Send M105 and parse the response."""
        if self.mock_mode:
            # Simulate slight temperature jitter or cool-down
            with self._lock:
                for k in ["tool0", "tool1", "bed"]:
                    tgt = self.temps[k]["target"] or 0.0
                    act = self.temps[k]["actual"] or 25.0
                    if tgt > 0:
                        if act < tgt:
                            self.temps[k]["actual"] = min(tgt, act + 5.0)
                    else:
                        if act > 25.0:
                            self.temps[k]["actual"] = max(24.0, act - 2.0)
            return

        # Attempt to acquire serial port exclusively without blocking callers
        if not self._serial_lock.acquire(blocking=False):
            # Serial bus is busy with another transaction (e.g. manual move or streaming)
            return

        try:
            with self._lock:
                if not self.ser or not self.ser.is_open:
                    return
                ser = self.ser

            ser.write(b"M105\n")
            # Read response lines until ok or timeout (without holding state lock across I/O)
            start_t = time.time()
            while time.time() - start_t < 1.0:
                line = ser.readline().decode('ascii', errors='ignore').strip()
                if line:
                    self._parse_temperature_string(line)
                    if line.startswith("ok") or "ok" in line:
                        break
        except Exception as e:
            logger.debug("M105 poll error: %s", e)
        finally:
            self._serial_lock.release()

    def _parse_temperature_string(self, text: str):
        """Extract T0, T1, and B readings from RepRap/GPX response string."""
        with self._lock:
            # Match Bed
            m_b = RE_TEMP_B.search(text)
            if m_b:
                self.temps["bed"]["actual"] = float(m_b.group(1))
                if m_b.group(2) is not None:
                    self.temps["bed"]["target"] = float(m_b.group(2))

            # Match T0
            m_t0 = RE_TEMP_T0.search(text)
            if m_t0:
                self.temps["tool0"]["actual"] = float(m_t0.group(1))
                if m_t0.group(2) is not None:
                    self.temps["tool0"]["target"] = float(m_t0.group(2))

            # Match T1
            m_t1 = RE_TEMP_T1.search(text)
            if m_t1:
                self.temps["tool1"]["actual"] = float(m_t1.group(1))
                if m_t1.group(2) is not None:
                    self.temps["tool1"]["target"] = float(m_t1.group(2))

            # Fallback to single T if T0 wasn't explicitly named
            if not m_t0:
                m_t = RE_TEMP_T.search(text)
                if m_t:
                    self.temps["tool0"]["actual"] = float(m_t.group(1))
                    if m_t.group(2) is not None:
                        self.temps["tool0"]["target"] = float(m_t.group(2))

    def start_print(self, filepath: str, job_info: Dict[str, Any]) -> bool:
        """
        Initiate streaming print of the given G-code file.
        Fails if currently printing or awaiting bed clearance.
        """
        with self._lock:
            if self._is_streaming or self._state in (STATE_PRINTING, STATE_HEATING, STATE_AWAITING_BED_CLEARANCE):
                logger.warning("Cannot start print: printer busy or awaiting clearance (state=%s)", self._state)
                return False

            if not os.path.isfile(filepath):
                logger.error("Print file not found: %s", filepath)
                return False

            if not self.mock_mode:
                if not self.ser or not self.ser.is_open:
                    if not self.connect():
                        return False

            self.active_job = dict(job_info)
            self.active_job["filepath"] = filepath
            self.lines_sent = 0
            self.total_lines = job_info.get("total_lines", 0)
            self.bytes_sent = 0
            self.total_bytes = os.path.getsize(filepath)
            self.start_time = time.time()
            self.end_time = None
            self._abort_requested.clear()
            self._pause_requested.clear()
            self._is_streaming = True

        self._set_state(STATE_PRINTING, f"Starting print: {job_info.get('filename')}")

        self._stream_thread = threading.Thread(
            target=self._stream_file_worker,
            args=(filepath,),
            name="GcodeStreamer",
            daemon=True
        )
        self._stream_thread.start()
        return True

    def _stream_file_worker(self, filepath: str):
        """Worker thread executing ping-pong streaming loop."""
        logger.info("Streaming started for %s", filepath)
        last_m105_time = time.time()
        completed_successfully = False

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f):
                    if self._abort_requested.is_set():
                        logger.info("Print abort requested, exiting stream loop at line %d", line_num)
                        break

                    raw = line.strip()
                    # Strip comments for lean serial transmission
                    clean = re.sub(r';.*$', '', raw).strip()
                    clean = re.sub(r'\(.*?\)', '', clean).strip()

                    line_bytes = len(line.encode('utf-8'))
                    self.bytes_sent += line_bytes

                    if not clean:
                        # Skip blank lines and pure comments locally to save serial round-trips
                        self.lines_sent += 1
                        continue

                    # Check for heating commands to transition status appropriately
                    if re.match(r'^(?:M109|M190)\b', clean, re.IGNORECASE):
                        self._set_state(STATE_HEATING, f"Heating tool/bed (line {line_num+1})")
                    elif self.state == STATE_HEATING and re.match(r'^G1\b', clean, re.IGNORECASE):
                        self._set_state(STATE_PRINTING, "Extruding")

                    # Interleave M105 temperature query every ~3 seconds
                    now = time.time()
                    if now - last_m105_time > 3.0:
                        self._send_status_query()
                        last_m105_time = now

                    # Send line via ping-pong
                    success = self._send_line_ping_pong(clean)
                    if not success:
                        if self._abort_requested.is_set():
                            break
                        logger.error("Failed to receive ACK for line %d: '%s'", line_num+1, clean)
                        self._set_state(STATE_ERROR, f"Communication failure on line {line_num+1}")
                        return

                    self.lines_sent += 1

                if not self._abort_requested.is_set():
                    completed_successfully = True

        except Exception as e:
            logger.exception("Unexpected error during file streaming: %s", e)
            self._set_state(STATE_ERROR, f"Stream error: {e}")
            return
        finally:
            self._is_streaming = False
            self.end_time = time.time()

            if completed_successfully:
                logger.info("Print completed successfully! Setting state to Awaiting Bed Clearance.")
                # Safety Gate: transition to Awaiting Bed Clearance
                self._set_state(
                    STATE_AWAITING_BED_CLEARANCE,
                    "Print finished. Operator must clear bed before next print."
                )
                if self.on_job_completed and self.active_job:
                    try:
                        self.on_job_completed(self.active_job)
                    except Exception as e:
                        logger.error("Error in on_job_completed callback: %s", e)
            elif self._abort_requested.is_set():
                logger.info("Executing safe emergency cancel sequence.")
                self._execute_safe_cancel_sequence()
                cancelled_job = self.active_job
                with self._lock:
                    self.active_job = None
                    self.lines_sent = 0
                    self.total_lines = 0
                    self.bytes_sent = 0
                    self.total_bytes = 0
                    self.start_time = None
                    self.end_time = None
                    self._abort_requested.clear()
                self._set_state(STATE_IDLE, "Print cancelled by operator")
                if self.on_job_cancelled and cancelled_job:
                    try:
                        self.on_job_cancelled(cancelled_job)
                    except Exception as e:
                        logger.error("Error in on_job_cancelled callback: %s", e)

    def _send_line_ping_pong(self, clean_line: str) -> bool:
        """
        Send a single ASCII G-code command and block until 'ok' ACK is received.
        Implements strict ping-pong flow control.
        """
        if self.mock_mode:
            # Simulate realistic timing in mock mode
            time.sleep(0.002)
            # Detect target temp commands to simulate heating
            m_s = re.search(r'\bS(\d+)', clean_line)
            if m_s:
                val = float(m_s.group(1))
                if clean_line.startswith(("M104", "M109")):
                    self.temps["tool0"]["target"] = val
                    self.temps["tool0"]["actual"] = val
                elif clean_line.startswith(("M140", "M190")):
                    self.temps["bed"]["target"] = val
                    self.temps["bed"]["actual"] = val
            return True

        if not self.ser or not self.ser.is_open:
            return False

        with self._serial_lock:
            try:
                cmd = (clean_line + "\n").encode('ascii')
                self.ser.write(cmd)

                # Wait for 'ok' ACK
                # MightyBoard/GPX operations like homing (G28) or heating (M109/M190) can take minutes,
                # so we use a loop with short readline timeouts rather than a fixed tiny timeout.
                start_wait = time.time()
                while True:
                    if self._abort_requested.is_set():
                        return False

                    line = self.ser.readline().decode('ascii', errors='ignore').strip()
                    if line:
                        self._parse_temperature_string(line)
                        # Check for 'ok' ACK
                        if line.startswith("ok") or line.endswith("ok") or "ok" in line.split():
                            return True
                        if "error" in line.lower():
                            logger.warning("Printer reported error: %s", line)

                    # Timeout guard (600s max per command e.g. long bed heatup)
                    if time.time() - start_wait > 600.0:
                        logger.error("Timeout (600s) waiting for ACK to command: %s", clean_line)
                        return False

            except Exception as e:
                logger.error("Serial transmission error for '%s': %s", clean_line, e)
                return False

    def cancel_print(self):
        """Request immediate graceful cancellation of the active print or emergency stop."""
        logger.warning("Emergency abort triggered!")
        self._abort_requested.set()

        if not self._is_streaming and self.state != STATE_PRINTING:
            logger.info("Cancel called while not actively streaming. Executing immediate safe parking.")
            self._execute_safe_cancel_sequence()
            cancelled_job = self.active_job
            with self._lock:
                self.active_job = None
                self.lines_sent = 0
                self.total_lines = 0
                self.bytes_sent = 0
                self.total_bytes = 0
                self.start_time = None
                self.end_time = None
                self._abort_requested.clear()
            self._set_state(STATE_IDLE, "Emergency abort completed")
            if self.on_job_cancelled and cancelled_job:
                try:
                    self.on_job_cancelled(cancelled_job)
                except Exception as e:
                    logger.error("Error in on_job_cancelled callback: %s", e)

    def _execute_safe_cancel_sequence(self):
        """
        Graceful cancel/abort sequence:
        1. Flush buffers
        2. Turn off heaters: M104 T0 S0, M104 T1 S0, M140 S0
        3. Stop extrusion motor: M108
        4. Lower bed: G91 -> G1 Z10 F1000 -> G90
        5. Park toolhead: G28 X Y
        6. Disable steppers: M84
        """
        logger.info("Sending safe abort commands to printer...")
        abort_gcode = [
            "M104 T0 S0",         # Kill right hotend heater
            "M104 T1 S0",         # Kill left hotend heater
            "M140 S0",            # Kill heated bed
            "M108",               # Stop extruder motor
            "G91",                # Relative positioning
            "G1 Z10 F1000",       # Lower bed 10mm away from nozzle
            "G90",                # Absolute positioning
            "G28 X Y",            # Home X and Y to park toolhead safely
            "M84"                 # Disable stepper motors
        ]

        if self.mock_mode:
            self.temps["tool0"]["target"] = 0.0
            self.temps["tool1"]["target"] = 0.0
            self.temps["bed"]["target"] = 0.0
            return

        if self.ser and self.ser.is_open:
            try:
                # Flush serial buffers
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()

                for cmd in abort_gcode:
                    try:
                        self.ser.write((cmd + "\n").encode('ascii'))
                        time.sleep(0.05)
                        # Read response with 1s timeout
                        t0 = time.time()
                        while time.time() - t0 < 1.0:
                            resp = self.ser.readline().decode('ascii', errors='ignore').strip()
                            if "ok" in resp:
                                break
                    except Exception as e:
                        logger.error("Error sending abort command '%s': %s", cmd, e)
            except Exception as e:
                logger.error("Failed during abort sequence: %s", e)

    def clear_bed(self) -> bool:
        """
        Physical/UI Safety Gate:
        Operator confirms that the printed part has been removed and the bed is clear.
        Transitions state from 'Awaiting Bed Clearance' to 'Idle'.
        """
        with self._lock:
            if self._state == STATE_AWAITING_BED_CLEARANCE:
                self._state = STATE_IDLE
                self._status_message = "Bed cleared. Ready for next job."
                self.active_job = None
                logger.info("Bed clearance confirmed by operator. State set to Idle.")
                return True
            else:
                logger.info("clear_bed called while not in Awaiting Bed Clearance state (state=%s)", self._state)
                if self._state in (STATE_IDLE, STATE_CANCELLED):
                    self._state = STATE_IDLE
                    self.active_job = None
                    return True
                return False

    def send_manual_gcode(self, gcode_str: str) -> str:
        """Send a single manual G-code command (only when Idle)."""
        with self._lock:
            if self._state not in (STATE_IDLE, STATE_AWAITING_BED_CLEARANCE):
                return "Error: Printer is busy or printing."

        if self.mock_mode:
            return "ok (mock response)"

        if not self.ser or not self.ser.is_open:
            if not self.connect():
                return "Error: Serial port offline."

        with self._serial_lock:
            try:
                # Flush input buffer before sending new command
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass

                self.ser.write((gcode_str.strip() + "\n").encode('ascii'))
                output = []
                cmd_upper = gcode_str.upper()
                timeout = 45.0 if any(k in cmd_upper for k in ("G28", "M109", "M190")) else 10.0
                start_t = time.time()
                while time.time() - start_t < timeout:
                    line = self.ser.readline().decode('ascii', errors='ignore').strip()
                    if line:
                        output.append(line)
                        self._parse_temperature_string(line)
                        if line.startswith("ok") or line.endswith("ok") or "ok" in line.split():
                            break
                        if "error" in line.lower():
                            break
                return "\n".join(output) if output else "ok"
            except Exception as e:
                return f"Error: {e}"

    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive status dictionary for API and web UI."""
        with self._lock:
            current_st = self._state
            msg = self._status_message
            temps = {
                "tool0": dict(self.temps["tool0"]),
                "tool1": dict(self.temps["tool1"]),
                "bed": dict(self.temps["bed"])
            }
            active = dict(self.active_job) if self.active_job else None
            lines_sent = self.lines_sent
            total_lines = self.total_lines
            bytes_sent = self.bytes_sent
            total_bytes = self.total_bytes
            start_t = self.start_time
            end_t = self.end_time

        # Calculate progress
        pct = 0.0
        if total_lines > 0:
            pct = round((lines_sent / total_lines) * 100.0, 1)
        elif total_bytes > 0:
            pct = round((bytes_sent / total_bytes) * 100.0, 1)
        pct = min(100.0, max(0.0, pct))

        # Calculate elapsed time
        elapsed_sec = 0
        if start_t:
            if end_t:
                elapsed_sec = int(end_t - start_t)
            else:
                elapsed_sec = int(time.time() - start_t)

        return {
            "state": current_st,
            "status_message": msg,
            "connected": bool(self.ser and self.ser.is_open) or self.mock_mode,
            "mock_mode": self.mock_mode,
            "port": self.port,
            "temperatures": temps,
            "active_job": active,
            "progress": {
                "percent": pct,
                "lines_sent": lines_sent,
                "total_lines": total_lines,
                "bytes_sent": bytes_sent,
                "total_bytes": total_bytes,
                "elapsed_seconds": elapsed_sec
            }
        }
