TAX_RATE = 0.1

def subtotal(items):
    total = 0
    for price, qty in items:
        total += price * qty
    return total

def apply_tax(amount):
    return round(amount * (1 + TAX_RATE), 2)

def apply_discount(amount, pct):
    # pct is a percentage off, e.g. 20 means 20% off
    return round(amount - amount * (pct / 100), 2)

def shipping(amount):
    if amount >= 100:
        return 0.0
    return 5.0

def line_label(name, qty):
    return name + ' x' + str(qty)

def order_total(items, pct):
    s = subtotal(items)
    discounted = apply_discount(s, pct)
    taxed = apply_tax(discounted)
    return round(taxed + shipping(discounted), 2)
