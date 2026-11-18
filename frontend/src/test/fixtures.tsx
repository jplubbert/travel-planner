import type { ReactNode } from "react";

import { createApi } from "../api";
import { TripContext, type TripCtx } from "../TripView";
import type { Booking, Role, SearchResult, Trip } from "../types";

export const TRIP: Trip = {
  id: "t1", name: "Lisboa", destinations: ["Lisbon"], startsOn: "2030-06-10", endsOn: "2030-06-14",
  travelers: 2, budget: { amount: "3000.00", currency: "USD" }, version: 3, createdBy: "ana",
  createdAt: "2030-01-01T00:00:00Z", updatedAt: "2030-01-01T00:00:00Z", myRole: "editor",
};

export function withTrip(ui: ReactNode, role: Role = "editor", overrides: Partial<TripCtx> = {}) {
  const rank = { viewer: 0, editor: 1, organizer: 2 };
  const ctx: TripCtx = {
    api: createApi("ana", (() => Promise.reject(new Error("no network in unit tests"))) as typeof fetch),
    trip: { ...TRIP, myRole: role },
    allows: (min) => rank[role] >= rank[min],
    ticks: { trip: 0, booking: 0, document: 0 },
    versioned: async () => undefined,
    reloadTrip: async () => undefined,
    ...overrides,
  };
  return <TripContext.Provider value={ctx}>{ui}</TripContext.Provider>;
}

export function booking(overrides: Partial<Booking> = {}): Booking {
  return {
    bookingId: "b1", itemId: "i1", itemTitle: "Central hotel", offerId: "offer-1", status: "confirmed",
    booked: true, nextStep: "Booked.", canRetry: false, expectedPrice: { amount: "670.00", currency: "USD" },
    expectedRevision: "r1", acceptedPrice: null, acceptedOffer: null, providerBookingId: null,
    clientReference: "ref-1", failure: null, lastError: null, currentTerms: null, requestedBy: "ana",
    createdAt: "2030-01-01T00:00:00Z", submittedAt: null, updatedAt: "2030-01-01T00:00:00Z",
    ...overrides,
  };
}

export function searchResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    outcome: "complete", message: "All queried suppliers answered.", disclaimer: "Prices are estimates.",
    query: { category: "accommodation", destination: "Lisbon", origin: null, startsOn: "2030-06-10",
      endsOn: "2030-06-14", travelers: 2, providerId: null },
    searchedAt: new Date().toISOString(), receivedAt: new Date().toISOString(),
    offers: [{
      offerId: "offer-lis-hotel-central-p2", providerId: "supplier-a", category: "accommodation",
      title: "Central hotel", description: "Four nights", destination: "Lisbon", origin: null,
      startsAt: "2030-06-10T15:00:00+01:00", endsAt: "2030-06-14T11:00:00+01:00", travelers: 2,
      price: { amount: "670.00", currency: "USD" }, priceType: "estimate", revision: "r1", available: true,
      validUntil: new Date(Date.now() + 600_000).toISOString(), quoteExpired: false,
      restrictions: ["Non-refundable"],
    }],
    failedSuppliers: [], error: null, requestId: "req-1",
    ...overrides,
  };
}
