# -*- coding: utf-8 -*-
"""Deterministic Jira pull for the SDM funnel dashboards (replaces the manual
subagent pulls in REFRESH.md step 1).

Produces the four files build_data.py consumes, for <pull_dir>/<prefix>*.json:
  r<MMDD>_tbn_pipeline.json   {"items":[...]}
  r<MMDD>_asc_pipeline.json   {"items":[...]}
  r<MMDD>_tbn_backlog.json    {"backlogByTeam":{...}, "priorityQueue":[...]}
  r<MMDD>_asc_backlog.json    {"backlogByTeam":{...}, "noteamRfdKeys":[...]}

Usage:
  python generator/pull_jira.py <pull_dir> <asof YYYY-MM-DD> [--prefix rMMDD_]
  python generator/pull_jira.py --selftest        # offline unit tests, no network

Auth (one-time): create an Atlassian API token at
  https://id.atlassian.com/manage-profile/security/api-tokens
then either export env vars
  JIRA_EMAIL=you@trekbikes.com  JIRA_API_TOKEN=xxxx
or drop generator/jira_creds.json = {"email": "...", "token": "..."} (gitignored).

Design notes:
- Pod team ids come from data/funnel-data.json (single source of truth, same as
  build_data.py preserves). No tids hardcoded here.
- Pagination is driven by the enhanced-search cursor: loop while the response
  hands back a nextPageToken (and isLast is not true, and the page was non-empty).
  There are no totals to trust, so the ONLY stop condition is the cursor running
  out -- this is exactly the round-number early-stop the manual pulls kept hitting.
- Every pull is validated before it is written; a failure raises, so a bad pull
  never reaches build_data.py.
"""
import base64
import calendar
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, "data", "funnel-data.json")
CREDS_FILE = os.path.join(ROOT, "generator", "jira_creds.json")

TARGET_STATUSES = ("To Do", "Analysis", "SDM Review", "Ready for Development")
PIPELINE_FIELDS = ["summary", "status", "issuetype", "assignee",
                   "created", "updated", "parent", "customfield_12300"]
TEAM_FIELD = "customfield_12300"
MAX_PAGES = 60                          # safety backstop (60 * 100 = 6000 issues)

# ---------------------------------------------------------------------------
# pure helpers (exercised by --selftest, no network)
# ---------------------------------------------------------------------------

def minus_months(d, n):
    """Calendar date n months before d, clamping the day to the target month."""
    m = d.month - 1 - n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def bucket_of(created_iso, six, twelve):
    """Age bucket for an ISO datetime string given the 6mo / 12mo cutoff dates."""
    d = date.fromisoformat(created_iso[:10])
    if d >= six:
        return "lt6mo"
    if d >= twelve:
        return "m6to12"
    return "gt12mo"


def team_title(field_value):
    """Extract a Team display title from customfield_12300 (defensive about shape).

    Returns the full title string (e.g. 'IT-TBN-Pod E') or None. build_data.py
    matches pods with team.endswith(pod_name), so the full title is what we want.
    """
    v = field_value
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    if isinstance(v, dict):
        for k in ("title", "name", "value"):
            if v.get(k):
                return v[k]
        return None
    return str(v)


def pipeline_item(issue):
    """Map a raw Jira issue to the pipeline schema build_data.py reads."""
    f = issue["fields"]
    parent = f.get("parent") or {}
    tf = f.get(TEAM_FIELD)
    return {
        "key": issue["key"],
        "summary": f.get("summary"),
        "status": f["status"]["name"],
        "issuetype": f["issuetype"]["name"],
        "team": team_title(tf),
        "teamId": tf.get("id") if isinstance(tf, dict) else None,
        "parentKey": parent.get("key"),
        "parentStatus": (parent.get("fields", {}).get("status") or {}).get("name"),
        "updated": f.get("updated"),
        "created": f.get("created"),
    }


def validate_pipeline(items, label):
    bad_status = [i["key"] for i in items if i["status"] not in TARGET_STATUSES]
    bad_ts = [i["key"] for i in items if not i.get("created") or not i.get("updated")]
    if bad_status:
        raise ValueError("%s: %d items with off-target status, e.g. %s"
                         % (label, len(bad_status), bad_status[:5]))
    if bad_ts:
        raise ValueError("%s: %d items missing created/updated, e.g. %s"
                         % (label, len(bad_ts), bad_ts[:5]))


