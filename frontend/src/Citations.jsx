import { useEffect, useRef } from "react";

// Map a cross-encoder rerank score (~ -11..+8) to a fixed-width bar fill.
function fillPct(score) {
  const f = Math.max(0, Math.min(1, ((score ?? -6) + 6) / 14));
  return `${Math.round(f * 100)}%`;
}

// Strip the "{ #anchor }" markers MkDocs leaves in heading paths.
function cleanHeading(h) {
  return (h || "").replace(/\s*\{\s*#[^}]*\}/g, "");
}

export default function Citations({ active, pulse, highlight, onCard }) {
  const refs = useRef({});

  // On a [n] click, scroll its card into view and re-trigger the glow pulse.
  useEffect(() => {
    if (!pulse?.n) return;
    const el = refs.current[pulse.n];
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("pulse");
    void el.offsetWidth; // force reflow so the animation restarts
    el.classList.add("pulse");
  }, [pulse?.tick, pulse?.n]);

  if (!active) {
    return (
      <aside className="panel">
        <div className="panel-head"><span className="qtext">Sources appear here</span></div>
        <div className="panel-body">
          {[1, 2, 3, 4, 5].map((i) => (
            <div className="slot" key={i}>
              <span className="idx">{i}</span>
              <span>retrieved passage [{i}]</span>
            </div>
          ))}
        </div>
      </aside>
    );
  }

  const { question, sources = [] } = active;
  return (
    <aside className="panel">
      <div className="panel-head">
        <span className="who">❯ </span>
        <span className="qtext">{question}</span>
      </div>
      <div className="panel-body">
        {sources.map((s) =>
          s.kind === "live_stats" ? (
            <div
              className={"source-card live" + (highlight === s.index ? " selected" : "")}
              key={s.index}
              ref={(el) => (refs.current[s.index] = el)}
              onClick={() => onCard(s.index)}
            >
              <div className="top">
                <span className="idx">{s.index}</span>
                <span className="live-badge"><span className="live-dot">●</span> LIVE · OP.GG</span>
              </div>
              <div className="heading">{cleanHeading(s.heading_path)}</div>
              <div className="preview">{s.preview}</div>
              <div className="freshness">
                {s.patch ? `patch ${s.patch}` : "current patch"}
                {s.fetched_at ? ` · fetched ${s.fetched_at}` : ""}
              </div>
              <a className="link quiet" href={s.source_url} target="_blank" rel="noreferrer">
                view on OP.GG ↗
              </a>
            </div>
          ) : (
            <div
              className={"source-card" + (highlight === s.index ? " selected" : "")}
              key={s.index}
              ref={(el) => (refs.current[s.index] = el)}
              onClick={() => onCard(s.index)}
            >
              <div className="top">
                <span className="idx">{s.index}</span>
                <span className="score">
                  <span className="bar">
                    <span className="fill" style={{ width: fillPct(s.rerank_score) }} />
                  </span>
                  <span className="num">
                    {s.rerank_score != null ? s.rerank_score.toFixed(2) : "—"}
                  </span>
                </span>
              </div>
              <div className="heading">{cleanHeading(s.heading_path)}</div>
              <div className="preview">{s.preview}</div>
              {/* Deliberately NOT a link. This used to point at raw Data Dragon
                  JSON, which is unreadable to a player, and some structured
                  answers carry no URL at all so the anchor silently did nothing.
                  A fan wiki would add a source our grounding story doesn't
                  cover, and an OP.GG link here would credit them for a number
                  that came from Riot. So: name the provenance, link nowhere. */}
              <div className="freshness">
                Riot Data Dragon{s.patch ? ` · ${s.patch}` : ""}
              </div>
            </div>
          )
        )}
      </div>
    </aside>
  );
}
