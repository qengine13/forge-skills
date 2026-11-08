#!/usr/bin/env python3
"""Recall cross-project lessons using keywords and optional semantic similarity.

The helper returns no lessons when its optional backend is unavailable. It can
be integrated into an operator-managed hook; this package installs no hook.
"""

# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DSN = os.environ.get("AGENT_MEMORY_POSTGRES_DSN", "postgresql:///forge_agent_memory")
EMBED_URL = os.environ.get("AGENT_MEMORY_EMBED_URL", "http://localhost:11434/api/embed")
EMBED_MODEL = os.environ.get("AGENT_MEMORY_EMBED_MODEL", "nomic-embed-text")

# Bound optional embedding latency; unavailable embeddings fall back to keywords.
EMBED_TIMEOUT_S = 1.5

# Semantic-only matches require a higher score than corroborated keyword matches.
MIN_SCORE = 0.34
MIN_SCORE_SEMANTIC_ONLY = 0.47
DEFAULT_K = 3

# Score produced by exactly one rare single-word trigger and nothing else.
LONE_RARE_SCORE = 0.66

# Maximum number of lessons sharing a trigger for that trigger to count as rare.
RARE_TRIGGER_MAX_LESSONS = 4
# Below this semantic similarity, a lone-rare-trigger match is a homonym.
HOMONYM_FLOOR = 0.15

# Trigger tokens too generic to carry meaning on their own. These are topic
# labels, not triggers -- matching on them fires a lesson at everyone.
STOP_TRIGGERS = {
    "python", "gate", "stale", "threshold", "test", "tests", "testing", "ci",
    "code review", "review", "git", "database", "db", "api", "cli", "schema",
    "data", "error", "debug", "config", "deploy", "build", "install", "search",
}


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------

