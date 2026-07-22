"""A tiny line-oriented record parser.

`parse_records` has a structural bug: fixing it requires reorganizing the
loop's control flow, not tweaking a comparison or a string literal.
"""


def parse_records(text):
    """Parse `text` into a list of record dicts.

    Format: `text` is a sequence of lines. Each non-blank line is a
    "key: value" pair belonging to the current record -- the key is
    everything before the first colon, the value is everything after,
    both stripped of surrounding whitespace. One or more consecutive
    blank lines separate one record from the next (multiple blank lines
    in a row are equivalent to a single separator and never produce an
    empty record on their own). A record with at least one field is
    included in the result, in the order it was encountered, as a dict
    of its fields. Leading/trailing blank lines in `text` do not produce
    empty records. If `text` has no fields at all, an empty list is
    returned.
    """
    records = []
    current = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "":
            records.append(current)
            current = {}
        else:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    return records
