"""Error types used by the lab CLI."""

from __future__ import annotations


class LabError(Exception):
    """A user-facing error. The message is printed without a traceback."""

    exit_code = 1


class UsageError(LabError):
    """Invalid command usage or user input."""

    exit_code = 2


class NotFoundError(LabError):
    """An experiment could not be found."""

    exit_code = 3


class StateError(LabError):
    """The requested operation is not valid for the current state."""

    exit_code = 4
