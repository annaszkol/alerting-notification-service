# Flexible Alerting & Notification Service — specyfikacja i podział pracy

Nov 20, 2026 · @Szymon

Wersja 2, po zewnętrznym przeglądzie. Cały stan trzymamy w Postgresie (bez Redisa), przetwarzanie jest idempotentne, a zakres zmniejszony do tego, co oceniają sędziowie. Wszyscy czytają sekcje 1, 2, 12 i 13. Dalej A czyta 3–5, B czyta 6–7, a C czyta 9; reszta służy jako materiał referencyjny.

## 1. Słownik

| Termin | Znaczenie |
| --- | --- |
| Źródło | Skonfigurowane wejście: endpoint HTTP, topic Kafki albo plik logów. |
| Event | Jedna wiadomość ze źródła, w dowolnym schemacie. Dostaje `$eventId` przy wejściu. |
| Mapowanie | Konfiguracja, która rozpoznaje schemat i wyciąga z niego pola `$`. Dodawane bez kodu. |
| Letter | Event po normalizacji: pola `$` plus nienaruszony surowy payload. |
| Reguła | Warunek + routing + prezentacja + dedup + runbooki. Właściciel: team albo osoba. |
| Alert | Wynik dopasowania eventu do reguły. Ma stan, licznik, first/last seen. Jedna reguła = osobny alert. |
| Fingerprint | Klucz deduplikacji: hash z id reguły i wybranych pól (plus okno czasu w trybie `window`). |
| Klucz korelacji | `$correlationKey`; łączy event „firing” z późniejszym „resolved”. |
| Notyfikacja | Jedna dostawa alertu do jednej osoby jednym kanałem; wiersz w tabeli-outboxie. |
| Kanał | UI, email, SMS, telefon; każdy ma adapter z tym samym interfejsem. |
| Dyżur (on-call) | Rotacja w teamie; odbiorca `oncall: team` = osoba, która ma teraz dyżur. |
| Runbook | Instrukcja: oryginał (YAML, Markdown, checklista, tekst, link) plus postać kanoniczna, czyli lista kroków. |
| Akcja | Wtyczka wykonująca krok runbooka, z minimalnym poziomem zatwierdzenia. |
| Zatwierdzenie | Decyzja uprawnionej osoby o wykonaniu kroku; z UI, linku w emailu albo SMS-em z kodem. |
| Role | `admin` (źródła, mapowania, ludzie), `manager` (reguły, runbooki, dyżury i zatwierdzenia swojego teamu), `member` (alerty, kroki, własne preferencje i reguły). |

## 2. Architektura

Stan jest w dwóch miejscach: Kafka buforuje surowe eventy, a Postgres trzyma całą resztę i jest jedynym źródłem prawdy. Procesy przetwarzające są bezstanowe i skalują się przez dodanie instancji. Jedynym wyjątkiem jest tailer plików logów: działa jako jedna instancja na plik i trzyma offset w Postgresie.

&#91;embedded content: architektura · od źródła do kanału\]

Odpowiedzi SMS (`ACK`, `APPROVE`) wracają z mocka kanałów do API. Redisa celowo nie ma: byłby szybszy przy duplikatach, ale wprowadza drugi stan, który może się rozjechać z bazą. Dokładamy go dopiero wtedy, gdy load test pokaże, że wąskim gardłem jest Postgres.

1. **Adaptery** zamieniają wejście na kopertę `{eventId, sourceId, receivedAt, contentType, payload}` na topicu `events.raw`. HTTP odpowiada `202` dopiero po potwierdzeniu zapisu przez Kafkę. Adapter Kafki czyta zewnętrzny topic. Tailer czyta nowe linie pliku. Nowy typ źródła to nowy mały adapter; nowe źródło istniejącego typu to wiersz w tabeli, bez kodu.
2. **Processor** pobiera paczkę eventów z Kafki i przetwarza ją w jednej transakcji (szczegóły w sekcji 5). Offset commituje dopiero po commicie transakcji.
3. **Delivery workers** pobierają notyfikacje przez `SELECT … FOR UPDATE SKIP LOCKED` i wysyłają je przez adaptery kanałów (sekcja 6).
4. **Executor + scheduler** wykonuje kroki runbooków i zadania cykliczne (sekcje 5 i 7).
5. **API + WebSocket** obsługuje REST dla UI i automatyzacji. Każda instancja nasłuchuje `LISTEN alerts_changed` i wypycha zmiany do przeglądarek. `NOTIFY` jest dostarczane dopiero po commicie, więc UI nigdy nie zobaczy wycofanej zmiany. W treści powiadomienia wysyłamy tylko id, bo limit wynosi 8000 bajtów.
6. **Mocki:** Mailpit (SMTP + UI), fake SMS/phone gateway (UI, odpowiedzi, przełącznik „awaria”), serwisy docelowe akcji, generatory eventów.

| Warstwa | Wybór |
| --- | --- |
| Broker | Redpanda (API zgodne z Kafką), jeden węzeł |
| Baza | PostgreSQL |
| Backend | jeden język dla wszystkich procesów, do decyzji (sekcja 14) |
| Frontend | React + Vite |
| Uruchomienie | `docker compose up` + seed |

## 3. Letter i mapowania

Letter to kilka pól `$` plus nienaruszony payload. Reguły mogą filtrować po obu, więc event w nieznanym schemacie da się obsłużyć od razu, a mapowanie tylko poprawia pola `$`. Prefiks `$` chroni przed kolizją z polami payloadu (sample events mają własne `severity`, `type`, `meta`).

| Pole | Skąd | Gdy brak |
| --- | --- | --- |
| `$eventId`, `$sourceId`, `$sourceType`, `$receivedAt` | adapter | — (zawsze są) |
| `$schema` | nazwa mapowania albo pole (`schemaFrom`) | `unmapped` |
| `$service`, `$instance`, `$environment` | mapowanie | null |
| `$severity`: `info` < `warning` < `error` < `critical` | mapowanie + tabela wartości; oryginał w `$severityRaw` | domyślna severity źródła (`info`) |
| `$status`: `firing` / `resolved` | mapowanie | `firing` |
| `$eventTime` | mapowanie (ISO 8601, epoch ms lub s) | `$receivedAt` |
| `$summary` | szablon | pierwsze 200 znaków payloadu |
| `$correlationKey` | szablon | null (brak auto-zamykania eventem) |

