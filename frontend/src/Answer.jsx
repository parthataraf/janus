import { useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeCitations from "./citations-plugin.js";

const REHYPE = [rehypeHighlight, rehypeCitations];
const ms = (x) => (x == null ? "?" : Math.round(x));
const secs = (x) => (x == null ? "?" : `${(x / 1000).toFixed(1)}s`);

export default function Answer({ message, isActive, highlight, onCite }) {
  const { status, answer, refusal, timings } = message;
  const [showTiming, setShowTiming] = useState(false);

  // Full per-stage breakdown (kept in the DOM via title + click-to-expand for
  // debugging screenshots); the collapsed readout shows only the muted total.
  const breakdown = timings && [
    timings.embed_ms ? `embed ${ms(timings.embed_ms)}ms` : null,
    timings.retrieve_ms ? `retrieve ${ms(timings.retrieve_ms)}ms` : null,
    timings.rerank_ms ? `rerank ${ms(timings.rerank_ms)}ms` : null,
    timings.mcp_ms ? `OP.GG ${ms(timings.mcp_ms)}ms` : null,
    `generation ${ms(timings.generation_ms)}ms`,
  ].filter(Boolean).join(" · ");

  // Delegated click: a [n] cite button anywhere in the answer swaps the panel
  // to this answer's sources and pulses card n.
  function handleClick(e) {
    const btn = e.target.closest(".cite");
    if (!btn) return;
    const n = parseInt(btn.textContent.replace(/\D/g, ""), 10);
    if (n) onCite(message.id, n);
  }

  if (status === "refused") {
    const live = refusal?.live;
    return (
      <div className={"refusal-card" + (live ? " warming" : "")}>
        {/* Neutral title: the API sends the specific reason (no data for this
            champion / slow / warming up / unavailable) in refusal.message, so a
            hardcoded "warming up" here would contradict the line beneath it. */}
        <div className="title">
          {live ? "Live stats unavailable" : "This isn't covered by the game data"}
        </div>
        <div>{refusal?.message}</div>
        {!live && refusal?.top_score != null && (
          <div className="score">
            Closest passage scored {Number(refusal.top_score).toFixed(2)} — below the
            0.00 relevance threshold.
          </div>
        )}
      </div>
    );
  }

  const streaming = status === "streaming";
  return (
    <>
      <div
        className="answer"
        data-hl={isActive && highlight ? String(highlight) : undefined}
        onClick={handleClick}
      >
        {answer ? (
          <>
            <ReactMarkdown rehypePlugins={REHYPE}>{answer}</ReactMarkdown>
            {streaming && <span className="caret" />}
          </>
        ) : streaming ? (
          <div className="shimmer">
            <span /><span /><span />
          </div>
        ) : null}
      </div>
      {status === "done" && timings && (
        <div className="latency">
          <button
            type="button"
            className="latency-total"
            title={breakdown}
            aria-expanded={showTiming}
            onClick={() => setShowTiming((v) => !v)}
          >
            {secs(timings.total_ms)}
          </button>
          {showTiming && <span className="latency-breakdown">{breakdown}</span>}
        </div>
      )}
    </>
  );
}
