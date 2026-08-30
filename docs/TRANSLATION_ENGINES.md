# Translation Engines

`/translate` now has a `translation_engine` option (autocomplete, since the
provider list is longer than Discord's 25-choice static limit would allow)
with three kinds of value:

| Value | Meaning |
|---|---|
| `Default` | Use the bot-wide default engine, as currently configured by an admin. |
| `Auto` | Intelligently pick a healthy engine and stick with it for the whole job. |
| A specific engine (e.g. `Translators - Bing`) | Use *only* that engine. If it fails, the job fails — it never silently falls back to something else. |

## Supported engines

| Internal id | Display name | Key required | Notes |
|---|---|---|---|
| `googletrans` | GoogleTrans | No | `googletrans==4.0.2`. Client is reused across calls within an event loop (see Performance below). |
| `deep-google` | Deep Translator - Google | No | `deep-translator`'s `GoogleTranslator`. |
| `deep-mymemory` | Deep Translator - MyMemory | No | `deep-translator`'s `MyMemoryTranslator`. Small daily quota per IP on the anonymous tier. |
| `translators-google` | Translators - Google | No | Via the `translators` package (unofficial endpoint, see below). |
| `translators-bing` | Translators - Bing | No | Same provider the previous implementation called "bing". |
| `translators-mymemory` | Translators - MyMemory | No | |
| `translators-yandex` | Translators - Yandex | No | |
| `translators-apertium` | Translators - Apertium | No | Open-source engine, narrower language coverage than the others. |
| `translators-reverso` | Translators - Reverso | No | Via the `translators` package. |
| `lingva` | Lingva Translate | No | Open-source, no-key front end for Google Translate; direct REST call (`translation/providers/lingva_backend.py`), no `translators`/`deep-translator` dependency. Configurable mirror via `LINGVA_URL`. |
| `libretranslate` | LibreTranslate | No | Open-source; direct REST call to a public mirror by default (`translation/providers/libretranslate_backend.py`) — implemented directly rather than through `deep-translator`'s `LibreTranslator`, which (as shipped in `deep-translator==1.11.4`) unconditionally requires an API key even against mirrors that don't. Configurable via `LIBRETRANSLATE_URL` / `LIBRETRANSLATE_API_KEY`. |
| `ai-claude` | AI - Claude | **Yes** (`ANTHROPIC_API_KEY`) | LLM-based translation via the Anthropic Messages API. Paid, opt-in, not in the default Auto rotation — see "AI translation mode" below. |
| `ai-openai` | AI - OpenAI | **Yes** (`OPENAI_API_KEY`) | LLM-based translation via the OpenAI Chat Completions API. Same opt-in rules as `ai-claude`. |

The free entries all work by talking to a provider's public web-translate
endpoint or a community-run open-source mirror the same way a browser
would — there's no official paid API involved, but that also means
they're unofficial/community endpoints that can change or start
rate-limiting without notice. Several `translators` providers were
deliberately **not** exposed:

* `alibaba`, `baidu`, `caiyun`, `deepl`, `niutrans`, `sysTran`, `volcEngine`,
  `youdao`, `papago`, and most of the remaining catalogue require paid
  accounts/API keys for reliable bulk use, or their free-tier behaviour
  through `translators` couldn't be verified as stable enough to ship here.
* `argos` needs a locally-installed language model per language pair, which
  doesn't fit a Discord bot translating arbitrary novel text.

Adding a properly credentialed provider later (e.g. an official DeepL key)
is a matter of adding one more `TranslationBackend` subclass and calling
`registry.register(...)` for it — see `translation/providers/*.py` for the
pattern, and `ai_backend.py` specifically for the "gate `is_available()` on
an env var, keep it out of the default Auto rotation" pattern a paid
provider should follow.

## AI translation mode

`ai-claude` and `ai-openai` ask a general-purpose LLM to translate rather
than calling a dedicated translation service. They're strictly opt-in:

* `is_available()` is `False` unless the corresponding API key
  (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) is set in the environment — so
  they never appear as selectable/eligible engines, and nothing is ever
  silently charged, on a deployment that hasn't configured one.
* They are **not** in the default `auto_engine_order` for the same reason
  — Auto must never spend a configured key's money without the operator
  choosing to. To let Auto use one, add its id to `auto_engine_order` in
  `config/translation_settings.json` yourself.
* An operator who has a key configured can still select `AI - Claude` /
  `AI - OpenAI` explicitly from `/translate`, or set it as the bot-wide
  default via `/set_translation_engine`.
* Model names are configurable (`ANTHROPIC_TRANSLATE_MODEL` /
  `OPENAI_TRANSLATE_MODEL`), defaulting to a small/cheap model for each
  provider. No key is ever logged, hard-coded, or persisted to
  `config/translation_settings.json`.

## Resource protection (rate limiting & concurrency)

Two independent mechanisms exist to keep a heavy translation workload
from tripping provider rate limits or overloading the host it runs on —
they solve different problems and are both needed:

* **Per-provider requests-per-minute limiting** (`translation/ratelimit.py`,
  wired into `TranslationManager._call_backend`). A token bucket per
  provider caps how many requests/minute go to that provider, *across
  every job in the process*. This is what actually prevents tripping a
  provider's own rate limiting — the concurrency semaphore below only
  bounds how many calls are in flight at once, not how fast they fire
  back-to-back. Configurable via `requests_per_minute` (global default)
  and `provider_requests_per_minute` (per-provider override). The initial
  burst allowance is capped at roughly a tenth of the per-minute rate
  (floored at 1) rather than the full minute's budget, so a job can't
  fire an entire minute's quota instantly at start — see
  `TranslationManager._rate_limit_for`.
* **Per-provider concurrency limiting** (already covered above under
  Performance) — `max_concurrency` / `provider_concurrency`, unchanged.
* **Bot-wide concurrent-job limiting** (`translation/jobs.py`,
  `job_limiter`, wired into `Translator.start()` in `utils/translate.py`).
  Each translation job spins up its own worker-thread pool (4-6 threads)
  independent of anything provider-related — enough *simultaneous* jobs
  from different users can add up to more OS threads/outbound traffic
  than a small host can handle at once, regardless of how well any single
  provider's load is bounded. `max_concurrent_jobs` (default `4`) caps
  how many jobs run at the same time; anything beyond that **queues**
  for a slot rather than being rejected — every request still completes,
  just not all simultaneously. `/translate` posts a one-time "queued"
  notice if a job has to wait for a slot.

All of these are configurable in `config/translation_settings.json`
(see the example below) and, like `default_engine`, degrade safely to
`translation.config.DEFAULT_CONFIG`'s values if the file is missing,
corrupted, or omits a key.

## Modes, in detail

### Default

Resolved from `translation.config.settings.default_engine` exactly once,
when the job starts. If an admin changes the default while a job is
already running, that job keeps using whatever it already resolved —
only new jobs see the new default (see `TranslationManager._resolve_default_engine`
and the `test_default_resolution_is_frozen_at_job_start` /
`test_admin_write_does_not_affect_already_constructed_managers` tests).

If the persisted default is missing, corrupted, or points at a provider
that's currently unavailable, the manager falls back through
`["googletrans", *auto_engine_order]` and picks the first available one
instead of crashing the job.

### Auto

* When there's no already-proven-healthy engine for this job yet (job
  start, or right after the current one just failed), Auto **races every
  currently-available candidate concurrently** and adopts whichever
  succeeds first — it does not wait on them one at a time in
  `auto_engine_order`. A candidate that loses the race is cancelled;
  losing candidates that had already started a real request are still
  counted as "called" (a request was genuinely fired) even though their
  result is discarded.
