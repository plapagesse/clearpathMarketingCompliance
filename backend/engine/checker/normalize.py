"""Text normalization for text_plane checks.

Implements the rulebook README's normative pipeline (one pipeline, applied
identically everywhere, in order): NFKC -> HTML-entity decode -> quote/apostrophe
normalization -> lowercase -> whitespace collapse. Matching is word-boundary
aware where patterns say so; pluralization is never implied.
"""

from __future__ import annotations

import html
import re
import unicodedata

_QUOTE_MAP = str.maketrans({
    "’": "'", "‘": "'",   # curly single quotes
    "“": '"', "”": '"',   # curly double quotes
    "—": "-", "–": "-", "−": "-",  # em/en dash, minus
    " ": " ",                   # nbsp (collapsed below anyway)
})


def normalize(text: str) -> str:
    """The README normalization pipeline, in its specified order."""
    s = unicodedata.normalize("NFKC", text)
    s = html.unescape(s)
    s = s.translate(_QUOTE_MAP)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    """Compile a pattern set (case-insensitive). Plain-word entries act as
    substrings — every entry in data/patterns.json is a valid regex, so one
    compile path serves both."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def pattern_spans(patterns: list[str], text: str) -> list[tuple[int, int, str]]:
    """All (start, end, matched_text) spans for any pattern in the set."""
    spans: list[tuple[int, int, str]] = []
    for rx in compile_patterns(patterns):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), m.group(0)))
    spans.sort()
    return spans


def any_pattern_match(patterns: list[str], text: str) -> str | None:
    """First matched text for any pattern, or None."""
    for rx in compile_patterns(patterns):
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def phrase_hits(phrases: list[str], text: str, match: str) -> list[str]:
    """Phrases that hit `text` under the rule's declared match mode.

    `case_insensitive_substring`: normalized-substring containment.
    `case_insensitive_regex`: regex search.
    Text is expected to be normalized already for substring mode.
    """
    hits: list[str] = []
    if match == "case_insensitive_regex":
        for p in phrases:
            if re.search(p, text, re.IGNORECASE):
                hits.append(p)
    else:  # case_insensitive_substring (default)
        # Word-boundary aware per the README normalization spec: pluralization
        # is never implied ("counselors" must not hit the phrase "counselor").
        for p in phrases:
            if re.search(rf"\b{re.escape(normalize(p))}\b", text):
                hits.append(p)
    return hits
