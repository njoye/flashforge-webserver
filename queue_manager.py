#!/usr/bin/env python3
"""
queue_manager.py - Persistent print queue manager with strict Bed-Clearance safety gating.
Designed for FlashForge Creator Pro on Raspberry Pi Zero W.
State persisted to /var/spool/ffcp/queue.json (with automatic local fallback).
"""

import os
import json
import time
import uuid
import logging
import threading
from typing import Dict, Any, List, Optional
from streamer import GcodeStreamer, STATE_IDLE, STATE_AWAITING_BED_CLEARANCE, STATE_PRINTING

logger = logging.getLogger("queue_manager")


class QueueManager:
    def __init__(self, streamer: GcodeStreamer, spool_dir: str = "/var/spool/ffcp"):
        self.streamer = streamer
        self.spool_dir = spool_dir
        self.queue_dir = os.path.join(self.spool_dir, "queue")
        self.state_file = os.path.join(self.spool_dir, "queue.json")
        self._lock = threading.Lock()

        # Ensure directories exist with fallback for local Windows dev
        self._init_spool_dirs()

        # Queue data structure
        self.pending_jobs: List[Dict[str, Any]] = []
        self.history_jobs: List[Dict[str, Any]] = []
        self.active_job: Optional[Dict[str, Any]] = None

        # Load persisted queue
        self._load_state()

        # Wire up streamer callbacks
        self.streamer.on_job_completed = self._on_streamer_job_completed
        self.streamer.on_job_cancelled = self._on_streamer_job_cancelled

    def _init_spool_dirs(self):
        """Ensure spool directory exists, fallback to current directory if permission denied."""
        try:
            os.makedirs(self.queue_dir, exist_ok=True)
        except OSError:
            # Fallback to local ./spool/ffcp
            fallback_base = os.path.abspath("./spool/ffcp")
            logger.warning("Could not access %s; falling back to %s", self.spool_dir, fallback_base)
            self.spool_dir = fallback_base
            self.queue_dir = os.path.join(self.spool_dir, "queue")
            self.state_file = os.path.join(self.spool_dir, "queue.json")
            os.makedirs(self.queue_dir, exist_ok=True)

    def _load_state(self):
        """Load queue and history from JSON state file."""
        with self._lock:
            if os.path.isfile(self.state_file):
                try:
                    with open(self.state_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        self.pending_jobs = data.get("pending", [])
                        self.history_jobs = data.get("history", [])
                        logger.info("Loaded queue: %d pending, %d history", len(self.pending_jobs), len(self.history_jobs))
                except Exception as e:
                    logger.error("Failed to load queue state from %s: %s", self.state_file, e)
                    self.pending_jobs = []
                    self.history_jobs = []

    def _save_state(self):
        """Persist current queue and recent history to JSON."""
        # Note: caller should hold self._lock
        data = {
            "pending": self.pending_jobs,
            "history": self.history_jobs[-50:],  # keep last 50 entries
            "updated_at": time.time()
        }
        try:
            temp_file = self.state_file + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.state_file)
        except Exception as e:
            logger.error("Failed to save queue state to %s: %s", self.state_file, e)

    def add_job(self, source_filepath: str, original_filename: str, owner: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add a new G-code job to the queue.
        If the printer is currently Idle (and NOT awaiting bed clearance),
        the job will be automatically dispatched to print.
        """
        job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        safe_name = "".join(c for c in original_filename if c.isalnum() or c in "._- ")
        stored_filename = f"{job_id}_{safe_name}"
        dest_filepath = os.path.join(self.queue_dir, stored_filename)

        # Copy or move source file to spool directory if needed
        if os.path.abspath(source_filepath) != os.path.abspath(dest_filepath):
            import shutil
            shutil.copy2(source_filepath, dest_filepath)

        job = {
            "id": job_id,
            "filename": original_filename,
            "filepath": dest_filepath,
            "owner": owner.strip() or "Operator",
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
            "tool": metadata.get("tool", "Right (T0)"),
            "tool_code": metadata.get("tool_code", "T0"),
            "target_temps": metadata.get("target_temps", {}),
            "estimated_time": metadata.get("estimated_time", "Unknown"),
            "estimated_seconds": metadata.get("estimated_seconds"),
            "total_lines": metadata.get("total_lines", 0),
            "file_size_bytes": metadata.get("file_size_bytes", os.path.getsize(dest_filepath)),
            "slicer": metadata.get("slicer", "Unknown"),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None
        }

        should_dispatch = False
        with self._lock:
            # Check if printer is completely idle and ready for immediate printing
            if self.streamer.state == STATE_IDLE and not self.active_job and len(self.pending_jobs) == 0:
                should_dispatch = True
                self.active_job = job
                job["status"] = "printing"
                job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                self.pending_jobs.append(job)

            self._save_state()

        if should_dispatch:
            logger.info("Printer is Idle: auto-dispatching new job %s (%s)", job_id, original_filename)
            self._dispatch_to_streamer(job)
        else:
            logger.info("Added job %s (%s) to queue at position %d", job_id, original_filename, len(self.pending_jobs))

        return job

    def _dispatch_to_streamer(self, job: Dict[str, Any]):
        """Hand off job to streamer engine."""
        success = self.streamer.start_print(job["filepath"], job)
        if not success:
            logger.error("Failed to start print on streamer for job %s", job["id"])
            with self._lock:
                job["status"] = "failed"
                job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.history_jobs.append(job)
                self.active_job = None
                self._save_state()

    def _on_streamer_job_completed(self, job_info: Dict[str, Any]):
        """
        Called when streamer finishes streaming the file.
        Transitions the job to 'completed' in history.
        NOTE: Does NOT auto-dispatch the next job, because streamer is now
        in STATE_AWAITING_BED_CLEARANCE. Next job will dispatch when bed is cleared.
        """
        with self._lock:
            if self.active_job and self.active_job["id"] == job_info["id"]:
                finished_job = self.active_job
                finished_job["status"] = "completed"
                finished_job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if self.streamer.start_time and self.streamer.end_time:
                    finished_job["duration_seconds"] = int(self.streamer.end_time - self.streamer.start_time)
                self.history_jobs.append(finished_job)
                self.active_job = None
                self._save_state()
                logger.info("Job %s marked completed. Printer awaiting bed clearance.", finished_job["id"])

    def _on_streamer_job_cancelled(self, job_info: Dict[str, Any]):
        """Called when a print is aborted/cancelled."""
        with self._lock:
            if self.active_job and self.active_job["id"] == job_info["id"]:
                cancelled_job = self.active_job
                cancelled_job["status"] = "cancelled"
                cancelled_job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                if self.streamer.start_time:
                    cancelled_job["duration_seconds"] = int(time.time() - self.streamer.start_time)
                self.history_jobs.append(cancelled_job)
                self.active_job = None
                self._save_state()
                logger.info("Job %s marked cancelled.", cancelled_job["id"])

    def bed_cleared(self) -> Dict[str, Any]:
        """
        CRITICAL SAFETY GATE HANDLER:
        Operator confirmed bed is cleared.
        1. Calls streamer.clear_bed() -> transitions printer state to Idle.
        2. If pending jobs exist, dispatches the next job immediately.
        """
        cleared = self.streamer.clear_bed()
        if not cleared:
            return {"success": False, "message": "Printer was not awaiting bed clearance."}

        next_job = None
        with self._lock:
            if self.pending_jobs:
                next_job = self.pending_jobs.pop(0)
                self.active_job = next_job
                next_job["status"] = "printing"
                next_job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_state()

        if next_job:
            logger.info("Bed cleared! Dispatching next pending job: %s (%s)", next_job["id"], next_job["filename"])
            self._dispatch_to_streamer(next_job)
            return {
                "success": True,
                "message": f"Bed cleared. Next job started: {next_job['filename']}",
                "next_job": next_job
            }
        else:
            logger.info("Bed cleared! No pending jobs in queue. Printer is Idle.")
            return {"success": True, "message": "Bed cleared. Printer is now Idle.", "next_job": None}

    def cancel_active_job(self) -> bool:
        """Emergency abort active print."""
        if self.streamer.state in (STATE_PRINTING, "Heating"):
            self.streamer.cancel_print()
            return True
        return False

    def remove_job(self, job_id: str) -> bool:
        """Remove a pending job from the queue and remove its spool file."""
        with self._lock:
            target_idx = -1
            target_job = None
            for idx, job in enumerate(self.pending_jobs):
                if job["id"] == job_id:
                    target_idx = idx
                    target_job = job
                    break

            if target_idx >= 0:
                self.pending_jobs.pop(target_idx)
                self._save_state()
                # Clean up file on disk
                if target_job and os.path.isfile(target_job.get("filepath", "")):
                    try:
                        os.remove(target_job["filepath"])
                    except OSError:
                        pass
                logger.info("Removed job %s from pending queue", job_id)
                return True
        return False

    def move_job(self, job_id: str, direction: str) -> bool:
        """Move a pending job up or down in the queue."""
        with self._lock:
            idx = next((i for i, j in enumerate(self.pending_jobs) if j["id"] == job_id), -1)
            if idx < 0:
                return False

            if direction == "up" and idx > 0:
                self.pending_jobs[idx], self.pending_jobs[idx - 1] = self.pending_jobs[idx - 1], self.pending_jobs[idx]
                self._save_state()
                return True
            elif direction == "down" and idx < len(self.pending_jobs) - 1:
                self.pending_jobs[idx], self.pending_jobs[idx + 1] = self.pending_jobs[idx + 1], self.pending_jobs[idx]
                self._save_state()
                return True
        return False

    def start_job_now(self, job_id: str) -> bool:
        """Start a specific pending job immediately (only valid if printer is Idle)."""
        if self.streamer.state != STATE_IDLE or self.active_job:
            return False

        job_to_start = None
        with self._lock:
            idx = next((i for i, j in enumerate(self.pending_jobs) if j["id"] == job_id), -1)
            if idx >= 0:
                job_to_start = self.pending_jobs.pop(idx)
                self.active_job = job_to_start
                job_to_start["status"] = "printing"
                job_to_start["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save_state()

        if job_to_start:
            self._dispatch_to_streamer(job_to_start)
            return True
        return False

    def clear_history(self):
        """Clear completed job history."""
        with self._lock:
            self.history_jobs.clear()
            self._save_state()

    def get_queue_data(self) -> Dict[str, Any]:
        """Return full queue dictionary for API."""
        with self._lock:
            return {
                "active": self.active_job,
                "pending": list(self.pending_jobs),
                "history": list(self.history_jobs),
                "pending_count": len(self.pending_jobs),
                "history_count": len(self.history_jobs)
            }
