"""Compatibility exports for the existing navigation security boundary.

The protocol stays native CDP; these aliases deliberately do not introduce a
second navigation implementation during the process-isolation migration.
"""

from .runtime import _DocumentNavigationGate, _RawBrowserCDP

__all__ = ["_DocumentNavigationGate", "_RawBrowserCDP"]