def _psql(sql: str) -> list[list[str]]:
    """Run a query through psql. Returns rows of raw string fields."""
    proc = subprocess.run(
        ["psql", DSN, "-tAF", "\x1f", "-c", sql],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return [ln.split("\x1f") for ln in proc.stdout.strip().split("\n") if ln]


def load_lessons() -> list[dict]:
    """Every live (non-superseded) lesson, with its vector unpacked."""
    rows = _psql(
        "SELECT lesson_id, lesson, category, triggers::text, cost, volatility, "
        "source_project, encode(embedding,'hex'), "
        "extract(epoch from (now()-verified_at))/86400 "
        "FROM agent_memory.lessons WHERE superseded_by IS NULL"
    )
    out = []
    for r in rows:
        if len(r) < 9:
            continue
        raw = r[7]
        vec = list(struct.unpack(f"<{len(raw)//8}f", bytes.fromhex(raw))) if raw else []
        out.append({
            "id": r[0], "lesson": r[1], "category": r[2],
            "triggers": [t for t in json.loads(r[3] or "[]")],
            "cost": r[4], "volatility": r[5], "project": r[6],
            "vec": vec, "age_days": float(r[8] or 0),
        })
    return out


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def trigger_matches(query: str, trigger: str) -> bool:
    """Does `trigger` occur in `query` as a whole token?

    Substring matching can fire inside a longer, unrelated word,
    so both ends are anchored. The trailing anchor allows an optional version
    suffix, because a package is typed as often with its major version as
    without -- the trigger 'examplelib' has to match 'examplelib5' and
    'examplelib-5' while still refusing 'examplelibrary'.
    """
    return re.search(
        r"(?<!\w)" + re.escape(trigger) + r"(?:[-._]?\d+)?(?!\w)", query
    ) is not None


def keyword_score(query: str, triggers: list[str], rarity: dict[str, int]) -> float:
    """Trigger matching weighted by how specific each trigger is.

    Rare non-dictionary tokens can match alone. Common words and topic labels
    require corroboration. Rarity is measured against the supplied lessons;
    it is a matching heuristic, not proof of relevance.
    """
    q = query.lower()
    multi = common = 0
    rare_hit = False
    for t in triggers:
        t = t.lower().strip()
        if not t or t in STOP_TRIGGERS or not trigger_matches(q, t):
            continue
        if " " in t or "-" in t:
            multi += 1
        elif rarity.get(t, 1) <= RARE_TRIGGER_MAX_LESSONS and t not in english_words():
            rare_hit = True
        else:
            common += 1
    if multi:
        return min(1.0, 0.62 + 0.19 * multi + 0.06 * (common + rare_hit))
    if rare_hit:
        return min(1.0, 0.66 + 0.10 * common)
    if common >= 2:
        return min(1.0, 0.40 + 0.12 * common)
    return 0.0


_DICT_PATHS = ("/usr/share/dict/words", "/usr/dict/words")
_ENGLISH: set[str] | None = None

# Ordinary words that also read as technical terms. The system word list is the
# real source, but minimal container images ship none -- and an empty word list
# silently switches the homonym guard off, restoring the exact false positives it
# exists to stop. This fallback keeps the guard working with no OS support: it
# covers the collision-prone vocabulary rather than the whole language, since a
# trigger has to be a plausible technical term to be in the corpus at all.
_FALLBACK_ENGLISH = frozenset("""
account action address agent alert alias anchor append archive argument array
attribute audit author bar base batch bind block board body book branch break
bridge budget buffer build bundle button cache calendar call canvas capture card
cart case cell chain channel chart check child chunk claim class clean clear
client clock close cloud cluster code collection column command comment commit
compare compile complete component composer condition config connect console
constant contact container content context control cookie copy core count cover
crash create credit cursor cycle data date deal debug default delete deploy
design desktop detail device diff digest directory dispatch display document
domain draft drain draw drive driver drop dump duration edge edit editor element
email embed engine entry environment error escape event exception exchange
execute exit expand export express extension factor feature feed field file
filter finding fire flag flat float flow folder font fork form format frame
function gate gateway generate global grade grain grant graph grid group guard
guide haiku handle hash head header health history home hook host image import index
input insert inspect install instance integer interface issue item join journal
key label language layer layout leaf ledger legend length letter level library
license link list load lock log login mail major manager map mark market master
match member memory menu merge message meta method metric migrate mirror mode
model module monitor month name native network node normal note notice object
offer offset open operation option order origin output package page panel parent
parse partition patch path pattern payment period permission phase pin pipe
pipeline pitch place plan platform plugin point policy pool port position post
power preview price primary print priority probe process product profile program
project prompt property protocol provider proxy public publish pull purchase
query queue quote range rate read record redirect reference refresh region
register release remote remove render repeat replace report request reserve
reset resource response result return review revision role root route router
rule run sample save scale scale schedule schema scope screen script search
second secret section secure select sequence series server service session set
shadow share sheet shell shift ship signal simple single site size slice slot
snapshot socket sort source space span split sponsor spring stack stage stamp
standard star start state static status step stock storage store stream string
strip structure style subject submit subscribe suite summary support surface
switch symbol sync system table tag target task team template term terminal test
text theme thread ticket time timer title token tool topic total trace track
trade traffic transaction transfer tree trigger trust type unit update upload
usage user value variable vendor version view volume wait watch web window word
work worker wrap write yield zone
""".split())


def english_words() -> set[str]:
    """Ordinary English vocabulary, used to spot triggers that are homonyms.

    A word can have both an ordinary and a technical meaning. Dictionary
    tokens require a second matching signal to reduce ambiguous matches.

    The built-in fallback is always unioned in, so a host with no system word
    list degrades to a smaller guard rather than to no guard at all.
    """
    global _ENGLISH
    if _ENGLISH is None:
        words = set(_FALLBACK_ENGLISH)
        for p in _DICT_PATHS:
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    words |= {w.strip().lower() for w in fh if len(w.strip()) > 2}
                break
            except OSError:
                continue
        _ENGLISH = words
    return _ENGLISH


def trigger_rarity(lessons: list[dict]) -> dict[str, int]:
    """How many lessons claim each trigger. Low count == high specificity."""
    counts: dict[str, int] = {}
    for L in lessons:
        for t in L["triggers"]:
            t = t.lower().strip()
            counts[t] = counts.get(t, 0) + 1
    return counts


def embed(text: str) -> list[float] | None:
    """Embed the query. Returns None on any failure -- never raises."""
    try:
        req = urllib.request.Request(
            EMBED_URL,
            data=json.dumps({"model": EMBED_MODEL, "input": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_S) as resp:
            payload = json.load(resp)
        # A 200 carrying an unexpected shape must degrade like any other failure.
        # The body is not assumed to be an object at all: a proxy or a wedged
        # server can return `[]`, `null`, or a bare string with a 200, and calling
        # .get() on those raises AttributeError straight through a hook that is
        # documented never to raise.
        if not isinstance(payload, dict):
            return None
        vecs = payload.get("embeddings")
        # `vecs[0]` must be a non-empty list: an empty vector satisfies every
        # type check, scores 0.0 against everything, and would masquerade as a
        # working embedding instead of signalling failure.
        if (not isinstance(vecs, list) or not vecs
                or not isinstance(vecs[0], list) or not vecs[0]):
            return None
        return vecs[0]
    except (urllib.error.URLError, OSError, AttributeError, KeyError,
            ValueError, TypeError, IndexError, TimeoutError):
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    return dot / (math.sqrt(na) * math.sqrt(nb)) if na and nb else 0.0


# Heuristic rescaling bounds; validate these on your own embedding model and notes.
_SEM_FLOOR = 0.42
_SEM_CEIL = 0.82


def _rescale(sim: float) -> float:
    return max(0.0, min(1.0, (sim - _SEM_FLOOR) / (_SEM_CEIL - _SEM_FLOOR)))


def recall(query: str, k: int = DEFAULT_K, lessons: list[dict] | None = None) -> list[dict]:
    """Return at most k lessons relevant to `query`, best first.

    Keyword and semantic scores are fused by taking the stronger signal and
    letting the weaker one top it up. Keyword hits are precise but brittle
    (an exact package name); semantic hits are fuzzy but catch intent that
    shares no vocabulary with the lesson.
    """
    if not query or not query.strip():
        return []
    try:
        lessons = lessons if lessons is not None else load_lessons()
    except Exception:
        return []

    rarity = trigger_rarity(lessons)
    qvec = embed(query)
    scored = []
    for L in lessons:
        kw = keyword_score(query, L["triggers"], rarity)
        sem = _rescale(cosine(qvec, L["vec"])) if qvec else 0.0
        # Require semantic corroboration for a lone rare token. Without an
        # embedding, suppress that ambiguous match rather than inject a lesson
        # whose relevance is supported by only one token.
        if kw and kw <= LONE_RARE_SCORE and (not qvec or sem < HOMONYM_FLOOR):
            continue
        score = max(kw, sem) + 0.30 * min(kw, sem)
        floor = MIN_SCORE if kw else MIN_SCORE_SEMANTIC_ONLY
        if score >= floor:
            scored.append((score, kw, sem, L))

    scored.sort(key=lambda t: -t[0])
    out = []
    for score, kw, sem, L in scored[:k]:
        note = None
        # A vendor limit or a CLI flag recorded months ago may simply be wrong now.
        # Say so inline rather than presenting it with equal confidence.
        if L["volatility"] != "stable" and L["age_days"] > 120:
            note = f"unverified for {int(L['age_days'])} days -- re-check before relying on it"
        out.append({
            "id": L["id"], "lesson": L["lesson"], "cost": L["cost"],
            "category": L["category"], "project": L["project"],
            "score": round(score, 3), "keyword": round(kw, 2), "semantic": round(sem, 2),
            "note": note,
        })
    return out


def recall_keyword(
    query: str,
    k: int = DEFAULT_K,
    lessons: list[dict] | None = None,
) -> list[dict]:
    """Return precise lesson matches without calling the local embedding model.

    This is the deterministic path for interactive MCP searches. It preserves
    the trigger rarity and ordinary-word guards used by the hook while avoiding
    an embedding round-trip during a user-requested memory lookup.
    """
    if not query or not query.strip():
        return []
    try:
        lessons = lessons if lessons is not None else load_lessons()
    except Exception:
        return []

    rarity = trigger_rarity(lessons)
    scored = []
    for lesson in lessons:
        score = keyword_score(query, lesson["triggers"], rarity)
        if score:
            scored.append((score, lesson))
    scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))

    out = []
    for score, lesson in scored[:k]:
        note = None
        if lesson["volatility"] != "stable" and lesson["age_days"] > 120:
            note = (
                f"unverified for {int(lesson['age_days'])} days -- "
                "re-check before relying on it"
            )
        out.append(
            {
                "id": lesson["id"],
                "lesson": lesson["lesson"],
                "cost": lesson["cost"],
                "category": lesson["category"],
                "project": lesson["project"],
                "score": round(score, 3),
                "keyword": round(score, 2),
                "semantic": 0.0,
                "note": note,
            }
        )
    return out


def format_for_hook(hits: list[dict]) -> str:
    """Render for injection. Returns '' when there is nothing worth saying."""
    if not hits:
        return ""
    lines = ["PRIOR ART — lessons already paid for on other projects:"]
    for h in hits:
        lines.append(f"  * {h['lesson']}")
        lines.append(f"    cost: {h['cost']}  [{h['project']}]")
        if h["note"]:
            lines.append(f"    NOTE: {h['note']}")
    lines.append("  These are recorded observations, not doctrine. Verify before relying on one.")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recall lessons from the configured database.")
    parser.add_argument("query", nargs="+", help="Words describing the task")
    args = parser.parse_args()
    print(format_for_hook(recall(" ".join(args.query))) or "(no lessons matched)")
