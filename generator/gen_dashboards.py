# -*- coding: utf-8 -*-
"""Generate the SDM digital-workflow funnel dashboards from data/funnel-data.json.

Outputs docs/index.html (parent), docs/tbn.html, docs/asc.html - self-contained
static pages, every bar/count linked to its Jira JQL.
Model: target = 3 x devs in RfD; burn/replenish = 2 items/dev/week; review = 17.5 min/item.
Refresh procedure: see REFRESH.md at the repo root.
"""
import json, os, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = json.load(open(os.path.join(ROOT, "data", "funnel-data.json"), encoding="utf-8"))
ASOF = DATA["asOf"]
PARENT_URL = "index.html"
BURN, RMIN = DATA["burnPerDevPerWeek"], DATA["reviewMinutesPerItem"]
JIRA = DATA["jiraBase"]

def jurl(jql): return JIRA + urllib.parse.quote(jql)

PODS = {k: v["pods"] for k, v in DATA["projects"].items()}
PROJ_META = {k: dict(label=v["label"], file=v["file"], noteam=v["noteam"], hygiene=v.get("hygiene", {}),
                     memoHeader=v.get("memoHeader", v["label"]))
             for k, v in DATA["projects"].items()}
# Active = every pod has a confirmed dev count. Pending projects are staged in the
# data file but not published (no card, no sub-page) until counts land.
ACTIVE = [p for p, v in DATA["projects"].items()
          if not any(pod.get("devs") is None for pod in v["pods"])]

def pkey(proj): return '"%s"' % proj  # always quote; safe for reserved-word keys (ASC, ...)

def tids_of(pod): return pod.get("tids") or [pod["tid"]]

def team_clause(pod):  # single tid -> team = "x"; merged pod -> team in ("x","y")
    ts = tids_of(pod)
    return ('team = "%s"' % ts[0]) if len(ts) == 1 else ('team in (%s)' % ", ".join('"%s"' % t for t in ts))

def st_jql(proj, pod, status):
    return 'project = %s AND %s AND status = "%s" AND issuetype not in subtaskIssueTypes()' % (pkey(proj), team_clause(pod), status)

def noteam_jql(proj, status):
    return 'project = %s AND status = "%s" AND team is EMPTY AND issuetype not in subtaskIssueTypes()' % (pkey(proj), status)

def back_jql(proj, pod, bucket=None):
    base = st_jql(proj, pod, "Backlog")
    if bucket == 0: base += " AND created >= -26w"
    elif bucket == 1: base += " AND created < -26w AND created >= -52w"
    elif bucket == 2: base += " AND created < -52w"
    return base + " ORDER BY created ASC"

def hyg_pq_jql(proj):    # priority-queue hygiene: teamless items in the ranked queue
    return ('parent = %s AND team is EMPTY AND statusCategory != Done ORDER BY Rank ASC'
            % PROJ_META[proj]["hygiene"]["parent"])

def hyg_rfd_jql(proj):   # noteam-rfd hygiene: the explicit no-team RfD key list
    keys = PROJ_META[proj]["hygiene"].get("rfdKeys", [])
    return "key in (%s)" % ", ".join(keys) if keys else noteam_jql(proj, "Ready for Development")

def plan(p):
    gap = max(0, p["tgt"] - p["rfd"])
    ua = min(gap, p["ana"]); ut = min(gap - ua, len(p["auto"]))
    review = ua + ut
    return dict(gap=gap, ua=ua, ut=ut, review=review,
                hours=round(review * RMIN / 60, 1), starve=gap - review,
                wk_items=BURN * p["devs"], wk_hours=round(BURN * p["devs"] * RMIN / 60, 1),
                runway=round(p["genuine"] * 7 / (BURN * p["devs"])),
                topup=max(0, 4 * p["devs"] - p["genuine"]))

