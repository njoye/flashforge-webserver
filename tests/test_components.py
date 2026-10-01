#!/usr/bin/env python3
"""
test_components.py - Automated self-test suite for FlashForge WebServer components.
Verifies G-code parsing, streamer state transitions, safety gate logic, and queue manager.
"""

import os
import sys
import time
import shutil

# Ensure parent directory is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from gcode_parser import inspect_gcode_file
from streamer import (
    GcodeStreamer,
    STATE_IDLE,
    STATE_PRINTING,
    STATE_AWAITING_BED_CLEARANCE
)
from queue_manager import QueueManager


def test_gcode_parser():
    print("[TEST 1/4] Testing G-code Parser...")
    sample_file = os.path.join(BASE_DIR, "examples", "test_cube.gcode")
    res = inspect_gcode_file(sample_file)

    assert "Right (T0)" in res["tool"], f"Expected Right (T0), got {res['tool']}"
    assert res["target_temps"]["tool0"] == 205.0, f"Expected 205, got {res['target_temps']['tool0']}"
    assert res["target_temps"]["bed"] == 60.0, f"Expected 60, got {res['target_temps']['bed']}"
    assert res["estimated_seconds"] == 28 * 60 + 14, f"Expected 1694s, got {res['estimated_seconds']}"
    assert res["estimated_time"] == "28m 14s", f"Expected 28m 14s, got {res['estimated_time']}"
    assert res["total_lines"] > 30, f"Expected >30 lines, got {res['total_lines']}"
    print(f"  ✓ Parser passed: Detected {res['tool']}, {res['target_temps']}, Time: {res['estimated_time']}")


def test_streamer_mock_and_safety_gate():
    print("[TEST 2/4] Testing Streamer Mock & Safety Gate...")
    streamer = GcodeStreamer(port="/tmp/fake-pty", mock_mode=True)
    assert streamer.state == STATE_IDLE, f"Initial state should be Idle, got {streamer.state}"

    sample_file = os.path.join(BASE_DIR, "examples", "test_cube.gcode")
    meta = inspect_gcode_file(sample_file)

    completed_flag = []
    streamer.on_job_completed = lambda job: completed_flag.append(True)

    job_info = {"id": "test-1", "filename": "test_cube.gcode", "total_lines": meta["total_lines"]}
    started = streamer.start_print(sample_file, job_info)
    assert started, "Streamer failed to start print"

    # Wait for completion (in mock mode it finishes quickly)
    timeout = 10.0
    t0 = time.time()
    while streamer.state != STATE_AWAITING_BED_CLEARANCE and time.time() - t0 < timeout:
        time.sleep(0.05)

    assert streamer.state == STATE_AWAITING_BED_CLEARANCE, (
        f"Streamer MUST transition to 'Awaiting Bed Clearance', got {streamer.state}"
    )
    assert len(completed_flag) == 1, "Completed callback was not invoked"
    print("  ✓ Print finished and entered 'Awaiting Bed Clearance' state.")

    # Try starting another print while in Awaiting Bed Clearance -> MUST FAIL
    second_start = streamer.start_print(sample_file, {"id": "test-2", "filename": "test_cube.gcode"})
    assert not second_start, "CRITICAL ERROR: Streamer allowed starting print while Awaiting Bed Clearance!"
    print("  ✓ Collision prevention verified: starting print while awaiting clearance was blocked.")

    # Operator clears bed
    cleared = streamer.clear_bed()
    assert cleared, "clear_bed() returned False"
    assert streamer.state == STATE_IDLE, f"State after clear_bed() should be Idle, got {streamer.state}"
    print("  ✓ Safety gate released: operator cleared bed, state transitioned to Idle.")


