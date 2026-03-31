from __future__ import annotations

import os
from typing import Any, List


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)
    return bool(value)


def cors_origins_from_env() -> List[str]:
    raw = str(os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000") or "")
    values: List[str] = []
    seen = set()
    for part in raw.split(","):
        origin = str(part or "").strip()
        if not origin or origin in seen:
            continue
        seen.add(origin)
        values.append(origin)
    if values:
        return values
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def backend_debug_from_env() -> bool:
    return _to_bool(os.getenv("BACKEND_DEBUG"), False)
