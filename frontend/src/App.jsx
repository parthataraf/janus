import { useEffect, useMemo, useRef, useState } from "react";
import Header from "./Header.jsx";
import Chat from "./Chat.jsx";
import Citations from "./Citations.jsx";
import { getCorpora, streamAsk } from "./api.js";

// The live face. When Palworld ships, this becomes a per-corpus lookup.
const DEFAULT_CORPUS = "lol";

// One chip per retrieval path, and no champion twice — the old set spent three
// of six on the live path and named Yasuo in two of them, which read as "this
// thing knows Yasuo" rather than "this thing knows the roster".
//
// Ordered fastest-first, deliberately. The structured and prose paths answer in
// 1-3s; the live ones spend 5-8s inside OP.GG's MCP. A visitor's first click
// sets their sense of how quick the site is, so the quick ones lead and the live
// answers are found afterwards, once it has already been seen responding fast.
const CHIPS = [
  "What's Zed's ultimate cooldown?",             // structured — exact number
  "Which items give armor penetration?",        // structured — multi-row, 23 items
  "What does Thresh's lantern do?",             // prose — retrieved and cited
  "Who counters Yasuo?",                        // live (OP.GG) — counters
  "Who is Master Yi good against?",             // live — favourable matchups
  "What should I build on Jinx?",               // live — items, runes, skill order
];

export default function App() {
  const [theme, setTheme] = useState(
    () => document.documentElement.getAttribute("data-theme") || "dark"
  );
  const [corpora, setCorpora] = useState([]);
  const [corpus, setCorpus] = useState(DEFAULT_CORPUS);
  const [version, setVersion] = useState(null); // null = all versions
  const [messages, setMessages] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [pulse, setPulse] = useState(null); // { n, tick }
  const [highlight, setHighlight] = useState(null); // cite index dimming the active answer
  // Frame from the last answered turn, sent back so referential follow-ups
  // resolve. A ref, not state: it must never trigger a re-render, and the
  // in-flight request needs the value at send time.
  const contextRef = useRef(null);

  useEffect(() => {
    getCorpora()
      .then((d) => {
        const list = d.corpora || [];
        setCorpora(list);
        const pick = list.find((c) => c.corpus === DEFAULT_CORPUS) || list[0];
        if (pick) {
          setCorpus(pick.corpus);
          setVersion(pick.versions?.[0]?.version ?? null);
        }
      })
      .catch(() => {});
  }, []);

  function goHome() {
    setMessages([]);
    setActiveId(null);
    setHighlight(null);
    setPulse(null);
    // Drop the conversation frame too: a follow-up must never resolve against a
    // conversation the user has just cleared off the screen.
    contextRef.current = null;
  }

  function toggleTheme() {
    const t = theme === "dark" ? "light" : "dark";
    setTheme(t);
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("janus-theme", t);
  }

  function selectCorpus(c) {
    setCorpus(c);
    // Never resolve a follow-up against a different game's conversation.
    contextRef.current = null;
    const v = corpora.find((x) => x.corpus === c)?.versions?.[0]?.version ?? null;
    setVersion(v);
  }

  const busy = messages.some((m) => m.status === "streaming");

  function ask(question) {
    if (busy) return;
    const id =
      (crypto.randomUUID && crypto.randomUUID()) ||
      String(Date.now()) + Math.random();
    const msg = {
      id, question, corpus, doc_version: version,
      status: "streaming", answer: "", sources: [], top_score: null,
      refusal: null, timings: null,
    };
    setMessages((m) => [...m, msg]);
    setActiveId(id);
    setHighlight(null);
    setPulse(null);

    const patch = (p) =>
      setMessages((ms) =>
        ms.map((x) => (x.id === id ? { ...x, ...(typeof p === "function" ? p(x) : p) } : x))
      );

    streamAsk(
      { question, corpus, doc_version: version, context: contextRef.current },
      {
        onSources: (d) =>
          patch({ sources: d.sources || [], top_score: d.top_score, refused: d.refused, live: d.live }),
        onToken: (t) => patch((x) => ({ answer: x.answer + t })),
        onDone: (d) => {
          // Carry the frame only when the server minted one (i.e. the turn
          // answered). A refusal leaves the previous frame in place rather
          // than clearing it, so one bad question doesn't break the thread.
          if (d && d.context) contextRef.current = d.context;
          patch({ status: "done", timings: d });
        },
        onRefusal: (d) => patch({ status: "refused", refusal: d }),
        onRateLimit: (ra) =>
          patch({
            status: "refused",
            refusal: { message: `Rate limit reached — retry in ~${ra || "60"}s.`, top_score: null },
          }),
        onError: (e) =>
          patch({
            status: "refused",
            refusal: { message: `Request failed: ${e.message}`, top_score: null },
          }),
      }
    );
  }

  function onCite(messageId, n) {
    setActiveId(messageId); // swap panel to that answer's sources
    setHighlight(null);
    setPulse({ n, tick: Date.now() });
  }

  function onCard(n) {
    setHighlight((h) => (h === n ? null : n)); // toggle sentence highlight/dim
  }

  const active = useMemo(
    () => messages.find((m) => m.id === activeId) || null,
    [messages, activeId]
  );
  const versions = corpora.find((c) => c.corpus === corpus)?.versions || [];
  const activeVersion = version || versions[0]?.version || null;
  const corpusName = corpus === "lol" ? "League of Legends" : corpus;

  return (
    <div className="app">
      <Header
        corpora={corpora}
        corpus={corpus}
        version={activeVersion}
        onCorpus={selectCorpus}
        theme={theme}
        onToggleTheme={toggleTheme}
        onHome={goHome}
      />
      <div className="main">
        <Chat
          messages={messages}
          chips={CHIPS}
          onAsk={ask}
          active={active}
          highlight={highlight}
          onCite={onCite}
          busy={busy}
        />
        <Citations active={active} pulse={pulse} highlight={highlight} onCard={onCard} />
      </div>
      <footer className="footer">
        <span className="fresh">
          {corpusName}
          {activeVersion ? ` · patch ${activeVersion}` : ""} · live stats via OP.GG
        </span>
        {/* Both links pointed at the repository, which is private now, so they
            would 404 for every visitor. Attribution takes their place. */}
        <span className="footer-links">Created by Polaris AI Technologies</span>
      </footer>
    </div>
  );
}
