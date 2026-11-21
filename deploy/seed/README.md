# Seed data (owner B)

`people.yaml`: demo users, teams, on-call rotations and channel preferences. Compose runs the one-shot `seed`
service after migrations: `python -m deploy.seed.load`.

| Team | Manager | Members | On call |
| --- | --- | --- | --- |
| settlements | carol | dave, erin | dave → carol → erin, daily 09:00 Warsaw |
| pricing | grace | frank, julia (New York) | frank → julia → grace, weekly 10:00 London |
| rates | bob | anna | — (whole team) |
| payments | erin | anna | anna → erin, daily 09:00 Warsaw |
| counterparty-support | henry | ivan | ivan → henry, daily 09:00 Warsaw |

`admin` is the only admin. Anna is in two teams (spec §13 test 14) and has her own preferences: critical → SMS
08:00–20:00 Warsaw time, otherwise email (test 4). Phones are fake numbers `+4850000000x` for the fake gateway; emails
use the reserved `example.test` domain and land in Mailpit.

- Re-running keeps existing rows (UI edits survive `docker compose up`). To restore the file's values:
  `docker compose -f deploy/docker-compose.yml run --rm seed python -m deploy.seed.load --reset`.
- The whole file is validated first (unknown members, on-call outside the team, bad channels / windows / durations,
  duplicate phones…) and loaded in one transaction.
- Sources, mappings, rules and runbooks are A's tables; their seed files come with those branches.
