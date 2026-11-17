from datetime import UTC, datetime, timedelta


class Clock:
    """Controllable clock. Defaults to system time; can be frozen or shifted."""

    def __init__(self) -> None:
        self._fixed: datetime | None = None
        self._offset = timedelta(0)

    def now(self) -> datetime:
        if self._fixed is not None:
            return self._fixed
        return datetime.now(UTC) + self._offset

    @property
    def frozen(self) -> bool:
        return self._fixed is not None

    def set(self, now: datetime, *, frozen: bool = True) -> None:
        if now.tzinfo is None:
            raise ValueError("clock time must include a timezone offset")
        if frozen:
            self._fixed = now.astimezone(UTC)
            self._offset = timedelta(0)
        else:
            self._fixed = None
            self._offset = now - datetime.now(UTC)

    def advance(self, seconds: float) -> None:
        delta = timedelta(seconds=seconds)
        if self._fixed is not None:
            self._fixed += delta
        else:
            self._offset += delta

    def reset(self) -> None:
        self._fixed = None
        self._offset = timedelta(0)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
