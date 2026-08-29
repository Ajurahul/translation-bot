from utils.urlnorm import normalize_url, VisitedTracker


def test_trailing_slash_ignored():
    assert normalize_url("https://example.com/chapter-1") == normalize_url("https://example.com/chapter-1/")


def test_fragment_dropped():
    assert normalize_url("https://example.com/c1#comments") == normalize_url("https://example.com/c1")


def test_scheme_host_case_insensitive():
    assert normalize_url("HTTPS://Example.COM/c1") == normalize_url("https://example.com/c1")


def test_path_case_preserved():
    # site paths can be case-sensitive; only scheme/host are lowercased
    assert normalize_url("https://example.com/Chapter-1") != normalize_url("https://example.com/chapter-1")


def test_query_string_preserved():
    assert normalize_url("https://example.com/c?x=1") != normalize_url("https://example.com/c?x=2")


def test_relative_url_resolved_against_base():
    n = normalize_url("chapter-2.html", base="https://example.com/book/chapter-1.html")
    assert n == "https://example.com/book/chapter-2.html"


def test_root_path_slash_not_stripped():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_visited_tracker_detects_duplicate_despite_variations():
    vt = VisitedTracker()
    vt.add("https://example.com/chapter-5/")
    assert vt.seen("https://example.com/chapter-5#top")
    assert vt.seen("HTTPS://EXAMPLE.com/chapter-5")
    assert not vt.seen("https://example.com/chapter-6")


def test_visited_tracker_len():
    vt = VisitedTracker()
    vt.add("https://example.com/a")
    vt.add("https://example.com/a/")  # same normalized URL
    vt.add("https://example.com/b")
    assert len(vt) == 2
