# ---------------------------------------------------------------------------
# Technofunda credentials template.
#
# 1. Copy this file to  credentials.ps1   (same folder)
# 2. Fill in your values
# 3. LIVE.bat (live_all.ps1) auto-loads credentials.ps1 on startup
#
# credentials.ps1 and kite_token.json are git-ignored — never commit them.
# ---------------------------------------------------------------------------

# --- Zerodha Kite (optional; enables the live price/volume reaction read) ---
# Get the API key/secret from your Kite Connect app at https://kite.trade
$env:KITE_API_KEY    = "your_api_key_here"
$env:KITE_API_SECRET = "your_api_secret_here"

# Access token expires DAILY. Generate each morning before 09:00 with:
#     python scripts\kite_login.py
# That writes kite_token.json which the scanner reads automatically, so you can
# usually leave KITE_ACCESS_TOKEN unset and rely on the token file instead.
# $env:KITE_ACCESS_TOKEN = "todays_access_token"

# --- Telegram alerts (optional) --------------------------------------------
# Create a bot via @BotFather to get the token; get your chat id from @userinfobot
$env:TELEGRAM_BOT_TOKEN = ""
$env:TELEGRAM_CHAT_ID   = ""

# --- Dhan (optional; live NSE/BSE quotes via DhanHQ v2, alternative to Kite) ---
# The realtime engine needs only client id + a daily access token:
$env:DHAN_CLIENT_ID    = ""
# $env:DHAN_ACCESS_TOKEN = "todays_token"   # or let scripts\dhan_login.py write dhan_token.json
# For scripts\dhan_login.py to mint the daily token headlessly (keep these LOCAL):
$env:DHAN_PIN          = ""
$env:DHAN_TOTP_SECRET  = ""

# --- Screener.in auto-login (preferred: scraper logs in itself) -------------
# The scraper logs in with these, caches the session, and reuses it for ~2 weeks.
$env:SCREENER_EMAIL    = ""
$env:SCREENER_PASSWORD = ""
# Optional manual override instead of email/password (a copied browser cookie):
# $env:SCREENER_SESSIONID = "..."