CSS = """
  :root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
    --blue:#2a78d6; --blue-soft:#86b6ef; --blue-deep:#104281;
    --age1:#86b6ef; --age2:#2a78d6; --age3:#104281;
    --good:#0ca30c; --good-text:#006300; --critical:#d03b3b; --critical-tint:rgba(208,59,59,0.10);
    --warn:#fab219; --code-bg:#f0efec; }
  @media (prefers-color-scheme: dark) { :root { --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
    --blue:#3987e5; --blue-soft:#6da7ec; --blue-deep:#184f95; --age1:#6da7ec; --age2:#3987e5; --age3:#184f95;
    --good:#0ca30c; --good-text:#0ca30c; --critical:#d03b3b; --critical-tint:rgba(208,59,59,0.16); --code-bg:#232322; } }
  :root[data-theme="dark"] { --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
    --blue:#3987e5; --blue-soft:#6da7ec; --blue-deep:#184f95; --age1:#6da7ec; --age2:#3987e5; --age3:#184f95;
    --good:#0ca30c; --good-text:#0ca30c; --critical:#d03b3b; --critical-tint:rgba(208,59,59,0.16); --code-bg:#232322; }
  :root[data-theme="light"] { --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
    --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
    --blue:#2a78d6; --blue-soft:#86b6ef; --blue-deep:#104281; --age1:#86b6ef; --age2:#2a78d6; --age3:#104281;
    --good:#0ca30c; --good-text:#006300; --critical:#d03b3b; --critical-tint:rgba(208,59,59,0.10); --code-bg:#f0efec; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--page); color:var(--ink); font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { max-width:1080px; margin:0 auto; padding:36px 24px 72px; }
  .eyebrow { text-transform:uppercase; letter-spacing:.09em; font-size:11.5px; font-weight:600; color:var(--muted); }
  .eyebrow a { color:var(--blue); text-decoration:none; }
  .eyebrow a:hover { text-decoration:underline; }
  h1 { font-size:26px; line-height:1.2; margin:6px 0 4px; text-wrap:balance; }
  .sub { color:var(--ink-2); max-width:76ch; margin:0; }
  .sub strong { color:var(--ink); }
  section { margin-top:42px; }
  h2 { font-size:19px; margin:0 0 4px; }
  .h2note { color:var(--ink-2); font-size:13.5px; margin:0 0 14px; max-width:82ch; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:12px; margin-top:24px; }
  .tile { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:15px 18px 13px; }
  .tile .v { font-size:28px; font-weight:700; line-height:1.15; }
  .tile .v small { font-size:15px; font-weight:600; color:var(--ink-2); }
  .tile .v.bad { color:var(--critical); }
  .tile .v.ok { color:var(--good-text); }
  .tile .k { font-size:12.5px; color:var(--ink-2); margin-top:3px; }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px 22px 16px; margin-top:12px; }
  a.trackline { display:grid; grid-template-columns:150px 1fr 170px; gap:14px; align-items:center; padding:8px 0;
    text-decoration:none; color:inherit; border-radius:6px; }
  a.trackline + a.trackline { border-top:1px solid var(--grid); }
  a.trackline:hover .bname, a.trackline:focus-visible .bname { color:var(--blue); text-decoration:underline; }
  .bname { font-size:13.5px; font-weight:600; }
  .bname .dv { display:block; font-weight:400; font-size:11.5px; color:var(--muted); }
  .track { position:relative; height:22px; }
  .track .rail { position:absolute; inset:4px 0; background:var(--page); border-radius:4px; border:1px solid var(--grid); }
  .seg { position:absolute; top:4px; bottom:4px; border-radius:4px; }
  .seg.cur { background:var(--blue); }
  .seg.over { background:var(--blue-soft); }
  .seg.deficit { background:var(--critical-tint); border:1.5px dashed var(--critical); }
  .ttick { position:absolute; top:0; bottom:0; width:2px; background:var(--ink); }
  .ttick::after { content:attr(data-t); position:absolute; top:-1px; left:5px; font-size:10.5px; color:var(--muted); }
  .bval { font-size:13px; color:var(--ink-2); font-variant-numeric:tabular-nums; }
  .bval b { color:var(--ink); font-size:14.5px; }
  .gap-chip { display:inline-block; margin-left:7px; padding:1px 7px; border-radius:99px; font-size:11.5px; font-weight:700; }
  .gap-chip.bad { background:var(--critical-tint); color:var(--critical); }
  .gap-chip.ok { background:rgba(12,163,12,0.10); color:var(--good-text); }
  .legend { display:flex; flex-wrap:wrap; gap:16px; margin:12px 2px 0; font-size:12px; color:var(--ink-2); }
  .legend .li { display:inline-flex; align-items:center; gap:6px; }
  .sw { width:14px; height:10px; border-radius:3px; display:inline-block; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; margin-top:12px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:17px 20px 15px; display:flex; flex-direction:column; gap:9px; }
  .card.alert { border-left:3px solid var(--critical); }
  .card h3 { margin:0; font-size:15.5px; display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
  .chip { font-size:11.5px; font-weight:700; padding:1px 8px; border-radius:99px; white-space:nowrap; }
  .chip.bad { background:var(--critical-tint); color:var(--critical); }
  .chip.ok { background:rgba(12,163,12,0.10); color:var(--good-text); }
  .story { font-size:13.5px; color:var(--ink-2); margin:0; }
  .story strong { color:var(--ink); }
  .runway { font-size:12.5px; color:var(--ink-2); display:flex; align-items:center; gap:7px; }
  .dot { width:9px; height:9px; border-radius:50%; flex:none; }
  .dot.r { background:var(--critical); } .dot.y { background:var(--warn); } .dot.g { background:var(--good); }
  .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:2px; align-items:center; }
  a.act { font:600 12.5px/1 system-ui,-apple-system,"Segoe UI",sans-serif; padding:8px 12px; border-radius:7px;
    border:1px solid var(--border); background:var(--page); color:var(--ink); text-decoration:none; }
  a.act.primary { background:var(--blue); border-color:var(--blue); color:#fff; }
  a.act:hover { border-color:var(--blue); }
  a.act.primary:hover { background:var(--blue-deep); border-color:var(--blue-deep); }
  span.act.none { font:600 12.5px/1 system-ui,sans-serif; padding:8px 12px; border-radius:7px; border:1px solid var(--border); color:var(--muted); }
  button.copy { font:600 12.5px/1 system-ui,-apple-system,"Segoe UI",sans-serif; padding:8px 12px; border-radius:7px;
    border:1px solid var(--border); background:var(--blue); border-color:var(--blue); color:#fff; cursor:pointer; }
  button.copy:hover { background:var(--blue-deep); }
  details { margin-top:2px; }
  summary { font-size:12px; color:var(--muted); cursor:pointer; }
  .jql { margin-top:6px; background:var(--code-bg); border:1px solid var(--grid); border-radius:7px; padding:10px 12px;
    font:12px/1.5 ui-monospace,Consolas,monospace; color:var(--ink-2); white-space:pre-wrap; word-break:break-all; max-height:200px; overflow-y:auto; }
  .memo { background:var(--code-bg); border:1px solid var(--grid); border-radius:7px; padding:12px 14px;
    font-size:13px; color:var(--ink-2); white-space:pre-wrap; }
  .hint { font-size:12px; color:var(--muted); margin:0; }
  .tblwrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th { text-align:right; font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); font-weight:600;
    padding:8px 10px; border-bottom:1px solid var(--baseline); }
  th:first-child, td:first-child { text-align:left; }
  td { padding:8px 10px; border-bottom:1px solid var(--grid); text-align:right; font-variant-numeric:tabular-nums; color:var(--ink-2); }
  td:first-child { color:var(--ink); font-weight:600; }
  td a { color:var(--blue); text-decoration:none; }
  td a:hover { text-decoration:underline; }
  td .note { font-weight:400; color:var(--muted); font-size:12px; }
  tr.dim td { color:var(--muted); font-weight:400; }
  td.hot, td.hot a { color:var(--critical); font-weight:700; }
  td.good { color:var(--good-text); font-weight:600; }
  .callout { border-left:3px solid var(--warn); background:var(--surface); border-top:1px solid var(--border);
    border-right:1px solid var(--border); border-bottom:1px solid var(--border); border-radius:0 10px 10px 0;
    padding:15px 20px; margin-top:12px; }
  .callout h3 { margin:0 0 6px; font-size:15px; }
  .callout p { margin:6px 0; color:var(--ink-2); font-size:14px; max-width:90ch; }
  .callout p strong { color:var(--ink); }
  .arow { display:grid; grid-template-columns:150px 1fr 130px; gap:14px; align-items:center; padding:8px 0; }
  .arow + .arow { border-top:1px solid var(--grid); }
  .aname { font-size:13.5px; font-weight:600; }
  .abar { height:18px; display:flex; gap:2px; }
  .abar a { height:100%; border-radius:3px; min-width:4px; display:block; }
  .abar a:hover { outline:2px solid var(--ink); outline-offset:1px; }
  .a1 { background:var(--age1); } .a2 { background:var(--age2); } .a3 { background:var(--age3); }
  .atot { font-size:13px; color:var(--ink-2); font-variant-numeric:tabular-nums; }
  .atot a { color:var(--blue); text-decoration:none; }
  .atot a:hover { text-decoration:underline; }
  .plist { display:grid; grid-template-columns:repeat(auto-fit,minmax(400px,1fr)); gap:14px; margin-top:16px; }
  a.pcard { display:block; background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:20px 22px; text-decoration:none; color:inherit; }
  a.pcard:hover { border-color:var(--blue); }
  a.pcard h3 { margin:0 0 2px; font-size:17px; color:var(--blue); }
  a.pcard .meta { font-size:13px; color:var(--ink-2); margin-bottom:10px; }
  .mini { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:8px; }
  .mini .m { background:var(--page); border:1px solid var(--grid); border-radius:7px; padding:8px 10px; }
  .mini .mv { font-size:17px; font-weight:700; }
  .mini .mv.bad { color:var(--critical); }
  .mini .mk { font-size:10.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  footer { margin-top:44px; color:var(--muted); font-size:12.5px; max-width:92ch; }
  :focus-visible { outline:2px solid var(--blue); outline-offset:2px; }
"""

