"""
Deterministic, lightweight fallback extraction heuristics.

These run ONLY when utils/selector.py has no entry for a site (or a
selector fails to match) - selector-driven extraction always wins when
available. Nothing here uses ML models, embeddings, or OCR; every score
is a small, explainable function of visible text, tag semantics, and a
handful of known multilingual label lists (English/Korean/Chinese, with
basic Japanese support for next-link detection).

Each public function returns a small dataclass with:
  - the extracted value (or None/"" if nothing usable was found)
  - a confidence in [0.0, 1.0]
  - a `method` string naming which heuristic produced the result
  - a `diagnostics` list of short strings explaining what was tried/why
    candidates were rejected, meant for logging - not shown to users.

Keep this conservative: it's fine to return low confidence (or nothing)
rather than guess. Callers decide what confidence bar is "good enough".
"""
import json
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

# --- multilingual "next chapter" label sets ------------------------------

NEXT_LABELS_EN = {
    "next", "next chapter", "next page", "continue", "continue reading",
    "read next", "next>>", "next >>", "\u203a", "\u00bb", "\u00bb\u00bb",
}
NEXT_LABELS_KO = {
    "\ub2e4\uc74c", "\ub2e4\uc74c\ud654", "\ub2e4\uc74c \ud3b8", "\ub2e4\uc74c\ud398\uc774\uc9c0",
}
NEXT_LABELS_ZH = {
    "\u4e0b\u4e00\u7ae0", "\u4e0b\u4e00\u9875", "\u4e0b\u7ae0", "\u540e\u7eed", "\u7ee7\u7eed\u9605\u8bfb",
    "\u4e0b\u4e00\u8282", "\u540e\u4e00\u9875", "\u4e0b\u9875",
}
NEXT_LABELS_JA = {
    "\u6b21", "\u6b21\u3078", "\u6b21\u306e\u8a71",
}
ALL_NEXT_LABELS = NEXT_LABELS_EN | NEXT_LABELS_KO | NEXT_LABELS_ZH | NEXT_LABELS_JA

PREV_REJECT_HINTS = {
    "prev", "previous", "\u4e0a\u4e00\u7ae0", "\u4e0a\u4e00\u9875", "\u4e0a\u7ae0",
    "\uc774\uc804", "\uc774\uc804\ud654", "\u524d", "\u524d\u3078",
}
NAV_REJECT_HINTS = {
    "index", "toc", "table of contents", "home", "login", "sign in", "register",
    "comment", "comments", "menu", "search", "\ubaa9\ub85d", "\ubcf8\ud64d", "\u76ee\u5f55", "\u9996\u9875",
}

CHALLENGE_MARKERS = (
    "checking your browser", "cf-browser-verification", "just a moment",
    "attention required", "please enable javascript", "verify you are human",
    "ddos protection by cloudflare", "access denied", "captcha",
)

BOILERPLATE_CLASS_HINTS = (
    "comment", "footer", "header", "sidebar", "menu", "nav", "advert", "ads",
    "related", "login", "share", "social", "breadcrumb", "pagination",
)
CONTENT_CLASS_HINTS = (
    "chapter", "content", "read", "article", "novel", "viewer", "text",
    "\u6b63\u6587",  # zh "main text"
    "\ubcf8\ubb38",  # ko "body text"
)

SITE_NAME_SEPARATORS = (" - ", " | ", " \u2013 ", " :: ", " \u00bb ", " > ")


@dataclass
class ExtractionResult:
    value: Optional[str] = None
    confidence: float = 0.0
    method: str = "none"
    diagnostics: List[str] = field(default_factory=list)

    def __bool__(self):
        return bool(self.value) and self.confidence > 0


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def _meta_content(soup: BeautifulSoup, *names_or_props) -> Optional[str]:
    for key in names_or_props:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content", "").strip():
            return tag["content"].strip()
    return None


