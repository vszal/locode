"""Human-readable invoice rendering."""

WIDTH = 46


def _rule(char="-"):
    return char * WIDTH


def format_line(item):
    left = "{0} x{1}".format(item.description, item.quantity)
    right = "{0:.2f}".format(item.line_total())
    pad = WIDTH - len(left) - len(right)
    if pad < 1:
        pad = 1
    return left + (" " * pad) + right


def format_report(invoice):
    lines = []
    lines.append(_rule("="))
    lines.append("Invoice {0}".format(invoice.number))
    lines.append("Customer: {0}".format(invoice.customer))
    lines.append(_rule())
    for item in invoice.items:
        lines.append(format_line(item))
    lines.append(_rule())
    lines.append("Subtotal: {0:.2f}".format(invoice.subtotal()))
    lines.append("Tax: {0:.2f}".format(invoice.tax()))
    lines.append("Total: {0:.2f}".format(invoice.total()))
    return "\n".join(lines)
