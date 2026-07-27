# scanX Research Library

Prompt templates and screening methodology that drive the manual research
workflow around the scanX boards. Use them with any capable AI assistant
(Claude, ChatGPT) — replace `[COMPANY NAME]` / `[TICKER]` and, per the 7-step
framework, upload the latest 3–4 concalls and investor presentations rather
than relying on the model's own browsing.

## Recommended workflow order

1. **[7_step_stock_analysis_framework.md](7_step_stock_analysis_framework.md)** —
   the meta-process: business → management → triggers → competition → risks →
   valuation scenarios → cross-questioning. Start here.
2. **[detailed_research_report.md](detailed_research_report.md)** — the
   institutional-grade deep-dive memo prompt (business, Porter's five forces,
   peer table, product concentration, pipeline, KPIs, analyst Q&A).
3. **[forensic_analysis.md](forensic_analysis.md)** — the trust layer. Run it
   *after* the detailed report: CFO-vs-PAT diagnostics, revenue quality,
   working-capital forensics, off-balance-sheet exposure, Beneish/Altman/
   Piotroski, walk-the-talk scorecard. Veto layer before sizing a position.
4. **[omp_growth_triggers_one_pager.md](omp_growth_triggers_one_pager.md)** —
   the 90-second fund-manager 1-pager: 5–7 quantified growth triggers with
   timeline and conviction tags.

## Screening references

- **[smart_financial_ratios.md](smart_financial_ratios.md)** — the ratio
  framework behind the Financial Health chips on the Fundamental page
  (OCF/Net Profit > 1, Debt/Equity < 0.30, Current Ratio > 2, CWIP trend)
  and the PEG ≤ 1.5 gate in the TechnoFunda GARP view.
- **[garp_value_screen.py](garp_value_screen.py)** — standalone deep-value +
  momentum decile ranker over five Screener.in custom screens (P/E–P/B–DivYld,
  P/OCF, EV/EBITDA, P/Sales, 6-month momentum). Reference implementation; the
  screen URLs are personal saved screens and may need replacing with your own.
  The in-dashboard GARP view (technofunda.html → GARP screen) is the
  earnings-momentum-biased successor.

Everything here is for research/education only — not investment advice.
