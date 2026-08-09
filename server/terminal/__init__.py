"""Bounded human-terminal gateway for JoeOS.

Only the authenticated human operator may open a terminal session. The PTY
runs as the JoeOS backend user's shell (never root, no sudo). Agents have no
tool exposing the terminal, and nothing bypasses ToolBroker/policy/approval
through the PTY. Output is bounded; sessions expire after inactivity.
"""

from .gateway import TerminalError, TerminalGateway
from .router import router as terminal_router

__all__ = ["TerminalError", "TerminalGateway", "terminal_router"]
