#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate docs/topic-queue.html -- the Ascend Topic Queue dashboard.

A ranked queue of 'topic' tickets (children of ASC-9433) joined with the live
delivery status of the work each topic points at. Mirrors the Claude-artifact
version of the same dashboard; this one is served by GitHub Pages and refreshed
by the update-topic-queue GitHub Action (or by running this script directly).

Usage:
  python generator/gen_topic_queue.py             # pull Jira, write docs/topic-queue.html
  python generator/gen_topic_queue.py --selftest  # offline unit tests, no network

Auth (same as pull_jira.py): export JIRA_EMAIL + JIRA_API_TOKEN, or drop
generator/jira_creds.json = {"email": "...", "token": "..."} (gitignored).

Data model per topic row:
  rank        position in `parent = ASC-9433 ORDER BY Rank ASC` (1-based)
  next step   first description paragraph mentioning "next step" (else first paragraph)
  delivery    first linked issue: status / due / assignee, plus for container types
              (hierarchyLevel > 0) an active/queued/on-hold breakdown of its open
              children via portfolioChildIssuesOf and the most recent child update
  health      On hold > Stalled (0 active or >30d quiet) > Slowing (8-30d) > Moving;
              unlinked topics are Plan ready (has next-step text) or Needs definition
"""
import base64
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_FILE = os.path.join(ROOT, "generator", "jira_creds.json")
OUT_FILE = os.path.join(ROOT, "docs", "topic-queue.html")

SITE = "https://trekbikes.atlassian.net"
PARENT_KEY = "ASC-9433"
BOARD_URL = SITE + "/jira/software/c/projects/ASC/boards/6316"
QUEUE_JQL_URL = (SITE + "/issues?jql=" + urllib.parse.quote(
    'parent = %s AND statusCategory != Done ORDER BY rank ASC' % PARENT_KEY))
WORKFLOW_URL = ("https://github.com/thomas-reid-801/digital-workflow-monitor-sdms"
                "/actions/workflows/update-topic-queue.yml")
MAX_PAGES = 30  # safety backstop per query (30 * 100 = 3000 issues)

# ---------------------------------------------------------------------------
# auth + search (network)
# ---------------------------------------------------------------------------

def load_creds():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if email and token:
        return email, token
    if os.path.exists(CREDS_FILE):
        with open(CREDS_FILE, encoding="utf-8") as f:
            c = json.load(f)
        return c["email"], c["token"]
    sys.exit("No Jira credentials: set JIRA_EMAIL + JIRA_API_TOKEN "
             "or create generator/jira_creds.json")


def search(jql, fields, auth):
    """All matching issues via the enhanced-search cursor (no totals to trust)."""
    email, token = auth
    basic = base64.b64encode(("%s:%s" % (email, token)).encode()).decode()
    issues, token_param = [], None
    for page in range(MAX_PAGES):
        params = {"jql": jql, "fields": ",".join(fields), "maxResults": "100"}
        if token_param:
            params["nextPageToken"] = token_param
        req = urllib.request.Request(
            SITE + "/rest/api/3/search/jql?" + urllib.parse.urlencode(params),
            headers={"Authorization": "Basic " + basic, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        batch = data.get("issues", [])
        issues.extend(batch)
        token_param = data.get("nextPageToken")
        print("  [%s] page %d: %d issues%s" % (
            jql[:40], page + 1, len(batch), "" if token_param else " [last]"))
        if not token_param or not batch:
            return issues
    raise RuntimeError("MAX_PAGES exceeded for jql: " + jql)

# ---------------------------------------------------------------------------
# pure helpers (exercised by --selftest, no network)
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(s or "", quote=True)


def adf_inline_html(node):
    """Inline HTML for an ADF node: text (strong/em/link marks), inlineCard, breaks."""
    t = node.get("type")
    if t == "text":
        txt = esc(node.get("text", ""))
        for m in node.get("marks", []) or []:
            mt = m.get("type")
            if mt == "strong":
                txt = "<strong>%s</strong>" % txt
            elif mt == "em":
                txt = "<em>%s</em>" % txt
            elif mt == "link":
                href = (m.get("attrs") or {}).get("href", "")
                txt = '<a href="%s">%s</a>' % (esc(href), txt)
        return txt
    if t == "inlineCard":
        url = (node.get("attrs") or {}).get("url") or ""
        label = urllib.parse.urlparse(url).netloc or "link"
        return '<a href="%s">%s</a>' % (esc(url), esc(label))
    if t == "hardBreak":
        return " "
    return "".join(adf_inline_html(c) for c in node.get("content", []) or [])


def adf_paragraphs(adf):
    """[(plain_text, inline_html)] for each non-empty top-level block of an ADF doc."""
    out = []
    if not isinstance(adf, dict):
        return out
    for block in adf.get("content", []) or []:
        h = adf_inline_html(block)
        plain = re.sub(r"<[^>]+>", "", h)
        plain = html.unescape(plain).strip()
        if plain or "href=" in h:
            out.append((plain, h))
    return out


def next_step_of(adf):
    """(html, has_hyperlink) -- the paragraph mentioning 'next step', else the first."""
    paras = adf_paragraphs(adf)
    if not paras:
        return None, False
    chosen = next((p for p in paras if "next step" in p[0].lower()), paras[0])
    has_link = any("href=" in p[1] for p in paras)
    return chosen[1], has_link


def aggregate_children(children):
    """{'active','queued','onhold','open','last'} from [{'status','category','updated'}]."""
    queued = sum(1 for c in children if c["category"] == "new")
    onhold = sum(1 for c in children if c["status"] == "On Hold")
    last = max((c["updated"] for c in children), default=None)
    return {"open": len(children), "queued": queued, "onhold": onhold,
            "active": len(children) - queued - onhold, "last": last}


def health_of(link_status, agg_active, days_quiet, has_next_step, is_linked):
    """One of: hold, stalled, slowing, moving, plan, undef."""
    if not is_linked:
        return "plan" if has_next_step else "undef"
    if link_status == "On Hold":
        return "hold"
    if agg_active == 0 or (days_quiet is not None and days_quiet > 30):
        return "stalled"
    if days_quiet is not None and days_quiet > 7:
        return "slowing"
    return "moving"


CHIP = {"moving": ("good", "Moving"), "slowing": ("warn", "Slowing"),
        "stalled": ("crit", "Stalled"), "hold": ("hold", "On hold"),
        "plan": ("plan", "Plan ready"), "undef": ("warn", "Needs definition")}


def movement_line(days_quiet, last_dt):
    """(text, css_class) for the movement line under a delivery block."""
    if days_quiet is None:
        return None, ""
    if days_quiet <= 0:
        return "Moved today", "fresh"
    if days_quiet == 1:
        return "Moved yesterday", "fresh"
    if days_quiet > 30:
        return "%d days without movement" % days_quiet, "quiet"
    return "Last movement %s %d (%d days)" % (
        last_dt.strftime("%b"), last_dt.day, days_quiet), ""


def parse_iso(s):
    """Jira ISO timestamp like 2026-07-15T08:36:26.200-0500 -> aware datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")


