"""Runtime-selectable translation engine subsystem.

Public surface:

    from translation.manager import TranslationManager
    from translation.registry import registry
    from translation.config import settings
    from translation.errors import TranslationError, TranslationFailedError, AllEnginesFailedError

See translation/manager.py for the three selection modes (Default / Auto /
explicit engine) and docs/TRANSLATION_ENGINES.md for the full write-up.
"""
