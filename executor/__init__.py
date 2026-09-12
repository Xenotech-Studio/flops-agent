"""Framework support for executors that run tools on user machines.

Products own transport, device discovery, and routing. This package provides
the transport-independent protocol and server-side dispatch hub; dispatch
registration and restart resume live in ``engine.runner`` and ``seams.run_store``.
"""
from . import protocol
from .link import DispatchLedger, ExecutorLink, InMemoryDispatchLedger, default_translate

__all__ = ["protocol", "DispatchLedger", "ExecutorLink", "InMemoryDispatchLedger", "default_translate"]
