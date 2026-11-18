import { describe, expect, it } from "vitest";

import { age, BOOKING, errorMessage, ROLE_LABEL, SEARCH } from "./format";
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

describe("roles in Spanish", () => {
  it("labels every role in Spanish", () => {
    expect(ROLE_LABEL).toEqual({ organizer: "organizador", editor: "editor", viewer: "lector" });
  });
});

describe("known error codes in Spanish", () => {
  it("translates FORBIDDEN with the roles named by the backend", () => {
    expect(errorMessage("FORBIDDEN", "This action requires the editor role; you are a viewer of this trip."))
      .toBe("Esta acción requiere el rol editor; en este viaje eres lector.");
    expect(errorMessage("FORBIDDEN", "This action requires the organizer role; you are an editor of this trip."))
      .toBe("Esta acción requiere el rol organizador; en este viaje eres editor.");
  });

  it("falls back to a generic FORBIDDEN text when the message has another shape", () => {
    expect(errorMessage("FORBIDDEN", "Nope.")).toBe("Tu rol en este viaje no permite esta acción.");
  });

  it.each([
    ["TRIP_VERSION_CONFLICT", /^El viaje cambió desde la versión/],
    ["VERSION_REQUIRED", /^Falta la versión del viaje/],
    ["VALIDATION_ERROR", /^Algunos campos no son válidos/],
  ])("translates %s", (code, text) => {
    expect(errorMessage(code, "English text from the backend.")).toMatch(text);
  });

  it("keeps the backend message for unknown codes", () => {
    expect(errorMessage("TERMS_CHANGED", "The provider changed the terms.")).toBe("The provider changed the terms.");
  });
});
