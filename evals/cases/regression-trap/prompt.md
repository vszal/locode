Looking someone up in the registry only works if you type their name with
exactly the capitalisation it was entered with -- `lookup("ACME corp")` misses
an entry added as `"Acme Corp"`. It should find it.

Fix registry.py. Do not edit test_registry.py.
