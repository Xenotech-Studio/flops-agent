"""Kernel keyring stash for hot-reload survival of K_conv / K_agent.

Why
---
chat_v2 holds K_conv / K_agent only in a per-request contextvar. When uvicorn
--reload sends SIGTERM the request task dies and the contextvar value evaporates
with it. The new process's startup recovery sweep then has no way to decrypt
the in-flight encrypted conversation → currently those runs are marked
resume_failed.

This module mirrors K_conv / K_agent to the Linux kernel user keyring
(KEY_SPEC_USER_KEYRING, "@u") for the duration of an active run. The new
process retrieves them on startup via search + read + unlink (atomic take).

Threat model
------------
Same-UID readable. An attacker with shell as the same UID can ``keyctl_print``
the stashed bytes. This is equivalent to the attacker reading
``/proc/{uvicorn_pid}/mem`` for the contextvar value — i.e. it does not expand
the blast radius beyond the compromised-process case in ENCRYPTION.md threat
model #6.

Key material never touches disk: the user keyring lives in kernel memory,
indexed per UID, with per-key TTL set via KEYCTL_SET_TIMEOUT. We default TTL
to 1 hour to cover the longest reasonable chat session; eviction-on-success
in the chat_v2 happy path keeps the resident set tiny.

Bootstrap (see ``_load``): at first use we join a throwaway named session
keyring and link @u into it so we *possess* @u — without that, default key
permissions on add_key would deny our own subsequent reads (EACCES).

API
---
- ``put(name, payload, ttl_seconds=3600)`` — add (or replace) a key
- ``take(name)`` — search + read + unlink atomically; returns bytes or None
- ``evict(name)`` — best-effort unlink, no error if not present
- ``available()`` — whether libkeyutils loaded (false on systems without it)

If libkeyutils is unavailable everything degrades to no-op — callers see
``available() == False`` / ``take() == None`` and continue with their existing
"resume failed" path, exactly as if the module weren't installed.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
from typing import Optional

_log = logging.getLogger(__name__)

# /usr/include/keyutils.h
_KEY_SPEC_USER_KEYRING = -4
_DEFAULT_TTL_SECONDS = 3600
_KEY_TYPE = b"user"
_DESC_PREFIX = "flops:keystash:"
_READ_BUF_SZ = 256  # K_conv/K_agent are 32B each; 256B is plenty headroom

# Storage is the per-UID user keyring (@u). @u always exists for an active
# UID (ubuntu has running processes constantly: uvicorn, sshd, systemd-user,
# etc.) → keys survive uvicorn worker death and reload window.
#
# Permission setup:
#   - The default key created by add_key only grants KEY_USR_VIEW to the
#     same UID — READ / SET_TIMEOUT / UNLINK require *possession*.
#   - @u isn't auto-linked into our session keyring, so we don't possess
#     it and any ops besides VIEW return EACCES.
#   - The persistent keyring (@us) is denied in some userns / hardened
#     setups (EKEYREVOKED) — observed on this host.
#
# Fix: at module init we (1) join a throwaway named session keyring so we
# have a session keyring we possess, then (2) link @u into it. After (2)
# @u is in our possessed-keyring tree → all ops succeed via POS perms.
# The throwaway session keyring's identity does not need to be stable
# across processes — only @u itself does, and @u is per-UID.
#
# Side effect: this replaces our @s for the calling process. uvicorn /
# FastAPI / our stack don't otherwise use the kernel keyring, so this is
# inert. Worth knowing if some future dep starts using @s.
_BOOTSTRAP_SESSION_NAME = b"flops-keystash-possess"

_lock = threading.Lock()
_lib: Optional[ctypes.CDLL] = None
_unavailable = False
_session_keyring_serial: Optional[int] = None


def _load() -> Optional[ctypes.CDLL]:
    global _lib, _unavailable, _session_keyring_serial
    if _lib is not None:
        return _lib
    if _unavailable:
        return None
    with _lock:
        if _lib is not None:
            return _lib
        if _unavailable:
            return None
        try:
            path = ctypes.util.find_library("keyutils") or "libkeyutils.so.1"
            lib = ctypes.CDLL(path, use_errno=True)
            # add_key(type, desc, payload, plen, keyring) -> key_serial_t (int32)
            lib.add_key.argtypes = [
                ctypes.c_char_p, ctypes.c_char_p,
                ctypes.c_void_p, ctypes.c_size_t,
                ctypes.c_int32,
            ]
            lib.add_key.restype = ctypes.c_int32
            # libkeyutils provides typed (non-variadic) wrappers around the keyctl(2)
            # syscall — bind those rather than the variadic keyctl().
            lib.keyctl_search.argtypes = [
                ctypes.c_int32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32,
            ]
            lib.keyctl_search.restype = ctypes.c_int32
            lib.keyctl_read.argtypes = [
                ctypes.c_int32, ctypes.c_char_p, ctypes.c_size_t,
            ]
            lib.keyctl_read.restype = ctypes.c_long
            lib.keyctl_unlink.argtypes = [ctypes.c_int32, ctypes.c_int32]
            lib.keyctl_unlink.restype = ctypes.c_long
            lib.keyctl_set_timeout.argtypes = [ctypes.c_int32, ctypes.c_uint]
            lib.keyctl_set_timeout.restype = ctypes.c_long
            # keyctl_join_session_keyring(name) — creates or joins a named
            # session keyring. Used here as a throwaway to obtain a session
            # keyring we possess, into which we link @u.
            lib.keyctl_join_session_keyring.argtypes = [ctypes.c_char_p]
            lib.keyctl_join_session_keyring.restype = ctypes.c_int32
            # keyctl_link(src_key, dest_keyring) — link the source key into
            # the destination keyring. We use it to make @u reachable from
            # our possessed session keyring.
            lib.keyctl_link.argtypes = [ctypes.c_int32, ctypes.c_int32]
            lib.keyctl_link.restype = ctypes.c_long
            _lib = lib
            serial = lib.keyctl_join_session_keyring(_BOOTSTRAP_SESSION_NAME)
            if serial < 0:
                _unavailable = True
                _lib = None
                _log.warning(
                    "keystash: join_session_keyring(%s) failed errno=%d — disabled",
                    _BOOTSTRAP_SESSION_NAME.decode(), ctypes.get_errno(),
                )
                return None
            _session_keyring_serial = int(serial)
            link_rc = lib.keyctl_link(_KEY_SPEC_USER_KEYRING, serial)
            if link_rc < 0:
                _unavailable = True
                _lib = None
                _log.warning(
                    "keystash: link @u into joined session failed errno=%d — disabled",
                    ctypes.get_errno(),
                )
                return None
            _log.info(
                "keystash: libkeyutils loaded (%s); session=%d, @u linked for possession",
                path, _session_keyring_serial,
            )
            return lib
        except (OSError, AttributeError) as e:
            _unavailable = True
            _log.warning(
                "keystash: libkeyutils unavailable — hot-reload K_conv survival disabled (%s)",
                e,
            )
            return None


def available() -> bool:
    return _load() is not None


def _desc_bytes(name: str) -> bytes:
    return (_DESC_PREFIX + name).encode("ascii")


def put(name: str, payload: bytes, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> bool:
    """Add ``payload`` to the user keyring under ``name``. Replaces existing
    entry of same description (add_key semantics). Returns True on success."""
    lib = _load()
    if lib is None:
        return False
    if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:  # pyright: ignore[reportUnnecessaryIsInstance]
        return False
    data = bytes(payload)
    buf = ctypes.create_string_buffer(data, len(data))
    serial = lib.add_key(
        _KEY_TYPE, _desc_bytes(name),
        ctypes.cast(buf, ctypes.c_void_p), len(data),
        _KEY_SPEC_USER_KEYRING,
    )
    if serial < 0:
        _log.warning("keystash put %s: add_key failed errno=%d", name, ctypes.get_errno())
        return False
    rc = lib.keyctl_set_timeout(serial, int(ttl_seconds))
    if rc < 0:
        # Key added but no TTL — log and continue. The chat_v2 happy path will
        # evict explicitly on run completion; only a SIGKILL'd worker that never
        # ran its finally would leave an entry. Without TTL such an entry sits
        # until @u itself is reclaimed (effectively forever for an active UID).
        _log.warning(
            "keystash put %s: set_timeout failed errno=%d (entry has no per-key TTL)",
            name, ctypes.get_errno(),
        )
    return True


def take(name: str) -> Optional[bytes]:
    """Atomic search + read + unlink. Returns payload bytes or None if not found."""
    lib = _load()
    if lib is None:
        return None
    serial = lib.keyctl_search(_KEY_SPEC_USER_KEYRING, _KEY_TYPE, _desc_bytes(name), 0)
    if serial < 0:
        # ENOKEY = -ENOKEY (typically -126). Expected when nothing was parked.
        return None
    buf = ctypes.create_string_buffer(_READ_BUF_SZ)
    n = lib.keyctl_read(serial, buf, _READ_BUF_SZ)
    if n < 0 or n > _READ_BUF_SZ:
        _log.warning("keystash take %s: read failed n=%d errno=%d", name, n, ctypes.get_errno())
        _safe_unlink(lib, serial)
        return None
    payload = bytes(buf.raw[: int(n)])
    _safe_unlink(lib, serial)
    return payload


def peek(name: str) -> Optional[bytes]:
    """Search + read WITHOUT unlink (non-destructive). Returns payload bytes or None.

    Unlike take(), the key stays in @u. Use this on hot-reload recovery: if the
    recovering process is SIGKILL'd mid-resume (e.g. a reload storm whose grace
    period expires during the heavy resume prelude) before it can re-flush, the key
    is still in the keyring for the *next* recovery to read — destructive take()
    would have lost the only copy. The caller is responsible for evicting on the
    run's terminal (done / resume_failed); the per-key TTL is the backstop otherwise.
    """
    lib = _load()
    if lib is None:
        return None
    serial = lib.keyctl_search(_KEY_SPEC_USER_KEYRING, _KEY_TYPE, _desc_bytes(name), 0)
    if serial < 0:
        return None
    buf = ctypes.create_string_buffer(_READ_BUF_SZ)
    n = lib.keyctl_read(serial, buf, _READ_BUF_SZ)
    if n < 0 or n > _READ_BUF_SZ:
        _log.warning("keystash peek %s: read failed n=%d errno=%d", name, n, ctypes.get_errno())
        return None
    return bytes(buf.raw[: int(n)])


def evict(name: str) -> None:
    """Best-effort unlink by name. Silent no-op if not present or unavailable."""
    lib = _load()
    if lib is None:
        return
    serial = lib.keyctl_search(_KEY_SPEC_USER_KEYRING, _KEY_TYPE, _desc_bytes(name), 0)
    if serial < 0:
        return
    _safe_unlink(lib, serial)


def _safe_unlink(lib: ctypes.CDLL, serial: int) -> None:
    try:
        lib.keyctl_unlink(serial, _KEY_SPEC_USER_KEYRING)
    except Exception:
        pass


__all__ = ["put", "take", "peek", "evict", "available"]