**Ścieżki** (w regułach, mapowaniach i szablonach): `error.code` oznacza pole `code` w obiekcie `error`; segment liczbowy indeksuje tablicę (`alerts.0.labels`); klucz zawierający kropkę zapisujemy w nawiasach: `labels["app.kubernetes.io/name"]`. Dzięki temu filtr działa na naprawdę dowolnym polu.

**Mapowanie** działa w czterech krokach:

1. Parsowanie: domyślnie JSON, dla linii tekstu `logfmt` albo `regex` z nazwanymi grupami (ustawiane w konfiguracji źródła).
2. Rozpoznanie: mapowania mają priorytet, opcjonalnie listę źródeł i warunek `detect` w języku reguł. Wygrywa pierwsze pasujące.
3. Rozbicie wsadu: `splitOn: alerts` zamienia tablicę na osobne eventy. Prawdziwy Alertmanager wysyła `{"alerts": […]}`, a sample to pojedynczy alert; obsługujemy oba kształty.
4. Brak dopasowania: event idzie dalej jako `unmapped`, a próbka trafia do widoku „Niezmapowane” (grupowanie po zbiorze kluczy). W UI klikamy pola w drzewie JSON, przypisujemy je do pól `$` z podglądem na żywo i zapisujemy. Nie trzeba pisać kodu.

```yaml
- name: workflow-event
  detect: { all: [ { type: { exists: true } }, { workflowId: { exists: true } } ] }
  fields:
    $schema: { const: workflow }
    $service: workflowName
    $environment: environment
    $severity: { from: severity, map: { INFO: info, WARN: warning, ERROR: error, CRITICAL: critical } }
    $eventTime: { from: timestamp, format: iso8601 }
    $summary: "{{workflowName}} / {{step}}: {{error.message}}"
    $correlationKey: "{{workflowId}}"

- name: metric-alert
  detect: { all: [ { alertname: { exists: true } }, { labels: { exists: true } } ] }
  splitOn: alerts
  fields:
    $schema: { const: metric-alert }
    $service: labels.service
    $instance: labels.instance
    $environment: labels.environment
    $severity: { from: labels.severity, map: { info: info, warning: warning, critical: critical } }
    $status: { from: status, map: { firing: firing, resolved: resolved } }
    $eventTime: { from: startsAt, format: iso8601 }
    $summary: "{{alertname}}: {{annotations.summary}}"
    $correlationKey: "{{alertname}}|{{labels.service}}|{{labels.region}}"

- name: business-event
  detect: { meta.schema: { exists: true } }
  schemaFrom: meta.schema          # np. trade.rejected.v2
  fields:
    $service: meta.producer
    $eventTime: { from: meta.ts, format: epoch_ms }
    $summary: "Trade {{payload.tradeId}} rejected: {{payload.reason}}"
    $correlationKey: "{{payload.tradeId}}"

- name: pricing-log
  sources: [pricing-logs]          # źródło typu logfile, parser logfmt
  detect: { level: { exists: true } }
  fields:
    $service: service
    $severity: { from: level, map: { DEBUG: info, INFO: info, WARN: warning, ERROR: error } }
    $eventTime: { from: ts, format: iso8601 }
    $summary: "{{service}} [{{instrument}}]: {{msg}}"
```

W mapowaniu wartość to ścieżka, `{ const: … }` to stała, a tekst z `{{…}}` to szablon. Format linii logów pricing-engine wymyślamy sami (to mock) i opisujemy go w README, np. `ts=2026-10-23T09:14:02.331Z level=WARN service=pricing-engine-2 instrument=EURUSD msg="quote latency high"`.

**Szablony.** Własny minimalny templater bez wyrażeń i bez wykonywania kodu. `{{ścieżka}}` czyta payload, `{{$pole}}` czyta letter, brakujące pole renderuje się jako `—`. Pola stałe alertu renderujemy raz, z pierwszego eventu. **Pola na żywo** (`{{$count}}`, `{{$state}}`, `{{$alertUrl}}`; `$state` to stan alertu, nie `$status` eventu) renderujemy przy każdym wyświetleniu i wysyłce, więc licznik w tytule nigdy się nie zestarzeje. Dlatego alert przechowuje tytuł i treść po pierwszym przebiegu, z nietkniętymi polami na żywo.

## 4. Reguły

Reguła ma pięć części: `when`, `route`, `present`, `dedup` (plus opcjonalne `autoResolveAfter`) i `runbooks`. Każda pasująca reguła tworzy własny alert. W bazie i w API reguła jest **jednym dokumentem JSON**; UI edytuje go formularzem, a YAML służy tylko do plików seeda i przykładów w tym dokumencie (loader zamienia YAML na JSON w jedną stronę).

**Warunek:** kombinatory `all` (AND), `any` (OR) i `not`, z dowolnym zagnieżdżaniem. Liść ma postać `ścieżka: wartość` (równość) albo `ścieżka: { operator: wartość }`. Blok `matches:` z runbooka w treści zadania to ten sam zapis.

| Operator | Przykład |
| --- | --- |
| równość, `ne` | `payload.desk: rates` |
| `gt`, `gte`, `lt`, `lte` | `payload.notional: { gt: 10000000 }`; na `$severity` według porządku severity |
| `in`, `not_in` | `error.code: { in: [TIMEOUT, REJECTED] }` |
| `glob` (`*`, `?`) | `$schema: { glob: "trade.rejected.*" }` |
| `regex`, `contains` | `error.message: { contains: "timeout" }` |
| `exists` | `labels.region: { exists: true }` |

Brakujące pole albo zły typ daje `false` (poza `exists: false` i `not`); liczby zapisane jako string porównujemy jak liczby. Ewaluacja nigdy nie rzuca wyjątku. Filtr po źródle to zwykły warunek na `$sourceId` albo `$sourceType`.

**Routing:** odbiorca to `user: anna`, `team: rates`, `oncall: settlements` albo `{ team: settlements, role: manager }`. Kanały to lista (`[phone, sms]`), harmonogram (`schedule` + `otherwise`, w strefie czasowej odbiorcy) albo `preferences` (preferencje odbiorcy według severity). Kanał z reguły wygrywa z preferencją. Alert zawsze trafia do feedu UI właściciela, więc „UI only” to `route: []`.