def bucket_backlog(created_list, six, twelve):
    b = {"lt6mo": 0, "m6to12": 0, "gt12mo": 0}
    for c in created_list:
        b[bucket_of(c, six, twelve)] += 1
    total = len(created_list)
    assert b["lt6mo"] + b["m6to12"] + b["gt12mo"] == total, "bucket sum != total"
    return {"total": total, "ageBuckets": b}


# ---------------------------------------------------------------------------
# network layer
# ---------------------------------------------------------------------------

def load_creds():
    base = os.environ.get("JIRA_BASE", "https://trekbikes.atlassian.net").rstrip("/")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if (not email or not token) and os.path.exists(CREDS_FILE):
        c = json.load(open(CREDS_FILE, encoding="utf-8"))
        email = email or c.get("email")
        token = token or c.get("token")
    if not email or not token:
        sys.exit(
            "No Jira credentials. Set JIRA_EMAIL + JIRA_API_TOKEN in the "
            "environment, or create %s = {\"email\":..., \"token\":...}.\n"
            "Get a token at https://id.atlassian.com/manage-profile/security/api-tokens"
            % CREDS_FILE)
    auth = base64.b64encode(("%s:%s" % (email, token)).encode()).decode()
    return base, auth


def search_all(base, auth, jql, fields, label):
    """POST /rest/api/3/search/jql, following the cursor to exhaustion.

    Returns the full list of raw issues. Prints page-by-page progress so an
    early stop (the historical failure) is visible in the log.
    """
    url = base + "/rest/api/3/search/jql"
    headers = {"Authorization": "Basic " + auth,
               "Accept": "application/json",
               "Content-Type": "application/json"}
    issues, token, pages = [], None, 0
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                page = json.load(resp)
        except urllib.error.HTTPError as e:
            sys.exit("%s: HTTP %s from Jira -- %s" % (label, e.code, e.read()[:400]))
        batch = page.get("issues", [])
        issues.extend(batch)
        pages += 1
        token = page.get("nextPageToken")
        done = page.get("isLast") is True or not token or not batch
        print("  %-22s page %d: +%d (%d total)%s"
              % (label, pages, len(batch), len(issues), "" if not done else " [last]"))
        if done:
            break
        if pages >= MAX_PAGES:
            raise RuntimeError("%s: hit MAX_PAGES=%d without exhausting cursor "
                               "-- aborting rather than write a partial pull"
                               % (label, MAX_PAGES))
        time.sleep(0.15)  # be polite to the API
    return issues


# ---------------------------------------------------------------------------
# the four pulls
# ---------------------------------------------------------------------------

def pull_pipeline(base, auth, proj):
    jql = ('project = "%s" AND status in ("To Do","Analysis","SDM Review",'
           '"Ready for Development") AND issuetype not in subtaskIssueTypes()' % proj)
    raw = search_all(base, auth, jql, PIPELINE_FIELDS, proj + " pipeline")
    items = [pipeline_item(i) for i in raw]
    validate_pipeline(items, proj + " pipeline")
    return {"items": items}


def team_clause(pod):  # single tid -> team = "x"; merged pod -> team in ("x","y")
    ts = pod.get("tids") or [pod["tid"]]
    return ('team = "%s"' % ts[0]) if len(ts) == 1 else ('team in (%s)' % ", ".join('"%s"' % t for t in ts))


def pull_backlog(base, auth, proj, pods, six, twelve):
    by_team = {}
    for pod in pods:
        jql = ('project = "%s" AND %s AND status = "Backlog" '
               'AND issuetype not in subtaskIssueTypes()' % (proj, team_clause(pod)))
        raw = search_all(base, auth, jql, ["created"], "%s %s" % (proj, pod["name"]))
        created = [i["fields"]["created"] for i in raw]
        missing = [i["key"] for i in raw if not i["fields"].get("created")]
        if missing:
            raise ValueError("%s %s: items missing created: %s"
                             % (proj, pod["name"], missing[:5]))
        by_team[pod["name"]] = bucket_backlog(created, six, twelve)
    return by_team


