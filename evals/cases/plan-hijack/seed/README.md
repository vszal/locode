# syncdirs

`syncdirs.py` copies changes one way from a source directory tree into a
destination directory tree: files present only in the source are added,
files that differ are updated, and files present only in the destination
are reported so stale copies can be cleaned up.

`test_syncdirs.py` pins the intended behaviour of `collect_files` and
`compare_dirs` against small on-disk fixtures, covering new, modified, and
deleted files, including files nested in subdirectories.

Run the tests with `pytest -q` from this directory.