**Prezentacja:** szablon tytułu i treści, pola pokazywane w karcie, kolor i opcjonalne nadpisanie severity (np. dla eventów biznesowych bez severity).

```yaml
- rule: settlements-prod-failures
  owner: { team: settlements }
  when: { all: [ { type: WORKFLOW_FAILED }, { workflowName: order-settlement }, { $environment: prod } ] }
  route: [ { to: { oncall: settlements }, channels: [phone, sms] } ]
  present: { title: "Settlement failed: {{workflowId}}", body: "Step {{step}}: {{error.message}}", fields: [step, error.message], color: red }
  dedup: { key: [workflowId], mode: until_resolved }

- rule: pricing-warnings
  owner: { team: pricing }
  when: { all: [ { $service: { glob: "pricing-engine-*" } }, { $severity: { gte: warning } } ] }
  route: []                                   # tylko UI
  present: { title: "{{instrument}}: {{$count}} ostrzeżeń z pricing-engine", fields: [$service, msg] }
  dedup: { key: [instrument], mode: window, window: 5m }
  autoResolveAfter: 30m

- rule: rates-large-rejections
  owner: { team: rates }
  when: { all: [ { $schema: { glob: "trade.rejected.*" } }, { payload.desk: rates }, { payload.notional: { gt: 10000000 } } ] }
  route: [ { to: { team: rates }, channels: [email] } ]
  present: { title: "Rejected {{payload.currency}} {{payload.notional}} — {{payload.reason}}" }
  dedup: { key: [payload.tradeId], mode: until_resolved }

- rule: anna-payments-critical
  owner: { user: anna }
  when: { all: [ { $service: payments-api }, { $severity: critical } ] }
  route: [ { to: { user: anna }, channels: { schedule: [ { between: "08:00-20:00", use: [sms] } ], otherwise: [email] } } ]
  runbooks: [high-error-rate-payments]
```

Sprawdzenie na sample events. Event workflow pasuje do pierwszej reguły. Event biznesowy pasuje do trzeciej (25 000 000 > 10 000 000), a tytuł wyrenderuje się jako „Rejected EUR 25000000 — LIMIT\_BREACH”. Webhook `HighErrorRate` pasuje do czwartej przez mapowanie `labels.service` i `labels.severity`.

**Zmiany w runtime:** zapis przez API waliduje ścieżki i szablony, ustawia `updated_by`, podbija `config_version` i wysyła `NOTIFY config_changed`. Processory przeładowują reguły po komunikacie, a dodatkowo co 5 s sprawdzają `config_version`. Nie ma historii wersji ani rollbacku. Nie ma też przeładowania plików, więc na prezentacji mówimy „zmiany przez API i UI”.

**Tester reguły w UI:** wklejasz albo wybierasz event i widzisz letter, wynik każdego warunku, wyrenderowany tytuł oraz faktycznych odbiorców i kanały na teraz.

## 5. Alerty: przetwarzanie, dedup, cykl życia

Paczka eventów jest przetwarzana w jednej transakcji, a offset Kafki commitujemy dopiero po niej. Efekt w bazie występuje więc dokładnie raz: po awarii transakcja jest albo w całości zapisana (i powtórka ją pominie), albo w całości wycofana (i powtórka ją wykona). 500 identycznych błędów daje dokładnie `count = 500`, także przy replayu i przy wielu instancjach processora.

### Algorytm dla jednej paczki

1. `BEGIN`.
2. `INSERT INTO processed_events (event_id) … ON CONFLICT DO NOTHING RETURNING event_id`. Eventy, których id nie wróciło, były już przetworzone, więc je pomijamy.
3. W pamięci: mapowanie, dopasowanie reguł, wyliczenie fingerprintów. Eventy `firing` grupujemy po fingerprincie: liczba wystąpień `n`, ostatni event, najpóźniejszy `$eventTime`.
4. Dla każdego fingerprintu, w kolejności posortowanej (stała kolejność blokad zmniejsza ryzyko deadlocku):
   1. `INSERT INTO alerts (…, count) VALUES (…, n) ON CONFLICT (fingerprint) WHERE state <> 'resolved' DO NOTHING RETURNING id`. Zwrócony wiersz oznacza nowy alert: w tej samej transakcji powstają notyfikacje (`plan_notifications`), wykonanie runbooka i wpis w audit logu.
   2. Brak wiersza oznacza, że alert już jest otwarty: `UPDATE alerts SET count = count + n, last_seen = now(), last_event_time = greatest(last_event_time, …), last_event = … WHERE fingerprint = … AND state <> 'resolved'`. Jeśli UPDATE nie trafił w żaden wiersz (alert zamknięto w międzyczasie), wracamy do kroku 1 (maks. 3 próby).
5. Eventy `resolved`: `UPDATE alerts SET state = 'resolved', resolved_how = 'event' WHERE correlation_key = … AND state <> 'resolved' AND last_event_time <= …` (czas eventu resolved). Warunek na czas sprawia, że kolejność eventów w paczce nie ma znaczenia: alert, który odpalił ponownie po „resolved”, zostaje otwarty. Eventy `resolved` nie tworzą nowych alertów.
6. `NOTIFY alerts_changed` z id zmienionych alertów, `COMMIT`, commit offsetu w Kafce.

Każdy błąd transakcji, w tym deadlock wykryty przez Postgresa, wycofuje całą paczkę, a my ją ponawiamy; dzięki krokom 2 i 4 jest to bezpieczne. Wiersze `processed_events` usuwa scheduler po 24 h, więc celowy replay topicu starszy niż doba zostanie policzony ponownie (zapisujemy to w README).

### Fingerprint i grupowanie

`fingerprint = hash(ruleId, wartości pól z dedup.key)`. Domyślny klucz to `$correlationKey`, a gdy go nie ma, `$service` + `$summary`. W trybie `window` do hasha dochodzi początek okna, `floor($eventTime / window)`: okna są wyrównane do zegara, więc każda instancja wylicza to samo bez koordynacji, a każde okno ma osobny alert. Jedyną gwarancją „jeden otwarty alert na fingerprint” jest unikalny indeks częściowy `ON alerts (fingerprint) WHERE state <> 'resolved'`.

### Cykl życia

