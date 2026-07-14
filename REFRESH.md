# REFRESH — regenerate the SDM funnel dashboards from live Jira

> Runbook for Claude Code. Trigger by telling Claude **"refresh the SDM funnel dashboards"**
> (or run on a schedule). Requires the Atlassian connector (Jira MCP) and gh auth as
> thomas-reid-801. Total runtime ≈ 5–10 minutes.

## Step 1 — Pull queue state from Jira

**Preferred: run the deterministic pull script.** It replaces the four manual pulls,
bakes in the full-cursor pagination, the correct `issue.fields.*` paths, per-pod backlog
bucketing, and sum/status/timestamp asserts — i.e. every failure mode the manual pulls
kept hitting (early pagination stop, copied pod result, wrong field path). It reads pod
team ids from `data/funnel-data.json`, so nothing is hardcoded.

```
python generator/pull_jira.py <pull_dir> <asof YYYY-MM-DD>
```

One-time auth (only needed once per machine): create an Atlassian API token at
`https://id.atlassian.com/manage-profile/security/api-tokens`, then either export
`JIRA_EMAIL` + `JIRA_API_TOKEN`, or create `generator/jira_creds.json`
= `{"email": "...", "token": "..."}` (gitignored). Offline sanity check any time:
`python generator/pull_jira.py --selftest`. The script prints each query's page-by-page
progress and a `[last]` marker so a short pull is visible in the log; a bad pull raises
and never reaches build_data.py.

**Fallback: manual subagent pulls** (use only if no API token is available — the MCP runs
on the connector's OAuth session). Cloud: `trekbikes.atlassian.net`. Always exclude subtasks.
**Quote "ASC"** (JQL reserved word). The Jira MCP returns **no total counts** — paginate
(maxResults 100, `pageInfo.endCursor` → `nextPageToken`) until `hasNextPage` is false, even
when a page returns a round number; tally locally; oversized results land in tool-result files
(parse with Python, `encoding='utf-8-sig'`, from `issues[].fields.*`). Fan out one subagent per
query group. Per project (TBN, "ASC"):

1. **Pipeline:** `project = <P> AND status in ("To Do","Analysis","SDM Review","Ready for Development") AND issuetype not in subtaskIssueTypes()`
   with fields `["summary","status","issuetype","assignee","created","updated","parent","components","customfield_12300"]`.
2. **Backlog per pod team** (team IDs are in `data/funnel-data.json` → `pods[].tid`):
   `project = <P> AND team = "<tid>" AND status = "Backlog" AND issuetype not in subtaskIssueTypes()`,
   fields `["created"]`, cap 500/team. Bucket by created: <6mo / 6–12mo / >12mo. Run each pod's
   query separately — do not reuse another pod's result.
3. **TBN only:** `parent = TBN-16487 AND status in ("To Do","Backlog") ORDER BY Rank ASC` —
   count teamless items for the hygiene block.
4. **ASC only:** `project = "ASC" AND status = "Ready for Development" AND team is EMPTY` —
   refresh `hygiene.ascNoteamRfdKeys`.

Either path writes the same four files (`r<MMDD>_{tbn,asc}_{pipeline,backlog}.json`) into
`<pull_dir>`, which Step 2's build_data.py consumes.

## Step 2 — Compute per pod

- Tally per pod (Team title) per status; `noteam` counts = items with empty Team.
- **genuine To Do** = pod Team AND parent exists AND parent status not Done/Closed/Resolved/Cancelled,
  OR the item is a child of TBN-16487. Failures → `flag` list (never silently dropped).
- **auto** = genuine To Do items of type Story/Task/Bug/Defect (keys, for the auto-story queue link).
- `anaOld` / `rfdOld` = oldest item age in days (from `updated`) in Analysis / Ready for Dev.

## Step 3 — Update `data/funnel-data.json`

Update per pod: `todo, genuine, ana, sdm, rfd, back[3], anaOld, rfdOld, auto[], flag[]`;
per project: `noteam`; plus `hygiene.*` and **`asOf`** (today's date). Leave `devs`/`tgt`
alone unless Thomas says membership changed (counts are Thomas-confirmed, not inferred).
Targets are always `3 × devs` — if devs changes, recompute tgt.

## Step 4 — Regenerate and verify

```
python generator/gen_dashboards.py
```

Verify: three files in `docs/`, titles carry the new asOf date, `docs/index.html` links
`tbn.html` / `asc.html`, spot-check one pod's numbers against a Jira JQL.

## Step 5 — Commit and push

```
git add data/ docs/
git commit -m "Refresh funnel data <asOf date>"
git push
```

## Step 6 — Sanity notes for the summary

Report to Thomas: which pods are gapped and by how much, soonest To Do dry-out, changes vs
the previous snapshot (git diff of funnel-data.json shows this directly), and anything odd
(e.g. a pod's flag list growing, no-team pools moving).

## Cadence

- **On demand:** "refresh the SDM funnel dashboards".
- **Scheduled:** a Claude Code routine can run this runbook. With `pull_jira.py` + a stored
  API token the whole pipeline (pull → build → generate → commit) is deterministic and needs
  no Atlassian connector, so it runs cleanly headless. Only the manual-subagent fallback needs
  the connector; if that path is taken headless and the connector is unavailable, stop without
  committing.
- Data-only edits (e.g. corrected dev counts): edit `data/funnel-data.json`, rerun the
  generator, commit — no Jira pull needed.
