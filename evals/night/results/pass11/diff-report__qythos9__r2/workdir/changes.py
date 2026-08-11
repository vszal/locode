"""Report what changed between two snapshots of a tree."""


def compute_changes(old, new):
    """Return (added, modified, removed) name lists."""
    added = []
    modified = []
    removed = []
    for name in new:
        if name not in old:
            added.append(name)
        elif old[name] != new[name]:
            modified.append(name)
    for name in old:
        if name not in new:
            removed.append(name)
    return added, modified, removed


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
