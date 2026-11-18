// Server-Sent Events read with fetch: EventSource cannot send the X-User-Id header.
// Events are hints only; on every (re)connect the backend sends "ready" and the UI
// re-reads its state with GET, so a missed event cannot leave it stale.

export type SseEvent = { event: string; data: string; id: string | null };

/** Incremental parser: feed text chunks, get complete events (split anywhere). */
export class SseParser {
  private buffer = "";
  retryMs: number | null = null;

  feed(chunk: string): SseEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const events: SseEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let event = "message";
      let id: string | null = null;
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue; // comment / keep-alive
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
        else if (field === "id") id = value;
        else if (field === "retry" && /^\d+$/.test(value)) this.retryMs = Number(value);
      }
      if (data.length) events.push({ event, data: data.join("\n"), id });
    }
    return events;
  }
}

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

/** Keeps a stream open, reconnecting after errors, until the signal aborts or the
 *  server says access was revoked. */
export async function followEvents(
  url: string,
  headers: Record<string, string>,
  onEvent: (e: SseEvent) => void,
  onStatus: (s: StreamStatus) => void,
  signal: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  let delay = 3000;
  while (!signal.aborted) {
    onStatus("connecting");
    const parser = new SseParser();
    try {
      const response = await fetchImpl(url, { headers, signal });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      onStatus("live");
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const e of parser.feed(value)) {
          onEvent(e);
          if (e.event === "access-revoked") {
            onStatus("closed");
            return;
          }
        }
      }
    } catch {
      if (signal.aborted) break;
    }
    if (signal.aborted) break;
    onStatus("reconnecting");
    delay = parser.retryMs ?? delay;
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
  onStatus("closed");
}
