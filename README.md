# 📈 IPO GMP Tracker & WhatsApp Notifier

Every weekday this pipeline scrapes IPO grey-market-premium (GMP) data, keeps only
the IPOs **closing today** whose GMP clears a threshold, cross-checks them against a
second site, and sends **one message before 2:00 PM IST**.

> **Current channel: Telegram.** CallMeBot's WhatsApp bot was too busy to reply
> reliably, so alerts go to the existing Telegram bot (`TELEGRAM_BOT_TOKEN`,
> `TELEGRAM_CHAT_ID` secrets). To switch to WhatsApp later, set the repo variable
> `NOTIFY_PROVIDER` to `meta`, `callmebot` or `twilio` (see Part 1).

Runs entirely on GitHub Actions — nothing runs on your computer.

---

## 🩹 What was broken (fixed in v3.0)

| Problem | Effect | Fix |
|---|---|---|
| **GitHub cron ran 5–9 h late.** The 07:23 UTC (12:53 IST) schedule actually started between **5:45 PM and 10:18 PM IST** on every run in Sep–Oct 2026. GitHub's free scheduler gives no timing guarantee. | Message (if any) arrived at night, after the IPO had closed. | Primary trigger is now an **external scheduler (cron-job.org) at 13:30 IST** calling the workflow API — starts within ~1 min. Early GitHub crons stay as **backups** that wait until 13:40 IST and only send if nothing was sent yet. |
| Default provider was **Telegram**. | With no `WHATSAPP_PROVIDER` variable the alert went to Telegram, not WhatsApp. | `auto` mode picks a WhatsApp provider first and logs a warning when it falls back to a non-WhatsApp channel. |
| A scraper error **crashed the run with no message**. | Silent days (e.g. 9 and 15 Oct runs failed). | Source failures now send a "could not build today's alert" message; IPO Watch failures degrade to InvestorGain-only; a failure-alert step covers everything else. |
| Retries treated bad credentials like network blips; CallMeBot errors returned with HTTP 200 slipped through. | Wasted retries / false "success". | 4xx = permanent (no retry), 5xx/429/timeouts = exponential backoff, body-level CallMeBot errors detected. Optional `FALLBACK_PROVIDER`. |
| No dedup. | Manual + scheduled runs could double-send. | One alert per IST day via a cache marker (`force` input to resend). |
| `ubuntu-latest` → Ubuntu 26 from 19 Oct 2026; Node 20 actions deprecated. | Chromium/Playwright could break. | Runner pinned to `ubuntu-24.04`; actions bumped to Node 24 versions. |
| No WhatsApp Cloud API support. | — | Official **Meta WhatsApp Cloud API** provider with approved-template support. |
| "Tests" only printed output. | Bugs not caught. | 33 real pytest tests + a CI workflow on every push. |

---

## 🧭 How it works

```
13:30 IST  cron-job.org ──POST workflow_dispatch──┐
06:11 / 08:11 / 10:11 / 13:17 IST  GitHub cron ──► gate (wait until 13:40, skip if already sent)
                                                  │
                                                  ▼
                       notify job  (one at a time · skip if today already sent)
   STEP 1  investorgain.com  → close date == today (IST) AND GMP% > threshold → SQLite
   STEP 2  ipowatch.in       → fuzzy-match names → enrich SQLite (optional; failure ≠ fatal)
   STEP 3  build message     → WhatsApp (Meta / CallMeBot / Twilio) → fallback channel
           mark today as sent · step summary · logs uploaded as artifact
           on failure → alert message with the run link
```

**Thresholds:** SME > 40 %, Mainboard > 15 % (GMP ÷ upper price band × 100).

```
├── .github/workflows/
│   ├── daily_ipo.yml        # dispatch trigger + backup crons, gate, dedup, alerts
│   ├── tests.yml            # pytest on every push
│   └── keepalive.yml        # stops GitHub disabling schedules after 60 idle days
├── src/
│   ├── config.py            # every setting comes from env vars / .env
│   ├── timing.py            # send window, backup gate, late banner (pure functions)
│   ├── investorgain_scraper.py   # STEP 1
│   ├── ipowatch_scraper.py       # STEP 2
│   ├── formatter.py              # message layout
│   ├── notifier.py               # STEP 3: providers, retry, fallback
│   ├── db.py · utils.py
├── scripts/wait_gate.py     # backup-run gate (stdlib only)
├── tests/                   # pytest suite (offline)
├── main.py                  # CLI / orchestrator
└── verify.py                # read-only audit of scraped data
```

