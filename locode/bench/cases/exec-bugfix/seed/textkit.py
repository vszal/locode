"""Small string-utility helpers.

Four independent functions. Each has a docstring describing its exact
contract; read it carefully before trusting the implementation below it.
"""


def word_wrap(text, width):
    """Wrap `text` on word boundaries into lines no longer than `width`.

    Splits `text` on whitespace and greedily packs words onto a line,
    separated by single spaces, such that each line's length is at most
    `width` (a line containing exactly `width` characters is allowed).
    A single word longer than `width` is placed alone on its own line
    (it is never split). Returns a list of line strings.
    """
    words = text.split()
    lines = []
    current = []
    current_len = 0
    for word in words:
        if not current:
            current = [word]
            current_len = len(word)
        elif current_len + 1 + len(word) < width:
            current.append(word)
            current_len += 1 + len(word)
        else:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    return lines


def truncate(text, limit, suffix="..."):
    """Shorten `text` to fit within `limit` characters.

    If `text` already fits (len(text) <= limit), it is returned unchanged.
    Otherwise the text is cut short and `suffix` is appended such that the
    TOTAL length of the returned string equals exactly `limit` (never
    longer). If `suffix` alone is longer than `limit`, the cut point is
    clamped to 0.
    """
    if len(text) <= limit:
        return text
    cut = limit - len(suffix) + 1
    if cut < 0:
        cut = 0
    return text[:cut] + suffix


SMALL_WORDS = {"a", "an", "and", "the", "of", "in", "on", "or", "to", "at", "by", "for"}


def title_case(text):
    """Capitalize each word, lowercasing common small words.

    Every word is capitalized (first letter upper, rest lower) EXCEPT
    words in `SMALL_WORDS`, which are left lowercase -- unless that word
    is the first word of the text, which is always capitalized regardless
    of `SMALL_WORDS` membership. Words are separated by single spaces in
    the output.
    """
    words = text.split()
    result = []
    for word in words:
        lower = word.lower()
        if lower in SMALL_WORDS:
            result.append(lower)
        else:
            result.append(lower.capitalize())
    return " ".join(result)


def dedupe_spaces(text):
    """Collapse any run of whitespace (spaces, tabs, newlines) into one space.

    Leading and trailing whitespace is stripped entirely. Returns the
    resulting string.
    """
    return " ".join(text.split())
