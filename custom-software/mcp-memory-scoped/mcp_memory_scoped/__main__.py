"""Entry point for the mcp-memory-scoped stdio server."""

from mcp_memory_scoped.server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
