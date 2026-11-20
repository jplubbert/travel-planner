"""Architecture rules from CLAUDE.md and ADR 0002, checked on the source."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "backend" / "app"
FRAMEWORKS = ("fastapi", "starlette", "sqlalchemy", "httpx", "httpx2", "asyncpg", "alembic",
              "mcp", "mcp_types", "google", "anthropic", "pydantic")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("layer", ["domain", "ports"])
def test_core_does_not_import_frameworks_or_adapters(layer):
    offenders = {}
    for path in (APP / layer).rglob("*.py"):
        bad = {
            m for m in imported_modules(path)
            if m.split(".")[0] in FRAMEWORKS
            or m.startswith(("app.adapters", "app.api", "app.mcp", "app.workers"))
        }
        if bad:
            offenders[str(path.relative_to(APP))] = sorted(bad)
    assert offenders == {}


def test_every_third_party_import_of_the_application_is_a_declared_dependency():
    """A package the code imports must not arrive only as another package's dependency."""
    import re
    import sys
    import tomllib
    from importlib.metadata import packages_distributions

    root = APP.parents[1]
    declared = {re.split(r"[\[<>=!~ ;]", d, maxsplit=1)[0].lower().replace("_", "-")
                for d in tomllib.loads((root / "pyproject.toml").read_text())["project"][
                    "dependencies"]}
    distributions = packages_distributions()
    undeclared = {}
    for path in APP.rglob("*.py"):
        for module in imported_modules(path):
            top = module.split(".")[0]
            if top == "app" or top in sys.stdlib_module_names:
                continue
            owners = {d.lower().replace("_", "-") for d in distributions.get(top, [top])}
            if not owners & declared:
                undeclared[top] = sorted(owners)
    assert undeclared == {}


def test_application_never_calls_mock_control_endpoints():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
                 if "/_control" in p.read_text(encoding="utf-8")]
    assert offenders == []


# --- MCP layer (ADR 0006): read-only by construction -------------------------------------------

MCP_DOMAIN_READS = {
    "TripAccess", "load_access", "parse_trip_id", "DomainError", "NotFound", "ValidationFailed",
    "ProviderError", "SearchInput", "SearchOutcomeKind",
    # read use cases only:
    "get_trip", "list_trips", "get_itinerary", "get_costs", "list_bookings", "list_documents",
    "search_offers",
}
WRITE_USE_CASES = {
    "create_trip", "update_trip", "set_member_role", "remove_member", "add_item",
    "remove_item", "reorder_items", "refresh_item", "request_booking", "retry_booking", "send",
    "sync_booking", "request_document", "retry_document", "download_document",
    "generate_document", "sweep_documents", "sweep_stale_submitted",
}


def mcp_imports() -> list[tuple[str, str, str | None]]:
    """(file, module, imported name) for every import in app/mcp."""
    found = []
    for path in (APP / "mcp").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found += [(path.name, alias.name, None) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                found += [(path.name, node.module, alias.name) for alias in node.names]
    return found


def test_mcp_layer_imports_only_read_use_cases():
    offenders = []
    for file, module, name in mcp_imports():
        if module.startswith("app.domain") and (
                name is None or name not in MCP_DOMAIN_READS or name in WRITE_USE_CASES):
            offenders.append((file, module, name))
        if module.startswith("app.api") and not (name or "").endswith("_out"):
            offenders.append((file, module, name))  # serializers only, never routes
    assert offenders == []


def test_write_use_cases_are_not_in_the_read_allowlist():
    assert not MCP_DOMAIN_READS & WRITE_USE_CASES


async def test_mcp_tools_are_exactly_the_read_only_set():
    from datetime import UTC, datetime

    from app.mcp.server import EXPECTED_TOOLS, build_mcp_server

    class Clock:
        def now(self) -> datetime:
            return datetime.now(UTC)

    server = build_mcp_server(None, None, Clock(), "secret")  # type: ignore[arg-type]
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS == {
        "list_my_trips", "get_trip", "get_itinerary", "get_costs", "get_bookings",
        "get_documents", "search_offers"}
    for tool in tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        properties = set(tool.input_schema.get("properties", {}))
        # The acting user is never a tool argument: the model cannot choose it.
        assert not properties & {"user", "user_id", "userId", "actor", "as_user", "token",
                                 "ctx", "context"}, (tool.name, properties)


def test_assistant_core_does_not_depend_on_a_model_provider():
    for path in (APP / "assistant").rglob("*.py"):
        mods = imported_modules(path)
        assert not {m for m in mods if m.split(".")[0] in ("google", "anthropic", "openai",
                                                           "mcp", "httpx", "httpx2")}, path
