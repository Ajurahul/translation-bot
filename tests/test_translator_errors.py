from translator.errors import (
    filter_error_text,
    has_empty_parts,
    is_error_response,
    validate_and_clean,
)


def test_valid_translation_is_not_flagged_as_error():
    assert is_error_response(["Bonjour le monde"]) is False


def test_translation_containing_the_word_error_is_not_flagged():
    # Important per the task spec: a legitimate translation that happens
    # to contain the word "error" (e.g. a chapter about a software bug)
    # must not be misdetected as a provider error page.
    text = "The engineer found a critical error in the code and fixed it before the deadline."
    assert is_error_response([text]) is False
    assert filter_error_text(text) == text


def test_http_500_error_page_is_flagged():
    assert is_error_response(["Error 500: Server Error. That's an error."]) is True


def test_multiple_error_markers_flagged_even_without_explicit_500():
    assert is_error_response(["Server Error - please try again later"]) is True


def test_single_error_marker_alone_is_not_enough():
    # A single incidental marker phrase shouldn't be enough on its own to
    # reject an otherwise plausible translation.
    assert is_error_response(["please try again later, said the old man"]) is False


def test_empty_response_is_an_error():
    assert is_error_response([""]) is True
    assert is_error_response([None]) is True
    assert is_error_response([]) is False  # nothing to judge


def test_has_empty_parts():
    assert has_empty_parts(["hello", "", "world"]) is True
    assert has_empty_parts(["hello", None]) is True
    assert has_empty_parts(["hello", "world"]) is False


def test_filter_error_text_strips_embedded_error_fragment():
    text = "Chapter one begins.\nError 500: Server Error.\nChapter continues here."
    cleaned = filter_error_text(text)
    assert "Error 500" not in cleaned
    assert "Chapter one begins." in cleaned


def test_validate_and_clean_raises_on_pure_error_page():
    try:
        validate_and_clean(["Error 500: Server Error. That's an error. Please try again later."])
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_validate_and_clean_passes_through_good_text():
    out = validate_and_clean(["Hola mundo"])
    assert out == ["Hola mundo"]
