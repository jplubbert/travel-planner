from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.documents import Document, DocumentStatus
from app.domain.errors import InvalidTransition
from app.domain.jobs import Job, JobStatus
from app.domain.money import format_usd, parse_usd, round_usd

NOW = datetime(2030, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize("raw,expected", [
    ("0", "0.00"), ("1500", "1500.00"), ("1500.5", "1500.50"), ("1500.55", "1500.55"),
])
def test_parse_usd_normalizes_to_cents(raw, expected):
    assert format_usd(parse_usd(raw)) == expected


@pytest.mark.parametrize("raw", [
    "-1", "1.234", "abc", "", "01.00", "1e3", " 10", 10, 10.5, None, "99999999999.00",
])
def test_parse_usd_rejects_invalid_amounts(raw):
    with pytest.raises(ValueError):
        parse_usd(raw)


def test_round_usd_is_half_up():
    assert round_usd(Decimal("0.125")) == Decimal("0.13")
    assert round_usd(Decimal("0.124")) == Decimal("0.12")


def doc() -> Document:
    return Document(uuid4(), uuid4(), 1, "ana", DocumentStatus.WAITING, NOW, NOW,
                    snapshot={}, snapshot_at=NOW, snapshot_sha256="0" * 64)


def test_document_lifecycle_and_retry():
    d = doc()
    d.move(DocumentStatus.IN_PROGRESS, NOW)
    d.move(DocumentStatus.FAILED, NOW)
    d.move(DocumentStatus.WAITING, NOW)  # retry
    d.move(DocumentStatus.IN_PROGRESS, NOW)
    d.move(DocumentStatus.COMPLETE, NOW)
    # A complete document only goes back to waiting (file lost: regenerate the snapshot).
    for target in (DocumentStatus.IN_PROGRESS, DocumentStatus.FAILED, DocumentStatus.COMPLETE):
        with pytest.raises(InvalidTransition):
            d.move(target, NOW)
    d.move(DocumentStatus.WAITING, NOW)


def test_document_cannot_complete_without_being_generated():
    d = doc()
    with pytest.raises(InvalidTransition):
        d.move(DocumentStatus.COMPLETE, NOW)


def test_job_lifecycle():
    j = Job(uuid4(), "generate_document", JobStatus.QUEUED, NOW, NOW, NOW)
    j.move(JobStatus.RUNNING, NOW)
    j.move(JobStatus.QUEUED, NOW)  # lease expired / retry
    j.move(JobStatus.RUNNING, NOW)
    j.move(JobStatus.SUCCEEDED, NOW)
    with pytest.raises(InvalidTransition):
        j.move(JobStatus.QUEUED, NOW)
