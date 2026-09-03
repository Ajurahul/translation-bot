"""Split text into chunks under a character limit, preferring to break on
paragraph, then sentence, then word boundaries -- never mid-word unless a
single "word" (or the text has no whitespace/punctuation at all) is
itself longer than the limit, in which case a hard split is the only
option left.

Used by the MyMemory backend (translation/providers/deep_translator_backend.py),
which rejects any single request over ~500 characters.
"""
import typing as t

# Separators tried in order, coarsest first. Each entry keeps its
# separator attached to the piece that precedes it (so rejoining chunks
# with "".join(...) reproduces the original text exactly).
_BOUNDARIES: t.Tuple[str, ...] = ("\n\n", "\n", ". ", "! ", "? ", " ")


def split_text(text: str, limit: int) -> t.List[str]:
    """Split `text` into chunks no longer than `limit` characters.
    `"".join(split_text(text, limit)) == text` always holds."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    for sep in _BOUNDARIES:
        if sep not in text:
            continue
        parts = text.split(sep)
        chunks: t.List[str] = []
        current = ""
        for i, part in enumerate(parts):
            piece = part + (sep if i < len(parts) - 1 else "")
            if not piece:
                continue
            if len(current) + len(piece) <= limit:
                current += piece
                continue
            if current:
                chunks.append(current)
                current = ""
            if len(piece) <= limit:
                current = piece
            else:
                # This single part is still too long on its own --
                # recurse with a finer-grained boundary.
                chunks.extend(split_text(piece, limit))
        if current:
            chunks.append(current)
        if chunks and all(len(c) <= limit for c in chunks):
            return chunks

    # Last resort: no whitespace/punctuation boundary got every piece
    # under the limit (e.g. one pathologically long "word") -- hard
    # split. Unavoidable, but only ever reached in that edge case.
    return [text[i : i + limit] for i in range(0, len(text), limit)]
