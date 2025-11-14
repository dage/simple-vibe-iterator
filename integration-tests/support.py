from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_dotenv(env_path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not env_path.exists():
        return env
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def get_env_value(name: str, dotenv: Dict[str, str]) -> Optional[str]:
    return os.getenv(name) or dotenv.get(name)


def ensure_project_root(*, add_src_to_syspath: bool = False) -> Path:
    os.chdir(PROJECT_ROOT)
    target = PROJECT_ROOT / "src" if add_src_to_syspath else PROJECT_ROOT
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    return PROJECT_ROOT


def bootstrap_test_env(*, add_src_to_syspath: bool = False) -> Tuple[Path, Dict[str, str]]:
    root = ensure_project_root(add_src_to_syspath=add_src_to_syspath)
    dotenv = parse_dotenv(root / ".env")
    return root, dotenv


def env_ready(dotenv: Dict[str, str], required: Sequence[str]) -> Tuple[bool, str]:
    missing = [key for key in required if not get_env_value(key, dotenv)]
    if missing:
        return False, f"missing: {', '.join(missing)}"
    return True, "all present"
