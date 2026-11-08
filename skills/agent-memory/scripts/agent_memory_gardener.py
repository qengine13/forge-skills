#!/usr/bin/env python3
"""agent-memory gardener — operator console & memory-health dashboard.

A localhost web dashboard for the agent-memory ledger: a memory-health overview,
episodes browser by project, the supersession graph, a drift timeline, dream/HEAL
run-dir inboxes, doctrine-audit findings, and keyword search. Pure Python stdlib
(http.server), with CSS/inline-SVG charts.

BEHAVIOUR CONTRACT (see the public verification scope):
  - GET is read-only browse. The ONLY mutation is POST /heal/apply, which
    delegates to the audited agent_memory_heal.apply_heal_plan (reviewed=True,
    explicit proposal-ids required — "apply all" refused, refuse-on-drift,
    append-only provenance). The gardener itself contains NO raw SQL and keeps
    NO UI-local copy of canonical state: Postgres stays the single
    source of truth, re-read per request.
  - Localhost only (binds 127.0.0.1). main() REFUSES non-loopback binds unless
    --allow-nonlocal: the console is unauthenticated and Postgres is local-trust,
    so a network bind would expose a write-capable window into the whole ledger.
"""

# Public edition adapted from the owner's Agent Memory server; see NOTICE.
from __future__ import annotations
from memory_paths import memory_path

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_memory_postgres import default_dsn, fetch_episodes  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MEMORY_HOME = Path(memory_path('')).expanduser()
RUN_ROOTS = {
    "dream": MEMORY_HOME / "dream-runs",
    "heal": MEMORY_HOME / "heal-runs",
    "doctrine-audit": MEMORY_HOME / "doctrine-audit-runs",
}
AUDIT_SLUG = "_audit-doctrine"