def central_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))


def bar_html(agg):
    """Composition bar + counts line; omits zero segments, widths sum to 100."""
    total = agg["open"]
    if total == 0:
        return '<span class="counts">0 open items</span>'
    segs, label = [], []
    for cls, key, name in (("s-active", "active", "active"),
                           ("s-queued", "queued", "queued"),
                           ("s-hold", "onhold", "on hold")):
        n = agg[key]
        label.append("%d %s" % (n, name))
        if n:
            segs.append('<span class="%s" style="width:%.1f%%"></span>'
                        % (cls, 100.0 * n / total))
    aria = "%d open items: %s" % (total, ", ".join(label))
    if not agg["onhold"]:
        label = label[:2]
    return ('<div class="bar" role="img" aria-label="%s">%s</div>\n'
            '        <span class="counts">%s</span>' %
            (esc(aria), "".join(segs), " &middot; ".join(label)))

# ---------------------------------------------------------------------------
# page assembly
# ---------------------------------------------------------------------------

def build_rows(topics, linked_details, child_aggs, now_ct):
    """Row dicts ready for the template, one per topic in rank order."""
    rows = []
    today = now_ct.date()
    for i, t in enumerate(topics, 1):
        f = t["fields"]
        links = []
        for ln in f.get("issuelinks", []) or []:
            other = ln.get("outwardIssue") or ln.get("inwardIssue")
            if other:
                links.append(other["key"])
        next_html, has_link = next_step_of(f.get("description"))
        link = linked_details.get(links[0]) if links else None
        agg, days_quiet, last_dt = None, None, None
        if link:
            if link["container"]:
                agg = child_aggs.get(links[0]) or aggregate_children([])
                last_dt = parse_iso(agg["last"]) if agg["last"] else None
            else:
                last_dt = parse_iso(link["updated"])
                active = 1 if (link["category"] == "indeterminate"
                               and link["status"] != "On Hold") else 0
                agg = {"open": 1, "active": active, "queued": 1 - active,
                       "onhold": 0, "last": link["updated"], "single": True}
            if last_dt:
                days_quiet = (today - last_dt.astimezone(now_ct.tzinfo).date()).days
        h = health_of(link["status"] if link else None,
                      agg["active"] if agg else 0,
                      days_quiet, bool(next_html), bool(link))
        rows.append({"rank": i, "key": t["key"], "summary": f["summary"],
                     "status": f["status"]["name"],
                     "focus": (f["status"]["name"] == "To Do"
                               or f["status"]["statusCategory"]["key"] == "indeterminate"),
                     "assignee": (f.get("assignee") or {}).get("displayName"),
                     "next_html": next_html, "has_plan_link": has_link,
                     "health": h, "link_key": links[0] if links else None,
                     "extra_links": len(links) - 1, "link": link, "agg": agg,
                     "days_quiet": days_quiet, "last_dt": last_dt})
    return rows


