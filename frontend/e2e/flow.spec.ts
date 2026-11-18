import { expect, test } from "@playwright/test";

import { apiAs, LISBON, mockControl, openAs, searchStays, seedTrip, tab } from "./helpers";

test.afterEach(async () => {
  await mockControl("delete", "scenario"); // failure scenarios are global in the mock
});

test("full flow: create, share, search, plan, book, document and assistant", async ({ page }) => {
  await openAs(page, "ana");
  await page.getByRole("button", { name: "New trip" }).click();
  const name = `Lisbon e2e ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Destinations (comma-separated)").fill("Lisbon");
  await page.getByLabel("Start date").fill("2030-06-10");
  await page.getByLabel("End date").fill("2030-06-14");
  await page.getByLabel("Travelers").fill("2");
  await page.getByLabel("Budget (USD)").fill("3000.00");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("status: live")).toBeVisible();

  // Participants (organizer only).
  await tab(page, "Participants");
  await page.getByLabel("new participant").selectOption("beto");
  await page.getByLabel("new role").selectOption("editor");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("cell", { name: /Beto/ })).toBeVisible();
  await page.getByLabel("new participant").selectOption("carla");
  await page.getByLabel("new role").selectOption("viewer");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("cell", { name: /Carla/ })).toBeVisible();

  // Search: complete outcome, freshness, estimate prices, restrictions.
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Search complete: every provider answered")).toBeVisible();
  await expect(page.getByText(/received \d+ s ago/)).toBeVisible();
  const hotel = page.locator("article.offer", { hasText: "Four-night central hotel stay" });
  await expect(hotel).toContainText("670.00 USD");
  await expect(hotel).toContainText("estimated price");
  await expect(hotel).toContainText("Non-refundable simulated booking");
  await expect(page.locator("article.offer", { hasText: "Three-night Belém boutique hotel" })).toContainText("unavailable");
  await hotel.getByRole("button", { name: "Add to itinerary" }).click();
  await expect(hotel.getByRole("button", { name: "Added to itinerary" })).toBeVisible();

  // Itinerary and costs: estimate, not booked.
  await tab(page, "Itinerary");
  const item = page.locator("article.item", { hasText: "Four-night central hotel stay" });
  await expect(item).toContainText("estimate");
  await expect(item).toContainText("not booked");
  await tab(page, "Costs");
  await expect(page.locator(".totals")).toContainText("Estimated (unconfirmed)670.00 USD");
  await expect(page.locator(".totals")).toContainText("Confirmed by the provider0.00 USD");

  // Booking: review the exact terms, confirm, see the provider's confirmation.
  await tab(page, "Bookings");
  await page.getByRole("button", { name: "Review terms" }).click();
  const review = page.getByRole("region", { name: "review terms" });
  await expect(review).toContainText("670.00 USD");
  await review.getByRole("button", { name: "Confirm booking for 670.00 USD" }).click();
  const booking = page.locator("article.booking", { hasText: "Four-night central hotel stay" });
  await expect(booking).toContainText("Booked: confirmed by the provider");
  await expect(booking).toContainText("Accepted price670.00 USD");
  await tab(page, "Costs");
  await expect(page.locator(".totals")).toContainText("Confirmed by the provider670.00 USD");

  // Document: generated in the background, announced live, then downloaded.
  await tab(page, "Documents");
  await page.getByRole("button", { name: /Generate document/ }).click();
  await expect(page.getByRole("row", { name: /Ready/ })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download" }).click();
  const file = await download;
  const content = await (await file.createReadStream()).toArray();
  expect(Buffer.concat(content).toString()).toContain(name);

  // Assistant: in this stack it has no key, and the UI shows the backend's answer as is.
  await tab(page, "Assistant");
  await page.getByLabel("message to the assistant").fill("Which accommodation do you recommend?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("503 ASSISTANT_UNAVAILABLE")).toBeVisible();
});

test("permissions: a viewer sees no actions and, if forced, the backend answers 403", async ({ page }) => {
  const tripId = await seedTrip(LISBON, "ana", { carla: "viewer" });
  await openAs(page, "carla", tripId);
  await expect(page.getByText("your role: viewer")).toBeVisible();
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Search complete")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add to itinerary" })).toHaveCount(0);

  await page.getByLabel(/Show actions my role does not allow/).check();
  await page.locator("article.offer", { hasText: "Four-night central hotel stay" })
    .getByRole("button", { name: "Add to itinerary" }).click();
  await expect(page.getByText("403 FORBIDDEN")).toBeVisible();
  await expect(page.getByText("This action requires the editor role; you are a viewer of this trip.")).toBeVisible();

  // A non-participant gets the same 404 as a trip that does not exist.
  const dani = await page.context().newPage();
  await openAs(dani, "dani", tripId);
  await expect(dani.getByText("404 TRIP_NOT_FOUND")).toBeVisible();
});

test("version 409: shows what changed and allows reapplying; re-reads on reconnect", async ({ page }) => {
  const tripId = await seedTrip(LISBON, "ana", { beto: "editor" });
  // Live channel down: beto keeps working on version 1 without hearing about changes.
  await page.route("**/events", (route) => route.abort());
  await openAs(page, "beto", tripId);
  await expect(page.getByText("version 1", { exact: true })).toBeVisible();
  await expect(page.getByText(/status: (reconnecting|connecting)/)).toBeVisible();

  const ana = await apiAs("ana");
  expect((await ana.patch(`trips/${tripId}`, { data: { version: 1, budget: "2500.00" } })).ok()).toBeTruthy();

  await searchStays(page, "Lisbon");
  await page.locator("article.offer", { hasText: "Four-night Alfama apartment" })
    .getByRole("button", { name: "Add to itinerary" }).click();
  const panel = page.getByText("409 TRIP_VERSION_CONFLICT").locator("..");
  await expect(panel).toContainText("version 1; the current one is 2");
  await expect(panel).toContainText(/v2 · ana .*Updated budget/);
  await expect(page.getByText("budget 2500.00 USD")).toBeVisible(); // current state shown
  await panel.getByRole("button", { name: /Reapply “add 'Four-night Alfama apartment'” on version 2/ }).click();
  await expect(page.getByText("409 TRIP_VERSION_CONFLICT")).toHaveCount(0);
  await expect(page.getByText("version 3", { exact: true })).toBeVisible();
  await tab(page, "Itinerary");
  await expect(page.locator("article.item", { hasText: "Four-night Alfama apartment" })).toBeVisible();

  // Back online: the reconnect re-reads with GET and picks up a change made meanwhile.
  expect((await ana.patch(`trips/${tripId}`, { data: { version: 3, name: "Renamed offline" } })).ok()).toBeTruthy();
  await page.unroute("**/events");
  await expect(page.getByText("status: live")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Renamed offline" })).toBeVisible();
});

test("partial and failed searches look different from empty", async ({ page }) => {
  const tripId = await seedTrip(LISBON);
  await openAs(page, "ana", tripId);

  await mockControl("post", "scenario", { supplierId: "supplier-b", mode: "unavailable" });
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Partial results: some providers did not answer")).toBeVisible();
  await expect(page.getByText(/No answer from supplier-b/)).toBeVisible();
  await expect(page.locator("article.offer", { hasText: "Four-night central hotel stay" })).toBeVisible();

  await mockControl("post", "scenario", { supplierId: "supplier-a", mode: "unavailable" });
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText("Search failed: this is not the same as no options")).toBeVisible();
  await expect(page.locator("article.offer")).toHaveCount(0);

  await mockControl("delete", "scenario");
  await searchStays(page, "Reykjavik");
  await expect(page.getByText("No options: every provider answered and nothing matches")).toBeVisible();
});

test("pending booking: does not look confirmed until the provider confirms (live)", async ({ page }) => {
  const tripId = await seedTrip({ name: "Paris", destinations: ["Paris"], startsOn: "2030-06-18",
    endsOn: "2030-06-22", budget: "4000.00" });
  await openAs(page, "ana", tripId);
  await searchStays(page, "Paris");
  await page.locator("article.offer", { hasText: "Four-night Montmartre apartment" })
    .getByRole("button", { name: "Add to itinerary" }).click();
  await tab(page, "Bookings");
  await page.getByRole("button", { name: "Review terms" }).click();
  await page.getByRole("button", { name: /Confirm booking for/ }).click();

  const booking = page.locator("article.booking", { hasText: "Four-night Montmartre apartment" });
  await expect(booking).toContainText("Accepted, awaiting confirmation (not booked yet)");
  await expect(booking).not.toContainText("Booked: confirmed");
  await tab(page, "Costs");
  await expect(page.locator(".totals")).toContainText("Confirmed by the provider0.00 USD");

  // Open the bookings tab *before* the provider settles it: from here on only a live event
  // can update the card (re-opening the tab would re-read with GET and hide a lost event).
  await tab(page, "Bookings");
  await expect(booking).toContainText("Accepted, awaiting confirmation (not booked yet)");
  const ana = await apiAs("ana");
  const [b] = await (await ana.get(`trips/${tripId}/bookings`)).json();
  await mockControl("post", `booking/${b.providerBookingId}/settle`, { status: "confirmed" });
  // The worker reconciles it and the live channel announces it: no reload by the user.
  await expect(booking).toContainText("Booked: confirmed by the provider", { timeout: 30_000 });
});
