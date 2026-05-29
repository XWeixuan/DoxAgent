"""Identifier helpers for DoxAgent contract objects."""

from typing import Annotated
from uuid import uuid4

from pydantic import StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


def new_id(prefix: str) -> str:
    """Create a stable string identifier with a readable prefix."""
    clean_prefix = prefix.strip().lower().replace("-", "_")
    if not clean_prefix:
        raise ValueError("ID prefix must be non-empty.")
    return f"{clean_prefix}_{uuid4().hex}"
