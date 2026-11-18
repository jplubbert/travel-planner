import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { booking, withTrip } from "../test/fixtures";
import type { BookingStatus } from "../types";
import { BookingCard } from "./BookingsTab";

const NOT_BOOKED: BookingStatus[] = ["draft", "submitted", "unknown", "pending", "failed", "rejected"];

describe("BookingCard", () => {
  it.each(NOT_BOOKED)("a %s booking never looks confirmed", (status) => {
    const { container } = render(withTrip(
      <BookingCard booking={booking({ status, booked: false, nextStep: "Do something" })} onChanged={() => undefined} />));
    expect(container).not.toHaveTextContent(/Booked: confirmed/);
    expect(container.querySelector("article")).not.toHaveClass("booked");
    expect(container.querySelector(".badge.ok")).toBeNull();
  });

  it("an unknown outcome says it may or may not be booked and not to book again", () => {
    render(withTrip(<BookingCard booking={booking({ status: "unknown", booked: false,
      nextStep: "Outcome not known yet: do not book this option again.",
      lastError: { code: "PROVIDER_TIMEOUT", message: "No answer in 5 s", providerRequestId: null, retryAfterSeconds: null, at: null } })}
      onChanged={() => undefined} />));
    expect(screen.getByText(/Outcome unknown: it may or may not be booked; do not book again/)).toBeInTheDocument();
    expect(screen.getByText(/do not book this option again/)).toBeInTheDocument();
    expect(screen.getByText("PROVIDER_TIMEOUT")).toBeInTheDocument();
  });

  it("a confirmed booking shows the accepted price and provider reference", () => {
    const { container } = render(withTrip(<BookingCard booking={booking({
      acceptedPrice: { amount: "670.00", currency: "USD" }, providerBookingId: "booking-000001" })}
      onChanged={() => undefined} />));
    expect(screen.getByText("Booked: confirmed by the provider")).toBeInTheDocument();
    expect(container).toHaveTextContent("Accepted price670.00 USD");
    expect(screen.getByText("booking-000001")).toBeInTheDocument();
  });

  it("shows lastError, retry-after and nextStep, and offers retry only to editors of retryable drafts", () => {
    const draft = booking({ status: "draft", booked: false, canRetry: true, nextStep: "Not booked: you can retry.",
      lastError: { code: "PROVIDER_RATE_LIMITED", message: "Too many requests", providerRequestId: "req-p",
        retryAfterSeconds: 30, at: null } });
    const { unmount } = render(withTrip(<BookingCard booking={draft} onChanged={() => undefined} />, "editor"));
    expect(screen.getByText("PROVIDER_RATE_LIMITED")).toBeInTheDocument();
    expect(screen.getByText(/retry after 30 s/)).toBeInTheDocument();
    expect(screen.getByText("Not booked: you can retry.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
    unmount();
    render(withTrip(<BookingCard booking={draft} onChanged={() => undefined} />, "viewer"));
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });
});
