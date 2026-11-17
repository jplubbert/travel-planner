import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from provider_mock.app import create_app
from provider_mock.clock import Clock
from provider_mock.settings import Settings

SPEC_PATH = Path(__file__).resolve().parents[2] / "assignment" / "openAPI.json"
SPEC_URI = "urn:travel-provider-openapi"
TOKEN = "travel-demo-token"
OTHER_TOKEN = "other-caller-token"
START = datetime(2030, 6, 1, 12, 0, tzinfo=UTC)


class Contract:
    """Validates real responses against assignment/openAPI.json (JSON Schema 2020-12)."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.registry: Registry = Registry().with_resource(
            SPEC_URI, Resource.from_contents(spec, default_specification=DRAFT202012)
        )

    def _deref(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.spec
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def validator(self, ref: str) -> Draft202012Validator:
        return Draft202012Validator(
            {"$ref": SPEC_URI + ref},
            registry=self.registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )

    def errors(self, ref: str, instance: Any) -> list[str]:
        return [e.message for e in self.validator(ref).iter_errors(instance)]

    def check(self, response: httpx.Response, method: str, path: str) -> Any:
        """Assert the status is declared for the operation and the body/headers match it."""
        operation = self.spec["paths"][path][method.lower()]
        status = str(response.status_code)
        assert status in operation["responses"], (
            f"{method} {path}: undeclared status {status}: {response.text}"
        )
        declared = self._deref(operation["responses"][status])
        for header in declared.get("headers", {}):
            assert header in response.headers, f"missing {header} header"
            if header == "Retry-After":
                assert int(response.headers[header]) >= 1
        schema_ref = declared["content"]["application/json"]["schema"]["$ref"]
        body = response.json()
        problems = self.errors(schema_ref, body)
        assert not problems, f"{method} {path} {status} violates {schema_ref}: {problems}"
        return body

    def check_request(self, schema_name: str, body: Any) -> None:
        """Guard against tests that unknowingly send bodies the contract forbids."""
        problems = self.errors(f"#/components/schemas/{schema_name}", body)
        assert not problems, f"test request violates {schema_name}: {problems}"


@pytest.fixture(scope="session")
def contract() -> Contract:
    return Contract(json.loads(SPEC_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        tokens=(TOKEN, OTHER_TOKEN),
        state_file=tmp_path / "state.json",
        quote_ttl_seconds=900,
        supplier_deadline_seconds=0.01,
        yield_in_critical_section=True,
    )


@pytest.fixture
def clock() -> Clock:
    c = Clock()
    c.set(START)
    return c


ClientFactory = Callable[..., httpx.AsyncClient]


@pytest.fixture
def app(settings: Settings, clock: Clock) -> FastAPI:
    return create_app(settings, clock)


@pytest.fixture
def make_client(app: FastAPI) -> ClientFactory:
    """Client for the shared app (or another instance, e.g. one simulating a restart)."""

    def factory(token: str | None = TOKEN, target: FastAPI | None = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target or app),
            base_url="http://mock",
            headers=headers,
        )

    return factory


@pytest.fixture
async def client(make_client: ClientFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c
