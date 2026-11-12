"""Structured exception hierarchy for the LeverEdge engine."""
from __future__ import annotations


class LeverEdgeError(Exception):
    """Base class for all model errors."""


class ValidationError(LeverEdgeError):
    """Raised when user inputs are internally inconsistent or out of range."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class ModelError(LeverEdgeError):
    """Raised when a calculation cannot be completed (e.g. a solver fails)."""
