from bs4 import BeautifulSoup

from utils import extraction as ex


def soup(html):
    return BeautifulSoup(html, "html.parser")


# --- title -----------------------------------------------------------

def test_title_prefers_og_title():
    s = soup("""
    <html><head>
      <meta property="og:title" content="Chapter 12: The Return">
      <title>Chapter 12 - MyNovelSite</title>
    </head><body><h1>Something else</h1></body></html>
    """)
    r = ex.extract_title(s)
    assert r.value == "Chapter 12: The Return"
    assert r.method == "og_meta"
    assert r.confidence > 0.8


def test_title_falls_back_to_json_ld():
    s = soup("""
    <html><head>
      <script type="application/ld+json">{"@type": "Article", "headline": "Chapter 3: Awakening"}</script>
    </head><body><p>no headings here</p></body></html>
    """)
    r = ex.extract_title(s)
    assert r.value == "Chapter 3: Awakening"
    assert r.method == "json_ld"


def test_title_falls_back_to_h1():
    s = soup("<html><body><h1>  Chapter 5: Storm  </h1></body></html>")
    r = ex.extract_title(s)
    assert r.value == "Chapter 5: Storm"
    assert r.method == "h1"


def test_title_falls_back_to_title_tag_and_strips_site_name():
    s = soup("<html><head><title>Chapter 7: Dawn - ReadNovels.com</title></head><body></body></html>")
    r = ex.extract_title(s)
    assert r.value == "Chapter 7: Dawn"
    assert r.method == "title_tag"


def test_title_none_when_nothing_found():
    s = soup("<html><body><p>just text</p></body></html>")
    r = ex.extract_title(s)
    assert r.value is None
    assert r.confidence == 0.0


# --- description -------------------------------------------------------

def test_description_prefers_meta():
    s = soup('<html><head><meta name="description" content="A tale of a hero."></head><body></body></html>')
    r = ex.extract_description(s)
    assert r.value == "A tale of a hero."
    assert r.method == "meta"


def test_description_falls_back_to_prominent_block():
    s = soup("""
    <html><body>
      <nav>Home | Library | Login</nav>
      <div class="synopsis">This is a long synopsis describing the plot of the novel in some detail, enough to be picked up.</div>
      <footer>copyright 2020</footer>
    </body></html>
    """)
    r = ex.extract_description(s)
    assert r.value is not None
    assert "synopsis" in r.method.lower() or r.method == "prominent_block"
    assert "plot of the novel" in r.value


# --- next-chapter link, multilingual ------------------------------------

def test_next_link_rel_next_wins():
    s = soup('<html><head><link rel="next" href="/chapter-2"></head><body>'
             '<a class="next-btn" href="/other">Something</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/chapter-1")
    assert r.value == "https://example.com/chapter-2"
    assert r.method == "link_rel_next"


def test_next_link_english_label():
    s = soup('<html><body><a href="/c2">Next Chapter</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_korean_label():
    s = soup('<html><body><a href="/c2">\ub2e4\uc74c\ud654</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_chinese_label():
    s = soup('<html><body><a href="/c2">\u4e0b\u4e00\u7ae0</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_japanese_label():
    s = soup('<html><body><a href="/c2">\u6b21\u3078</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_rejects_previous_chapter():
    s = soup('<html><body>'
             '<a href="/c0">Previous Chapter</a>'
             '<a href="/c2">Next Chapter</a>'
             '</body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_rejects_login_and_index_text():
    s = soup('<html><body>'
             '<a href="/login">Login</a>'
             '<a href="/index">Table of Contents</a>'
             '<a href="/c2">Next</a>'
             '</body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


def test_next_link_rejects_cross_site_candidate():
    s = soup('<html><body><a href="https://other-site.com/c2">Next</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value is None


def test_next_link_skips_already_visited():
    visited = {"https://example.com/c1"}
    s = soup('<html><body><a href="/c1">Next</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1", visited=visited)
    assert r.value is None


def test_next_link_class_based_fallback():
    s = soup('<html><body><a class="btn nxt-chapter" href="/c2">&raquo;&raquo;</a></body></html>')
    r = ex.find_next_link(s, base_url="https://example.com/c1")
    assert r.value == "https://example.com/c2"


# --- content extraction ------------------------------------------------

def test_content_prefers_article_tag_with_good_text():
    s = soup("""
    <html><body>
      <nav>Home Library Login</nav>
      <article class="chapter-content">
        <p>%s</p><p>%s</p><p>%s</p>
      </article>
      <div class="comments">Someone said: nice chapter!</div>
    </body></html>
    """ % ("This is paragraph one of the chapter. " * 5,
           "This is paragraph two of the chapter. " * 5,
           "This is paragraph three of the chapter. " * 5))
    r = ex.extract_content(s)
    assert r.value is not None
    assert "paragraph one" in r.value
    assert "nice chapter" not in r.value
    assert r.confidence > 0.3


def test_content_detects_cloudflare_challenge_page():
    s = soup("<html><body><h1>Just a moment...</h1><p>Checking your browser before accessing example.com</p></body></html>")
    r = ex.extract_content(s)
    assert r.value is None
    assert r.method == "rejected"


def test_content_detects_suspiciously_short_page():
    s = soup("<html><body><p>hi</p></body></html>")
    r = ex.extract_content(s)
    assert r.value is None
    assert r.method == "rejected"


def test_looks_like_challenge_or_empty_direct():
    s = soup("<html><body><p>Please verify you are human to continue.</p></body></html>")
    assert ex.looks_like_challenge_or_empty(s) is not None
    normal = soup("<html><body><p>%s</p></body></html>" % ("This is a normal chapter. " * 10))
    assert ex.looks_like_challenge_or_empty(normal) is None


def test_clean_title_text_keeps_longer_fragment():
    assert ex.clean_title_text("Chapter 9: Fire - SmallSite") == "Chapter 9: Fire"
    assert ex.clean_title_text("SmallSite | Chapter 9: Fire and Ash") == "Chapter 9: Fire and Ash"
    assert ex.clean_title_text("No separator here") == "No separator here"
