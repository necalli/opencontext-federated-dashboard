from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List

from .anthropic_agent_sdk_runtime import AnthropicAgentSDKRuntime
from .agent_runtime import AgentRuntime, DeterministicMCPRuntime
from .anthropic_mcp_connector import AnthropicMCPConnectorRuntime
from .server_registry import ServerRegistryService
from .skill_packages import SkillPackage, SkillPackageRegistry


class AgentOrchestrator:
    ONBOARDING_SKILL_ID = "mcp-server-onboarder"

    def __init__(self, registry: ServerRegistryService) -> None:
        self.registry = registry
        self.skill_registry = SkillPackageRegistry()
        self.runtime = AgentRuntime(
            agent_sdk_runtime=AnthropicAgentSDKRuntime(),
            connector_runtime=AnthropicMCPConnectorRuntime(),
            deterministic_runtime=DeterministicMCPRuntime(),
        )
        self.max_history = max(2, int(os.getenv("AGENT_SESSION_HISTORY", "12") or 12))
        self.default_system_prompt = str(
            os.getenv(
                "AGENT_SYSTEM_PROMPT",
                (
                    "You are an MCP orchestration assistant. Prefer direct facts from MCP tool outputs. "
                    "Route tool usage to the best-matching MCP server based on user intent: "
                    "for NYC/New York/Socrata requests, prefer nyc-opengov tools, especially get_data__nyc_opengov; "
                    "for New York State / NYS / MTA requests, prefer nys-opengov tools, especially get_data__nys_opengov from data.ny.gov; "
                    "for Boston/CKAN requests, prefer ckan__* tools from opencontext-main. "
                    "Use generic get_data only when alias-specific tools are unavailable. "
                    "When user asks to add/connect/onboard an MCP server, execute the MCP onboarding flow: "
                    "convert discovery intent into short search phrases (1-3 words) and call mcp_server_discover one phrase per call; "
                    "never use sentence-length discovery queries; "
                    "first discover candidates and recommend options, then wait for explicit user confirmation, "
                    "if candidate onboarding_mode is stdio_bridge_required, prefer mcp_stdio_bridge_start "
                    "(auto_onboard=true, confirmed=true) to start bridge and onboard automatically; "
                    "fallback to mcp_stdio_bridge_plan for manual bridge steps, then onboard local HTTP endpoint; "
                    "otherwise onboard directly. After onboarding, run MCP connection test, verify tools are listed for that server, "
                    "and report pass/fail with actionable remediation. "
                    "If user intent is ambiguous across servers, ask a clarifying question before deep analysis."
                ),
            )
        ).strip()
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}
        self.onboarding_scope_sticky_turns = max(0, int(os.getenv("AGENT_ONBOARDING_SCOPE_STICKY_TURNS", "6") or 6))
        self._onboarding_scope_state: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _contains_phrase(prompt: str, phrases: List[str]) -> bool:
        text = str(prompt or "").strip().casefold()
        return any(str(token or "").strip().casefold() in text for token in phrases)

    @classmethod
    def _is_onboarding_cancel_intent(cls, prompt: str) -> bool:
        return cls._contains_phrase(
            prompt,
            [
                "stop onboarding",
                "cancel onboarding",
                "exit onboarding",
                "done onboarding",
                "finish onboarding",
            ],
        )

    @classmethod
    def _looks_onboarding_followup(cls, prompt: str) -> bool:
        text = str(prompt or "").strip().casefold()
        if not text:
            return False
        if "mcp" in text:
            return True
        if re.search(r"@[a-z0-9][\w.-]*/[\w.-]+", text):
            return True
        return cls._contains_phrase(
            text,
            [
                "add",
                "onboard",
                "server",
                "discover",
                "search",
                "query",
                "candidate",
                "recommend",
                "endpoint",
                "auth",
                "no auth",
                "try another",
                "other candidate",
                "another term",
            ],
        )

    def _find_skill_package(self, skill_id: str) -> SkillPackage | None:
        target = str(skill_id or "").strip()
        if not target:
            return None
        for package in self.skill_registry.packages:
            if str(package.skill_id or "").strip() == target:
                return package
        return None

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        output: List[str] = []
        seen = set()
        for raw in values:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            output.append(value)
        return output

    def _apply_onboarding_scope_sticky(
        self,
        *,
        session_id: str,
        prompt: str,
        skill_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        scoped = dict(skill_context) if isinstance(skill_context, dict) else {}
        selected_ids = [str(v).strip() for v in (scoped.get("selected_skill_ids") or []) if str(v).strip()]
        selected_titles = [str(v).strip() for v in (scoped.get("selected_skill_titles") or []) if str(v).strip()]
        selected_skills = [dict(v) for v in (scoped.get("selected_skills") or []) if isinstance(v, dict)]
        allowed_patterns = [str(v).strip() for v in (scoped.get("allowed_tool_patterns") or []) if str(v).strip()]

        explicit_onboarding = self.ONBOARDING_SKILL_ID in selected_ids
        cancel_intent = self._is_onboarding_cancel_intent(prompt)
        state = self._onboarding_scope_state.get(session_id, {}) if session_id else {}
        carry_left = int(state.get("carry_turns_left") or 0)

        sticky_active = False
        if cancel_intent:
            carry_left = 0
        elif explicit_onboarding:
            sticky_active = True
            carry_left = int(self.onboarding_scope_sticky_turns)
        elif carry_left > 0 and self._looks_onboarding_followup(prompt):
            sticky_active = True
            carry_left = max(0, carry_left - 1)
        else:
            carry_left = 0

        if sticky_active:
            onboarding_package = self._find_skill_package(self.ONBOARDING_SKILL_ID)
            if onboarding_package is not None:
                if self.ONBOARDING_SKILL_ID not in selected_ids:
                    selected_ids.append(self.ONBOARDING_SKILL_ID)
                title = str(onboarding_package.title or self.ONBOARDING_SKILL_ID).strip()
                if title and title not in selected_titles:
                    selected_titles.append(title)
                if not any(
                    str(item.get("skill_id") or "").strip() == self.ONBOARDING_SKILL_ID for item in selected_skills
                ):
                    selected_skills.append(
                        {
                            "skill_id": self.ONBOARDING_SKILL_ID,
                            "title": title or self.ONBOARDING_SKILL_ID,
                            "description": str(onboarding_package.description or "").strip(),
                            "instruction": str(onboarding_package.instruction or "").strip(),
                            "tools": list(onboarding_package.allowed_tool_patterns),
                            "path": str(onboarding_package.path or "").strip(),
                        }
                    )
                allowed_patterns.extend(list(onboarding_package.allowed_tool_patterns))

        scoped["selected_skill_ids"] = self._dedupe(selected_ids)
        scoped["selected_skill_titles"] = self._dedupe(selected_titles)
        scoped["selected_skills"] = selected_skills
        scoped["allowed_tool_patterns"] = self._dedupe(allowed_patterns)
        scoped["allowed_tool_names"] = list(scoped["allowed_tool_patterns"])

        if sticky_active:
            addendum = str(scoped.get("system_prompt_addendum") or "").strip()
            sticky_line = (
                "Onboarding continuity is active for this session. "
                "Keep MCP onboarding tools in scope until onboarding is canceled or completed."
            )
            if sticky_line not in addendum:
                scoped["system_prompt_addendum"] = (
                    f"{addendum}\n{sticky_line}".strip() if addendum else sticky_line
                )

        if session_id:
            self._onboarding_scope_state[session_id] = {
                "active": bool(sticky_active),
                "carry_turns_left": int(carry_left),
            }
        return scoped

    def run_turn(
        self,
        *,
        message: str,
        session_id: str | None,
        prefer_connector: bool,
        runtime_preference: str = "",
        event_sink: Any | None = None,
    ) -> Dict[str, Any]:
        prompt = str(message or "").strip()
        if not prompt:
            raise ValueError("message is required")

        current_session_id = str(session_id or "").strip() or str(uuid.uuid4())
        history = list(self.sessions.get(current_session_id, []))
        servers = [row for row in self.registry.list_servers() if bool(row.get("enabled", True))]
        self.skill_registry.refresh()
        resolved_skill_context = self.skill_registry.resolve_for_message(prompt)
        skill_context = self._apply_onboarding_scope_sticky(
            session_id=current_session_id,
            prompt=prompt,
            skill_context=resolved_skill_context,
        )

        result = self.runtime.run(
            message=prompt,
            servers=servers,
            history=history,
            session_id=current_session_id,
            prefer_connector=prefer_connector,
            system_prompt=self.default_system_prompt,
            skill_context=skill_context,
            runtime_preference=runtime_preference,
            event_sink=event_sink,
        )

        assistant_message = str(result.get("message") or "").strip()
        updated = history + [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant_message},
        ]
        if len(updated) > self.max_history:
            updated = updated[-self.max_history :]
        self.sessions[current_session_id] = updated

        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        meta["session_id"] = current_session_id
        meta["history_size"] = len(updated)
        meta["server_count"] = len(servers)

        return {
            "message": assistant_message,
            "meta": meta,
            "session_id": current_session_id,
        }
