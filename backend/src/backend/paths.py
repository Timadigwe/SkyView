"""Resolve paths to the minimal MCP server build artifact."""

from pathlib import Path


def _walk_find_mcp_index() -> Path | None:
    here = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = here / "solana-mcp-minimal" / "build" / "index.js"
        if candidate.is_file():
            return candidate
        if here.parent == here:
            break
        here = here.parent
    return None


def default_mcp_minimal_entry() -> Path:
    found = _walk_find_mcp_index()
    if found is not None:
        return found
    # Fallback: repo layout backend/src/backend/paths.py
    return (
        Path(__file__).resolve().parent.parent.parent.parent
        / "solana-mcp-minimal"
        / "build"
        / "index.js"
    )
