# coding=utf-8
"""
AI 客户端模块

统一接入共享 llm_client，复用 core-common-tools 的 provider 链路。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_SHARED_LLM_CALLABLE: Optional[Callable[..., str]] = None
_SHARED_LLM_IMPORT_ERROR: Optional[str] = None


def _get_shared_llm_callable() -> Optional[Callable[..., str]]:
    global _SHARED_LLM_CALLABLE, _SHARED_LLM_IMPORT_ERROR
    if _SHARED_LLM_CALLABLE is not None:
        return _SHARED_LLM_CALLABLE
    if _SHARED_LLM_IMPORT_ERROR is not None:
        return None

    client_path = (
        Path(__file__).resolve().parents[3]
        / "core-common-tools"
        / "core_common_tools"
        / "llm_client.py"
    )
    try:
        spec = importlib.util.spec_from_file_location("trendradar_shared_llm_client", client_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载共享 LLM client: {client_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        llm_call = getattr(module, "chat_completion_or_raise", None)
        if not callable(llm_call):
            raise ImportError(f"共享 LLM client 缺少 chat_completion_or_raise: {client_path}")
        _SHARED_LLM_CALLABLE = llm_call
        return _SHARED_LLM_CALLABLE
    except Exception as exc:
        _SHARED_LLM_IMPORT_ERROR = str(exc)
        return None


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text", "") or "").strip()
                if text:
                    parts.append(text)
            else:
                text = str(item or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _messages_to_prompt(messages: List[Dict[str, Any]]) -> tuple[str, str]:
    system_parts: List[str] = []
    prompt_parts: List[str] = []

    for message in messages or []:
        role = str(message.get("role", "user") or "user").strip().lower()
        content = _normalize_message_content(message.get("content", ""))
        if not content:
            continue

        if role in {"system", "developer"}:
            system_parts.append(content)
            continue

        if role == "user" and not prompt_parts:
            prompt_parts.append(content)
            continue

        prompt_parts.append(f"[{role}]\n{content}")

    system_prompt = "\n\n".join(system_parts).strip()
    prompt = "\n\n".join(prompt_parts).strip()
    return system_prompt, prompt


class AIClient:
    """统一的 AI 客户端（基于共享 llm_client）"""

    def __init__(self, config: Dict[str, Any]):
        self.model = config.get("MODEL", "deepseek/deepseek-chat")
        self.api_key = config.get("API_KEY", "")
        self.api_base = config.get("API_BASE", "")
        self.temperature = config.get("TEMPERATURE", 1.0)
        self.max_tokens = config.get("MAX_TOKENS", 5000)
        self.timeout = config.get("TIMEOUT", 120)
        self.num_retries = config.get("NUM_RETRIES", 2)
        self.fallback_models = config.get("FALLBACK_MODELS", [])

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        llm_call = _get_shared_llm_callable()
        if not llm_call:
            raise RuntimeError(
                "共享 llm_client 不可用: "
                f"{_SHARED_LLM_IMPORT_ERROR or 'unknown import error'}"
            )

        system_prompt, prompt = _messages_to_prompt(messages)
        if not prompt:
            return ""

        return llm_call(
            prompt,
            system_prompt=system_prompt or None,
            model=kwargs.get("model", self.model),
            temperature=kwargs.get("temperature", self.temperature),
            base_url=kwargs.get("base_url", self.api_base),
            api_key=kwargs.get("api_key", self.api_key),
            timeout=kwargs.get("timeout", self.timeout),
        )

    def validate_config(self) -> tuple[bool, str]:
        llm_call = _get_shared_llm_callable()
        if not llm_call:
            return False, f"共享 llm_client 不可用：{_SHARED_LLM_IMPORT_ERROR or 'unknown import error'}"
        return True, ""
