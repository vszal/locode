store.get returns None for a key that isn't there, which quietly hides typos.
Change it to raise KeyError for a missing key instead.

report.py, cli.py and cache.py all call it. They must keep behaving exactly as
they do today: report.line still gives `key=?` for an unknown key, cli.run
still skips unknown keys silently, and cache.warm still leaves them out.
Nothing about the behaviour for keys that do exist should change.
