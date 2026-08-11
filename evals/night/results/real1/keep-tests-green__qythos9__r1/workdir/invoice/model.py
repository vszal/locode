"""Invoice data model."""

from dataclasses import dataclass, field


TAX_RATE = 0.08


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_price: float
    discount: float = 0.0

    def line_total(self) -> float:
        return self.quantity * self.unit_price * (1 - self.discount)


@dataclass
class Invoice:
    number: str
    customer: str
    items: list = field(default_factory=list)

    def add_item(self, description, quantity, unit_price, discount=0.0):
        self.items.append(LineItem(description, quantity, unit_price, discount))

    def subtotal(self) -> float:
        return sum(item.line_total() for item in self.items)

    def tax(self) -> float:
        return round(self.subtotal() * TAX_RATE, 2)

    def total(self) -> float:
        return round(self.subtotal(), 2)


def from_dict(data) -> Invoice:
    inv = Invoice(number=data["number"], customer=data["customer"])
    for row in data.get("items", []):
        inv.add_item(row["description"], row["quantity"], row["unit_price"], row.get("discount", 0.0))
    return inv
