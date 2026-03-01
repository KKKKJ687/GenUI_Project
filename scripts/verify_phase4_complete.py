#!/usr/bin/env python3
"""
Phase 4 Deep Defense & Monitoring System Acceptance Script
Verifies: Unit-aware interception, multi-protocol data conversion, runtime log integrity.
"""
import unittest
import json
import os
import shutil
import sys
from pathlib import Path

# Ensure project root is in sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.modules.runtime.runtime_guard import check_command_safe
from src.models.protocol_interaction import convert_protocol_data
from src.core.status_reporter import generate_report

class TestPhase4FinalAcceptance(unittest.TestCase):
    
    def setUp(self):
        self.test_run_dir = Path("test_runs/p4_verify")
        if self.test_run_dir.exists():
            shutil.rmtree(self.test_run_dir)
        self.test_run_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.test_run_dir.exists():
            shutil.rmtree(self.test_run_dir)

    def test_01_unit_aware_guard(self):
        """Acceptance Standard 1: Runtime Guard must recognize 5000mV == 5V and correctly intercept OOB commands"""
        print("\n[Test 1] Runtime Unit-Aware Guard...")
        
        # Hardware Constraint: Max 3.3V
        constraint = {"min": 0, "max": 3.3, "unit": "V"}
        
        # Command A: 3000 mV (Should pass, as 3V <= 3.3V)
        self.assertTrue(check_command_safe(3000, "mV", constraint), "Failed to accept safe value (3000mV <= 3.3V)")
        
        # Command B: 0.005 kV (Should intercept, as 5V > 3.3V)
        self.assertFalse(check_command_safe(0.005, "kV", constraint), "Failed to intercept unsafe value (0.005kV > 3.3V)")
        print("  -> PASSED: Guard understands physical magnitudes.")

    def test_02_protocol_interop_robustness(self):
        """Acceptance Standard 2: Cross-protocol data exchange no longer relies on fragile string splitting"""
        print("\n[Test 2] Robust Protocol Mapping...")
        
        raw_mqtt_data = {"topic": "factory/temp", "value": 25.5}
        # Define mapping relationship, not relying on code split
        mapping = {"modbus_address": "40001", "target_val": "value"}
        
        # Note: convert_protocol_data signature in User Request was (data, mapping). 
        # But in my implementation I kept (data, mapping) as requested.
        result = convert_protocol_data(raw_mqtt_data, mapping)
        self.assertEqual(result.get("target_val"), 25.5)
        print("  -> PASSED: Protocol mapping is configuration-driven.")

    def test_03_monitor_evidence_chain(self):
        """Acceptance Standard 3: Reports must be traceable to physical datasheet evidence"""
        print("\n[Test 3] Runtime Evidence Traceability...")
        
        # Simulate a runtime log containing rule_source
        
        mock_event = {
            "ts_utc": "2026-01-29T00:00:00Z",
            "event_type": "command_guard",
            "payload": {
                "command": {"target": "motor.speed", "val": 100},
                "allowed": False,
                "reason": "Exceeds Max Voltage",
                "rule_source": "Datasheet_Motor_v2.pdf, Page 12"
            }
        }
        
        with open(self.test_run_dir / "runtime_events.jsonl", "w") as f:
            f.write(json.dumps(mock_event) + "\n")
            
        report = generate_report(self.test_run_dir)
        # Verify summary report captures detailed cause of safety violation
        # status_reporter.py logic: iterates 'commands', counts guard_denied if guard.allowed is False
        self.assertEqual(report["summary"].get("guard_denied"), 1, "Report failed to count denied command")
        
        # Verify evidence chain in commands list
        commands = report["commands"]["events"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["guard"]["rule_source"], "Datasheet_Motor_v2.pdf, Page 12", "Traceability evidence missing")
        
        print("  -> PASSED: Report successfully captures evidence context.")

if __name__ == "__main__":
    print("=== Phase 4 Engineering & Academic Acceptance ===")
    unittest.main()
