# PROJECT: NSE-BSE Institutional Earnings Intelligence Agent

## Mission

Build an institutional-grade autonomous multi-agent system capable of:

* Scanning entire NSE and BSE universe continuously
* Detecting newly released earnings reports
* Parsing PDFs, XBRL filings, investor presentations and transcripts
* Calculating earnings surprise and PEAD signals
* Ranking all stocks in real-time
* Generating actionable Buy/Sell alerts
* Managing portfolio risk
* Executing trades through Kite MCP
* Continuously learning from trade outcomes

---

# PRIMARY OBJECTIVE

Detect:

```text
Strong Earnings Beat
+
Positive Guidance
+
Institutional Buying
+
Volume Expansion
+
PEAD Confirmation
+
Momentum Confirmation
```

Before the broader market fully prices the information.

---

# SYSTEM ARCHITECTURE

```text
┌────────────────────────────┐
│ NSE/BSE Filing Scanner     │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Earnings Extraction Agent  │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ SUE Calculation Engine     │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ PEAD Intelligence Engine   │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Transcript NLP Agent       │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Institutional Flow Agent   │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Options Flow Agent         │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Technical Confirmation     │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Ranking & Scoring Engine   │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Portfolio Risk Agent       │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Kite MCP Execution Agent   │
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐
│ Dashboard + Alerts         │
└────────────────────────────┘
```

---

# AGENT 1 : NSE/BSE SCANNER

## Frequency

Every 60 seconds

## Sources

NSE Corporate Filings

NSE XBRL

NSE Results Calendar

BSE Announcements

Investor Presentations

Conference Calls

Board Meeting Outcomes

Corporate Actions

## Output

```json
{
  "symbol":"ABC",
  "event":"Quarterly Result",
  "timestamp":"2026-06-07T10:45:00"
}
```

---

# AGENT 2 : EARNINGS EXTRACTION

## Input

PDF

XBRL

Presentation

Transcript

## Extract

Revenue

EBITDA

EBITDA Margin

PAT

EPS

Debt

Cash

Capex

Order Book

Guidance

Promoter Holdings

Institutional Holdings

FCF

ROE

ROCE

## Libraries

```text
pdfplumber
camelot
tabula
pandas
llamaindex
unstructured
```

---

# AGENT 3 : SUE ENGINE

## Standardized Unexpected Earnings

Formula:

SUE =
(Actual EPS − Expected EPS)
/ Std Deviation

## Rank Stocks

```text
Top 5%  = Long Watchlist
Bottom 5% = Short Watchlist
```

## Score

0-100

---

# AGENT 4 : PEAD ENGINE

## Purpose

Capture Post Earnings Announcement Drift.

## Inputs

Revenue Surprise

PAT Surprise

EPS Surprise

Guidance Surprise

Volume Expansion

Delivery %

Relative Strength

Institutional Activity

## PEAD Score

```text
Revenue Surprise      20%
PAT Surprise          20%
EPS Surprise          20%
Guidance              15%
Volume Expansion      10%
Delivery              5%
RS                    5%
Institutional Flow    5%
```

Output:

```json
{
  "pead_score":92
}
```

---

# AGENT 5 : TRANSCRIPT NLP AGENT

## Analyze

Management Tone

Growth Confidence

Future Guidance

Risk Statements

Capex Intentions

Order Book Commentary

Demand Outlook

Margin Outlook

## Produce

Sentiment Score

Confidence Score

Guidance Score

## Output

```json
{
  "sentiment":"Bullish",
  "score":88
}
```

---

# AGENT 6 : INSTITUTIONAL FLOW AGENT

## Detect

FII Buying

DII Buying

Mutual Fund Buying

Promoter Buying

Block Deals

Bulk Deals

Stake Changes

Pledge Changes

## Score

0-100

---

# AGENT 7 : OPTIONS FLOW AGENT

## Analyze

Call OI

Put OI

OI Change

Volume

PCR

Long Build-up

Short Covering

Gamma Exposure

Dealer Positioning

## Output

Bullish

Neutral

Bearish

---

# AGENT 8 : MARKET MICROSTRUCTURE AGENT

## Analyze

Volume Expansion

Delivery %

VWAP Position

Spread

Liquidity

Market Impact

ATR Expansion

