"""Deprecated shim — import from cortex.media_gate instead.

Kept only so older call sites don't break. Will be removed in a future release.
"""
from __future__ import annotations

import warnings

from .media_gate import (  # noqa: F401
    DEFAULT,
    MediaDescription,
    classify,
)

warnings.warn(
    "cortex.cat_gate is deprecated; import cortex.media_gate instead.",
    DeprecationWarning,
    stacklevel=2,
)
