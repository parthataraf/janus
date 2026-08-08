import { useEffect, useRef, useState } from "react";
import Mark from "./Mark.jsx";
import Answer from "./Answer.jsx";

// The grounding claim, in two lengths. The full version carries the empty
// state; the condensed one pins under the header for the rest of the session so
// "AI-generated, from real game data, cited" is never more than a glance away —
// it is the claim the whole product rests on, and it used to scroll away with
// the hero after the first question.
//
// Once the hero collapses this is the only persistent framing on screen, and its
// job changes: on the empty state it sells, here it reassures. Hence "answer
// here" rather than "this answer" — the line has to stay true at the fifth
// question, not just the first — and "so you can check it", which turns the
// citation from a boast into an invitation. It leads with the AI doing the
// writing and ends on the verification promise, which is the part worth reading
// twice and the reason that clause carries the accent.
export function GroundingBar() {
  return (
    <div className="grounding" role="note">
      <span className="grounding-glyph" aria-hidden="true">✦</span>
      <span>
        AI writes every answer here from Riot's official game data and live
        match stats.{" "}
        <strong className="grounding-key">
          Every claim links to its source, so you can check it.
        </strong>
      </span>
    </div>
  );
}

function Hero({ chips, onPick }) {
  return (
    <div className="hero">
      <Mark className="mark" />
      {/* Split so the big line serves a player (what do I get?) and the
          subtitle serves a sceptic (where does it come from?). */}
      <h1>Get All Your League of Legends Answers</h1>
      <p className="subtitle">
        AI-powered, built on Riot's official game data and live match stats.
      </p>
      {/* Breadth and the honest refusal only. The AI/data/cited claim is made
          by the subtitle above and the grounding bar during the session —
          stating it a third time on one screen reads as anxious, not emphatic. */}
      <p>
        Ask almost anything about League of Legends: champions, abilities, items,
        base stats, matchups, builds, or what's strong right now. When the data
        doesn't cover your question, Janus says so instead of guessing.
      </p>
      <div className="chips">
        {chips.map((c) => (
          <button className="chip" key={c} onClick={() => onPick(c)}>{c}</button>
        ))}
      </div>
    </div>
  );
}

export default function Chat({ messages, chips, onAsk, active, highlight, onCite, busy }) {
  const [text, setText] = useState("");
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    onAsk(q);
    setText("");
  };

  return (
    <section className="chat">
      {messages.length > 0 && <GroundingBar />}
      <div className="messages">
        <div className="messages-inner">
          {messages.length === 0 ? (
            <Hero chips={chips} onPick={onAsk} />
          ) : (
            messages.map((m) => (
              <div className="qa" key={m.id}>
                <div className="question">
                  <span className="who">❯</span>
                  <span className="text">{m.question}</span>
                </div>
                <Answer
                  message={m}
                  isActive={active?.id === m.id}
                  highlight={highlight}
                  onCite={onCite}
                />
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
      <div className="composer">
        <div className="composer-inner">
          <textarea
            rows={1}
            value={text}
            placeholder="Ask about champions, abilities, items, matchups, builds, the meta…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <button className="send" onClick={submit} disabled={!text.trim() || busy}>
            Ask
          </button>
        </div>
      </div>
    </section>
  );
}
