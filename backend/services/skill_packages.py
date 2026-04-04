from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = os.getenv("AGENT_SKILLS_DIR", str(PROJECT_ROOT / "agent_skills"))
RUNTIME_CONFIG_FILES = ("runtime.json", "skill.runtime.json")


@dataclass(frozen=True)
class SkillPackage:
    skill_id: str
    title: str
    description: str
    instruction: str
    trigger_keywords: Tuple[str, ...]
    allowed_tool_patterns: Tuple[str, ...]
    always_on: bool
    enabled: bool
    path: str


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_keyword_list(value: Any) -> List[str]:
    if isinstance(value, list):
        raw = [str(item or "").strip().lower() for item in value]
    elif isinstance(value, str):
        raw = [part.strip().lower() for part in value.split(",")]
    else:
        raw = []
    output: List[str] = []
    seen = set()
    for item in raw:
        normalized = _normalize_text(item).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _normalize_tools(value: Any) -> List[str]:
    if isinstance(value, list):
        values = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    else:
        values = []
    output: List[str] = []
    seen = set()
    for item in values:
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    text = str(raw or "")
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    end_index = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index < 1:
        return {}, text.strip()

    metadata: Dict[str, Any] = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    body = "\n".join(lines[end_index + 1 :]).strip()
    return metadata, body


def _extract_markdown_tools(body: str) -> List[str]:
    lines = body.splitlines()
    in_tools_section = False
    tools: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_tools_section = stripped.lower().startswith("## preferred tools")
            continue
        if not in_tools_section:
            continue
        if not stripped.startswith("-"):
            if stripped:
                continue
            break
        matches = re.findall(r"`([^`]+)`", stripped)
        candidate = matches[0] if matches else stripped[1:].strip()
        candidate = str(candidate or "").strip()
        if candidate and candidate not in tools:
            tools.append(candidate)
    return tools


def _load_runtime_config(skill_dir: Path) -> Dict[str, Any]:
    for filename in RUNTIME_CONFIG_FILES:
        candidate = skill_dir / filename
        if candidate.exists():
            payload = _read_json(candidate)
            if payload:
                return payload
    return {}


def _default_keywords(skill_id: str, title: str) -> List[str]:
    parts = re.split(r"[_\-\s]+", f"{skill_id} {title}")
    output: List[str] = []
    seen = set()
    for part in parts:
        value = str(part or "").strip().lower()
        if len(value) < 3 or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _load_skill(skill_dir: Path) -> Optional[SkillPackage]:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    raw = _read_text(skill_file)
    if not raw:
        return None
    frontmatter, body = _parse_frontmatter(raw)
    runtime = _load_runtime_config(skill_dir)

    skill_id = skill_dir.name
    title = _normalize_text(frontmatter.get("name") or runtime.get("name") or skill_id)
    description = _normalize_text(frontmatter.get("description") or runtime.get("description") or "")
    instruction = str(body or "").strip()

    enabled = bool(runtime.get("enabled", True))
    always_on = bool(runtime.get("always_on", False))

    tools = _normalize_tools(runtime.get("tools"))
    if not tools:
        tools = _normalize_tools(frontmatter.get("tools"))
    if not tools:
        tools = _extract_markdown_tools(body)

    keywords = _normalize_keyword_list(runtime.get("trigger_keywords"))
    if not keywords:
        keywords = _normalize_keyword_list(frontmatter.get("trigger_keywords"))
    if not keywords:
        keywords = _default_keywords(skill_id, title)

    return SkillPackage(
        skill_id=skill_id,
        title=title or skill_id,
        description=description,
        instruction=instruction,
        trigger_keywords=tuple(keywords),
        allowed_tool_patterns=tuple(tools),
        always_on=always_on,
        enabled=enabled,
        path=str(skill_file),
    )


