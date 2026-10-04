from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from .errors import DragonFruitMeError


def state_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the host-bound state directory (recipe store).

    The agent-facing schema never accepts a filesystem path. The host binds
    the state directory with ``DRAGONFRUITME_STATE_DIR``; the default is
    ``~/.dragonfruitme``.
    """
    env = os.environ if environ is None else environ
    raw = env.get("DRAGONFRUITME_STATE_DIR", "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / ".dragonfruitme"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DragonFruitMeError(
            "STATE_DIR_UNAVAILABLE",
            f"Cannot create DragonFruitMe state directory {path}: {exc}",
            recoverable=False,
        ) from exc
    if path.is_symlink():
        raise DragonFruitMeError("STATE_DIR_SYMLINK", f"State directory must not be a symlink: {path}", recoverable=False)
    return path.resolve()
