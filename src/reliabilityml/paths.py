"""Where the project's data folders live (corpus/, eval/, artifacts/).

Resolution order: RELIABILITYML_HOME, then the working directory if it holds corpus/, then the source
checkout. The last one alone breaks for a non-editable install, where the package sits in site-packages.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("RELIABILITYML_HOME")
    if env:
        return Path(env)
    cwd = Path.cwd()
    if (cwd / "corpus").is_dir():
        return cwd
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
