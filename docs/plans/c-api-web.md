# Plan osoby C: API, auth, WebSocket, frontend

Właściciel: C. Zakres według spec §12: REST API, logowanie i role, WebSocket z `LISTEN`, cały frontend według
priorytetów z §9, montaż video. Katalogi: `services/api/`, `web/`, kontrakty `contracts/openapi.yaml`,
`contracts/ws-messages.md`, `contracts/notify.md`.

Proszę A i B o przejrzenie, zwłaszcza sekcji **Uzgodnienia z A i B**. Uwagi zgłaszajcie w komentarzach do PR.

## Stan wyjściowy (main, 2026-11-21)

- Na `main`: `users`, `teams`, `team_members`, `notifications`, `channels` (B), `plan_notifications` ze stubem
  odbiorców (B), adaptery kanałów i fake gateway (B). `services/api/app.py` to placeholder z `/healthz`.
- Brak: kontraktów A (letter, warunki, szablony) i C (OpenAPI, WS, NOTIFY); tabel `alerts`, `rules`,
  `config_version`, `schema_mappings`, `unmapped_samples`, `runbooks`, `runbook_runs`, `step_runs`, `approvals`,
  `audit_log`, `suggestions`, `oncall_schedules`, `channel_preferences`, `sources`.
- Ustalone przez kontrakt B i compose: API słucha na `api:8000` z prefiksem **`/api`**
  (`POST /api/inbound/sms` wywołuje fake gateway). Linki w SMS-ach i emailach (`<base>/a/<id>`,
  `<base>/approvals/<token>`) prowadzą do **UI**, więc `<base>` to adres frontendu.

## Decyzje techniczne (domyślne, jeśli nikt nie zgłosi sprzeciwu)

| Temat | Wybór | Dlaczego |
| --- | --- | --- |
| API | FastAPI (już w `pyproject.toml`), psycopg 3 async + `psycopg_pool`, SQL bez ORM | jeden styl z resztą repo, pełna kontrola nad transakcjami |
| Auth | mock login: wybór użytkownika z seeda → JWT HS256 (`pyjwt`), sekret `JWT_SECRET` z env, ważność 12 h | spec §9; README opisuje to jako mock |
| Role | liczone z bazy przy każdym żądaniu (`users.is_admin`, `team_members.role`), nie z tokenu | zmiana roli działa od razu, token zawiera tylko `sub` |
| Frontend | React + Vite + TypeScript, React Router, TanStack Query, Mantine (formularze, tabele) | szybkie formularze edytora reguł |
| Typy API w TS | `openapi-typescript` generuje `web/src/api/schema.d.ts` z `contracts/openapi.yaml` | UI i kontrakt nie rozjadą się po cichu |
| Serwowanie UI | osobny serwis `web`: build Vite + nginx; `/api` i `/ws` proxy do `api:8000`, reszta to SPA | jeden origin (bez CORS), `/a/{id}` i `/approvals/{token}` działają jako trasy SPA |
| `<base>` w linkach | env `PUBLIC_BASE_URL` (domyślnie `http://localhost:3000`) | używa go B w treści SMS i emaili, a API w `$alertUrl` |
| Testy API | pytest + `TestClient`/httpx, baza z `db.testing.temporary_database` | jak w testach B |
| Testy web | Vitest + Testing Library + MSW; jeden scenariusz Playwright dla demo (opcjonalnie) | |

## Faza 0: `contracts/v1-api-ws` (pierwszy PR, przed logiką)

### `contracts/openapi.yaml` (wszystko pod `/api`)

Wspólne: auth `Authorization: Bearer <jwt>`, błąd `{"error": {"code", "message", "details"}}`, paginacja kursorem
`?cursor=&limit=` → `{"items": [...], "nextCursor": null|str}`. Kody: 401 bez tokenu, 403 bez roli, 404, 409
(konflikt stanu, np. ack zamkniętego alertu), 422 (walidacja, w `details` lista `{path, message}`).

