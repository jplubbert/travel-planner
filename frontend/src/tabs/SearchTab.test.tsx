import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { searchResult, withTrip } from "../test/fixtures";
import { SearchResults } from "./SearchTab";

describe("SearchResults", () => {
  it("complete: offers with estimate price, validity, availability and restrictions", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult()} />));
    expect(container.querySelector("[data-outcome=complete] .notice.ok")).not.toBeNull();
    expect(screen.getByText(/Search complete/)).toBeInTheDocument();
    expect(screen.getByText("estimated price")).toBeInTheDocument();
    expect(screen.getByText(/valid until/)).toBeInTheDocument();
    expect(screen.getByText("Non-refundable")).toBeInTheDocument();
    expect(screen.getByText(/received \d+ s ago/)).toBeInTheDocument();
    expect(screen.getByText("Prices are estimates.")).toBeInTheDocument();
  });

  it("partial: warns, names the suppliers that failed, and still lists offers", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "partial",
      failedSuppliers: [{ providerId: "supplier-b", code: "SUPPLIER_UNAVAILABLE", message: "down" }] })} />));
    expect(container.querySelector(".notice.warn")).not.toBeNull();
    expect(screen.getByText(/Partial results/)).toBeInTheDocument();
    expect(screen.getByText("supplier-b")).toBeInTheDocument();
    expect(container.querySelectorAll("article.offer")).toHaveLength(1);
  });

  it("empty: neutral, no options", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "empty", offers: [] })} />));
    expect(container.querySelector(".notice.neutral")).not.toBeNull();
    expect(screen.getByText(/No options/)).toBeInTheDocument();
  });

  it("failed: an error with its code and retry-after, never shown as 'no options'", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "failed", offers: [],
      error: { code: "PROVIDER_RATE_LIMITED", message: "Slow down", retryAfterSeconds: 20, providerRequestId: "p-1" } })} />));
    expect(container.querySelector(".notice.bad")).not.toBeNull();
    expect(screen.getByText(/Search failed/)).toBeInTheDocument();
    expect(screen.getByText("PROVIDER_RATE_LIMITED")).toBeInTheDocument();
    expect(container).toHaveTextContent("retry in 20 s");
    expect(screen.queryByText(/No options/)).toBeNull();
    expect(screen.queryByText("Prices are estimates.")).toBeNull();
  });

  it("flags an expired quote and hides 'add' from viewers", () => {
    const expired = searchResult();
    expired.offers[0] = { ...expired.offers[0], quoteExpired: true, validUntil: "2020-01-01T00:00:00Z" };
    render(withTrip(<SearchResults result={expired} />, "viewer"));
    expect(screen.getByText(/quote expired/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to itinerary" })).toBeNull();
  });
});
