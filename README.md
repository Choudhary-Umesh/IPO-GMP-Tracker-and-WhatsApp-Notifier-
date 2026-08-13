# 📈 IPO Tracker & WhatsApp Notifier

A zero-cost, fully cloud-hosted pipeline that scrapes IPO grey-market-premium data
every weekday, keeps only the IPOs **closing today with GMP above 15%**,
cross-checks them against a second source, and sends you a **single WhatsApp
message before 2:00 PM IST**.

Nothing runs on your Mac. No background daemon, no browser window, no battery drain.

---

## 🧭 Pipeline

```
GitHub Actions cron (08:00 UTC = 13:30 IST)
        │
        ├─ STEP 1  investorgain.com/report/ipo-gmp-live/331/
        │          filter: Close Date == today (IST) AND (GMP / Issue Price)*100 > 15
        │          store survivors ──────────────► SQLite (ipo_tracker.db)
        │
        ├─ STEP 2  read names from SQLite
        │          scrape ipowatch.in, fuzzy-match names (rapidfuzz / difflib)
        │          write matched GMP back ───────► SQLite
        │
        └─ STEP 3  build message ──► CallMeBot / Twilio ──► 📱 your WhatsApp
```

---

## 📁 Directory tree

```
ipo-tracker/
├── .github/
│   └── workflows/
│       ├── daily_ipo.yml           # daily cron + manual trigger
│       └── keepalive.yml           # stops GitHub disabling the cron after 60 days
├── src/
│   ├── __init__.py
│   ├── config.py                   # all env-var driven settings
│   ├── utils.py                    # HTTP, number/date parsing, name normalisation
│   ├── db.py                       # SQLite schema + CRUD
│   ├── investorgain_scraper.py     # STEP 1 — primary source + filtering
│   ├── ipowatch_scraper.py         # STEP 2 — cross-validation + fuzzy matching
│   ├── formatter.py                # WhatsApp message layout
│   └── notifier.py                 # STEP 3 — CallMeBot / Twilio delivery
├── tests/
│   └── test_pipeline_offline.py    # runs the whole pipeline on fixture HTML
├── main.py                         # orchestrator / CLI entry point
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 📱 Sample output

```
📈 IPOs Closing TODAY (13-Aug-2026)
Filter: GMP above 15%

Company Name: Alpha Cement Industries Ltd
- InvestorGain GMP: ₹45 (39.47%)
- IPO Watch GMP: ₹44 (38.60%)
---------------------------------

Company Name: Delta Pharma Ltd
- InvestorGain GMP: ₹250 (23.15%)
- IPO Watch GMP: ₹240 (22.22%)
---------------------------------