Stany to `open` → `acknowledged` → `resolved`. Zamknięty alert nie jest otwierany ponownie; kolejny event tworzy nowy alert z polem `previous_alert_id`. Każda zmiana stanu trafia do audit logu i do UI.

| Przejście | Wyzwalacz |
| --- | --- |
| → `acknowledged` | UI; SMS `ACK <kod>`; „wciśnij 1” w mocku telefonu |
| → `resolved`, ręcznie | UI, z opcjonalną notatką |
| → `resolved`, eventem | krok 5 algorytmu |
| → `resolved`, runbookiem | krok z `resolves: true` zakończony sukcesem |
| → `resolved`, ciszą | scheduler: `autoResolveAfter` bez nowych eventów (alerty z logów nie mają eventu „resolved”) |

### Scheduler

Działa w procesie executora, co 10 s. Każde zadanie to jedno idempotentne polecenie SQL, więc może działać na wielu instancjach jednocześnie bez koordynacji.

| Zadanie | Polecenie |
| --- | --- |
| Zamykanie po ciszy | `UPDATE alerts SET state = 'resolved', resolved_how = 'silence' WHERE state <> 'resolved' AND auto_resolve_after IS NOT NULL AND last_seen < now() - auto_resolve_after RETURNING id` + `NOTIFY` |
| Sprzątanie idempotencji | `DELETE FROM processed_events WHERE processed_at < now() - interval '24 hours'` |

Kroki runbooków z czekaniem (`next_run_at`) obsługuje sam executor (sekcja 7).

## 6. Dostawa

Notyfikacje powstają w tej samej transakcji co alert, a wysyła je osobny worker. Awaria kanału, workera albo restart opóźnia wiadomość, ale jej nie gubi. Gwarancja to at-least-once: duplikat jest możliwy tylko wtedy, gdy worker padnie w trakcie wysyłki, a ogranicza go klucz idempotencji (id notyfikacji) przekazywany providerowi.

### `plan_notifications(alert, rule)`, wołane w transakcji processora

1. Odbiorcy: `user`, `team`, `oncall` (osoba z bieżącej rotacji), `{team, role}`.
2. Kanały: z reguły (lista albo harmonogram w strefie czasowej odbiorcy), a przy `preferences` z ustawień odbiorcy według severity. Ustawienia osoby nadpisują domyślne ustawienia teamu.
3. Jedna osoba, jeden kanał, jeden event: `INSERT … ON CONFLICT (user_id, channel, dedup_key) DO NOTHING`, gdzie `dedup_key` to id pierwszego eventu alertu (dla wiadomości o limicie: początek okna; dla prośby o zatwierdzenie: id tej prośby). Osoba w dwóch teamach, których reguły złapały ten sam event, dostaje jedną wiadomość, a alert jest w obu feedach.
4. Throttling per osoba i kanał: `pg_advisory_xact_lock(hashtext(user_id || channel))`, a potem liczba notyfikacji tej osoby na tym kanale w oknie (np. 5 SMS / 10 min). Poniżej limitu: `pending`. Przy pierwszym przekroczeniu: jedna wiadomość `kind = limit` („limit osiągnięty, kolejne alerty tylko w UI”), a bieżąca dostaje `held`. Dalej: `held`. Blokada sprawia, że limit jest dokładny także przy wielu processorach. Domyślnie `critical` przez telefon omija limit.

### Wysyłka

- Pobranie paczki: `UPDATE notifications SET status = 'sending', lease_until = now() + interval '60 seconds', attempts = attempts + 1 WHERE id IN (SELECT id FROM notifications WHERE (status = 'pending' AND next_attempt_at <= now()) OR (status = 'sending' AND lease_until < now()) LIMIT 50 FOR UPDATE SKIP LOCKED) RETURNING *`. Wiersz po padniętym workerze wraca po wygaśnięciu lease.
- Treść renderujemy w chwili wysyłki, więc pola na żywo (`{{$count}}`, `{{$state}}`) są aktualne.
- Wynik: sukces daje `sent`; błąd przejściowy daje `pending` z backoffem 5 s, 10 s, 20 s, … (sufit 5 min, 10 prób, łącznie ok. 25 min); błąd trwały albo wyczerpane próby dają `dead` i czerwony baner przy alercie.
- Statusy: `pending`, `sending`, `sent`, `dead`, `held`.

| Kanał | Mock | Format |
| --- | --- | --- |
| UI | WebSocket | pełny: tytuł, treść, pola, runbook, przyciski |
| Email | Mailpit | pełny + linki zatwierdzeń i link do alertu |
| SMS | fake gateway | maks. 160 znaków: severity, tytuł, link `/a/{id}`, kod |
| Telefon | fake gateway (wpis + przycisk „wciśnij 1”) | krótki tekst do odczytania |

Adapter kanału ma jeden interfejs: `send(notification, message) → sent | retryable_error | permanent_error`. Prawdziwy provider (np. Twilio) to nowa klasa i wpis w tabeli `channels`; reszta systemu się nie zmienia.

### Dyżury, odpowiedzi i linki

- Rotacja teamu: uporządkowana lista osób, długość zmiany, godzina przekazania, strefa czasowa. UI pokazuje, kto ma teraz dyżur.
- **`ACK <kod>`**: kod jest zapisany w notyfikacji, czyli unikalny dla pary (alert, osoba) i ważny tylko z numeru tej osoby.
- **`APPROVE <kod>` / `REJECT <kod>`**: osobny kod dla każdej prośby o zatwierdzenie (wiersz w `approvals`), więc przy kilku krokach w jednym alercie wiadomo jednoznacznie, który krok jest zatwierdzany. Ważny tylko z numeru osoby, która ma wymaganą rolę.
- Email: linki „Zatwierdź” / „Odrzuć” z podpisanym, jednorazowym tokenem z terminem ważności, prowadzące do strony w UI.
- `/a/{id}` przekierowuje do strony alertu (po zalogowaniu).

Scenariusz demo: włączamy „awarię” w fake SMS, kolejka rośnie, wyłączamy awarię, wszystko dochodzi; liczba `dead` = 0.

### Czas pracy (start / stop)

Osoba może włączyć „powiadamiaj mnie tylko, gdy jestem w pracy” i klikać w aplikacji **Start** / **Stop**. Kto tego nie włączy, działa jak dotąd (godziny pracy z preferencji, harmonogramy z reguł).