def attention_html(rows):
    items = []
    undef = [r for r in rows if r["health"] == "undef"]
    if undef:
        names = " and ".join("%s (rank %d)" % (esc(r["summary"]), r["rank"]) for r in undef)
        items.append("<li><strong>%s %s no next step or linked work.</strong> "
                     "Write the next step, or they can&rsquo;t genuinely compete for focus.</li>"
                     % (names, "have" if len(undef) > 1 else "has"))
    for r in [r for r in rows if r["health"] == "stalled"]:
        items.append("<li><strong>%s (rank %d) is stalled.</strong> Its linked project "
                     "has zero items in flight%s. Kick it off with a next step, or rank it "
                     "honestly lower.</li>"
                     % (esc(r["summary"]), r["rank"],
                        " and hasn&rsquo;t moved in %d days" % r["days_quiet"]
                        if r["days_quiet"] and r["days_quiet"] > 30 else ""))
    hold = [r for r in rows if r["health"] == "hold"]
    if hold:
        ranks = ", ".join(str(r["rank"]) for r in hold)
        items.append("<li><strong>%d topic%s (rank%s %s) point%s at On Hold projects.</strong> "
                     "Confirm the hold reason still stands &mdash; otherwise they&rsquo;re quietly "
                     "consuming queue positions.</li>"
                     % (len(hold), "s" if len(hold) > 1 else "", "s" if len(hold) > 1 else "",
                        ranks, "" if len(hold) > 1 else "s"))
    for r in [r for r in rows if r["health"] == "slowing"]:
        items.append("<li><strong>%s (rank %d) is slowing.</strong> Last movement %d days ago "
                     "&mdash; check whether it needs attention or a re-rank.</li>"
                     % (esc(r["summary"]), r["rank"], r["days_quiet"]))
    if not items:
        items = ["<li>Nothing urgent &mdash; walk the list top-down, update next steps, "
                 "and re-rank as needed.</li>"]
    return "\n      ".join(items[:4])


