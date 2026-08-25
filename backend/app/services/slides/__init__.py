"""Slides generation service facade."""

from .agent import JobError, ProgressCallback, generate_slides

__all__ = ("JobError", "ProgressCallback", "generate_slides")
