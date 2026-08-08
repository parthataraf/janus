import { useEffect, useState } from "react";
import Mark from "./Mark.jsx";

// Friendly display names for the corpus ids that /corpora returns. The short
// form is for phones: "League of Legends" is a long label for what is currently
// one real option, and it dominates the row once everything else has collapsed.
const CORPUS_LABELS = { lol: "League of Legends" };
const CORPUS_LABELS_SHORT = { lol: "LoL" };
const corpusLabel = (c, short) =>
  (short ? CORPUS_LABELS_SHORT[c] : CORPUS_LABELS[c]) || CORPUS_LABELS[c] || c;

/** Option text inside a <select> can't be swapped by CSS, so this one label
 *  needs the breakpoint in JS. Everything else collapses in the stylesheet. */
function useNarrow(query = "(max-width: 420px)") {
  const [narrow, setNarrow] = useState(
    () => typeof matchMedia === "function" && matchMedia(query).matches
  );
  useEffect(() => {
    const mq = matchMedia(query);
    const on = (e) => setNarrow(e.matches);
    mq.addEventListener("change", on);
    setNarrow(mq.matches);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return narrow;
}

/** Theme as a segmented control: both modes named, the live one filled.
 *  A single toggle button would have to name either the current state or the
 *  destination, and either reading is ambiguous without hovering for a tooltip
 *  — which requires already being curious. This shows state and options at
 *  once. Home is a verb and this is a state, so they are typed differently on
 *  purpose. */
function ThemeControl({ theme, onToggleTheme }) {
  const pick = (want) => want !== theme && onToggleTheme();
  return (
    <div className="theme-seg" role="group" aria-label="Colour theme">
      <button
        className={theme === "light" ? "on" : ""}
        onClick={() => pick("light")}
        aria-pressed={theme === "light"}
        title="Light theme"
      >
        <span aria-hidden="true">☀</span>
        <span className="seg-label">Light</span>
      </button>
      <button
        className={theme === "dark" ? "on" : ""}
        onClick={() => pick("dark")}
        aria-pressed={theme === "dark"}
        title="Dark theme"
      >
        <span aria-hidden="true">☾</span>
        <span className="seg-label">Dark</span>
      </button>
    </div>
  );
}

export default function Header({
  corpora, corpus, version, onCorpus, theme, onToggleTheme, onHome,
}) {
  const narrow = useNarrow();
  return (
    <header className="header">
      <div className="header-left">
        {/* Icon AND word: a bare glyph relied on recognising ⌂, and this is the
            one control that discards a whole conversation, so it should not
            need deciphering. Always present, so its position is learnable. */}
        <button
          className="btn-labeled"
          onClick={onHome}
          title="Start a new conversation"
          aria-label="Home — start a new conversation"
        >
          <span aria-hidden="true">⌂</span>
          <span className="btn-label">Home</span>
        </button>
        {/* Second path to the same action: people click the logo to go home. */}
        <button
          className="brand"
          onClick={onHome}
          aria-label="Janus — start a new conversation"
        >
          <Mark />
          <div className="brand-text">
            <span className="brand-name">Janus</span>
            <span className="brand-sub">AI answers from real game data · League of Legends</span>
          </div>
        </button>
      </div>
      <div className="header-right">
        {/* Which patch the answers come from: a product signal (the data is
            current), not a control. No border, background or hover state, so it
            never invites a click. There is nothing here to choose. */}
        {version && (
          <span className="patch-badge">
            <span className="patch-word">Patch </span>{version}
          </span>
        )}
        <select
          className="select"
          value={corpus}
          onChange={(e) => onCorpus(e.target.value)}
          title="Game"
          aria-label="Game"
        >
          {corpora.map((c) => (
            <option key={c.corpus} value={c.corpus}>
              {corpusLabel(c.corpus, narrow)}
            </option>
          ))}
          {/* Second face, on the roadmap. Hardcoded + disabled so it shows even
              though it's (correctly) absent from /corpora until it's ingested. */}
          <option value="palworld" disabled>
            {narrow ? "Palworld (soon)" : "Palworld — coming soon"}
          </option>
        </select>
        <span className="sep" />
        <ThemeControl theme={theme} onToggleTheme={onToggleTheme} />
      </div>
    </header>
  );
}