* Whichever engine wins becomes `active_engine` and is reused directly
  for every subsequent chunk in the same job — no re-racing/re-probing
  per chunk. This is the main cost/latency trade-off of racing: it can
  fire several requests at once, but only **once** per "engine needed"
  event, not once per chunk.
* If the active engine starts failing, it's retried (per the normal retry
  policy) then marked failed *for this job only*, and Auto races the
  remaining healthy candidates again to find a replacement.
* `failed_engines` is a per-job set. It's never written to global/shared
  state, so one job's outage never affects another job's — or another
  user's — choice of engine (see `test_per_job_failed_state_does_not_leak_between_jobs`).
* If every candidate in a race fails, the per-job failed set is cleared
  **once** and a fresh race is run (bounded recovery — a provider's
  outage might have been transient). If that also fails, the job raises
  `AllEnginesFailedError` — there is no unbounded retry loop
  (`test_all_engines_fail_bounded_reset_then_raises`).

See `TranslationManager._discover_auto_engine` in `translation/manager.py`
and the `test_auto_races_candidates_and_a_faster_later_engine_can_win` /
`test_auto_race_survives_a_loser_raising_after_being_cancelled` tests.

### Explicit engine

Only that engine is ever tried (with the normal retry policy). On final
failure the job raises `TranslationFailedError` — never a silent switch to
another provider (`test_explicit_engine_fails_without_falling_back`).

