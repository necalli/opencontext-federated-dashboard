from __future__ import annotations

import json
import os
from typing import Dict
from urllib.parse import urlencode

import requests
from flask import Flask, Response, jsonify, request

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _clean_base_url(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return text.rstrip("/")

def _load_targets() -> Dict[str, str]:
    targets: Dict[str, str] = {
        "opencontext": _clean_base_url(
            os.getenv("MCP_OPENCONTEXT_URL", ""),
            "http://127.0.0.1:8000/mcp",
        ),
        "opengov": _clean_base_url(
            os.getenv("MCP_OPENGOV_URL", ""),
            "http://127.0.0.1:8100/mcp",
        ),
    }

    nys_url = str(os.getenv("MCP_OPENGOV_NYS_URL", "")).strip()
    if nys_url:
        targets["opengov-nys"] = _clean_base_url(nys_url, nys_url)

    raw_targets = str(os.getenv("MCP_GATEWAY_TARGETS_JSON", "")).strip()
    if raw_targets:
        try:
            parsed = json.loads(raw_targets)
            if isinstance(parsed, dict):
                for name, url in parsed.items():
                    key = str(name or "").strip().lower()
                    value = str(url or "").strip()
                    if not key or not value:
                        continue
                    targets[key] = _clean_base_url(value, value)
        except Exception:
            pass

    return targets


def create_app() -> Flask:
    app = Flask(__name__)
    targets = _load_targets()

    timeout_seconds = max(1.0, float(os.getenv("MCP_GATEWAY_TIMEOUT_SECONDS", "75")))

    @app.get("/_gateway/health")
    def health() -> Response:
        return jsonify(
            {
                "service": "mcp-path-gateway",
                "status": "ok",
                "targets": targets,
            }
        )

    @app.route("/<target>/mcp", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    @app.route("/<target>/mcp/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def proxy(target: str) -> Response:
        key = str(target or "").strip().lower()
        upstream_base = targets.get(key)
        if not upstream_base:
            return jsonify({"error": f"Unknown MCP target '{key}'"}), 404

        query = request.args.to_dict(flat=False)
        query_string = urlencode(query, doseq=True)
        upstream_url = f"{upstream_base}?{query_string}" if query_string else upstream_base

        outbound_headers: Dict[str, str] = {}
        for header_name, header_value in request.headers.items():
            lowered = str(header_name or "").strip().lower()
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            outbound_headers[str(header_name)] = str(header_value)

        try:
            upstream = requests.request(
                method=request.method,
                url=upstream_url,
                headers=outbound_headers,
                data=request.get_data(),
                allow_redirects=False,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            return (
                jsonify(
                    {
                        "error": "upstream_request_failed",
                        "target": key,
                        "upstream_url": upstream_base,
                        "details": str(exc),
                    }
                ),
                502,
            )

        response_headers = {}
        for name, value in upstream.headers.items():
            lowered = str(name or "").strip().lower()
            if lowered in HOP_BY_HOP_HEADERS:
                continue
            response_headers[str(name)] = str(value)

        return Response(
            upstream.content,
            status=upstream.status_code,
            headers=response_headers,
        )

    return app


if __name__ == "__main__":
    host = str(os.getenv("MCP_GATEWAY_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = int(os.getenv("MCP_GATEWAY_PORT", "8200"))
    gateway_app = create_app()
    gateway_app.run(host=host, port=port, debug=False)
