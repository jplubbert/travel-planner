"""Architecture rules from CLAUDE.md and ADR 0002, checked on the source."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "backend" / "app"
FRAMEWORKS = ("fastapi", "starlette", "sqlalchemy", "httpx", "asyncpg", "alembic", "mcp",
              "anthropic", "pydantic")


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


def test_application_never_calls_mock_control_endpoints():
    offenders = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
                 if "/_control" in p.read_text(encoding="utf-8")]
    assert offenders == []
