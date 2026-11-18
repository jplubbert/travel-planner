import { describe, expect, it, vi } from "vitest";

import { followEvents, SseParser, type SseEvent, type StreamStatus } from "./sse";

describe("SseParser", () => {
  it("parses events split at any point, with ids, retry, comments and CRLF", () => {
    const parser = new SseParser();
    const text = "retry: 3000\r\n\r\nevent: ready\ndata: {\"tripId\":\"t1\"}\n\n: keep-alive\n\nevent: booking\nid: 7\ndata: {\"id\":\"b1\",\n";
    const events: SseEvent[] = [];
    for (const ch of text) events.push(...parser.feed(ch));
    expect(events).toEqual([{ event: "ready", data: "{\"tripId\":\"t1\"}", id: null }]);
    events.push(...parser.feed("data: \"status\":\"confirmed\"}\n\n"));
    expect(events[1]).toEqual({ event: "booking", data: "{\"id\":\"b1\",\n\"status\":\"confirmed\"}", id: "7" });
    expect(parser.retryMs).toBe(3000);
  });

  it("keeps an incomplete event until its blank line arrives", () => {
    const parser = new SseParser();
    expect(parser.feed("event: trip\ndata: {}\n")).toEqual([]);
    expect(parser.feed("\n")).toEqual([{ event: "trip", data: "{}", id: null }]);
  });
});

function stream(chunks: string[], fail = false): Response {
  // Delivers each chunk on demand, then ends (or drops the connection) once all were read.
  const queue = [...chunks];
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const next = queue.shift();
      if (next !== undefined) controller.enqueue(new TextEncoder().encode(next));
      else if (fail) controller.error(new Error("connection reset"));
      else controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("followEvents", () => {
  it("sends the identity header, reconnects after a drop and announces ready again", async () => {
    const abort = new AbortController();
    const seen: string[] = [];
    const statuses: StreamStatus[] = [];
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(stream(["retry: 5\n\nevent: ready\ndata: {}\n\n"], true))
      .mockResolvedValueOnce(stream(["event: ready\ndata: {}\n\nevent: trip\ndata: {\"version\":2}\n\n"]))
      .mockImplementation(() => { abort.abort(); return Promise.reject(new DOMException("aborted", "AbortError")); });
    await followEvents("/api/trips/t1/events", { "X-User-Id": "ana" }, (e) => seen.push(e.event),
      (s) => statuses.push(s), abort.signal, fetchImpl as unknown as typeof fetch);
    expect(fetchImpl.mock.calls[0][1].headers).toEqual({ "X-User-Id": "ana" });
    expect(seen).toEqual(["ready", "ready", "trip"]);
    expect(statuses).toContain("reconnecting");
    expect(statuses.at(-1)).toBe("closed");
  });

  it("stops for good when access is revoked", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(stream(["event: access-revoked\ndata: {}\n\n"]));
    const statuses: StreamStatus[] = [];
    await followEvents("/x", {}, () => undefined, (s) => statuses.push(s), new AbortController().signal,
      fetchImpl as unknown as typeof fetch);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(statuses.at(-1)).toBe("closed");
  });

  it("retries when the server refuses the stream", async () => {
    const abort = new AbortController();
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response("{}", { status: 503 }))
      .mockImplementation(() => { abort.abort(); return Promise.reject(new DOMException("aborted", "AbortError")); });
    vi.useFakeTimers();
    const done = followEvents("/x", {}, () => undefined, () => undefined, abort.signal,
      fetchImpl as unknown as typeof fetch);
    await vi.advanceTimersByTimeAsync(3000);
    await done;
    vi.useRealTimers();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
