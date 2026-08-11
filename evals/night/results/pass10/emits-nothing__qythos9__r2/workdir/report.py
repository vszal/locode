def build_report():
    rows = ["item: 1", "item: 2"]
    return "\n".join(rows)

def main():
    report = build_report()
    print(report)

if __name__ == "__main__":
    main()
