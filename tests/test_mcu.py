from __future__ import annotations

import unittest

from smc_mixer_tools.diagnostics import mcu as mcu_tool
from smc_mixer_tools.diagnostics import mcu_names


class McuNamesTest(unittest.TestCase):
    def test_groups(self) -> None:
        self.assertEqual(mcu_names.note_name(0), "REC 1")
        self.assertEqual(mcu_names.note_name(15), "SOLO 8")
        self.assertEqual(mcu_names.note_name(24), "SELECT 1")
        self.assertEqual(mcu_names.note_name(111), "FADER TOUCH 8")

    def test_singles_seen_on_device(self) -> None:
        # notes observed with aseqdump on the SMC-Mixer
        expected = {46: "BANK LEFT", 47: "BANK RIGHT", 91: "REWIND", 92: "FAST FWD", 94: "PLAY", 95: "RECORD"}
        for note, name in expected.items():
            self.assertEqual(mcu_names.note_name(note), name)
        self.assertEqual(mcu_names.note_name(54), "F1")
        self.assertEqual(mcu_names.note_name(127), "-")

    def test_cc_names(self) -> None:
        self.assertEqual(mcu_names.cc_name(16), "V-POT 1 (Turn)")
        self.assertEqual(mcu_names.cc_name(55), "V-POT 8 (LED ring)")


class DescribeTest(unittest.TestCase):
    def test_note_on_off(self) -> None:
        self.assertIn("on", mcu_tool.describe(bytes([0x90, 46, 0x7F])))
        self.assertIn("off", mcu_tool.describe(bytes([0x90, 46, 0x00])))
        self.assertIn("off", mcu_tool.describe(bytes([0x80, 46, 0x00])))

    def test_vpot_direction(self) -> None:
        self.assertIn("(+1)", mcu_tool.describe(bytes([0xB0, 16, 1])))
        self.assertIn("(-1)", mcu_tool.describe(bytes([0xB0, 16, 65])))

    def test_other_cc_is_plain_value(self) -> None:
        # Historical USER-mode capture without Shift: CC 52-58 as 127/0, CC 30 absolute.
        text = mcu_tool.describe(bytes([0xB0, 52, 127]))
        self.assertIn("= 127", text)
        self.assertNotIn("LED ring", text)
        self.assertNotIn("-63", text)

    def test_user_mode_button_on_vpot_cc(self) -> None:
        # In USER mode, MUTE 1 sends CC 20 = 127/0, not a V-Pot step.
        for val in (127, 0):
            text = mcu_tool.describe(bytes([0xB0, 20, val]))
            self.assertNotIn("V-POT", text)

    def test_pitch_bend(self) -> None:
        self.assertIn("16256", mcu_tool.describe(bytes([0xE7, 0x00, 0x7F])))
        self.assertIn("FADER 8", mcu_tool.describe(bytes([0xE7, 0x00, 0x7F])))


class KnobLedTest(unittest.TestCase):
    def test_blink_sends_far_value(self) -> None:
        self.assertEqual(mcu_tool.knob_led_value(127, True), 0)
        self.assertEqual(mcu_tool.knob_led_value(64, True), 0)
        self.assertEqual(mcu_tool.knob_led_value(0, True), 127)
        self.assertEqual(mcu_tool.knob_led_value(63, True), 127)

    def test_off_sends_current_value(self) -> None:
        for value in (0, 59, 127):
            self.assertEqual(mcu_tool.knob_led_value(value, False), value)


if __name__ == "__main__":
    unittest.main()
