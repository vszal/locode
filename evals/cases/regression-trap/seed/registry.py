"""A small name -> value registry.

Names are typed by hand in two places -- once when an entry is added and again
when it is looked up -- so they are normalised before use. The normalised form
is also what gets listed back to the user, so it has to stay readable.
"""


def normalize_key(raw):
    """Trim a hand-typed name and collapse any run of whitespace to one space."""
    return " ".join(raw.split())


class Registry:
    def __init__(self):
        self._items = {}

    def add(self, name, value):
        """Add or replace an entry."""
        self._items[normalize_key(name)] = value

    def lookup(self, name):
        """The value for `name`, or None."""
        return self._items.get(normalize_key(name))

    def labels(self):
        """Every entry's name in normalised form, keeping the capitalisation
        it was entered with, in alphabetical order."""
        return sorted(self._items)

    def __len__(self):
        return len(self._items)