COPY_JS = """
<script>
document.addEventListener("click", function (e) {
  var b = e.target.closest("button.copy"); if (!b) return;
  var el = document.getElementById(b.getAttribute("data-for")); if (!el) return;
  var text = el.textContent;
  function done(){ var o=b.textContent; b.textContent="Copied \\u2713"; setTimeout(function(){ b.textContent=o; },1600); }
  if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, fb); } else fb();
  function fb(){ var t=document.createElement("textarea"); t.value=text; t.style.position="fixed"; t.style.opacity="0";
    document.body.appendChild(t); t.select(); try{ document.execCommand("copy"); done(); }catch(err){} document.body.removeChild(t); }
});
</script>
"""

def page(title, body):
    parts = ["<!doctype html>", "<html lang=\"en\">", "<head>", "<meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<title>" + title + "</title>", "<style>" + CSS + "</style>", "</head>", "<body>",
             "<div class=\"wrap\">", body, "</div>", COPY_JS, "</body>", "</html>"]
    return "\n".join(parts).replace("{{ASOF}}", ASOF)

def bullet_rows(proj, pods):
    mx = max(max(p["rfd"], p["tgt"]) for p in pods) * 1.08
    rows = []
    for p in pods:
        pl = plan(p); gap = pl["gap"]; over = max(0, p["rfd"] - p["tgt"])
        base = min(p["rfd"], p["tgt"])
        segs = '<div class="rail"></div>'
        if base: segs += '<div class="seg cur" style="left:0;width:%.2f%%"></div>' % (base/mx*100)
        if over: segs += '<div class="seg over" style="left:calc(%.2f%% + 2px);width:calc(%.2f%% - 2px)"></div>' % (p["tgt"]/mx*100, over/mx*100)
        if gap:  segs += '<div class="seg deficit" style="left:calc(%.2f%% + 2px);width:calc(%.2f%% - 2px)"></div>' % (p["rfd"]/mx*100, gap/mx*100)
        segs += '<div class="ttick" style="left:%.2f%%" data-t="%d"></div>' % (p["tgt"]/mx*100, p["tgt"])
        chip = '<span class="gap-chip bad">−%d</span>' % gap if gap else '<span class="gap-chip ok">+%d</span>' % over
        rows.append(
          '<a class="trackline" href="%s" target="_blank" rel="noopener" title="Open %s Ready for Development in Jira">'
          '<div class="bname">%s<span class="dv">%d devs ✓</span></div>'
          '<div class="track">%s</div>'
          '<div class="bval"><b>%d</b> / %d%s</div></a>'
          % (jurl(st_jql(proj, p,"Ready for Development")), p["name"], p["name"], p["devs"], segs, p["rfd"], p["tgt"], chip))
    return "\n".join(rows)

