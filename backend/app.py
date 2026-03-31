from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any, Dict, List

from flask import Flask, Response, jsonify, request, stream_with_context
from flask_cors import CORS

from services.agent_orchestrator import AgentOrchestrator
from services.opencontext_mcp_client import MCPClientError, OpenContextMCPClient
from services.server_registry import (
    NotFoundError,
    RegistryError,
    ServerRegistryService,
)
from services.run_traces import RunTraceNotFoundError, RunTraceService
from services.storage import Storage
from services.runtime_config import backend_debug_from_env, cors_origins_from_env
from services.tool_router import ToolRouter, ToolRouterError

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    load_dotenv = None

storage = Storage()
server_registry = ServerRegistryService(storage=storage)
try:
    server_registry.ensure_default_servers_from_env()
except RegistryError as exc:
    print(f"[startup] default MCP server registration failed: {exc.message}")
run_traces = RunTraceService(storage=storage)
agent_orchestrator = AgentOrchestrator(registry=server_registry)
tool_router = ToolRouter(client_factory=lambda endpoint, **kwargs: OpenContextMCPClient(endpoint, **kwargs))


def _payload() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _error_response(error: RegistryError, status_code: int) -> Any:
    return (
        jsonify(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            }
        ),
        status_code,
    )


def _chunk_text(text: str, *, chunk_size: int = 260) -> List[str]:
    source = str(text or "")
    if not source:
        return []
    chunks: List[str] = []
    cursor = 0
    total = len(source)
    while cursor < total:
        chunks.append(source[cursor : cursor + chunk_size])
        cursor += chunk_size
    return chunks


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_tool_progress_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    debug = meta.get("debug") if isinstance(meta.get("debug"), dict) else {}

    connector = debug.get("connector") if isinstance(debug.get("connector"), dict) else {}
    connector_events = connector.get("tool_events") if isinstance(connector.get("tool_events"), list) else []
    sdk = debug.get("agent_sdk") if isinstance(debug.get("agent_sdk"), dict) else {}
    sdk_events = sdk.get("tool_events") if isinstance(sdk.get("tool_events"), list) else []
    sdk_visualizations = (
        sdk.get("visualizations")
        if isinstance(sdk.get("visualizations"), list)
        else []
    )
    combined_events = list(connector_events) + list(sdk_events)
    for item in combined_events:
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("type") or "").strip()
        if event_type == "mcp_tool_use":
            events.append(
                {
                    "phase": "tool_use",
                    "tool_name": str(item.get("tool_name") or "").strip(),
                    "server_name": str(item.get("server_name") or "").strip(),
                    "tool_use_id": str(item.get("tool_use_id") or "").strip(),
                }
            )
            continue
        if event_type == "mcp_tool_result":
            events.append(
                {
                    "phase": "tool_result",
                    "tool_name": str(item.get("tool_name") or "").strip(),
                    "tool_use_id": str(item.get("tool_use_id") or "").strip(),
                    "is_error": bool(item.get("is_error")),
                    "text_preview": str(item.get("text_preview") or "").strip(),
                }
            )

    for artifact in sdk_visualizations:
        if not isinstance(artifact, dict):
            continue
        events.append(
            {
                "phase": "visualization",
                "visualization_id": str(artifact.get("id") or "").strip(),
                "title": str(artifact.get("title") or "").strip(),
                "chart_type": str(artifact.get("chart_type") or "").strip(),
                "record_count": len(artifact.get("records") if isinstance(artifact.get("records"), list) else []),
            }
        )

    deterministic = debug.get("deterministic") if isinstance(debug.get("deterministic"), dict) else {}
    server_results = (
        deterministic.get("server_results")
        if isinstance(deterministic.get("server_results"), list)
        else []
    )
    for row in server_results:
        if not isinstance(row, dict):
            continue
        events.append(
            {
                "phase": "server_result",
                "server_name": str(row.get("name") or "").strip(),
                "ok": bool(row.get("ok")),
                "tool_count": int(row.get("tool_count") or 0),
            }
        )

    return events