class SkillPackageRegistry:
    def __init__(self, skills_dir: str | None = None) -> None:
        configured = Path(_normalize_text(skills_dir or DEFAULT_SKILLS_DIR))
        if not configured.is_absolute():
            configured = PROJECT_ROOT / configured
        self.skills_dir = configured
        self.packages: Tuple[SkillPackage, ...] = tuple()
        self.refresh()

    def refresh(self) -> None:
        root = self.skills_dir
        if not root.exists() or not root.is_dir():
            self.packages = tuple()
            return
        loaded: List[SkillPackage] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill = _load_skill(child)
            if skill is not None:
                loaded.append(skill)
        self.packages = tuple(loaded)

    @staticmethod
    def _is_mcp_onboarding_intent(text: str) -> bool:
        value = _normalize_text(text).lower()
        if not value:
            return False

        # Covers prompts like "add @org/server-mcp" even if "mcp server" is not explicit.
        has_package_ref = bool(re.search(r"@[a-z0-9][\w.-]*/[\w.-]+", value))
        has_mcp_signal = "mcp" in value or has_package_ref

        action_tokens = (
            "add",
            "onboard",
            "register",
            "connect",
            "integrate",
            "enable",
            "disable",
            "remove",
            "delete",
            "test",
            "verify",
            "discover",
            "recommend",
        )
        has_action = any(token in value for token in action_tokens)
        return bool(has_mcp_signal and has_action)

    def resolve_for_message(self, message: str, *, max_skills: int = 3) -> Dict[str, Any]:
        text = _normalize_text(message).lower()
        if not text:
            return {
                "selected_skill_ids": [],
                "selected_skill_titles": [],
                "selected_skills": [],
                "allowed_tool_patterns": [],
                "allowed_tool_names": [],
                "system_prompt_addendum": "",
            }

        active = [item for item in self.packages if item.enabled]
        if not active:
            return {
                "selected_skill_ids": [],
                "selected_skill_titles": [],
                "selected_skills": [],
                "allowed_tool_patterns": [],
                "allowed_tool_names": [],
                "system_prompt_addendum": "",
            }

        always_on = [item for item in active if item.always_on]
        scored: List[Tuple[int, SkillPackage]] = []
        onboarding_intent = self._is_mcp_onboarding_intent(text)
        for package in active:
            score = 0
            for keyword in package.trigger_keywords:
                key = str(keyword or "").strip().lower()
                if key and key in text:
                    score += 1
            if onboarding_intent and package.skill_id == "mcp-server-onboarder":
                # Force onboarding skill into top-N selection for add/register/discover MCP requests.
                score += 1000
            if score > 0:
                scored.append((score, package))

        scored.sort(key=lambda row: (-row[0], row[1].skill_id))
        selected: List[SkillPackage] = []
        seen = set()
        for package in always_on:
            if package.skill_id not in seen:
                seen.add(package.skill_id)
                selected.append(package)
        for _, package in scored:
            if package.skill_id in seen:
                continue
            selected.append(package)
            seen.add(package.skill_id)
            if len(selected) >= max(1, int(max_skills)):
                break
        if not selected:
            return {
                "selected_skill_ids": [],
                "selected_skill_titles": [],
                "selected_skills": [],
                "allowed_tool_patterns": [],
                "allowed_tool_names": [],
                "system_prompt_addendum": "",
            }

        allowed_patterns: List[str] = []
        for package in selected:
            for pattern in package.allowed_tool_patterns:
                if pattern and pattern not in allowed_patterns:
                    allowed_patterns.append(pattern)

        prompt_lines = ["Active skill packages:"]
        for package in selected:
            guidance = package.description or "Follow skill instructions and return grounded results."
            prompt_lines.append(f"- {package.title}: {guidance}")
        if allowed_patterns:
            prompt_lines.append(
                "Tool scope policy: Prefer only these MCP tools unless user explicitly requests broader tooling."
            )
            for pattern in allowed_patterns:
                prompt_lines.append(f"- {pattern}")

        selected_skills = [
            {
                "skill_id": package.skill_id,
                "title": package.title,
                "description": package.description,
                "instruction": package.instruction,
                "tools": list(package.allowed_tool_patterns),
                "path": package.path,
            }
            for package in selected
        ]

        return {
            "selected_skill_ids": [package.skill_id for package in selected],
            "selected_skill_titles": [package.title for package in selected],
            "selected_skills": selected_skills,
            "allowed_tool_patterns": allowed_patterns,
            "allowed_tool_names": list(allowed_patterns),
            "system_prompt_addendum": "\n".join(prompt_lines).strip(),
        }


def tool_allowed(tool_name: str, allowed_patterns: List[str]) -> bool:
    candidate = str(tool_name or "").strip()
    if not candidate:
        return False
    if not allowed_patterns:
        return True
    for pattern in allowed_patterns:
        p = str(pattern or "").strip()
        if not p:
            continue
        if fnmatch.fnmatch(candidate, p):
            return True
    return False
