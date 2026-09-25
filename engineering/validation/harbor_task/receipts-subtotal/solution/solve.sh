#!/bin/bash
# The reference solution, which Harbor runs to check the task is solvable.
set -eu

cat > /app/receipts.py <<'PYEOF'
"""Totals for a shop's receipts."""


def subtotal(prices):
    """The sum of the prices."""
    total = 0
    for price in prices:
        total += price
    return total


def with_tax(prices, rate):
    """The subtotal plus tax at ``rate`` (0.2 for 20%), rounded to cents."""
    amount = subtotal(prices)
    return round(amount + amount * rate, 2)
PYEOF
