"""Shared base for backends that talk to a REST API directly via httpx,
rather than through googletrans/deep-translator/translators.

Each call builds a *fresh* httpx.AsyncClient rather than caching one --
deliberately, per the lesson learned with googletrans (see
translation/providers/googletrans_backend.py's module docstring): an
async HTTP client's connection-pool internals bind to whichever event
loop is running the first time they're used, and this project processes
each chunk via a fresh `asyncio.run()` call, so a cached client would
eventually be reused across a closed loop. These backends are lower
traffic / optional (public community mirrors, or the paid AI backends),
so the extra per-call client construction is a non-issue -- the
googletrans backend still gets the more careful per-loop-tracked reuse
because it's the highest-traffic default engine.
"""
import asyncio
import typing as t

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - httpx ships as a googletrans dependency
    httpx = None
    _HTTPX_AVAILABLE = False

from ..errors import PermanentTranslationError, TransientTranslationError


class HttpJsonBackend:
    """Mixin providing `_request_json()`. Not a TranslationBackend
    itself -- concrete backends still implement `translate()`."""

    request_timeout: float = 15.0

    @staticmethod
    def httpx_available() -> bool:
        return _HTTPX_AVAILABLE

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: t.Optional[dict] = None,
        json_body: t.Optional[dict] = None,
        headers: t.Optional[dict] = None,
        permanent_status_codes: t.Optional[t.FrozenSet[int]] = None,
    ) -> t.Any:
        if not _HTTPX_AVAILABLE:
            raise PermanentTranslationError("httpx is not installed")
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.request(
                    method, url, params=params, json=json_body, headers=headers
                )
        except asyncio.TimeoutError as exc:
            raise TransientTranslationError(f"{getattr(self, 'name', 'http')} timed out") from exc
        except Exception as exc:
            raise TransientTranslationError(str(exc)) from exc

        # Some callers (e.g. an AI backend rejecting a bad API key with
        # 401) want specific status codes treated as non-retryable
        # rather than the default "any 4xx/5xx is worth retrying".
        if permanent_status_codes and response.status_code in permanent_status_codes:
            raise PermanentTranslationError(
                f"{getattr(self, 'name', 'http')} returned HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise TransientTranslationError(
                f"{getattr(self, 'name', 'http')} returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except Exception as exc:
            raise TransientTranslationError(
                f"{getattr(self, 'name', 'http')} returned a non-JSON response"
            ) from exc
