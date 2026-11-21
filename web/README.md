# Web UI (owner C; P1 screens built by B while C is out of capacity)

React 18 + Vite + TypeScript, Mantine, TanStack Query, React Router (stack from `docs/plans/c-api-web.md`).
Types are generated from the API contract: `npm run gen:api` → `src/api/schema.d.ts` (commit the result).

| Path | What |
| --- | --- |
| `src/api/client.ts` | `fetch` wrapper: bearer token, error format `{error: {code, message}}`, 401 → login |
| `src/feed/live.tsx` | one WebSocket per tab (`/ws?token=`): subscribe, ping/pong, reconnect 1 s → 30 s, 4401 → login |
| `src/feed/feedState.ts` | pure feed rules from `contracts/ws-messages.md`: newer `version` wins, client-side state filter, REST/WS race |
| `src/feed/useAlertsFeed.ts` | feed = `GET /alerts` page + `alert.upsert` messages; reload on `subscribed` / `resync` |
| `src/pages/Login.tsx` | mock login: pick a seeded user |
| `src/pages/Feed.tsx` | P1 live feed with team / severity / state / title filters |
| `src/pages/AlertDetail.tsx` | P1 alert details: fields, count, ack / resolve, red banner for undelivered notifications, runbooks, notifications, timeline |
| `src/runbooks/RunbookPanel.tsx` | runbook on the alert page: rendered steps, start run, Run / Done / Approve / Reject per step, who did what |
| `src/pages/Approvals.tsx` | approvals I can decide (or all pending), approve / reject |
| `src/pages/ApprovalLink.tsx` | page behind the email link (`/approvals/:token`), no sign-in; 404 / 409 / 410 explained |
| `src/pages/Runbooks.tsx` | runbooks with canonical steps and original text; managers approve, switch auto mode, edit, delete |
| `src/pages/RunbookImport.tsx` | import YAML / numbered list / checklist / text / link, preview the canonical steps (`dryRun`), save |
| `src/pages/RunbookEditor.tsx` | step editor; action parameters as a form from `paramsSchema`; approval never below the action's minimum |
| `src/pages/Rules.tsx`, `RuleEditor.tsx` | rule list (enable / disable) and editor: condition AND / OR / NOT, routing, presentation, grouping, auto-resolve, runbooks, tester next to it |
| `src/rules/` | `condition.ts` (condition JSON <-> form tree), `ConditionBuilder`, `RouteEditor`, `RuleTester`, `ruleDoc.ts` |
| `src/pages/Unmapped.tsx`, `Mappings.tsx`, `MappingEditor.tsx` | admin: unmapped shapes, mappings, editor with clickable JSON tree and live preview |
| `src/pages/Preferences.tsx` | my channels per severity, working hours, time zone |
| `src/pages/Oncall.tsx` | every team's rotation and who is on duty; managers edit their team's rotation |
| `src/pages/Suggestions.tsx` | proposed changes; managers of the owning team accept / reject (proposing is on the alert page) |
| `src/pages/Admin.tsx` | admin: teams (with new people inline = onboarding in one call), people, sources (ingest token shown once) |
| `src/auth/permissions.ts` | who may do what; mirrors `services/api/auth.py` so the UI hides buttons that would end in 403 |

Routes: `/login`, `/` (feed), `/alerts/:id`, `/a/:id` (short link from SMS), `/approvals`, `/approvals/:token`
(email link, no sign-in), `/rules`, `/rules/new`, `/rules/:id` (`?alert=<id>` tests on that alert), `/runbooks`,
`/runbooks/import`, `/runbooks/:id/edit`, `/oncall`, `/suggestions`, `/preferences`, admin: `/unmapped`, `/mappings`,
`/mappings/new`, `/mappings/:id`, `/admin`.

### Who sees what

The API decides (roles are read from the database on every request); the UI only hides what would be refused.

| | member | manager of the team | admin |
| --- | --- | --- | --- |
| Feed, alert, ack / resolve, propose a change | yes | yes | yes |
| Run runbook steps, tick manual steps | own teams' alerts | own teams' alerts | all |
| Approve / reject a step | if the step needs a `member` | yes | only as a team member |
| Rules | personal rules | team rules + personal | all |
| Runbooks: import, edit, approve, auto mode, delete | read only | own team | all |
| On-call rotation | read only | own team | all |
| Decide proposed changes | — | own team's rules / runbooks | all |
| Unmapped events, mappings, teams, people, sources | — | — | yes |

## Run

```sh
docker compose -f deploy/docker-compose.yml up --build    # UI on http://localhost:3000
# or, for development with hot reload (API from compose on :8000):
cd web && npm install && npm run dev                       # http://localhost:3000, proxies /api and /ws
```

Checks (also in CI): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.

Until A's `alerts` table is migrated, the alert endpoints return 500 and the feed shows an error; login works.
