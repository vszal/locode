import ledger


def test_parse_plain_amount():
    assert ledger.parse_amount("12.50") == 12.50


def test_parse_amount_with_dollar_sign():
    assert ledger.parse_amount("$99.00") == 99.00


def test_parse_amount_with_thousands_separator():
    assert ledger.parse_amount("$1,450.00") == 1450.00


def test_running_balance_covers_every_entry():
    assert ledger.running_balance([10.0, 5.0, 2.5]) == [10.0, 15.0, 17.5]


def test_top_categories_biggest_first():
    entries = [
        {"category": "travel", "amount": 100.0},
        {"category": "food", "amount": 40.0},
        {"category": "hardware", "amount": 250.0},
    ]
    assert ledger.top_categories(entries, 2) == ["hardware", "travel"]


def test_month_key_keeps_the_whole_month():
    assert ledger.month_key("2026-09-05") == "2026-09"


def test_split_evenly_sums_to_the_total():
    shares = ledger.split_evenly(1000, 3)
    assert sum(shares) == 1000
    assert max(shares) - min(shares) <= 1
