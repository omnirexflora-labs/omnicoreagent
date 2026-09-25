"""A streamable-HTTP MCP server with one made-up VAT rate, in its own container."""

from mcp.server.fastmcp import FastMCP

RATES = {"kestria": 0.0625}

server = FastMCP("ledger", host="0.0.0.0", port=8000)


@server.tool()
def ledger_vat_rate(country: str) -> float:
    """The ledger's VAT rate for ``country``, as a fraction (0.2 is 20%)."""
    key = country.strip().lower()
    if key not in RATES:
        raise ValueError(f"the ledger has no rate for {country!r}")
    return RATES[key]


if __name__ == "__main__":
    server.run(transport="streamable-http")