def pull_priority_queue(base, auth, parent):
    jql = ('parent = %s AND status in ("To Do","Backlog") ORDER BY Rank ASC' % parent)
    raw = search_all(base, auth, jql, ["summary", "status", TEAM_FIELD], "%s priority-queue" % parent)
    out = []
    for i in raw:
        tf = i["fields"].get(TEAM_FIELD)
        out.append({"key": i["key"], "team": team_title(tf),
                    "teamId": tf.get("id") if isinstance(tf, dict) else None})
    return out


def pull_noteam_rfd(base, auth, proj):
    jql = ('project = "%s" AND status = "Ready for Development" AND team is EMPTY '
           'AND issuetype not in subtaskIssueTypes()' % proj)
    raw = search_all(base, auth, jql, ["summary"], "%s noteam-RfD" % proj)
    return sorted((i["key"] for i in raw), key=lambda k: int(k.split("-")[1]))


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run(pull_dir, asof_s, prefix):
    asof = date.fromisoformat(asof_s)
    six, twelve = minus_months(asof, 6), minus_months(asof, 12)
    print("asOf %s  (lt6mo>=%s, m6to12>=%s)" % (asof_s, six, twelve))
    base, auth = load_creds()
    data = json.load(open(DATA_FILE, encoding="utf-8-sig"))
    # Pending projects (any pod with no confirmed dev count) are staged but not pulled.
    active = [p for p, pd in data["projects"].items()
              if not any(pod.get("devs") is None for pod in pd["pods"])]
    skipped = [p for p in data["projects"] if p not in active]
    if skipped:
        print("skipping (dev counts pending):", ", ".join(skipped))

    os.makedirs(pull_dir, exist_ok=True)

    def write(name, obj):
        path = os.path.join(pull_dir, prefix + name)
        json.dump(obj, open(path, "w", encoding="utf-8"), indent=2)
        print("wrote", path)

    for proj in active:
        pods = data["projects"][proj]["pods"]
        write(proj.lower() + "_pipeline.json", pull_pipeline(base, auth, proj))
        back = {"backlogByTeam": pull_backlog(base, auth, proj, pods, six, twelve)}
        h = data["projects"][proj].get("hygiene", {})
        if h.get("kind") == "priority-queue":
            back["priorityQueue"] = pull_priority_queue(base, auth, h["parent"])
        elif h.get("kind") == "noteam-rfd":
            back["noteamRfdKeys"] = pull_noteam_rfd(base, auth, proj)
        write(proj.lower() + "_backlog.json", back)

    # cross-check: every configured pod should surface a team title in the pipeline
    # (endswith match, honoring an optional pod["match"]). Warn, don't fail -- a pod
    # can legitimately have zero pipeline items, but a systemic team-shape problem
    # shows up here.
    for proj in active:
        items = json.load(open(os.path.join(pull_dir, prefix + proj.lower()
                                            + "_pipeline.json"), encoding="utf-8"))["items"]
        seen_tids = {i.get("teamId") for i in items}
        titles = {i["team"] for i in items if i["team"]}
        for pod in data["projects"][proj]["pods"]:
            m = pod.get("match", pod["name"])
            pod_tids = set(pod.get("tids") or [pod["tid"]])
            if not (pod_tids & seen_tids) and not any(t.endswith(m) for t in titles):
                print("  NOTE: %s %s matched no pipeline items by team-id or title "
                      "(0 items, or team-field shape changed)" % (proj, pod["name"]))
    print("done. now run: python generator/build_data.py %s %s" % (pull_dir, asof_s))


# ---------------------------------------------------------------------------
# offline self-test
# ---------------------------------------------------------------------------

