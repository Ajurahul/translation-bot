import json
import os

from translator import settings


def _point_settings_at(monkeypatch, tmp_path):
    settings_dir = tmp_path / "config"
    settings_path = settings_dir / "translation_settings.json"
    monkeypatch.setattr(settings, "_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setattr(settings, "_SETTINGS_PATH", str(settings_path))
    return settings_path


def test_missing_settings_file_falls_back_to_default(monkeypatch, tmp_path):
    _point_settings_at(monkeypatch, tmp_path)
    assert settings.get_default_engine() == settings.FALLBACK_DEFAULT_ENGINE


def test_set_and_get_default_engine(monkeypatch, tmp_path):
    path = _point_settings_at(monkeypatch, tmp_path)
    settings.set_default_engine("googletrans")
    assert settings.get_default_engine() == "googletrans"
    assert os.path.exists(path)


def test_default_engine_persists_across_reload(monkeypatch, tmp_path):
    # Simulates "admin changes -> bot restarts -> loads persisted value".
    _point_settings_at(monkeypatch, tmp_path)
    settings.set_default_engine("bing")
    # A "restart" is just re-reading the file fresh; there is no
    # in-memory cache to invalidate, so this alone proves persistence.
    assert settings.get_default_engine() == "bing"

    settings.set_default_engine("deep_translator")
    assert settings.get_default_engine() == "deep_translator"


def test_corrupted_settings_file_falls_back_gracefully(monkeypatch, tmp_path):
    path = _point_settings_at(monkeypatch, tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        fp.write("{not valid json,,,")
    assert settings.get_default_engine() == settings.FALLBACK_DEFAULT_ENGINE


def test_settings_file_that_is_not_a_json_object_falls_back(monkeypatch, tmp_path):
    path = _point_settings_at(monkeypatch, tmp_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fp:
        json.dump([1, 2, 3], fp)
    assert settings.get_default_engine() == settings.FALLBACK_DEFAULT_ENGINE


def test_never_crashes_when_settings_dir_does_not_exist(monkeypatch, tmp_path):
    _point_settings_at(monkeypatch, tmp_path / "does" / "not" / "exist")
    # get_default_engine must not raise even though the directory tree
    # doesn't exist at all yet.
    assert settings.get_default_engine() == settings.FALLBACK_DEFAULT_ENGINE
    # set_default_engine must create the directory tree itself.
    settings.set_default_engine("bing")
    assert settings.get_default_engine() == "bing"


def test_set_default_engine_rejects_empty_value(monkeypatch, tmp_path):
    _point_settings_at(monkeypatch, tmp_path)
    try:
        settings.set_default_engine("")
        assert False, "expected ValueError"
    except ValueError:
        pass
