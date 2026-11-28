# E2E coverage, part c: use cases (`tests/usecases/`)

`COVERAGE.md` maps every FEATURE to a test. This part maps every HAND-OVER: a chain in which one
person gives something to another - a password file, a mailed link, a pepper, a role, a setting - and
the next one uses it.

**Why it exists.** The feature specs set their preconditions up over HTTP and SQL, so what one person
produced was rarely what the next one used. Read from all specs on 2026-11-22 (`../.fleet` audit,
section 3): of 17 hand-overs, 2 were followed to the next person in the browser, 14 partly, 1 not. Every
sign-in was `signIn()` (a form POST from an API context) or the admin session `setup.ts` saved, so no
admin ever used the login form; the pepper a counter is set up with was SQL, never typed into Account;
the counter's last will was published by the test, never by the broker.

**The rules** (the last one pinned by `tests/test_e2e_hygiene.py`):

- Every person acts in their own browser and logs in through the FORM - the admin too
  (`browserOf(THE_ADMIN)` in `tests/usecases/lib.ts`).
- What is handed over is the SAME thing that is used: the password in the file the server wrote
  (`adminPasswordFile`), the mail to that address (`inboxOf`), the MAC and pepper on the counter's
  screen (`aCounter`).
- The chain is followed to its end, with its second use or undo where it has one, and the outcome is
  read on the affected person's screen.
- A use-case spec imports only `./lib` and `@playwright/test` and calls no API: no SQL, no HTTP
  context, no saved session, no `mqttPublish`. Background facts it is not about (an account, a counter
  that has been reporting, another public counter) come from `given` in `lib.ts`, the one file that
  may. A Geiger counter is a program, not a person: `aCounter` connects to Mosquitto the way the
  firmware does, and what the broker lets it do is what it sees.

**A failing chain is a product finding.** The first run (2026-11-28) found none in the product: every
chain held to its end, the first admin's and the counter's included. It found one in the suite. U5's
counter waited 15 s for mosquitto_sub's log in vain: through a pipe the client writes it only when it
exits. `support.ts::mqttListen` read it the same way, so `mqtt › … nor read the others' readings`
published only after its listener had stopped and passed whatever the ACL said (an ACL that lets every
counter read all readings: green before, red now). Both listen in a terminal (`docker exec -t`) now.

Two things the chains brought up are the owner's to decide, not bugs: a dose rate above a counter's
threshold only turns its card orange (nobody is told), and a person whose session ended - idle past the
timeout, deleted, or signed out by a password change - lands on the login page with no word of why.

- **Run:** `scripts/e2e.sh` (the use cases run inside the suite: `first-run.spec.ts` in its own
  `firstrun` project before `setup.ts`, `global.spec.ts` in `global`, the rest in `main`).
- **Items:** 15 hand-overs (14 partial, 1 none before), ✅ 15, in 15 tests.

## First run - `first-run.spec.ts`

| # | Hand-over | Followed to | Test | Before |
|---|---|---|---|---|
| U1 | The server writes the first admin password to `data/admin_initial_password.txt` | the admin reads it there, logs in through the form with it, sets their own on Account; the file is gone, the old password is refused, the new one logs in | `U1 the first admin signs in with the password from data/ and sets their own; the file goes, the old one is refused` | partial (`setup.ts`, all HTTP) |
| U2 | The admin types Base URL and the SMTP server into the admin page | a person's reset request before that sends nothing; after it, exactly one mail from the From address typed, its link on the Base URL typed; the person sets a password with it and logs in; the link a second time is spent | `U2 the mail server and Base URL the admin types in carry a reset mail; the person sets a password with its link` | partial (set over HTTP in `setup.ts`; the UI save never followed by a mail) |

## Counters - `counters.spec.ts`

