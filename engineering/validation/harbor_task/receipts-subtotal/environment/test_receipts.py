from receipts import subtotal, with_tax


def test_subtotal_adds_the_prices():
    assert subtotal([1.50, 2.25, 3.00]) == 6.75


def test_subtotal_of_nothing_is_zero():
    assert subtotal([]) == 0


def test_with_tax_adds_tax_to_the_subtotal():
    assert with_tax([10.00], 0.2) == 12.00
    assert with_tax([1.50, 2.50], 0.1) == 4.40