---

# 🚀 Setup

No secret ever goes in code. Everything is a **GitHub Secret** (sensitive) or a
**GitHub Variable** (settings), or a local `.env` file that git ignores.

## Part 1 — Choose a WhatsApp provider (one is enough)

### Option A — CallMeBot (free, 5 minutes, your own number only)

1. Open <https://www.callmebot.com/blog/free-api-whatsapp-messages/> and save the bot
   number shown there in your phone contacts (they rotate it — trust the page).
2. From WhatsApp send it: `I allow callmebot to send me messages`
3. It replies with your **API key**. If nothing comes in ~2 minutes, their signups are
   full — try later or use Option B.
4. Secrets: `CALLMEBOT_PHONE` = `+919876543210`, `CALLMEBOT_APIKEY` = the key.

### Option B — Meta WhatsApp Cloud API (official, most reliable)

1. <https://developers.facebook.com> → **Create app** → type **Business** → add **WhatsApp**.
2. In **WhatsApp → API Setup** note the **Phone number ID**. With the free test number
   you must add your own number as a verified recipient there.
3. Create a template in **WhatsApp Manager → Message templates**:
   name `ipo_daily_alert`, category **Utility**, language **English (en)**,
   body: `IPO GMP update: {{1}}` → submit and wait for **Approved**.
   (Business-initiated messages need an approved template; the whole alert is passed
   as `{{1}}`, flattened to one line because Meta forbids newlines in parameters.)
4. Create a **permanent token**: Business Settings → System users → add a system user →
   assign the app → **Generate token** with `whatsapp_business_messaging`.
5. Secrets: `META_WA_TOKEN`, `META_WA_PHONE_NUMBER_ID`, `META_WA_TO` (`919876543210`).
   Variable: `META_WA_TEMPLATE` = `ipo_daily_alert` (and `META_WA_TEMPLATE_LANG` if not `en`).
6. Meta bills template messages per conversation; check Meta's current pricing.

### Option C — Twilio WhatsApp
Secrets `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `TWILIO_TO`.
The sandbox expires every 72 h unless re-joined, so it is poor for automation.

### Recommended extra — Telegram as fallback
@BotFather → `/newbot` → token; message your bot once; get your chat id from
@userinfobot. Secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, variable
`FALLBACK_PROVIDER` = `telegram`. If WhatsApp delivery ever fails you still get the
alert (and the run is marked red so you know to fix WhatsApp).

## Part 2 — Add secrets and variables

Repo → **Settings → Secrets and variables → Actions**.

**Secrets tab** — only the ones for your provider(s) from Part 1.

**Variables tab** (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `NOTIFY_PROVIDER` | `telegram` | `telegram`, `meta`, `callmebot`, `twilio`, or `auto` (= first configured of meta → callmebot → twilio → telegram). Any old `WHATSAPP_PROVIDER` repo variable is ignored. |
| `FALLBACK_PROVIDER` | — | e.g. `telegram` |
| `META_WA_TEMPLATE` | — | approved template name (Option B) |
| `MIN_GMP_PCT_SME` | `40` | SME threshold % |
| `MIN_GMP_PCT_MAINBOARD` | `15` | mainboard threshold % |
| `SEND_WHEN_EMPTY` | `true` | send a "nothing today" message so you know it ran |
| `SEND_DEADLINE` | `14:00` | later than this, the message gets a "delayed" banner |
| `FUZZY_THRESHOLD` | `80` | name-match strictness |
| `USE_PLAYWRIGHT` | `false` | `true` if InvestorGain renders its table with JavaScript |

> Locally (`.env`) the same setting is called `WHATSAPP_PROVIDER`.

## Part 3 — Test delivery right now

1. **Actions → Daily IPO WhatsApp Alert → Run workflow**, tick **force**, run.
2. Open the run: the **Provider check** step shows which provider was picked (no secret
   values printed); the **run summary** shows the exact message and the IST send time.
3. The message should reach your phone within a minute.

## Part 4 — Exact-time trigger with cron-job.org (the actual fix)

**4a. Create a GitHub token** — GitHub → Settings → Developer settings →
**Fine-grained tokens** → Generate:
- Repository access: **Only select repositories** → this repo
- Permissions → Repository → **Actions: Read and write** (nothing else)
- Expiration: up to 1 year — set a reminder to renew it.

**4b. Create the job** at <https://cron-job.org> (free account) → **Create cronjob**:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/Choudhary-Umesh/IPO-GMP-Tracker-and-WhatsApp-Notifier-/actions/workflows/daily_ipo.yml/dispatches` |
| Schedule | Custom: **13:30**, days **Mon–Fri**, timezone **Asia/Kolkata** |
| Advanced → Request method | `POST` |
| Advanced → Headers | `Accept: application/vnd.github+json` · `Authorization: Bearer <your token>` · `X-GitHub-Api-Version: 2022-11-28` · `Content-Type: application/json` |
| Advanced → Request body | `{"ref":"main","inputs":{"source":"cron-job.org"}}` |
| Notifications | enable "on failure" |