- Sesje w `work_sessions` (najwyżej jedna otwarta na osobę), zgoda w `work_tracking`. Sesja otwarta dłużej niż 12 h nie liczy się jako aktywna (zapomniany „Stop”), a scheduler ją zamyka (`ended_how = 'auto'`).
- W `plan_notifications` osoba ze zgodą i bez aktywnej sesji dostaje tylko kanał `ui`: alert jest w feedzie, ale bez emaila, SMS-a i telefonu.
- Wyjątki, żeby nikt nie przegapił awarii przez niekliknięty „Start”: odbiorca z `oncall` (dyżurny) dostaje wszystko, a `critical` przez telefon przechodzi zawsze (jak przy throttlingu).
- Prośby o zatwierdzenie kroków runbooka nie zależą od obecności.

## 7. Runbooki

Runbook przechowujemy jako oryginał i jako postać kanoniczną, czyli listę kroków. Import jest deterministyczny i nie wymaga LLM; LLM jest opcjonalnym dodatkiem na koniec. Akcje może wykonywać tylko runbook zatwierdzony przez managera; do tego czasu jest tekstem do przeczytania.

**Kroki kanoniczne:** `text` (ręczny, z checkboxem „wykonane” i zapisem kto i kiedy), `action` (wtyczka z rejestru), `wait`. Każdy krok ma `approval` (`none` albo `required` + rola) i opcjonalnie `attempts`, `on_failure` (id następnego kroku), `resolves: true` oraz `only_if_open: true` (krok pomijany, gdy alert jest już zamknięty). Parametry są szablonami.

| Format | Import |
| --- | --- |
| YAML strukturalny | parser pól `matches` / `check` / `action` / `url` / `approval`. Tekstowe `if` rozpoznajemy z listy fraz („fails twice”, „fails N times” → `attempts` + `on_failure`). Nieznane pole lub fraza = krok `text` do przeglądu. |
| Markdown, lista numerowana | każdy punkt jako krok `text` |
| Checklista `- [ ]` | każdy punkt jako krok `text` z checkboxem |
| Wolny tekst | jeden krok `text` z całą treścią |
| Link (wiki) | link w alercie |
| Opcjonalnie: LLM | zamienia tekst lub Markdown na kroki z akcjami (np. restart `{{$service}}` → `wait 2m` → `notify` dyżurnego pricing z `only_if_open: true`, czyli „jeśli latencja nadal wysoka”), jako propozycja do zatwierdzenia przez managera |

Runbook YAML z treści zadania po imporcie:

```yaml
runbook: settlement-timeout
match: { all: [ { type: WORKFLOW_FAILED }, { error.code: TIMEOUT } ] }
steps:
  - { id: check-counterparty, kind: action, title: "Is counterparty-service healthy?", action: http_get, params: { url: "http://counterparty-service/health" }, approval: none }
  - { id: retry, kind: action, action: retry_workflow, params: { workflowId: "{{workflowId}}" }, approval: none, attempts: 2, resolves: true, on_failure: escalate }
  - { id: escalate, kind: action, action: escalate, params: { team: counterparty-support }, approval: required }
```

Oryginał nie mówi, co zrobić przy nieudanym health checku, więc wynik zapisujemy i pokazujemy, a wykonanie idzie dalej.

**Dopasowanie i wyświetlanie:** runbook jest podpięty do reguły (`runbooks: […]`) albo ma własne `match`; alert pokazuje wszystkie pasujące. Kroki są renderowane z wartościami z eventu: pełne w UI i emailu, w SMS-ie tylko link.

**Executor:**

- Stan w `runbook_runs` i `step_runs`; krok `wait` ustawia `next_run_at`, a executor pobiera gotowe kroki przez `SKIP LOCKED`. Restart wznawia wykonanie od miejsca przerwania.
- Start: przycisk „Uruchom” dla członka teamu. Manager może włączyć `mode: auto`; wtedy kroki z `approval: none` startują same.
- Krok wymagający zatwierdzenia przechodzi w `waiting_approval`, tworzy wiersz w `approvals` z własnym kodem i wysyła prośbę do osób z wymaganą rolą. Odrzucenie kończy wykonanie.
- Każdy krok zapisuje: kto uruchomił, kto zatwierdził, parametry po podstawieniu, wynik i czasy. To odpowiada na wymóg „kto i co zrobił”.

| Akcja | Cel w demo | Minimalne zatwierdzenie |
| --- | --- | --- |
| `http_get` (sukces przy 2xx, tylko adresy z allowlisty) | mock `counterparty-service /health` | brak |
| `retry_workflow` | mock silnika workflow | brak |
| `restart_service` | mock orkiestratora | wymagane |
| `webhook` (POST pod adres z allowlisty) | mock odbiornik | wymagane |
| `open_ticket` | mock ticketów | brak |
| `escalate` (dodaje team do alertu, powiadamia jego dyżurnego) | wewnętrzne | brak |
| `notify` | wewnętrzne | brak |

Runbook może podnieść wymagane zatwierdzenie (jak `escalate` w przykładzie), ale nie może zejść poniżej minimum akcji. Nowa akcja to jedna klasa (`name`, schemat parametrów, minimalne zatwierdzenie, `execute(params, ctx)`) plus rejestracja; edytor w UI buduje formularz z jej schematu.

## 8. Model danych

Wszystko jest w Postgresie. Pełnych eventów nie archiwizujemy: alert trzyma pierwszy i ostatni event, a historię ma Kafka (retencja topicu).

