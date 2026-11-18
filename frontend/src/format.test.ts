import { describe, expect, it } from "vitest";

import { age, BOOKING, SEARCH } from "./format";
import type { BookingStatus } from "./types";

describe("booking presentation", () => {
  it("presents only 'confirmed' as booked", () => {
    for (const [status, { label, tone }] of Object.entries(BOOKING) as [BookingStatus, { label: string; tone: string }][]) {
      if (status === "confirmed") {
        expect(tone).toBe("ok");
        expect(label).toMatch(/^Reservado/);
      } else {
        expect(tone, status).not.toBe("ok");
        expect(label, status).not.toMatch(/^Reservado/);
        expect(label, status).toMatch(/no reservada|puede o no estar reservada/i);
      }
    }
  });

  it("warns not to book again when the outcome is unknown", () => {
    expect(BOOKING.unknown.label).toContain("no reservar otra vez");
    expect(BOOKING.unknown.tone).toBe("warn");
  });
});

describe("search presentation", () => {
  it("gives each outcome its own label and tone, and failed is not 'no options'", () => {
    const labels = new Set(Object.values(SEARCH).map((s) => s.label));
    expect(labels.size).toBe(4);
    expect(SEARCH.failed.tone).toBe("bad");
    expect(SEARCH.partial.tone).toBe("warn");
    expect(SEARCH.empty.tone).toBe("neutral");
    expect(SEARCH.failed.label).toContain("no es lo mismo que no haber opciones");
  });

  it("describes freshness relative to now", () => {
    const now = Date.parse("2030-06-01T12:00:00Z");
    expect(age("2030-06-01T11:59:48Z", now)).toBe("hace 12 s");
    expect(age("2030-06-01T11:55:00Z", now)).toBe("hace 5 min");
    expect(age(null, now)).toBe("—");
  });
});
