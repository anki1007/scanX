# Onboarding — technofunda + scanX

You're setting up two things that share this folder:

- **scanX** — a Screener.in PEAD results dashboard, published live on GitHub Pages,
  refreshed every 60s.
- **technofunda** — a round-the-clock NSE/BSE + Kite earnings scanner that logs
  and (optionally) Telegrams alerts. Screening only; it never places orders.

Do them in order. Step 0 gets everything running locally in ~2 minutes with no
accounts. Steps 1-3 make scanX live. Step 4 is the Kite scanner (optional).

---

## Prerequisites

- **Python 3.10+** — install from https://www.python.org/downloads/ and tick
  "Add Python to PATH" during setup.
- **Git** — https://git-scm.com/download/win (only needed for Step 2).
- Accounts you already have / may want: Screener.in (Google login), GitHub
  (`anki1007`), Zerodha Kite (optional), Telegram (optional).

Open **PowerShell** in this folder: Shift-right-click the folder → "Open
PowerShell window here".

---

## Step 0 — Local setup (one command)

```powershell
powershell -ExecutionPolicy Bypass -File .\onboard.ps1
```

This checks Python, installs dependencies, runs the tests, builds the dashboard
from bundled real sample data, and prints what's still needed. Then preview it:

```powershell
.\serve.ps1
```

A browser opens at http://localhost:8777 showing the dashboard. (Opening
`docs\index.html` directly shows an empty table — browsers block local file
loads, so always preview via `serve.ps1`.) Press Ctrl+C to stop the server.

✅ If you see ranked companies, the whole engine works.

---

## Step 1 — scanX live data (Screener.in)

1. Copy the credentials template:
   ```powershell
   copy credentials.example.ps1 credentials.ps1
   ```
2. You're logged into Screener via Google. Get the session cookie:
   - In the browser, press **F12** → **Application** tab → **Cookies** →
     `https://www.screener.in` → click the row named **`sessionid`** → copy its
     **Value**.
3. Open `credentials.ps1` in Notepad and set:
   ```powershell
   $env:SCREENER_SESSIONID = "paste_the_value_here"
   ```
4. Pull live results and rebuild the dashboard:
   ```powershell
   . .\credentials.ps1
   python scripts\refresh_scanx.py --pages 5
   .\serve.ps1
   ```

✅ The dashboard now shows the freshest ~125 real results, scored and ranked.
The cookie lasts about 2 weeks; recopy it when scraping says "redirected to login".

---

## Step 2 — Publish the dashboard to GitHub (once)

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_github.ps1
```

This commits everything and pushes to `https://github.com/anki1007/scanx`
(your browser/Git will ask you to sign in to GitHub the first time). Then:

1. Repo → **Settings → Pages** → Build and deployment → **Deploy from a branch**
   → Branch **`main`**, folder **`/docs`** → Save.
2. Repo → **Settings → Secrets and variables → Actions → New secret** →
   name `SCREENER_SESSIONID`, value = the cookie from Step 1 (lets the backup
   refresh run when your PC is off).

✅ Live in ~1 minute at **https://anki1007.github.io/scanx/**

---

## Step 3 — Keep it refreshing every 60s

```powershell
.\scanx_publish.ps1
```

Leave this running. Each cycle it re-scrapes, re-scores, and—if anything
changed—pushes to GitHub, so the Pages dashboard updates live. The GitHub Action
also refreshes about every 5 minutes as a backup.

---

## Step 4 — technofunda live scanner (NSE/BSE + Kite) — optional

Alerts on fresh NSE/BSE filings with a Kite price/volume reaction.

1. (Optional, for the price read) In `credentials.ps1` set your Kite app keys:
   ```powershell
   $env:KITE_API_KEY    = "..."
   $env:KITE_API_SECRET = "..."
   ```
   Then generate today's token (Kite tokens expire daily):
   ```powershell
   python scripts\kite_login.py
   ```
2. Run it now (visible terminal, 09:00-23:55 window):
   ```powershell
   .\technofunda.ps1
   ```
   Or a quick offline test: `.\technofunda.ps1 -Mode demo -Once`
3. Schedule it daily at 09:00:
   ```powershell
   .\technofunda.ps1 -Register
   ```
   Alerts land in `alerts\` (a daily log + `alerts.csv`) and Telegram if configured.

---

## Daily routine

- **Each market morning:** `python scripts\kite_login.py` (Kite token is 1-day).
- **Every ~2 weeks:** recopy the Screener `sessionid` if scraping starts failing.
- Everything else runs itself once `scanx_publish.ps1` and the scheduled task are up.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "running scripts is disabled on this system" | Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or always launch with `powershell -ExecutionPolicy Bypass -File .\<script>.ps1` |
| Dashboard table is empty | Don't open `docs\index.html` directly — use `.\serve.ps1` |
| Screener: "redirected to login" | `sessionid` expired — recopy it (Step 1.2) |
| Kite: "Please log in first" | Run `python scripts\kite_login.py` (daily) |
| `python` not found | Reinstall Python with "Add to PATH" ticked, reopen PowerShell |
| GitHub push rejected | `setup_github.ps1` merges the remote README automatically; re-run it |

> Note: the PEAD score is our transparent growth-based formula (weights in
> `earnings_intel\screener_screen.py`) — tune them anytime; the screen and
> ranking behave like the reference dashboard.