Click **Test run**: GitHub must answer **HTTP 204** and a new run appears in Actions
within a minute (it will skip with "already sent today" if Part 3 already sent one —
that is the dedup working). Optionally add a second identical job at 13:45 as a
retry; dedup prevents a double message.

**Timeline on a normal day:** 13:30 trigger → ~13:31–13:33 message on your phone.
If cron-job.org or the token ever fails, a GitHub backup cron sends at ~13:40 or
whenever GitHub gets to it (with a "delayed" banner after 14:00).

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env          # fill in your provider values (.env is git-ignored)

python main.py --check-config        # which providers are configured
python main.py --test-message        # send a test message only
python main.py --dry-run             # full scrape, print the message, send nothing
python main.py --date 2026-08-20     # pretend it's another date
python verify.py                     # audit scraped data row by row
python -m pytest -q                  # offline tests
```

Exit codes: `0` ok · `1` failed · `3` delivered only via the fallback channel.

---

## 🩺 Troubleshooting

| Symptom | Check |
|---|---|
| `Provider 'telegram' selected but its secrets are missing` | Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets (README Part 1, Telegram). |
| Telegram `HTTP 400 chat not found` / `403 bot was blocked` | Wrong `TELEGRAM_CHAT_ID`, or you haven't sent your bot a message yet / blocked it. Open the bot and press Start. |
| Want WhatsApp instead of Telegram | Add that provider's secrets and set the variable `NOTIFY_PROVIDER`. |
| No run at 13:30 | cron-job.org job history: 401 = token expired/wrong, 404 = token lacks repo access or wrong URL, 422 = wrong `ref`. |
| Run says "already sent today" | Dedup. Use **Run workflow → force** to resend. |
| `Meta WhatsApp rejected … HTTP 401` | Token expired — use a system-user permanent token. |
| Meta accepted but nothing arrived | Template not approved/paused, wrong language code, or (test number) recipient not verified. Without `META_WA_TEMPLATE`, text is only delivered within 24 h of you messaging the business number. |
| `CallMeBot rejected the message: APIKey is invalid` | Redo the CallMeBot activation; phone must include `+91`. |
| "InvestorGain could not be read" message | Site down or layout changed. Download the `run-<id>` artifact (logs + HTML). If the table is missing from the HTML, set `USE_PLAYWRIGHT=true`. |
| "IPO Watch cross-check unavailable" | Second source failed; InvestorGain data was still sent. |
| Schedules stopped after ~2 months | GitHub disables schedules in idle repos; keep `keepalive.yml` enabled. |

---

## ⚠️ Notes

* GMP is unofficial grey-market data — a signal to look, not investment advice.
* Each site is fetched once per run. Please respect their terms of use.
