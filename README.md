# Digital Workflow Monitor — SDM Funnel Dashboards

Static dashboards showing, per development pod, where the SDLC funnel (Backlog → To Do →
Analysis → SDM Review → Ready for Development) constricts against the goal of **3 items per
developer in Ready for Development**, and exactly what closes each gap.

Owner: Thomas Reid (thomas_reid@trekbikes.com)

## Pages

| Page | Content |
|---|---|
| `docs/index.html` | Parent index — cross-product totals, one card per product, data-hygiene levers |
| `docs/tbn.html` | TBN (Trekbikes.com) — Pods B, C, D, E, I |
| `docs/asc.html` | Ascend — Mt Doom, Neverest, RockyBluff |
| `docs/mbi.html` | MBI (Mobile, iOS + Android) — Pod J |
| `docs/bi.html` | BI (Business Intelligence) — BI Data, Reports |
| `docs/erp.html` | ERP — Dyno-POD, POD$TARS, Team Toddler, Tech Blasters, The PodFathers |
| `docs/crm.html` | CRM — BAPP-CRM, Communications |
| `docs/hris.html` | HRIS — IT-HRIS |
| `docs/plm.html` | PLM — Pod A |
| `docs/topic-queue.html` | Ascend Topic Queue — ranked topic backlog under ASC-9433 |

Every bar, count, and backlog age segment links directly to the Jira JQL behind it.
Each product page carries the four headline tiles (behind target / catch-up + weekly
refinement / weekly PM input / days to bottleneck), per-pod action cards (Auto-story
queue ↗ / To Do Needs Review ↗ / Backlog ↗), and a copyable product manager note.

## How it works

```
data/funnel-data.json       ← the refreshable snapshot (counts, key lists, member counts, asOf)
generator/gen_dashboards.py ← reads the JSON, writes the three pages into docs/
docs/                       ← the published site (self-contained HTML, no build step, no CDN)
```

Regenerate any time with:

```
python generator/gen_dashboards.py
```

## Refreshing the data

See [REFRESH.md](REFRESH.md) — a runbook Claude Code executes end-to-end (pull Jira →
recompute → update JSON → regenerate → commit + push). Run it on demand ("refresh the SDM
funnel dashboards") or on a schedule.

## Model & assumptions

- Target: 3 items per developer in Ready for Development, per pod (Team field `customfield_12300`).
- Burn & replenishment rate: **2 items per developer per week** (also the required weekly
  Backlog → To Do input from product managers).
- Pod refinement: 17.5 minutes per item, whole pod.
- "Genuine To Do" = has a pod Team AND a live parent (parent not Done), or is a child of the
  curated TBN-16487 priority queue. Everything else in To Do is flagged for human review.
- Pod member counts confirmed by Thomas Reid 2026-07-08 (B 4, C 3, D 5, E 7, I 4,
  Mt Doom 5, Neverest 5, RockyBluff 6; TBN Pods F/K excluded — no queue activity).
- Subtasks excluded everywhere. `"ASC"` must stay quoted in JQL (reserved word).

Full method + item-level evidence: `SDM-Queue-Management/progress/funnel-analysis-2026-07-08.md`
(OneDrive workspace).