def _json_ld_objects(soup: BeautifulSoup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    yield item
        elif isinstance(data, dict):
            if isinstance(data.get("@graph"), list):
                for item in data["@graph"]:
                    if isinstance(item, dict):
                        yield item
            else:
                yield data


def clean_title_text(title: str) -> str:
    """Strip a trailing/leading ' - Site Name' style suffix commonly found
    in <title> tags, keeping the longer, more specific side of the split
    (the chapter/novel title, not the site brand)."""
    if not title:
        return title
    title = title.strip()
    for sep in SITE_NAME_SEPARATORS:
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            if len(parts) >= 2:
                # keep the longest fragment - the site name is usually the
                # short, repeated one
                title = max(parts, key=len)
    return title.strip()


# --- title -----------------------------------------------------------------

def extract_title(soup: BeautifulSoup) -> ExtractionResult:
    diag = []

    val = _meta_content(soup, "og:title", "twitter:title")
    if val:
        return ExtractionResult(clean_title_text(val), 0.9, "og_meta", diag + ["matched og:title/twitter:title"])
    diag.append("no og:title/twitter:title meta")

    for obj in _json_ld_objects(soup):
        headline = obj.get("headline") or obj.get("name")
        if isinstance(headline, str) and headline.strip():
            return ExtractionResult(clean_title_text(headline), 0.85, "json_ld",
                                    diag + ["matched JSON-LD headline/name"])
    diag.append("no usable JSON-LD headline/name")

    h1 = soup.find("h1")
    if h1 and _text(h1):
        return ExtractionResult(clean_title_text(_text(h1)), 0.7, "h1", diag + ["used first <h1>"])
    diag.append("no non-empty <h1>")

    h2 = soup.find("h2")
    if h2 and _text(h2):
        return ExtractionResult(clean_title_text(_text(h2)), 0.55, "h2", diag + ["used first <h2> (no h1 found)"])
    diag.append("no non-empty <h2>")

    if soup.title and _text(soup.title):
        return ExtractionResult(clean_title_text(_text(soup.title)), 0.4, "title_tag",
                                diag + ["fell back to <title> with site-name separator stripped"])
    diag.append("no <title> tag either")

    return ExtractionResult(None, 0.0, "none", diag)


# --- description -------------------------------------------------------

def extract_description(soup: BeautifulSoup) -> ExtractionResult:
    diag = []

    val = _meta_content(soup, "og:description", "description")
    if val:
        return ExtractionResult(val, 0.85, "meta", diag + ["matched og:description/meta description"])
    diag.append("no og:description/meta description")

    for obj in _json_ld_objects(soup):
        desc = obj.get("description")
        if isinstance(desc, str) and desc.strip():
            return ExtractionResult(desc.strip(), 0.75, "json_ld", diag + ["matched JSON-LD description"])
    diag.append("no usable JSON-LD description")

    best = None
    best_score = 0.0
    for el in soup.find_all(["p", "div", "section"]):
        classes = " ".join(el.get("class", []) or []) + " " + (el.get("id") or "")
        classes = classes.lower()
        if any(h in classes for h in BOILERPLATE_CLASS_HINTS):
            continue
        looks_synopsis = any(h in classes for h in ("synopsis", "summary", "intro", "desc"))
        text = _text(el)
        if not text or len(text) < 40:
            continue
        score = min(len(text), 600) / 600.0
        if looks_synopsis:
            score += 0.4
        if score > best_score:
            best_score = score
            best = text
    if best:
        confidence = min(0.6, 0.3 + best_score * 0.3)
        return ExtractionResult(best[:800], confidence, "prominent_block",
                                diag + ["used the highest-scoring visible text block"])
    diag.append("no prominent synopsis-like block found")

    return ExtractionResult(None, 0.0, "none", diag)


# --- next-chapter link ------------------------------------------------

def _href_ok(href: Optional[str]) -> bool:
    if not href:
        return False
    href = href.strip()
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return False
    return True


def find_next_link(soup: BeautifulSoup, base_url: str, visited=None) -> ExtractionResult:
    """``visited`` may be a utils.urlnorm.VisitedTracker, or anything
    supporting ``in`` on a URL - candidates already visited are skipped so
    a mislabeled "next" link can't turn into an immediate self-loop."""
    diag = []
    base_netloc = urlsplit(base_url).netloc.lower() if base_url else None

    def resolve_and_check(href: str) -> Optional[str]:
        if not _href_ok(href):
            return None
        full = urljoin(base_url, href) if base_url else href
        netloc = urlsplit(full).netloc.lower()
        if base_netloc and netloc and netloc != base_netloc:
            diag.append(f"rejected cross-site candidate: {netloc}")
            return None
        if visited is not None and full in visited:
            diag.append("rejected already-visited candidate")
            return None
        return full

    link_next = soup.find("link", attrs={"rel": lambda v: v and "next" in (v if isinstance(v, list) else [v])})
    if link_next:
        resolved = resolve_and_check(link_next.get("href"))
        if resolved:
            return ExtractionResult(resolved, 0.95, "link_rel_next", diag + ["matched <link rel=next>"])
    diag.append("no usable <link rel=next>")

    for a in soup.find_all("a", attrs={"rel": lambda v: v and "next" in (v if isinstance(v, list) else [v])}):
        resolved = resolve_and_check(a.get("href"))
        if resolved:
            return ExtractionResult(resolved, 0.9, "a_rel_next", diag + ["matched <a rel=next>"])
    diag.append("no usable <a rel=next>")

    best = None
    best_score = 0.0
    best_reason = ""
    for a in soup.find_all("a"):
        href = a.get("href")
        text = _text(a).lower()
        classes = " ".join(a.get("class", []) or []) + " " + (a.get("id") or "")
        classes = classes.lower()

        if any(h in text or h in classes for h in PREV_REJECT_HINTS):
            continue
        if any(h in text for h in NAV_REJECT_HINTS):
            continue

        score = 0.0
        reason = ""
        if text in ALL_NEXT_LABELS:
            score = 0.8
            reason = f"exact label match ({text!r})"
        elif "next" in classes and "prev" not in classes:
            score = 0.55
            reason = f"class/id contains 'next' ({classes.strip()!r})"
        elif any(label in text for label in ALL_NEXT_LABELS if len(label) > 1):
            score = 0.5
            reason = f"partial label match in text ({text!r})"

        if score <= best_score:
            continue
        resolved = resolve_and_check(href)
        if not resolved:
            continue
        best, best_score, best_reason = resolved, score, reason

    if best:
        return ExtractionResult(best, best_score, "label_or_class_match", diag + [best_reason])
    diag.append("no anchor matched any multilingual next-chapter label or class")

    return ExtractionResult(None, 0.0, "none", diag)


# --- chapter content -----------------------------------------------------

def looks_like_challenge_or_empty(soup: BeautifulSoup, text: str = None) -> Optional[str]:
    """Returns a short reason string if the page looks like a challenge/
    error/login-only/empty page rather than real chapter content, else
    None. Deliberately conservative - only trips on strong, specific
    signals so it doesn't reject legitimately short/unusual chapters."""
    body_text = text if text is not None else _text(soup.body if soup and soup.body else soup)
    low = (body_text or "")[:3000].lower()
    for marker in CHALLENGE_MARKERS:
        if marker in low:
            return f"challenge/block page marker found: {marker!r}"
    if len(body_text.strip()) < 40:
        return f"page text suspiciously short ({len(body_text.strip())} chars)"
    return None


def _link_density(el: Tag) -> float:
    text_len = len(_text(el))
    if text_len == 0:
        return 1.0
    link_len = sum(len(_text(a)) for a in el.find_all("a"))
    return min(1.0, link_len / text_len)


def extract_content(soup: BeautifulSoup) -> ExtractionResult:
    """Scores candidate containers and returns the best one's text.
    Intended as a fallback alongside (not a replacement for) readabilipy -
    use this to sanity-check/score a result, or when readabilipy itself
    returns something too short/garbage-looking."""
    diag = []

    reason = looks_like_challenge_or_empty(soup)
    if reason:
        return ExtractionResult(None, 0.0, "rejected", diag + [reason])

    candidates = []
    for el in soup.find_all(["article", "main", "div", "section"]):
        if el.get("role") == "main":
            base = 0.3
        elif el.name in ("article", "main"):
            base = 0.25
        else:
            base = 0.0
        classes = " ".join(el.get("class", []) or []) + " " + (el.get("id") or "")
        classes = classes.lower()
        if any(h in classes for h in BOILERPLATE_CLASS_HINTS):
            continue
        if any(h in classes for h in CONTENT_CLASS_HINTS):
            base += 0.25

        text = _text(el)
        text_len = len(text)
        if text_len < 80:
            continue
        paragraphs = el.find_all("p")
        para_score = min(len(paragraphs), 20) / 20.0
        length_score = min(text_len, 4000) / 4000.0
        density = _link_density(el)
        score = base + 0.3 * length_score + 0.2 * para_score - 0.3 * density
        candidates.append((score, text, classes))

    if not candidates:
        diag.append("no candidate container had usable text")
        return ExtractionResult(None, 0.0, "none", diag)

    candidates.sort(key=lambda c: c[0], reverse=True)
    score, text, classes = candidates[0]
    confidence = max(0.0, min(0.9, score))
    diag.append(f"best candidate classes={classes!r} score={score:.2f} among {len(candidates)} candidates")
    return ExtractionResult(text, confidence, "scored_container", diag)