# --------------------------------------------------------------------------- #
# Pure data-shaping helpers (no I/O)
# --------------------------------------------------------------------------- #
def slug_counts(episodes: list[dict[str, object]]) -> list[tuple[str, int]]:
    """Project slugs with episode counts, descending."""
    counts: dict[str, int] = {}
    for ep in episodes:
        slug = str(ep.get("project_slug", "") or "(none)")
        counts[slug] = counts.get(slug, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def supersession_edges(episodes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Derive directed supersession edges (older -> newer) from episodes whose
    `supersedes[]` is non-empty. Reason is read from the `supersedes-reason:` tag."""
    by_id = {str(ep.get("episode_id", "")): ep for ep in episodes}
    edges: list[dict[str, object]] = []
    for ep in episodes:
        newer_id = str(ep.get("episode_id", ""))
        for older_id in ep.get("supersedes", []) or []:
            reason = next(
                (t.split(":", 1)[1] for t in ep.get("tags", []) or []
                 if isinstance(t, str) and t.startswith("supersedes-reason:")),
                "(untyped)",
            )
            older = by_id.get(str(older_id))
            edges.append({
                "newer_id": newer_id,
                "newer_summary": str(ep.get("summary", ""))[:160],
                "newer_created_at_ms": int(ep.get("created_at_ms", 0) or 0),
                "older_id": str(older_id),
                "older_summary": (str(older.get("summary", ""))[:160] if older else "(older episode not in corpus)"),
                "reason": reason,
            })
    return edges


def audit_findings_by_gate(episodes: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    """Group `_audit-doctrine` episodes by gate x status, from their tags."""
    grouped: dict[str, dict[str, int]] = {}
    for ep in episodes:
        if str(ep.get("project_slug", "")) != AUDIT_SLUG:
            continue
        tags = [t for t in ep.get("tags", []) or [] if isinstance(t, str)]
        gate = next((t.split(":", 1)[1] for t in tags if t.startswith("gate:")), "(no gate)")
        status = next((t.split(":", 1)[1] for t in tags if t.startswith("status:")), "(no status)")
        grouped.setdefault(gate, {})
        grouped[gate][status] = grouped[gate].get(status, 0) + 1
    return grouped


def keyword_search(episodes: list[dict[str, object]], query: str) -> list[dict[str, object]]:
    """Read-only keyword filter over canonical episodes (summary + tags),
    case-insensitive. NOT the embedding search: deliberately avoids Ollama/GPU."""
    q = query.strip().lower()
    if not q:
        return []
    hits = []
    for ep in episodes:
        haystack = str(ep.get("summary", "")).lower() + " " + " ".join(
            str(t).lower() for t in ep.get("tags", []) or []
        )
        if q in haystack:
            hits.append(ep)
    return hits


def scan_run_dirs(roots: dict[str, Path]) -> list[dict[str, object]]:
    """Scan dream/HEAL/audit run-dirs for plan JSON. Read-only; missing roots
    degrade gracefully to nothing."""
    runs: list[dict[str, object]] = []
    plan_names = ("heal-plan.json", "plan.json")
    for kind, root in roots.items():
        if not root.exists():
            continue
        for run_dir in sorted(root.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            plan_path = next((run_dir / n for n in plan_names if (run_dir / n).exists()), None)
            if plan_path is None:
                continue
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            summary = plan.get("summary", {}) if isinstance(plan, dict) else {}
            runs.append({
                "kind": kind,
                "run_id": run_dir.name,
                "ts_ms": _run_id_to_ms(run_dir.name),
                "action_counts": summary.get("action_counts", {}),
                "supersession_confidence": summary.get("supersession_confidence", {}),
                "review_required": plan.get("review_required", "?"),
                "semantic_unavailable_reason": plan.get("semantic_unavailable_reason"),
            })
    return runs


def health_metrics(episodes: list[dict[str, object]], runs: list[dict[str, object]]) -> dict[str, object]:
    """Derive memory-health signals for the dashboard (pure). Computes only what's
    derivable from the ledger today; signals needing recall-counts/failed-writes
    are out of scope (labeled in the UI), not faked."""
    edges = supersession_edges(episodes)
    reasons = Counter(str(e["reason"]) for e in edges)
    # Thin-provenance: an episode asserting a fact with no decisions, files, or verification.
    thin = [e for e in episodes
            if not (e.get("decisions") or e.get("files_touched") or e.get("verification"))]
    # Top topical tags (skip namespaced tags like gate:/status:/supersedes-reason:).
    tag_counts = Counter(
        t for e in episodes for t in (e.get("tags") or [])
        if isinstance(t, str) and ":" not in t
    )
    times = [int(e.get("created_at_ms", 0) or 0) for e in episodes if e.get("created_at_ms")]
    return {
        "total_episodes": len(episodes),
        "total_projects": len({str(e.get("project_slug", "")) for e in episodes}),
        "total_edges": len(edges),
        "total_runs": len(runs),
        "thin_provenance": len(thin),
        "reasons": reasons.most_common(),
        "top_tags": tag_counts.most_common(12),
        "newest_ms": max(times) if times else 0,
        "oldest_ms": min(times) if times else 0,
    }


def timeline_events(episodes: list[dict[str, object]], runs: list[dict[str, object]],
                    limit: int = 60) -> list[dict[str, object]]:
    """Merge supersession applies (by newer episode time) + run-dir events into a
    single drift timeline, newest first."""
    events: list[dict[str, object]] = []
    for ed in supersession_edges(episodes):
        events.append({
            "ts_ms": int(ed["newer_created_at_ms"]),
            "kind": "supersession",
            "label": f"{ed['reason']}",
            "detail": str(ed["newer_summary"]),
        })
    for r in runs:
        counts = r.get("action_counts") or {}
        events.append({
            "ts_ms": int(r.get("ts_ms", 0) or 0),
            "kind": f"{r['kind']}-run",
            "label": str(r["run_id"]),
            "detail": json.dumps(counts) if counts else "(no actions)",
        })
    events.sort(key=lambda e: e["ts_ms"], reverse=True)
    return events[:limit]


def _run_id_to_ms(run_id: str) -> int:
    """Parse a run directory named YYYYMMDD-HHMMSS to epoch milliseconds.

    Run-ids are stamped with `time.strftime` in LOCAL time by the heal/dream CLIs,
    so we parse as naive-local and let .timestamp() convert to epoch — matching the
    UTC epoch-ms that episodes carry (a forced-UTC parse would misorder the
    timeline by the local offset)."""
    try:
        dt = datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")  # naive = local wall-clock
        return int(dt.timestamp() * 1000)
    except (ValueError, IndexError):
        return 0


# --------------------------------------------------------------------------- #
# Pure HTML render helpers (data -> str)
# --------------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#0b0e14;--bg2:#0f131c;--panel:#151a24;--panel2:#1a2030;--border:#262d3d;
  --fg:#e6ebf4;--muted:#8a93a6;--faint:#5b6377;
  --accent:#6ea8fe;--accent2:#a78bfa;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:linear-gradient(160deg,#0b0e14,#0d1019 60%,#0b0e14);color:var(--fg);
  font:14px/1.55 var(--sans);display:flex;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{color:#9cc4ff}
code,.mono{font-family:var(--mono)}
/* sidebar */
aside{width:228px;flex:0 0 228px;background:var(--bg2);border-right:1px solid var(--border);
  padding:20px 14px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column}
.brand{font-weight:700;font-size:16px;letter-spacing:.2px;padding:4px 10px 18px;display:flex;align-items:center;gap:8px}
.brand .leaf{font-size:20px}
nav.side a{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:9px;color:var(--muted);
  font-weight:500;margin-bottom:2px}
nav.side a:hover{background:var(--panel);color:var(--fg)}
nav.side a.active{background:linear-gradient(90deg,rgba(110,168,254,.18),rgba(167,139,250,.10));
  color:#fff;box-shadow:inset 0 0 0 1px var(--border)}
nav.side a .ic{width:18px;text-align:center;opacity:.9}
.sidefoot{margin-top:auto;font-size:11px;color:var(--faint);padding:12px 10px;line-height:1.5}
.robadge{display:inline-block;color:var(--ok);border:1px solid rgba(74,222,128,.4);
  border-radius:6px;padding:1px 8px;font-size:11px;font-weight:600;letter-spacing:.4px}
/* main */
main{flex:1;min-width:0;padding:26px 34px;max-width:1180px}
.h{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:18px}
h1{font-size:22px;margin:0;font-weight:700;letter-spacing:-.01em}
h2{font-size:12px;color:var(--muted);margin:26px 0 10px;text-transform:uppercase;letter-spacing:.09em;font-weight:600}
.sub{color:var(--muted);font-size:13px}
/* search box top-right */
form.search{position:relative}
form.search input{background:var(--panel);border:1px solid var(--border);color:var(--fg);
  padding:8px 12px 8px 32px;border-radius:10px;width:260px;font:13px var(--sans)}
form.search input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(110,168,254,.15)}
form.search .mag{position:absolute;left:11px;top:8px;color:var(--faint)}
/* stat tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
.tile{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 18px;position:relative;overflow:hidden}
.tile:before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--accent);opacity:.8}
.tile.v2:before{background:var(--accent2)}.tile.ok:before{background:var(--ok)}.tile.warn:before{background:var(--warn)}
.tile .n{font-size:28px;font-weight:700;letter-spacing:-.02em}
.tile .l{color:var(--muted);font-size:12px;margin-top:2px}
/* cards */
.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:16px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}aside{display:none}}
/* bar chart */
.bar{display:flex;align-items:center;gap:10px;margin:7px 0}
.bar .lab{width:170px;flex:0 0 170px;color:var(--muted);font-size:12px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .track{flex:1;background:var(--panel2);border-radius:6px;height:16px;overflow:hidden}
.bar .fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:6px}
.bar .val{width:38px;text-align:right;color:var(--fg);font-size:12px;font-variant-numeric:tabular-nums}
/* tables */
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--border);padding:8px 12px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
tbody tr:hover{background:var(--panel2)}
/* chips + pills */
.tag{display:inline-block;background:var(--panel2);border:1px solid var(--border);border-radius:20px;
  padding:1px 10px;margin:2px 3px 2px 0;font-size:12px;color:var(--muted)}
.pill{border-radius:6px;padding:1px 8px;font-size:11px;font-weight:600;font-family:var(--mono)}
.explicit{background:rgba(74,222,128,.15);color:var(--ok)}
.low_review{background:rgba(251,191,36,.15);color:var(--warn)}
.heuristic{background:var(--panel2);color:var(--fg)}
.kindtag{font-family:var(--mono);font-size:11px;color:var(--accent2);background:rgba(167,139,250,.12);border-radius:6px;padding:1px 8px}
.muted{color:var(--muted)}.faint{color:var(--faint)}
/* timeline */
.tl{list-style:none;margin:0;padding:0;position:relative}
.tl:before{content:"";position:absolute;left:120px;top:4px;bottom:4px;width:1px;background:var(--border)}
.tl li{display:flex;gap:16px;padding:8px 0;position:relative}
.tl .when{width:104px;flex:0 0 104px;text-align:right;color:var(--faint);font-size:12px;font-family:var(--mono)}
.tl .dot{position:absolute;left:117px;top:13px;width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px var(--bg)}
.tl .body{padding-left:24px;min-width:0}
.tl .body .d{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.summary{font-size:15px;line-height:1.6}
.btn{background:linear-gradient(90deg,var(--accent),var(--accent2));color:#0b0e14;border:0;
  border-radius:9px;padding:9px 18px;font:600 13px var(--sans);cursor:pointer}
.btn:hover{filter:brightness(1.08)}
input[type=checkbox]{width:15px;height:15px;accent-color:var(--accent)}
"""

_NAV = [
    ("/", "Overview", "◎"),
    ("/projects", "Projects", "▤"),
    ("/supersessions", "Supersessions", "⇄"),
    ("/timeline", "Timeline", "◷"),
    ("/runs", "Runs", "▷"),
    ("/audit", "Audit", "✓"),
    ("/heal", "HEAL", "✚"),
]


def _e(value: object) -> str:
    return html.escape(str(value))


def _page(title: str, body: str, active: str = "/") -> str:
    nav_parts = []
    for p, label, ic in _NAV:
        cls = "active" if p == active else ""
        nav_parts.append(
            f"<a href='{p}' class='{cls}'><span class='ic'>{ic}</span>{_e(label)}</a>"
        )
    nav = "".join(nav_parts)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)} · gardener</title><style>{_CSS}</style></head><body>"
        f"<aside><div class='brand'><span class='leaf'>🌱</span> gardener</div>"
        f"<nav class='side'>{nav}</nav>"
        "<div class='sidefoot'><span class='robadge'>READ-ONLY</span><br><br>"
        "agent-memory ledger<br>localhost · single-user</div></aside>"
        f"<main>{body}</main></body></html>"
    )