def pod_cards(proj, pods):
    out = []
    for p in pods:
        pl = plan(p); gap = pl["gap"]
        rw = pl["runway"]; rwc = "r" if rw <= 7 else ("y" if rw <= 14 else "g")
        autoj = "key in (%s) ORDER BY Rank ASC" % ", ".join(p["auto"])
        flagj = "key in (%s)" % ", ".join(p["flag"]) if p["flag"] else ""
        if gap:
            story = ('<strong>%d short</strong> of its %d-item target. Catch-up: review %d from Analysis%s '
                     '— one <strong>%.1f h session</strong>%s Steady state then needs <strong>%.1f h/wk</strong> '
                     'of refinement (%d items/wk).') % (
                gap, p["tgt"], pl["ua"],
                " + %d auto-storied from To Do" % pl["ut"] if pl["ut"] else "",
                pl["hours"],
                (" closes all but %d; then To Do is empty." % pl["starve"]) if pl["starve"] > 0 else " closes it.",
                pl["wk_hours"], pl["wk_items"])
        else:
            story = ('<strong>%d over target</strong>. Sustaining it needs <strong>%.1f h/wk</strong> of refinement '
                     'and %d items/wk arriving into To Do — currently holding %d genuine items.') % (
                p["rfd"] - p["tgt"], pl["wk_hours"], pl["wk_items"], p["genuine"])
        chip = '<span class="chip bad">−%d to target</span>' % gap if gap else '<span class="chip ok">+%d over</span>' % (p["rfd"] - p["tgt"])
        review_btn = ('<a class="act" href="%s" target="_blank" rel="noopener">To Do Needs Review ↗ (%d)</a>'
                      % (jurl(flagj), len(p["flag"]))) if p["flag"] else '<span class="act none">No flagged items</span>'
        qtext = "-- auto-story queue --\n" + autoj
        if flagj: qtext += "\n\n-- To Do needs review --\n" + flagj
        qtext += "\n\n-- backlog (ranked oldest first) --\n" + back_jql(proj, p)
        out.append(
          '<div class="card%s">' % (" alert" if gap else "") +
          '<h3>%s %s</h3>' % (p["name"], chip) +
          '<p class="story">%s</p>' % story +
          '<div class="runway"><span class="dot %s"></span>To Do runs dry in ~<b>&nbsp;%d day%s</b>&nbsp;at 2 items/dev/week</div>' % (rwc, rw, "" if rw == 1 else "s") +
          '<div class="actions">' +
          '<a class="act primary" href="%s" target="_blank" rel="noopener">Auto-story queue ↗ (%d)</a>' % (jurl(autoj), len(p["auto"])) +
          review_btn +
          '<a class="act" href="%s" target="_blank" rel="noopener">Backlog ↗ (%d)</a>' % (jurl(back_jql(proj, p)), sum(p["back"])) +
          '</div>' +
          '<p class="hint">Auto-story queue = genuine To Do items ready for enrichment. Tell Claude: “Tag these with claude-ready-sdm, assign to me, then run auto story.”</p>' +
          '<details><summary>Show queries</summary><div class="jql">%s</div></details>' % qtext +
          '</div>')
    return "\n".join(out)

def funnel_table(proj, pods, noteam):
    rows = []
    for p in pods:
        pl = plan(p)
        cell = lambda st, n: '<a href="%s" target="_blank" rel="noopener">%d</a>' % (jurl(st_jql(proj, p,st)), n)
        gap = pl["gap"]
        gapcell = '<td class="hot">−%d</td>' % gap if gap else '<td class="good">+%d</td>' % (p["rfd"] - p["tgt"])
        rows.append('<tr><td>%s</td><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td>%s</tr>' % (
            p["name"], p["devs"], cell("To Do", p["todo"]), p["genuine"], len(p["auto"]),
            cell("Analysis", p["ana"]), cell("SDM Review", p["sdm"]), cell("Ready for Development", p["rfd"]),
            p["tgt"], gapcell))
    nt = lambda st, n: '<a href="%s" target="_blank" rel="noopener">%d</a>' % (jurl(noteam_jql(proj, st)), n)
    rows.append('<tr class="dim"><td>(no team — invisible to pods)</td><td>—</td><td>%s</td><td>—</td><td>—</td><td>%s</td><td>—</td><td>%s</td><td>—</td><td>—</td></tr>' % (
        nt("To Do", noteam["todo"]), nt("Analysis", noteam["ana"]), nt("Ready for Development", noteam["rfd"])))
    return ('<div class="panel tblwrap"><table><thead><tr>'
            '<th>Pod</th><th>Devs</th><th>To Do</th><th>genuine</th><th>auto-story ready</th>'
            '<th>Analysis</th><th>SDM Review</th><th>Ready for Dev</th><th>Target</th><th>Gap</th>'
            '</tr></thead><tbody>%s</tbody></table></div>' % "\n".join(rows))