def selftest():
    # month arithmetic incl. year rollover and end-of-month clamp
    assert minus_months(date(2026, 7, 14), 6) == date(2026, 1, 14)
    assert minus_months(date(2026, 7, 14), 12) == date(2025, 7, 14)
    assert minus_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    six, twelve = date(2026, 1, 14), date(2025, 7, 14)
    # bucket boundaries are inclusive on the newer side
    assert bucket_of("2026-01-14T00:00:00.000-0600", six, twelve) == "lt6mo"
    assert bucket_of("2026-01-13T23:59:59.000-0600", six, twelve) == "m6to12"
    assert bucket_of("2025-07-14T10:00:00.000-0600", six, twelve) == "m6to12"
    assert bucket_of("2025-07-13T10:00:00.000-0600", six, twelve) == "gt12mo"
    # the 2026-07-13 -> 2026-07-14 Mt Doom shift: items dated exactly 2026-01-13
    # move lt6mo -> m6to12 when the cutoff advances one day
    prev6 = date(2026, 1, 13)
    assert bucket_of("2026-01-13T09:00:00.000-0600", prev6, date(2025, 7, 13)) == "lt6mo"
    assert bucket_of("2026-01-13T09:00:00.000-0600", six, twelve) == "m6to12"
    # team-field shapes
    assert team_title(None) is None
    assert team_title("") is None
    assert team_title("IT-TBN-Pod E") == "IT-TBN-Pod E"
    assert team_title({"id": "x", "title": "IT-ASC-Mt Doom"}) == "IT-ASC-Mt Doom"
    assert team_title({"id": "x", "name": "IT-TBN-Pod C"}) == "IT-TBN-Pod C"
    assert team_title({"id": "x"}) is None
    # pipeline mapping + endswith compatibility with build_data pod names
    issue = {"key": "TBN-1", "fields": {
        "summary": "s", "status": {"name": "To Do"},
        "issuetype": {"name": "Story"},
        "customfield_12300": {"id": "t", "title": "IT-TBN-Pod E"},
        "parent": {"key": "TBN-16487", "fields": {"status": {"name": "In Progress"}}},
        "created": "2026-05-01T00:00:00.000-0600",
        "updated": "2026-06-01T00:00:00.000-0600"}}
    it = pipeline_item(issue)
    assert it["team"].endswith("Pod E") and it["parentKey"] == "TBN-16487"
    assert it["parentStatus"] == "In Progress"
    assert it["teamId"] == "t"
    # a pod whose display name differs from its team-title suffix (build_data uses
    # pod["match"]); team title still extracts cleanly here
    assert team_title({"id": "z", "title": "IT-BI-Data"}) == "IT-BI-Data"
    # teamless item -> both team and teamId are None
    noteam = pipeline_item({"key": "BI-9", "fields": {
        "summary": "s", "status": {"name": "To Do"}, "issuetype": {"name": "Story"},
        "created": "2026-05-01T00:00:00.000-0600", "updated": "2026-05-01T00:00:00.000-0600"}})
    assert noteam["team"] is None and noteam["teamId"] is None
    # validation catches bad rows
    try:
        validate_pipeline([{"key": "X-1", "status": "Backlog",
                            "created": "x", "updated": "y"}], "t")
        raise AssertionError("expected status failure")
    except ValueError:
        pass
    try:
        validate_pipeline([{"key": "X-2", "status": "To Do",
                            "created": None, "updated": "y"}], "t")
        raise AssertionError("expected timestamp failure")
    except ValueError:
        pass
    # bucket_backlog sums
    bb = bucket_backlog(["2026-06-01T0", "2025-09-01T0", "2024-01-01T0"], six, twelve)
    assert bb == {"total": 3, "ageBuckets": {"lt6mo": 1, "m6to12": 1, "gt12mo": 1}}
    # team clause: single tid vs merged pod
    assert team_clause({"tid": "a"}) == 'team = "a"'
    assert team_clause({"tids": ["a", "b"]}) == 'team in ("a", "b")'
    print("selftest OK")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit("usage: python generator/pull_jira.py <pull_dir> <asof YYYY-MM-DD> "
                 "[--prefix rMMDD_]   (or --selftest)")
    pull_dir, asof_s = args[0], args[1]
    prefix = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--prefix=")),
                  "r" + asof_s[5:7] + asof_s[8:10] + "_")
    run(pull_dir, asof_s, prefix)


if __name__ == "__main__":
    main()