def _topbar(title: str, sub: str = "") -> str:
    return (
        f"<div class='h'><div><h1>{_e(title)}</h1>"
        f"{f'<div class=sub>{_e(sub)}</div>' if sub else ''}</div>"
        "<form class='search' action='/search' method='get'><span class='mag'>⌕</span>"
        "<input type='text' name='q' placeholder='search summaries & tags…' autocomplete='off'></form></div>"
    )


def _bars(rows: list[tuple[str, int]], cls: str = "") -> str:
    if not rows:
        return "<div class='muted'>none</div>"
    top = max(v for _, v in rows) or 1
    out = ""
    for label, val in rows:
        pct = max(3, round(100 * val / top))
        out += (f"<div class='bar'><div class='lab'>{_e(label)}</div>"
                f"<div class='track'><div class='fill' style='width:{pct}%'></div></div>"
                f"<div class='val'>{val}</div></div>")
    return out


def _tile(n: object, label: str, cls: str = "") -> str:
    return f"<div class='tile {cls}'><div class='n'>{_e(n)}</div><div class='l'>{_e(label)}</div></div>"


def render_overview(episodes: list[dict[str, object]], runs: list[dict[str, object]]) -> str:
    m = health_metrics(episodes, runs)
    counts = slug_counts(episodes)
    tiles = (
        _tile(m["total_episodes"], "episodes")
        + _tile(m["total_projects"], "projects", "v2")
        + _tile(m["total_edges"], "supersession edges", "ok")
        + _tile(m["total_runs"], "run-dirs", "v2")
        + _tile(m["thin_provenance"], "thin-provenance facts", "warn")
    )
    reason_bars = _bars([(r, n) for r, n in m["reasons"]])
    proj_bars = _bars([(s, n) for s, n in counts[:10]])
    tag_chips = "".join(f"<span class='tag'>{_e(t)} · {n}</span>" for t, n in m["top_tags"])
    span = (f"{_fmt_ts(m['oldest_ms'])} → {_fmt_ts(m['newest_ms'])}"
            if m["newest_ms"] else "—")
    body = (
        _topbar("Memory health", f"ledger span {span}")
        + f"<div class='tiles'>{tiles}</div>"
        + "<div class='grid2'>"
        + f"<div class='card'><h2>Supersessions by reason</h2>{reason_bars}</div>"
        + f"<div class='card'><h2>Top projects</h2>{proj_bars}"
          "<div class='sub' style='margin-top:10px'><a href='/projects'>all projects →</a></div></div>"
        + "</div>"
        + f"<div class='card'><h2>Top tags</h2>{tag_chips or '<span class=muted>none</span>'}</div>"
    )
    return _page("Overview", body, "/")


