"""Invoice CLI front end."""

import argparse
import json
import sys

from invoice.model import from_dict
from invoice.report import format_report


def load_invoice(path):
    with open(path) as handle:
        return from_dict(json.load(handle))


def cmd_report(args):
    invoice = load_invoice(args.path)
    print(format_report(invoice))
    return 0


def cmd_total(args):
    invoice = load_invoice(args.path)
    print("{0:.2f}".format(invoice.total()))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="invoice")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="print the full invoice report")
    report.add_argument("path")
    report.set_defaults(func=cmd_report)

    total = sub.add_parser("total", help="print just the invoice total")
    total.add_argument("path")
    total.set_defaults(func=cmd_total)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