| Tabela | Najważniejsze pola | Ograniczenia i indeksy |
| --- | --- | --- |
| `users` | id, name, email, phone, timezone, is\_admin | seed |
| `teams`, `team_members` | team: id, name; member: team\_id, user\_id, role (`manager` / `member`) | seed |
| `oncall_schedules` | team\_id, rotation (lista user\_id), shift\_length, handoff\_time, timezone |  |
| `channel_preferences` | owner (user lub team), severity → kanały, godziny pracy, limity throttlingu |  |
| `channels` | name, adapter, config, enabled |  |
| `sources` | id, type, config, parser, default\_severity, enabled |  |
| `tailer_offsets` | source\_id, file, offset |  |
| `schema_mappings` | id, priority, source\_ids, detect, schema\_from, split\_on, fields |  |
| `rules` | id, owner, enabled, when, route, present, dedup, auto\_resolve\_after, runbook\_ids, updated\_by, updated\_at |  |
| `config_version` | jedna liczba | podbijana przy zmianie źródeł, mapowań, reguł, runbooków |
| `runbooks` | id, owner team, format, original, steps, match, mode, approved\_by |  |
| `processed_events` | event\_id, processed\_at | PK na event\_id; czyszczone po 24 h |
| `alerts` | id, rule\_id, owner, fingerprint, correlation\_key, state, severity, title, body, count, first\_seen, last\_seen, last\_event\_time, first\_event, last\_event, auto\_resolve\_after, acked\_by/at, resolved\_by/at/how, previous\_alert\_id | unikalny indeks częściowy `(fingerprint) WHERE state <> 'resolved'`; indeks na `correlation_key` |
| `notifications` | id, alert\_id, user\_id, channel, kind (`alert` / `limit` / `approval`), dedup\_key, status, attempts, next\_attempt\_at, lease\_until, last\_error, reply\_code, sent\_at | unikalny `(user_id, channel, dedup_key)`; indeks na `(status, next_attempt_at)` |
| `runbook_runs`, `step_runs` | run: alert\_id, runbook\_id, status, started\_by; step: step\_id, status, params, result, attempt, next\_run\_at, executed\_by, timestamps |  |
| `approvals` | step\_run\_id, required\_role, code, decided\_by, decision, via (`ui` / `sms` / `email`) | unikalny `code` wśród otwartych |
| `unmapped_samples` | shape\_hash, source\_id, sample, count, last\_seen |  |
| `audit_log` | actor, action, target, details, ts | append-only; tylko zmiany stanu alertów, kroki runbooków i zatwierdzenia |
| `suggestions` | id, alert\_id, target (reguła / runbook), text, author, status, decided\_by | „propose fixes” |

## 9. UI i role

UI budujemy w trzech priorytetach; P1 musi działać przed bramką MVP, a P3 robimy tylko z zapasem czasu. Logowanie jest uproszczone (wybór użytkownika z seeda + JWT, opisane w README jako mock). Uprawnienia sprawdzamy w API tylko tam, gdzie mają znaczenie: edycja reguł i runbooków, zatwierdzanie kroków, operacje admina.

| Priorytet | Ekran | Kto |
| --- | --- | --- |
| P1 | Feed alertów na żywo (WebSocket) z filtrami: team, severity, stan | wszyscy |
| P1 | Szczegóły alertu: pola z reguły, licznik, oś czasu, notyfikacje i ich status, runbook z wartościami, ack/resolve | wszyscy |
| P1 | Edytor reguł (formularz AND/OR) z testerem | manager (team), każdy (reguły osobiste) |
| P2 | Niezmapowane eventy i edytor mapowań z podglądem | admin |
| P2 | Wykonanie runbooka: uruchom krok, odhacz krok ręczny, zatwierdź lub odrzuć | member / manager |
| P2 | Runbooki: import, podgląd postaci kanonicznej, zatwierdzenie, `mode: auto` | manager |
| P2 | Własne preferencje kanałów i godziny pracy | wszyscy |
| P3 | Grafik dyżurów | manager |
| P3 | „Zaproponuj zmianę” reguły lub runbooka z poziomu alertu (wymóg „propose fixes”) + lista propozycji dla managera | wszyscy / manager |
| P3 | CRUD użytkowników, teamów i źródeł | admin |

Zanim powstanie P3, te funkcje są dostępne przez API i seed. „Propose fixes” jest w treści zadania, więc jeśli P3 nie wejdzie, minimum to pole „Zaproponuj zmianę” w szczegółach alertu, zapisujące tekst do tabeli `suggestions`.

**Onboarding nowego teamu** (kryterium UX), mierzony stoperem na demo: admin tworzy team i osoby (ekran P3 albo jedno wywołanie API) → manager tworzy regułę i testuje ją na przykładowym evencie → wysyłamy event → alert jest w feedzie i na kanale.

## 10. Wydajność i pomiar

Jury wymaga liczb zmierzonych, nie szacowanych. Przed nagraniem video README i prezentacja muszą więc zawierać: eventy/s, p50/p95/p99 end-to-end, sprzęt, liczbę instancji, czas pomiaru oraz zmierzony czas „fresh clone → demo” i onboardingu teamu. Celu liczbowego nie ustalamy przed pierwszym pomiarem na szkielecie.

**Skalowanie:** processor przez partycje `events.raw` (np. 12, z zapasem), delivery i executor przez `SKIP LOCKED`, API przez load balancer. Agregacja po fingerprincie w paczce sprawia, że burst duplikatów to jeden UPDATE na paczkę, a nie jeden na event.

| Element | Demo | Produkcja (mówimy na prezentacji) | Przy awarii |
| --- | --- | --- | --- |
| Processor, delivery, executor, API | N instancji | autoscaling | rebalans Kafki / wygaśnięcie lease; nic nie ginie |
| Tailer logów | 1 na plik | agent (np. Fluent Bit) wysyła logi prosto do Kafki | po restarcie czyta od zapisanego offsetu |
| Kafka (Redpanda) | 1 węzeł | 3 węzły, replication factor 3 | HTTP ingest zwraca błąd, więc nadawca ponawia |
| Postgres | 1 instancja | primary + replika (managed) | przetwarzanie stoi, eventy czekają w Kafce |

**Pomiar:**

1. Generator: k6 dla HTTP i prosty producer dla Kafki; mieszanka schematów z Appendix A; każdy event niesie `sentAt`.
2. Koniec pomiaru: mock SMS i testowy klient WebSocket zapisują czas odebrania. Latencja = odebranie − `sentAt` (generator i mocki na jednej maszynie, wspólny zegar). Percentyle liczymy z surowych próbek.
3. Metryki Prometheus: eventy/s przyjęte i przetworzone, lag consumera, rozmiar kolejki notyfikacji.
4. Scenariusze: unikalne eventy; burst duplikatów; skalowanie 1 → 2 → 4 processory; awaria kanału i zabicie instancji pod obciążeniem (liczba dostarczonych = liczba oczekiwanych).
5. Skrypty i surowe wyniki trafiają do `loadtest/` w repo.