def render_projects(episodes: list[dict[str, object]]) -> str:
    counts = slug_counts(episodes)
    rows = "".join(
        f"<tr><td><a href='/slug?name={_e(s)}'>{_e(s)}</a></td><td class='mono'>{n}</td></tr>"
        for s, n in counts
    )
    body = _topbar("Projects", f"{len(counts)} projects") + \
        f"<div class='card'><table><thead><tr><th>slug</th><th>episodes</th></tr></thead><tbody>{rows}</tbody></table></div>"
    return _page("Projects", body, "/projects")


def render_slug(slug: str, episodes: list[dict[str, object]]) -> str:
    eps = [e for e in episodes if str(e.get("project_slug", "")) == slug]
    eps.sort(key=lambda e: int(e.get("created_at_ms", 0) or 0), reverse=True)
    rows = "".join(
        f"<tr><td class='faint mono' style='white-space:nowrap'>{_fmt_ts(e.get('created_at_ms'))}</td>"
        f"<td><a href='/episode?id={_e(e.get('episode_id'))}'>{_e(str(e.get('summary',''))[:130])}</a></td></tr>"
        for e in eps
    )
    body = _topbar(slug, f"{len(eps)} episodes") + \
        f"<div class='card'><table><thead><tr><th>when</th><th>summary</th></tr></thead><tbody>{rows}</tbody></table></div>"
    return _page(slug, body, "/projects")


def render_episode(ep: dict[str, object] | None) -> str:
    if ep is None:
        return _page("Not found", _topbar("Not found") + "<div class='card'>Episode not found.</div>")
    tags = "".join(f"<span class='tag'>{_e(t)}</span>" for t in ep.get("tags", []) or [])
    sup = "".join(f"<li><a href='/episode?id={_e(s)}'>{_e(s)}</a></li>" for s in ep.get("supersedes", []) or [])
    dec = "".join(f"<li>{_e(d)}</li>" for d in ep.get("decisions", []) or [])
    files = "".join(f"<li class='mono'>{_e(f)}</li>" for f in ep.get("files_touched", []) or [])
    nexts = "".join(f"<li>{_e(n)}</li>" for n in ep.get("next_steps", []) or [])
    body = (
        _topbar("Episode")
        + "<div class='card'>"
        + f"<div class='muted' style='margin-bottom:8px'><a href='/slug?name={_e(ep.get('project_slug'))}'>{_e(ep.get('project_slug'))}</a>"
          f" · {_fmt_ts(ep.get('created_at_ms'))} · <span class='mono faint'>{_e(ep.get('episode_id'))}</span></div>"
        + f"<div class='summary'>{_e(ep.get('summary',''))}</div>"
        + (f"<div style='margin-top:12px'>{tags}</div>" if tags else "")
        + "</div>"
        + (f"<div class='card'><h2>supersedes</h2><ul>{sup}</ul></div>" if sup else "")
        + (f"<div class='card'><h2>decisions</h2><ul>{dec}</ul></div>" if dec else "")
        + (f"<div class='card'><h2>next steps</h2><ul>{nexts}</ul></div>" if nexts else "")
        + (f"<div class='card'><h2>files touched</h2><ul>{files}</ul></div>" if files else "")
    )
    return _page("Episode", body, "/projects")


