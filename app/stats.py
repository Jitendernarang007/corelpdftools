"""Usage counting for the private /stats page.

Only events are recorded (what happened, when, which CorelDRAW preset, page count,
failure reason). No files, file names, IP addresses or other personal data.
"""

from __future__ import annotations

import html
import json
import os
import secrets
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("STATS_TZ", "Asia/Kolkata"))
_lock = threading.Lock()
_events_file: Path | None = None


def init(data_dir: Path) -> None:
    global _events_file
    _events_file = data_dir / "events.jsonl"


def log_event(kind: str, **fields) -> None:
    """kind: fix_ok | fix_fail | limit_hit | feedback"""
    if _events_file is None:
        return
    row = {"t": int(time.time()), "kind": kind}
    row.update({k: v for k, v in fields.items() if v is not None})
    try:
        with _lock, open(_events_file, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass  # stats must never break the tool


def _read_events(days: int) -> list[dict]:
    if _events_file is None or not _events_file.exists():
        return []
    cutoff = time.time() - days * 86400
    out = []
    with open(_events_file) as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("t", 0) >= cutoff:
                out.append(e)
    return out


# ---------- password ----------
def stats_password() -> str | None:
    pw = os.getenv("STATS_PASSWORD")
    if pw:
        return pw
    path = Path(os.getenv("STATS_PASSWORD_FILE", "/etc/corelpdftools/stats-password"))
    try:
        pw = path.read_text().strip()
        return pw or None
    except OSError:
        return None


def check_password(given: str) -> bool:
    pw = stats_password()
    return bool(pw) and secrets.compare_digest(given.encode(), pw.encode())


# ---------- page ----------
def render(days: int = 30) -> str:
    events = _read_events(days)
    today = datetime.now(TZ).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    per_day: dict[str, Counter] = defaultdict(Counter)
    presets, reasons = Counter(), Counter()
    pages_total = 0
    for e in events:
        d = datetime.fromtimestamp(e["t"], TZ).date().isoformat()
        k = e.get("kind")
        if k == "feedback":
            per_day[d]["opened_yes" if e.get("opened") else "opened_no"] += 1
        else:
            per_day[d][k] += 1
        if k == "fix_ok":
            presets[e.get("preset", "?")] += 1
            pages_total += int(e.get("pages", 0))
        if k == "fix_fail":
            reasons[e.get("reason", "other")] += 1

    tot = Counter()
    for c in per_day.values():
        tot.update(c)
    fb = tot["opened_yes"] + tot["opened_no"]
    open_rate = f"{round(100 * tot['opened_yes'] / fb)}%" if fb else "–"
    tried = tot["fix_ok"] + tot["fix_fail"]
    success = f"{round(100 * tot['fix_ok'] / tried)}%" if tried else "–"

    def tile(label, value, note=""):
        return (f"<div class='tile'><div class='v'>{value}</div><div class='l'>{html.escape(label)}</div>"
                f"<div class='n'>{html.escape(note)}</div></div>")

    rows = []
    max_ok = max([per_day[d]["fix_ok"] for d in day_keys] + [1])
    for d in day_keys:
        c = per_day[d]
        bar = int(120 * c["fix_ok"] / max_ok)
        rows.append(
            f"<tr><td>{d}</td><td class='num'>{c['fix_ok']}</td>"
            f"<td><span class='bar' style='width:{bar}px'></span></td>"
            f"<td class='num'>{c['fix_fail']}</td><td class='num'>{c['limit_hit']}</td>"
            f"<td class='num'>{c['opened_yes']}</td><td class='num'>{c['opened_no']}</td></tr>")

    def small_table(title, counter, labels=None):
        if not counter:
            return f"<h2>{title}</h2><p class='muted'>Nothing yet.</p>"
        items = "".join(
            f"<tr><td>{html.escape((labels or {}).get(k, k))}</td><td class='num'>{v}</td></tr>"
            for k, v in counter.most_common())
        return f"<h2>{title}</h2><table class='small'>{items}</table>"

    preset_labels = {"x4-x7": "X4 to X7", "2017-2020": "2017 to 2020", "2021+": "2021 or newer"}
    reason_labels = {"not_pdf": "Not a PDF", "password": "Password-protected", "damaged": "Too damaged to read",
                     "too_many_pages": "Too many pages", "too_big": "File too big", "empty": "Empty file",
                     "timeout": "Took too long", "rebuild_failed": "Ghostscript couldn't rebuild it",
                     "server_error": "Server error", "other": "Other"}
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Stats | CorelPDFtools</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:30px auto;padding:0 18px;color:#16181d;background:#f7f8fa}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px}} .muted{{color:#5b6170}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}}
.tile{{background:#fff;border:1px solid #e4e6eb;border-radius:12px;padding:14px}}
.v{{font-size:28px;font-weight:700}} .l{{font-weight:600}} .n{{color:#5b6170;font-size:13px}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e4e6eb;border-radius:12px;overflow:hidden}}
th,td{{padding:7px 10px;border-bottom:1px solid #eef0f3;text-align:left}} th{{font-size:13px;color:#5b6170;font-weight:600}}
.num{{text-align:right;font-variant-numeric:tabular-nums}} table.small{{max-width:420px}}
.bar{{display:inline-block;height:10px;background:#e3262e;border-radius:5px;vertical-align:middle}}
</style></head><body>
<h1>CorelPDFtools usage</h1>
<div class="muted">Last {days} days · India time · visitors are in GoatCounter, Google searches in Search Console</div>
<div class="tiles">
{tile("PDFs fixed", tot["fix_ok"], f"{pages_total} pages")}
{tile("Success rate", success, f"{tot['fix_fail']} failed")}
{tile("Opened in CorelDRAW", open_rate, f"{fb} answer{'' if fb == 1 else 's'}")}
{tile("Hit free limit", tot["limit_hit"], "times someone wanted more")}
</div>
<h2>By day</h2>
<table><tr><th>Date</th><th class="num">Fixed</th><th></th><th class="num">Failed</th><th class="num">Hit limit</th>
<th class="num">Opened ✓</th><th class="num">Still error</th></tr>{''.join(rows)}</table>
{small_table("CorelDRAW version picked", presets, preset_labels)}
{small_table("Why fixes failed", reasons, reason_labels)}
</body></html>"""