| # | Hand-over | Followed to | Test | Before |
|---|---|---|---|---|
| U3 | A counter shows its MAC and the pepper it made up | the owner follows "account settings" from Devices, types the pepper into Account and the MAC into Devices; the counter logs in with what it derives, sends a reading; the owner's devices page says online with a last-seen time, the dashboard shows the reading (CPM, dose rate, online) | `U3 a counter set up with the pepper on its screen logs in, and its reading is on its owner's pages` | partial (pepper by SQL, the counter's login re-derived by the test, `signIn()`) |
| U4 | The owner deletes a counter | the counter is locked out; registered again, it is let in and online again | `U4 a counter its owner deletes is locked out, and let in again once registered again` | partial (`signIn()`) |
| U5 | A counter loses power | its connection, made with the firmware's last will, drops without a goodbye; the broker publishes the will and the owner's page says offline; back on, online | `U5 a counter whose power goes is offline on its owner's page (the broker's last will), and online once back` | partial (the test published "offline" itself) |
| U6 | The owner sets a counter's alert threshold on Devices | a guest picks the public counter: the dose-rate card turns orange; cleared, the admin's default (0.5) applies and it is not orange | `U6 the alert threshold an owner sets on Devices turns a guest's dose-rate card orange; cleared, the default applies` | none (no UI until phase 2, then the owner's own dashboard only) |

## Accounts - `accounts.spec.ts`

| # | Hand-over | Followed to | Test | Before |
|---|---|---|---|---|
| U7 | The admin deletes a user (confirm dialog) | the person's open tab is sent to the login page, the form refuses them, their counter is locked out | `U7 a user the admin deletes is signed out, cannot sign in, and their counter is locked out` | partial (`ADMIN_STATE`, `signIn()`) |
| U8 | The admin makes a user an admin, then a user again | the person's nav gets Admin, the admin page opens with their own role locked; taken back, the Admin link on their open page leads to the dashboard and is gone | `U8 a role the admin gives is the admin page the person gets, and taken back, the page is gone` | partial (`ADMIN_STATE`, `signIn()`) |
| U9 | The admin makes a user public, then private | a guest can pick their counter, and the person's Account says Public; private again, both go back | `U9 a person the admin makes public: a guest sees their counter and the person's account says so; private again, both go` | partial (`ADMIN_STATE`) |
| U10 | A person makes their own profile public, then private | a guest can pick their counter, then not | `U10 a person who makes their profile public is seen by a guest, and private again, is not` | partial (`signIn()`) |
| U11 | A person changes their password on one device | the other device is sent to the login page; there the old password is refused, the new one logs in; the first device stays in | `U11 a password changed on one device signs the other out; there only the new one signs in` | partial (`signIn()`) |

## Site settings - `global.spec.ts`

Runs in the `global` project, one test at a time; `given.settingsBack` puts the settings back.

| # | Hand-over | Followed to | Test | Before |
|---|---|---|---|---|
| U12 | The admin sets the display timezone, and back | a person's last-seen time on Devices in New York time, then in Vienna time again | `U12 the display timezone the admin sets is the one a person's dates are shown in` | partial (`ADMIN_STATE`, `signIn()`) |
| U13 | The admin sets the offline timeout and the default alert threshold, and back | a person's dashboard: a counter last seen 30 min ago turns online, 0.0570 µSv/h orange; back, offline and not orange | `U13 the offline timeout and the default alert threshold the admin sets change a person's dashboard cards` | partial (`ADMIN_STATE`, `signIn()`) |
| U14 | The admin closes registration, then opens it | a guest's Register link leads to the login page with the reason; opened, the guest registers and logs in | `U14 registration the admin closes turns a guest away; opened again, the guest registers and signs in` | partial (GET only) |
| U15 | The admin sets the session timeout to a minute | a person who leaves their tab alone that long is sent to the login page on their next click; logged in again, a session in use goes on | `U15 with the session timeout the admin sets, a person who leaves the tab alone that long is signed out` | partial (read from a `Set-Cookie` header) |

U15 waits 61 s: time on the server has no event (its `wait` is on the scan's allowlist). U14 is the one
use case that registers: registration allows 5 a minute per address and the whole suite is one
address, so it registers in `global`, after `auth.spec.ts`'s registrations in `main`.

## Left out

| Hand-over | Why |
|---|---|
| The firmware itself on a counter | (a) needs the ESP8266 and its tube; `aCounter` plays it over the same TLS listener and topic, with credentials derived the firmware's way (pinned against `helpers.derive_mqtt_credentials()` by `tests/test_helpers.py`) |
| A notification when a counter's dose rate passes its threshold | not built: the orange card is the only alert the app gives (U6, U13) |
