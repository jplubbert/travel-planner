import { describe, expect, it, vi } from "vitest";

import { ApiError, createApi } from "./api";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(status === 204 ? null : JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json", ...headers } });

describe("createApi", () => {
  it("sends X-User-Id and JSON, under /api", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json(201, { id: "t1" }));
    const api = createApi("beto", fetchImpl as unknown as typeof fetch);
    expect(await api.post("/trips", { name: "x" })).toEqual({ id: "t1" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/api/trips");
    expect(init.headers).toEqual({ "X-User-Id": "beto", "Content-Type": "application/json" });
    expect(init.body).toBe("{\"name\":\"x\"}");
  });

  it("turns error bodies into ApiError with code, details and requestId, unchanged", async () => {
    const details = { yourVersion: 1, currentVersion: 2 };
    const fetchImpl = vi.fn().mockResolvedValue(json(409, {
      code: "TRIP_VERSION_CONFLICT", message: "changed", details, requestId: "req-9" }));
    const api = createApi("ana", fetchImpl as unknown as typeof fetch);
    const error = await api.put("/trips/t1/itinerary/order", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "TRIP_VERSION_CONFLICT", message: "changed", details, requestId: "req-9" });
  });

  it("keeps the status of a non-JSON error and handles 204", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response("Bad gateway", { status: 502, statusText: "Bad Gateway" }))
      .mockResolvedValueOnce(json(204, null));
    const api = createApi("ana", fetchImpl as unknown as typeof fetch);
    await expect(api.get("/trips")).rejects.toMatchObject({ status: 502, code: "HTTP_502" });
    await expect(api.del("/trips/t1/members/beto")).resolves.toBeUndefined();
  });

  it("returns a failed search (503/504) as a result, not as a generic error", async () => {
    const failed = { outcome: "failed", offers: [], error: { code: "PROVIDER_UNAVAILABLE" } };
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(json(503, failed))
      .mockResolvedValueOnce(json(422, { code: "VALIDATION_ERROR", message: "bad", details: { fields: [] } }));
    const api = createApi("ana", fetchImpl as unknown as typeof fetch);
    expect(await api.search("/trips/t1/offers/search", {})).toEqual(failed);
    await expect(api.search("/trips/t1/offers/search", {})).rejects.toMatchObject({ status: 422, code: "VALIDATION_ERROR" });
  });
});
