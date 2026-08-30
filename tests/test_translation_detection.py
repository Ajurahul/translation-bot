import asyncio

import translation.detection as detection


def run(coro):
    return asyncio.run(coro)


def test_detects_english():
    code = run(detection.detect_language_code(
        "The quick brown fox jumps over the lazy dog repeatedly, again and again."
    ))
    assert code == "en"


def test_detects_chinese_simplified():
    code = run(detection.detect_language_code(
        "这是一个测试文本用于检测语言是否可以被正确识别出来。"
    ))
    assert code in ("zh-cn", "zh")


def test_detects_french():
    code = run(detection.detect_language_code(
        "Ceci est un texte de test pour la détection automatique de la langue."
    ))
    assert code == "fr"


def test_empty_text_returns_na():
    assert run(detection.detect_language_code("")) == "NA"
    assert run(detection.detect_language_code("   ")) == "NA"
    assert run(detection.detect_language_code(None)) == "NA"


def test_too_short_sample_is_skipped_not_misdetected():
    # A handful of characters shouldn't produce a confident (and likely
    # wrong) result -- it should just fall through to "NA" rather than
    # guessing.
    assert run(detection.detect_language_code("hi")) == "NA"


def test_multiple_samples_first_usable_one_wins():
    code = run(
        detection.detect_language_code(
            samples=["", "   ", "Bonjour tout le monde, comment allez-vous aujourd'hui ?"]
        )
    )
    assert code == "fr"


def test_first_detector_failure_falls_through_to_second(monkeypatch):
    async def _broken(sample):
        raise RuntimeError("simulated langdetect crash")

    async def _fallback(sample):
        return "de"

    monkeypatch.setattr(
        detection,
        "_DETECTORS",
        (("broken", _broken), ("fallback", _fallback)),
    )
    assert run(detection.detect_language_code("Guten Tag, wie geht es Ihnen heute?")) == "de"


def test_all_detectors_returning_none_yields_na(monkeypatch):
    async def _none(sample):
        return None

    monkeypatch.setattr(detection, "_DETECTORS", (("a", _none), ("b", _none)))
    assert run(detection.detect_language_code("some reasonably long text here")) == "NA"


def test_unusable_codes_are_treated_as_no_result(monkeypatch):
    async def _unknown(sample):
        return "un"

    async def _good(sample):
        return "es"

    monkeypatch.setattr(detection, "_DETECTORS", (("a", _unknown), ("b", _good)))
    assert run(detection.detect_language_code("Hola, como estas hoy? Espero que bien.")) == "es"
