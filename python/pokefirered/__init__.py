"""Helpers for PC-side Python tools: repository paths, constants read from the
C headers, and Pokemon data. Game code itself lives in src/*.py (see the gba
package)."""

import os

# Root of the pokefirered repository (this file lives in python/pokefirered/).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)
