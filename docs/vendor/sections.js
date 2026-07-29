/* scanX shared collapsible sections — every ".sec > h3" becomes a real header.
 *
 * Progressive enhancement, same spirit as vendor/nav.js: the page still ships
 * plain <div class="sec"><h3>…</h3>…</div> markup and reads fine if this file
 * never loads. All this adds is a caret, a hit area, keyboard support and a
 * remembered open/closed state, plus one "Collapse all / Expand all" control at
 * the top of the content area.
 *
 * DEFAULT: sections stay OPEN — unlike the nav rail, whose groups start shut.
 * A page can opt one section out by marking it <div class="sec sec-collapsed">,
 * which starts that one closed. A remembered choice always wins over both.
 *
 * State lives in localStorage under "scanx.sections.v1", keyed per page and per
 * section, so collapsing "Financial statements" on the fundamental page doesn't
 * collapse a same-named section elsewhere.
 *
 * Open/close is DISPLAY toggling, deliberately not an animated max-height: a
 * background tab throttles CSS transitions, and a section caught mid-transition
 * looks permanently stuck (this already bit the nav rail).
 *
 * Pages that render their sections asynchronously (fundamental.html rebuilds
 * #out after every lookup) are covered by a MutationObserver, so freshly
 * injected sections get enhanced without the page calling anything. It is still
 * exposed as window.scanxSections.refresh() for an explicit nudge.
 *
 * Include once, after the markup:  <script src="vendor/sections.js"></script>
 */