| Grupa | Endpointy | Uprawnienia |
| --- | --- | --- |
| auth | `GET /auth/users` (lista do ekranu logowania), `POST /auth/login {userId}` → `{token, user}`, `GET /me` (użytkownik, teamy z rolami, `isAdmin`) | publiczne / zalogowany |
| alerty | `GET /alerts?team=&severity=&state=&q=&cursor=` → `AlertSummary[]`; `GET /alerts/{id}` → `AlertDetail`; `POST /alerts/{id}/ack`; `POST /alerts/{id}/resolve {note?}` | zalogowany |
| alert: oś czasu | `GET /alerts/{id}/timeline` → zdarzenia z `audit_log`, `notifications` (status, kanał, osoba, `last_error`), `step_runs`, `approvals` posortowane po czasie | zalogowany |
| reguły | `GET /rules?owner=`, `GET /rules/{id}`, `POST /rules`, `PUT /rules/{id}`, `PATCH /rules/{id} {enabled}`, `DELETE /rules/{id}`; `POST /rules/test {rule, event \| eventId, sourceId?}` | manager teamu (reguła teamu), właściciel (reguła osobista), admin |
| runbooki | `GET /runbooks`, `GET /runbooks/{id}` (oryginał + kroki), `POST /runbooks/import {format, original, owner}` (podgląd kanoniczny bez zapisu: `?dryRun=true`), `PUT /runbooks/{id}`, `POST /runbooks/{id}/approve`, `PATCH /runbooks/{id} {mode}`, `GET /actions` (rejestr akcji z `params_schema` i `min_approval`) | odczyt: zalogowany; zmiany: manager teamu |
| wykonania | `POST /alerts/{id}/runbook-runs {runbookId}`, `GET /runbook-runs/{id}`, `POST /runbook-runs/{id}/steps/{stepId}/start`, `POST /runbook-runs/{id}/steps/{stepId}/done` (krok `text`) | członek teamu właściciela alertu |
| zatwierdzenia | `GET /approvals?mine=pending`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject`, `GET /approvals/by-token/{token}` (strona z linku), `POST /approvals/by-token/{token} {decision}` | rola wymagana przez krok; token z linku bez logowania |
| mapowania | `GET/POST/PUT/DELETE /mappings`, `POST /mappings/preview {mapping, sample}` → letter, `GET /unmapped` (grupy po `shape_hash`), `GET /unmapped/{shapeHash}` (próbki) | admin |
| preferencje | `GET /preferences/me`, `PUT /preferences/me` (severity → kanały, godziny pracy, strefa) | zalogowany (tylko swoje) |
| dyżury | `GET /oncall?team=` (rotacja + kto ma teraz dyżur), `PUT /oncall/{team}` | odczyt: zalogowany; zmiana: manager |
| propozycje | `POST /alerts/{id}/suggestions {target, targetId, text}`, `GET /suggestions?team=&status=`, `POST /suggestions/{id}/accept\|reject` | zalogowany / manager |
| admin | `GET/POST/PUT/DELETE /users`, `/teams` (POST przyjmuje członków inline: onboarding jednym wywołaniem, §13 nr 20), `/sources` | admin |
| SMS | `POST /inbound/sms` (kształt z `contracts/delivery.md`; logikę pisze B) | sieć wewnętrzna |

Kształty: `AlertSummary {id, ruleId, owner, teams[], state, severity, title (pola na żywo już podstawione),
count, firstSeen, lastSeen, color, hasDeadNotification, version}` oraz
`AlertDetail = AlertSummary + {body, fields: [{path, value}], firstEvent, lastEvent, resolvedHow, ackedBy/At,
resolvedBy/At, previousAlertId, runbooks: [{id, title, steps (wyrenderowane), runId?}]}`.
`version` = `last_seen` + stan; klient ignoruje starszą wersję niż ta, którą już ma.

Wynik `POST /rules/test`: `{letter, mappingName, matched, conditions: <drzewo {node, result, actual}>,
title, body, fingerprint, recipients: [{userId, channels[], reason}], errors[]}`.

### `contracts/ws-messages.md`

- Połączenie: `GET /ws?token=<jwt>` (przeglądarka nie ustawia nagłówków dla WS). Zły token → close `4401`.
- Klient → serwer: `{"type": "subscribe", "filters": {"team"?, "severity"?, "state"?}}` (zastępuje poprzednie),
  `{"type": "pong"}`.
- Serwer → klient: `{"type": "alert.upsert", "alert": AlertSummary}`, `{"type": "ping"}` co 20 s,
  `{"type": "resync"}` (API zgubiło `LISTEN`: klient pobiera listę od nowa przez REST).
- Klient po reconnect (backoff 1 s → 30 s) zawsze robi `GET /alerts` i dopiero potem nakłada wiadomości.

### `contracts/notify.md`

- `alerts_changed`: payload `{"ids": [<alert id>, ...]}`, maksymalnie 500 id w jednym NOTIFY (limit 8000 B).
  Nadawcy: processor (A), executor/scheduler (B), API (C). Zawsze w transakcji zmiany, więc dochodzi po commicie.
- `config_changed`: payload `{"version": <config_version>}`. Nadawca: API przy zapisie reguł, mapowań, źródeł
  i runbooków. Słuchacz: processor (A).

Test kontraktu: `contracts/tests/test_openapi.py` sprawdza, że plik jest poprawnym OpenAPI 3.1, a w kodzie API
(od brancha 1) każdy path z kontraktu ma trasę.

## Uzgodnienia z A i B (blokują konkretne branche)

| # | Temat | Propozycja | Blokuje |
| --- | --- | --- | --- |
| 1 | `audit_log` | C pisze migrację w `feat/api-auth-crud` (`id, ts, actor, action, target_type, target_id, details jsonb`), A i B tylko do niej piszą | 1 |
| 2 | `suggestions` | C pisze migrację (pisze do niej tylko API) | 10 |
| 3 | Ack i resolve | C dostarcza `services/api/domain/alerts.py`: `ack_alert(conn, alert_id, actor, via) -> Result` i `resolve_alert(conn, alert_id, actor, how, note)`. Funkcje piszą `audit_log` i `NOTIFY` w transakcji wywołującego. Handler SMS B (działa w procesie API) woła `ack_alert`, więc UI, SMS i telefon zmieniają stan tym samym kodem | 1, B: `feat/inbound-sms-codes` |
| 4 | Decyzja o zatwierdzeniu | B dostarcza `decide_approval(conn, approval_id, actor, decision, via)` (zmienia `approvals` i `step_runs`, executor podejmuje krok). API woła ją z UI i ze strony linku, handler SMS tak samo | 8 |
| 5 | Token w linku email | B podpisuje i weryfikuje (`sign_approval_token` / `verify_approval_token`, HMAC-SHA256, sekret `APPROVAL_LINK_SECRET`, 24 h, jednorazowy przez `approvals.decided_at`). API tylko woła `verify` | 8 |
| 6 | Tester reguły | A wystawia w `libs/conditions`: `validate_rule(doc) -> errors`, `explain(cond, letter) -> drzewo`, `render(template, letter)`, `render_live(text, live)`, oraz `map_event(envelope, mappings) -> letter`. B: `plan_targets` + directory (już jest) | 1 (stub), 4 |
| 7 | Pola na żywo | API renderuje tytuł przez `render_live` z `{$count, $state, $alertUrl}` przy każdym odczycie i w każdym `alert.upsert` | 2, 3 |
| 8 | `escalate` dodaje team do alertu | potrzebna kolumna `alerts.extra_teams text[]` (A) albo tabela `alert_teams`; feed filtruje po `owner OR extra_teams` | 3 |
| 9 | Kolejność ack i resolve | `ack` tylko z `open`, `resolve` z `open` i `acknowledged`, inaczej 409. Ten sam warunek w SQL (`WHERE state = ...`), więc wyścig z processorem jest bezpieczny | 1 |
| 10 | Compose i CI | C pisze `web/Dockerfile` i `web/nginx.conf`. B dopisuje serwis `web` (port 3000), env `JWT_SECRET`, `PUBLIC_BASE_URL`, `APPROVAL_LINK_SECRET` do compose i `.env.example`. Do CI dochodzi job `web` (`npm ci`, `lint`, `typecheck`, `test`, `build`) | 3 |
| 11 | Nowe zależności | `pyjwt`, `psycopg-pool`, `pyyaml` (import runbooków w API, jeśli A nie wystawi parsera z YAML) | 1 |

## Struktura kodu

```
services/api/
  app.py               # create_app(): routery, pool, lifespan (start/stop listenera)
  settings.py          # env: DATABASE_URL, JWT_SECRET, PUBLIC_BASE_URL
  db.py                # AsyncConnectionPool, dependency `conn` (jedna transakcja na żądanie zapisu)
  auth.py              # JWT, current_user, require_admin, require_team_role(team, "manager")
  errors.py            # wspólny format błędu, handlery 401/403/404/409/422
  domain/              # logika bez HTTP: alerts.py, rules.py, config.py (bump config_version + NOTIFY)
  routers/             # auth, alerts, rules, runbooks, runs, approvals, mappings, admin, preferences, oncall,
                       # suggestions, inbound (cienka trasa → handler B)
  ws.py                # Hub: jeden LISTEN na instancję, subskrypcje z filtrami, ping
  stubs.py             # stuby libs/conditions i tabel A/B do czasu ich merge; usuwane w miarę merge
  tests/
