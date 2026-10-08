"""Python ports of pokefirered game systems.

The C sources in src/ remain the code that is compiled into the ROM. The
modules in this package are Python ports of individual game systems that read
the same data files as the C build, so they can be used for tools, simulations
and tests without a GBA toolchain.
"""

import os

# Root of the pokefirered repository (this file lives in python/pokefirered/).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)
