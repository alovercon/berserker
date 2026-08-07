"""ULID-style ID generation matching the reference TypeScript implementation."""

import time
import secrets
from typing import Dict, Optional

# Module-level state for monotonic ID generation
_last_timestamp = 0  # type: int
_counter = 0  # type: int

_PREFIXES = {
    "event": "evt",
    "session": "ses",
    "message": "msg",
    "permission": "per",
    "question": "que",
    "user": "usr",
    "part": "prt",
    "pty": "pty",
    "tool": "tool",
    "workspace": "wrk",
}  # type: Dict[str, str]

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_MASK_48 = 0xFFFFFFFFFFFF  # 48-bit mask


def _random_base62(length):
    # type: (int) -> str
    """Generate `length` random base62 characters using secrets.token_bytes."""
    chars = []
    rand_bytes = secrets.token_bytes(length)
    for b in rand_bytes:
        chars.append(_BASE62[b % 62])
    return "".join(chars)


def _create(kind, descending, timestamp=None):
    # type: (str, bool, Optional[int]) -> str
    """Core ID creation logic.

    Args:
        kind: One of the prefix keys (e.g. "session", "message").
        descending: True for descending sort order (sessions), False for ascending.
        timestamp: Optional millisecond timestamp (for testing). Auto-detected if None.

    Returns:
        A 26-character ID string (e.g. "ses_a1b2c3d4e5f6ABCDEFghijklmnop").
    """
    global _last_timestamp, _counter

    current_timestamp = timestamp if timestamp is not None else int(time.time() * 1000)

    if current_timestamp != _last_timestamp:
        _last_timestamp = current_timestamp
        _counter = 0
    _counter += 1

    # Combine timestamp and counter: timestamp << 12 | counter
    now = current_timestamp * 0x1000 + _counter

    # For descending IDs, bitwise NOT so newer IDs sort BEFORE older ones
    if descending:
        now = ~now

    # Extract lower 48 bits as 6 bytes big-endian hex (12 hex chars)
    now_48 = now & _MASK_48
    hex_str = format(now_48, "012x")

    prefix_str = _PREFIXES[kind]
    return prefix_str + "_" + hex_str + _random_base62(14)


def generate_id(kind, descending=True, timestamp=None):
    # type: (str, bool, Optional[int]) -> str
    """Generate a ULID-style ID with the given prefix kind.

    Args:
        kind: One of "session", "message", "part", "event", "permission",
              "question", "user", "pty", "tool", "workspace".
        descending: True for descending sort (newer IDs are lexicographically smaller).
                    False for ascending sort (newer IDs are lexicographically larger).
        timestamp: Optional millisecond timestamp for testing.

    Returns:
        A 26-character ID string.
    """
    return _create(kind, descending, timestamp)


def generate_session_id(timestamp=None):
    # type: (Optional[int]) -> str
    """Generate a session ID (descending order: newer IDs sort first)."""
    return _create("session", True, timestamp)


def generate_message_id(timestamp=None):
    # type: (Optional[int]) -> str
    """Generate a message ID (ascending order: newer IDs sort last)."""
    return _create("message", False, timestamp)


def generate_part_id(timestamp=None):
    # type: (Optional[int]) -> str
    """Generate a part ID (ascending order: newer IDs sort last)."""
    return _create("part", False, timestamp)


def extract_timestamp(id_str):
    # type: (str) -> int
    """Extract the millisecond timestamp from an ascending ID.

    Args:
        id_str: An ascending ID string (e.g. "msg_...").

    Returns:
        The millisecond timestamp encoded in the ID.
    """
    underscore_pos = id_str.index("_")
    hex_part = id_str[underscore_pos + 1 : underscore_pos + 13]
    encoded = int(hex_part, 16)
    return encoded // 0x1000