def _build_run_trace(
    *,
    run_id: str,
    endpoint: str,
    created_at: str,
    started_perf: float,
    session_id: str | None,
    message: str,
    prefer_connector: bool,
    runtime_preference: str,
    result: Dict[str, Any] | None = None,
    error: str = "",
) -> Dict[str, Any]:
    completed_at = _iso_now()
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    if isinstance(result, dict):
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        debug = meta.get("debug") if isinstance(meta.get("debug"), dict) else {}
        tool_events = _collect_tool_progress_events(result)
        errors = debug.get("errors") if isinstance(debug.get("errors"), list) else []
        warnings = debug.get("warnings") if isinstance(debug.get("warnings"), list) else []
        record = {
            "run_id": run_id,
            "status": "completed",
            "endpoint": endpoint,
            "created_at": created_at,
            "completed_at": completed_at,
            "duration_ms": duration_ms,
            "session_id": str(result.get("session_id") or session_id or "").strip(),
            "request": {
                "message": message,
                "prefer_connector": bool(prefer_connector),
                "runtime_preference": str(runtime_preference or "").strip() or None,
            },
            "response": {
                "runtime": meta.get("runtime"),
                "fallback_used": bool(meta.get("fallback_used")),
                "fallback_reason": meta.get("fallback_reason"),
                "history_size": meta.get("history_size"),
                "server_count": meta.get("server_count"),
            },
            "tool_events": tool_events,
            "errors": [item for item in errors if isinstance(item, dict)],
            "warnings": [str(item) for item in warnings if str(item).strip()],
            "result_message": str(result.get("message") or ""),
            "result_meta": meta,
        }
        return record

    return {
        "run_id": run_id,
        "status": "failed",
        "endpoint": endpoint,
        "created_at": created_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "session_id": str(session_id or "").strip(),
        "request": {
            "message": message,
            "prefer_connector": bool(prefer_connector),
            "runtime_preference": str(runtime_preference or "").strip() or None,
        },
        "response": {
            "runtime": "error",
            "fallback_used": False,
            "fallback_reason": None,
        },
        "tool_events": [],
        "errors": [{"code": "runtime_error", "message": str(error or "Unknown runtime error")}],
        "warnings": [],
        "result_message": "",
        "result_meta": {},
    }


def _to_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


def _execute_sql_selected(tool_name: str) -> bool:
    return str(tool_name or "").strip().lower().endswith("execute_sql")


def _filter_servers(
    *,
    servers: List[Dict[str, Any]],
    server_id: str,
    server_name: str,
    enabled_only: bool,
) -> List[Dict[str, Any]]:
    rows = list(servers)
    if enabled_only:
        rows = [row for row in rows if bool(row.get("enabled", True))]

    if server_id:
        rows = [row for row in rows if str(row.get("id") or "").strip() == server_id]
    if server_name:
        target = server_name.casefold()
        rows = [row for row in rows if str(row.get("name") or "").strip().casefold() == target]
    return rows


def _list_registry_servers_for_runtime() -> List[Dict[str, Any]]:
    internal_list = getattr(server_registry, "list_servers_internal", None)
    if callable(internal_list):
        rows = internal_list()
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    rows = server_registry.list_servers()
    return [item for item in rows if isinstance(item, dict)]


