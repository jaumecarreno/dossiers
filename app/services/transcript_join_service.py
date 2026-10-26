import string


def dedupe_overlap(previous_text: str, current_text: str,
                   max_words: int = 40, min_words: int = 3) -> str:
    """Remove only exact suffix/prefix matches; never fuzzy-match away new speech."""
    previous_words, current_words = previous_text.split(), current_text.split()
    normalize = lambda word: word.strip(string.punctuation + "¿¡“”‘’«»").casefold()
    previous = [normalize(word) for word in previous_words[-max_words:]]
    current = [normalize(word) for word in current_words[:max_words]]
    for count in range(min(len(previous), len(current)), min_words - 1, -1):
        if previous[-count:] == current[:count]:
            return " ".join(current_words[count:]).strip()
    return current_text


def append_transcript(transcripts: list[str], text: str) -> int:
    """Shared canonical join for processing and every exported representation."""
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    original_count = len(cleaned.split())
    if transcripts:
        cleaned = dedupe_overlap("\n\n".join(transcripts), cleaned)
    if cleaned:
        transcripts.append(cleaned)
    return original_count - len(cleaned.split())