## Admin command

`/set_translation_engine engine:<id>` — guarded by the same
`@commands.has_role(1020638168237740042)` check as every other admin-only
command in `cogs/admin.py`. It validates the engine is registered *and*
currently available, persists it to
`config/translation_settings.json`, and confirms with the new display
name. A missing/corrupted settings file is never fatal — the bot falls
back to `translation.config.DEFAULT_CONFIG` and keeps running; the file is
(re)written the next time the default changes.

## Response validation

Every backend's raw response goes through `translation.validation` before
it's ever returned or written to a file:

* `None` or an empty string (when the source text wasn't itself empty) is
  rejected.
* HTTP 429/500/502/503/504, timeouts, and connection errors are treated as
  retry-worthy (`TransientTranslationError`).
* A detected provider error page (matching known phrases such as
  `"error 500"`, `"server error"`, `"please try again later"`, or two or
  more such phrases together) is rejected and retried — but a legitimate
  translation that simply happens to contain the word "error" once is
  **not** rejected (`test_valid_translation_containing_the_word_error_is_accepted`).
  `filter_error_text()` (a secondary cleanup step, carried over from the
  previous implementation) is only ever applied *after* an error was
  already detected — never unconditionally — specifically so it can't
  mangle a clean response that happens to contain that word.

## Performance

What changed vs. the previous ~1 hour/2 MB implementation:

* **Client reuse.** `GoogleTransBackend` builds its `googletrans.Translator`
  client once and reuses it for every call for the life of the process
  (previously: `async with GoogleTransClient(): ...` per chunk). Same idea
  for `deep-translator`'s clients, cached per `(source, target)` language
  pair.
* **Client reuse, with a real constraint made explicit.** `GoogleTransBackend`
  reuses its `googletrans.Translator` client across calls, but only *within
  a single event loop*: googletrans holds an `httpx.AsyncClient`, whose
  connection-pool internals bind to whichever event loop is running the
  first time they're actually used. This project processes each chunk via
  a fresh `asyncio.run()` call (see `Translator._run_async_blocking` in
  `utils/translate.py`), so blindly caching one client forever and reusing
  it across those separate loops would silently start reusing internals
  bound to an already-closed loop after the very first chunk (a real bug
  caught during review — see the code review notes below). The backend
  now tracks which loop its cached client belongs to and transparently
  rebuilds it whenever that loop has changed, so reuse still happens
  within a call/loop without ever crossing a closed one. `deep-translator`'s
  clients hold no event-loop-bound state at all (verified against the
  installed package source — each call is a plain, fresh `requests.get()`),
  so those are cached and reused freely across threads/loops.
* **Provider instances are process-lifetime singletons** (`ProviderRegistry`
  caches them), so this goes a step further than "one client per job" —
  they're reused across jobs too, not just across chunks within one job.
* **Bounded, provider-scoped concurrency** via a `threading.BoundedSemaphore`
  per engine id (`translation.max_concurrency`, overridable per-provider via
  `translation.provider_concurrency`), acquired through `asyncio.to_thread`
  so it can never block an event loop that has other work to do.
* **Auto mode stops probing once it finds a healthy engine** instead of
  trying every engine on every chunk (`test_auto_reuses_successful_engine_on_next_chunk_without_reprobing`).
* **Centralized retry with a short, configurable backoff**
  (`translation.retry_delays`, default `[2, 4, 7]`) instead of ad hoc sleeps
  scattered through the translation code.
* **Bounded worst case**: Auto's all-engines-failed recovery resets and
  retries exactly once, never loops indefinitely.

No formal before/after benchmark was run against live provider endpoints as
part of this change (this environment has no outbound network access to
translation providers) — the claims above describe what changed
structurally, not a measured wall-clock number. Run the pipeline against a
real ~2 MB file in an environment with network access and compare against
the ~1 hour baseline to get an actual figure; the removed per-chunk client
creation and the switch from full-order probing to active-engine reuse in
Auto mode are the two changes most likely to matter for that number.

## Configuration

`config/translation_settings.json` (created on first admin write; a
missing file just means "use defaults"):

```json
{
  "default_engine": "googletrans",
  "auto_engine_order": [
    "googletrans",
    "deep-google",
    "translators-google",
    "translators-bing",
    "deep-mymemory",
    "translators-mymemory",
    "translators-yandex",
    "translators-reverso",
    "libretranslate",
    "lingva"
  ],
  "retry_delays": [2, 4, 7],
  "request_delay": 0.2,
  "max_concurrency": 3,
  "provider_concurrency": {},
  "min_recoverable_chunk_chars": 120,
  "requests_per_minute": 50,
  "provider_requests_per_minute": {},
  "max_concurrent_jobs": 4
}
```

Only `default_engine` is ever written by the admin command; the rest are
read from this same file if present (edit it directly to tune them) and
otherwise come from `translation.config.DEFAULT_CONFIG`. Nothing in this
file is ever a secret — API keys, if a future provider needs one, belong
in an environment variable instead (see `translation/providers/translators_backend.py`
and `deep_translator_backend.py` for the `is_available()` pattern that
checks for one).

## Known limitations

* Every `translators`-package provider here is an **unofficial endpoint**
  (browser-style scraping of the provider's public translate page, not an
  official paid API) and can break or start rate-limiting without notice.
  Treat `translators-*` engines as lower-reliability than `googletrans` /
  `deep-google`.
* `deep-mymemory` and `translators-mymemory` share MyMemory's anonymous
  daily quota, which is fairly small; heavy use will hit 429s.
* None of the `translators` backends here expose a real batch API in this
  version of the package, so they fall back to one HTTP call per string —
  `googletrans` and the `deep-*` backends are meaningfully faster for large
  batches.
* Language detection (`Translator.detect_with_retry` /
  `Translator.adetect_with_retry`, and `FileHandler.find_language`) tries
  multiple independent detectors — an offline statistical detector
  (`langdetect`) first, then `googletrans` as a network-based fallback —
  across several text samples, and is not affected by the selected
  translation engine. See `translation/detection.py`.

## Files

```
translation/
├── base.py           TranslationBackend interface, ProviderCapabilities
├── manager.py         TranslationManager: mode resolution, Auto racing/failover, retry, concurrency, rate limiting
├── registry.py         ProviderRegistry: factory-based provider lookup/availability
├── errors.py           Exception hierarchy + error-page detection
├── validation.py        Response validation (used by the manager before returning anything)
├── retry.py            Centralized retry/backoff
├── ratelimit.py         Per-provider requests-per-minute token bucket
├── jobs.py              Bot-wide concurrent-job limiter
├── detection.py         Multi-engine language detection (langdetect + googletrans)
├── config.py            Persistent settings (config/translation_settings.json)
└── providers/
    ├── googletrans_backend.py
    ├── deep_translator_backend.py
    ├── translators_backend.py
    ├── lingva_backend.py
    ├── libretranslate_backend.py
    ├── ai_backend.py
    └── http_backend.py   shared helper for the direct-REST-call backends
```

`utils/translate.py`'s `Translator` class is now a thin backward-compatible
facade over this package — every previously-existing call site
(`translate()`, `translates()`, `start()`, the static `*_with_retry`
helpers, `_is_error_500_response`, `_filter_error_text`) keeps working
unchanged; it just delegates to `TranslationManager` internally instead of
containing the engine-switching logic itself.