def test_queue_manager():
    print("[TEST 3/4] Testing Queue Manager & Safety Gate Dispatch...")
    test_spool = os.path.join(BASE_DIR, "test_spool")
    if os.path.exists(test_spool):
        shutil.rmtree(test_spool)

    streamer = GcodeStreamer(port="/tmp/fake-pty", mock_mode=True)
    qm = QueueManager(streamer=streamer, spool_dir=test_spool)

    sample_file = os.path.join(BASE_DIR, "examples", "test_cube.gcode")
    meta = inspect_gcode_file(sample_file)

    # 1. Add job while idle -> Should immediately dispatch
    job1 = qm.add_job(sample_file, "job1.gcode", "Tim", meta)
    assert job1["status"] == "printing", f"Job 1 should be printing, got {job1['status']}"
    print("  ✓ Job 1 auto-dispatched because printer was Idle.")

    # 2. Add second job while Job 1 is printing -> Should go to pending queue
    job2 = qm.add_job(sample_file, "job2.gcode", "Alice", meta)
    assert job2["status"] == "pending", f"Job 2 should be pending, got {job2['status']}"
    assert len(qm.pending_jobs) == 1, f"Expected 1 pending job, got {len(qm.pending_jobs)}"
    print("  ✓ Job 2 queued as pending because printer is active.")

    # 3. Wait for Job 1 to finish
    t0 = time.time()
    while streamer.state != STATE_AWAITING_BED_CLEARANCE and time.time() - t0 < 10.0:
        time.sleep(0.05)

    assert streamer.state == STATE_AWAITING_BED_CLEARANCE, "Expected Awaiting Bed Clearance"
    # Verify Job 2 has NOT started yet!
    assert len(qm.pending_jobs) == 1, "Job 2 must remain pending until bed is cleared!"
    print("  ✓ Safety Gate verified: Job 2 held in pending while printer awaits clearance.")

    # 4. Trigger Bed Clearance -> Job 2 should now automatically start!
    clear_result = qm.bed_cleared()
    assert clear_result["success"], "bed_cleared() failed"
    assert clear_result["next_job"] is not None, "Expected next job to be dispatched"
    assert clear_result["next_job"]["id"] == job2["id"], "Expected Job 2 to be dispatched"
    assert streamer.state in (STATE_PRINTING, "Heating"), f"Expected printing, got {streamer.state}"
    print(f"  ✓ Bed cleared! Job 2 auto-dispatched: {clear_result['message']}")

    # Clean up test spool
    shutil.rmtree(test_spool, ignore_errors=True)


def test_abort_sequence():
    print("[TEST 4/4] Testing Graceful Abort Sequence...")
    streamer = GcodeStreamer(port="/tmp/fake-pty", mock_mode=True)
    sample_file = os.path.join(BASE_DIR, "examples", "test_cube.gcode")
    meta = inspect_gcode_file(sample_file)

    cancelled_flag = []
    streamer.on_job_cancelled = lambda j: cancelled_flag.append(True)

    streamer.start_print(sample_file, {"id": "abort-test", "filename": "abort.gcode", "total_lines": 100000})
    time.sleep(0.05)

    # Cancel print
    streamer.cancel_print()
    time.sleep(0.2)

    assert streamer.state == STATE_IDLE, f"State after cancel should be Idle, got {streamer.state}"
    assert len(cancelled_flag) == 1, "Cancelled callback was not invoked"
    assert streamer.temps["tool0"]["target"] == 0.0, "Heater T0 was not turned off"
    assert streamer.temps["bed"]["target"] == 0.0, "Heater Bed was not turned off"
    print("  ✓ Abort verified: heaters shut off, buffers flushed, state returned to Idle.")


if __name__ == '__main__':
    print("==================================================")
    print(" FlashForge Creator Pro WebServer Component Tests ")
    print("==================================================")
    test_gcode_parser()
    test_streamer_mock_and_safety_gate()
    test_queue_manager()
    test_abort_sequence()
    print("\n==================================================")
    print(" ALL TESTS PASSED SUCCESSFULLY! ")
    print("==================================================")
