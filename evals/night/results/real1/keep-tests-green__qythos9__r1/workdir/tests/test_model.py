from invoice.model import Invoice, LineItem, from_dict


def test_line_total():
    item = LineItem("widget", 3, 2.5)
    assert item.line_total() == 7.5

def test_line_total_with_discount():
    item = LineItem("widget", 3, 2.5, discount=0.1)
    assert item.line_total() == 6.75


def test_subtotal():
    inv = Invoice(number="A-1", customer="Acme")
    inv.add_item("widget", 2, 10.0)
    inv.add_item("gadget", 1, 5.0)
    assert inv.subtotal() == 25.0


def test_from_dict():
    inv = from_dict({"number": "A-2", "customer": "Beta",
                     "items": [{"description": "bolt", "quantity": 4,
                                "unit_price": 1.25}]})
    assert inv.number == "A-2"
    assert len(inv.items) == 1
