# Translation engines

The novel-translation feature (`utils/translate.py`'s `Translator`, backed
by the `translation/` package) can use several different translation
services. Some work out of the box with no signup; a few optional ones
unlock automatically the moment you set the right environment
variable(s) — no code changes or restarts-with-flags needed, just set
the variable and restart the bot (or run `.enginecheck`, see below).

This doc covers: which engines are already active for free, how to add
the optional keyed ones, how engine selection/health-checking actually
behaves, and how to use the `.enginecheck` admin command.

## Already active, free, no signup

These work immediately, no configuration required:

| Engine | Notes |
|---|---|
| Google Translate (`googletrans`) | Unofficial client library. |
| Google Translate (`deep-google`) | Via `deep-translator`'s `GoogleTranslator`. |
| Bing (`translators-bing`) | Via the `translators` package. |
| MyMemory (`deep-mymemory`) | Via `deep-translator`; see the MyMemory-specific handling below. |
| MyMemory (`translators-mymemory`) | Via the `translators` package (independent implementation/quirks from the one above). |
| Yandex (`translators-yandex`) | Via the `translators` package. |
| Reverso (`translators-reverso`) | Via the `translators` package. |
| LibreTranslate (`libretranslate`) | Public mirror by default; see below for pointing it at a different/self-hosted instance. |
| Lingva (`lingva`) | Public Lingva instance (a privacy-respecting Google Translate front-end). |

Having this many free engines active is the whole point of Auto mode
(see "How engine selection works" below): if one is down, rate-limited,
or blocked, the bot has several others to fall back to without an
operator having to do anything.

### MyMemory's quirks, handled automatically

MyMemory is free and requires no signup, but it has three rough edges
that `deep-mymemory` now handles for you:

- **~500 character request limit.** Longer text is automatically split
  on the nearest paragraph, then sentence, then word boundary (never
  mid-word except as an absolute last resort) and stitched back
  together after translation.
- **Region-tagged language codes** (`en-GB`, `ko-KR`, ...) instead of
  plain 2-letter codes. Handled by an automatic code mapping — you don't
  need to do anything.
- **Silent quota exhaustion.** When MyMemory's free daily quota runs
  out, it doesn't return an HTTP error — it returns a warning string
  (e.g. "MYMEMORY WARNING", "YOU USED ALL AVAILABLE FREE
  TRANSLATIONS...") *as the translation itself*. This is detected and
  treated as a real failure instead of being passed through as garbled
  output.

### A note on flaky/dead public mirrors

Several of the free engines above depend on a third party's public
mirror rather than an official paid API, and those go down or move
without notice. As of this doc:

- **LibreTranslate**'s old default host, `translate.argosopentech.com`,
  is decommissioned — the default here now points at
  `translate.terraprint.co` instead. Override with `LIBRETRANSLATE_URL`
  if that one goes down too.
- **Lingva**'s own official instance, `lingva.ml`, has been unreliable
  (Cloudflare 523s, and more recently a bot-abuse lockdown requiring a
  key on the public instance) — the default here now points at
  `lingva.lunar.icu`, per the project's own current instance list.
  Override with `LINGVA_URL` if that one goes down too — see
  <https://github.com/thedaviddelta/lingva-translate/blob/main/instances.json>
  for currently-known instances.
- **`translators-bing`** needs a JavaScript runtime (e.g. Node.js)
  installed and on the host's `PATH` — the `translators` package shells
  out to one for Bing's signature generation. If Node.js isn't
  installed, this engine will show as failing in `.enginecheck` with
  "Could not find an available JavaScript runtime" even though the
  Python package itself is fine.
- **`translators-apertium`** and **`translators-reverso`** can also show
  as failing in `.enginecheck` for reasons outside this bot's control:
  Apertium's code table is scraped live by the `translators` package and
  doesn't line up with this bot's plain 2-letter codes (it wants e.g.
  `"spa"`, not `"es"`) with no static table shipped to correct it here;
  Reverso's failures come from that same package's HTML scraper for
  reverso.net breaking, not from this bot's wrapper. Both are exactly
  the kind of thing `.enginecheck`/the startup health check exist to
  catch — Auto mode just skips them and uses one of the many other
  free engines instead.

`.enginecheck` (see below) is the fastest way to check current status
of any of these rather than assuming this doc stays perfectly in sync.

### LibreTranslate: free by default, pointable at your own instance

`libretranslate` talks to a public LibreTranslate mirror with no key
required. If you run your own instance (or a mirror that requires a
key), set:

- `LIBRETRANSLATE_URL` — base URL of your instance, e.g.
  `https://libretranslate.example.com`.
- `LIBRETRANSLATE_API_KEY` — only if your instance requires one.

Neither variable is required for the default free path to keep working.

## Optional, free-with-signup engines

Each of these is **only added to the engine rotation if its required
environment variable(s) are set**. If a variable is missing, the engine
simply never appears as a candidate — no partial/broken entry, no error
at startup, nothing logged as a failure. Set the variable(s) and restart
the bot (or trigger `.enginecheck`) to bring one online.

### DeepL

- **Free tier:** DeepL API Free — 500,000 characters/month, no credit
  card required.
- **Env var:** `DEEPL_API_KEY`
- **Sign up:**
  1. Go to <https://www.deepl.com/en/pro-api> (or
     `deepl.com/en/pro#developer`) and create a **DeepL API** account —
     this is a separate account/product from a regular deepl.com
     translator login, even if you already have one of those.
  2. Choose the **API Free** plan.
  3. Find your key under **Account → API Keys**. Free keys end in
     `:fx` — the bot leaves `use_free_api` at its default (`True`), so a
     `:fx` key is automatically routed to the free `api-free.deepl.com`
     endpoint; nothing else to configure.
  4. Set `DEEPL_API_KEY` to that key and restart the bot.

