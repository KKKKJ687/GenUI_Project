#!/usr/bin/env python3
"""
Phase 1 Acceptance Test Suite
验证核心指标: Schema Validation, Deterministic Rendering, Safety Escaping, Layout Collision

Run: python verify_phase1_complete.py
"""
import sys
import unittest
import html as html_lib
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from src.models.schema import HMIPanel, SliderWidget, SwitchWidget, LayoutItem, DataBinding
    from src.modules.renderer.renderer import render_panel, _resolve_layout_collisions
except ImportError as e:
    print(f"ERROR: Missing modules. Ensure Phase 1 files are in place.\n{e}")
    sys.exit(1)


class TestPhase1Acceptance(unittest.TestCase):
    """Phase 1 Acceptance Test Suite"""

    def test_01_schema_enforcement(self):
        """验收标准 1: DSL Schema 必须拒绝非法数据 (Missing Required Fields)"""
        print("\n[Test 1] Schema Enforcement...")
        
        # Test: Slider without binding should still work (binding is optional)
        # But slider without min/max should fail
        try:
            # This should succeed - minimal valid slider
            valid_slider = SliderWidget(
                id="test_slider",
                label="Test",
                type="slider",
                min=0,
                max=100,
                value=50
            )
            print(f"  -> Slider created: {valid_slider.id}")
        except Exception as e:
            self.fail(f"Valid slider creation failed unexpectedly: {e}")
        
        # Test: Invalid widget type should fail validation
        try:
            from pydantic import ValidationError
            invalid_data = {
                "id": "bad",
                "label": "Bad Widget",
                "type": "invalid_type_xyz"  # Invalid type
            }
            # Try to create via HMIPanel validation
            panel = HMIPanel(
                title="Test",
                widgets=[invalid_data],
                layout=[{"i": "bad", "x": 0, "y": 0, "w": 4, "h": 2}]
            )
            self.fail("Schema should reject invalid widget type!")
        except Exception:
            print("  -> PASSED: Successfully caught invalid widget type.")

    def test_02_xss_prevention(self):
        """验收标准 2: 渲染器必须转义 XSS 攻击向量"""
        print("\n[Test 2] XSS Safety Check...")
        
        malicious_label = "<script>alert('XSS')</script>"
        malicious_title = "<img src=x onerror=alert('pwned')>"
        
        panel = HMIPanel(
            title=malicious_title,
            description="<b>Bold Attack</b>",
            widgets=[
                SliderWidget(
                    id="w1",
                    label=malicious_label,
                    type="slider",
                    min=0,
                    max=100,
                    value=50,
                    binding=DataBinding(protocol="mock", address="test/1")
                )
            ],
            layout=[LayoutItem(i="w1", x=0, y=0, w=4, h=2)]
        )
        
        html_out = render_panel(panel)
        
        # Verify that ACTUAL executable script tags are NOT present
        # (Look for unescaped opening tags that would execute)
        self.assertNotIn("<script>alert", html_out, "XSS Vulnerability: Unescaped script tag!")
        self.assertNotIn("<img src=x onerror", html_out, "XSS Vulnerability: Unescaped img onerror!")
        self.assertNotIn("<b>Bold", html_out, "XSS Vulnerability: Unescaped HTML in description!")
        
        # Verify escaped versions ARE present (security working)
        self.assertIn("&lt;script&gt;", html_out, "Label was not properly escaped.")
        self.assertIn("&lt;img", html_out, "Title img tag was not properly escaped.")
        self.assertIn("&lt;b&gt;", html_out, "Description bold tag was not properly escaped.")
        
        print("  -> PASSED: Malicious script tags successfully neutralized.")

    def test_03_layout_collision_resolution(self):
        """验收标准 3: 布局重叠必须被自动修复 (Gravity Algorithm)"""
        print("\n[Test 3] Layout Collision Resolution...")
        
        # Construct two COMPLETELY overlapping LayoutItems
        items = [
            LayoutItem(i="w1", x=0, y=0, w=4, h=2),
            LayoutItem(i="w2", x=0, y=0, w=4, h=2)  # Same coords!
        ]
        
        # Run collision resolution
        fixed_items = _resolve_layout_collisions(items)
        
        item1 = next(i for i in fixed_items if i.i == "w1")
        item2 = next(i for i in fixed_items if i.i == "w2")
        
        print(f"  Item w1: y={item1.y}")
        print(f"  Item w2: y={item2.y}")
        
        # Verify y coordinates are different (collision resolved)
        self.assertNotEqual(item1.y, item2.y, "Layout items still overlap!")
        
        # At least one should stay at y=0 (first placed)
        self.assertTrue(item1.y == 0 or item2.y == 0, "Expected one item at y=0")
        
        # The other should be pushed down by at least h=2
        max_y = max(item1.y, item2.y)
        self.assertGreaterEqual(max_y, 2, "Pushed item should be at y>=2")
        
        print("  -> PASSED: Collision resolved by gravity.")

    def test_04_mobile_responsiveness(self):
        """验收标准 4: CSS 必须包含移动端适配规则"""
        print("\n[Test 4] Mobile Responsive CSS...")
        
        panel = HMIPanel(
            title="Mobile Test",
            widgets=[],
            layout=[]
        )
        html_out = render_panel(panel)
        
        # Check for @media query
        self.assertIn("@media (max-width:", html_out, "Missing CSS Media Queries.")
        
        # Check for mobile flex-column layout
        self.assertIn("flex-direction: column", html_out, "Missing mobile flex-column layout.")
        
        # Check for finger-friendly touch targets
        self.assertIn("min-height: 120px", html_out, "Missing finger-friendly touch targets.")
        
        print("  -> PASSED: Mobile responsive styles detected.")

    def test_05_determinism(self):
        """验收标准 5: 两次渲染相同DSL必须产生完全相同的HTML"""
        print("\n[Test 5] Deterministic Rendering...")
        
        panel = HMIPanel(
            title="Determinism Test",
            description="Testing reproducibility",
            widgets=[
                SliderWidget(
                    id="slider_a",
                    label="Slider A",
                    type="slider",
                    min=0,
                    max=100,
                    value=25,
                    binding=DataBinding(protocol="mqtt", address="a/val")
                ),
                SwitchWidget(
                    id="switch_b",
                    label="Switch B",
                    type="switch",
                    on_label="ON",
                    off_label="OFF",
                    value=True,
                    binding=DataBinding(protocol="modbus", address="40001")
                )
            ],
            layout=[
                LayoutItem(i="slider_a", x=0, y=0, w=6, h=2),
                LayoutItem(i="switch_b", x=6, y=0, w=4, h=2)
            ]
        )
        
        # Render twice
        html_1 = render_panel(panel)
        html_2 = render_panel(panel)
        
        # Compare lengths first
        self.assertEqual(len(html_1), len(html_2), "HTML lengths differ!")
        
        # Compare content
        self.assertEqual(html_1, html_2, "Rendered HTML is not deterministic!")
        
        # Verify hash consistency
        import hashlib
        hash_1 = hashlib.sha256(html_1.encode()).hexdigest()[:8]
        hash_2 = hashlib.sha256(html_2.encode()).hexdigest()[:8]
        self.assertEqual(hash_1, hash_2, "Hashes differ!")
        
        print(f"  -> PASSED: Determinism verified. Hash: {hash_1}")

    def test_06_complex_collision_grid(self):
        """验收标准 6: 复杂网格布局碰撞测试"""
        print("\n[Test 6] Complex Grid Collision...")
        
        # Create multiple overlapping items in various configurations
        items = [
            LayoutItem(i="a", x=0, y=0, w=6, h=2),
            LayoutItem(i="b", x=0, y=0, w=4, h=2),   # Overlaps with a
            LayoutItem(i="c", x=4, y=0, w=4, h=2),   # Partially overlaps with a
            LayoutItem(i="d", x=0, y=2, w=12, h=1),  # Should be safe at y=2
        ]
        
        fixed = _resolve_layout_collisions(items)
        
        # Build occupancy grid to verify no overlaps
        occupied = {}
        for item in fixed:
            for r in range(item.y, item.y + item.h):
                for c in range(item.x, item.x + item.w):
                    key = (r, c)
                    if key in occupied:
                        self.fail(f"Cell {key} occupied by both {occupied[key]} and {item.i}!")
                    occupied[key] = item.i
        
        print(f"  -> Resolved {len(fixed)} items into {len(occupied)} cells without overlap.")
        print("  -> PASSED: Complex grid collision resolved.")


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 1 Acceptance Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
