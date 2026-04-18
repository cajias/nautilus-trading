## Current: <task-id> — <task-title>
PR: <N>/8 · Attempt: 0 · Assigned: Planner · Updated: <ISO-8601 UTC>

### Brief
_Planner writes the task brief here: files to touch, step list, acceptance criteria. Pings Worker when done._

### Attempt
_Worker writes attempt records here (append-only). Each record: timestamp, files changed, test command output summary. Pings Reviewer when attempt is ready._

### Verdict
_Reviewer writes `ACCEPT` or `REJECT` here with reasons. On REJECT with Attempt < 2, pings Worker for retry. On REJECT with Attempt == 2, pings Planner to escalate. On ACCEPT, pings Integrator._

### History
_Integrator appends a one-line record after each ACCEPT rotation: `- <task-id> — ACCEPT on attempt <N> — commit <short-sha>`._
