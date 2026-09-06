from registry import Registry, normalize_key


def test_normalize_collapses_whitespace():
    assert normalize_key("  Acme   Corp \n") == "Acme Corp"


def test_lookup_finds_an_entry():
    reg = Registry()
    reg.add("Acme Corp", 1)
    assert reg.lookup("Acme Corp") == 1


def test_lookup_ignores_capitalisation():
    reg = Registry()
    reg.add("Acme Corp", 1)
    assert reg.lookup("ACME corp") == 1


def test_labels_preserve_the_name_as_entered():
    reg = Registry()
    reg.add("Acme Corp", 1)
    reg.add("Zebra Ltd", 2)
    assert reg.labels() == ["Acme Corp", "Zebra Ltd"]


def test_adding_a_case_variant_replaces_the_entry():
    reg = Registry()
    reg.add("Acme Corp", 1)
    reg.add("acme corp", 2)
    assert len(reg) == 1
    assert reg.lookup("Acme Corp") == 2
