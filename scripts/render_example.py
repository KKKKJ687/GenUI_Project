import sys
import os
import json
import hashlib
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from src.models.schema import (
        HMIPanel,
        SliderWidget,
        SwitchWidget,
        GaugeWidget,
        LayoutItem,
        DataBinding,
        SafetyPolicy,
    )
    from src.modules.renderer.renderer import render_panel
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

def main():
    print("=== Deterministic Renderer Verification ===")

    # 1. Create a Sample DSL Object (Phase 1.1 Output)
    panel = HMIPanel(
        title="Turbine Control Unit A",
        description="Main control interface for gas turbine #1",
        theme="dark",
        widgets=[
            SliderWidget(
                id="throttle_val",
                label="Throttle Position",
                min=0, max=100, step=1, value=15,
                binding=DataBinding(protocol="mqtt", address="turbine/A/throttle", access_mode="rw"),
                safety=SafetyPolicy(max_value=90, unit="%")
            ),
            SwitchWidget(
                id="emergency_stop",
                label="Emergency Override",
                on_label="ACTIVE", off_label="SAFE",
                color_on="red",
                value=False,
                binding=DataBinding(protocol="modbus", address="40001", access_mode="rw")
            ),
            GaugeWidget(
                id="rpm_gauge",
                label="Turbine RPM",
                min=0, max=12000, value=3500,
                thresholds=[8000, 11000],
                binding=DataBinding(protocol="mqtt", address="turbine/A/rpm", access_mode="r"),
                safety=SafetyPolicy(max_value=12000, unit="RPM")
            )
        ],
        layout=[
            LayoutItem(i="throttle_val", x=0, y=0, w=4, h=2),
            LayoutItem(i="rpm_gauge", x=4, y=0, w=4, h=4),
            LayoutItem(i="emergency_stop", x=0, y=2, w=4, h=2)
        ]
    )

    # 2. Render First Pass
    html_1 = render_panel(panel)
    hash_1 = hashlib.sha256(html_1.encode()).hexdigest()
    print(f"[Run 1] Generated HTML ({len(html_1)} chars). Hash: {hash_1[:8]}")

    # 3. Render Second Pass (Same Input)
    html_2 = render_panel(panel)
    hash_2 = hashlib.sha256(html_2.encode()).hexdigest()
    print(f"[Run 2] Generated HTML ({len(html_2)} chars). Hash: {hash_2[:8]}")

    # 4. Verify Determinism
    if hash_1 == hash_2:
        print("[PASS] Determinism Verified: Hashes match.")
    else:
        print("[FAIL] Determinism Failed: Hashes do not match!")
        sys.exit(1)

    # 5. Output to File
    output_path = Path(__file__).parent / "example.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_1)
    
    print(f"\n[SUCCESS] Rendered file saved to: {output_path}")
    print("Open this file in your browser to inspect the UI.")

if __name__ == "__main__":
    main()
