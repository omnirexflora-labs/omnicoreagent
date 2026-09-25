"""Totals for a shop's receipts."""


def subtotal(prices):
    """The sum of the prices."""
    total = 0
    for price in prices:
        total -= price          # a bug: it subtracts
    return total


def with_tax(prices, rate):
    """The subtotal plus tax at ``rate`` (0.2 for 20%), rounded to cents."""
    return round(subtotal(prices) * rate, 2)      # a bug: it drops the subtotal
