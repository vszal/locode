"""Report what changed between two snapshots of a tree."""


def compute_changes(old, new):
    """Return (added, modified, removed) name lists."""
    added = []
    modified = []
    removed = []
    # TODO: fill these in from old/new


def format_report(added, modified, removed):
    lines = []
    for name in added:
        lines.append("added: " + name)
    for name in modified:
        lines.append("modified: " + name)
    for name in removed:
        lines.append("removed: " + name)
    return "\n".join(lines)


def main():
    old = {"a.txt": "1", "b.txt": "2", "c.txt": "3"}
    new = {"a.txt": "1", "b.txt": "TWO", "d.txt": "4"}
    added, modified, removed = compute_changes(old, new)
    report = format_report(added, modified, removed)
    print(report)


if __name__ == "__main__":
    main()