## 11. Bezpieczeństwo, README, video

**Bezpieczeństwo:**

- Ingest HTTP wymaga tokenu źródła w nagłówku; do tego limit rozmiaru payloadu.
- Akcje HTTP trafiają tylko pod adresy z allowlisty, sprawdzane po podstawieniu szablonu (ochrona przed SSRF).
- Templater nie wykonuje kodu.
- Zatwierdzenia: rola sprawdzana w API; SMS tylko z numeru uprawnionej osoby i z kodem tej prośby.
- Sekrety w zmiennych środowiskowych, w repo tylko `.env.example`.
- LLM (jeśli wejdzie): do modelu idzie tylko treść runbooka, nigdy payload alertu; wynik jest propozycją, którą zatwierdza człowiek.

**Sekcja o narzędziach AI** (wymagana w zgłoszeniu): do czego ich używaliśmy, jak weryfikowaliśmy wynik (testy, review, niezależny przegląd projektu), że nie wklejaliśmy sekretów ani prawdziwych danych.

**README:** quick start (jedna komenda + zmierzony czas), adresy UI i mocków, loginy z seeda; co jest zmockowane; diagram; przepisy krok po kroku (nowe źródło, schemat, reguła, kanał, akcja, runbook); wyniki load testu; znane ograniczenia (jeden węzeł brokera w demo, replay starszy niż 24 h, własny format logów).

| Czas | Video (5:00) | Kryterium |
| --- | --- | --- |
| 0:00–0:30 | Problem i diagram | Presentation, Scalability |
| 0:30–0:50 | Clone → `docker compose up` (przyspieszone), zmierzony czas | UX |
| 0:50–1:40 | Trzy sample events i logi pricing → telefon + SMS, grupowanie w UI, email, SMS Anny | Flexibility |
| 1:40–2:05 | 500 identycznych błędów → jeden alert z licznikiem 500 | Don't flood people |
| 2:05–2:30 | Awaria SMS → retry → 0 utraconych | Niezawodność |
| 2:30–3:15 | Nowy schemat → mapowanie w UI → reguła z testerem → alert, ze stoperem | Flexibility, UX |
| 3:15–4:05 | Runbook YAML: health check, dwa nieudane retry, eskalacja zatwierdzona SMS-em; checklista Markdown odhaczana w UI | Runbooki |
| 4:05–4:40 | Wyniki load testu i wykres skalowania | Throughput / Latency |
| 4:40–5:00 | Użycie AI, bezpieczeństwo, link do repo | Zgłoszenie |

Zgłoszenie: video do Lockboxa w katalogu nazwanym nazwą teamu, obok plik tekstowy z linkiem do repo.

## 12. Podział pracy, repo i branche

A „rozumie wejście i decyduje”, B „dostarcza i działa”, C „pokazuje”. Zakres C jest okrojony do API i UI, bo to on wcześniej był wąskim gardłem: compose i seed przejął B, a load test A (jego kod jest gorącą ścieżką). README pisze każdy w swojej części, scenariusz video wszyscy, a montaż C.

| Osoba | Zakres |
| --- | --- |
| A | adaptery HTTP / Kafka / tailer; silnik warunków i templater (wspólna biblioteka); mapowania i „Niezmapowane”; processor (idempotencja, reguły, dedup, zamykanie eventem); import runbooków; metryki; load test |
| B | szkielet repo i compose; `plan_notifications`, outbox, delivery, adaptery i mocki kanałów; preferencje, dyżury, throttling; odpowiedzi SMS i linki; executor, scheduler, akcje i ich mocki, zatwierdzenia; seed |
| C | REST API, logowanie i role, WebSocket z `LISTEN`, cały frontend według priorytetów z sekcji 9, montaż video |

### Kontrakty (faza 0, razem, przed pierwszą linią logiki)

| Kontrakt | Właściciel |
| --- | --- |
| Koperta eventu, pola letter, składnia ścieżek, JSON warunków, składnia szablonów | A |
| Sygnatura `plan_notifications(alert, rule)`, interfejs adaptera kanału, model kanoniczny runbooka, interfejs akcji | B |
| OpenAPI, format wiadomości WebSocket i `NOTIFY` | C |
| Schemat bazy: każdy pisze migracje swoich tabel (sekcja 8) | wszyscy |

### Repo

```
contracts/           letter, warunki, runbook, openapi.yaml, wiadomości WS/NOTIFY
libs/conditions/     silnik warunków + templater                 (A)
services/ingest/     services/processor/                          (A)
services/delivery/   services/executor/   mocks/                  (B)
services/api/        web/                                         (C)
db/migrations/       pliki z timestampem w nazwie, każdy dodaje własne
deploy/              docker-compose.yml, seed/                    (B)
loadtest/                                                         (A)
```

### Zasady pracy z gitem

- Krótkie branche od `main`: jeden branch to jedna rzecz na kilka godzin, maksymalnie dzień. PR, szybkie review innej osoby, merge, następny branch.
- `main` zawsze działa: `docker compose up` startuje, CI (testy + build) jest zielone. Przed PR robicie rebase na `main`.
- Zależność, której jeszcze nie ma, zastępujecie stubem za interfejsem z kontraktu, zamiast czekać. Przykład: pierwsze `plan_notifications` zwraca „wszyscy członkowie teamu, kanał UI”.
- Zmiana kontraktu idzie osobnym PR-em do `contracts/` i tylko przez dodawanie pól, nigdy przez zmianę istniejących.
- Żadnych długich branchy per osoba mergowanych na końcu.

### Kolejka branchy

Najpierw sekwencyjnie: `chore/repo-skeleton` (B: katalogi, compose z Redpandą, Postgresem i Mailpitem, CI), równolegle z nim `contracts/v1` (wszyscy). Potem trzy kolejki; kto skończy wcześniej, bierze najwyższy niezaczęty branch z cudzej kolejki, który ma już zmergowane zależności, i mówi o tym na czacie.

