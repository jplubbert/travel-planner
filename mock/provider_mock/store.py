"""Durable provider data: bookings, client references, idempotency records and quotes.

The whole dataset is rewritten atomically (temp file + fsync + rename) on every change,
before the response is sent, so accepted bookings survive lost responses and restarts.
"""

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.clear()
        if path.exists():
            self._load()

    def clear(self) -> None:
        # bookingId -> {"caller": str, "booking": Booking}
        self.bookings: dict[str, dict[str, Any]] = {}
        # caller -> Idempotency-Key -> {"fingerprint": str, "bookingId": str}
        self.idempotency: dict[str, dict[str, dict[str, str]]] = {}
        # caller -> clientReference -> bookingId
        self.references: dict[str, dict[str, str]] = {}
        # offerId -> {"seq": int, "price": str, "available": bool, "validUntil": str}
        self.quotes: dict[str, dict[str, Any]] = {}
        self.next_booking_seq = 1

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schemaVersion") != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported mock state schema in {self.path}")
        self.bookings = data["bookings"]
        self.idempotency = data["idempotency"]
        self.references = data["references"]
        self.quotes = data["quotes"]
        self.next_booking_seq = data["nextBookingSeq"]

    def save(self) -> None:
        data = {
            "schemaVersion": SCHEMA_VERSION,
            "bookings": self.bookings,
            "idempotency": self.idempotency,
            "references": self.references,
            "quotes": self.quotes,
            "nextBookingSeq": self.next_booking_seq,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
