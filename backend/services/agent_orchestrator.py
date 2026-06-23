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
    RENTAL_SKILL_ID = "rental_dashboard_ops"

    def __init__(self, registry: ServerRegistryService) -> None:
        self.registry = registry
        self.skill_registry = SkillPackageRegistry()
        self.runtime = AgentRuntime(
            agent_sdk_runtime=AnthropicAgentSDKRuntime(server_registry=registry),
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
                    "For Airbnb/rental listing searches, listing URL ingests, listing reviews, photos, and rental comparisons, prefer rental-dashboard tools and preserve rental context across short follow-up turns. "
                    "Do not treat empty local review results as proof Airbnb has no reviews. "
                    "Use generic get_data only when alias-specific tools are unavailable. "
                    "If user intent is ambiguous across servers, ask a clarifying question before deep analysis."
                ),
            )
        ).strip()
        self.onboarding_system_prompt_addendum = str(
            os.getenv(
                "AGENT_ONBOARDING_SYSTEM_PROMPT_ADDENDUM",
                (
                    "Onboarding mode is active. Use MCP onboarding tools to ground every operational claim. "
                    "Start each onboarding cycle with mcp_servers_list, use mcp_server_discover with short queries, "
                    "and do not claim tools are disconnected/unavailable unless a current-turn onboarding tool call "
                    "returns that failure."
                ),
            )
        ).strip()
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}
        self.onboarding_scope_sticky_turns = max(0, int(os.getenv("AGENT_ONBOARDING_SCOPE_STICKY_TURNS", "6") or 6))
        self.onboarding_scope_strict_continuity = str(
            os.getenv("AGENT_ONBOARDING_SCOPE_STRICT_CONTINUITY", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        self.onboarding_prompt_injection_enabled = str(
            os.getenv("AGENT_ONBOARDING_PROMPT_INJECTION_ENABLED", "true")
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        self._onboarding_scope_state: Dict[str, Dict[str, Any]] = {}
        self.rental_scope_sticky_turns = max(0, int(os.getenv("AGENT_RENTAL_SCOPE_STICKY_TURNS", "8") or 8))
        self._rental_scope_state: Dict[str, Dict[str, Any]] = {}

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
    def _is_onboarding_complete_intent(cls, prompt: str) -> bool:
        return cls._contains_phrase(
            prompt,
            [
                "onboarding complete",
                "onboarding completed",
                "completed onboarding",
                "we are done onboarding",
                "finished onboarding",
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

    @classmethod
    def _looks_rental_followup(cls, prompt: str) -> bool:
        text = re.sub(r"[^a-z0-9\s'-]", " ", str(prompt or "").strip().casefold())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return False
        if text in {
            "continue",
            "continue please",
            "keep going",
            "proceed",
            "check again",
            "poll again",
            "try again",
            "what is the status",
            "what's the status",
            "are they done",
        }:
            return True
        return cls._contains_phrase(
            text,
            [
                "those listings",
                "these listings",
                "each listing",
                "the listings",
                "the ingest jobs",
                "those jobs",
                "job status",
                "get the reviews",
                "fetch the reviews",
                "listing reviews",
                "listing photos",
                "listing details",
                "ingest them",
                "poll the jobs",
            ],
        )

    def _rental_skill_context(self) -> Dict[str, Any] | None:
        package = self._find_skill_package(self.RENTAL_SKILL_ID)
        if package is None:
            return None
        tools = list(package.allowed_tool_patterns)
        title = str(package.title or self.RENTAL_SKILL_ID).strip()
        prompt_lines = [
            "Active skill packages:",
            f"- {title}: {package.description or 'Follow rental-dashboard MCP workflow instructions.'}",
        ]
        if tools:
            prompt_lines.append("Tool scope policy: Prefer only these MCP tools unless user explicitly requests broader tooling.")
            prompt_lines.extend(f"- {tool}" for tool in tools)
        return {
            "selected_skill_ids": [self.RENTAL_SKILL_ID],
            "selected_skill_titles": [title],
            "selected_skills": [
                {
                    "skill_id": self.RENTAL_SKILL_ID,
                    "title": title,
                    "description": str(package.description or "").strip(),
                    "instruction": str(package.instruction or "").strip(),
                    "tools": tools,
                    "path": str(package.path or "").strip(),
                }
            ],
            "allowed_tool_patterns": tools,
            "allowed_tool_names": list(tools),
            "system_prompt_addendum": "\n".join(prompt_lines).strip(),
        }

    def _apply_rental_scope_sticky(
        self,
        *,
        session_id: str,
        prompt: str,
        skill_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        scoped = dict(skill_context) if isinstance(skill_context, dict) else {}
        selected_ids = [str(value).strip() for value in (scoped.get("selected_skill_ids") or []) if str(value).strip()]
        state = self._rental_scope_state.get(session_id, {}) if session_id else {}

        if self.RENTAL_SKILL_ID in selected_ids:
            if session_id:
                self._rental_scope_state[session_id] = {"turns_left": self.rental_scope_sticky_turns}
            return scoped

        if selected_ids or not self._looks_rental_followup(prompt):
            if session_id:
                self._rental_scope_state.pop(session_id, None)
            return scoped

        turns_left = int(state.get("turns_left") or 0)
        if turns_left <= 0:
            return scoped
        rental_context = self._rental_skill_context()
        if rental_context is None:
            return scoped

        rental_context["system_prompt_addendum"] = (
            f"{rental_context.get('system_prompt_addendum', '')}\n"
            "Rental workflow continuity is active for this session. Resume the saved job ids from conversation history; do not queue duplicate work."
        ).strip()
        if session_id:
            self._rental_scope_state[session_id] = {"turns_left": turns_left - 1}
        return rental_context

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
        complete_intent = self._is_onboarding_complete_intent(prompt)
        followup_intent = self._looks_onboarding_followup(prompt)
        state = self._onboarding_scope_state.get(session_id, {}) if session_id else {}
        state_active = bool(state.get("active"))
        carry_left = int(state.get("carry_turns_left") or 0)

        sticky_active = False
        if cancel_intent or complete_intent:
            state_active = False
            carry_left = 0
        elif explicit_onboarding:
            sticky_active = True
            state_active = True
            carry_left = int(self.onboarding_scope_sticky_turns)
        elif state_active and carry_left > 0:
            if self.onboarding_scope_strict_continuity:
                if followup_intent:
                    sticky_active = True
                    carry_left = int(self.onboarding_scope_sticky_turns)
                elif selected_ids:
                    sticky_active = False
                    state_active = False
                    carry_left = 0
                else:
                    sticky_active = True
                    carry_left = max(0, carry_left - 1)
                    if carry_left <= 0:
                        sticky_active = False
                        state_active = False
            elif followup_intent:
                sticky_active = True
                carry_left = max(0, carry_left - 1)
            else:
                state_active = False
                carry_left = 0
        elif carry_left > 0 and followup_intent:
            sticky_active = True
            state_active = True
            carry_left = max(0, carry_left - 1)
        else:
            state_active = False
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
            addendum_lines: List[str] = []
            if self.onboarding_prompt_injection_enabled and self.onboarding_system_prompt_addendum:
                addendum_lines.append(self.onboarding_system_prompt_addendum)
            addendum_lines.append(
                "Onboarding continuity is active for this session. "
                "Keep MCP onboarding tools in scope until onboarding is canceled or completed."
            )
            merged = addendum
            for line in addendum_lines:
                if line and line not in merged:
                    merged = f"{merged}\n{line}".strip() if merged else line
            scoped["system_prompt_addendum"] = merged

        if session_id:
            self._onboarding_scope_state[session_id] = {
                "active": bool(sticky_active and carry_left > 0),
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
        all_servers = [row for row in self.registry.list_servers() if isinstance(row, dict)]
        enabled_servers = [row for row in all_servers if bool(row.get("enabled", True))]
        self.skill_registry.refresh()
        resolved_skill_context = self.skill_registry.resolve_for_message(prompt)
        skill_context = self._apply_onboarding_scope_sticky(
            session_id=current_session_id,
            prompt=prompt,
            skill_context=resolved_skill_context,
        )
        skill_context = self._apply_rental_scope_sticky(
            session_id=current_session_id,
            prompt=prompt,
            skill_context=skill_context,
        )

        result = self.runtime.run(
            message=prompt,
            servers=enabled_servers,
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
        meta["server_count"] = len(enabled_servers)
        meta["enabled_server_count"] = len(enabled_servers)
        meta["total_server_count"] = len(all_servers)
        debug = meta.get("debug") if isinstance(meta.get("debug"), dict) else {}
        if debug:
            debug["enabled_server_count"] = len(enabled_servers)
            debug["total_server_count"] = len(all_servers)

        return {
            "message": assistant_message,
            "meta": meta,
            "session_id": current_session_id,
        }