### Microsoft Translator (Azure)

- **Free tier:** F0 tier — 2,000,000 characters/month, doesn't expire.
- **Env vars:** `MICROSOFT_API_KEY` (required), `MICROSOFT_REGION`
  (optional, but recommended — e.g. `eastus`; some Azure resources
  reject requests that omit the region header).
- **Sign up:**
  1. Create/sign in to an Azure account at <https://azure.microsoft.com>.
     **Friction to know about up front:** creating an Azure account
     itself requires a credit/debit card for identity verification, even
     though the Translator F0 tier itself never charges it.
  2. In the Azure Portal, **Create a resource** → search **Translator**
     → **Create**.
  3. Under **Pricing tier**, select **F0 (Free)** — each Azure account
     gets one free Translator subscription.
  4. Once created, go to **Keys and Endpoint** on the resource to get
     your key and region.
  5. Set `MICROSOFT_API_KEY` (and ideally `MICROSOFT_REGION`) and
     restart the bot.

### Papago (Naver)

Especially strong for Korean — worth prioritizing given this bot already
has Korean-specific handling elsewhere (see `utils/handler.py`'s
channel-routing logic).

- **Free tier:** Naver Cloud Platform (NCP) issues free starter credit
  for its AI/NAVER APIs (Papago included); check the current amount/
  duration in the NCP console, since Naver changes this periodically.
- **Env vars:** `PAPAGO_CLIENT_ID` and `PAPAGO_SECRET_KEY` (both
  required).
- **Sign up:**
  1. Create an account at <https://www.ncloud.com> (Naver Cloud
     Platform — a *different* signup from a regular naver.com account).
     **Friction to know about up front:** NCP signup requires phone
     verification, and historically this has been the roughest part of
     this whole doc for anyone outside Korea — some regions/carriers
     aren't accepted, in which case Naver's own support is the only
     real path forward. Budget extra time for this step specifically.
  2. In the NCP console, go to **Services → AI·NAVER API → Application**
     and register a new application, enabling the **Papago Translation**
     (NMT) API for it.
  3. Copy the issued **Client ID** and **Client Secret**.
  4. Set `PAPAGO_CLIENT_ID` / `PAPAGO_SECRET_KEY` and restart the bot.

### Baidu Translate

