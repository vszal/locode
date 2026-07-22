# envcfg — layered configuration loading

A small Python module, `envcfg`, that assembles one configuration dictionary
from three layers and reports clear errors when a value is wrong.

## The layers

Lowest priority is a dictionary of defaults supplied by the caller in code.
Above that, values parsed from a TOML file. Highest priority, values from
environment variables. A later layer overrides an earlier one key by key, so a
TOML file that sets only one key leaves the other defaults intact.

## Keys and nesting

Configuration is nested — a table in TOML becomes a nested dictionary. A nested
key is addressed from the environment by joining the path with a double
underscore and upper-casing it, under a prefix the caller chooses. With the
prefix APP, the environment variable APP__SERVER__PORT sets the port key inside
the server table.

## Coercion

Environment variables arrive as strings and must be coerced to match the type of
the default already present for that key. Integers, floats, booleans, and
strings are all supported. Booleans accept the obvious true and false spellings
in either case, plus one, zero, yes, and no. A value that cannot be coerced is
an error, not a silent fallback. An environment key with no corresponding
default is also an error — it is almost always a typo, and silently accepting it
is how a misspelled variable goes unnoticed.

## Errors

The loader raises a single custom exception type carrying a message that names
the offending key and says what was expected. Several problems in one load
should be reported together rather than one at a time, so the caller fixes them
all in one pass.

## The public surface

One function that takes the defaults dictionary, an optional path to a TOML
file, an environment prefix, and optionally the environment mapping to read
from, and returns the merged dictionary. A missing TOML file is not an error;
the layer is simply skipped. One exception class for configuration problems.

## Constraints

Python 3.10+, standard library only — use tomllib for the TOML parsing. Keep the
module under about 150 lines. Ship tests covering the precedence order, nested
overrides, every coercion type, both error cases, and the missing-file case.
