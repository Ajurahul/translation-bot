"""Generic language-code mapper for deep-translator backends whose service
expects a code table that doesn't line up with this bot's own 2-letter
`languages.choices` codes (MyMemory's region-tagged codes, DeepL/Baidu's
full-name keys, Papago's code-keyed-by-name-value table, ...).

One implementation is shared by every such backend instead of writing a
bespoke mapper per engine:

  1. "auto" always passes straight through.
  2. If our code is already directly valid for the target engine -- as
     either a key *or* a value of its language dict, so this works
     whether the dict is name->code (DeepL, Baidu, MyMemory) or
     code->name (Papago) -- it's used unchanged.
  3. Otherwise the code's language *name* is looked up via this bot's
     own `languages.choices` table (2-letter code -> name) and mapped
     through the engine's dict (built in whichever direction it's
     actually keyed).
  4. If nothing matches, the original code is returned unchanged rather
     than raising -- a best-effort pass-through beats hard-failing a
     whole translation job over one unmapped language.

Results are cached per (engine, code) pair, and each engine's language
dict (which may itself be expensive to obtain -- e.g. Microsoft's is
fetched over the network) is fetched at most once per process via
`dict_factory` and cached too.
"""
import re
import threading
import typing as t

from languages import languages

_CODE_LIKE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$", re.IGNORECASE)

# 2-letter/short code -> lowercase language name, built once from the
# bot's own name -> code table (languages.choices).
_CODE_TO_NAME: t.Dict[str, str] = {
    str(code).lower(): name for name, code in languages.choices.items()
}

_dict_lock = threading.Lock()
_engine_dicts: t.Dict[str, t.Dict[str, str]] = {}

_code_lock = threading.Lock()
_code_cache: t.Dict[t.Tuple[str, str], str] = {}


def _is_code_like(value: str) -> bool:
    return bool(_CODE_LIKE.match(value or ""))


def _get_engine_dict(
    engine_name: str, dict_factory: t.Callable[[], t.Dict[str, str]]
) -> t.Dict[str, str]:
    with _dict_lock:
        cached = _engine_dicts.get(engine_name)
        if cached is not None:
            return cached
    try:
        built = dict(dict_factory() or {})
    except Exception:
        built = {}
    with _dict_lock:
        _engine_dicts.setdefault(engine_name, built)
        return _engine_dicts[engine_name]


def _build_name_to_code(lang_dict: t.Dict[str, str]) -> t.Dict[str, str]:
    """Normalize an engine's language dict -- whichever direction it's
    keyed in -- into a single lowercase-name -> code lookup."""
    name_to_code: t.Dict[str, str] = {}
    for key, value in lang_dict.items():
        key_s, value_s = str(key), str(value)
        key_is_code, value_is_code = _is_code_like(key_s), _is_code_like(value_s)
        if key_is_code and not value_is_code:
            # code -> name (e.g. Papago's PAPAGO_LANGUAGE_TO_CODE)
            name_to_code[value_s.lower()] = key_s
        elif value_is_code and not key_is_code:
            # name -> code (e.g. DeepL/Baidu/MyMemory)
            name_to_code[key_s.lower()] = value_s
        else:
            # Ambiguous -- register both directions rather than guessing.
            name_to_code[key_s.lower()] = value_s
            name_to_code[value_s.lower()] = key_s
    return name_to_code


def clear_cache() -> None:
    """Test hook -- drop cached engine dicts/code mappings."""
    with _dict_lock:
        _engine_dicts.clear()
    with _code_lock:
        _code_cache.clear()


def map_language_code(
    engine_name: str,
    code: t.Optional[str],
    dict_factory: t.Callable[[], t.Dict[str, str]],
) -> str:
    """Map `code` (one of this bot's own language codes, or "auto") to
    whatever `engine_name`'s deep-translator backend expects, per the
    algorithm described in the module docstring."""
    if not code:
        return "auto"
    original = str(code).strip()
    low = original.lower()
    if low == "auto":
        return "auto"

    cache_key = (engine_name, low)
    with _code_lock:
        cached = _code_cache.get(cache_key)
    if cached is not None:
        return cached

    lang_dict = _get_engine_dict(engine_name, dict_factory)

    valid_codes = {str(k).lower() for k in lang_dict.keys()} | {
        str(v).lower() for v in lang_dict.values()
    }
    if low in valid_codes:
        result = original
    else:
        name = _CODE_TO_NAME.get(low)
        name_to_code = _build_name_to_code(lang_dict)
        mapped = name_to_code.get(name) if name else None
        result = mapped if mapped else original

    with _code_lock:
        _code_cache[cache_key] = result
    return result