web/
  src/api/             # schema.d.ts (generowane), client.ts, useAlertsFeed.ts (WS + TanStack Query)
  src/pages/           # Login, Feed, AlertDetail, RuleList, RuleEditor, Runbooks, RunbookRun, Approval,
                       # Unmapped, MappingEditor, Preferences, Oncall, Suggestions, Admin
  src/components/      # ConditionBuilder, RouteEditor, JsonTree, StepList, Timeline, SeverityBadge
```

## Kolejka branchy

Szacunki w godzinach pracy. „Gotowe” = testy z kolumny przechodzą w CI, a zmiana działa w `docker compose up`.

### 1. `feat/api-auth-crud` (~6 h)

- Szkielet z sekcji „Struktura kodu”, pool, wspólny format błędu, `/healthz` zostaje.
- Auth: `GET /auth/users`, `POST /auth/login`, `GET /me`; dependency `current_user`, `require_admin`,
  `require_team_role`.
- Migracja `audit_log` (uzgodnienie 1).
- Reguły: CRUD + `PATCH enabled`; walidacja przez `validate_rule` (stub: JSON Schema warunku + `route.schema.json`
  od B), `updated_by`, `updated_at`; bump `config_version` + `NOTIFY config_changed` w tej samej transakcji.
- Alerty: `GET /alerts` z filtrami i kursorem, `GET /alerts/{id}`, `GET /alerts/{id}/timeline`,
  `ack` / `resolve` przez `domain/alerts.py` (uzgodnienia 3 i 9).
- Do czasu migracji A: fixture SQL w testach z tabelami `alerts`, `rules`, `config_version` według §8.

Testy: `test_auth.py` (login, zły i wygasły token → 401, `/me` z rolami); `test_permissions.py` (macierz: member
edytuje regułę teamu → 403, manager → 200, osoba edytuje własną → 200, admin wszędzie); `test_rules.py`
(walidacja → 422 z `details`, zapis podbija `config_version` i wysyła `config_changed`, rollback nie wysyła nic);
`test_alerts.py` (ack → `acknowledged` + wiersz audit + NOTIFY, ack zamkniętego → 409, resolve z notatką,
filtry i paginacja).

### 2. `feat/ws-push` (~4 h)

- `ws.py`: przy starcie aplikacji osobne `AsyncConnection` z `LISTEN alerts_changed`; zebrane id są grupowane co
  ~100 ms, dociągane jednym `SELECT ... WHERE id = ANY(...)` i rozsyłane do klientów, których filtry pasują.
- Utrata połączenia z bazą: ponowne połączenie z backoffem, potem `resync` do wszystkich klientów.
- Ping co 20 s, rozłączanie klientów bez `pong`, kolejka per klient z limitem (wolny klient dostaje `resync`
  zamiast zapchać pamięć).

Testy (`db`): wiadomość dociera po commicie; po rollbacku nic; filtr `team` odcina cudze alerty; 600 id w kilku
NOTIFY → wszystkie dotarły; zły token → close 4401.

### 3. `feat/web-feed-alert` (~8 h) — §13 nr 1, 2, 4 (część UI)

- Vite + TS + Mantine, `openapi-typescript`, ESLint, `web/Dockerfile`, `web/nginx.conf` (uzgodnienie 10).
- Login (lista użytkowników z seeda), token w `localStorage`, wylogowanie.
- Feed: tabela z filtrami team / severity / stan (w URL), kolor z `present.color`, licznik na żywo, wskaźnik
  połączenia WS, nowy alert podświetlony.
- Szczegóły: pola z `present.fields`, licznik, first / last seen, czerwony baner przy `hasDeadNotification`,
  notyfikacje z kanałem i statusem, oś czasu, runbook (Markdown przez `react-markdown`, bez surowego HTML),
  przyciski ack / resolve z notatką.
- Trasy `/a/:id` → `/alerts/:id` (po zalogowaniu wraca pod ten adres).

Testy Vitest: feed nakłada `alert.upsert` na listę i ignoruje starszą `version`; po `resync` pobiera listę od
nowa; filtry zmieniają subskrypcję; baner `dead`; ack wyłącza przycisk i odświeża stan; `/a/7` bez logowania →
login → `/alerts/7`.

### 4. `feat/web-rule-editor` (~8 h) — §13 nr 6, 7

- API: `POST /rules/test` (uzgodnienie 6; event wklejony albo `eventId` z ostatnich alertów, `sourceId`
  do symulacji źródła).
- `ConditionBuilder`: grupy `all` / `any` / `not` z dowolnym zagnieżdżaniem, liść = ścieżka + operator z §4
  (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `glob`, `regex`, `contains`, `exists`), podpowiedzi
  ścieżek z przykładowego eventu (w tym `labels["a.b"]`), pola `$` z listy, w tym `$sourceId`.
- `RouteEditor`: odbiorca `user` / `team` (+ `role`) / `oncall`, kanały: lista, harmonogram + `otherwise`,
  `preferences`; pusty route = „tylko UI”.
- `present` (tytuł, treść, pola, kolor, nadpisanie severity), `dedup` (klucz, `until_resolved` / `window` + okno),
  `autoResolveAfter`, podpięte runbooki.
- Tester obok formularza: letter, drzewo warunków z zielonym / czerwonym wynikiem i faktyczną wartością,
  tytuł, odbiorcy i kanały „na teraz”.
- Podgląd JSON reguły (tylko do odczytu) dla zaawansowanych.

Testy: Vitest dla `ConditionBuilder` (budowa zagnieżdżonego `any` w `all` daje dokładnie JSON z kontraktu A;
odczyt istniejącej reguły i zapis bez zmian = identyczny JSON); pytest dla `/rules/test` (dopasowanie, brak
dopasowania z powodem, `$sourceId`). Test akceptacyjny `test_acceptance_06_rule_change_without_restart` i
`..._07_source_filter` jako skrypty na pełnym stacku, gdy A ma processor.

### 5. `feat/web-runbook-view` (~3 h)

- Szczegóły alertu: postać kanoniczna runbooka z podstawionymi wartościami (`render` A), typ kroku, wymagane
  zatwierdzenie (efektywne: `max(step, action.min)`), `needs_review` jako ostrzeżenie, link dla runbooka-linku.
- Lista runbooków teamu tylko do odczytu.

**Bramka v1**: testy 1–7 na `main`. Przed nią sprawdzam razem z A i B scenariusze 1, 2, 4 i 6 w przeglądarce.

### 7. `feat/web-mappings-unmapped` (~6 h) — §13 nr 11

- API: `/unmapped`, `/mappings` CRUD (bump `config_version`), `/mappings/preview` (A: `map_event`).
- UI: lista grup niezmapowanych (zbiór kluczy, liczba, ostatnio), `JsonTree` z klikaniem pola → wybór pola `$`
  (ścieżka z nawiasami dla kluczy z kropką), mapa wartości severity, szablon `$summary`, warunek `detect`
  (reuse `ConditionBuilder`), podgląd letter na żywo, zapis, przycisk „Utwórz regułę z tego eventu”.

Testy: `JsonTree` generuje `labels["app.kubernetes.io/name"]` dla klucza z kropką; preview zwraca letter;
member dostaje 403.

### 8. `feat/web-runbook-run-approvals` (~7 h) — §13 nr 12, 13 (część UI)

- API: start wykonania i kroku, odhaczenie kroku `text` (zapis osoby i czasu w `step_runs` + audit),
  `approvals` z UI i z tokenu (uzgodnienia 4 i 5), import runbooka z podglądem, zatwierdzenie runbooka,
  `mode: auto`, `GET /actions`.
- UI: panel wykonania w szczegółach alertu (status kroków na żywo przez `alert.upsert` → refetch), checkboxy
  kroków ręcznych, przyciski zatwierdź / odrzuć dla osób z rolą, strona `/approvals/:token` (działa bez
  logowania), ekran importu (wklej YAML / Markdown / checklistę / tekst / link → podgląd → zapisz),
  edytor parametrów kroku z formularzem z `params_schema`.
- Minimum „Zaproponuj zmianę”: pole tekstowe w szczegółach alertu → `POST /alerts/{id}/suggestions` (tu, a nie
  w P3, bo §14 pozwala wyciąć `feat/web-p3`). Migracja `suggestions` (uzgodnienie 2).

Testy: odhaczenie zapisuje `executed_by` i czas; zatwierdzenie bez wymaganej roli → 403; token użyty drugi raz
→ 409; wygasły token → 410; runbook niezatwierdzony nie daje przycisku „Uruchom” akcji.

### 9. `feat/web-preferences` (~3 h)

- API `GET/PUT /preferences/me` na tabeli B (`channel_preferences`), walidacja kanałów i godzin.
- UI: macierz severity × kanał, godziny pracy, strefa czasowa.

### 10. `feat/web-p3` (~6 h) — §13 nr 20

- Dyżury: widok rotacji, „kto teraz”, edycja dla managera.
- Lista propozycji dla managera z akcjami przyjmij / odrzuć.
- Admin: CRUD użytkowników, teamów (z członkami inline), źródeł.
- Scenariusz onboardingu (§9) przećwiczony i zmierzony stoperem, czas do README.

**Bramka v2 / v3**: moje testy UI dla 11–13, ekran onboardingu dla 20, potem feature freeze.

## Pokrycie wymagań

| Wymaganie | Gdzie |
| --- | --- |
| §9 P1 feed na żywo z filtrami | 2, 3 |
| §9 P1 szczegóły alertu (pola, licznik, oś czasu, notyfikacje, runbook, ack / resolve) | 1, 3, 5 |
| §9 P1 edytor reguł z testerem, role | 1, 4 |
| §9 P2 niezmapowane + mapowania | 7 |
| §9 P2 wykonanie runbooka i zatwierdzenia | 8 |
| §9 P2 import runbooków, zatwierdzenie, `mode: auto` | 8 |
| §9 P2 preferencje | 9 |
| §9 P3 dyżury, propozycje, CRUD admina | 10 (minimum propozycji w 8) |
| §4 zmiany w runtime (walidacja, `updated_by`, `config_version`, NOTIFY) | 1, 7 |
| §6 `/a/{id}`, linki zatwierdzeń z emaila, `POST /inbound/sms` | 3, 8, trasa w 1 |
| §11 bezpieczeństwo: role w API, sekrety w env, Markdown bez HTML | 1, 3 |
| §13 nr 1, 2, 4, 6, 7, 11, 12, 13, 20 (część UI) | 3, 4, 7, 8, 10 |
| README: sekcja API (loginy z seeda, mock auth, adres UI), `web/README.md` | w każdym branchu |
| Montaż video (§11) | po bramce v3 |

## Video (C montuje, scenariusz piszą wszyscy)

- Po bramce v2: scenariusz kadr po kadrze według tabeli w §11, z gotowymi skryptami wysyłającymi eventy
  (`deploy/demo/*.sh`, uzgadniam z B).
- Nagranie ekranu w 1080p, przyspieszony `docker compose up` z widocznym czasem, stoper przy onboardingu.
- Montaż do 5:00, napisy z nazwą kryterium w rogu, link do repo na końcu. Wgranie do Lockboxa z plikiem z linkiem.

## Ryzyka

| Ryzyko | Co robię |
| --- | --- |
| Tabele A/B wchodzą później niż moje branche | stuby w `services/api/stubs.py` i fixture SQL według §8; po merge podmieniam i usuwam stub w tym samym PR |
| Edytor reguł jest największy | najpierw `ConditionBuilder` z testem „JSON wejście = JSON wyjście”, potem reszta formularza |
| Brak czasu | kolejność cięcia z §14: `feat/web-p3` → `feat/web-preferences`; minimum „Zaproponuj zmianę” zostaje w 8 |
