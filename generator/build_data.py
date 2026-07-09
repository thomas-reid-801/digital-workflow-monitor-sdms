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

    pipes = {p: load(os.path.join(pull_dir, prefix + p.lower() + "_pipeline.json"))["items"] for p in ("TBN", "ASC")}
    backs = {p: load(os.path.join(pull_dir, prefix + p.lower() + "_backlog.json")) for p in ("TBN", "ASC")}

    pq = backs["TBN"].get("priorityQueue", [])
    pq_keys = {i["key"] for i in pq}

    for proj, pdata in data["projects"].items():
        items = pipes[proj]
        pdata["noteam"] = {
            "todo": sum(1 for i in items if i["status"] == "To Do" and not i.get("team")),
            "ana": sum(1 for i in items if i["status"] == "Analysis" and not i.get("team")),
            "rfd": sum(1 for i in items if i["status"] == "Ready for Development" and not i.get("team")),
        }
        for pod in pdata["pods"]:
            mine = [i for i in items if i.get("team") and i["team"].endswith(pod["name"])]
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

    data["hygiene"]["ascNoteamRfdKeys"] = sorted(backs["ASC"].get("noteamRfdKeys", []),
                                                  key=lambda k: int(k.split("-")[1]))
    data["hygiene"]["tbn16487QueueSize"] = len(pq)
    data["hygiene"]["tbn16487TeamlessCount"] = sum(1 for i in pq if not i.get("team"))
    data["asOf"] = asof_s

    json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), indent=2)
    print("wrote", DATA_FILE, "asOf", asof_s)

    # diff summary
    print("\n=== change vs previous (%s) ===" % old["asOf"])
    for proj in data["projects"]:
        for np_, op in zip(data["projects"][proj]["pods"], old["projects"][proj]["pods"]):
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
    ho, hn = old["hygiene"], data["hygiene"]
    print("ASC no-team RfD keys: %d -> %d" % (len(ho["ascNoteamRfdKeys"]), len(hn["ascNoteamRfdKeys"])))
    print("TBN-16487 queue: %d (teamless %d) -> %d (teamless %d)" % (
        ho["tbn16487QueueSize"], ho["tbn16487TeamlessCount"], hn["tbn16487QueueSize"], hn["tbn16487TeamlessCount"]))

if __name__ == "__main__":
    main()
