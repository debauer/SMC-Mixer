from __future__ import annotations

import unittest

from smc_mixer import sinco as sc
from smc_mixer.errors import ProtocolError
from smc_mixer_tools.diagnostics import sinco as sinco_tool

# Self-test vector from M-UPGRADE upgrade_core.exe (query name/version).
GOLDEN = bytes.fromhex("F0 00 32 45 00 00 00 40 7F F7")


class SincoCodecTest(unittest.TestCase):
    def test_name_version_packet(self) -> None:
        self.assertEqual(sc.build_packet(sc.CMD_QUERY_NAME_VER), bytes.fromhex("00 59 11 00 00 00 FF"))

    def test_golden_vector(self) -> None:
        self.assertEqual(sc.encode_sysex(sc.CMD_QUERY_NAME_VER), GOLDEN)

    def test_real_name_version_reply(self) -> None:
        # Captured from the device (firmware 011) on the "Private" port.
        reply = bytes.fromhex(
            "F0 00 32 45 58 01 00 40 29 4D 06 35 69 14 0D 5E 32 72 3E 41 09 13 06"
            " 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 20 3E F7"
        )
        pkt = sc.decode_sysex(reply)
        self.assertEqual(pkt.cmd, sc.CMD_QUERY_NAME_VER)
        self.assertEqual(len(pkt.payload), 27)
        self.assertEqual(pkt.payload.rstrip(b"\x00"), b"SMC-Mixer_011")

    def test_read_packet_layout(self) -> None:
        payload = sc.read_payload(0x02, 0x12345678, 0x40)
        self.assertEqual(payload, bytes.fromhex("02 78 56 34 12 40 00 00"))
        pkt = sc.build_packet(sc.CMD_READ, payload)
        self.assertEqual(pkt[:6], bytes.fromhex("00 59 23 08 00 00"))
        self.assertEqual(pkt[-1], (~sum(payload)) & 0xFF)

    def test_roundtrip(self) -> None:
        for payload in (b"", b"\x01", bytes(range(256)), b"\xff" * 33):
            msg = sc.encode_sysex(sc.CMD_WRITE, payload)
            self.assertTrue(all(b < 0x80 for b in msg[1:-1]))
            self.assertEqual(sc.decode_sysex(msg), sc.Packet(sc.CMD_WRITE, payload))

    def test_decode_rejects_bad_checksum(self) -> None:
        raw = bytearray(sc.build_packet(sc.CMD_READ, b"\x01\x02"))
        raw[-1] ^= 0x01
        with self.assertRaises(ProtocolError):
            sc.decode_sysex(b"\xf0" + sc.pack7(bytes(raw)) + b"\xf7")

    def test_decode_rejects_foreign_sysex(self) -> None:
        with self.assertRaises(ProtocolError):
            sc.decode_sysex(bytes.fromhex("F0 7E 7F 06 01 F7"))


class FaderTableTest(unittest.TestCase):
    def test_parse_real_dump(self) -> None:
        # type 4, 0x326..0x33D captured on firmware 011 with known fader positions
        data = bytes.fromhex("12 7F 01 4A 55 01 0B 00 01 65 3B 01 38 7F 01 49 00 01 5A 20 01 72 69 01")
        self.assertEqual(sinco_tool.parse_faders(data), [0x7F, 0x55, 0x00, 0x3B, 0x7F, 0x00, 0x20, 0x69])

    def test_parse_rejects_short_data(self) -> None:
        with self.assertRaises(sinco_tool.ProbeError):
            sinco_tool.parse_faders(b"\x00" * 23)


if __name__ == "__main__":
    unittest.main()
