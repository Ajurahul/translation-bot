"""LLM-based translation backends.

These call a general-purpose chat/completion API and ask it to translate,
rather than a dedicated translation service. They're optional and
strictly opt-in:

  * `is_available()` returns False unless the relevant API key env var is
    set, so they never appear as selectable engines (and are never
    silently paid for) on a deployment that hasn't configured one.
  * They are NOT included in the default `auto_engine_order` -- Auto mode
    must never spend a configured API key's money without the operator
    explicitly choosing to. An operator who wants an AI engine in the
    Auto rotation can add its id to `auto_engine_order` in
    config/translation_settings.json themselves.
  * `capabilities.requires_api_key = True` / `free_without_key = False`,
    consistent with how every other paid/keyed provider in this project
    is represented (see translation/base.py).

Env vars:
    ANTHROPIC_API_KEY          - enables "ai-claude"
    ANTHROPIC_TRANSLATE_MODEL  - optional, defaults to a small/cheap model
    OPENAI_API_KEY             - enables "ai-openai"
    OPENAI_TRANSLATE_MODEL     - optional, defaults to a small/cheap model

No key is ever logged, hard-coded, or persisted to
config/translation_settings.json -- only read from the environment at
call time.
"""
import os

from ..base import ProviderCapabilities, TranslationBackend
from ..errors import PermanentTranslationError
from .http_backend import HttpJsonBackend

_TRANSLATION_INSTRUCTION = (
    "You are a translation engine embedded in an application. Translate the "
    "user's text from {source} to {target}. Preserve the original meaning, "
    "tone, formatting, and line breaks as closely as possible. Output ONLY "
    "the translated text -- no explanations, no quotation marks, no "
    "commentary of any kind."
)

# A rejected/invalid API key should fail the job immediately rather than
# be retried three times and burn the retry budget for no reason.
_PERMANENT_STATUS_CODES = frozenset({401, 403})


class AnthropicAIBackend(HttpJsonBackend, TranslationBackend):
    name = "ai-claude"
    display_name = "AI - Claude"
    capabilities = ProviderCapabilities(
        requires_api_key=True, free_without_key=False, supports_batch=False
    )
    request_timeout = 30.0

    def is_available(self) -> bool:
        return self.httpx_available() and bool(os.getenv("ANTHROPIC_API_KEY"))

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise PermanentTranslationError("ANTHROPIC_API_KEY is not configured")
        model = os.getenv("ANTHROPIC_TRANSLATE_MODEL", "claude-haiku-4-5-20251001")

        data = await self._request_json(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json_body={
                "model": model,
                "max_tokens": 4096,
                "system": _TRANSLATION_INSTRUCTION.format(
                    source=source_language or "the detected source language",
                    target=target_language,
                ),
                "messages": [{"role": "user", "content": str(text)}],
            },
            permanent_status_codes=_PERMANENT_STATUS_CODES,
        )

        content = data.get("content", []) if isinstance(data, dict) else []
        parts = [block.get("text", "") for block in content if block.get("type") == "text"]
        result = "".join(parts).strip()
        if not result:
            raise PermanentTranslationError("Anthropic API returned no translated text")
        return result


class OpenAIBackend(HttpJsonBackend, TranslationBackend):
    name = "ai-openai"
    display_name = "AI - OpenAI"
    capabilities = ProviderCapabilities(
        requires_api_key=True, free_without_key=False, supports_batch=False
    )
    request_timeout = 30.0

    def is_available(self) -> bool:
        return self.httpx_available() and bool(os.getenv("OPENAI_API_KEY"))

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise PermanentTranslationError("OPENAI_API_KEY is not configured")
        model = os.getenv("OPENAI_TRANSLATE_MODEL", "gpt-4o-mini")

        data = await self._request_json(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json_body={
                "model": model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": _TRANSLATION_INSTRUCTION.format(
                            source=source_language or "the detected source language",
                            target=target_language,
                        ),
                    },
                    {"role": "user", "content": str(text)},
                ],
            },
            permanent_status_codes=_PERMANENT_STATUS_CODES,
        )

        try:
            result = str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise PermanentTranslationError("OpenAI API returned an unexpected response") from exc

        if not result:
            raise PermanentTranslationError("OpenAI API returned no translated text")
        return result


def register(reg) -> None:
    reg.register("ai-claude", AnthropicAIBackend, display_name="AI - Claude")
    reg.register("ai-openai", OpenAIBackend, display_name="AI - OpenAI")
