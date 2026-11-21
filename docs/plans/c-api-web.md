# Plan osoby C: API, auth, WebSocket, frontend

Właściciel: C. Zakres według spec §12: REST API, logowanie i role, WebSocket z `LISTEN`, frontend według
priorytetów z §9, montaż video. Katalogi: `services/api/`, `web/`, kontrakty OpenAPI / WS / NOTIFY.

Proszę A i B o przejrzenie, zwłaszcza sekcji **Zależności** i **Pytania**. Uwagi zgłaszajcie w komentarzach do PR.

## Faza 0: `contracts/v1-api-ws`

Zgodnie z [contracts/README.md](../../contracts/README.md) każdy wypełnia swoją część na osobnym branchu.

- **`contracts/openapi.yaml`**
  - auth: `POST /auth/login` (wybór użytkownika z seeda, zwraca JWT; mock opisany w README), `GET /me`
  - alerty: `GET /alerts` (filtry: team, severity, state; paginacja kursorem), `GET /alerts/{id}`
    (pola z reguły, licznik, oś czasu z `audit_log`, notyfikacje ze statusem, runbooki),
    `POST /alerts/{id}/ack`, `POST /alerts/{id}/resolve` (opcjonalna notatka), `GET /a/{id}` (redirect)
  - reguły: CRUD `/rules`, `POST /rules/test` (letter, wynik każdego warunku, tytuł, odbiorcy i kanały na teraz)
  - runbooki: `/runbooks` (import, zatwierdzenie, `mode`), `/runbook-runs/...` (uruchom krok, odhacz krok ręczny)
  - zatwierdzenia: `POST /approvals/{id}/approve|reject`, strona dla podpisanego linku z emaila
  - admin i reszta: `/mappings`, `/unmapped`, `/users`, `/teams`, `/sources`, `/oncall`, `/preferences/me`,
    `/alerts/{id}/suggestions`
  - wspólny format błędu `{error: {code, message, details}}`
- **`contracts/ws-messages.md`**
  - klient → serwer: `{type: "subscribe", filters: {team?, severity?, state?}}`
  - serwer → klient: `{type: "alert.upsert", alert: AlertSummary}`; `{type: "ping"}`
  - auth tokenem JWT przy handshake (`/ws?token=…`)
- **`contracts/notify.md`**
  - `alerts_changed`: payload `{"ids": [<alert id>, …]}`, tylko id, bo limit wynosi 8000 B. Przy dużej paczce
    nadawca wysyła kilka NOTIFY. API dociąga wiersze z bazy po id.
  - `config_changed`: payload `{"version": <config_version>}`

Kontrakty A (letter, warunki, szablony) i B (route, runbook, akcje, delivery) tylko referuję, nie zmieniam.

## Kolejka branchy (spec §12)

| # | Branch | Zawartość | Testy |
| --- | --- | --- | --- |
| 1 | `feat/api-auth-crud` | FastAPI z routerami; mock login + JWT (sekret z env); `current_user`, `require_role(team, manager)`, `require_admin`; CRUD reguł z walidacją (`libs/conditions`, do czasu merge stub), `updated_by`, bump `config_version` + `NOTIFY config_changed` w jednej transakcji; odczyt alertów, ack/resolve z `audit_log` i `NOTIFY alerts_changed` | pytest + httpx: role (403/200), bump wersji, ack/resolve zmienia stan i pisze audit |
| 2 | `feat/ws-push` | `/ws`, jeden `LISTEN alerts_changed` na instancję (psycopg async), fan-out z filtrami, ping i reconnect | test DB: wiadomość dociera po commicie, a po rollbacku nie przychodzi nic |
| 3 | `feat/web-feed-alert` | `web/` (React + Vite + TypeScript): login, feed na żywo z filtrami, szczegóły alertu (czerwony baner przy `dead`, runbook Markdown, ack/resolve) | Vitest; §13 nr 1, 2, 4 (część UI) |
| 4 | `feat/web-rule-editor` | formularz AND/OR z zagnieżdżaniem, operatory z §4, route / present / dedup, tester | §13 nr 6, 7 |
| 5 | `feat/web-runbook-view` | postać kanoniczna runbooka z podstawionymi wartościami | |
| | **bramka v1** | | |
| 7 | `feat/web-mappings-unmapped` | „Niezmapowane”, drzewo JSON → pola `$`, podgląd na żywo | §13 nr 11 |
| 8 | `feat/web-runbook-run-approvals` | uruchom krok, odhacz krok ręczny, approve/reject, strona linku z emaila, minimalne „Zaproponuj zmianę” | §13 nr 12, 13 (część UI) |
| 9 | `feat/web-preferences` | preferencje kanałów, godziny pracy | |
| 10 | `feat/web-p3` | dyżury, lista propozycji, CRUD admina | §13 nr 20 |

Na końcu montaż video według harmonogramu z §11.

## Zależności od A i B

- `users`, `teams`, `team_members` są już na `main` (B, `202611211300_users_teams.sql`). API je czyta
  i edytuje, schematu nie zmienia.
- `alerts`, `rules`, `config_version`, `schema_mappings`, `unmapped_samples` (A) oraz `runbooks`,
  `runbook_runs`, `step_runs`, `approvals` (B) jeszcze nie istnieją. Do czasu ich migracji testy API używają
  fixture SQL zgodnej z §8, a kod jest pisany pod kontrakt.
- `libs/conditions` (A): walidacja reguł i renderowanie tytułów w testerze. Do czasu merge używam stuba.
- `plan_notifications` / directory (B): tester reguły pokazuje odbiorców i kanały przez tę samą funkcję,
  żeby UI i processor nie liczyły tego inaczej.
- `deploy/docker-compose.yml` (B): w `feat/web-feed-alert` dojdzie serwis `web`. Zgłoszę go B, zamiast edytować sam.

## Pytania do zespołu

1. **`audit_log`**: kto pisze migrację? Piszą do niego processor (A), executor (B) i API (C). Proponuję, że C
   dodaje tabelę w `feat/api-auth-crud`, bo ack/resolve z UI są pierwszym piszącym.
2. **`config_version` i `NOTIFY config_changed`**: A przeładowuje reguły, C je zapisuje. Proponuję, że tabela
   jest A (razem z `rules`), a API tylko podbija wersję i wysyła NOTIFY w transakcji zapisu.
3. **Nowe zależności** w `pyproject.toml`: `pyjwt` (auth); `pyyaml` tylko jeśli import runbooków przez API
   tego wymaga.
4. **Frontend**: React + Vite + TypeScript. Jeśli nikt nie zgłosi sprzeciwu, zostaje TS.
