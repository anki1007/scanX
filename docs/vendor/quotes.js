/* scanX shared quote feed - live LTP and day change for any board.
 *
 * Two files back this, and a page that reads only the first shows a dash on
 * ~91% of its rows:
 *
 *   data/quotes.json       ~500 names, BSE per-scrip, the direct exchange read
 *   data/quotes_wide.json  ~5,650 names, the Upstox market-quote pass over the
 *                          whole baked universe, with Yahoo filling its gaps
 *
 * Both refresh on the same ~20-minute cycle. Wide is laid down first and
 * narrow over it, so a code held by both keeps the direct read. Each file is
 * aged on its OWN timestamp, so a stale one is dropped without suppressing the
 * other, and nothing older than 90 minutes is applied at all -- a stale change
 * shown as today's is worse than no change at all.
 *
 * The feed is CACHED, and that is the point. A poller alone only reaches the
 * datasets that happen to exist when it fires: switch to a screen that loads
 * its rows afterwards and every row shows a dash until the next tick a minute
 * later. Pages call apply() the moment their rows arrive, and subscribe for
 * later refreshes.
 *
 *   <script src="vendor/quotes.js"></script>
 *   scanXQuotes.apply(rows);                       // right after rows load
 *   scanXQuotes.onUpdate(function(){ scanXQuotes.apply(rows); render(); });
 *
 * Progressive enhancement: if the files are missing or stale the page keeps
 * whatever price was baked into it, and nothing throws.
 */
(function () {
  "use strict";

  var NARROW = "data/quotes.json";
  var WIDE = "data/quotes_wide.json";
  var MAX_AGE = 5400;          // 90 minutes, in seconds
  var EVERY = 60000;           // poll cadence

  var feed = {};
  var stamp = "";
  var subs = [];
  var timer = null;
  var started = false;

  function fresh(j) {
    return !!(j && j.quotes && (Date.now() / 1000 - (j.ts || 0)) < MAX_AGE);
  }

  function grab(url) {
    return fetch(url + "?t=" + Date.now())
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  /* Mutates rows in place. Returns how many carried a quote.
   * priceKey/pctKey let a board that names its columns differently share this. */
  function apply(rows, opts) {
    if (!rows || !rows.length) return 0;
    opts = opts || {};
    var pk = opts.priceKey || "ltp";
    var ck = opts.pctKey || "pct";
    var n = 0;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r) continue;
      var q = feed[String(r.code || "").toUpperCase()];
      if (!q) continue;
      if (q.ltp != null) r[pk] = q.ltp;
      // Absent stays absent. A 0 here would render as "unchanged today",
      // which is a claim about the stock rather than about the feed.
      if (q.pct != null) r[ck] = q.pct;
      n++;
    }
    return n;
  }

  function refresh() {
    return Promise.all([grab(NARROW), grab(WIDE)]).then(function (both) {
      var narrow = both[0], wide = both[1];
      var next = {};
      if (fresh(wide)) { for (var a in wide.quotes) next[a] = wide.quotes[a]; }
      if (fresh(narrow)) { for (var b in narrow.quotes) next[b] = narrow.quotes[b]; }
      if (!Object.keys(next).length) return false;   // keep the last good feed
      feed = next;
      stamp = (fresh(narrow) && narrow.ist) || (fresh(wide) && wide.ist) || "";
      for (var i = 0; i < subs.length; i++) {
        try { subs[i](feed, stamp); } catch (e) { /* one bad page, not all */ }
      }
      return true;
    }).catch(function () { return false; });
  }

  function loop() {
    refresh().then(function () {
      clearTimeout(timer);
      timer = setTimeout(loop, EVERY);
    });
  }

  var api = {
    apply: apply,
    feed: function () { return feed; },
    stamp: function () { return stamp; },
    count: function () { return Object.keys(feed).length; },
    refresh: refresh,
    onUpdate: function (fn) { if (typeof fn === "function") subs.push(fn); },
    start: function () {
      if (started) return api.ready;
      started = true;
      api.ready = refresh().then(function (ok) {
        clearTimeout(timer);
        timer = setTimeout(loop, EVERY);
        return ok;
      });
      return api.ready;
    }
  };
  api.ready = null;
  window.scanXQuotes = api;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { api.start(); });
  } else {
    api.start();
  }
})();
