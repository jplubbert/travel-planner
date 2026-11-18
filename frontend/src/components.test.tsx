import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { ConflictPanel, ErrorBox, type PendingConflict } from "./components";
import type { VersionConflictDetails } from "./types";
import { TRIP } from "./test/fixtures";

const details: VersionConflictDetails = {
  yourVersion: 1, currentVersion: 3,
  changesSinceYourVersion: [
    { version: 2, by: "ana", at: "2030-06-01T10:00:00Z", kind: "trip_updated", summary: "Updated budget" },
    { version: 3, by: "beto", at: "2030-06-01T10:05:00Z", kind: "item_added", summary: "Added 'Tram tour'" },
  ],
  current: { ...TRIP, version: 3 },
  currentItinerary: { tripId: "t1", tripVersion: 3, items: [], missing: [], rules: [], generatedAt: "2030-06-01T10:05:00Z" },
};

describe("ConflictPanel", () => {
  it("lists every change since the user's version and offers to reapply on the current one", async () => {
    const conflict: PendingConflict = { label: "quitar «Hotel»", details, reapply: vi.fn() };
    const onReapply = vi.fn();
    const onDiscard = vi.fn();
    render(<ConflictPanel conflict={conflict} onReapply={onReapply} onDiscard={onDiscard} />);
    expect(screen.getByText(/409 TRIP_VERSION_CONFLICT/)).toBeInTheDocument();
    expect(screen.getByText(/versión 1; la actual es la 3/)).toBeInTheDocument();
    expect(screen.getByText(/ana .*Updated budget/)).toBeInTheDocument();
    expect(screen.getByText(/beto .*Added 'Tram tour'/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reaplicar «quitar «Hotel»» sobre la versión 3" }));
    expect(onReapply).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Descartar mi cambio" }));
    expect(onDiscard).toHaveBeenCalledOnce();
  });
});

describe("ErrorBox", () => {
  it("shows status, code, field errors and requestId as returned, with the message in Spanish", () => {
    render(<ErrorBox error={new ApiError(422, "VALIDATION_ERROR", "Some fields are invalid; see details.",
      { fields: [{ field: "budget", message: "must be positive" }] }, "req-42")} />);
    expect(screen.getByText("422 VALIDATION_ERROR")).toBeInTheDocument();
    expect(screen.getByText("Algunos campos no son válidos; revisa el detalle.")).toBeInTheDocument();
    expect(screen.getByText("Some fields are invalid; see details.").closest("details")).not.toBeNull();
    expect(screen.getByText("budget")).toBeInTheDocument();
    expect(screen.getByText(/must be positive/)).toBeInTheDocument();
    expect(screen.getByText("requestId: req-42")).toBeInTheDocument();
  });

  it("shows a 403 with its status and code, the roles in Spanish and the original message kept", () => {
    render(<ErrorBox error={new ApiError(403, "FORBIDDEN", "This action requires the editor role; you are a viewer of this trip.")} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("403 FORBIDDEN");
    expect(screen.getByText("Esta acción requiere el rol editor; en este viaje eres lector.")).toBeInTheDocument();
    expect(screen.getByText("Mensaje original del backend")).toBeInTheDocument();
    expect(alert).toHaveTextContent("requires the editor role");
  });

  it.each([
    [409, "TRIP_VERSION_CONFLICT", /^El viaje cambió desde la versión/],
    [400, "VERSION_REQUIRED", /^Falta la versión del viaje/],
  ])("translates %i %s", (status, code, text) => {
    render(<ErrorBox error={new ApiError(status, code, "English message.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent(`${status} ${code}`);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it("shows unknown codes with the backend message and no duplicate original", () => {
    render(<ErrorBox error={new ApiError(404, "TRIP_NOT_FOUND", "Trip not found.")} />);
    expect(screen.getByText("Trip not found.")).toBeInTheDocument();
    expect(screen.queryByText("Mensaje original del backend")).toBeNull();
  });

  it("renders nothing without an error", () => {
    const { container } = render(<ErrorBox error={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
