from bs4 import BeautifulSoup

from utils import parsing


def test_make_soup_basic_lxml():
    soup = parsing.make_soup("<html><body><p>hello</p></body></html>")
    assert soup.find("p").get_text() == "hello"


def test_make_soup_malformed_html_does_not_raise():
    # unclosed tags, stray characters - should not raise regardless of parser
    soup = parsing.make_soup("<div><p>unclosed <span>nested</div>")
    assert soup is not None
    assert "nested" in soup.get_text()


def test_make_soup_empty_input():
    soup = parsing.make_soup("")
    assert soup is not None
    assert soup.get_text() == ""


def test_make_soup_none_input():
    soup = parsing.make_soup(None)
    assert soup is not None


def test_make_soup_falls_back_when_lxml_unavailable(monkeypatch):
    monkeypatch.setattr(parsing, "_lxml_available", lambda: False)
    soup = parsing.make_soup("<p>plain</p>")
    assert soup.find("p").get_text() == "plain"


def test_make_soup_falls_back_when_lxml_raises(monkeypatch):
    calls = []

    real_bs = BeautifulSoup

    def fake_bs(markup, parser, **kwargs):
        calls.append(parser)
        if parser == "lxml":
            raise RuntimeError("simulated lxml failure")
        return real_bs(markup, parser, **kwargs)

    monkeypatch.setattr(parsing, "_lxml_available", lambda: True)
    monkeypatch.setattr(parsing, "BeautifulSoup", fake_bs)

    soup = parsing.make_soup("<p>fallback test</p>")
    assert calls == ["lxml", "html.parser"]
    assert soup.find("p").get_text() == "fallback test"


def test_make_soup_bytes_input_with_encoding():
    soup = parsing.make_soup("<p>caf\u00e9</p>".encode("utf-8"), from_encoding="utf-8")
    assert "café" in soup.get_text()