- **Free tier:** two tiers exist —
  - **Standard**: 50,000 characters/month free, available immediately
    after registering, no identity verification.
  - **Premium** (recommended if you'll use this regularly): 1,000,000
    characters/month free, but requires completing Baidu's identity
    verification step during setup.
- **Env vars:** `BAIDU_APP_ID` and `BAIDU_APP_KEY` (both required).
- **Sign up:**
  1. Go to <https://fanyi-api.baidu.com/> and log in with (or create) a
     Baidu account.
  2. Open the console and register as an **Individual Developer** (or
     Enterprise, if applicable).
  3. Activate the **General Translation API**, choosing Standard or
     Premium (Premium requires the identity-verification step mentioned
     above).
  4. Your **APP ID** and **Key** are shown at the bottom of the console
     page (<https://fanyi-api.baidu.com/api/trans/product/desktop>).
  5. Set `BAIDU_APP_ID` / `BAIDU_APP_KEY` and restart the bot.

### A note on engines that *don't* have a genuine free path

Not every major translation provider offers something that's actually
free-with-signup the way the ones above do. **Alibaba's translation API
is one of these** — it doesn't have a no-cost tier comparable to DeepL
Free, Azure's F0, or Baidu's Standard tier, so it isn't wired in here.
If you specifically need it, it would have to be added as a paid engine
with its own billing setup — not something this doc pretends is free
just to pad out the list. The same caution applies to any other provider
not listed above: if it isn't documented here with a real free tier and
real signup steps, assume it needs a paid plan.

## How auto-mode engine selection works

When a job uses `engine="auto"` (the bot's `.translate` command's Auto
option), the manager doesn't try engines one at a time in a fixed order
with a single "preferred" engine — it **races every currently-available
engine concurrently** the first time it needs one, and adopts whichever
responds successfully first. Every later chunk in that same job reuses
that winner directly instead of re-racing, so the racing overhead only
happens once (or again, if the winner later fails mid-job).

This means:

- There's no single global "currently preferred" engine — which engine
  wins can vary job to job (network conditions, which engines happen to
  be under load elsewhere, etc). The `/translate` progress embed's
  "Engine" field always tells you what actually happened for *that* job
  (e.g. `"Google Translate"`, or `"Google Translate (41), MyMemory (3)"`
  if it had to hop mid-job) rather than a static prediction.
- A configured optional engine (DeepL, Microsoft, Papago, Baidu) is
  automatically included as a race candidate the moment its env
  var(s) are set — no separate "enable in auto mode" step.

## Cooldown vs. session-disable

An **ordinary transient failure** (a timeout, a network blip) during a
job only affects that one job — it's excluded from that job's engine
race and nothing else changes; the very next `/translate` job will try
it again fresh.

A **quota/rate-limit failure** is treated differently: retrying an
engine that's already burned through its daily/monthly quota every few
minutes is never going to succeed until the quota resets, so instead of
just backing off, that engine is **session-disabled** — excluded from
Auto's race entirely — for the rest of the bot process. There's no
timer; it only comes back once a health check (see below) actually
succeeds against it again, which normally means: the quota reset, or an
admin fixed a bad key and ran `.enginecheck`.

## Startup and on-demand health checks

Every time the translation cog loads (bot startup, or a cog
reload), it fires a background health check: one tiny real translation
("hello", English → Spanish) through **every currently configured
engine — including already session-disabled ones**, so a *recovery*
gets detected too, not just new failures. This never blocks or crashes
bot startup; failures are just logged.

- Engine passes, was previously disabled → re-enabled (available again
  as an Auto candidate; not necessarily what wins the very next race,
  just eligible again).
- Engine passes, was already fine → no change.
- Engine fails → session-disabled for the rest of the process, with the
  error recorded as the reason.
- An engine missing its required credentials/package isn't treated as a
  failure at all — it's just not applicable right now.

## The `.enginecheck` command

Bot-owner only (`@commands.is_owner()`, the same gate this bot's other
true owner-only command — `addrole` in `cogs/general.py` — uses). Runs
the health check above **right now** and posts an embed listing every
registered engine with a status icon:

- 🟢 recovered / newly working this check
- ✅ still working (no change)
- 🔴 newly failed this check
- 🟥 still disabled (no change), with a short reason
- ⚪ not configured (missing package/credentials — not a failure)

It also marks which engine is the configured default (`*(default)*`)
and shows a summary of which engine(s) your own most recent translation
job actually used.

If a non-owner runs `.enginecheck`, the bot's existing global error
handler (`cogs/errors.py`'s `on_command_error`) already catches
`commands.NotOwner` and replies with a friendly embed rather than an
unhandled exception — no special-casing needed in the command itself.
