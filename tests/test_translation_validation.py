import pytest

from translation.errors import PermanentTranslationError, TransientTranslationError, is_error_response
from translation.validation import validate_translation, validate_translation_batch


def test_http_500_error_page_is_rejected():
    body = ["Error 500", "Server Error", "That's an error", "That's all we know."]
    assert is_error_response(body) is True
    with pytest.raises(TransientTranslationError):
        validate_translation("hello", " ".join(body))


def test_google_error_page_text_is_rejected():
    text = "Server Error\nThat's an error. There was an error. Please try again later."
    assert is_error_response([text]) is True
    with pytest.raises(TransientTranslationError):
        validate_translation("some text", text)


def test_valid_translation_containing_the_word_error_is_accepted():
    # A single, legitimate occurrence of "error" must never trip the
    # detector -- only "error 500" or >=2 distinct indicator phrases do.
    text = "The error handling code retries the request automatically."
    assert is_error_response([text]) is False
    assert validate_translation("original text", text) == text


def test_empty_response_is_rejected():
    with pytest.raises(PermanentTranslationError):
        validate_translation("non-empty original", "")


def test_none_response_is_rejected():
    with pytest.raises(PermanentTranslationError):
        validate_translation("non-empty original", None)


def test_empty_original_allows_empty_translation():
    # Translating whitespace-only input legitimately can yield an empty
    # result; only reject empty output when the input actually had content.
    assert validate_translation("   ", "") == ""


def test_batch_rejects_none():
    with pytest.raises(PermanentTranslationError):
        validate_translation_batch(["a", "b"], None)


def test_batch_rejects_error_page():
    from translation.errors import TranslationError

    with pytest.raises(TranslationError):
        validate_translation_batch(
            ["a", "b"], ["Error 500", "Server Error. That's an error."]
        )


def test_batch_accepts_valid_translations():
    result = validate_translation_batch(["hola", "mundo"], ["hello", "world"])
    assert result == ["hello", "world"]


def test_batch_rejects_single_empty_item_when_original_non_empty():
    with pytest.raises(PermanentTranslationError):
        validate_translation_batch(["hola", "mundo"], ["hello", ""])
