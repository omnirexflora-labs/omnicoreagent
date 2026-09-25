"""A stdio MCP server with one made-up VAT rate."""

from mcp.server.fastmcp import FastMCP

RATES = {"zeldovia": 0.137}

server = FastMCP("archive")


@server.tool()
def archived_vat_rate(country: str) -> float:
    """The archived VAT rate of ``country``, as a fraction (0.2 is 20%)."""
    key = country.strip().lower()
    if key not in RATES:
        raise ValueError(f"the archive has no rate for {country!r}")
    return RATES[key]


if __name__ == "__main__":
    server.run()
