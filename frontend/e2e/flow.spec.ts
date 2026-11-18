import { expect, test } from "@playwright/test";

import { apiAs, LISBON, mockControl, openAs, searchStays, seedTrip, tab } from "./helpers";

test.afterEach(async () => {
  await mockControl("delete", "scenario"); // failure scenarios are global in the mock
});

test("flujo completo: crear, compartir, buscar, planificar, reservar, documento y asistente", async ({ page }) => {
  await openAs(page, "ana");
  await page.getByRole("button", { name: "Nuevo viaje" }).click();
  const name = `Lisboa e2e ${Date.now()}`;
  await page.getByLabel("Nombre").fill(name);
  await page.getByLabel("Destinos (separados por comas)").fill("Lisbon");
  await page.getByLabel("Desde").fill("2030-06-10");
  await page.getByLabel("Hasta").fill("2030-06-14");
  await page.getByLabel("Viajeros").fill("2");
  await page.getByLabel("Presupuesto (USD)").fill("3000.00");
  await page.getByRole("button", { name: "Crear" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByText("estado: en vivo")).toBeVisible();

  // Participants (organizer only).
  await tab(page, "Participantes");
  await page.getByLabel("nuevo participante").selectOption("beto");
  await page.getByLabel("rol nuevo").selectOption("editor");
  await page.getByRole("button", { name: "Añadir", exact: true }).click();
  await expect(page.getByRole("cell", { name: /Beto/ })).toBeVisible();
  await page.getByLabel("nuevo participante").selectOption("carla");
  await page.getByLabel("rol nuevo").selectOption("viewer");
  await page.getByRole("button", { name: "Añadir", exact: true }).click();
  await expect(page.getByRole("cell", { name: /Carla/ })).toBeVisible();

  // Search: complete outcome, freshness, estimate prices, restrictions.
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Búsqueda completa: respondieron todos los proveedores")).toBeVisible();
  await expect(page.getByText(/recibido hace \d+ s/)).toBeVisible();
  const hotel = page.locator("article.offer", { hasText: "Four-night central hotel stay" });
  await expect(hotel).toContainText("670.00 USD");
  await expect(hotel).toContainText("precio estimado");
  await expect(hotel).toContainText("Non-refundable simulated booking");
  await expect(page.locator("article.offer", { hasText: "Three-night Belém boutique hotel" })).toContainText("no disponible");
  await hotel.getByRole("button", { name: "Añadir al itinerario" }).click();
  await expect(hotel.getByRole("button", { name: "Añadido al itinerario" })).toBeVisible();

  // Itinerary and costs: estimate, not booked.
  await tab(page, "Itinerario");
  const item = page.locator("article.item", { hasText: "Four-night central hotel stay" });
  await expect(item).toContainText("estimado");
  await expect(item).toContainText("sin reservar");
  await tab(page, "Costes");
  await expect(page.locator(".totals")).toContainText("Estimado (sin confirmar)670.00 USD");
  await expect(page.locator(".totals")).toContainText("Confirmado por el proveedor0.00 USD");

  // Booking: review the exact terms, confirm, see the provider's confirmation.
  await tab(page, "Reservas");
  await page.getByRole("button", { name: "Revisar términos" }).click();
  const review = page.getByRole("region", { name: "revisar términos" });
  await expect(review).toContainText("670.00 USD");
  await review.getByRole("button", { name: "Confirmar reserva por 670.00 USD" }).click();
  const booking = page.locator("article.booking", { hasText: "Four-night central hotel stay" });
  await expect(booking).toContainText("Reservado: confirmado por el proveedor");
  await expect(booking).toContainText("Precio aceptado670.00 USD");
  await tab(page, "Costes");
  await expect(page.locator(".totals")).toContainText("Confirmado por el proveedor670.00 USD");

  // Document: generated in the background, announced live, then downloaded.
  await tab(page, "Documentos");
  await page.getByRole("button", { name: /Generar documento/ }).click();
  await expect(page.getByRole("row", { name: /Listo/ })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar" }).click();
  const file = await download;
  const content = await (await file.createReadStream()).toArray();
  expect(Buffer.concat(content).toString()).toContain(name);

  // Assistant: in this stack it has no key, and the UI shows the backend's answer as is.
  await tab(page, "Asistente");
  await page.getByLabel("mensaje al asistente").fill("¿Qué alojamiento nos recomiendas?");
  await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByText("503 ASSISTANT_UNAVAILABLE")).toBeVisible();
});

test("permisos: el lector no ve acciones y, si las fuerza, el backend responde 403", async ({ page }) => {
  const tripId = await seedTrip(LISBON, "ana", { carla: "viewer" });
  await openAs(page, "carla", tripId);
  await expect(page.getByText("tu rol: lector")).toBeVisible();
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Búsqueda completa")).toBeVisible();
  await expect(page.getByRole("button", { name: "Añadir al itinerario" })).toHaveCount(0);

  await page.getByLabel(/Mostrar acciones aunque mi rol no las permita/).check();
  await page.locator("article.offer", { hasText: "Four-night central hotel stay" })
    .getByRole("button", { name: "Añadir al itinerario" }).click();
  await expect(page.getByText("403 FORBIDDEN")).toBeVisible();
  await expect(page.getByText("Esta acción requiere el rol editor; en este viaje eres lector.")).toBeVisible();

  // A non-participant gets the same 404 as a trip that does not exist.
  const dani = await page.context().newPage();
  await openAs(dani, "dani", tripId);
  await expect(dani.getByText("404 TRIP_NOT_FOUND")).toBeVisible();
});

test("409 de versión: muestra qué cambió y permite reaplicar; al reconectar relee", async ({ page }) => {
  const tripId = await seedTrip(LISBON, "ana", { beto: "editor" });
  // Live channel down: beto keeps working on version 1 without hearing about changes.
  await page.route("**/events", (route) => route.abort());
  await openAs(page, "beto", tripId);
  await expect(page.getByText("versión 1")).toBeVisible();
  await expect(page.getByText(/estado: (reconectando|conectando)/)).toBeVisible();

  const ana = await apiAs("ana");
  expect((await ana.patch(`trips/${tripId}`, { data: { version: 1, budget: "2500.00" } })).ok()).toBeTruthy();

  await searchStays(page, "Lisbon");
  await page.locator("article.offer", { hasText: "Four-night Alfama apartment" })
    .getByRole("button", { name: "Añadir al itinerario" }).click();
  const panel = page.getByText("409 TRIP_VERSION_CONFLICT").locator("..");
  await expect(panel).toContainText("versión 1; la actual es la 2");
  await expect(panel).toContainText(/v2 · ana .*Updated budget/);
  await expect(page.getByText("presupuesto 2500.00 USD")).toBeVisible(); // current state shown
  await panel.getByRole("button", { name: /Reaplicar «añadir «Four-night Alfama apartment»» sobre la versión 2/ }).click();
  await expect(page.getByText("409 TRIP_VERSION_CONFLICT")).toHaveCount(0);
  await expect(page.getByText("versión 3")).toBeVisible();
  await tab(page, "Itinerario");
  await expect(page.locator("article.item", { hasText: "Four-night Alfama apartment" })).toBeVisible();

  // Back online: the reconnect re-reads with GET and picks up a change made meanwhile.
  expect((await ana.patch(`trips/${tripId}`, { data: { version: 3, name: "Renombrado offline" } })).ok()).toBeTruthy();
  await page.unroute("**/events");
  await expect(page.getByText("estado: en vivo")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Renombrado offline" })).toBeVisible();
});

test("búsqueda parcial y fallida se muestran distinto de vacía", async ({ page }) => {
  const tripId = await seedTrip(LISBON);
  await openAs(page, "ana", tripId);

  await mockControl("post", "scenario", { supplierId: "supplier-b", mode: "unavailable" });
  await searchStays(page, "Lisbon");
  await expect(page.getByText("Resultados parciales: algunos proveedores no respondieron")).toBeVisible();
  await expect(page.getByText(/Sin respuesta de supplier-b/)).toBeVisible();
  await expect(page.locator("article.offer", { hasText: "Four-night central hotel stay" })).toBeVisible();

  await mockControl("post", "scenario", { supplierId: "supplier-a", mode: "unavailable" });
  await page.getByRole("button", { name: "Buscar" }).click();
  await expect(page.getByText("La búsqueda falló: no es lo mismo que no haber opciones")).toBeVisible();
  await expect(page.locator("article.offer")).toHaveCount(0);

  await mockControl("delete", "scenario");
  await searchStays(page, "Reykjavik");
  await expect(page.getByText("Sin opciones: todos respondieron y nada encaja")).toBeVisible();
});

test("reserva pendiente: no parece confirmada hasta que el proveedor confirma (en vivo)", async ({ page }) => {
  const tripId = await seedTrip({ name: "París", destinations: ["Paris"], startsOn: "2030-06-18",
    endsOn: "2030-06-22", budget: "4000.00" });
  await openAs(page, "ana", tripId);
  await searchStays(page, "Paris");
  await page.locator("article.offer", { hasText: "Four-night Montmartre apartment" })
    .getByRole("button", { name: "Añadir al itinerario" }).click();
  await tab(page, "Reservas");
  await page.getByRole("button", { name: "Revisar términos" }).click();
  await page.getByRole("button", { name: /Confirmar reserva por/ }).click();

  const booking = page.locator("article.booking", { hasText: "Four-night Montmartre apartment" });
  await expect(booking).toContainText("Aceptada, pendiente de confirmación (aún no reservada)");
  await expect(booking).not.toContainText("Reservado: confirmado");
  await tab(page, "Costes");
  await expect(page.locator(".totals")).toContainText("Confirmado por el proveedor0.00 USD");

  // Open the bookings tab *before* the provider settles it: from here on only a live event
  // can update the card (re-opening the tab would re-read with GET and hide a lost event).
  await tab(page, "Reservas");
  await expect(booking).toContainText("Aceptada, pendiente de confirmación (aún no reservada)");
  const ana = await apiAs("ana");
  const [b] = await (await ana.get(`trips/${tripId}/bookings`)).json();
  await mockControl("post", `booking/${b.providerBookingId}/settle`, { status: "confirmed" });
  // The worker reconciles it and the live channel announces it: no reload by the user.
  await expect(booking).toContainText("Reservado: confirmado por el proveedor", { timeout: 30_000 });
});
