"""The one error a tool raises, and the codes it carries.

A tool failure is returned to the model as data, so it can ask the user or try another way.
The code says what kind of failure it is; the message says, in words a controller would
use, what to do next.
"""

from __future__ import annotations

UNRESOLVED_ENTITY = "UNRESOLVED_ENTITY"
"""An id, name or field that does not exist."""
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
"""Probably a typo of something real. Ask which was meant; never pick for the user."""
AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
"""Several real things match (a flight number flies on several days). Ask which."""
INVALID_ARGUMENT = "INVALID_ARGUMENT"
"""Present but malformed or out of range."""
ALREADY_STARTED = "ALREADY_STARTED"
"""The pairing reported before now and can no longer be re-crewed."""
FORBIDDEN = "FORBIDDEN"
"""The caller's role may not use this tool, or not for this crew member."""
UNAVAILABLE = "UNAVAILABLE"
"""A backing service (the disruption store) is not connected."""
INTERNAL = "INTERNAL"
"""A bug. Reported, never hidden."""


class ToolError(RuntimeError):
    """A failure the model should see and route around."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
