"""Generate docs/index.html  (python scripts/build_home.py)

Re-run after adding a module to the rail; tests/test_home.py fails until every
menu entry has a card. Descriptions live in DESC below.

 - the scanX home: every module, what it does,
a live stat and how fresh its data is.

The cards are written into the markup (not injected by script) so the page is
useful with the stats unavailable, and so a crawler or a link preview reads it.
Nav, social preview and nav CSS are lifted from an existing page so the rail
stays byte-identical across the site.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BASE = "https://anki1007.github.io/scanX/"
HEADLINE = "scanX \u2014 Fundamental Stock Screening &amp; Market Breadth"

src = (DOCS / "technofunda.html").read_text(encoding="utf-8")

# ---------------------------------------------------------------- social
social = re.search(r"<!-- social preview -->.*?<!-- /social preview -->", src, re.S).group(0)
social = social.replace(f'href="{BASE}technofunda.html"', f'href="{BASE}"')
social = social.replace(f'content="{BASE}technofunda.html"', f'content="{BASE}"')
social = social.replace('content="scanX \u00b7 TechnoFunda 100"', f'content="{HEADLINE}"')
assert f'<link rel="canonical" href="{BASE}">' in social
assert social.count(HEADLINE) == 2, "og:title and twitter:title"

# ---------------------------------------------------------------- nav
nav = re.search(r'<aside class="nav">.*?</aside>', src, re.S).group(0)
nav = nav.replace('class="nav-i active" href="technofunda.html"', 'class="nav-i" href="technofunda.html"')
nav = nav.replace('class="nav-i nav-top" href="index.html"', 'class="nav-i nav-top active" href="index.html"')
assert nav.count(' active"') == 1 and 'nav-top active" href="index.html"' in nav

navcss = re.search(r"  \.app\{.*?\.nav-i \.ic\{[^}]*\}\n", src, re.S).group(0)

# sections, in the rail's own order
items, sections, cur = {}, [], "Start here"
for m in re.finditer(r'<div class="grpL">([^<]+)</div>|href="([^"]+)"><span class="ic">([^<]+)</span><span>([^<]+)</span>', nav):
    if m.group(1):
        cur = m.group(1)
        continue
    href, icon, label = m.group(2), m.group(3), m.group(4)
    if href == "index.html":
        continue
    items[href] = (icon, label)
    if not sections or sections[-1][0] != cur:
        sections.append((cur, []))
    sections[-1][1].append(href)

# ---------------------------------------------------------------- copy
DESC = {
    "breadth.html": "How much of the market is actually taking part: advancers against decliners, stocks above their 20, 50 and 200-day averages, and names near 52-week highs and lows.",
    "technofunda.html": "Every listed company scored on outperforming results, relative strength and quality, with GARP, rising-profit and pullback screens built in.",
    "pead.html": "Post-earnings drift: fresh results with strong sales and profit growth, ranked by PEAD score and tracked from the day they were screened.",
    "magicformula.html": "Greenblatt's Magic Formula: businesses ranked on return on capital against earnings yield, so quality is bought cheaply.",
    "intraday.html": "The day's biggest gainers and losers with VWAP and traded volume. Refreshes while the local realtime runner is on.",
    "orders.html": "Order wins and contract awards picked out of company filings, so a large win is read against the size of the business.",
    "buybacks.html": "Tender-offer buybacks with the acceptance-ratio maths done, to estimate what the arbitrage actually returns.",
    "special.html": "Event-driven situations surfaced from corporate filings: the one-offs that move a stock apart from its results.",
    "demergers.html": "Demergers followed from announcement through NCLT and record date to listing, grouped by stage.",
    "sector.html": "Earnings momentum, breadth, flows and quality rolled up per sector into a headwind or tailwind. Open any sector for its stocks.",
    "industries.html": "Every listed company rolled up by industry instead of 22 coarse sectors, compared on median P/E, ROCE, margins and growth.",
    "ivranking.html": "Good businesses at a sensible price: a growth, quality and value funnel alongside the Magic Formula, with the best in each sector.",
    "returnmaps.html": "Ten-year price CAGR against each factor, showing which zones of growth, quality and cheapness actually paid off.",
    "fairvalue.html": "Intrinsic value per stock from a two-stage DCF, ranked by the discount to today's price.",
    "valuation.html": "Project earnings forward, value them on an exit P/E, and back-solve the price to pay for the return you want.",
    "fundamental.html": "Look up any listed company by name or BSE code for the full picture: statements, ratios, signals, documents and the bull/bear debate.",
    "stdrl.html": "Reinforcement-learning trading agents trained and compared on monthly bars, with each agent's equity curve.",
    "marketmood.html": "A single reading of market sentiment built from breadth and daily closes, with its recent history.",
    "fii.html": "Where foreign institutional money is going, sector by sector, fortnight by fortnight.",
    "fpi.html": "Foreign portfolio investment by sector over time: which sectors are being bought and which are being sold.",
    "banking.html": "Listed banks compared side by side on financing margin, return on equity and assets, profit margin and growth.",
    "auto.html": "Monthly vehicle registrations by maker and vehicle category, with market share and year-on-year change.",
    "deals.html": "Bulk, block and insider deals: who is buying and selling in size.",
    "announcements.html": "The latest corporate announcements across listed companies, as they are filed.",
    "actions.html": "Upcoming dividends, bonuses, splits and rights issues across listed companies, with their ex-dates.",
}
missing = [h for h in items if h not in DESC]
assert not missing, f"no description written for: {missing}"

# file, stat (JS arrow over the parsed JSON, or None)
LOAD = {
    "breadth.html": ("data/marketmood_meta.json", "d=>n(d.resolved)+' stocks read'"),
    "technofunda.html": ("data/technofunda_meta.json", "d=>n(d.universe)+' scored \u00b7 '+n(d.buy)+' BUY'"),
    "pead.html": ("data/meta.json", "d=>n(d.total)+' results \u00b7 '+n(d.high)+' HIGH'"),
    "magicformula.html": ("data/magicformula_meta.json", "d=>n(d.ranked)+' ranked'"),
    "intraday.html": ("data/intraday.json", "d=>d.rows&&d.rows.length?n(d.rows.length)+' movers':''"),
    "orders.html": ("data/orders_meta.json", "d=>n(d.orders)+' order wins'"),
    "buybacks.html": ("data/buybacks_meta.json", "d=>n(d.buybacks)+' buybacks \u00b7 '+n(d.candidates)+' candidates'"),
    "special.html": ("data/special_meta.json", "d=>n(d.count)+' situations'"),
    "demergers.html": ("data/demergers_meta.json", "d=>n(d.count)+' tracked'"),
    "sector.html": ("data/sector_tailwind.json", "d=>n((d.sectors||[]).length)+' sectors'"),
    "industries.html": ("data/industries.json", "d=>n((d.levels||{}).subgroup)+' industries'"),
    "ivranking.html": ("data/iv_ranking.json", "d=>n(d.candidates)+' candidates'"),
    "returnmaps.html": ("data/iv_returnmap.json", "d=>n((d.rows||[]).length)+' stocks mapped'"),
    "fairvalue.html": ("data/iv_fairvalue.json", "d=>n((d.rows||[]).length)+' valued'"),
    "stdrl.html": ("data/stdrl.json", "d=>d.best_agent?'best agent: '+d.best_agent:''"),
    "marketmood.html": ("data/marketmood_meta.json", "d=>n(d.history_days)+' days of history'"),
    "fii.html": ("data/fii.json", "d=>n((d.sectors||[]).length)+' sectors'"),
    "fpi.html": ("data/fpi.json", None),
    "banking.html": ("data/banking_meta.json", "d=>n(d.banks)+' banks'"),
    "auto.html": ("data/auto_meta.json", "d=>n(d.makers)+' makers'"),
    "deals.html": ("data/deals.json", "d=>n((d.rows||[]).length)+' deals'"),
    "announcements.html": ("data/announcements.json", "d=>n((d.rows||[]).length)+' announcements'"),
    "actions.html": ("data/actions.json", "d=>n((d.rows||[]).length)+' actions'"),
}
dropped = [h for h, (f, _) in LOAD.items() if not (DOCS / f).exists()]
for h in dropped:
    print(f"  !! {h}: {LOAD[h][0]} not on disk, stat dropped")
    del LOAD[h]

# verify the two descriptions that make specific claims
bank = json.loads((DOCS / "data" / "banking.json").read_text(encoding="utf-8"))
print("banking metrics :", [m.get("label", m.get("key", m)) if isinstance(m, dict) else m for m in bank.get("metrics", [])])
acts = json.loads((DOCS / "data" / "actions.json").read_text(encoding="utf-8"))
print("actions sample  :", [{k: r.get(k) for k in list(r)[:5]} for r in acts.get("rows", [])[:3]])

# ---------------------------------------------------------------- markup
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

cards = []
for title, hrefs in sections:
    cells = []
    for h in hrefs:
        icon, label = items[h]
        cells.append(
            f'      <a class="mod" href="{h}" data-mod="{h}">\n'
            f'        <div class="mod-top"><span class="mod-ic">{icon}</span>'
            f'<span class="mod-t">{esc(label)}</span><span class="mod-f"></span></div>\n'
            f'        <p class="mod-d">{esc(DESC[h])}</p>\n'
            f'        <div class="mod-s"></div>\n'
            f'      </a>')
    cards.append(
        f'    <section class="sec">\n'
        f'      <h2 class="sec-h">{esc(title)}</h2>\n'
        f'      <div class="grid">\n' + "\n".join(cells) + "\n      </div>\n    </section>")

# Days a module may go without new data before its badge turns amber, where
# the default three days is wrong for it. Vahan publishes registrations once a
# month and is refreshed from a desktop, and STDRL trains weekly on monthly
# bars: holding either to three days showed them stale most of the time and
# taught readers to ignore the badge.
MAX_AGE = {"auto.html": 35, "stdrl.html": 8}

loaders = ",\n".join(
    f'  "{h}":{{f:"{f}",s:{s if s else "null"}'
    + (f',m:{MAX_AGE[h]}' if h in MAX_AGE else '') + '}'
    for h, (f, s) in LOAD.items())

page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>scanX \u00b7 NSE / BSE terminal</title>
{social}
<link rel="stylesheet" href="vendor/theme.css">
  <script src="vendor/theme.js"></script>
  <style>
*{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
  a{{color:var(--teal2);text-decoration:none}}a:hover{{text-decoration:underline}}
{navcss}  .content{{flex:1;min-width:0;display:flex;flex-direction:column}}
  header.hero{{padding:22px 26px 18px;border-bottom:1px solid var(--line);background:var(--panel)}}
  .hero h1{{margin:0;font-size:24px;font-weight:800;letter-spacing:-.2px}}
  .hero h1 b{{color:var(--teal)}}
  .hero p{{margin:6px 0 0;color:var(--muted);font-size:13px;max-width:780px}}
  .kpis{{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}}
  .kpi{{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:150px}}
  .kpi .v{{font-size:19px;font-weight:800}}
  .kpi .l{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}}
  .kpi .v.g{{color:var(--green)}}.kpi .v.a{{color:var(--amber)}}
  .wrap{{padding:6px 26px 40px;max-width:1440px}}
  .sec{{margin-top:22px}}
  .sec-h{{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}}
  a.mod{{display:flex;flex-direction:column;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 15px;color:var(--text);transition:border-color .12s ease,transform .12s ease}}
  a.mod:hover{{border-color:var(--teal);text-decoration:none;transform:translateY(-1px)}}
  a.mod:focus-visible{{outline:2px solid var(--teal);outline-offset:2px}}
  .mod-top{{display:flex;align-items:center;gap:9px}}
  .mod-ic{{font-size:19px;width:24px;text-align:center}}
  .mod-t{{font-weight:800;font-size:14.5px}}
  .mod-f{{margin-left:auto;font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:999px;color:var(--muted);background:var(--chip);white-space:nowrap}}
  .mod-f:empty{{display:none}}
  .mod-f.ok{{color:var(--green);background:rgba(52,211,153,.10)}}
  .mod-f.stale{{color:var(--amber);background:rgba(251,191,36,.12)}}
  .mod-d{{margin:0;color:var(--muted);font-size:12.5px;line-height:1.5;flex:1}}
  .mod-s{{font-size:12px;font-weight:700;color:var(--teal);min-height:16px}}
  .foot{{margin-top:30px;font-size:11px;color:var(--muted)}}
</style>
</head>
<body>
<div class="app">
  {nav}

  <div class="content">
    <header class="hero">
      <h1><b>scanX</b> \u00b7 NSE / BSE terminal</h1>
      <p>Fundamental screening, intrinsic value and market breadth across every listed Indian company. Pick a module below; each card shows what it does and how fresh its data is.</p>
      <div class="kpis">
        <div class="kpi"><div class="v" id="kUni">\u2013</div><div class="l">Companies scored</div></div>
        <div class="kpi"><div class="v g" id="kBuy">\u2013</div><div class="l">BUY signals</div></div>
        <div class="kpi"><div class="v" id="kQuotes">\u2013</div><div class="l">Quotes as of</div></div>
        <div class="kpi"><div class="v" id="kFresh">\u2013</div><div class="l">Modules up to date</div></div>
      </div>
    </header>

    <div class="wrap">
{chr(10).join(cards)}
      <div class="foot">Rules-based screens for research and education, not investment advice. Freshness badges turn amber once a module's data is more than three days old (five weeks for monthly vehicle data, eight days for the weekly model).</div>
    </div>
  </div>
</div>

<script>
/* Stats and freshness are the only scripted part: every card above is plain
   markup, so the page still works when a data file fails to load. */
const STALE_DAYS=3;
const n=v=>v==null?'\u2013':Number(v).toLocaleString('en-IN');
const MODS={{
{loaders}
}};
function stampOf(d){{
  const s=String((d&&(d.generated_at_ist||d.generated_at||d.date))||'');
  const m=s.match(/(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
  return m?new Date(+m[1],+m[2]-1,+m[3]):null;
}}
function daysOld(dt){{
  const a=new Date(); a.setHours(0,0,0,0);
  const b=new Date(dt); b.setHours(0,0,0,0);
  return Math.max(0,Math.round((a-b)/864e5));
}}
const agoTxt=d=>d===0?'today':(d===1?'1 day ago':d+' days ago');
const cache={{}};
const grab=f=>cache[f]||(cache[f]=fetch(f+'?t='+Date.now()).then(r=>r.ok?r.json():null).catch(()=>null));

async function paint(){{
  let fresh=0, dated=0;
  await Promise.all(Object.keys(MODS).map(async href=>{{
    const card=document.querySelector('a.mod[data-mod="'+href+'"]');
    if(!card) return;
    const d=await grab(MODS[href].f);
    if(!d) return;
    const dt=stampOf(d);
    if(dt){{
      const age=daysOld(dt), badge=card.querySelector('.mod-f');
      dated++;
      // A stale module is flagged, never hidden: several boards went weeks
      // without updating and nothing on the site said so.
      if(age>(MODS[href].m||STALE_DAYS)){{ badge.className='mod-f stale';
        badge.title='No new data for '+age+' days'; }}
      else {{ badge.className='mod-f ok'; fresh++; }}
      badge.textContent=agoTxt(age);
    }}
    try{{ const s=MODS[href].s&&MODS[href].s(d);
      if(s) card.querySelector('.mod-s').textContent=s; }}catch(e){{}}
  }}));
  if(dated){{ const el=document.getElementById('kFresh');
    el.textContent=fresh+' / '+dated; el.className='v '+(fresh===dated?'g':'a'); }}
}}

async function header(){{
  const [tf,q]=await Promise.all([grab('data/technofunda_meta.json'),grab('data/quotes.json')]);
  if(tf){{ document.getElementById('kUni').textContent=n(tf.universe);
    document.getElementById('kBuy').textContent=n(tf.buy); }}
  if(q&&q.ist){{
    const live=((Date.now()/1000)-(q.ts||0))<5400;
    document.getElementById('kQuotes').textContent=q.ist+(live?'':' \u00b7 '+(q.date||''));
  }}
}}
paint(); header();
</script>
<script src="vendor/nav.js"></script>
</body>
</html>
"""
(DOCS / "index.html").write_text(page, encoding="utf-8")
print(f"wrote docs/index.html: {sum(len(h) for _, h in sections)} cards in {len(sections)} sections, "
      f"{len(LOAD)} with live stats")
for title, hrefs in sections:
    print(f"  {title:<16}{len(hrefs)}  " + ", ".join(items[h][1] for h in hrefs))