def weekly_table(proj, pods):
    rows = []
    for p in pods:
        pl = plan(p)
        rcls = ' class="hot"' if pl["runway"] <= 8 else ""
        rows.append('<tr><td>%s</td><td>%d</td><td>%d items</td><td>%.1f h</td><td>%d items</td><td%s>~%d day%s</td></tr>' % (
            p["name"], p["devs"], pl["wk_items"], pl["wk_hours"], pl["wk_items"], rcls, pl["runway"], "" if pl["runway"] == 1 else "s"))
    tot_d = sum(p["devs"] for p in pods); tot_i = BURN * tot_d
    tot_h = round(sum(plan(p)["wk_hours"] for p in pods), 1)
    rows.append('<tr class="dim"><td>Total</td><td>%d</td><td>%d items</td><td>%.1f h</td><td>%d items</td><td>—</td></tr>' % (tot_d, tot_i, tot_h, tot_i))
    return ('<div class="panel tblwrap"><table><thead><tr>'
            '<th>Pod</th><th>Devs</th><th>Weekly burn (2 × devs)</th><th>Weekly pod refinement</th>'
            '<th>PM replenishment / wk</th><th>To Do runway today</th></tr></thead><tbody>%s</tbody></table></div>' % "\n".join(rows))

def age_bars(proj, pods):
    mx = max(sum(p["back"]) for p in pods) or 1
    rows = []
    lbl = ["&lt; 6 mo", "6–12 mo", "&gt; 12 mo"]
    for p in pods:
        tot = sum(p["back"]); segs = []
        for i, v in enumerate(p["back"]):
            if v > 0:
                segs.append('<a class="a%d" style="width:%.2f%%" href="%s" target="_blank" rel="noopener" title="%s — %s: %d items — open in Jira"></a>'
                            % (i + 1, v / mx * 100, jurl(back_jql(proj, p, i)), p["name"], lbl[i].replace("&lt;", "<").replace("&gt;", ">"), v))
        rows.append('<div class="arow"><div class="aname">%s</div><div class="abar">%s</div>'
                    '<div class="atot"><a href="%s" target="_blank" rel="noopener"><b>%d</b> items ↗</a></div></div>'
                    % (p["name"], "".join(segs), jurl(back_jql(proj, p)), tot))
    return "\n".join(rows)

def product_page(proj):
    pods = PODS[proj]; meta = PROJ_META[proj]
    plans = {p["name"]: plan(p) for p in pods}
    tot_gap = sum(pl["gap"] for pl in plans.values())
    catchup = round(sum(pl["hours"] for pl in plans.values()), 1)
    weekly_h = round(sum(pl["wk_hours"] for pl in plans.values()), 1)
    weekly_i = sum(pl["wk_items"] for pl in plans.values())
    min_run = min(pl["runway"] for pl in plans.values())
    min_pod = [n for n, pl in plans.items() if pl["runway"] == min_run][0]

    tiles = (
      '<div class="tiles">'
      '<div class="tile"><div class="v %s">%s</div><div class="k">items behind the 3-per-dev Ready-for-Dev target%s</div></div>'
      '<div class="tile"><div class="v">%.1f h <small>+ %.1f h/wk</small></div><div class="k">refinement: one-time catch-up + weekly to hold pace (17.5 min/item)</div></div>'
      '<div class="tile"><div class="v">%d / wk</div><div class="k">items PMs must move Backlog → To Do weekly (2 per dev per week)</div></div>'
      '<div class="tile"><div class="v %s">≈%d day%s</div><div class="k">until the first pod (%s) has an empty To Do queue</div></div>'
      '</div>' % (
        "bad" if tot_gap else "ok", ("−%d" % tot_gap) if tot_gap else "0",
        " (" + ", ".join("%s −%d" % (n, pl["gap"]) for n, pl in plans.items() if pl["gap"]) + ")" if tot_gap else "",
        catchup, weekly_h, weekly_i,
        "bad" if min_run <= 7 else "", min_run, "" if min_run == 1 else "s", min_pod))

    memo_id = "memo-%s" % proj.lower()
    memo = memo_text(proj)

    hygiene = hygiene_block(proj)

    body = (
      '<header>'
      '<div class="eyebrow"><a href="%s">← All products</a> &nbsp;·&nbsp; SDM Queue Management · pulled from Jira {{ASOF}}</div>' % PARENT_URL +
      '<h1>%s — pod funnel</h1>' % meta["label"] +
      '<p class="sub">Target: <strong>3 items per developer</strong> in Ready for Development, per pod. '
      'Working rate: <strong>2 items per developer per week</strong>. Every bar and count links straight to the Jira query behind it.</p>'
      + tiles +
      '</header>'
      '<section><h2>Ready for Dev vs target</h2>'
      '<p class="h2note">Solid = in Ready for Dev. Black tick = target. Dashed red = gap. <b>Click a row to open that pod’s Ready-for-Dev queue in Jira.</b></p>'
      '<div class="panel">%s</div>'
      '<div class="legend"><span class="li"><span class="sw" style="background:var(--blue)"></span>in Ready for Dev</span>'
      '<span class="li"><span class="sw" style="width:2px;height:12px;border-radius:0;background:var(--ink)"></span>target = 3 × devs</span>'
      '<span class="li"><span class="sw" style="background:var(--critical-tint);border:1.5px dashed var(--critical)"></span>gap</span>'
      '<span class="li"><span class="sw" style="background:var(--blue-soft)"></span>over target</span></div>'
      '</section>' % bullet_rows(proj, pods) +
      '<section><h2>Act on each pod</h2>'
      '<p class="h2note">Auto-story queue = genuine To Do items (pod Team + live parent) ready for Claude enrichment. '
      'To Do Needs Review = items that failed that check and need a human decision.</p>'
      '<div class="cards">%s</div></section>' % pod_cards(proj, pods) +
      hygiene +
      '<section><h2>The full funnel</h2>'
      '<p class="h2note">Every count is a link — click through to the exact Jira query.</p>'
      '%s</section>' % funnel_table(proj, pods, meta["noteam"]) +
      '<section><h2>Sustaining it — the weekly baseline</h2>'
      '<p class="h2note">At 2 items per developer per week, each pod needs this much flowing through refinement AND arriving into To Do, every week.</p>'
      '%s</section>' % weekly_table(proj, pods) +
      '<section><h2>Backlog depth — the raw material</h2>'
      '<p class="h2note">Team-tagged Backlog by age. Click a segment to open that age band in Jira; click the total for the pod’s full ranked backlog.</p>'
      '<div class="panel">%s</div>'
      '<div class="legend"><span class="li"><span class="sw a1"></span>&lt; 6 months</span>'
      '<span class="li"><span class="sw a2"></span>6–12 months</span>'
      '<span class="li"><span class="sw a3"></span>&gt; 12 months</span></div>'
      '</section>' % age_bars(proj, pods) +
      '<section><h2>Product manager note</h2>'
      '<p class="h2note">Copy and send, or point PMs at this page — every ask below is also a clickable query above.</p>'
      '<div class="panel"><div class="memo" id="%s">%s</div>'
      '<div class="actions" style="margin-top:10px"><button class="copy" data-for="%s">Copy note</button></div></div>'
      '</section>' % (memo_id, memo, memo_id) +
      '<footer>Source: Jira (trekbikes.atlassian.net), pulled {{ASOF}}; subtasks excluded; pod = Team field. '
      'Member counts confirmed by Thomas 2026-07-08. Model: 2 items/dev/week burn &amp; replenishment; 17.5 min/item pod review. '
      'Method + item lists: SDM-Queue-Management/progress/funnel-analysis-2026-07-08.md. “ASC” must stay quoted in JQL.</footer>')
    return page("%s Pod Funnel · {{ASOF}}" % proj, body)

