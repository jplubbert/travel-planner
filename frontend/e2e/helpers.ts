import { type APIRequestContext, expect, type Page, request } from "@playwright/test";

export const BASE = process.env.E2E_BASE_URL ?? "http://localhost:5173";
export const MOCK = process.env.E2E_MOCK_URL ?? "http://localhost:4010";

export type TripSeed = { name: string; destinations: string[]; startsOn: string; endsOn: string; budget: string };

export const LISBON: TripSeed = {
  name: "Lisbon", destinations: ["Lisbon"], startsOn: "2030-06-10", endsOn: "2030-06-14", budget: "3000.00",
};

/** API client acting as a test user, through the same nginx proxy the UI uses. */
export async function apiAs(user: string): Promise<APIRequestContext> {
  return request.newContext({ baseURL: `${BASE}/api/`, extraHTTPHeaders: { "X-User-Id": user } });
}

/** Creates a trip as ``owner`` and shares it; returns its id. */
export async function seedTrip(seed: TripSeed, owner = "ana",
  members: Record<string, "editor" | "viewer"> = {}): Promise<string> {
  const api = await apiAs(owner);
  const trip = await (await api.post("trips", { data: { ...seed, name: `${seed.name} ${Date.now()}`, travelers: 2 } })).json();
  for (const [user, role] of Object.entries(members)) {
    expect((await api.put(`trips/${trip.id}/members/${user}`, { data: { role } })).ok()).toBeTruthy();
  }
  return trip.id as string;
}

export async function mockControl(method: "post" | "delete", path: string, data?: unknown) {
  const mock = await request.newContext({ baseURL: `${MOCK}/_control/` });
  const response = await mock[method](path, data === undefined ? {} : { data });
  expect(response.ok(), await response.text()).toBeTruthy();
}

export async function openAs(page: Page, user: string, tripId?: string) {
  await page.addInitScript((u) => localStorage.setItem("tp-user", u), user);
  await page.goto(tripId ? `/#/trips/${tripId}` : "/");
  await expect(page.getByLabel("test user")).toHaveValue(user);
}

export async function tab(page: Page, name: string) {
  await page.getByRole("tab", { name }).click();
}

export async function searchStays(page: Page, destination: string) {
  await tab(page, "Search");
  await page.getByLabel("Category").selectOption("accommodation");
  await page.getByLabel("Destination").fill(destination);
  await page.getByRole("button", { name: "Search" }).click();
}
