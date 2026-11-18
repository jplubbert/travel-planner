import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { searchResult, withTrip } from "../test/fixtures";
import { SearchResults } from "./SearchTab";

describe("SearchResults", () => {
  it("complete: offers with estimate price, validity, availability and restrictions", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult()} />));
    expect(container.querySelector("[data-outcome=complete] .notice.ok")).not.toBeNull();
    expect(screen.getByText(/Búsqueda completa/)).toBeInTheDocument();
    expect(screen.getByText("precio estimado")).toBeInTheDocument();
    expect(screen.getByText(/válida hasta/)).toBeInTheDocument();
    expect(screen.getByText("Non-refundable")).toBeInTheDocument();
    expect(screen.getByText(/recibido hace \d+ s/)).toBeInTheDocument();
    expect(screen.getByText("Prices are estimates.")).toBeInTheDocument();
  });

  it("partial: warns, names the suppliers that failed, and still lists offers", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "partial",
      failedSuppliers: [{ providerId: "supplier-b", code: "SUPPLIER_UNAVAILABLE", message: "down" }] })} />));
    expect(container.querySelector(".notice.warn")).not.toBeNull();
    expect(screen.getByText(/Resultados parciales/)).toBeInTheDocument();
    expect(screen.getByText("supplier-b")).toBeInTheDocument();
    expect(container.querySelectorAll("article.offer")).toHaveLength(1);
  });

  it("empty: neutral, no options", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "empty", offers: [] })} />));
    expect(container.querySelector(".notice.neutral")).not.toBeNull();
    expect(screen.getByText(/Sin opciones/)).toBeInTheDocument();
  });

  it("failed: an error with its code and retry-after, never shown as 'no options'", () => {
    const { container } = render(withTrip(<SearchResults result={searchResult({ outcome: "failed", offers: [],
      error: { code: "PROVIDER_RATE_LIMITED", message: "Slow down", retryAfterSeconds: 20, providerRequestId: "p-1" } })} />));
    expect(container.querySelector(".notice.bad")).not.toBeNull();
    expect(screen.getByText(/La búsqueda falló/)).toBeInTheDocument();
    expect(screen.getByText("PROVIDER_RATE_LIMITED")).toBeInTheDocument();
    expect(container).toHaveTextContent("reintentar en 20 s");
    expect(screen.queryByText(/Sin opciones/)).toBeNull();
    expect(screen.queryByText("Prices are estimates.")).toBeNull();
  });

  it("flags an expired quote and hides 'add' from viewers", () => {
    const expired = searchResult();
    expired.offers[0] = { ...expired.offers[0], quoteExpired: true, validUntil: "2020-01-01T00:00:00Z" };
    render(withTrip(<SearchResults result={expired} />, "viewer"));
    expect(screen.getByText(/cotización caducada/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Añadir al itinerario" })).toBeNull();
  });
});
