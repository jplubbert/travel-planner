import { useState } from "react";

import { Badge, ErrorBox, Notice } from "../components";
import { useAction, useTrip } from "../TripView";
import type { ChatReply, ChatTurn } from "../types";

type Exchange = { question: string; reply: ChatReply };

const STOP: Record<ChatReply["stopReason"], string> = {
  answered: "",
  iteration_limit: "Step limit reached: the answer may be incomplete.",
  time_limit: "Time limit reached: the answer may be incomplete.",
};

export function AssistantTab() {
  const { api, trip } = useTrip();
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [message, setMessage] = useState("");
  const action = useAction();

  async function send() {
    const question = message.trim();
    if (!question) return;
    const history: ChatTurn[] = exchanges.slice(-10).flatMap((x) => [
      { role: "user" as const, text: x.question },
      { role: "assistant" as const, text: x.reply.answer },
    ]);
    const reply = await action.run(() =>
      api.post<ChatReply>(`/trips/${trip.id}/assistant/chat`, { message: question, history }));
    if (reply) {
      setExchanges((xs) => [...xs, { question, reply }]);
      setMessage("");
    }
  }

  return (
    <div className="assistant">
      <Notice tone="info">
        The assistant is read-only: it reads the trip and searches for options, but it never books or changes anything.
      </Notice>
      {exchanges.map((x, i) => (
        <div key={i} className="exchange">
          <div className="bubble user">{x.question}</div>
          <div className="bubble assistant">
            <div className="answer">{x.reply.answer}</div>
            {STOP[x.reply.stopReason] && <Notice tone="warn">{STOP[x.reply.stopReason]}</Notice>}
            <details>
              <summary>
                Tools used: {x.reply.toolCalls.length} · {x.reply.model} · {x.reply.iterations} steps
              </summary>
              <ol className="tool-calls">
                {x.reply.toolCalls.map((c, j) => (
                  <li key={j}>
                    <code>{c.name}</code> {c.isError ? <Badge tone="bad">error</Badge> : <Badge tone="ok">ok</Badge>}
                    <pre>{JSON.stringify(c.arguments, null, 2)}</pre>
                    <details>
                      <summary>result</summary>
                      <pre>{typeof c.result === "string" ? c.result : JSON.stringify(c.result, null, 2)}</pre>
                    </details>
                  </li>
                ))}
              </ol>
            </details>
          </div>
        </div>
      ))}
      <ErrorBox error={action.error} onClose={action.clear} />
      <form className="row" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <textarea aria-label="message to the assistant" value={message} maxLength={2000} rows={2}
          placeholder="For example: which accommodation in Lisbon fits our budget?"
          onChange={(e) => setMessage(e.target.value)} />
        <button type="submit" disabled={action.busy || !message.trim()}>
          {action.busy ? "Thinking…" : "Send"}
        </button>
      </form>
    </div>
  );
}
