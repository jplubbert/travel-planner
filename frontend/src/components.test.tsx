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
  it("shows the backend's status, code, message, field errors and requestId as returned", () => {
    render(<ErrorBox error={new ApiError(422, "VALIDATION_ERROR", "Some fields are invalid; see details.",
      { fields: [{ field: "budget", message: "must be positive" }] }, "req-42")} />);
    expect(screen.getByText("422 VALIDATION_ERROR")).toBeInTheDocument();
    expect(screen.getByText("Some fields are invalid; see details.")).toBeInTheDocument();
    expect(screen.getByText("budget")).toBeInTheDocument();
    expect(screen.getByText(/must be positive/)).toBeInTheDocument();
    expect(screen.getByText("requestId: req-42")).toBeInTheDocument();
  });

  it("shows a 403 from the backend without rewording it", () => {
    render(<ErrorBox error={new ApiError(403, "FORBIDDEN", "This action requires the editor role; you are a viewer of this trip.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("403 FORBIDDEN");
    expect(screen.getByRole("alert")).toHaveTextContent("requires the editor role");
  });

  it("renders nothing without an error", () => {
    const { container } = render(<ErrorBox error={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