(function () {
  "use strict";
  var KEY = "scanx.sections.v1";
  var PAGE = (location.pathname.split("/").pop() || "index.html").toLowerCase();

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode */ }
  }
  var state = load();

  function css() {
    if (document.getElementById("scanx-sections-css")) return;
    var el = document.createElement("style");
    el.id = "scanx-sections-css";
    el.textContent = [
      /* the heading becomes the control — keep the page's own type, add a hit area */
      ".sec>h3.secx{cursor:pointer;display:flex;align-items:center;gap:8px;user-select:none;",
      "  transition:color .12s ease,border-color .12s ease}",
      ".sec>h3.secx:hover{color:var(--teal,#2dd4bf);border-bottom-color:var(--teal,#2dd4bf)}",
      ".sec>h3.secx:active{transform:translateY(1px)}",
      ".sec>h3.secx:focus-visible{outline:2px solid var(--teal,#2dd4bf);outline-offset:3px}",
      ".sec>h3.secx .secx-cv{font-size:9px;line-height:1;opacity:.75;flex:0 0 auto;",
      "  transition:transform .16s ease}",
      ".sec>h3.secx.secx-open .secx-cv{transform:rotate(90deg)}",
      ".sec>h3.secx .secx-lbl{min-width:0}",
      ".sec>h3.secx .secx-hint{margin-left:auto;flex:0 0 auto;font-size:10px;font-weight:600;",
      "  letter-spacing:.03em;opacity:.6}",
      ".sec>h3.secx.secx-open .secx-hint{display:none}",
      /* display, never max-height: a throttled transition must not strand a section */
      ".secx-body{display:block}",
      ".sec.secx-shut>.secx-body{display:none}",
      ".sec.secx-shut>h3.secx{margin-bottom:0}",
      /* collapse-all / expand-all */
      ".secx-bar{display:flex;gap:8px;align-items:center;margin:0 0 14px}",
      ".secx-bar button{background:var(--panel2,#1b2438);border:1px solid var(--line,#243049);",
      "  color:var(--muted,#8b9bb4);border-radius:7px;padding:5px 12px;font:inherit;font-size:12px;",
      "  font-weight:700;cursor:pointer;transition:color .12s ease,border-color .12s ease}",
      ".secx-bar button:hover{color:var(--teal,#2dd4bf);border-color:var(--teal,#2dd4bf)}",
      ".secx-bar button:focus-visible{outline:2px solid var(--teal,#2dd4bf);outline-offset:2px}"
    ].join("");
    document.head.appendChild(el);
  }

  /* every .sec that owns a DIRECT h3 child — nested headings (.pc .pros h3) stay
     plain text, they are not sections of their own */
  function heads() {
    var out = [];
    var secs = document.querySelectorAll(".sec");
    for (var i = 0; i < secs.length; i++) {
      var kid = secs[i].firstElementChild;
      while (kid && kid.tagName !== "H3") kid = kid.nextElementSibling;
      if (kid) out.push({ sec: secs[i], h3: kid });
    }
    return out;
  }

  /* the heading's own words — never the caret and "show" this file injected,
     which is why an already-dressed heading is read through its label span */
  function titleOf(h3) {
    var lbl = h3.querySelector(".secx-lbl") || h3;
    return (lbl.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  /* Key on the section's id when it has one — #priceSec rewrites its own <h3> to
     append the ticker, and a title-derived key would then change per company. */
  function keyFor(sec, h3, used) {
    var base = sec.id ? "#" + sec.id : titleOf(h3);
    if (!base) base = "sec";
    var k = PAGE + "|" + base, n = 2;
    while (used[k]) { k = PAGE + "|" + base + "#" + n; n++; }
    used[k] = true;
    return k;
  }

  function apply(sec, h3, open) {
    sec.classList.toggle("secx-shut", !open);
    h3.classList.toggle("secx-open", open);
    h3.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function isOpen(sec, key) {
    var v = state[key];
    if (v === true || v === false) return v;
    return !sec.classList.contains("sec-collapsed");   // OPEN unless the page says otherwise
  }

  function dress(sec, h3) {
    // wrap everything after the heading so ONE element carries the display flip
    var box = document.createElement("div");
    box.className = "secx-body";
    var n = h3.nextSibling;
    while (n) { var next = n.nextSibling; box.appendChild(n); n = next; }
    sec.appendChild(box);

    var lbl = document.createElement("span");
    lbl.className = "secx-lbl";
    while (h3.firstChild) lbl.appendChild(h3.firstChild);   // keep inline markup intact
    var cv = document.createElement("span");
    cv.className = "secx-cv";
    cv.textContent = "▶";
    var hint = document.createElement("span");
    hint.className = "secx-hint";
    hint.textContent = "show";
    h3.appendChild(cv); h3.appendChild(lbl); h3.appendChild(hint);

    h3.classList.add("secx");
    h3.setAttribute("role", "button");
    h3.setAttribute("tabindex", "0");
    h3.dataset.secx = "1";

    function toggle() {
      var key = h3.dataset.secxKey;
      var open = !isOpen(sec, key);
      state[key] = open;
      save(state);
      apply(sec, h3, open);
    }
    h3.addEventListener("click", toggle);
    h3.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault(); toggle();
      }
    });
  }

  function setAll(open) {
    var list = heads();
    for (var i = 0; i < list.length; i++) {
      var key = list[i].h3.dataset.secxKey;      // assigned by enhance(); no re-derive
      if (!key) continue;
      state[key] = open;
      apply(list[i].sec, list[i].h3, open);
    }
    save(state);
  }

  function bar(count) {
    var el = document.getElementById("scanx-secbar");
    if (!el) {
      var host = document.querySelector(".content .wrap")
              || document.querySelector(".content") || document.body;
      el = document.createElement("div");
      el.id = "scanx-secbar";
      el.className = "secx-bar";
      el.appendChild(button("Collapse all", function () { setAll(false); }));
      el.appendChild(button("Expand all", function () { setAll(true); }));
      host.insertBefore(el, host.firstChild);
    }
    el.style.display = count ? "" : "none";   // no sections yet -> no stray control
  }
  function button(text, fn) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    b.addEventListener("click", fn);
    return b;
  }

  /* Idempotent: run it as often as you like. Already-dressed headings only have
     their key re-derived (document order can shift when a page re-renders). */
  function enhance() {
    var list = heads(), used = {};
    for (var i = 0; i < list.length; i++) {
      var sec = list[i].sec, h3 = list[i].h3;
      if (!h3.dataset.secx) dress(sec, h3);
      h3.dataset.secxKey = keyFor(sec, h3, used);
      apply(sec, h3, isOpen(sec, h3.dataset.secxKey));
    }
    bar(list.length);
    return list.length;
  }

  var busy = false, pending = null;
  function run() {
    busy = true;
    try { enhance(); } catch (e) { /* never break a page over chrome */ }
    busy = false;
  }
  function schedule() {
    if (pending) return;
    pending = setTimeout(function () { pending = null; run(); }, 40);
  }

  function init() {
    css();
    run();
    try {
      // fundamental.html rebuilds #out on every lookup, and #priceSec / #docsSec
      // fill in later still — watch for those instead of asking pages to call us
      new MutationObserver(function () { if (!busy) schedule(); })
        .observe(document.body, { childList: true, subtree: true });
    } catch (e) { /* no observer: static pages are already done */ }
    window.scanxSections = {
      refresh: run,
      collapseAll: function () { setAll(false); },
      expandAll: function () { setAll(true); }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
