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
| `googletrans` | GoogleTrans | No | `googletrans==4.0.2`. Client is created once and reused for every chunk. |
| `deep-google` | Deep Translator - Google | No | `deep-translator`'s `GoogleTranslator`. |
| `deep-mymemory` | Deep Translator - MyMemory | No | `deep-translator`'s `MyMemoryTranslator`. Small daily quota per IP on the anonymous tier. |
| `translators-google` | Translators - Google | No | Via the `translators` package (unofficial endpoint, see below). |
| `translators-bing` | Translators - Bing | No | Same provider the previous implementation called "bing". |
| `translators-mymemory` | Translators - MyMemory | No | |
| `translators-yandex` | Translators - Yandex | No | |
| `translators-apertium` | Translators - Apertium | No | Open-source engine, narrower language coverage than the others. |

No provider in this implementation requires an API key or paid credentials.
The `translators` package works by talking to each provider's public
web-translate endpoint the same way a browser does; there's no official
paid API involved anywhere in this list, which is also why they're
unofficial and can change or start rate-limiting without notice. Several
`translators` providers were deliberately **not** exposed:

* `alibaba`, `baidu`, `caiyun`, `deepl`, `niutrans`, `sysTran`, `volcEngine`,
  `youdao`, `papago`, and most of the remaining catalogue require paid
  accounts/API keys for reliable bulk use, or their free-tier behaviour
  through `translators` couldn't be verified as stable enough to ship here.
* `argos` needs a locally-installed language model per language pair, which
  doesn't fit a Discord bot translating arbitrary novel text.

Adding a properly credentialed provider later (e.g. an official DeepL key)
is a matter of adding one more `TranslationBackend` subclass and calling
`registry.register(...)` for it — see `translation/providers/*.py` for the
pattern. Read any credential from an environment variable (never hard-code
it), and gate `is_available()` on that variable being set so the bot
degrades gracefully when it isn't configured. `PROVIDER_ENV_VAR` (docs
below) is the placeholder to fill in following that same pattern.

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

* Tries engines in `translation.auto_engine_order` (configurable, see
  below) until one succeeds.
* Remembers that engine as `active_engine` and reuses it directly for
  every subsequent chunk in the same job — it does **not** re-probe
  every engine on every chunk.
* If the active engine starts failing, it's retried (per the normal retry
  policy) then marked failed *for this job only* and the next healthy
  candidate in the order is tried.
* `failed_engines` is a per-job set. It's never written to global/shared
  state, so one job's outage never affects another job's — or another
  user's — choice of engine (see `test_per_job_failed_state_does_not_leak_between_jobs`).
* If every candidate fails, the per-job failed set is cleared **once**
  and a fresh pass is tried (bounded recovery — a provider's outage might
  have been transient). If that also fails, the job raises
  `AllEnginesFailedError` — there is no unbounded retry loop
  (`test_all_engines_fail_bounded_reset_then_raises`).

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
    "translators-yandex"
  ],
  "retry_delays": [2, 4, 7],
  "request_delay": 0.2,
  "max_concurrency": 3,
  "provider_concurrency": {},
  "min_recoverable_chunk_chars": 120
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
  `Translator.adetect_with_retry`) always uses `googletrans` directly and
  is not affected by the selected translation engine — it's a single,
  cheap, one-time lookup per document, not part of the per-chunk
  translation path.

## Files

```
translation/
├── base.py           TranslationBackend interface, ProviderCapabilities
├── manager.py         TranslationManager: mode resolution, Auto failover, retry, concurrency
├── registry.py         ProviderRegistry: factory-based provider lookup/availability
├── errors.py           Exception hierarchy + error-page detection
├── validation.py        Response validation (used by the manager before returning anything)
├── retry.py            Centralized retry/backoff
├── config.py            Persistent settings (config/translation_settings.json)
└── providers/
    ├── googletrans_backend.py
    ├── deep_translator_backend.py
    └── translators_backend.py
```

`utils/translate.py`'s `Translator` class is now a thin backward-compatible
facade over this package — every previously-existing call site
(`translate()`, `translates()`, `start()`, the static `*_with_retry`
helpers, `_is_error_500_response`, `_filter_error_text`) keeps working
unchanged; it just delegates to `TranslationManager` internally instead of
containing the engine-switching logic itself.