def row_html(r):
    chips = ['<span class="chip outline">%s</span>' % esc(r["status"]),
             '<span class="chip %s">%s</span>' % CHIP[r["health"]]]
    next_p = (('<p class="nextstep">%s</p>' % r["next_html"]) if r["next_html"] else
              '<p class="nextstep missing">No next step written yet &mdash; '
              'what&rsquo;s the first action, and who is on it?</p>')
    owner = ('<div class="owner">Owner: %s</div>' % esc(r["assignee"])) if r["assignee"] else ""

    if r["link"]:
        link = r["link"]
        due = ("due %s %d" % (link["due"].strftime("%b"), link["due"].day)
               if link["due"] else "no due date")
        who = esc(link["assignee"] or "unassigned")
        move_txt, move_cls = movement_line(r["days_quiet"], r["last_dt"])
        counts = (('<span class="counts">Single story &mdash; no child breakdown</span>')
                  if r["agg"].get("single") else bar_html(r["agg"]))
        extra = (('\n        <span class="counts">+%d more linked item%s on the topic card</span>'
                  % (r["extra_links"], "s" if r["extra_links"] > 1 else ""))
                 if r["extra_links"] > 0 else "")
        delivery = ('''<div class="linkline">
          <span class="pname"><a href="%s/browse/%s">%s</a> %s</span>
        </div>
        <span>%s &middot; %s &middot; %s</span>
        %s%s
        <span class="movement %s">%s</span>''' % (
            SITE, r["link_key"], r["link_key"], esc(link["summary"]),
            esc(link["status"]), due, who, counts, extra, move_cls, esc(move_txt or "")))
    elif r["has_plan_link"]:
        delivery = '<span class="none">No Jira work yet &mdash; plan linked in the next step</span>'
    else:
        delivery = '<span class="none">No linked work items</span>'

    return '''    <div class="row%s">
      <div class="rank">%d</div>
      <div class="topic">
        <div class="titleline">
          <span class="name"><a href="%s/browse/%s">%s</a></span>
          <span class="key">%s</span>
          %s
        </div>
        %s%s
      </div>
      <div class="delivery">
        %s
      </div>
    </div>''' % ((" focus" if r["focus"] else ""), r["rank"], SITE, r["key"],
                 esc(r["summary"]), r["key"], "\n          ".join(chips),
                 next_p, owner, delivery)


def stats_html(rows):
    counts = {}
    for r in rows:
        counts[r["health"]] = counts.get(r["health"], 0) + 1
    tiles = [(len(rows), "Topics", "")]
    for key, label in (("moving", "Moving"), ("slowing", "Slowing"),
                       ("plan", "Plan stage"), ("hold", "On hold"),
                       ("stalled", "Stalled"), ("undef", "Undefined")):
        n = counts.get(key, 0)
        if key == "slowing" and n == 0:
            continue
        tiles.append((n, label, " flagged" if key in ("stalled", "undef") and n else ""))
    return "\n    ".join(
        '<div class="stat%s"><div class="n">%d</div><div class="l">%s</div></div>'
        % (flag, n, label) for n, label, flag in tiles)


def render(rows, now_ct):
    stamp = "%s %d, %s, %d:%02d %s CT" % (
        now_ct.strftime("%b"), now_ct.day, now_ct.year,
        (now_ct.hour % 12) or 12, now_ct.minute, "AM" if now_ct.hour < 12 else "PM")
    return (TEMPLATE
            .replace("{{STAMP}}", stamp)
            .replace("{{STATS}}", stats_html(rows))
            .replace("{{ATTENTION}}", attention_html(rows))
            .replace("{{ROWS}}", "\n\n".join(row_html(r) for r in rows)))

# ---------------------------------------------------------------------------
# main pull
# ---------------------------------------------------------------------------