def hygiene_block(proj):
    nt = PROJ_META[proj]["noteam"]
    h = PROJ_META[proj]["hygiene"]
    kind = h.get("kind")
    if kind == "noteam-rfd":
        n_rfd = nt["rfd"]
        tot_rfd = n_rfd + sum(p["rfd"] for p in PODS[proj])
        rfdj = hyg_rfd_jql(proj)
        return ('<div class="callout"><h3>⚠ Data hygiene — the fastest lever</h3>'
          '<p><strong>%d of %s’s %d Ready-for-Dev items have no Team</strong>%s. '
          'If they’re pod-owned, setting Team closes most of the gap with zero refinement. If deliberately podless, the dashboard should exclude them.</p>'
          '<div class="actions">'
          '<a class="act primary" href="%s" target="_blank" rel="noopener">No-team Ready-for-Dev ↗ (%d)</a>'
          '<a class="act" href="%s" target="_blank" rel="noopener">No-team To Do ↗ (%d)</a>'
          '</div>'
          '<details><summary>Show queries</summary><div class="jql">%s\n\n%s</div></details></div>'
          % (n_rfd, proj, tot_rfd, h.get("note", ""), jurl(rfdj), n_rfd, jurl(noteam_jql(proj, "To Do")), nt["todo"],
             rfdj, noteam_jql(proj, "To Do")))
    if kind == "priority-queue":
        qsize = h["queueSize"]; qteamless = h["teamlessCount"]; parent = h["parent"]
        pqj = hyg_pq_jql(proj)
        return ('<div class="callout"><h3>⚠ Data hygiene — the priority queue isn’t feeding the boards</h3>'
          '<p><strong>%d of the %d items in the ranked priority queue (%s) have no Team</strong> '
          '— plus <strong>%d teamless To Do items</strong>. Working these credits no pod. Assign Teams so the curated queue counts.</p>'
          '<div class="actions">'
          '<a class="act primary" href="%s" target="_blank" rel="noopener">%s teamless ↗ (%d)</a>'
          '<a class="act" href="%s" target="_blank" rel="noopener">No-team To Do ↗ (%d)</a>'
          '</div>'
          '<details><summary>Show queries</summary><div class="jql">%s\n\n%s</div></details></div>'
          % (qteamless, qsize, parent, nt["todo"], jurl(pqj), parent, qteamless, jurl(noteam_jql(proj, "To Do")), nt["todo"],
             pqj, noteam_jql(proj, "To Do")))
    # generic: surface teamless work straight from the noteam counts (no extra pull)
    if not (nt["rfd"] or nt["todo"]):
        return ""
    return ('<div class="callout"><h3>⚠ Data hygiene — teamless work</h3>'
      '<p><strong>%d Ready-for-Dev and %d To Do items have no Team</strong> and count toward no pod. '
      'Assign Teams so this work shows up on the boards.</p>'
      '<div class="actions">'
      '<a class="act primary" href="%s" target="_blank" rel="noopener">No-team Ready-for-Dev ↗ (%d)</a>'
      '<a class="act" href="%s" target="_blank" rel="noopener">No-team To Do ↗ (%d)</a>'
      '</div>'
      '<details><summary>Show queries</summary><div class="jql">%s\n\n%s</div></details></div>'
      % (nt["rfd"], nt["todo"], jurl(noteam_jql(proj, "Ready for Development")), nt["rfd"],
         jurl(noteam_jql(proj, "To Do")), nt["todo"],
         noteam_jql(proj, "Ready for Development"), noteam_jql(proj, "To Do")))

