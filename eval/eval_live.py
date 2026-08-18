"""Behavior eval for the live OP.GG stats path. Live numbers are
patch-volatile, so we score BEHAVIOR, not pinned values:
  - routed to the expected live path (or, for off-corpus, NOT routed live)
  - the MCP was actually called (mcp_ms > 0)
  - attribution present (OP.GG + patch + fetched-at on the card)
  - numbers present in the answer
  - hedging respected (no dodge/avoid/'don't play' advice)
Plus: off-corpus refuses, and a forced-degradation check (MCP blocked ->
honest "warming up" message, never a fabricated answer). Existing value-pinned
eval (testset_lol.jsonl) is untouched and separately confirmed 0-drift.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
from core import followup, opgg_live, lol_routing, generation, retrieval, store
from app.routes import _fetch_live_card, LIVE_UNAVAILABLE

HERE = Path(__file__).resolve().parent
LIVE = HERE / "testset_lol_live.jsonl"
PINNED = HERE / "testset_lol.jsonl"
_DODGE = ("dodge", "don't play", "do not play", "avoid playing", "ban this", "never play")
_NUM = re.compile(r"\d")


def _load(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def _answer(q, cand):
    return "".join(generation.generate_stream(q, [cand]))


def _warm(champs):
    """Warm OP.GG's server cache for the eval champions (a timed-out first call
    still triggers the compute, so a second call lands warm)."""
    for cid in champs:
        for _ in range(2):
            try:
                opgg_live.analyze(cid, cid, "both")
                break
            except opgg_live.OpggUnavailable:
                continue


def main() -> int:
    opgg_live.start()
    patch = (store.lol_patches() or [None])[-1]
    rows = _load(LIVE)
    print(f"Live eval: {len(rows)} questions, patch {patch}, "
          f"generator {generation.config.GEN_MODEL}")
    print("warming eval champions...", flush=True)
    _warm(["Yasuo", "Zed", "Jinx", "Garen"])
    print()

    passed = 0
    for r in rows:
        q, typ, expect = r["question"], r["type"], r["expect_live"]
        intent = lol_routing.detect_live_intent(q, patch)
        kind = intent["kind"] if intent else None
        checks = {"routed": kind == expect}

        if expect is None:  # off-corpus: must NOT route live AND must refuse
            chunks = retrieval.route(q, "lol", top_n=5)
            refused = generation._should_refuse(chunks)
            checks["not_live"] = intent is None
            checks["refused"] = refused
        elif intent is None:  # expected live but didn't route — hard fail, no crash
            checks["mcp_called"] = False
            r["_answer"] = "(did not route to live path)"
        else:
            try:
                card = _fetch_live_card(intent)
                ans = _answer(q, retrieval.Candidate(
                    id=-99, content=card["content"], source_url=card["source_url"],
                    heading_path=card["heading_path"], rerank_score=15.0,
                    sources={"live_stats"}))
                checks["mcp_called"] = (card.get("mcp_ms") or 0) > 0
                checks["attribution"] = bool(card.get("patch")) and bool(card.get("fetched_at")) \
                    and "OP.GG" in card["content"]
                checks["numbers"] = bool(_NUM.search(ans))
                checks["hedged"] = not any(w in ans.lower() for w in _DODGE)
                r["_answer"] = ans
            except opgg_live.OpggUnavailable as e:
                checks["mcp_called"] = False
                r["_answer"] = f"(degraded: {e})"

        ok = all(checks.values())
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {typ:10} {q}")
        print(f"        {checks}")
        if r.get("_answer"):
            print(f"        -> {r['_answer'][:160]}")

    # --- forced degradation check ---
    print("\n--- degradation check (MCP blocked) ---")
    orig = opgg_live.analyze
    # Each failure kind now carries its own copy, so score the MAPPING rather
    # than one fixed string: the message must match the failure that occurred.
    cases = [
        (opgg_live.OpggTimeout(champion="YASUO", first_touch=True), "warming up"),
        (opgg_live.OpggTimeout(champion="YASUO", first_touch=False), "slow to respond"),
        (opgg_live.OpggIncomplete("Yasuo", "the entire analysis payload"),
         "doesn't have current stats"),
        (opgg_live.OpggEndpointError("tool_error"), "unavailable right now"),
    ]
    try:
        intent = lol_routing.detect_live_intent("Who counters Yasuo?", patch)
        for exc, expected in cases:
            opgg_live.analyze = (lambda e: lambda *a, **k:
                                 (_ for _ in ()).throw(e))(exc)
            degraded, msg = False, None
            try:
                _fetch_live_card(intent)
            except opgg_live.OpggUnavailable as caught:
                degraded = True
                msg = getattr(caught, "user_message", LIVE_UNAVAILABLE)
            ok = degraded and expected in msg
            print(f"[{'PASS' if ok else 'FAIL'}] {type(exc).__name__} -> honest message: "
                  f"degraded={degraded}, msg={msg!r}")
            passed += ok
    finally:
        opgg_live.analyze = orig

    # --- conversational carryover (follow-ups) ---
    # A follow-up that names no champion used to refuse. These score the gate in
    # BOTH directions: a referential follow-up must resolve to the previous
    # champion, and a non-referential entity-less question must NOT.
    print("\n--- follow-ups: referential carryover, and its limits ---")
    seed_q = "who is jax good against in top lane"
    seed = lol_routing.detect_live_intent(seed_q, patch)
    frame = followup.mint(seed, {}, ["Yone"], "lol", patch) if seed else None
    followups = [
        # (question, expect_carryover, expected champion or None)
        ("what about some more champs he's good against", True, "Jax"),
        ("you told me yone but can't tell me more", True, "Jax"),
        ("who else is he strong against", True, "Jax"),
        # NEGATIVE: entity-less but NOT referential -> must route normally.
        ("how do I climb in ranked", False, None),
        ("what is the best way to farm minions", False, None),
        # NEGATIVE: a genuinely new champion is a new subject, not a follow-up.
        ("what about Darius", False, None),
    ]
    fu_ok = 0
    if seed is None:
        print("[FAIL] seed question did not route live; follow-up checks skipped")
    else:
        for q, expect, champ in followups:
            got = lol_routing.detect_live_intent(q, patch, frame)
            carried = bool(got and got.get("followup"))
            ok = (carried == expect) and (not expect or got["a"][1] == champ)
            fu_ok += ok
            detail = f"-> {got['a'][1]} ({got['kind']})" if carried else "-> no carryover"
            print(f"[{'PASS' if ok else 'FAIL'}] {'carry ' if expect else 'NO carry'} "
                  f"| {q!r} {detail}")
    passed += fu_ok

    # --- "who else" must return DIFFERENT champions, end to end ---
    # The gate already resolved these; what it could not do was answer them. The
    # intent a follow-up resolves to is identical to the previous turn's, so the
    # card re-printed the same names — invisible while the source held three rows
    # per direction, and the whole point of moving to the full matchup table.
    print("\n--- follow-ups: 'who else' returns new champions ---")
    walk_ok = 0
    walk_total = 2
    for seed_query, direction in [("who counters yasuo", "weak"),
                                  ("who is jax good against in top lane", "strong")]:
        try:
            i1 = lol_routing.detect_live_intent(seed_query, patch)
            subject = i1["a"][1]          # the preview names it too ("Counters to Yasuo")
            c1 = _fetch_live_card(i1)
            listed = lambda card: [n for n in
                                   lol_routing.champions_in(card["preview"], patch)
                                   if n != subject]
            named1 = listed(c1)
            f1 = followup.mint(i1, c1, lol_routing.champions_in(c1["preview"], patch),
                               "lol", patch)
            i2 = lol_routing.detect_live_intent("who else", patch, f1)
            c2 = _fetch_live_card(i2)
            named2 = listed(c2)
            fresh = [n for n in named2 if n not in named1]
            overlap = sorted(set(named1) & set(named2))
            ok = bool(named1) and bool(fresh) and not overlap
            walk_ok += ok
            print(f"[{'PASS' if ok else 'FAIL'}] {seed_query!r} ({direction})")
            print(f"        turn 1: {named1}")
            print(f"        turn 2: {named2}  new={fresh} repeated={overlap or 'none'}")
        except opgg_live.OpggUnavailable as e:
            print(f"[FAIL] {seed_query!r} degraded: {e}")
    passed += walk_ok

    # --- guard: existing pinned eval never triggers the live path ---
    # Checked WITH a live frame present: carryover must not drag the pinned
    # questions into the live path, which is a strictly stronger guard.
    print("\n--- guard: live path never triggers on the 32 pinned questions ---")
    pinned = _load(PINNED)
    triggered = [r["question"] for r in pinned
                 if lol_routing.detect_live_intent(r["question"], patch, frame) is not None]
    guard_ok = not triggered
    print(f"[{'PASS' if guard_ok else 'FAIL'}] {len(pinned)} pinned questions "
          f"(with a live frame present), {len(triggered)} triggered live"
          + (f": {triggered}" if triggered else ""))
    passed += guard_ok

    # rows + degradation cases + follow-up cases + list-walk cases + the guard
    total = len(rows) + len(cases) + len(followups) + walk_total + 1
    print(f"\n=== {passed}/{total} behavior checks passed ===")
    opgg_live.stop()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
