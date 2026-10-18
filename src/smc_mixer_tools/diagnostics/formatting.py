"""Human-readable descriptions of diagnostic protocol packets."""

from smc_mixer.errors import ProtocolError
from smc_mixer.sinco import decode_sysex


def describe_sysex(message: bytes) -> str:
    try:
        packet = decode_sysex(message)
    except ProtocolError as exc:
        return f"(kein Sinco-Paket: {exc})"
    text = packet.payload.decode("ascii", errors="replace")
    return f"Sinco cmd {packet.cmd:02X}, Payload {packet.payload.hex(' ').upper()} | {text!r}"
