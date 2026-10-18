"""Exception types of the smc_mixer package."""

from __future__ import annotations


class SmcMixerError(Exception):
    """Base class for all errors raised by smc_mixer."""


class PortNotFoundError(SmcMixerError):
    """A MIDI port of the mixer is not available (not connected?)."""


class ProtocolError(SmcMixerError):
    """A message from the mixer does not match the expected protocol."""


class ResponseTimeoutError(SmcMixerError):
    """The mixer did not answer a configuration request in time."""


class DisconnectedError(SmcMixerError):
    """The mixer is currently not connected (unplugged or switched off)."""


class UnsupportedFirmwareError(SmcMixerError):
    """The feature is only verified for another firmware version."""