def pull_and_render():
    auth = load_creds()
    now_ct = central_now()

    print("[1/3] queue: children of %s" % PARENT_KEY)
    topics = search("parent = %s ORDER BY Rank ASC" % PARENT_KEY,
                    ["summary", "status", "labels", "assignee", "description",
                     "issuelinks", "updated"], auth)
    if not topics:
        raise RuntimeError("Queue query returned no topics -- refusing to publish an empty page")

    linked_keys = []
    for t in topics:
        for ln in t["fields"].get("issuelinks", []) or []:
            other = ln.get("outwardIssue") or ln.get("inwardIssue")
            if other and other["key"] not in linked_keys:
                linked_keys.append(other["key"])

    print("[2/3] linked issues: %s" % ", ".join(linked_keys))
    linked_details = {}
    if linked_keys:
        for iss in search("key in (%s)" % ", ".join(linked_keys),
                          ["summary", "status", "duedate", "assignee",
                           "updated", "issuetype"], auth):
            f = iss["fields"]
            linked_details[iss["key"]] = {
                "summary": f["summary"], "status": f["status"]["name"],
                "category": f["status"]["statusCategory"]["key"],
                "container": f["issuetype"].get("hierarchyLevel", 0) > 0,
                "due": (datetime.strptime(f["duedate"], "%Y-%m-%d")
                        if f.get("duedate") else None),
                "assignee": (f.get("assignee") or {}).get("displayName"),
                "updated": f["updated"]}

    print("[3/3] children of container links")
    child_aggs = {}
    for key, d in linked_details.items():
        if not d["container"]:
            continue
        kids = search('issue in portfolioChildIssuesOf("%s") AND statusCategory != Done' % key,
                      ["status", "updated"], auth)
        child_aggs[key] = aggregate_children([
            {"status": k["fields"]["status"]["name"],
             "category": k["fields"]["status"]["statusCategory"]["key"],
             "updated": k["fields"]["updated"]} for k in kids])

    rows = build_rows(topics, linked_details, child_aggs, now_ct)
    page = render(rows, now_ct)
    with open(OUT_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    print("Wrote %s (%d topics, %d bytes)" % (OUT_FILE, len(rows), len(page)))

# ---------------------------------------------------------------------------
# selftest (offline)
# ---------------------------------------------------------------------------

def selftest():
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Next step:", "marks": [{"type": "strong"}]},
            {"type": "text", "text": " review the "},
            {"type": "inlineCard", "attrs": {"url": "https://x.sharepoint.com/p?e=1"}}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Shape: project"}]}]}
    nxt, has_link = next_step_of(adf)
    assert "<strong>Next step:</strong>" in nxt and "x.sharepoint.com" in nxt and has_link

    agg = aggregate_children([
        {"status": "Implementation", "category": "indeterminate", "updated": "2026-07-15T08:00:00.000-0500"},
        {"status": "On Hold", "category": "indeterminate", "updated": "2026-07-01T08:00:00.000-0500"},
        {"status": "Backlog", "category": "new", "updated": "2026-06-01T08:00:00.000-0500"}])
    assert (agg["open"], agg["active"], agg["queued"], agg["onhold"]) == (3, 1, 1, 1)
    assert agg["last"].startswith("2026-07-15")

    assert health_of("On Hold", 5, 2, True, True) == "hold"
    assert health_of("Backlog", 0, 77, False, True) == "stalled"
    assert health_of("Implementation", 5, 12, True, True) == "slowing"
    assert health_of("Implementation", 5, 0, True, True) == "moving"
    assert health_of(None, 0, None, True, False) == "plan"
    assert health_of(None, 0, None, False, False) == "undef"

    assert movement_line(0, None) == ("Moved today", "fresh")
    assert movement_line(35, None)[1] == "quiet"
    d = parse_iso("2026-06-19T13:23:13.182-0500")
    assert movement_line(26, d) == ("Last movement Jun 19 (26 days)", "")

    now = datetime(2026, 7, 15, 13, 0, tzinfo=timezone(timedelta(hours=-5)))
    topics = [
        {"key": "ASC-1", "fields": {"summary": "Linked topic", "status":
            {"name": "To Do", "statusCategory": {"key": "new"}}, "assignee": None,
            "description": None, "issuelinks": [{"outwardIssue": {"key": "ASC-100"}}]}},
        {"key": "ASC-2", "fields": {"summary": "Bare topic", "status":
            {"name": "Backlog", "statusCategory": {"key": "new"}}, "assignee": None,
            "description": None, "issuelinks": []}}]
    details = {"ASC-100": {"summary": "Proj", "status": "Implementation",
               "category": "indeterminate", "container": True, "due": None,
               "assignee": "Dev One", "updated": "2026-07-15T08:00:00.000-0500"}}
    aggs = {"ASC-100": agg}
    rows = build_rows(topics, details, aggs, now)
    assert rows[0]["health"] == "moving" and rows[1]["health"] == "undef"
    page = render(rows, now)
    assert BOARD_URL in page and page.count('class="row') == 2
    assert "Jul 15, 2026, 1:00 PM CT" in page
    print("selftest OK")

# ---------------------------------------------------------------------------
# template (keep in sync with the Claude-artifact version of this dashboard)
# ---------------------------------------------------------------------------

TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ascend Topic Queue</title>
<style>
  :root { --bg:#F7F8F7; --surface:#FFFFFF; --ink:#1A2421; --muted:#5C6B66; --faint:#8A9691; --border:#E1E6E4; --accent:#0E6E63; --accent-ink:#0A5A51; --accent-soft:#E3F0EE; --good:#2E7D46; --good-soft:#E4F1E8; --warn:#9A6512; --warn-soft:#F7EDDA; --crit:#B3392E; --crit-soft:#F8E7E5; --hold:#5E6B8C; --hold-soft:#E9ECF4; --bar-active:#0E6E63; --bar-queued:#C3CCC9; --bar-hold:#8B99BD; --shadow:0 1px 3px rgba(26,36,33,0.07); }
  @media (prefers-color-scheme: dark) { :root { --bg:#101615; --surface:#18201E; --ink:#E8EDEB; --muted:#93A29D; --faint:#6C7A75; --border:#2A3532; --accent:#3FBFAE; --accent-ink:#6ACFC1; --accent-soft:#16302C; --good:#58B878; --good-soft:#16301F; --warn:#D9A04A; --warn-soft:#33260F; --crit:#E0685C; --crit-soft:#391512; --hold:#8B99BD; --hold-soft:#1D2333; --bar-active:#3FBFAE; --bar-queued:#39443F; --bar-hold:#8B99BD; --shadow:0 1px 3px rgba(0,0,0,0.35); } }
  :root[data-theme="dark"] { --bg:#101615; --surface:#18201E; --ink:#E8EDEB; --muted:#93A29D; --faint:#6C7A75; --border:#2A3532; --accent:#3FBFAE; --accent-ink:#6ACFC1; --accent-soft:#16302C; --good:#58B878; --good-soft:#16301F; --warn:#D9A04A; --warn-soft:#33260F; --crit:#E0685C; --crit-soft:#391512; --hold:#8B99BD; --hold-soft:#1D2333; --bar-active:#3FBFAE; --bar-queued:#39443F; --bar-hold:#8B99BD; --shadow:0 1px 3px rgba(0,0,0,0.35); }
  :root[data-theme="light"] { --bg:#F7F8F7; --surface:#FFFFFF; --ink:#1A2421; --muted:#5C6B66; --faint:#8A9691; --border:#E1E6E4; --accent:#0E6E63; --accent-ink:#0A5A51; --accent-soft:#E3F0EE; --good:#2E7D46; --good-soft:#E4F1E8; --warn:#9A6512; --warn-soft:#F7EDDA; --crit:#B3392E; --crit-soft:#F8E7E5; --hold:#5E6B8C; --hold-soft:#E9ECF4; --bar-active:#0E6E63; --bar-queued:#C3CCC9; --bar-hold:#8B99BD; --shadow:0 1px 3px rgba(26,36,33,0.07); }
  * { box-sizing:border-box; }
  body { background:var(--bg); color:var(--ink); font-family:"Segoe UI Variable Text","Segoe UI","Avenir Next",-apple-system,"Helvetica Neue",sans-serif; line-height:1.5; margin:0; padding:2.5rem 1.25rem 4rem; }
  .wrap { max-width:1080px; margin:0 auto; }
  a { color:var(--accent-ink); text-decoration:none; } a:hover { text-decoration:underline; }
  a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:2px; }
  header { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:0.5rem 1.5rem; margin-bottom:0.4rem; }
  h1 { font-family:"Segoe UI Variable Display","Segoe UI","Avenir Next",sans-serif; font-size:1.45rem; font-weight:650; letter-spacing:-0.01em; margin:0; text-wrap:balance; }
  .meta { color:var(--muted); font-size:0.82rem; } .meta a { color:var(--muted); text-decoration:underline; text-decoration-color:var(--border); }
  .subtitle { color:var(--muted); font-size:0.9rem; max-width:62ch; margin:0 0 1.6rem; }
  .stats { display:flex; flex-wrap:wrap; gap:0.5rem; margin-bottom:1.6rem; }
  .stat { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:0.5rem 0.9rem 0.55rem; min-width:5.4rem; box-shadow:var(--shadow); }
  .stat .n { font-size:1.35rem; font-weight:650; font-variant-numeric:tabular-nums; line-height:1.15; }
  .stat .l { font-size:0.68rem; letter-spacing:0.07em; text-transform:uppercase; color:var(--muted); }
  .stat.flagged .n { color:var(--crit); }
  .attention { background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--warn); border-radius:8px; padding:0.85rem 1.1rem; margin-bottom:2rem; box-shadow:var(--shadow); }
  .attention h2 { font-size:0.72rem; letter-spacing:0.09em; text-transform:uppercase; color:var(--warn); margin:0 0 0.5rem; font-weight:650; }
  .attention ul { margin:0; padding-left:1.1rem; } .attention li { font-size:0.88rem; margin-bottom:0.3rem; max-width:90ch; } .attention li:last-child { margin-bottom:0; }
  .queue-head { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:0.5rem 1rem; margin-bottom:0.7rem; }
  .queue-head h2 { font-size:0.78rem; letter-spacing:0.09em; text-transform:uppercase; color:var(--muted); font-weight:650; margin:0; }
  .queue-tools { display:flex; align-items:center; flex-wrap:wrap; gap:0.5rem 1.1rem; }
  .rerank { display:inline-block; border:1px solid var(--accent); color:var(--accent-ink); border-radius:99px; padding:0.22rem 0.75rem; font-size:0.76rem; font-weight:650; white-space:nowrap; }
  .rerank:hover { background:var(--accent-soft); text-decoration:none; }
  .barkey { font-size:0.75rem; color:var(--muted); display:flex; gap:0.9rem; flex-wrap:wrap; }
  .dot { display:inline-block; width:0.55em; height:0.55em; border-radius:50%; margin-right:0.32em; vertical-align:0.02em; }
  .dot.active { background:var(--bar-active); } .dot.queued { background:var(--bar-queued); } .dot.hold { background:var(--bar-hold); }
  .queue { display:flex; flex-direction:column; gap:0.55rem; }
  .row { display:grid; grid-template-columns:3.2rem 1fr 19rem; gap:0 1.25rem; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:0.95rem 1.15rem 1rem 0.9rem; box-shadow:var(--shadow); }
  .rank { font-family:"Cascadia Code","SF Mono",Consolas,monospace; font-variant-numeric:tabular-nums; font-size:1.5rem; font-weight:600; color:var(--faint); text-align:center; line-height:1.2; padding-top:0.1rem; }
  .row.focus .rank { color:var(--accent); }
  .topic .titleline { display:flex; flex-wrap:wrap; align-items:baseline; gap:0.25rem 0.6rem; margin-bottom:0.28rem; }
  .topic .name { font-size:1.02rem; font-weight:640; letter-spacing:-0.005em; } .topic .name a { color:var(--ink); }
  .key { font-family:"Cascadia Code","SF Mono",Consolas,monospace; font-size:0.74rem; color:var(--faint); }
  .nextstep { font-size:0.88rem; color:var(--muted); max-width:62ch; margin:0; } .nextstep strong { color:var(--ink); font-weight:600; } .nextstep.missing { color:var(--warn); }
  .owner { font-size:0.78rem; color:var(--faint); margin-top:0.3rem; }
  .chip { display:inline-block; font-size:0.68rem; font-weight:650; letter-spacing:0.05em; text-transform:uppercase; border-radius:99px; padding:0.13rem 0.55rem; white-space:nowrap; }
  .chip.outline { border:1px solid var(--border); color:var(--muted); } .chip.good { background:var(--good-soft); color:var(--good); } .chip.warn { background:var(--warn-soft); color:var(--warn); } .chip.crit { background:var(--crit-soft); color:var(--crit); } .chip.hold { background:var(--hold-soft); color:var(--hold); } .chip.plan { background:var(--accent-soft); color:var(--accent-ink); }
  .delivery { border-left:1px solid var(--border); padding-left:1.25rem; font-size:0.82rem; color:var(--muted); display:flex; flex-direction:column; gap:0.3rem; justify-content:center; }
  .delivery .linkline { display:flex; flex-wrap:wrap; align-items:baseline; gap:0.25rem 0.55rem; }
  .delivery .pname { font-weight:600; color:var(--ink); font-size:0.86rem; } .delivery .none { color:var(--faint); font-style:italic; }
  .bar { display:flex; gap:2px; height:7px; border-radius:4px; overflow:hidden; margin:0.15rem 0 0.1rem; max-width:15.5rem; }
  .bar span { display:block; height:100%; } .bar .s-active { background:var(--bar-active); } .bar .s-queued { background:var(--bar-queued); } .bar .s-hold { background:var(--bar-hold); }
  .bar span:first-child { border-radius:4px 0 0 4px; } .bar span:last-child { border-radius:0 4px 4px 0; }
  .counts { font-variant-numeric:tabular-nums; }
  .movement { font-size:0.78rem; } .movement.quiet { color:var(--crit); font-weight:600; } .movement.fresh { color:var(--good); }
  footer { margin-top:2.4rem; padding-top:1.2rem; border-top:1px solid var(--border); color:var(--muted); font-size:0.82rem; }
  footer p { max-width:88ch; margin:0 0 0.5rem; }
  footer code { font-family:"Cascadia Code","SF Mono",Consolas,monospace; font-size:0.78rem; background:var(--surface); border:1px solid var(--border); border-radius:4px; padding:0.05rem 0.35rem; }
  @media (max-width:860px) { .row { grid-template-columns:2.4rem 1fr; } .delivery { grid-column:2; border-left:none; border-top:1px dashed var(--border); padding-left:0; padding-top:0.6rem; margin-top:0.65rem; } }
  @media (prefers-reduced-motion: no-preference) { .row { transition:border-color 120ms ease; } .row:hover { border-color:var(--faint); } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Ascend Topic Queue</h1>
    <div class="meta">
      Snapshot &middot; {{STAMP}} &middot;
      <a href="https://trekbikes.atlassian.net/browse/ASC-9433">ASC-9433</a> &middot;
      <a href="https://trekbikes.atlassian.net/jira/software/c/projects/ASC/boards/6316">re-rank board</a> &middot;
      <a href="''' + QUEUE_JQL_URL + r'''">open in Jira</a>
    </div>
  </header>
  <p class="subtitle">
    One ranked list of everything competing for the team&rsquo;s attention &mdash; quick fixes, projects, and
    compliance work side by side. Rank order is the priority; the right column shows whether the
    linked delivery work is actually moving.
  </p>

  <div class="stats">
    {{STATS}}
  </div>

  <section class="attention">
    <h2>Needs a decision</h2>
    <ul>
      {{ATTENTION}}
    </ul>
  </section>

  <div class="queue-head">
    <h2>The queue</h2>
    <div class="queue-tools">
      <a class="rerank" href="https://trekbikes.atlassian.net/jira/software/c/projects/ASC/boards/6316">Re-rank on the board &#8599;</a>
      <div class="barkey">
        <span><span class="dot active"></span>Active</span>
        <span><span class="dot queued"></span>Queued</span>
        <span><span class="dot hold"></span>On hold</span>
      </div>
    </div>
  </div>

  <div class="queue">

{{ROWS}}

  </div>

  <footer>
    <p><strong>How this works.</strong> Each topic is one card under <a href="https://trekbikes.atlassian.net/browse/ASC-9433">ASC-9433</a>, whatever the size of the work behind it. Rank is the priority &mdash; re-order by dragging cards on the <a href="https://trekbikes.atlassian.net/jira/software/c/projects/ASC/boards/6316">re-rank board</a>. &ldquo;Active&rdquo; counts items genuinely in flight under the linked project (excluding On Hold); &ldquo;Queued&rdquo; is backlog; movement is the most recent update to any open child item.</p>
    <p><strong>To refresh:</strong> run the <a href="''' + WORKFLOW_URL + r'''">Update Topic Queue workflow</a> (Actions &rarr; Run workflow) &mdash; it regenerates this page from live Jira. It also runs automatically every morning.</p>
  </footer>
</div>
</body>
</html>
'''

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        pull_and_render()
