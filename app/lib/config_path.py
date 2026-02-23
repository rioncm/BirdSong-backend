from __future__ import annotations

import os
from pathlib import Path


def resolve_config_path(project_root: Path) -> Path:
    """
    Resolve active config path with this precedence:
    1) BIRDSONG_CONFIG env override
    2) /etc/birdsong/config.yaml (container mount)
    3) <project_root>/config.yaml
    """
    override = os.getenv("BIRDSONG_CONFIG")
    if override:
        return Path(override).expanduser()

    mounted = Path("/etc/birdsong/config.yaml")
    if mounted.exists():
        return mounted

    return project_root / "config.yaml"