def memo_text(proj):
    pods = PODS[proj]
    lines = []
    lines.append("%s funnel status — {{ASOF}}" % PROJ_META[proj]["memoHeader"])
    lines.append("Target: 3 items per developer in Ready for Development, per pod. Working rate: 2 items per developer per week.")
    lines.append("")
    lines.append("Per pod — where they stand and what they need:")
    for p in pods:
        pl = plan(p); gap = pl["gap"]
        if gap:
            state = "%d SHORT (%d of %d)" % (gap, p["rfd"], p["tgt"])
            fix = "catch-up = one %.1f h refinement (%d from Analysis%s)%s" % (
                pl["hours"], pl["ua"],
                " + %d auto-storied" % pl["ut"] if pl["ut"] else "",
                "; still %d short after — needs Backlog→To Do" % pl["starve"] if pl["starve"] > 0 else "")
        else:
            state = "+%d over target (%d of %d)" % (p["rfd"] - p["tgt"], p["rfd"], p["tgt"])
            fix = "no catch-up needed"
        lines.append("• %s (%d devs): %s; %s." % (p["name"], p["devs"], state, fix))
        topup = pl["topup"]
        lines.append("   – Needs %d items/week into To Do; holding %d genuine (≈%d day%s of runway)%s. Backlog available: %d items." % (
            pl["wk_items"], p["genuine"], pl["runway"], "" if pl["runway"] == 1 else "s",
            "; top-up +%d now for a 2-week buffer" % topup if topup else "", sum(p["back"])))
    lines.append("")
    tot_i = sum(plan(p)["wk_items"] for p in pods)
    tot_topup = sum(plan(p)["topup"] for p in pods)
    lines.append("• Product total ask: %d items/week moved Backlog → To Do, every week; one-time top-up +%d to give every pod a 2-week buffer." % (tot_i, tot_topup))
    h = PROJ_META[proj]["hygiene"]; kind = h.get("kind")
    nt_rfd, nt_todo = PROJ_META[proj]["noteam"]["rfd"], PROJ_META[proj]["noteam"]["todo"]
    if kind == "noteam-rfd":
        lines.append("• Fastest fix first: %d Ready-for-Dev items carry no Team%s. Assigning Teams may close most of the gap with zero new refinement." % (
            nt_rfd, h.get("note", "")))
    elif kind == "priority-queue":
        lines.append("• Hygiene: %d of %d items in the ranked priority queue (%s) have no Team, plus %d teamless To Do items. Assign pods so the queue feeds the boards." % (
            h["teamlessCount"], h["queueSize"], h["parent"], nt_todo))
    elif nt_rfd or nt_todo:
        lines.append("• Hygiene: %d Ready-for-Dev and %d To Do items have no Team — assign Teams so they count toward the boards." % (nt_rfd, nt_todo))
    if kind != "priority-queue":
        worst = min(pods, key=lambda p: plan(p)["runway"])
        lines.append("• Urgency: %s’s To Do queue is ~%d DAY%s from empty. When To Do empties, the funnel stalls and Ready-for-Dev cannot recover." % (
            worst["name"], plan(worst)["runway"], "" if plan(worst)["runway"] == 1 else "S"))
    lines.append("• Pod capacity: keeping pace costs each pod %.1f–%.1f h/week of refinement (17.5 min/item)." % (
        min(plan(p)["wk_hours"] for p in pods), max(plan(p)["wk_hours"] for p in pods)))
    lines.append("")
    lines.append("Detail: SDM-Queue-Management/progress/funnel-analysis-2026-07-08.md")
    return "\n".join(lines)