⚠️ Apply before the cut-off (usually 5 PM). GMP is unofficial data.
```

---

# 🚀 Setup guide (start to finish, ~20 minutes)

## Part 1 — Set up your notification channel

Pick ONE. Telegram is the default because it never fails to register; CallMeBot
delivers to WhatsApp but has a hard capacity limit on new signups.

### Option A — Telegram (recommended, always available)

1. In Telegram, message **@BotFather** → `/newbot` → pick a name and a username
   ending in `bot`. He replies with a token like `123456789:AAE...`.
2. Open a chat with your new bot and send it any message (this is required — bots
   cannot message you first).
3. Get your chat ID: open in a browser
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and read `"chat":{"id":123456789`.
   Or just message **@userinfobot**, which replies with your ID.
4. Secrets to add later: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   Set the repo variable `WHATSAPP_PROVIDER` to `telegram` (this is the default).

### Option B — WhatsApp via CallMeBot

CallMeBot is free forever for personal use, needs no credit card, and — unlike the
Twilio sandbox — does not expire every 72 hours. It can only message **your own
number**, which is exactly what you need.

**Availability caveat:** CallMeBot caps how many people can register. When their
bot is full the setup page masks the phone number entirely and no new signups are
possible until slots free up. Check the page before relying on this route.

1. Open <https://www.callmebot.com/blog/free-api-whatsapp-messages/> and copy the
   bot's phone number shown there. They rotate it, so always trust the page.
2. Save that number in your phone contacts (any name).
3. From WhatsApp, send it exactly: `I allow callmebot to send me messages`
4. Within ~2 minutes the bot replies: `API Activated for your phone number. Your APIKEY is 1234567`.
   Save that 7-digit key. (If nothing arrives in 2 minutes, retry after 24 hours.)
5. Note your own number in international format, e.g. `+919876543210`.

> Prefer Twilio instead? Set `WHATSAPP_PROVIDER=twilio` and fill the four Twilio
> secrets. Be aware the sandbox requires re-sending the `join <code>` message
> every 72 hours, which breaks unattended automation — CallMeBot is the better fit.

## Part 2 — Create the GitHub repository

1. Download/unzip this project.
2. On GitHub: **New repository** → name it `ipo-tracker` → **Private** → Create.
3. Push the code:

```bash
cd ipo-tracker
git init
git add .
git commit -m "IPO tracker & WhatsApp notifier"
git branch -M main
git remote add origin https://github.com/<your-username>/ipo-tracker.git
git push -u origin main
```

> Make sure `.env` is never committed — `.gitignore` already covers it.

## Part 3 — Add your secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret name        | Value                        |
|--------------------|------------------------------|
| `TELEGRAM_BOT_TOKEN` | the BotFather token (Option A) |
| `TELEGRAM_CHAT_ID`   | your numeric chat ID (Option A) |
| `CALLMEBOT_PHONE`    | `+919876543210` — only for Option B |
| `CALLMEBOT_APIKEY`   | the 7-digit key — only for Option B |

Optional, under the **Variables** tab (not secrets — these aren't sensitive):

| Variable          | Default | Meaning                                   |
|-------------------|---------|-------------------------------------------|
| `MIN_GMP_PCT`     | `15`    | GMP % threshold                            |
| `FUZZY_THRESHOLD` | `80`    | name-match strictness (0-100)              |
| `SEND_WHEN_EMPTY` | `true`  | send a "nothing today" heartbeat message   |
| `WHATSAPP_PROVIDER` | `telegram` | `telegram`, `callmebot` or `twilio`    |

## Part 4 — Test it immediately (don't wait for tomorrow)

1. Repo → **Actions** tab → if prompted, click **I understand my workflows, go ahead and enable them**.
2. Select **Daily IPO WhatsApp Alert** → **Run workflow** → **Run workflow**.
3. Open the run, expand **Run IPO pipeline**, and watch the logs.
4. A WhatsApp message should arrive within ~60 seconds.

To test only the WhatsApp leg (no scraping), run locally:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your phone + apikey
export $(grep -v '^#' .env | xargs)
python main.py --test-message
```

Other useful local commands:

```bash
python main.py --dry-run              # full scrape, prints message, sends nothing
python main.py --date 2026-08-20      # pretend it's another date
python main.py -v                     # verbose logs
python tests/test_pipeline_offline.py # no network needed; validates parsing logic
```

## Part 5 — Confirm the schedule

The workflow is set to `cron: "0 8 * * 1-5"` → **08:00 UTC = 13:30 IST, Mon–Fri**.

It fires 30 minutes early on purpose: GitHub's shared scheduler frequently delays
cron jobs by 5–20 minutes, so a 13:30 trigger reliably lands well before 2:00 PM IST.
Edit that line in `.github/workflows/daily_ipo.yml` to shift it (`* * *` instead of
`* * 1-5` for all seven days).

You're done. Check your phone tomorrow afternoon.

---

## ⚙️ How the filtering works

* **Close date** — parsed from the site's Close column and compared to today's date in
  `Asia/Kolkata`, so a run at 13:30 IST always uses the correct Indian calendar day.
* **GMP %** — `(GMP / Issue Price) * 100`, using the **upper band** of the price range
  (the cap price is what applicants actually pay). If a site prints its own gain %,
  that value is preferred over the computed one.
* **Threshold** — strictly greater than 15% by default.
* **Name matching** — both names are stripped of noise (`Ltd`, `Limited`, `IPO`, `SME`,
  `(Mainboard)`, punctuation) then compared with `rapidfuzz.token_set_ratio`. A score
  below 80 is treated as "not found" and the message shows `IPO Watch GMP: Not listed`
  rather than a wrong number.

---

## 🩺 Troubleshooting

| Symptom | Cause & fix |
|---|---|
| No message at all, workflow green | You had no qualifying IPO and `SEND_WHEN_EMPTY=false`. Set it to `true`. |
| `CallMeBot rejected the message` | Wrong API key, or phone number missing the `+91` prefix. Re-run the activation from Part 1. |
| `No GMP table found on InvestorGain` | Layout change or JS rendering. Download the `debug-html-*` artifact from the failed run to inspect. If the table is missing from the HTML, uncomment `playwright` in `requirements.txt`, add a `python -m playwright install --with-deps chromium` step to the workflow, and set `USE_PLAYWRIGHT=true`. |
| `IPO Watch GMP: Not listed` | The IPO genuinely isn't on ipowatch.in yet, or the names differ too much. Lower `FUZZY_THRESHOLD` to ~70. |
| Workflow stopped running after ~2 months | GitHub disables schedules in dormant repos. The `keepalive.yml` workflow prevents this — make sure it's enabled. |
| Message arrives split in two | More than ~900 characters (a very busy IPO day). Raise `MAX_MESSAGE_CHARS` if your provider allows. |
| Everything green but late | GitHub cron delay. Move the trigger earlier, e.g. `"45 7 * * 1-5"` (13:15 IST). |

---

## 💰 Cost

| Component | Cost |
|---|---|
| GitHub Actions (public repo) | Free, unlimited minutes |
| GitHub Actions (private repo) | Free tier: 2,000 min/month. This job uses ~1 min/day ≈ 22 min/month |
| CallMeBot WhatsApp API | Free for personal use |
| Scraping + SQLite | Free |

---

## ⚠️ Notes

* GMP is unofficial, unregulated grey-market data. Treat it as a signal to look, not
  as investment advice — a high GMP on the closing day can and does evaporate by listing.
* Both sites are scraped politely (one request each per day). Their HTML can change
  at any time; the parsers map columns by header text rather than position to survive
  minor reshuffles, and fail loudly with a saved HTML artifact when they can't.
* Please respect each site's terms of use.

---

## 🔍 Verifying the scraped data is correct

`verify.py` is a read-only audit tool: it fetches both sites, prints every row
with the raw cell text next to the value extracted from it, explains why each row
passed or failed the filter, and previews the message — without sending anything
or touching the production database.

```bash
source .venv/bin/activate
python verify.py           # live audit
python verify.py --save    # also write debug/*.html and debug/*.csv
python verify.py --offline # re-parse the saved HTML, no network
python verify.py --date 2026-08-20   # audit a different day
```

Read the five sections in order — each one fails differently:

1. **Table detection** — the column map. If `gmp` points at the Est. Listing
   column, every number below it is wrong and nothing else matters.
2. **InvestorGain rows** — raw cell → extracted value, with a colour-coded verdict
   (green = selected, yellow = GMP too low, dim = closes another day, red = parse
   failure). Compare the row count and a few numbers against the website.
3. **IPO Watch rows** — same treatment for the second source.
4. **Fuzzy matching** — every score. Yellow rows are near-misses; if a company you
   can see on ipowatch.in scores 65–79, lower `FUZZY_THRESHOLD`.
5. **Message preview** — exactly what WhatsApp would have received.

`--save` also writes CSVs you can open in Numbers/Excel and diff against the site
side by side, plus the raw HTML for when you need to see what the parser actually
received.
