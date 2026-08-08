// API client for the Janus 3a service.
//
// /ask is a POST-based SSE stream, so EventSource (GET-only) can't be used; we
// read the fetch response body as a stream and parse SSE frames by hand. Events:
//   sources  -> retrieved chunks (fires BEFORE any token)
//   token    -> one answer fragment
//   done     -> latency breakdown (grounded answers)
//   refusal  -> the docs don't cover it (no tokens)

export const API_BASE =
  import.meta.env.VITE_API_BASE || "http://localhost:8000";

export async function getCorpora() {
  const r = await fetch(`${API_BASE}/corpora`);
  if (!r.ok) throw new Error(`/corpora HTTP ${r.status}`);
  return r.json();
}

export async function getHealth() {
  const r = await fetch(`${API_BASE}/health`);
  return { status: r.status, body: await r.json() };
}

function parseFrame(raw) {
  let event = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  let data = null;
  if (dataLines.length) {
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      data = dataLines.join("\n");
    }
  }
  return { event, data };
}

// streamAsk drives one /ask request. `handlers` may define onSources, onToken,
// onDone, onRefusal, onRateLimit, onError. Returns an abort function.
export function streamAsk({ question, corpus, doc_version, context }, handlers) {
  const controller = new AbortController();

  (async () => {
    let resp;
    try {
      resp = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          corpus,
          doc_version: doc_version ?? null,
          // Frame from the last answered turn, echoed back verbatim so a
          // referential follow-up ("what about more champs he's good
          // against") resolves against it. Opaque to us; the server mints it.
          context: context ?? null,
        }),
        signal: controller.signal,
      });
    } catch (e) {
      if (!controller.signal.aborted) handlers.onError?.(e);
      return;
    }

    if (resp.status === 429) {
      handlers.onRateLimit?.(resp.headers.get("Retry-After"));
      return;
    }
    if (!resp.ok || !resp.body) {
      handlers.onError?.(new Error(`/ask HTTP ${resp.status}`));
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const raw = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          if (!raw.trim()) continue;
          const { event, data } = parseFrame(raw);
          if (event === "sources") handlers.onSources?.(data);
          else if (event === "token") handlers.onToken?.(data?.text ?? "");
          else if (event === "done") handlers.onDone?.(data);
          else if (event === "refusal") handlers.onRefusal?.(data);
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) handlers.onError?.(e);
    }
  })();

  return () => controller.abort();
}