def parent_page(prod_urls):
    all_pods = [(proj, p) for proj in ACTIVE for p in PODS[proj]]
    plans = [(proj, p, plan(p)) for proj, p in all_pods]
    tot_gap = sum(pl["gap"] for _, _, pl in plans)
    catchup = round(sum(pl["hours"] for _, _, pl in plans), 1)
    weekly_h = round(sum(pl["wk_hours"] for _, _, pl in plans), 1)
    weekly_i = sum(pl["wk_items"] for _, _, pl in plans)
    min_run = min(pl["runway"] for _, _, pl in plans)
    min_pod = [p["name"] for _, p, pl in plans if pl["runway"] == min_run][0]

    def prod_card(proj):
        pods = PODS[proj]; pls = [plan(p) for p in pods]
        g = sum(pl["gap"] for pl in pls); wi = sum(pl["wk_items"] for pl in pls)
        ch = round(sum(pl["hours"] for pl in pls), 1); mr = min(pl["runway"] for pl in pls)
        gapped = ", ".join("%s −%d" % (p["name"], pl["gap"]) for p, pl in zip(pods, pls) if pl["gap"]) or "none"
        return ('<a class="pcard" href="%s">'
          '<h3>%s →</h3><div class="meta">%d pods · %d devs · gapped: %s</div>'
          '<div class="mini">'
          '<div class="m"><div class="mv %s">%s</div><div class="mk">behind target</div></div>'
          '<div class="m"><div class="mv">%.1f h</div><div class="mk">catch-up</div></div>'
          '<div class="m"><div class="mv">%d/wk</div><div class="mk">PM input</div></div>'
          '<div class="m"><div class="mv %s">~%dd</div><div class="mk">first dry To Do</div></div>'
          '</div></a>') % (
            prod_urls[proj], PROJ_META[proj]["label"], len(pods), sum(p["devs"] for p in pods), gapped,
            "bad" if g else "", ("−%d" % g) if g else "0", ch, wi, "bad" if mr <= 7 else "", mr)

    wk_breakdown = ", ".join("%s %d" % (proj, sum(BURN * p["devs"] for p in PODS[proj])) for proj in ACTIVE)
    cards = "".join(prod_card(proj) for proj in ACTIVE)

    # Cross-product hygiene: one line per project with a configured lever, plus a
    # combined teamless-To-Do link across all active projects.
    hyg_bits, hyg_acts = [], []
    for proj in ACTIVE:
        h = PROJ_META[proj]["hygiene"]; kind = h.get("kind")
        if kind == "noteam-rfd":
            n = PROJ_META[proj]["noteam"]["rfd"]
            hyg_bits.append("<strong>%s: %d Ready-for-Dev items with no Team</strong>%s" % (proj, n, h.get("note", "")))
            hyg_acts.append('<a class="act primary" href="%s" target="_blank" rel="noopener">%s no-team RfD ↗ (%d)</a>'
                            % (jurl(hyg_rfd_jql(proj)), proj, n))
        elif kind == "priority-queue":
            hyg_bits.append("<strong>%s: %d of %d ranked priority-queue items (%s) are teamless</strong>"
                            % (proj, h["teamlessCount"], h["queueSize"], h["parent"]))
            hyg_acts.append('<a class="act" href="%s" target="_blank" rel="noopener">%s teamless ↗ (%d)</a>'
                            % (jurl(hyg_pq_jql(proj)), h["parent"], h["teamlessCount"]))
    nt_todo_all = sum(PROJ_META[p]["noteam"]["todo"] for p in ACTIVE)
    td_jql = ('project in (%s) AND status = "To Do" AND team is EMPTY AND issuetype not in subtaskIssueTypes() '
              'ORDER BY project, created ASC' % ", ".join(pkey(p) for p in ACTIVE))
    hyg_acts.append('<a class="act" href="%s" target="_blank" rel="noopener">Teamless To Do, all ↗ (%d)</a>'
                    % (jurl(td_jql), nt_todo_all))
    hygiene_section = ""
    if hyg_bits or nt_todo_all:
        intro = " ".join(hyg_bits)
        if nt_todo_all:
            intro += (" Plus " if hyg_bits else "") + "%d teamless To Do items across all products." % nt_todo_all
        hygiene_section = ('<section><h2>Cross-product data hygiene</h2>'
          '<p class="h2note">Work that counts toward no pod because the Team field is empty — the fastest levers.</p>'
          '<div class="callout"><h3>⚠ Assign Teams on these first</h3>'
          '<p>%s</p><div class="actions">%s</div></div></section>' % (intro, "".join(hyg_acts)))

    more_section = ('<section><h2>More dashboards</h2>'
      '<p class="h2note">Other live views published from this repo.</p>'
      '<div class="plist"><a class="pcard" href="topic-queue.html"><h3>Ascend Topic Queue →</h3>'
      '<div class="meta">SDM + PM ranked topic backlog joined with live delivery status · '
      'refreshed daily by the update-topic-queue GitHub Action</div></a></div></section>')

    counts = "; ".join("%s %s" % (proj, ", ".join("%s %d" % (pod["name"], pod["devs"]) for pod in PODS[proj]))
                       for proj in ACTIVE)

    body = (
      '<header>'
      '<div class="eyebrow">SDM Queue Management · pulled from Jira {{ASOF}}</div>'
      '<h1>Pod funnel dashboards</h1>'
      '<p class="sub">Goal: every pod holds <strong>3 items per developer</strong> in Ready for Development, '
      'working at <strong>2 items per developer per week</strong>. Open a product dashboard for per-pod detail, '
      'clickable Jira queries, and the product manager note.</p>'
      '<div class="tiles">'
      '<div class="tile"><div class="v bad">−%d</div><div class="k">items behind target across all gapped pods</div></div>'
      '<div class="tile"><div class="v">%.1f h <small>+ %.1f h/wk</small></div><div class="k">refinement: one-time catch-up + weekly to hold pace</div></div>'
      '<div class="tile"><div class="v">%d / wk</div><div class="k">items PMs must move Backlog → To Do weekly (%s)</div></div>'
      '<div class="tile"><div class="v bad">≈%d day</div><div class="k">until the first pod (%s) has an empty To Do queue</div></div>'
      '</div></header>' % (tot_gap, catchup, weekly_h, weekly_i, wk_breakdown, min_run, min_pod) +
      '<section><h2>Products</h2><div class="plist">%s</div></section>' % cards +
      hygiene_section +
      more_section +
      '<footer>Source: Jira (trekbikes.atlassian.net), pulled {{ASOF}}. Member counts confirmed by Thomas '
      '(%s). Method + item lists: SDM-Queue-Management/progress/funnel-analysis-2026-07-08.md.</footer>' % counts)
    return page("Pod Funnel Dashboards · {{ASOF}}", body)

if __name__ == "__main__":
    out = os.path.join(ROOT, "docs")
    os.makedirs(out, exist_ok=True)
    for proj in ACTIVE:
        fn = os.path.join(out, PROJ_META[proj]["file"])
        open(fn, "w", encoding="utf-8").write(product_page(proj))
        print("wrote", fn)
    fn = os.path.join(out, "index.html")
    open(fn, "w", encoding="utf-8").write(parent_page({k: v["file"] for k, v in PROJ_META.items()}))
    print("wrote", fn)
    pending = [p for p in DATA["projects"] if p not in ACTIVE]
    if pending:
        print("skipped (dev counts pending):", ", ".join(pending))
