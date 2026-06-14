# FPI Sector Analysis — Automation

This folder is automated. NSDL publishes new fortnightly FPI sector data twice a month;
this setup discovers, downloads, parses, and merges it for you.

## Files

| File | Purpose |
|---|---|
| `fpi_update.py` | The scraper / updater. Run it any time to pull anything new. |
| `fpi_server.py` | Optional local helper (127.0.0.1:8765). Start it once and the dashboard's **Sync** button can run the updater on demand. |
| `FPI_Sectorial-Analysis.xlsx` | Your workbook. Existing sheets are preserved. The script adds **Auto Data**, **Sectors Wide**, **Monthly**, **Quarterly**, **Yearly (FY)** and appends new fortnight columns to **FPIs Fortnightly Investments**. Includes Excel charts (sector trends + monthly bars) and a green/red heatmap on each pivot sheet. |
| `FPI_Dashboard.html` | Double-click to open. Reads `fpi_data.js`. Fortnightly / Monthly / Quarterly / Yearly tabs, KPI tiles, total-flow + cumulative charts, sector trend lines, heatmap (with year filter), top movers, momentum leaderboard, **Sync** button. |
| `fpi_data.js` / `fpi_data.json` | Long-format data the dashboard reads. Regenerated every run. |
| `fpi_update.log` | Append-only run log. |

## How it stays current

A scheduled task (`fpi-nsdl-auto-update`) runs `fpi_update.py` at **9:00 AM on the 1st-5th and 16th-20th** of every month — the window in which NSDL typically publishes the fortnight that just ended. On days with no new data the run is a no-op (~1 second). When NSDL posts a new fortnight, the task:

1. Downloads the new report from NSDL.
2. Appends a row-per-sector to **Auto Data** in the workbook.
3. Adds a new column to your existing **FPIs Fortnightly Investments** sheet.
4. Rebuilds the **Sectors Wide / Monthly / Quarterly / Yearly** pivot sheets.
5. Re-writes `fpi_data.js` so the dashboard auto-refreshes on next open.

You can re-run it manually any time:

```
cd D:\FPI
python fpi_update.py
```

## On-demand Sync button (one-time setup)

The dashboard has a **Sync** button next to its title. To make it work forever
with zero ongoing effort, double-click **`install_sync_helper.bat`** once.

That installer:

- pip-installs the required Python packages,
- creates a Windows Scheduled Task (`FPI_Sync_Helper`) that launches
  `fpi_server.py` silently via `pythonw.exe` at every user logon,
- starts it immediately,
- verifies that `http://127.0.0.1:8765` is responding.

After it succeeds, the helper is just *always running* in the background. No
terminal windows, no manual steps, no remembering. Click **Sync** in the
dashboard and `fpi_update.py` runs on demand; if new fortnights were published
the page auto-reloads.

To remove later:

```
cd D:\FPI
.\install_sync_helper.ps1 -Uninstall
```

(Or open Task Scheduler and delete the `FPI_Sync_Helper` task.)

The scripts `start_sync_helper.ps1` / `start_sync_helper.bat` are still around
if you ever want to run the helper manually in a visible window (useful for
debugging — its log output appears live).

## First-time setup (one-off)

The script needs three Python packages. From a terminal in this folder:

```
pip install requests beautifulsoup4 openpyxl
```

If Python isn't on PATH, install Python 3.10+ from python.org (tick "Add Python to PATH").

## Data scope

- **Starts**: Jul 01-15, 2024 (matching the original workbook scope).
- **Latest fetched**: see `Auto Data` → max of column A, or open the dashboard.
- **Source schema**: NSDL changed the report layout on Aug 31, 2024 (added Debt-FAR / Mutual Funds / AIF columns). The scraper handles both old and new layouts automatically.

## Caveats

- Only the **Equity Net Investment (INR Cr)** stream is written to the existing
  `FPIs Fortnightly Investments` sheet — same convention as the original workbook.
  The full set of instrument categories (Debt-General, Debt-VRR, Debt-FAR, Hybrid,
  MF-Equity, MF-Debt, MF-Hybrid, MF-Solution, MF-Other, AIF, Total) is stored in
  the new `Auto Data` sheet and in `fpi_data.json`.
- Dashboard requires internet on first load (pulls Chart.js from cdnjs). After that
  it's cached by the browser.
- Scheduled tasks only fire while the Claude desktop app is running. If the app was
  closed when a fire-time elapsed, the task runs on next launch.