Relative Volume

## Purpose

Validate institutional participation.

---

# AGENT 9 : TECHNICAL CONFIRMATION AGENT

## Indicators

20 EMA

50 EMA

200 EMA

VWAP

RSI

ADX

Volume Profile

Market Structure

Breakout Detection

## Score

0-100

---

# AGENT 10 : CORPORATE EVENT AGENT

## Monitor

Order Wins

Acquisitions

Mergers

Buybacks

Bonus Issues

Stock Splits

Fund Raising

Credit Upgrades

Management Changes

---

# AGENT 11 : VALUATION AGENT

## Calculate

PE

EV/EBITDA

PEG

P/B

FCF Yield

Sector Relative Valuation

## Objective

Prevent buying overvalued earnings beats.

---

# AGENT 12 : COMPOSITE SCORING ENGINE

## Final Score

```text
PEAD Score               30%
Transcript Score         15%
Institutional Score      15%
Options Score            10%
Technical Score          10%
Valuation Score          10%
Corporate Event Score    10%
```

Output:

```text
0-100
```

---

# SIGNAL GENERATION RULES

## STRONG BUY

Requirements:

```text
PEAD > 80

SUE > 80

Transcript > 75

Institutional > 70

Volume > 3x

Delivery > 45%

Technical > 70
```

Generate:

```text
STRONG BUY
```

---

## STRONG SELL

Requirements:

```text
PEAD < 20

Negative Guidance

Promoter Selling

Institutional Selling

Volume Spike

Breakdown Structure
```

Generate:

```text
STRONG SELL
```

---

# PORTFOLIO RISK AGENT

## Responsibilities

Position Sizing

Sector Exposure

Maximum Drawdown

Correlation Analysis

Volatility Adjustment

Kelly Fraction

Risk Parity

Stop Loss Management

Trailing Stop

Profit Locking

---

# KITE MCP EXECUTION AGENT

## Responsibilities

Authenticate

Fetch Holdings

Fetch Positions

Fetch Margin

Place Orders

Modify Orders

Cancel Orders

Monitor Orders

Update Stops

Exit Trades

## Flow

```text
Signal Generated
      ↓
Risk Validation
      ↓
Position Sizing
      ↓
Order Construction
      ↓
Kite MCP
      ↓
Execution
```

---

# ALERT ENGINE

## Channels

Telegram

WhatsApp

Discord

Email

Web Dashboard

Mobile App

---

# ALERT FORMAT

```text
BUY ALERT

Stock: XYZ

Revenue Growth: +28%
PAT Growth: +41%

PEAD Score: 93
SUE Score: 89

Transcript Sentiment: Bullish

Institutional Flow: Positive

Entry: 1245

Stop: 1180

Target 1: 1320
Target 2: 1380

Confidence: 95%
```

---

# NIGHTLY BATCH PROCESS

Run:

21:00 IST

Tasks:

1. Scan all NSE/BSE stocks
2. Recalculate SUE
3. Recalculate PEAD
4. Recalculate rankings
5. Generate Top Longs
6. Generate Top Shorts
7. Generate Sector Reports
8. Store data lake snapshots

---

# DATABASE

## PostgreSQL

Store

Filings

Financials

Signals

Trades

Portfolio

Backtests

## DuckDB

Store

Research

Factor Data

Historical Earnings

PEAD Dataset

---

# BACKTEST ENGINE

Backtest:

5 Years

10 Years

15 Years

Metrics:

```text
Win Rate

Sharpe

Sortino

Profit Factor

Max Drawdown

Expectancy

Alpha

Beta
```

---

# FUTURE PHASES

Phase 2

* Earnings Call Audio Analysis
* CEO Voice Stress Analysis
* Real-time News Agent

Phase 3

* Autonomous Portfolio Construction
* Self-Learning Ranking Engine
* Reinforcement Learning Layer

Phase 4

* Fully Autonomous Trading
* Multi-Broker Support
* Cross Asset Support

---

# SUCCESS CRITERIA

System scans:

* Entire NSE
* Entire BSE

Processes:

* Earnings within 60 seconds
* Generates ranked opportunities
* Identifies PEAD opportunities
* Produces institutional-grade buy/sell signals
* Executes trades through Kite MCP
* Continuously improves through feedback loops
