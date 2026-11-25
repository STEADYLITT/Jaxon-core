"""Errors shared by the worker and governance modules (kept separate to avoid an import cycle)."""


class NonRetryableError(Exception):
    """Raise from a handler when retrying cannot help (bad input, unknown kind, denied)."""
