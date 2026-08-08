"""Live LoL stats via OP.GG's official MCP server (sanctioned, no API key).

Product decision (owner-approved): the meta-question path — matchups/counters,
win/pick/ban, tier/popularity — is answered by LIVE-QUERYING OP.GG's MCP per
question and is NEVER cached into our own tables. Every answer is attributed to
OP.GG with the patch + a fetched-at timestamp.

The MCP client is async, but our serving path (`app/routes.ask`) is sync, so this
module owns a persistent event loop on a background thread and exposes SYNC
helpers. The session is initialized once (warm) at API startup and reused. On a
dead/stale-session transport error we re-initialize ONCE and retry before giving
up; a hard timeout (`TIMEOUT_S`) degrades to `OpggUnavailable` so the caller can
show an honest "live stats unavailable" message rather than hang or guess.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from datetime import datetime, timezone

# Both of these names are mcp 1.x spellings and BOTH were renamed in 2.x
# (McpError -> MCPError, streamablehttp_client -> streamable_http_client) with no
# back-compat alias, so `mcp` is pinned to 1.28.1 in requirements.txt. A defensive
# try/except on one name would only move the ImportError down to the other; 2.x
# also swaps httpx for httpx2, so adopting it is a migration, not a rename.
from mcp import ClientSession, McpError
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("docpilot")

MCP_URL = "https://mcp-api.op.gg/mcp"
# Per-attempt MCP timeout. Owner approved 5s, but measured cold (server-side
# per-champion compute) latency is ~7s on first touch, so 5s degraded on almost
# every uncached champion. Raised to 8s so cold champions resolve; still degrades
# gracefully on a true hang/outage. (Flagged for owner review — revert to 5.0 to
# prefer frequent degradation over the wait.)
TIMEOUT_S = 8.0
_ANALYSIS = "lol_get_champion_analysis"

# The full matchup table. `lol_get_champion_analysis` exposes only OP.GG's
# curated weak/strong_counters (three rows per direction, sample-thresholded, and
# routinely EMPTY on the favourable side — Master Yi jungle returns none), which
# is why we used to answer "no sufficient favorable-matchup sample" for a
# champion whose OP.GG page shows five. This tool returns `data.counters`: every
# tracked matchup for the champion+lane, 13-41 rows, both directions. Sorting it
# reproduces both panels of OP.GG's counter tab exactly, to the game count.
#
# It costs more than everything else we call: 40-48KB and 2.6-6.8s measured, with
# NO desired_output_fields parameter to trim it server-side. Hence its own
# timeout — an 8s budget clipped a measured 6.8s call with no margin.
_MATCHUP_GUIDE = "lol_get_lane_matchup_guide"
GUIDE_TIMEOUT_S = 12.0

# `data.counters` is IDENTICAL for every `opponent_champion` we pass (verified
# across three) — the opponent only selects the prose tip, which we don't use. So
# a question with no opponent ("who counters Yasuo") passes the champion as its
# own opponent. Verified to work and to leave the champion out of its own table.
_SELF_OPPONENT = True

# Matchups below this many games are dropped before we report anything.
#
# Chosen from the data, not from taste. At 50 games a win rate carries a 95%
# interval of about +/-13pp, which is wide but readable next to the sample we
# print; at 30 it is +/-17pp, which is noise wearing a decimal point. Going the
# other way, a 100-game floor deletes EVERY favourable matchup Master Yi has —
# reinstating the exact bug this replaces — and empties Azir, Kindred and Soraka
# entirely. 50 leaves 10 of 12 sampled champions a full five rows per direction
# while cutting the tail that reads as precision and isn't.
MIN_MATCHUP_GAMES = 50
MATCHUP_ROWS = 5

# Champion display/build page for click-through attribution.
_OPGG_PAGE = "https://op.gg/lol/champions/{slug}/build"

# Neutral seed for the role-DISCOVERY query. OP.GG returns the champion's full
# positions[] (every lane, with role_rate) regardless of which position we query,
# so positions[] is the AUTHORITATIVE source of the most-played lane — there is NO
# static per-champion role map. This single default just seeds the discovery call;
# positions[] then decides, and we re-query the winning lane for its counters.
_SEED_POSITION = "mid"
_POSITIONS = ("top", "mid", "jungle", "adc", "support")

# DDragon champion id -> OP.GG UPPER_SNAKE_CASE overrides where a naive transform
# would be wrong. Most ids convert cleanly (camelCase -> UPPER_SNAKE).
_NAME_OVERRIDES = {
    "MonkeyKing": "WUKONG", "Nunu": "NUNU_WILLUMP",
}


class OpggUnavailable(Exception):
    """Live stats could not be fetched (timeout, transport, or tool error).

    Every subclass carries its own `user_message`, because these failures are not
    interchangeable and the copy is the only thing the user ever sees. Telling
    someone to "try again in a few seconds" when OP.GG simply has no data for the
    champion sends them into a retry loop that cannot succeed. The caller reads
    `user_message` rather than matching on type.
    """

    user_message = "Live stats are unavailable right now."


class OpggEndpointError(OpggUnavailable):
    """The endpoint errored, was unreachable, or returned something unusable."""

    user_message = "Live stats are unavailable right now."


class OpggTimeout(OpggUnavailable):
    """The request exceeded its deadline.

    Retrying IS reasonable here, so the copy invites it. We further split genuine
    first-touch cold starts from a generally slow endpoint: OP.GG computes a
    champion's aggregate on first touch (~7s) and serves it fast afterwards, so a
    timeout on a champion this process has never fetched is plausibly that cold
    compute. NOTE this is a proxy, not a true signal — OP.GG's cache is
    server-side and shared, so a champion another client just warmed still counts
    as "first touch" to us. It only ever changes the wording, never the behaviour.
    """

    def __init__(self, champion: str | None = None, first_touch: bool = False):
        self.champion, self.first_touch = champion, first_touch
        super().__init__(f"timeout{' (first touch)' if first_touch else ''}")

    @property
    def user_message(self) -> str:
        if self.first_touch:
            return ("Live stats are still warming up for this champion — "
                    "try again in a few seconds.")
        return "OP.GG is slow to respond right now — try again in a moment."


# OP.GG names that have returned a usable payload in this process. Only used to
# tell a first-touch cold start from a slow endpoint (see OpggTimeout).
_SEEN_CHAMPIONS: set[str] = set()


class OpggIncomplete(OpggUnavailable):
    """The response ARRIVED but is missing fields the card would have to render.

    Separate from OpggUnavailable (which means we never got an answer) because the
    two deserve different copy: a timeout is "try again in a few seconds", whereas
    a payload with no `summary` means OP.GG genuinely has nothing for this
    champion right now and retrying will not help.

    This exists because a partial payload used to render as fact: `summary` came
    back absent, `.get()` chains collapsed to None, and the card read "win None% ·
    pick None% · tier None (#None)" — which the generator then narrated as prose.
    Missing data must degrade, never render.
    """

    def __init__(self, display: str, what: str):
        self.display, self.what = display, what
        super().__init__(f"incomplete OP.GG payload for {display}: missing {what}")

    @property
    def user_message(self) -> str:
        return f"OP.GG doesn't have current stats for {self.display} right now."


def opgg_name(champion_id: str) -> str:
    """DDragon champion id -> OP.GG UPPER_SNAKE_CASE (e.g. AurelionSol -> AURELION_SOL)."""
    if champion_id in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[champion_id]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", champion_id)  # camelCase split
    return re.sub(r"[^A-Za-z0-9]+", "_", s).upper().strip("_")


def _slug(champion_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", champion_id.lower())


# --------------------------------------------------------------------------- #
# Persistent warm session on a background event loop
# --------------------------------------------------------------------------- #
class _Manager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = threading.Lock()

    # --- lifecycle (called from lifespan) ---
    def start(self) -> bool:
        """Idempotently spin up the loop thread and initialize the MCP session.
        Returns True if the warm session is ready. Never raises — a failure just
        means the live path will lazily reconnect (and degrade if it can't)."""
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._run, name="opgg-mcp", daemon=True)
                self._thread.start()
            if self._session is not None:
                return True
            try:
                self._run_coro(self._connect(), timeout=15).result(timeout=16)
                logger.info(json.dumps({"event": "opgg_startup", "status": "ready"}))
                return True
            except Exception as e:  # noqa: BLE001 — don't crash API startup
                logger.info(json.dumps(
                    {"event": "opgg_startup", "status": "failed", "error": str(e)[:200]}))
                return False

    def reset(self) -> bool:
        """Drop the warm session so the next call builds a fresh one.

        The session is long-lived (initialized once at startup and reused for
        days). If the far side associates anything durable with it — a cached
        result computed while a champion's aggregate was mid-refresh, say — every
        later call on that session inherits it, while a brand-new session gets
        correct data. Reconnecting is the only lever we have over that.
        """
        with self._lock:
            if not (self._loop and self._loop.is_running()):
                return False
            try:
                self._run_coro(self._disconnect(), timeout=10).result(timeout=12)
                logger.info(json.dumps({"event": "opgg_session_reset"}))
                return True
            except Exception as e:  # noqa: BLE001 — best effort; next call reconnects
                logger.info(json.dumps(
                    {"event": "opgg_session_reset", "error": str(e)[:120]}))
                self._session = self._stack = None
                return False

    def stop(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_running():
                try:
                    self._run_coro(self._disconnect(), timeout=5).result(timeout=6)
                except Exception:  # noqa: BLE001
                    pass
                self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # --- async session management (run on the loop thread) ---
    async def _connect(self) -> None:
        stack = AsyncExitStack()
        read, write, _ = await stack.enter_async_context(streamablehttp_client(MCP_URL))
        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=TIMEOUT_S * 2)
        # Prime the tool cache so per-call output validation doesn't trigger an
        # extra list_tools round-trip on every call_tool (adds latency + can be
        # cancelled mid-validation by the call timeout).
        await asyncio.wait_for(session.list_tools(), timeout=TIMEOUT_S * 2)
        self._stack, self._session = stack, session

    async def _disconnect(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._stack = self._session = None

    async def _one_call(self, name: str, args: dict, timeout: float):
        return await asyncio.wait_for(self._session.call_tool(name, args), timeout=timeout)

    def _timeout(self, args: dict) -> OpggTimeout:
        # The guide tool names the champion differently from the analysis tool.
        champ = args.get("champion") or args.get("my_champion")
        return OpggTimeout(champion=champ, first_touch=champ not in _SEEN_CHAMPIONS)

    async def _call_healing(self, name: str, args: dict, timeout: float):
        if self._session is None:
            await self._connect()
        try:
            return await self._one_call(name, args, timeout)
        except asyncio.TimeoutError:
            # ONE immediate retry on the existing (warm) session. A timeout is
            # usually OP.GG computing this champion's aggregate for the first
            # time; the compute keeps running server-side after we give up, so
            # the second call often lands on the finished result. Costs one extra
            # deadline in the worst case, which the outer bound accounts for.
            logger.info(json.dumps({"event": "opgg_retry_timeout", "tool": name,
                                    "champion": args.get("champion")}))
            try:
                return await self._one_call(name, args, timeout)
            except asyncio.TimeoutError:
                raise self._timeout(args)
        except McpError as e:  # tool/validation error — not a session problem
            raise OpggEndpointError(f"tool:{str(e)[:80]}")
        except Exception:  # noqa: BLE001 — likely dead/stale session: self-heal once
            logger.info(json.dumps({"event": "opgg_reconnect", "tool": name}))
            await self._disconnect()
            await self._connect()
            try:
                return await self._one_call(name, args, timeout)
            except asyncio.TimeoutError:
                raise self._timeout(args)
            except Exception as e2:  # noqa: BLE001
                raise OpggEndpointError(f"retry_failed:{type(e2).__name__}")

    # --- sync bridge for the (sync) serving path ---
    def call_tool(self, name: str, args: dict, timeout: float | None = None,
                  parse=None) -> dict:
        """One tool call. `parse` maps the response text to a dict — the analysis
        tool answers in OP.GG's compact positional repr, the matchup guide answers
        in plain JSON, so the parser travels with the call."""
        if self._loop is None:
            self.start()
        timeout = TIMEOUT_S if timeout is None else timeout
        parse = parse or _parse_repr
        fut = self._run_coro(self._call_healing(name, args, timeout), timeout=timeout)
        try:
            # Bounds the worst inner path: first call + one timeout retry (or a
            # reconnect + retry), each capped at `timeout`, plus slack.
            result = fut.result(timeout=timeout * 3 + 4)
        except FutureTimeout:
            raise self._timeout(args)
        if getattr(result, "isError", False):
            raise OpggEndpointError("tool_error")
        for c in result.content:
            txt = getattr(c, "text", None)
            if txt:
                try:
                    return parse(txt)
                except Exception as e:  # noqa: BLE001
                    raise OpggEndpointError(f"parse:{type(e).__name__}")
        raise OpggEndpointError("empty")


_mgr = _Manager()


# --------------------------------------------------------------------------- #
# Parser for OP.GG MCP's compact positional repr.
# The payload is a schema header (`class Name: f1,f2,...` lines) followed by a
# nested positional expression `Name(v1, v2, Nested(...), [Item(...), ...])`.
# We map each object's positional args back to its class's field names.
# --------------------------------------------------------------------------- #
def _parse_repr(text: str) -> dict:
    lines = text.strip().split("\n")
    schema: dict[str, list[str]] = {}
    body_idx = 0
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("class "):
            name, _, fields = st[6:].partition(":")
            schema[name.strip()] = [f.strip() for f in fields.split(",") if f.strip()]
        elif st:
            body_idx = i
            break
    body = "\n".join(lines[body_idx:]).strip()
    val, _ = _parse_value(body, 0, schema)
    # A real payload is always an object, so a non-dict here means the text was
    # not the format we think it is. Raise instead of returning {"value": ...}:
    # the caller turns this into OpggUnavailable and the answer degrades
    # honestly, rather than a junk dict flowing on as if it were live stats.
    if not isinstance(val, dict):
        raise ValueError(f"unrecognised MCP payload (parsed {type(val).__name__})")
    return val


def _parse_value(s: str, i: int, schema: dict):
    i = _skip_ws(s, i)
    ch = s[i]
    if ch == '"':
        return _parse_string(s, i)
    if ch == '[':
        return _parse_list(s, i, schema)
    if ch == '-' or ch.isdigit():
        return _parse_number(s, i)
    # identifier: object Name(...) or a bareword (null/true/false/enum)
    j = i
    while j < len(s) and (s[j].isalnum() or s[j] in "_."):
        j += 1
    ident = s[i:j]
    k = _skip_ws(s, j)
    if k < len(s) and s[k] == '(':
        return _parse_object(ident, s, k, schema)
    low = ident.lower()
    if low in ("none", "null"):
        return None, j
    if low == "true":
        return True, j
    if low == "false":
        return False, j
    return ident, j


def _parse_object(name: str, s: str, i: int, schema: dict):
    i += 1  # consume '('
    args = []
    i = _skip_ws(s, i)
    if s[i] == ')':
        return {"_type": name}, i + 1
    while True:
        v, i = _parse_value(s, i, schema)
        args.append(v)
        i = _skip_ws(s, i)
        if s[i] == ',':
            i = _skip_ws(s, i + 1)
            continue
        if s[i] == ')':
            i += 1
            break
    fields = schema.get(name, [])
    obj = {fields[n]: args[n] for n in range(min(len(fields), len(args)))}
    return obj, i


def _parse_list(s: str, i: int, schema: dict):
    i += 1  # consume '['
    items = []
    i = _skip_ws(s, i)
    if s[i] == ']':
        return items, i + 1
    while True:
        v, i = _parse_value(s, i, schema)
        items.append(v)
        i = _skip_ws(s, i)
        if s[i] == ',':
            i = _skip_ws(s, i + 1)
            continue
        if s[i] == ']':
            i += 1
            break
    return items, i


def _parse_string(s: str, i: int):
    i += 1  # consume opening quote
    buf = []
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            buf.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            return "".join(buf), i + 1
        buf.append(c)
        i += 1
    return "".join(buf), i


def _parse_number(s: str, i: int):
    j = i
    if s[j] == '-':
        j += 1
    while j < len(s) and (s[j].isdigit() or s[j] == '.'):
        j += 1
    tok = s[i:j]
    return (float(tok) if '.' in tok else int(tok)), j


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def start() -> bool:
    return _mgr.start()


def stop() -> None:
    _mgr.stop()


# --------------------------------------------------------------------------- #
# High-level: fetch + normalize + format the live-stats context card
# --------------------------------------------------------------------------- #
_STATS_FIELDS = [
    "champion", "data.summary.average_stats",
    "data.summary.positions[].name", "data.summary.positions[].stats.role_rate",
    "data.trends.win",
]
_COUNTER_FIELDS = ["data.weak_counters", "data.strong_counters", "data.counters_meta"]
# Recommended-build fields. OP.GG returns these per (champion, position) with a
# pick_rate and a play/win sample, so a build answer is attributable the same way
# a win-rate answer is. `summoner_spells` is deliberately NOT requested: it comes
# back as numeric ids and we do not ingest Data Dragon's summoner-spell map, so
# we would have to either print raw ids or guess. Omitting beats guessing.
_BUILD_FIELDS = [
    "data.core_items.ids_names", "data.core_items.pick_rate",
    "data.core_items.play", "data.core_items.win",
    "data.boots.ids_names", "data.boots.pick_rate",
    "data.starter_items.ids_names", "data.starter_items.pick_rate",
    "data.runes.primary_page_name", "data.runes.primary_rune_names",
    "data.runes.secondary_page_name", "data.runes.secondary_rune_names",
    "data.runes.pick_rate",
    "data.skills.order", "data.skills.pick_rate",
]


def _pct(x):
    return None if x is None else round(float(x) * 100, 1)


def _primary_position(data: dict, fallback: str | None) -> str | None:
    """OP.GG's most-played lane for the champion, read from positions[] by
    role_rate. Names come back UPPERCASE (JUNGLE), so lowercase before matching —
    the earlier case mismatch silently disabled this and fell back to the seed."""
    positions = (((data.get("data") or {}).get("summary") or {}).get("positions") or [])
    best, best_rate = None, -1.0
    for p in positions:
        name = (p.get("name") or "").lower()
        rate = ((p.get("stats") or {}).get("role_rate")) or 0.0
        if name in _POSITIONS and rate > best_rate:
            best, best_rate = name, rate
    return best or fallback


# Fields each card actually prints and therefore cannot render without. Keys are
# card kinds; values are keys of the normalized `stats` dict. Anything NOT listed
# here (ban_rate, kda, play) is optional and is omitted from the sentence when
# absent rather than printed as None.
_REQUIRED_STATS = {
    "stats": ("win_rate", "pick_rate", "tier", "rank"),
    "matchup": ("win_rate",),   # the no-head-to-head fallback quotes the overall rate
}


def _payload_is_empty(a: dict) -> bool:
    """True when the analysis came back with no usable content at all.

    The Garen incident looked exactly like this: `data.summary` absent, so every
    stat normalized to None and positions[] to [], and the lane silently fell back
    to the discovery seed (MID) as though it were a finding.
    """
    stats = a.get("stats") or {}
    build = a.get("build") or {}
    has_build = any((build.get(g) or {}).get("names") for g in
                    ("core_items", "boots", "starter_items")) \
        or bool((build.get("runes") or {}).get("primary")) or bool(build.get("skill_order"))
    return not (any(v is not None for v in stats.values())
                or a.get("roles") or a.get("weak_counters")
                or a.get("strong_counters") or has_build)


def _validate(a: dict, kind: str) -> None:
    """Gate a card on the fields it will render. Raises OpggIncomplete to degrade.

    Every formatter calls this first, so there is one place to answer "is this
    payload good enough to state as fact?" rather than a null check per f-string.
    An EMPTY-but-healthy result (no counter sample, no build sample) is NOT a
    failure — that is a real finding and those formatters say so explicitly. What
    this catches is the missing-data-rendered-as-data case.
    """
    display = a.get("display") or "this champion"
    if _payload_is_empty(a):
        raise OpggIncomplete(display, "the entire analysis payload (no summary)")

    missing = [k for k in _REQUIRED_STATS.get(kind, ())
               if (a.get("stats") or {}).get(k) is None]
    if missing:
        raise OpggIncomplete(display, "stat fields " + ", ".join(missing))

    # A lane claim must come from positions[], never from the discovery seed.
    if kind == "role" and not a.get("roles"):
        raise OpggIncomplete(display, "positions[] (no lane breakdown to report)")


def _normalize(champion_id: str, display: str, position: str, data: dict,
               position_source: str = "discovered") -> dict:
    d = data.get("data") or {}
    stats = (d.get("summary") or {}).get("average_stats") or {}
    win = (d.get("trends") or {}).get("win") or {}
    # Full lane breakdown from positions[] (role_rate = share of the champion's
    # games), sorted most-played first — powers role/lane answers.
    roles = []
    for p in ((d.get("summary") or {}).get("positions") or []):
        name = (p.get("name") or "").lower()
        if name in _POSITIONS:
            roles.append({"lane": name, "share": _pct((p.get("stats") or {}).get("role_rate"))})
    roles.sort(key=lambda r: -(r["share"] or 0))
    return {
        "display": display, "champion_id": champion_id, "position": position,
        # "explicit" (lane from the query) | "discovered" (won positions[]) |
        # "assumed" (seed fallback — NOT a finding, so cards must not assert it).
        "position_source": position_source,
        "patch": win.get("version"), "data_updated": win.get("created_at"),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source_url": _OPGG_PAGE.format(slug=_slug(champion_id)),
        "stats": {
            "win_rate": _pct(stats.get("win_rate")),
            "pick_rate": _pct(stats.get("pick_rate")),
            "ban_rate": _pct(stats.get("ban_rate")),
            "tier": stats.get("tier"), "rank": stats.get("rank"),
            "kda": stats.get("kda"), "play": stats.get("play"),
        },
        "roles": roles,
        "weak_counters": _normalize_counters(d.get("weak_counters")),
        "strong_counters": _normalize_counters(d.get("strong_counters")),
        "build": _normalize_build(d),
    }


def _item_group(g) -> dict:
    """One item slot group -> {names, pick_rate, play, win, win_rate}."""
    g = g or {}
    names = [n for n in (g.get("ids_names") or []) if isinstance(n, str)]
    play, win = g.get("play"), g.get("win")
    win_rate = round(100.0 * win / play, 1) if (play and win is not None) else None
    return {"names": names, "pick_rate": _pct(g.get("pick_rate")),
            "play": play, "win_rate": win_rate}


def _normalize_build(d: dict) -> dict:
    """Recommended build for the queried position: items, runes, skill order.

    Everything is optional — OP.GG omits groups with too small a sample, and a
    build card must degrade to whatever it actually has rather than inventing
    slots. Skill order is collapsed to the first-three max order (the way players
    state it: "Q first, then E") because the raw 15-entry list is noise.
    """
    runes = d.get("runes") or {}
    skills = d.get("skills") or {}
    order = [s for s in (skills.get("order") or []) if isinstance(s, str)]
    # "max order" = the distinct skills in the order they are first levelled,
    # ignoring R (every champion takes R on cooldown).
    max_order = []
    for s in order:
        if s not in max_order and s.upper() != "R":
            max_order.append(s)
    return {
        "core_items": _item_group(d.get("core_items")),
        "boots": _item_group(d.get("boots")),
        "starter_items": _item_group(d.get("starter_items")),
        "runes": {
            "primary_page": runes.get("primary_page_name"),
            "primary": [r for r in (runes.get("primary_rune_names") or []) if isinstance(r, str)],
            "secondary_page": runes.get("secondary_page_name"),
            "secondary": [r for r in (runes.get("secondary_rune_names") or []) if isinstance(r, str)],
            "pick_rate": _pct(runes.get("pick_rate")),
        },
        "skill_order": max_order[:3],
        "skill_pick_rate": _pct(skills.get("pick_rate")),
    }


def _parse_json(text: str) -> dict:
    """The matchup guide answers in plain JSON, not the positional repr."""
    val = json.loads(text)
    if not isinstance(val, dict):
        raise ValueError(f"unrecognised guide payload ({type(val).__name__})")
    return val


def _matchup_rows(rows, floor: int = MIN_MATCHUP_GAMES) -> tuple[list, list]:
    """`data.counters` -> (all rows, rows at or above the sample floor).

    The guide gives raw `play`/`win` only, so the rates are ours to compute:
    win/play is the champion's own rate and the opponent's is its complement
    (League has no draws — verified against the analysis tool, where
    my_win_rate + counter_win_rate is 1.0 on every row).

    Both lists are returned because they answer different questions. A "who
    counters X" LIST must respect the floor; a specific "X vs Y" head-to-head
    should still answer from a thin sample and say the sample is thin, rather
    than claim no data exists.
    """
    every = []
    for c in (rows or []):
        name, play, win = c.get("champion_name"), c.get("play"), c.get("win")
        if not name or not play or win is None:
            continue
        mine = round(100.0 * win / play, 1)
        every.append({"name": name, "my_win_rate": mine,
                      "counter_win_rate": round(100.0 - mine, 1), "play": play})
    return every, [c for c in every if c["play"] >= floor]


def _split_matchups(rows) -> tuple[list, list]:
    """Reportable rows -> (weak, strong), each sorted worst/best first.

    An exactly-even matchup belongs to neither side and is dropped from both;
    listing a 50.0% row as a champion someone is "strong against" would be a
    claim the number doesn't make.
    """
    weak = sorted((c for c in rows if c["my_win_rate"] < 50.0),
                  key=lambda c: c["my_win_rate"])
    strong = sorted((c for c in rows if c["my_win_rate"] > 50.0),
                    key=lambda c: -c["my_win_rate"])
    return weak, strong


def fetch_matchups(champion_id: str, position: str) -> dict:
    """The full matchup table for one champion+lane, floored and split."""
    champ = opgg_name(champion_id)
    data = _mgr.call_tool(
        _MATCHUP_GUIDE,
        {"my_champion": champ,
         "opponent_champion": champ if _SELF_OPPONENT else "GAREN",
         "position": position},
        timeout=GUIDE_TIMEOUT_S, parse=_parse_json)
    every, kept = _matchup_rows((data.get("data") or {}).get("counters"))
    weak, strong = _split_matchups(kept)
    return {"weak_counters": weak, "strong_counters": strong,
            "all_matchups": every, "matchup_total": len(every),
            "matchups_below_floor": len(every) - len(kept)}


def _normalize_counters(rows):
    """Matchup rows, with incomplete ones DROPPED rather than carried as None.

    Both counter formatters interpolate every one of these fields, so a row
    missing any of them would render as "Xerath (beats Garen None% ...)". A short
    list of real matchups beats a long list with holes in it.
    """
    out = []
    for c in (rows or []):
        row = {
            "name": c.get("champion_name"),
            "my_win_rate": _pct(c.get("my_win_rate")),
            "counter_win_rate": _pct(c.get("counter_win_rate")),
            "play": c.get("play"),
        }
        if all(row[k] is not None for k in ("name", "my_win_rate", "counter_win_rate", "play")):
            out.append(row)
    return out


def analyze(champion_id: str, display: str, want: str = "both",
            position: str | None = None) -> dict:
    """Live OP.GG analysis for one champion. `want`: 'stats' | 'counters' | 'both' | 'build'.

    Role: if `position` is given (an explicit lane from the query) it's used as-is;
    otherwise the lane is OP.GG's MOST-PLAYED position, read from positions[] — no
    static role map. Discovery seeds `_SEED_POSITION`; if the winning lane differs
    we re-query it so its counters/stats are correct. Raises OpggUnavailable.

    Matchups do NOT come from this tool. Anything that needs them takes the full
    table from `fetch_matchups` instead (see _MATCHUP_GUIDE) and the analysis
    tool's own three-row lists are overwritten."""
    champ = opgg_name(champion_id)
    wants_matchups = want in ("counters", "both")
    fields = list(_STATS_FIELDS)
    if want == "build":
        fields += _BUILD_FIELDS
    elif want != "stats":
        fields += _COUNTER_FIELDS
    t0 = time.perf_counter()

    def call(pos):
        return _mgr.call_tool(_ANALYSIS, {"game_mode": "ranked", "champion": champ,
                                          "position": pos, "desired_output_fields": fields})

    def fetch():
        """One full fetch (explicit lane, or discovery + re-query)."""
        if position:                   # explicit lane override — trust the query
            return position, "explicit", call(position)
        data = call(_SEED_POSITION)    # discover OP.GG's most-played lane
        found = _primary_position(data, None)
        # No positions[] means we did NOT learn the lane. Say so, rather than
        # letting the seed masquerade as the champion's real position.
        #
        # The re-query is skipped when the guide is about to run: the only
        # lane-dependent thing in this payload is the counter list, which the
        # guide replaces wholesale. `summary.average_stats` is champion-wide and
        # byte-identical whichever position is asked for (Master Yi returns
        # play=14848 from JUNGLE, TOP and the guide alike), so dropping the
        # re-query loses nothing and keeps the counters path at two calls total.
        if found and found != _SEED_POSITION and not wants_matchups:
            data = call(found)
        return (found or _SEED_POSITION,
                "discovered" if found else "assumed", data)

    def log_empty(stage: str, raw) -> None:
        """Record what we ACTUALLY received. Without this an empty payload is
        indistinguishable from 'the champion has no data', which is precisely the
        ambiguity that made the Garen case unfalsifiable from logs alone."""
        logger.warning(json.dumps({
            "event": "opgg_empty_payload", "stage": stage, "champion": champ,
            "want": want, "requested_position": position or _SEED_POSITION,
            "raw": json.dumps(raw, default=str)[:1500],
        }))

    pos, source, data = fetch()
    out = _normalize(champion_id, display, pos, data, position_source=source)

    if _payload_is_empty(out):
        log_empty("first", data)
        # An empty payload on a session that serves other champions fine points at
        # session-scoped staleness, not missing data — the same call on a fresh
        # session returns content. Rebuild the session and try once more before
        # concluding OP.GG has nothing. Only ever costs an already-failing request.
        if _mgr.reset():
            pos, source, data = fetch()
            out = _normalize(champion_id, display, pos, data, position_source=source)
            if _payload_is_empty(out):
                log_empty("after_session_reset", data)
            else:
                logger.info(json.dumps(
                    {"event": "opgg_recovered_after_reset", "champion": champ}))

    # Matchups: replace the analysis tool's three-row lists with the full table.
    # A failure here propagates rather than falling back to those three rows —
    # silently serving the thin list is how "no favourable matchups for Master Yi"
    # reached a user in the first place, and the caller has honest copy for a
    # live-stats failure.
    if wants_matchups and not _payload_is_empty(out):
        out.update(fetch_matchups(champion_id, pos))

    out["mcp_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    # Remember only champions that actually came back with content, so a later
    # timeout on one of them reads as "slow" rather than "warming up".
    if not _payload_is_empty(out):
        _SEEN_CHAMPIONS.add(champ)
    return out


def _lane_label(a: dict) -> str | None:
    """The lane, but only when we actually know it (explicit in the query, or won
    positions[]). Returns None for an assumed seed lane so callers can drop the
    claim instead of asserting a default."""
    if a.get("position_source") == "assumed" or not a.get("position"):
        return None
    return a["position"].upper()


def _scope(a: dict) -> str:
    """Parenthetical scope for card copy: "TOP, ranked" or just "ranked"."""
    lane = _lane_label(a)
    return f"{lane}, ranked" if lane else "ranked"


def _attrib(a: dict) -> str:
    """Attribution as it reads in PROSE: source and patch, nothing else.

    This string ends up in the generated answer, and it used to carry two machine
    timestamps ("data updated 2026-07-29T02:51:48+09:00; fetched 2026-07-31 22:18
    UTC") in the middle of a sentence a person is meant to read. The card keeps
    both values in its freshness line for anyone who wants them — `data_updated`
    and `fetched_at` are still on the analysis dict and still sent to the UI.
    """
    patch = f"patch {a['patch']}" if a.get("patch") else "current patch"
    return f"Source: OP.GG, {patch}."


def format_stats_card(a: dict) -> dict:
    _validate(a, "stats")
    s = a["stats"]
    # Only claim a lane we actually know. When positions[] was empty the lane is
    # the discovery seed, which is an implementation detail, not a fact about the
    # champion — so the card reports champion-wide figures without a lane.
    lane = _lane_label(a)
    scope = f" — {lane}" if lane else ""
    # Optional extras: omitted when OP.GG doesn't supply them (never "None").
    extras = []
    if s.get("ban_rate") is not None:
        extras.append(f"ban rate {s['ban_rate']}%")
    if s.get("kda") is not None:
        extras.append(f"KDA {s['kda']}")
    extra_txt = (", " + ", ".join(extras)) if extras else ""
    sample = f" Sample: {s['play']} games." if s.get("play") else ""
    body = (
        f"[LIVE STATS] {a['display']}{scope} (ranked). "
        f"Win rate {s['win_rate']}%, pick rate {s['pick_rate']}%{extra_txt}. "
        f"OP.GG tier {s['tier']} (ranked #{s['rank']} among champions).{sample} "
        f"Interpretation: these figures indicate {a['display']}'s current overall strength and "
        f"popularity (a higher win rate / better tier is statistically stronger); they reflect "
        f"overall performance, not specifically how easy the champion is to learn. {_attrib(a)}"
    )
    preview = (f"{a['display']} ({lane + ', ' if lane else ''}ranked) — win {s['win_rate']}% · "
               f"pick {s['pick_rate']}% · tier {s['tier']} (#{s['rank']})")
    return _card(body, preview, f"Live stats (OP.GG) — {a['display']}", a)


def format_role_card(a: dict) -> dict:
    # _validate raises when positions[] is empty. The old fallback here reported
    # the discovery seed as the champion's lane, which is how "Garen is primarily
    # played MID" reached a user.
    _validate(a, "role")
    roles = a["roles"]
    primary = roles[0]
    body = (f"[LIVE STATS] {a['display']} is primarily played "
            f"{primary['lane'].upper()} ({primary['share']}% of ranked games)")
    if len(roles) > 1:
        others = ", ".join(f"{r['lane'].upper()} ({r['share']}%)" for r in roles[1:])
        body += f", with some {others}"
    body += f". {_attrib(a)}"
    preview = f"{a['display']} role split — " + ", ".join(
        f"{r['lane'].upper()} {r['share']}%" for r in roles)
    return _card(body, preview, f"Live role (OP.GG) — {a['display']}", a)


def format_build_card(a: dict) -> dict:
    """The most-picked build for this champion+lane, from OP.GG's live aggregate.

    Framed as "what players are building", never as advice — same posture as the
    win-rate and counter cards. Each group carries its own pick rate so the
    reader can see how dominant the build actually is.
    """
    _validate(a, "build")
    b = a.get("build") or {}
    pos = _lane_label(a) or "ranked play"
    core, boots, start = b.get("core_items") or {}, b.get("boots") or {}, b.get("starter_items") or {}
    runes, order = b.get("runes") or {}, b.get("skill_order") or []

    parts, prev = [], []
    if core.get("names"):
        seg = "Core items: " + " → ".join(core["names"])
        bits = []
        if core.get("pick_rate") is not None:
            bits.append(f"{core['pick_rate']}% of games")
        if core.get("win_rate") is not None:
            bits.append(f"{core['win_rate']}% win rate")
        if core.get("play"):
            bits.append(f"{core['play']} games")
        seg += f" ({', '.join(bits)})" if bits else ""
        parts.append(seg)
        prev.append(" → ".join(core["names"]))
    if boots.get("names"):
        parts.append(f"Boots: {', '.join(boots['names'])}"
                     + (f" ({boots['pick_rate']}%)" if boots.get("pick_rate") is not None else ""))
        prev.append(f"boots {boots['names'][0]}")
    if start.get("names"):
        parts.append(f"Starting items: {', '.join(start['names'])}"
                     + (f" ({start['pick_rate']}%)" if start.get("pick_rate") is not None else ""))
    if runes.get("primary"):
        seg = f"Runes: {runes.get('primary_page') or 'primary'} — {', '.join(runes['primary'])}"
        if runes.get("secondary"):
            seg += f"; {runes.get('secondary_page') or 'secondary'} — {', '.join(runes['secondary'])}"
        if runes.get("pick_rate") is not None:
            seg += f" ({runes['pick_rate']}%)"
        parts.append(seg)
        prev.append(f"runes {runes.get('primary_page') or ''}".strip())
    if order:
        parts.append("Skill max order: " + " → ".join(order)
                     + (f" ({b['skill_pick_rate']}%)" if b.get("skill_pick_rate") is not None else ""))
        prev.append("skills " + "→".join(order))

    if not parts:
        body = (f"[LIVE STATS] OP.GG has no sufficient build sample for "
                f"{a['display']} ({pos}) at this time. {_attrib(a)}")
        preview = f"No build sample for {a['display']} ({pos})"
    else:
        body = (f"[LIVE STATS] Most-picked build for {a['display']} — {pos} (ranked). "
                + " ".join(p + "." for p in parts)
                + f" Interpretation: this is what players are most often building right now, "
                  f"aggregated across ranked games — a statistical tendency, not a "
                  f"prescription for any particular game. {_attrib(a)}")
        preview = f"{a['display']} ({pos}) build — " + " · ".join(prev)
    return _card(body, preview, f"Live build (OP.GG) — {a['display']}", a)


def _position_note(shown: int, available: int, a: dict, pos: str, kind: str,
                   continued: bool) -> str:
    """Where the printed rows sit inside the table they came from.

    The full table is 13-41 rows and we print five, so silence here would read as
    "these five are all there is" — the same over-claim, in a new place. Every
    clause is a count we actually hold: how many we printed, how many cleared the
    floor, how many didn't.
    """
    below = a.get("matchups_below_floor") or 0
    floor_txt = (f" {'Another' if below == 1 else 'A further'} {below} "
                 f"matchup{'s' if below != 1 else ''} had fewer than "
                 f"{MIN_MATCHUP_GAMES} games and {'was' if below == 1 else 'were'} "
                 f"left out as too small a sample to report." if below else "")
    if shown >= available:
        claim = (f"that is every {kind} OP.GG lists for {a['display']} ({pos}) "
                 f"at {MIN_MATCHUP_GAMES}+ games")
    else:
        # "the 5 strongest" is ambiguous on the weak side — strongest counters, or
        # the champion's strongest showings? Lead with the count instead; the
        # sentence above already states which way the list is ordered.
        claim = (f"OP.GG lists {available} {kind}{'s' if available != 1 else ''} for "
                 f"{a['display']} ({pos}) at {MIN_MATCHUP_GAMES}+ games; these are the "
                 f"{shown} most one-sided")
    sentence = f"Continuing the same list, {claim}" if continued \
        else claim[0].upper() + claim[1:]
    return f" {sentence}.{floor_txt}"


_SAMPLE_CAVEAT = (" Interpretation: each figure is the observed win rate over the "
                  "sample of ranked games shown beside it, not a skill-adjusted or "
                  "significance-tested rating — read the smaller samples loosely.")


def _drop_seen(rows, exclude) -> list:
    """Rows the previous answer didn't already name (see the `exclude` note on
    format_counters_card)."""
    seen = {str(n).strip().lower() for n in (exclude or ()) if n}
    return [c for c in rows if str(c["name"]).strip().lower() not in seen] if seen else list(rows)


def format_counters_card(a: dict, direction: str = "weak", exclude=None) -> dict:
    """Matchups for one champion+lane, worst-first or best-first.

    `exclude` carries the champions a previous answer already named, so "who else
    counters Yasuo" returns the NEXT rows instead of restating the same five. That
    only became possible with the full table behind it — the old three-row source
    had nothing left to give, so the follow-up repeated itself.
    """
    _validate(a, "counters")
    pos = _lane_label(a) or "ranked play"
    strong = direction == "strong"
    kind = "favourable matchup" if strong else "counter"
    pool = _drop_seen(a["strong_counters" if strong else "weak_counters"], exclude)
    rows = pool[:MATCHUP_ROWS]
    title = f"Live matchups (OP.GG) — {a['display']}" if strong \
        else f"Live counters (OP.GG) — {a['display']}"

    if not rows:
        total = a.get("matchup_total") or 0
        # Rows on THIS side before the floor was applied. Without this the two
        # reasons for an empty list collapse into one message: Azir (MID) has
        # matchups above the floor but not one he wins, which is a finding about
        # the champion, not about sample sizes.
        untrimmed = [c for c in (a.get("all_matchups") or ())
                     if (c["my_win_rate"] > 50.0) == strong and c["my_win_rate"] != 50.0]
        if exclude and (a["strong_counters"] if strong else a["weak_counters"]):
            # We had rows; the previous answer used them all. That is a real and
            # specific answer, not a data failure.
            body = (f"[LIVE STATS] OP.GG lists no further {kind}s for {a['display']} "
                    f"({pos}) beyond the ones already given. {_attrib(a)}")
            preview = f"No further {kind}s for {a['display']} ({pos})"
        elif untrimmed:
            body = (f"[LIVE STATS] OP.GG tracks {len(untrimmed)} {kind}"
                    f"{'s' if len(untrimmed) != 1 else ''} for {a['display']} ({pos}) but "
                    f"none reaches {MIN_MATCHUP_GAMES} games, so there is no sample worth "
                    f"reporting. {_attrib(a)}")
            preview = f"No {kind} reaches {MIN_MATCHUP_GAMES} games for {a['display']} ({pos})"
        elif total:
            side = "wins more than half its games against" if strong \
                else "loses more than half its games to"
            body = (f"[LIVE STATS] Across the {total} matchups OP.GG tracks for "
                    f"{a['display']} ({pos}), there is no champion {a['display']} "
                    f"{side}. {_attrib(a)}")
            preview = f"No {kind} for {a['display']} ({pos}) in {total} tracked matchups"
        else:
            body = (f"[LIVE STATS] OP.GG has no sufficient "
                    f"{'favorable-matchup' if strong else 'counter'} sample for "
                    f"{a['display']} ({pos}) at this time. {_attrib(a)}")
            preview = (f"No {'favorable-matchup' if strong else 'counter'} sample for "
                       f"{a['display']} ({pos})")
        return _card(body, preview, title, a)

    if strong:
        body_rows = "; ".join(
            f"{c['name']} ({a['display']} wins {c['my_win_rate']}%, {c['play']} games)"
            for c in rows)
        head = (f"[LIVE STATS] {a['display']}'s best-performing matchups ({pos}, ranked) "
                f"— the champions they win against most often: {body_rows}.")
        prev_rows = ", ".join(f"{c['name']} {c['my_win_rate']}%" for c in rows)
        prev_head = f"{a['display']} is strong against ({pos}) — {prev_rows}"
    else:
        body_rows = "; ".join(
            f"{c['name']} (beats {a['display']} {c['counter_win_rate']}% — {a['display']} wins "
            f"{c['my_win_rate']}%, {c['play']} games)" for c in rows)
        head = (f"[LIVE STATS] Counters to {a['display']} ({pos}, ranked) — the champions "
                f"{a['display']} loses to most often: {body_rows}.")
        prev_rows = ", ".join(f"{c['name']} {c['counter_win_rate']}%" for c in rows)
        prev_head = f"Counters to {a['display']} ({pos}) — {prev_rows}"

    body = (head
            + _position_note(len(rows), len(pool), a, pos, kind, bool(exclude))
            + _SAMPLE_CAVEAT + f" {_attrib(a)}")
    preview = prev_head + (f" ({len(rows)} of {len(pool)})" if len(pool) > len(rows) else "")
    return _card(body, preview, title, a)


def format_matchup_card(a: dict, opp_display: str) -> dict:
    # "matchup" requires stats.win_rate: the no-head-to-head branch below quotes
    # the champion's overall rate, so a partial payload would print "None%".
    _validate(a, "matchup")
    pos = _lane_label(a) or "ranked play"
    row = None
    # Search the UNFLOORED table. A specific head-to-head question deserves the
    # thin number plus a warning about it, rather than "no data" about a matchup
    # OP.GG demonstrably tracks — the floor exists to keep noisy rows out of
    # top-5 LISTS, which is a different job.
    for c in (a.get("all_matchups") or (a["weak_counters"] + a["strong_counters"])):
        if c["name"] and opp_display.lower() in c["name"].lower():
            row = c
            break
    if row:
        thin = ("" if row["play"] >= MIN_MATCHUP_GAMES else
                f" Note the small sample: {row['play']} games is too few to read as a "
                f"reliable matchup rate.")
        body = (f"[LIVE STATS] Matchup {a['display']} vs {row['name']} "
                f"({pos}, ranked): {a['display']} wins {row['my_win_rate']}%, "
                f"{row['name']} wins {row['counter_win_rate']}% ({row['play']} games)."
                f"{thin} {_attrib(a)}")
        preview = (f"{a['display']} vs {row['name']} ({pos}) — {a['display']} {row['my_win_rate']}% / "
                   f"{row['name']} {row['counter_win_rate']}%")
    else:
        # Empty matchup cell: answer SPECIFICALLY (never a generic refusal), with a
        # cheap fallback to A's own win rate (already fetched — no extra MCP call).
        s = a["stats"]
        body = (f"[LIVE STATS] OP.GG has no direct head-to-head matchup data for "
                f"{a['display']} vs {opp_display} at this time (they are not in each other's "
                f"top tracked matchups). State that plainly. For reference, {a['display']}'s "
                f"overall win rate is {s['win_rate']}% ({pos}, ranked). {_attrib(a)}")
        preview = (f"No OP.GG matchup data for {a['display']} vs {opp_display} — "
                   f"{a['display']} overall {s['win_rate']}% ({pos})")
    return _card(body, preview, f"Live matchup (OP.GG) — {a['display']} vs {opp_display}", a)


def _card(body: str, preview: str, heading: str, a: dict) -> dict:
    # `content` carries the [LIVE STATS] scaffolding for generation; `preview` is
    # clean human copy for the UI source card (no internal tags).
    return {"content": body, "preview": preview, "heading_path": heading,
            "source_url": a["source_url"], "patch": a.get("patch"),
            "fetched_at": a["fetched_at"], "kind": "live_stats", "mcp_ms": a.get("mcp_ms")}


# Champions warmed at API startup so common first queries land in the ~2.5s
# (server-cached) regime instead of ~7s cold. Try-chip champions + most-played.
# NB: pre-warm only triggers OP.GG's server-side compute; NOTHING is stored here.
PREWARM = ["Yasuo", "Zed", "Garen", "Jinx", "Ashe", "Lux", "Ezreal", "Thresh",
           "Leona", "Caitlyn", "MasterYi", "Renekton"]


def prewarm() -> None:
    """Best-effort: fire one analysis per PREWARM champion to warm OP.GG's cache.
    Runs on a background thread at startup; failures are swallowed."""
    ok = 0
    for cid in PREWARM:
        try:
            analyze(cid, cid, "both")
            ok += 1
        except Exception:  # noqa: BLE001
            pass
    logger.info(json.dumps({"event": "opgg_prewarm", "warmed": ok, "total": len(PREWARM)}))
