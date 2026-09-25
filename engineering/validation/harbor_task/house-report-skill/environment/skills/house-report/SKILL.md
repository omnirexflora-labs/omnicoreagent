---
name: house-report
description: The house format for a sales report. Use it whenever a sales report is asked for.
---

# The house report format

A house report is a plain text file of exactly three lines:

1. `HOUSE-REPORT v3`
2. `revenue=<total>` — the sum of units × unit_price over every row, with two
   decimals (for example `revenue=12.50`).
3. `top=<product>` — the product with the most revenue once its rows are added
   together.

No other lines, no trailing spaces.
