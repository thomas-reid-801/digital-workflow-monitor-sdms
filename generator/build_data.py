# -*- coding: utf-8 -*-
"""Build data/funnel-data.json from raw Jira pulls (refresh step 2-3 of REFRESH.md).

Usage: python generator/build_data.py <pull_dir> <asof YYYY-MM-DD>
Expects in <pull_dir>: r*_tbn_pipeline.json, r*_asc_pipeline.json,
r*_tbn_backlog.json, r*_asc_backlog.json (prefix given by --prefix, default from asof).

Preserves devs/tgt/labels/tids from the existing data file (member counts are
Thomas-confirmed, not derived). Prints an old-vs-new diff per pod.
"""
import json, os, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT, "data", "funnel-data.json")
DONE = {"Done", "Closed", "Resolved", "Cancelled", "Canceled"}
WORKABLE = {"Story", "Task", "Bug", "Defect"}

def has_team(i):
    """Whether an item has a Team assigned. teamId is authoritative and matches
    Jira's `team is EMPTY` semantics; fall back to the title only for older pulls
    that predate teamId. A "hidden" team (id present but empty title, for teams the
    API user can't see) still counts as assigned -- NOT as no-team."""
    if "teamId" in i:
        return i["teamId"] is not None
    return bool(i.get("team"))

def load(p):
    return json.load(open(p, encoding="utf-8-sig"))

def age_days(iso, asof):
    try:
        return (asof - date.fromisoformat(iso[:10])).days
    except Exception:
        return None

def main():
    pull_dir, asof_s = sys.argv[1], sys.argv[2]
    prefix = sys.argv[3] if len(sys.argv) > 3 else "r" + asof_s[5:7] + asof_s[8:10] + "_"
    asof = date.fromisoformat(asof_s)
    data = load(DATA_FILE)
    old = json.loads(json.dumps(data))  # deep copy for diff

    # A project is "pending" until every pod has a confirmed dev count; pending
    # projects are staged in the data file but skipped by the whole pipeline.
    active = [p for p, pd in data["projects"].items()
              if not any(pod.get("devs") is None for pod in pd["pods"])]
    skipped = [p for p in data["projects"] if p not in active]
    if skipped:
        print("skipping (dev counts pending):", ", ".join(skipped))

    pipes = {p: load(os.path.join(pull_dir, prefix + p.lower() + "_pipeline.json"))["items"] for p in active}
    backs = {p: load(os.path.join(pull_dir, prefix + p.lower() + "_backlog.json")) for p in active}

    for proj in active:
        pdata = data["projects"][proj]
        items = pipes[proj]
        pq_keys = {i["key"] for i in backs[proj].get("priorityQueue", [])}
        pdata["noteam"] = {
            "todo": sum(1 for i in items if i["status"] == "To Do" and not has_team(i)),
            "ana": sum(1 for i in items if i["status"] == "Analysis" and not has_team(i)),
            "rfd": sum(1 for i in items if i["status"] == "Ready for Development" and not has_team(i)),
        }
        for pod in pdata["pods"]:
            # Prefer exact team-id match (reliable, equals the configured tid(s)); a
            # merged pod carries multiple tids. Fall back to title-suffix (pod["match"]
            # or name) only for items lacking teamId.
            match = pod.get("match", pod["name"])
            tids = set(pod.get("tids") or [pod["tid"]])
            def mine_of(i, tids=tids, match=match):
                if i.get("teamId"):
                    return i["teamId"] in tids
                return bool(i.get("team")) and i["team"].endswith(match)
            mine = [i for i in items if mine_of(i)]
            by = lambda st: [i for i in mine if i["status"] == st]
            todo = by("To Do")
            genuine, auto, flag = [], [], []
            for i in todo:
                ok = i["key"] in pq_keys or (i.get("parentKey") and (i.get("parentStatus") not in DONE))
                if ok:
                    genuine.append(i)
                    if i["issuetype"] in WORKABLE:
                        auto.append(i["key"])
                else:
                    flag.append(i["key"])
            ana, rfd = by("Analysis"), by("Ready for Development")
            oldest = lambda lst: max((age_days(i["updated"], asof) or 0) for i in lst) if lst else 0
            pod.update(
                todo=len(todo), genuine=len(genuine), ana=len(ana), sdm=len(by("SDM Review")),
                rfd=len(rfd), tgt=3 * pod["devs"],
                anaOld=oldest(ana), rfdOld=oldest(rfd),
                auto=sorted(auto), flag=sorted(flag))
            bt = backs[proj]["backlogByTeam"]
            bkey = next((k for k in bt if k.endswith(pod["name"]) or pod["name"].endswith(k)), None)
            if bkey:
                b = bt[bkey]["ageBuckets"]
                pod["back"] = [b["lt6mo"], b["m6to12"], b["gt12mo"]]

    for proj in active:
        h = data["projects"][proj].get("hygiene", {})
        if h.get("kind") == "priority-queue":
            pq = backs[proj].get("priorityQueue", [])
            h["queueSize"] = len(pq)
            h["teamlessCount"] = sum(1 for i in pq if not has_team(i))
        elif h.get("kind") == "noteam-rfd":
            h["rfdKeys"] = sorted(backs[proj].get("noteamRfdKeys", []),
                                  key=lambda k: int(k.split("-")[1]))
    data["asOf"] = asof_s

    json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), indent=2)
    print("wrote", DATA_FILE, "asOf", asof_s)

    # diff summary
    print("\n=== change vs previous (%s) ===" % old["asOf"])
    for proj in active:
        if proj not in old["projects"]:
            print("%s (new project)" % proj)
            continue
        for np_, op in zip(data["projects"][proj]["pods"], old["projects"][proj]["pods"]):
            if "rfd" not in op:  # pod was pending (no computed fields) last run
                print("%s %-12s (now active)" % (proj, np_["name"]))
                continue
            changes = []
            for f in ("todo", "genuine", "ana", "sdm", "rfd"):
                if np_[f] != op[f]:
                    changes.append("%s %d->%d" % (f, op[f], np_[f]))
            gap_o, gap_n = max(0, op["tgt"] - op["rfd"]), max(0, np_["tgt"] - np_["rfd"])
            if gap_o != gap_n:
                changes.append("GAP %d->%d" % (gap_o, gap_n))
            print("%s %-12s %s" % (proj, np_["name"], "; ".join(changes) if changes else "(no change)"))
        no, nn = old["projects"][proj]["noteam"], data["projects"][proj]["noteam"]
        if no != nn:
            print("%s no-team: %s -> %s" % (proj, no, nn))
        ho = old["projects"][proj].get("hygiene", {})
        hn = data["projects"][proj].get("hygiene", {})
        if hn.get("kind") == "priority-queue":
            print("%s priority queue: %s (teamless %s) -> %d (teamless %d)" % (
                proj, ho.get("queueSize", "?"), ho.get("teamlessCount", "?"),
                hn["queueSize"], hn["teamlessCount"]))
        elif hn.get("kind") == "noteam-rfd":
            print("%s no-team RfD keys: %s -> %d" % (proj, len(ho.get("rfdKeys", [])), len(hn["rfdKeys"])))

if __name__ == "__main__":
    main()
