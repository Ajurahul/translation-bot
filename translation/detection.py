"""Multi-engine language detection.

Previously, detection relied solely on a single network call to
googletrans' `.detect()` -- any network hiccup, rate limit, or blocked
endpoint made that call fail, and with no fallback engine the caller just
got back "NA". That failure mode turned out to be common enough that
detection was returning "NA" most of the time.

This module tries a short chain of *independent* detectors -- an offline,
network-free statistical detector first (so a googletrans outage/rate
limit can no longer take detection down at all), then googletrans as a
network-based fallback/backstop -- across one or more text samples, and
only gives up (returning "NA") once every combination has failed.
"""
import asyncio
import logging
import typing as t

logger = logging.getLogger(__name__)

try:
    import langdetect
    from langdetect import DetectorFactory as _LangDetectFactory

    # Deterministic results -- langdetect's default is randomized per-call,
    # which is undesirable for something we may retry.
    _LangDetectFactory.seed = 0
    _LANGDETECT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    langdetect = None
    _LANGDETECT_AVAILABLE = False

try:
    from asyncio import Timeout
except ImportError:  # Python < 3.11
    from async_timeout import Timeout

try:
    from googletrans import Translator as _GoogleTransClient

    _GOOGLETRANS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when dependency missing
    _GoogleTransClient = None
    _GOOGLETRANS_AVAILABLE = False

_SERVICE_URLS = [
    "translate.google.com",
    "translate.google.co.in",
    "translate.google.co.kr",
    "translate.google.co.uk",
    "translate.google.ca",
    "translate.google.com.au",
    "translate.google.de",
    "translate.google.fr",
    "translate.google.es",
    "translate.google.it",
    "translate.google.co.jp",
]

# Too short a sample makes every detector unreliable (both statistical
# and network-based ones); skip samples below this length rather than
# waste a call/attempt on them.
_MIN_SAMPLE_CHARS = 8
_UNUSABLE_CODES = frozenset({"un", "unknown", ""})

_googletrans_client = None
_googletrans_client_lock = asyncio.Lock()


async def _detect_with_langdetect(sample: str) -> t.Optional[str]:
    if not _LANGDETECT_AVAILABLE:
        return None

    def _work() -> t.Optional[str]:
        try:
            return langdetect.detect(sample)
        except Exception:
            return None

    try:
        code = await asyncio.wait_for(asyncio.to_thread(_work), timeout=5.0)
    except Exception:
        return None
    return str(code).lower() if code else None


async def _get_googletrans_client():
    global _googletrans_client
    if _googletrans_client is not None:
        return _googletrans_client
    async with _googletrans_client_lock:
        if _googletrans_client is None:
            _googletrans_client = _GoogleTransClient(
                timeout=Timeout(10.0),
                raise_exception=True,
                service_urls=_SERVICE_URLS,
            )
    return _googletrans_client


async def _detect_with_googletrans(sample: str) -> t.Optional[str]:
    if not _GOOGLETRANS_AVAILABLE:
        return None
    try:
        client = await _get_googletrans_client()
        result = await asyncio.wait_for(client.detect(sample), timeout=10.0)
    except Exception:
        return None
    code = getattr(result, "lang", None)
    if isinstance(code, list) and code:
        code = code[0]
    return str(code).lower() if code else None


# Ordered cheapest/most-reliable first: langdetect needs no network call
# at all, so it can never be taken down by a provider outage or rate
# limit -- googletrans is kept as a fallback/backstop for the cases
# langdetect gets wrong (it's weaker on very short or mixed-script text).
_DETECTORS: t.Tuple[t.Tuple[str, t.Callable[[str], t.Awaitable[t.Optional[str]]]], ...] = (
    ("langdetect", _detect_with_langdetect),
    ("googletrans", _detect_with_googletrans),
)


async def detect_language_code(
    text: t.Optional[str] = None,
    samples: t.Optional[t.List[str]] = None,
) -> str:
    """Try every available detector against one or more text samples, in
    order, returning the first confident result. Returns "NA" only once
    every (sample, detector) combination has been exhausted.

    Trying several samples matters as much as trying several engines --
    a single sample that's mid-sentence, mostly punctuation/numbers, or
    otherwise ambiguous can trip up an otherwise-healthy detector even
    though a different slice of the same document would have worked
    fine.
    """
    candidate_samples = [s for s in (samples if samples else [text]) if s and str(s).strip()]
    if not candidate_samples:
        return "NA"

    for sample in candidate_samples:
        cleaned = str(sample).strip()
        if len(cleaned) < _MIN_SAMPLE_CHARS:
            continue
        for engine_name, detector in _DETECTORS:
            try:
                code = await detector(cleaned)
            except Exception:
                code = None
            if code and code not in _UNUSABLE_CODES:
                return code
            logger.debug("Language detector %s found nothing usable for this sample", engine_name)

    return "NA"
