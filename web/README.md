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

Routes: `/login`, `/` (feed), `/alerts/:id`, `/a/:id` (short link from SMS).

## Run

```sh
docker compose -f deploy/docker-compose.yml up --build    # UI on http://localhost:3000
# or, for development with hot reload (API from compose on :8000):
cd web && npm install && npm run dev                       # http://localhost:3000, proxies /api and /ws
```

Checks (also in CI): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.

Until A's `alerts` table is migrated, the alert endpoints return 500 and the feed shows an error; login works.
