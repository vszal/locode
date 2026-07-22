# Plan: finish stats.py

`stats.py` already has one finished function, `mean`, which you should treat
as the reference example for style: a clear docstring, a `ValueError` raised
on empty input, and a plain-Python implementation with no imports needed.
Implement the four tasks below in `stats.py`. Do not change `mean` or the
existing tests in `test_stats.py`; just make the failing tests in
`test_stats.py` pass.

## Task 1: `median`

Add a function named `median` that takes one argument, a list of numbers
(ints or floats), and returns a float: the middle value of the list once
sorted. If the list has an odd number of elements, return the single middle
element (as a float). If the list has an even number of elements, return the
average of the two middle elements. The input list must not be modified in
place, so sort a copy rather than sorting the argument itself. If the list is
empty, raise a ValueError with a message saying the input must not be empty,
matching the style used in `mean`.

## Task 2: `mode`

Add a function named `mode` that takes one argument, a list of numbers, and
returns the single value that occurs most often in the list. If there is a
tie for the most common value, return the smallest of the tied values, not
just whichever one happened to appear first. If the list is empty, raise a
ValueError the same way `mean` and `median` do.

## Task 3: `summary`

Add a function named `summary` that takes one argument, a list of numbers,
and returns a dictionary describing the list. The dictionary must have
exactly these five keys: "n" mapping to the number of elements in the list
as an integer, "mean" mapping to the result of calling `mean` on the list,
"median" mapping to the result of calling `median` on the list, "mode"
mapping to the result of calling `mode` on the list, "min" mapping to the
smallest value in the list, and "max" mapping to the largest value in the
list. Reuse the three functions above rather than recomputing their logic.
If the list is empty, raise a ValueError the same way the others do (letting
the ValueError from one of the helper calls propagate is fine).

## Task 4: keep everything import-free and dependency-free

None of the four functions should import anything -- no `statistics` module,
no third-party packages. Everything can be written with built-in list,
sorting, and counting operations only. When you are done, run the test
command below and confirm every test passes.

Test command: `python -m pytest -q test_stats.py`
