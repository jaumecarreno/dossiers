"""Local completeness checks. No model calls or paid verification."""
from __future__ import annotations

from functools import lru_cache
import tiktoken


class IncompleteTranscription(RuntimeError):
    pass


class EmptyTranscription(IncompleteTranscription):
    pass


@lru_cache(maxsize=1)
def tokenizer():
    return tiktoken.get_encoding("o200k_base")


def validate_transcription(text: str, model: str, output_tokens: int | None = None) -> None:
    if not text.strip():
        raise EmptyTranscription("Respuesta vacia; no se ha confirmado la transcripcion del audio.")
    # Reserve margin below the documented 2,000-token output limit. Counting locally
    # also protects responses without usage metadata (including the 2,047-token incident).
    if model.startswith(("gpt-4o-transcribe", "gpt-4o-mini-transcribe")):
        count = len(tokenizer().encode(text, disallowed_special=()))
        if max(count, output_tokens or 0) >= 1900:
            raise IncompleteTranscription("Respuesta cerca del limite de salida; posible transcripcion recortada.")