| # | A | B | C |
| --- | --- | --- | --- |
| 1 | `feat/conditions-templater` | `feat/notifications-outbox` (stub odbiorców, unikalność osób) | `feat/api-auth-crud` |
| 2 | `feat/ingest-http-kafka` | `feat/channels-mocks` | `feat/ws-push` |
| 3 | `feat/processor-mapping` | `feat/preferences-oncall` | `feat/web-feed-alert` |
| 4 | `feat/rules-reload` | `feat/delivery-retry-lease` | `feat/web-rule-editor` |
| 5 | `feat/alerts-dedup-lifecycle` | `feat/seed-four-teams` | `feat/web-runbook-view` |
| 6 | `feat/log-tailer` | — | — |
|  | **bramka v1: testy 1–7, tag `v1-mvp`** |  |  |
| 7 | `feat/unmapped-samples` | `feat/inbound-sms-codes` | `feat/web-mappings-unmapped` |
| 8 | `feat/runbook-import` | `feat/executor-scheduler` | `feat/web-runbook-run-approvals` |
| 9 | `feat/metrics` | `feat/actions-approvals` | `feat/web-preferences` |
| 10 | `feat/loadtest` | `feat/throttling` | `feat/web-p3` |
|  | **bramka v2: testy 8–17, tag `v2`** |  |  |
|  | **bramka v3: testy 18–20, wyniki w README, feature freeze, nagranie** |  |  |

Opcjonalny `feat/runbook-llm` bierze ten, kto pierwszy będzie wolny po bramce v2.

## 13. Testy akceptacyjne

To jest definicja „gotowe”: bramka przechodzi dopiero wtedy, gdy wszystkie jej testy są odhaczone na `main`.

**Bramka v1 (MVP)**

- [ ] 1\. Event workflow z Appendix A → alert Settlements, telefon + SMS do dyżurnego, w UI na czerwono ze `step` i `error.message`.
- [ ] 2\. Linie WARN z `pricing-engine-1` i `pricing-engine-2` dla jednego instrumentu w jednym 5-minutowym oknie → jeden alert, tytuł pokazuje aktualny licznik, brak emaila i SMS-a.
- [ ] 3\. `trade.rejected.v2` z notional 25 000 000 → email do Rates z tytułem „Rejected EUR 25000000 — LIMIT\_BREACH”. Ten sam event z notional 5 000 000 → brak alertu.
- [ ] 4\. `HighErrorRate` critical z `payments-api` → SMS do Anny między 08:00 a 20:00 jej czasu, poza tym email; alert pokazuje runbook Markdown.
- [ ] 5\. Ten sam alert ze `status: resolved` → alert zamyka się sam.
- [ ] 6\. Zmiana reguły w UI działa od następnego eventu, bez restartu.
- [ ] 7\. Reguła z warunkiem `$sourceId` łapie event z tego źródła, a identyczny event z innego źródła już nie.

**Bramka v2 (trudne wymagania)**

- [ ] 8\. 500 identycznych eventów w minutę przy dwóch processorach → jeden alert, `count = 500`, jedna seria notyfikacji.
- [ ] 9\. Ponowne odczytanie topicu od początku (w ciągu 24 h) → liczniki bez zmian, żadnych nowych notyfikacji.
- [ ] 10\. Fake SMS w trybie awarii przez 2 min → po powrocie wszystko dostarczone, zero `dead`.
- [ ] 11\. Nieznany schemat → „Niezmapowane” → mapowanie i reguła w UI bez kodu → alert. Do tego event z kluczem zawierającym kropkę filtrowany przez `["…"]`.
- [ ] 12\. Runbook YAML: health check wykonany, dwa nieudane retry (mock ustawiony na błąd), eskalacja czeka na zatwierdzenie, `APPROVE <kod>` SMS-em ją wykonuje; oś czasu pokazuje autorów. Kod jednej prośby nie zatwierdza innego kroku.
- [ ] 13\. Checklista `- [ ]` i wolny tekst → kroki ręczne; odhaczenie zapisuje osobę i czas.
- [ ] 14\. Osoba w dwóch teamach, event pasujący do reguł obu → dwa alerty, jeden SMS.
- [ ] 15\. Przekroczenie limitu SMS-ów → dokładnie jedna wiadomość „limit osiągnięty”, kolejne alerty tylko w UI.
- [ ] 16\. Alert pricing bez nowych eventów przez `autoResolveAfter` → zamyka się sam.
- [ ] 17\. Zabicie processora i delivery workera pod obciążeniem → zero utraconych alertów i notyfikacji.

**Bramka v3 (demo)**

- [ ] 18\. Fresh clone → działające demo jedną komendą; czas zmierzony i wpisany do README.
- [ ] 19\. Wyniki load testu (eventy/s, p50/p95/p99, sprzęt, instancje) w README i w prezentacji.
- [ ] 20\. Onboarding nowego teamu do pierwszego alertu; czas zmierzony.

## 14. Decyzje i co ciąć

Trzy decyzje do podjęcia w fazie 0. Domyślne odpowiedzi obowiązują, jeśli nikt nie zgłosi sprzeciwu.

| Decyzja | Domyślnie | Alternatywa |
| --- | --- | --- |
| Język backendu | ten, w którym cała trójka pisze najszybciej (np. Python + FastAPI) | Java + Spring Boot: wyższa przepustowość, wolniejszy start |
| `critical` przez telefon a throttling | omija limit | podlega limitowi |
| Start runbooków z `approval: none` | ręcznie; `mode: auto` włącza manager | zawsze automatycznie |
| Powiadomienia przy czasie pracy start/stop (§6) | nieaktywny dostaje tylko UI; dyżurny i `critical` przez telefon zawsze; auto-stop po 12 h | brak wyjątków |

**Poza zakresem, pokazujemy jako roadmapę:** Redis jako szybka ścieżka dedupu (gdy load test pokaże, że wąskim gardłem jest Postgres), circuit breaker, kanał zapasowy, eskalacja przy braku potwierdzenia, historia wersji i rollback reguł, przeładowanie plików konfiguracyjnych, automatyczna aktualizacja runbooków na podstawie wykonanych kroków.

**Kolejność cięcia przy braku czasu:** `feat/web-p3` → `feat/runbook-llm` → `feat/throttling` → `feat/web-preferences` (zostaje seed). Wycięcie `feat/throttling` usuwa test 15 z bramki v2. Wycięcie `feat/web-p3` wymaga minimalnego pola „Zaproponuj zmianę” (sekcja 9), które C dokłada w `feat/web-runbook-run-approvals`. Nigdy nie tniemy testów 1–12 ani bramki v3, bo odpowiadają wprost na kryteria oceny.