def create_app() -> Flask:
    app = Flask(__name__)
    cors_origins = cors_origins_from_env()
    app.config["BACKEND_CORS_ORIGINS"] = cors_origins
    CORS(app, origins=cors_origins)

    @app.get("/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "service": "opencontext-federated-dashboard",
                "timestamp": int(time.time()),
            }
        )

    @app.get("/api/v1/system/info")
    def system_info() -> Any:
        return jsonify(
            {
                "runtime_mode": os.getenv("AGENT_RUNTIME_PRIMARY", "agent_sdk"),
                "anthropic_model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            }
        )

    @app.get("/api/v1/mcp/servers")
    def list_servers() -> Any:
        return jsonify({"servers": server_registry.list_servers()})

    @app.post("/api/v1/mcp/servers")
    def create_server() -> Any:
        try:
            created = server_registry.create_server(_payload())
            return jsonify({"server": created}), 201
        except RegistryError as exc:
            return _error_response(exc, 400)

    @app.get("/api/v1/mcp/servers/<server_id>")
    def get_server(server_id: str) -> Any:
        try:
            return jsonify({"server": server_registry.get_server(server_id)})
        except NotFoundError as exc:
            return _error_response(exc, 404)

    @app.put("/api/v1/mcp/servers/<server_id>")
    def update_server(server_id: str) -> Any:
        try:
            updated = server_registry.update_server(server_id, _payload())
            return jsonify({"server": updated})
        except NotFoundError as exc:
            return _error_response(exc, 404)
        except RegistryError as exc:
            return _error_response(exc, 400)

    @app.delete("/api/v1/mcp/servers/<server_id>")
    def delete_server(server_id: str) -> Any:
        try:
            server_registry.delete_server(server_id)
            return jsonify({"deleted": True, "id": server_id})
        except NotFoundError as exc:
            return _error_response(exc, 404)

    @app.post("/api/v1/mcp/servers/<server_id>/test")
    def test_server_connection(server_id: str) -> Any:
        try:
            result = server_registry.test_connection(server_id)
            status_code = 200 if result.get("ok") else 502
            return jsonify(result), status_code
        except NotFoundError as exc:
            return _error_response(exc, 404)

    @app.get("/api/v1/mcp/tools/list")
    def list_tools() -> Any:
        server_id = str(request.args.get("server_id") or "").strip()
        server_name = str(request.args.get("server_name") or "").strip()
        enabled_only = _to_bool(request.args.get("enabled_only"), True)

        all_servers = _list_registry_servers_for_runtime()
        filtered_servers = _filter_servers(
            servers=all_servers,
            server_id=server_id,
            server_name=server_name,
            enabled_only=enabled_only,
        )

        if (server_id or server_name) and not filtered_servers:
            return _error_response(
                NotFoundError(
                    "No MCP server matched the provided filter",
                    details={
                        "server_id": server_id or None,
                        "server_name": server_name or None,
                    },
                ),
                404,
            )

        servers_out: List[Dict[str, Any]] = []
        for server in filtered_servers:
            current_server = {
                "id": str(server.get("id") or "").strip(),
                "name": str(server.get("name") or "").strip(),
                "endpoint": str(server.get("endpoint") or "").strip(),
                "enabled": bool(server.get("enabled", True)),
            }
            servers_out.append(current_server)

        catalog = tool_router.build_catalog(filtered_servers)
        tools_out = catalog.tools
        errors = catalog.errors

        response_body = {
            "ok": len(errors) == 0,
            "tool_count": len(tools_out),
            "tools": tools_out,
            "servers": servers_out,
            "errors": errors,
            "filters": {
                "server_id": server_id or None,
                "server_name": server_name or None,
                "enabled_only": enabled_only,
            },
        }
        status_code = 200 if tools_out or not errors else 502
        return jsonify(response_body), status_code

    @app.post("/api/v1/mcp/tools/call")
    def call_tool() -> Any:
        payload = _payload()
        server_id = str(payload.get("server_id") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        arguments = payload.get("arguments")
        advanced_mode = _to_bool(payload.get("advanced_mode"), False)

        if not tool_name:
            return jsonify({"error": {"code": "validation_error", "message": "tool_name is required"}}), 400
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "validation_error",
                            "message": "arguments must be a JSON object",
                        }
                    }
                ),
                400,
            )

        is_execute_sql = _execute_sql_selected(tool_name)
        sql_guardrails: Dict[str, Any] = {}
        sql_max_rows = _to_int(os.getenv("SQL_TOOL_MAX_ROWS"), 5000, min_value=50, max_value=100000)
        sql_timeout_ms = _to_int(os.getenv("SQL_TOOL_READ_TIMEOUT_MS"), 30000, min_value=1000, max_value=120000)
        if is_execute_sql:
            if not advanced_mode:
                return (
                    jsonify(
                        {
                            "error": {
                                "code": "advanced_mode_required",
                                "message": "execute_sql requires advanced mode confirmation",
                                "details": {
                                    "hint": "Set advanced_mode=true in tool call payload to proceed.",
                                    "max_rows": sql_max_rows,
                                    "timeout_ms": sql_timeout_ms,
                                },
                            }
                        }
                    ),
                    400,
                )
            try:
                sql_guardrails = tool_router.validate_execute_sql_arguments(arguments, max_rows=sql_max_rows)
            except ToolRouterError as exc:
                return jsonify({"error": exc.to_dict()}), 400

        all_servers = _list_registry_servers_for_runtime()
        enabled_servers = [row for row in all_servers if bool(row.get("enabled", True))]
        if not enabled_servers:
            return (
                jsonify(
                    {
                        "error": {
                            "code": "no_enabled_servers",
                            "message": "No enabled MCP servers are available",
                        }
                    }
                ),
                400,
            )

        if server_id:
            target = [row for row in enabled_servers if str(row.get("id") or "").strip() == server_id]
            if not target:
                return (
                    jsonify(
                        {
                            "error": {
                                "code": "not_found",
                                "message": "Selected MCP server was not found or is disabled",
                                "details": {"server_id": server_id},
                            }
                        }
                    ),
                    404,
                )
            routing_pool = target
        else:
            routing_pool = enabled_servers

        try:
            candidates, catalog = tool_router.route_candidates(
                tool_name=tool_name,
                servers=routing_pool,
                preferred_server_id=server_id,
            )
        except ToolRouterError as exc:
            status_code = 404 if exc.code in {"not_found", "tool_not_routable", "tool_not_available"} else 400
            return jsonify({"error": exc.to_dict()}), status_code

        attempts: List[Dict[str, Any]] = []
        for server in candidates:
            endpoint = str(server.get("endpoint") or "").strip()
            headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
            try:
                client_kwargs: Dict[str, Any] = {}
                if is_execute_sql:
                    client_kwargs["read_timeout_ms"] = sql_timeout_ms
                client = OpenContextMCPClient(endpoint, headers=headers, **client_kwargs)
                initialize_result = client.initialize()
                call_result = client.tools_call(tool_name, arguments)
                return jsonify(
                    {
                        "ok": True,
                        "server": {
                            "id": str(server.get("id") or "").strip(),
                            "name": str(server.get("name") or "").strip(),
                            "endpoint": endpoint,
                        },
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "result": call_result.result,
                        "latency_ms": {
                            "initialize": initialize_result.latency_ms,
                            "tools_call": call_result.latency_ms,
                        },
                        "routing": {
                            "mode": "explicit_server" if server_id else "capability_match",
                            "candidate_count": len(candidates),
                            "attempted_server_ids": [row.get("server_id") for row in attempts],
                            "catalog_errors": catalog.errors,
                        },
                        "guardrails": {
                            "is_execute_sql": is_execute_sql,
                            "advanced_mode": bool(advanced_mode),
                            "max_rows": sql_max_rows if is_execute_sql else None,
                            "timeout_ms": sql_timeout_ms if is_execute_sql else None,
                            "query_preview": sql_guardrails.get("query_preview") if is_execute_sql else None,
                            "limit": sql_guardrails.get("limit") if is_execute_sql else None,
                        },
                    }
                )
            except MCPClientError as exc:
                attempts.append(
                    {
                        "server_id": str(server.get("id") or "").strip(),
                        "server_name": str(server.get("name") or "").strip(),
                        "endpoint": endpoint,
                        "error": exc.to_dict(),
                    }
                )

        return (
            jsonify(
                {
                    "ok": False,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "error": {
                        "code": "all_candidates_failed",
                        "message": "All routed MCP server candidates failed while executing tool",
                        "details": {
                            "candidate_count": len(candidates),
                            "attempt_count": len(attempts),
                            "hint": "Check server connectivity and run /api/v1/mcp/tools/list for capability diagnostics.",
                        },
                    },
                    "routing": {
                        "mode": "explicit_server" if server_id else "capability_match",
                        "catalog_errors": catalog.errors,
                        "attempts": attempts,
                    },
                    "guardrails": {
                        "is_execute_sql": is_execute_sql,
                        "advanced_mode": bool(advanced_mode),
                        "max_rows": sql_max_rows if is_execute_sql else None,
                        "timeout_ms": sql_timeout_ms if is_execute_sql else None,
                        "query_preview": sql_guardrails.get("query_preview") if is_execute_sql else None,
                        "limit": sql_guardrails.get("limit") if is_execute_sql else None,
                    },
                }
            ),
            502,
        )

    @app.post("/api/v1/agent/chat")
    def chat_non_stream() -> Any:
        payload = _payload()
        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("session_id") or "").strip() or None
        prefer_connector = _to_bool(payload.get("prefer_connector"), True)
        runtime_preference = str(payload.get("runtime_preference") or "").strip().lower()
        run_id = str(uuid.uuid4())
        started_perf = time.perf_counter()
        created_at = _iso_now()

        if not message:
            return jsonify({"error": "message is required"}), 400

        try:
            result = agent_orchestrator.run_turn(
                message=message,
                session_id=session_id,
                prefer_connector=prefer_connector,
                runtime_preference=runtime_preference,
            )
            result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            result_meta["run_id"] = run_id
            result["meta"] = result_meta
            trace = _build_run_trace(
                run_id=run_id,
                endpoint="/api/v1/agent/chat",
                created_at=created_at,
                started_perf=started_perf,
                session_id=session_id,
                message=message,
                prefer_connector=prefer_connector,
                runtime_preference=runtime_preference,
                result=result,
            )
            run_traces.log_run(trace)
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            trace = _build_run_trace(
                run_id=run_id,
                endpoint="/api/v1/agent/chat",
                created_at=created_at,
                started_perf=started_perf,
                session_id=session_id,
                message=message,
                prefer_connector=prefer_connector,
                runtime_preference=runtime_preference,
                result=None,
                error=str(exc),
            )
            run_traces.log_run(trace)
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/v1/agent/chat/stream")
    def chat_stream() -> Response:
        payload = _payload()
        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("session_id") or "").strip() or None
        prefer_connector = _to_bool(payload.get("prefer_connector"), True)
        runtime_preference = str(payload.get("runtime_preference") or "").strip().lower()
        run_id = str(uuid.uuid4())
        started_perf = time.perf_counter()
        created_at = _iso_now()

        if not message:
            return jsonify({"error": "message is required"}), 400

        def _event(name: str, body: Dict[str, Any]) -> str:
            return f"event: {name}\ndata: {json.dumps(body)}\n\n"

        @stream_with_context
        def _generate() -> Any:
            yield _event("status", {"phase": "accepted"})
            yield _event(
                "status",
                {
                    "phase": "runtime_started",
                    "preferred_runtime": runtime_preference
                    or (os.getenv("AGENT_RUNTIME_PRIMARY", "agent_sdk") if prefer_connector else "deterministic_mcp"),
                },
            )
            stream_queue: Queue = Queue()
            stream_state: Dict[str, Any] = {"result": None, "error": None}
            streamed_delta = False
            streamed_tool_progress = False
            streamed_visualization = False

            def _event_sink(event: Dict[str, Any]) -> None:
                if not isinstance(event, dict):
                    return
                event_name = str(event.get("event") or "").strip()
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if not event_name:
                    return
                stream_queue.put({"event": event_name, "payload": payload})

            def _run_turn() -> None:
                try:
                    stream_state["result"] = agent_orchestrator.run_turn(
                        message=message,
                        session_id=session_id,
                        prefer_connector=prefer_connector,
                        runtime_preference=runtime_preference,
                        event_sink=_event_sink,
                    )
                except Exception as exc:
                    stream_state["error"] = exc

            run_thread = threading.Thread(target=_run_turn, daemon=True)
            run_thread.start()

            while run_thread.is_alive() or not stream_queue.empty():
                try:
                    queued = stream_queue.get(timeout=0.2)
                except Empty:
                    continue
                event_name = str(queued.get("event") or "").strip()
                payload = queued.get("payload") if isinstance(queued.get("payload"), dict) else {}
                if not event_name:
                    continue
                if event_name == "delta":
                    streamed_delta = True
                if event_name == "tool_progress":
                    streamed_tool_progress = True
                if event_name == "visualization":
                    streamed_visualization = True
                yield _event(event_name, payload)

            try:
                run_thread.join(timeout=0)
                if stream_state.get("error") is not None:
                    raise stream_state["error"]
                result = stream_state.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Agent runtime completed without a result payload")
            except Exception as exc:
                trace = _build_run_trace(
                    run_id=run_id,
                    endpoint="/api/v1/agent/chat/stream",
                    created_at=created_at,
                    started_perf=started_perf,
                    session_id=session_id,
                    message=message,
                    prefer_connector=prefer_connector,
                    runtime_preference=runtime_preference,
                    result=None,
                    error=str(exc),
                )
                run_traces.log_run(trace)
                yield _event("error", {"error": str(exc)})
                yield _event("done", {"message": "", "meta": {"runtime": "stream_error"}, "session_id": session_id})
                return

            result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            result_meta["run_id"] = run_id
            result["meta"] = result_meta
            trace = _build_run_trace(
                run_id=run_id,
                endpoint="/api/v1/agent/chat/stream",
                created_at=created_at,
                started_perf=started_perf,
                session_id=session_id,
                message=message,
                prefer_connector=prefer_connector,
                runtime_preference=runtime_preference,
                result=result,
            )
            run_traces.log_run(trace)

            if not streamed_tool_progress:
                for progress_event in _collect_tool_progress_events(result):
                    yield _event("tool_progress", progress_event)

            if not streamed_visualization:
                meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
                debug = meta.get("debug") if isinstance(meta.get("debug"), dict) else {}
                agent_sdk = debug.get("agent_sdk") if isinstance(debug.get("agent_sdk"), dict) else {}
                visualizations = (
                    agent_sdk.get("visualizations")
                    if isinstance(agent_sdk.get("visualizations"), list)
                    else []
                )
                for artifact in visualizations:
                    if isinstance(artifact, dict):
                        yield _event("visualization", {"artifact": artifact})

            text = str(result.get("message") or "")
            if not streamed_delta:
                chunks = _chunk_text(text)
                total_chunks = len(chunks)
                for index, chunk in enumerate(chunks):
                    yield _event(
                        "delta",
                        {
                            "text": chunk,
                            "index": index,
                            "total": total_chunks,
                        },
                    )
            yield _event("status", {"phase": "completed"})
            yield _event("done", result)

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(_generate(), mimetype="text/event-stream", headers=headers)

    @app.get("/api/v1/runs")
    def list_runs() -> Any:
        limit = _to_int(request.args.get("limit"), 50, min_value=1, max_value=200)
        session_id = str(request.args.get("session_id") or "").strip()
        rows = run_traces.list_runs(limit=limit, session_id=session_id)
        return jsonify({"runs": rows, "count": len(rows), "limit": limit, "session_id": session_id or None})

    @app.get("/api/v1/runs/<run_id>")
    def get_run(run_id: str) -> Any:
        try:
            return jsonify({"run": run_traces.get_run(run_id)})
        except RunTraceNotFoundError as exc:
            return jsonify({"error": {"code": "not_found", "message": str(exc)}}), 404

    @app.get("/api/v1/runs/<run_id>/export")
    def export_run(run_id: str) -> Any:
        try:
            run = run_traces.get_run(run_id)
        except RunTraceNotFoundError as exc:
            return jsonify({"error": {"code": "not_found", "message": str(exc)}}), 404
        body = json.dumps(run, indent=2)
        headers = {
            "Content-Disposition": f'attachment; filename="run-{run_id}.json"'
        }
        return Response(body, status=200, mimetype="application/json", headers=headers)

    return app


app = create_app()


if __name__ == "__main__":
    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("BACKEND_PORT", "5100"))
    app.run(host=host, port=port, debug=backend_debug_from_env())



