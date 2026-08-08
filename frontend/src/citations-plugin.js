// rehype plugin: make [n] citations interactive and group prose into sentences.
//
// For every <p>/<li>, it rewrites inline content into `<span class="sent cites-N …">`
// groups (one per sentence) and turns each `[n]` into `<button class="cite">[n]</button>`.
// The sentence's `cites-N` classes let a card click dim the answer and light up
// just the sentence(s) that cite that source (see styles.css .answer[data-hl]).
//
// Numbers are read from the button text on click, so no data-attributes are
// needed — keeping everything to className, which react-markdown maps reliably.

import { visit } from "unist-util-visit";

// A node with BLOCK-level element children is a container (e.g. a loose <li>
// wrapping a <p>, or a <p>… no — <p> never nests blocks), not a leaf prose
// block; skip it and let those block children be processed on their own visit.
// Crucially this must NOT include inline `code`: a sentence like "use `Query()`
// [1]." is leaf prose and must still get its citation wrapped.
const BLOCK = new Set([
  "p", "ul", "ol", "li", "pre", "blockquote", "div", "table", "thead", "tbody",
  "tr", "td", "th", "figure", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
]);

function citeButton(n) {
  return {
    type: "element",
    tagName: "button",
    properties: { className: ["cite"], type: "button" },
    children: [{ type: "text", value: `[${n}]` }],
  };
}

function sentenceSpan(nodes, cites) {
  const className = ["sent", ...[...cites].map((c) => `cites-${c}`)];
  return { type: "element", tagName: "span", properties: { className }, children: nodes };
}

// Split a paragraph/list-item's inline children into sentence spans, extracting
// [n] citations. Sentence boundary = . ! ? followed by whitespace or end.
function groupSentences(children) {
  const out = [];
  let cur = [];
  let cites = new Set();

  const flush = () => {
    if (cur.length === 0) return;
    out.push(sentenceSpan(cur, cites));
    cur = [];
    cites = new Set();
  };

  for (const child of children) {
    if (child.type !== "text") {
      cur.push(child);
      continue;
    }
    const s = child.value;
    const re = /\[(\d+)\]|([.!?])(?=\s|$)/g;
    let idx = 0;
    let m;
    while ((m = re.exec(s)) !== null) {
      const pre = s.slice(idx, m.index);
      if (pre) cur.push({ type: "text", value: pre });
      if (m[1] !== undefined) {
        cites.add(m[1]);
        cur.push(citeButton(m[1]));
      } else {
        cur.push({ type: "text", value: m[2] });
        flush();
      }
      idx = re.lastIndex;
    }
    const rest = s.slice(idx);
    if (rest) cur.push({ type: "text", value: rest });
  }
  flush();
  return out.length ? out : children;
}

export default function rehypeCitations() {
  return (tree) => {
    visit(tree, "element", (node) => {
      if (node.tagName !== "p" && node.tagName !== "li") return;
      if (node.children.some((c) => c.type === "element" && BLOCK.has(c.tagName))) return;
      node.children = groupSentences(node.children);
    });
  };
}