def render_supersessions(episodes: list[dict[str, object]]) -> str:
    edges = supersession_edges(episodes)
    edges.sort(key=lambda e: e["newer_created_at_ms"], reverse=True)
    by_reason = Counter(str(e["reason"]) for e in edges)
    rows = "".join(
        f"<tr><td><span class='pill { _reason_cls(ed['reason']) }'>{_e(ed['reason'])}</span></td>"
        f"<td><a href='/episode?id={_e(ed['older_id'])}'>{_e(ed['older_summary'])}</a></td>"
        f"<td><a href='/episode?id={_e(ed['newer_id'])}'>{_e(ed['newer_summary'])}</a></td></tr>"
        for ed in edges
    )
    body = (
        _topbar("Supersession graph", f"{len(edges)} edges (older → newer)")
        + f"<div class='card'><h2>By reason</h2>{_bars(by_reason.most_common())}</div>"
        + f"<div class='card'><table><thead><tr><th>reason</th><th>superseded (older)</th>"
          f"<th>supersedes (newer)</th></tr></thead><tbody>{rows}</tbody></table></div>"
    )
    return _page("Supersessions", body, "/supersessions")


def render_timeline(episodes: list[dict[str, object]], runs: list[dict[str, object]]) -> str:
    events = timeline_events(episodes, runs)
    items = "".join(
        f"<li><div class='when'>{_fmt_ts(ev['ts_ms'])}</div><span class='dot'></span>"
        f"<div class='body'><span class='kindtag'>{_e(ev['kind'])}</span> "
        f"<span class='pill { _reason_cls(ev['label']) }'>{_e(ev['label'])}</span>"
        f"<div class='d'>{_e(ev['detail'])}</div></div></li>"
        for ev in events
    )
    body = _topbar("Drift timeline", f"{len(events)} recent events") + \
        (f"<div class='card'><ul class='tl'>{items}</ul></div>" if items
         else "<div class='card muted'>No timeline events yet.</div>")
    return _page("Timeline", body, "/timeline")


def render_runs(runs: list[dict[str, object]]) -> str:
    if not runs:
        return _page("Runs", _topbar("Runs") + "<div class='card muted'>No dream/HEAL/audit "
                     "run-dirs under ~/.forge-agent-memory/. They appear after plan runs.</div>", "/runs")
    rows = ""
    for r in runs:
        conf = r.get("supersession_confidence") or {}
        conf_html = "".join(f"<span class='pill {_e(k)}'>{_e(k)}·{_e(v)}</span> " for k, v in conf.items())
        rows += (
            f"<tr><td><span class='kindtag'>{_e(r['kind'])}</span></td>"
            f"<td class='mono faint'>{_e(r['run_id'])}</td>"
            f"<td class='mono'>{_e(json.dumps(r.get('action_counts', {})))}</td>"
            f"<td>{conf_html}</td><td>{_e(r.get('review_required'))}</td></tr>"
        )
    body = _topbar("Run-dir inbox", f"{len(runs)} runs") + \
        (f"<div class='card'><table><thead><tr><th>kind</th><th>run-id</th><th>actions</th>"
         f"<th>confidence</th><th>review?</th></tr></thead><tbody>{rows}</tbody></table></div>")
    return _page("Runs", body, "/runs")


def render_audit(episodes: list[dict[str, object]]) -> str:
    grouped = audit_findings_by_gate(episodes)
    if not grouped:
        return _page("Audit", _topbar("Doctrine audit") + "<div class='card muted'>No _audit-doctrine findings.</div>", "/audit")
    rows = ""
    for gate in sorted(grouped):
        for status, n in sorted(grouped[gate].items()):
            rows += f"<tr><td class='mono'>{_e(gate)}</td><td>{_e(status)}</td><td class='mono'>{n}</td></tr>"
    body = _topbar("Doctrine audit", "findings by gate × status") + \
        f"<div class='card'><table><thead><tr><th>gate</th><th>status</th><th>count</th></tr></thead><tbody>{rows}</tbody></table></div>"
    return _page("Audit", body, "/audit")


def render_search(query: str, hits: list[dict[str, object]]) -> str:
    rows = "".join(
        f"<tr><td class='muted'>{_e(h.get('project_slug'))}</td>"
        f"<td><a href='/episode?id={_e(h.get('episode_id'))}'>{_e(str(h.get('summary',''))[:130])}</a></td></tr>"
        for h in hits
    )
    note = ("<div class='sub' style='margin-bottom:12px'>Keyword filter over summary + tags "
            "(not the embedding search — keeps the console off the GPU).</div>")
    body = _topbar(f"Search “{query}”", f"{len(hits)} hits") + note + \
        (f"<div class='card'><table><tbody>{rows}</tbody></table></div>" if hits
         else "<div class='card muted'>No matches.</div>")
    return _page("Search", body, "/")


