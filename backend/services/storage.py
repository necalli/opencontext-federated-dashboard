from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List


class Storage:
    def __init__(self, root_dir: Path | None = None) -> None:
        base = root_dir or (Path(__file__).resolve().parent.parent / "data")
        self.root_dir = Path(base)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._server_registry_path = self.root_dir / "mcp_servers.json"
        self._run_traces_path = self.root_dir / "run_traces.json"
        self._agent_sdk_sessions_path = self.root_dir / "agent_sdk_sessions.json"
        if not self._server_registry_path.exists():
            self._write_json(self._server_registry_path, {"servers": []})
        if not self._run_traces_path.exists():
            self._write_json(self._run_traces_path, {"runs": []})
        if not self._agent_sdk_sessions_path.exists():
            self._write_json(self._agent_sdk_sessions_path, {"session_map": {}})

    def _read_json(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return dict(default)

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def get_mcp_servers(self) -> List[Dict[str, Any]]:
        with self._lock:
            parsed = self._read_json(self._server_registry_path, {"servers": []})
            servers = parsed.get("servers")
            if not isinstance(servers, list):
                return []
            return [item for item in servers if isinstance(item, dict)]

    def save_mcp_servers(self, servers: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._write_json(self._server_registry_path, {"servers": servers})

    def get_run_traces(self) -> List[Dict[str, Any]]:
        with self._lock:
            parsed = self._read_json(self._run_traces_path, {"runs": []})
            rows = parsed.get("runs")
            if not isinstance(rows, list):
                return []
            return [item for item in rows if isinstance(item, dict)]

    def save_run_traces(self, runs: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._write_json(self._run_traces_path, {"runs": runs})

    def get_agent_sdk_session_map(self) -> Dict[str, str]:
        with self._lock:
            parsed = self._read_json(self._agent_sdk_sessions_path, {"session_map": {}})
            rows = parsed.get("session_map")
            if not isinstance(rows, dict):
                return {}
            output: Dict[str, str] = {}
            for key, value in rows.items():
                app_session_id = str(key or "").strip()
                sdk_session_id = str(value or "").strip()
                if not app_session_id or not sdk_session_id:
                    continue
                output[app_session_id] = sdk_session_id
            return output

    def save_agent_sdk_session_map(self, session_map: Dict[str, str]) -> None:
        payload: Dict[str, str] = {}
        for key, value in (session_map or {}).items():
            app_session_id = str(key or "").strip()
            sdk_session_id = str(value or "").strip()
            if not app_session_id or not sdk_session_id:
                continue
            payload[app_session_id] = sdk_session_id
        with self._lock:
            self._write_json(self._agent_sdk_sessions_path, {"session_map": payload})
