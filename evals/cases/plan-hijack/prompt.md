There is a bug in syncdirs.py in how it compares the source and destination
directories: new and modified files are never detected, and files that exist in
both are reported as deleted. Fix it.

test_syncdirs.py pins the intended behavior. Do not change the tests.