def latest_heal_plan(roots: dict[str, Path]) -> tuple[Path | None, dict[str, object] | None]:
    """Find the newest heal run-dir containing a heal-plan.json. Read-only."""
    heal_root = roots.get("heal")
    if not heal_root or not heal_root.exists():
        return None, None
    for run_dir in sorted(heal_root.iterdir(), reverse=True):
        plan_path = run_dir / "heal-plan.json"
        if plan_path.exists():
            try:
                return plan_path, json.loads(plan_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None, None


def _proposal_row(action: dict[str, object]) -> str:
    conf = str(action.get("confidence", "heuristic"))
    ev = action.get("evidence", {}) if isinstance(action.get("evidence"), dict) else {}
    summaries = ev.get("summaries", ["", ""]) if isinstance(ev, dict) else ["", ""]
    newer = str(summaries[0] if len(summaries) > 0 else "")[:150]
    older = str(summaries[1] if len(summaries) > 1 else "")[:150]
    fid = str(action.get("finding_id", ""))
    # Pre-check explicit (author-declared); leave heuristic/low_review for the operator to opt in.
    checked = "checked" if conf == "explicit" else ""
    return (
        f"<tr><td><input type='checkbox' name='proposal_id' value='{_e(fid)}' {checked}></td>"
        f"<td><span class='pill {conf}'>{_e(conf)}</span></td>"
        f"<td><span class='pill { _reason_cls(action.get('reason')) }'>{_e(action.get('reason'))}</span></td>"
        f"<td><a href='/episode?id={_e(action.get('older_episode_id'))}'>{_e(older)}</a></td>"
        f"<td><a href='/episode?id={_e(action.get('newer_episode_id'))}'>{_e(newer)}</a></td></tr>"
    )


def render_heal(plan_path: Path | None, plan: dict[str, object] | None) -> str:
    """HEAL review surface — proposals from the latest plan, grouped by confidence,
    with an apply form (POST /heal/apply). Read until the operator submits."""
    if plan is None or plan_path is None:
        body = _topbar("HEAL review", "no plan found") + (
            "<div class='card'><p>No HEAL plan in <code>~/.forge-agent-memory/heal-runs/</code> yet.</p>"
            "<p class='muted'>Generate one (read-only): <code>python3 scripts/agent_memory_heal.py plan "
            "--project-slug &lt;slug&gt; --run-dir ~/.forge-agent-memory/heal-runs/$(date +%Y%m%d-%H%M%S)</code></p></div>"
        )
        return _page("HEAL", body, "/heal")
    proposals = [a for a in plan.get("actions", []) if a.get("kind") == "propose_supersession"]
    conf_counts = Counter(str(a.get("confidence", "heuristic")) for a in proposals)
    rows = "".join(_proposal_row(a) for a in sorted(
        proposals, key=lambda a: {"explicit": 0, "heuristic": 1, "low_review": 2}.get(str(a.get("confidence")), 3)))
    pills = " ".join(f"<span class='pill {_e(k)}'>{_e(k)}·{_e(v)}</span>" for k, v in conf_counts.items())
    body = (
        _topbar("HEAL review & apply", f"{len(proposals)} proposals · {_e(plan_path.parent.name)}")
        + f"<div class='card'>{pills}<div class='sub' style='margin-top:8px'>Selecting a proposal IS the review. "
          "Apply writes a reversible <code>supersedes[]</code> edge to Postgres via the audited apply path "
          "(refuse-on-drift, append-only provenance). <b>explicit</b> tier is pre-checked; "
          "<b>low_review</b> (many-to-one) is left for you to opt into.</div></div>"
        + "<form method='post' action='/heal/apply'>"
        + f"<input type='hidden' name='plan' value='{_e(plan_path)}'>"
        + "<div class='card'><table><thead><tr><th></th><th>confidence</th><th>reason</th>"
          "<th>superseded (older)</th><th>supersedes (newer)</th></tr></thead>"
          f"<tbody>{rows}</tbody></table>"
        + "<div style='margin-top:14px'><button type='submit' class='btn'>Apply selected →</button></div></div></form>"
    )
    return _page("HEAL", body, "/heal")


def latest_adjudication_manifest(plan_path: Path | None) -> dict[str, object] | None:
    """Load the adjudication-manifest.json next to the latest plan, if present (read-only)."""
    if plan_path is None:
        return None
    mpath = plan_path.parent / "adjudication-manifest.json"
    if not mpath.exists():
        return None
    try:
        return json.loads(mpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _adj_rows(items: list[dict[str, object]]) -> str:
    return "".join(
        f"<tr><td><span class='pill { _reason_cls(it.get('verdict')) }'>{_e(it.get('verdict'))}"
        f"·{_e(it.get('confidence'))}</span></td><td>{_e(it.get('explanation'))}</td></tr>"
        for it in items
    )


def render_adjudicated(manifest: dict[str, object]) -> str:
    """The 'surface only doubt' view: what the adjudicator auto-healed, auto-discarded,
    and the handful it wants your eyes on — each with a plain-English reason."""
    counts = manifest.get("counts", {}) if isinstance(manifest.get("counts"), dict) else {}
    surface = manifest.get("surface", []) or []
    auto = manifest.get("auto_apply", []) or []
    disc = manifest.get("discard", []) or []
    dry = manifest.get("dry_run", True)
    mode = ("<span class='pill low_review'>DRY-RUN — nothing written yet</span>"
            if dry else "<span class='pill explicit'>APPLIED</span>")
    tiles = (
        _tile(len(auto), "auto-healed" if not dry else "would auto-heal", "ok")
        + _tile(len(disc), "auto-discarded (independent)", "v2")
        + _tile(len(surface), "needs your eyes", "warn")
    )
    surfaced_block = (
        f"<div class='card'><h2>Needs your eyes ({len(surface)})</h2>"
        + (f"<table><tbody>{_adj_rows(surface)}</tbody></table>"
           if surface else "<div class='muted'>Nothing ambiguous — the adjudicator was confident on every proposal.</div>")
        + "</div>"
    )
    body = (
        _topbar("HEAL — adjudicated", f"model {_e(manifest.get('model','host-configured'))} · {mode}")
        + f"<div class='tiles'>{tiles}</div>"
        + surfaced_block
        + f"<div class='card'><h2>Auto-healed ({len(auto)})</h2><table><tbody>{_adj_rows(auto)}</tbody></table></div>"
        + f"<div class='card'><h2>Auto-discarded — independent ({len(disc)})</h2><table><tbody>{_adj_rows(disc)}</tbody></table></div>"
        + ("<div class='card muted'>This was a dry-run preview. Re-run with "
           "<code>agent_memory_heal.py adjudicate --plan &lt;plan&gt; --apply</code> to write the "
           "auto-heal bucket (reversible · provenance-logged).</div>" if dry else "")
    )
    return _page("HEAL", body, "/heal")


def render_apply_result(result: dict[str, object]) -> str:
    applied = result.get("applied_updates", 0)
    skipped = result.get("skipped_actions", []) or []
    skip_rows = "".join(
        f"<tr><td class='mono faint'>{_e(s.get('finding_id',''))[:18]}</td><td>{_e(s.get('reason'))}</td></tr>"
        for s in skipped if isinstance(s, dict)
    )
    body = (
        _topbar("Apply result", f"{applied} supersession edge(s) written")
        + f"<div class='card'><div class='n' style='font-size:32px;color:var(--ok)'>{_e(applied)}</div>"
          "<div class='l'>edges written to Postgres (reversible · provenance appended)</div></div>"
        + (f"<div class='card'><h2>Skipped ({len(skip_rows and skipped or [])})</h2><table><tbody>{skip_rows}</tbody></table></div>" if skip_rows else "")
        + "<div class='card'><a href='/heal'>← back to HEAL review</a> · <a href='/supersessions'>see the graph →</a></div>"
    )
    return _page("Applied", body, "/heal")


def _validate_heal_plan_path(plan_path: str) -> Path | None:
    """Resolve a posted plan path and require it to be an existing `heal-plan.json`
    UNDER the configured heal-runs root (a tampered POST must not be able
    to apply an arbitrary local HEAL-shaped file outside the review surface)."""
    if not plan_path:
        return None
    heal_root = RUN_ROOTS["heal"].resolve()
    try:
        resolved = Path(plan_path).resolve()
    except (OSError, RuntimeError):
        return None
    if resolved.name != "heal-plan.json" or not resolved.exists():
        return None
    if heal_root not in resolved.parents:
        return None
    return resolved


def apply_action(form: dict[str, list[str]], dsn: str) -> tuple[int, str]:
    """The ONLY mutation surface. Delegates to the audited agent_memory_heal.apply_heal_plan
    (reviewed=True, explicit proposal_ids required, refuse-on-drift, append-only provenance).
    The gardener itself writes no SQL — Postgres stays the single source of truth."""
    import agent_memory_heal  # local import: only the apply path needs it
    plan_path = _validate_heal_plan_path((form.get("plan") or [""])[0])
    proposal_ids = [p for p in form.get("proposal_id", []) if p]
    if plan_path is None:
        return 400, _page("Apply refused", _topbar("Apply refused")
                          + "<div class='card'>Plan path must be an existing heal-plan.json under "
                            "~/.forge-agent-memory/heal-runs/. Refusing.</div>", "/heal")
    try:
        result = agent_memory_heal.apply_heal_plan(
            plan_path=Path(plan_path),
            reviewed=True,                 # selecting in the UI is the review
            proposal_ids=proposal_ids,     # empty -> apply_heal_plan refuses "apply all"
            dsn=dsn,
            write_postgres=True,
        )
    except agent_memory_heal.AgentMemoryHealError as exc:
        return 400, _page("Apply refused", _topbar("Apply refused")
                          + f"<div class='card'>Refused: {_e(exc)}</div>"
                            "<div class='card muted'>Select at least one proposal — "
                            "“apply all” is intentionally refused.</div>", "/heal")
    return 200, render_apply_result(result)


def _reason_cls(reason: object) -> str:
    r = str(reason)
    if r in ("explicit",):
        return "explicit"
    if r in ("low_review",):
        return "low_review"
    return "heuristic"


def _fmt_ts(ms: object) -> str:
    try:
        v = int(ms)
        if v <= 0:
            return "—"
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------- #
# GET-only HTTP handler (no write methods exist -> read-only by construction)
# --------------------------------------------------------------------------- #
def route(path: str, params: dict[str, list[str]], dsn: str) -> tuple[int, str]:
    """Map a GET path to (status, html). Pure dispatch over read-only helpers."""
    episodes = fetch_episodes(dsn, None, None)
    if path == "/":
        return 200, render_overview(episodes, scan_run_dirs(RUN_ROOTS))
    if path == "/projects":
        return 200, render_projects(episodes)
    if path == "/slug":
        return 200, render_slug((params.get("name") or [""])[0], episodes)
    if path == "/episode":
        eid = (params.get("id") or [""])[0]
        ep = next((e for e in episodes if str(e.get("episode_id")) == eid), None)
        return 200, render_episode(ep)
    if path == "/supersessions":
        return 200, render_supersessions(episodes)
    if path == "/timeline":
        return 200, render_timeline(episodes, scan_run_dirs(RUN_ROOTS))
    if path == "/runs":
        return 200, render_runs(scan_run_dirs(RUN_ROOTS))
    if path == "/audit":
        return 200, render_audit(episodes)
    if path == "/heal":
        plan_path, plan = latest_heal_plan(RUN_ROOTS)
        manifest = latest_adjudication_manifest(plan_path)
        if manifest is not None:
            return 200, render_adjudicated(manifest)
        return 200, render_heal(plan_path, plan)
    if path == "/search":
        q = (params.get("q") or [""])[0]
        return 200, render_search(q, keyword_search(episodes, q))
    return 404, _page("Not found", _topbar("Not found") + "<div class='card'>No such page.</div>")


def post_route(path: str, form: dict[str, list[str]], dsn: str) -> tuple[int, str]:
    """The ONLY mutating dispatch. The single write surface is /heal/apply, which
    delegates to the audited apply_heal_plan. Every other POST is rejected — there
    is no other way to mutate the ledger from the UI."""
    if path == "/heal/apply":
        return apply_action(form, dsn)
    return 404, _page("Not found", _topbar("Not found") + "<div class='card'>No such action.</div>")


def make_handler(dsn: str) -> type[BaseHTTPRequestHandler]:
    class GardenerHandler(BaseHTTPRequestHandler):
        # GET = read-only browse. POST = exactly one route (/heal/apply) that
        # delegates to the audited apply_heal_plan. No PUT/DELETE/PATCH exist.
        def _respond(self, status: int, page: str) -> None:
            payload = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                status, page = route(parsed.path, parse_qs(parsed.query), dsn)
            except Exception as exc:
                status, page = 500, _page("Error", _topbar("Error") + f"<div class='card'>Error: {_e(exc)}</div>")
            self._respond(status, page)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not _request_origin_ok(self.headers.get("Origin", ""), self.headers.get("Referer", "")):
                self._respond(403, _page("Blocked", _topbar("Blocked")
                              + "<div class='card'>Cross-site POST rejected (CSRF guard).</div>"))
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            form = parse_qs(body)
            try:
                status, page = post_route(parsed.path, form, dsn)
            except Exception as exc:
                status, page = 500, _page("Error", _topbar("Error") + f"<div class='card'>Error: {_e(exc)}</div>")
            self._respond(status, page)

        def log_message(self, *args: object) -> None:
            pass

    return GardenerHandler


def serve(host: str, port: int, dsn: str) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(dsn))
    print(f"agent-memory gardener (read-only) → http://{host}:{port}  (Ctrl-C to stop)")
    print("Localhost-only by design — do NOT expose to a network (Postgres is local-trust).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only operator console + memory-health dashboard for agent-memory.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host (default 127.0.0.1; do not change without auth)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: AGENT_MEMORY_POSTGRES_DSN or local)")
    parser.add_argument("--self-check", action="store_true",
                        help="Render every page once against the live ledger and exit (no server).")
    parser.add_argument("--allow-nonlocal", action="store_true",
                        help="Permit binding a non-loopback host. UNSAFE: the console is "
                             "unauthenticated and Postgres is local-trust.")
    return parser


def _is_loopback(host: str) -> bool:
    """True for loopback binds; non-loopback exposes the unauthenticated ledger."""
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def _request_origin_ok(origin: str, referer: str) -> bool:
    """CSRF guard for the POST write route: a cross-site form submit
    to http://127.0.0.1:PORT/heal/apply would otherwise mutate the ledger. Modern
    browsers send Origin on cross-site POSTs, so reject any present Origin/Referer
    whose host is non-loopback. Absent headers (same-origin form, curl) are allowed."""
    for value in (origin, referer):
        if not value:
            continue
        host = urlparse(value).hostname or ""
        if not _is_loopback(host):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dsn = args.dsn or default_dsn()
    if args.self_check:
        for path in ("/", "/projects", "/supersessions", "/timeline", "/runs", "/audit", "/heal", "/search"):
            status, page = route(path, {"q": ["heal"]} if path == "/search" else {}, dsn)
            print(f"  {path:16s} -> {status}  ({len(page)} bytes)")
        return 0
    if not args.allow_nonlocal and not _is_loopback(args.host):
        print(
            f"refusing to bind non-loopback host {args.host!r}: the gardener is an "
            "unauthenticated read window into the whole ledger and Postgres is local-trust. "
            "Pass --allow-nonlocal only if you have put your own authentication in front of it.",
            file=sys.stderr,
        )
        return 2
    serve(args.host, args.port, dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
