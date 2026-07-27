/* scanX shared left-rail behaviour — collapsible groups + collapsible rail.
 *
 * Progressive enhancement: every page still ships its own plain <aside class="nav">
 * markup, so navigation keeps working if this file fails to load. All this does is
 * wrap each "grpL" heading and the nav-i links that follow it into a toggleable
 * group, and add a rail collapse button.
 *
 * Groups start COLLAPSED. A user's expand/collapse choices persist across pages
 * (localStorage) so the rail doesn't re-collapse on every navigation, and the
 * group holding the current page is marked so you can see where you are while
 * everything is shut.
 *
 * Include once, after the markup:  <script src="vendor/nav.js"></script>
 */
(function () {
  "use strict";
  var KEY = "scanx.nav.v1";

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* private mode */ }
  }

  function css() {
    if (document.getElementById("scanx-nav-css")) return;
    var el = document.createElement("style");
    el.id = "scanx-nav-css";
    el.textContent = [
      ".nav .grpL{cursor:pointer;display:flex;align-items:center;gap:6px;",
      "  user-select:none;border-radius:6px;padding:4px 6px;margin-left:4px;margin-right:4px;",
      "  transition:color .12s ease,background .12s ease}",
      ".nav .grpL:hover{color:var(--teal,#2dd4bf);background:rgba(255,255,255,.04)}",
      ".nav .grpL .cv{font-size:9px;line-height:1;transition:transform .16s ease;opacity:.75}",
      ".nav .grpL.open .cv{transform:rotate(90deg)}",
      /* a dot on a shut group that contains the page you're on */
      ".nav .grpL .hasact{width:5px;height:5px;border-radius:50%;background:var(--teal,#2dd4bf);",
      "  margin-left:auto;flex:0 0 auto}",
      ".nav .grpL.open .hasact{display:none}",
      ".nav .nav-grp{display:none}",
      ".nav .nav-grp.open{display:block}",
      /* rail collapse */
      ".nav .railtog{position:absolute;top:14px;right:8px;width:22px;height:22px;line-height:20px;",
      "  text-align:center;border:1px solid var(--line,#243049);border-radius:6px;cursor:pointer;",
      "  color:var(--muted,#8b9bb4);font-size:12px;background:var(--panel2,#1b2438);z-index:5}",
      ".nav .railtog:hover{color:var(--teal,#2dd4bf);border-color:var(--teal,#2dd4bf)}",
      ".nav{position:relative}",
      ".nav.railmin{width:58px!important;flex-basis:58px!important;padding-left:6px;padding-right:6px}",
      ".nav.railmin .brand small,.nav.railmin .nav-i span:not(.ic),",
      ".nav.railmin .grpL .lbl,.nav.railmin .grpL .cv,.nav.railmin .grpL .hasact{display:none}",
      ".nav.railmin .brand{font-size:0;text-align:center}",
      ".nav.railmin .brand::after{content:'sX';font-size:15px;color:var(--teal,#2dd4bf)}",
      ".nav.railmin .grpL{justify-content:center;padding:4px 0;margin:10px 0 2px;opacity:.5}",
      ".nav.railmin .grpL::after{content:'';display:block;width:16px;border-top:1px solid var(--line,#243049)}",
      ".nav.railmin .nav-grp{display:block}",           /* icons stay reachable when minimised */
      ".nav.railmin .nav-i{justify-content:center;padding:9px 0}"
      /* no width media-query here: shrinking the rail without ALSO hiding the
         labels breaks the layout. Narrow screens get the real railmin class
         from init() instead, which hides labels and keeps icons usable. */
    ].join("");
    document.head.appendChild(el);
  }

  function build(nav) {
    var state = load();
    var here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
    var kids = Array.prototype.slice.call(nav.children);
    var groups = [];
    var cur = null;

    kids.forEach(function (el) {
      if (el.classList.contains("grpL")) {
        cur = { head: el, items: [] };
        groups.push(cur);
      } else if (cur && el.classList.contains("nav-i")) {
        cur.items.push(el);
      }
    });
    if (!groups.length) return;

    groups.forEach(function (g, i) {
      var name = (g.head.textContent || ("g" + i)).trim();
      // wrap this group's links so one element can be animated
      var box = document.createElement("div");
      box.className = "nav-grp";
      g.head.parentNode.insertBefore(box, g.items[0] || g.head.nextSibling);
      g.items.forEach(function (a) {
        if (!a.title) a.title = (a.textContent || "").trim();   // tooltip when minimised
        box.appendChild(a);
      });

      // heading: caret + label (+ dot when it holds the active page)
      var label = g.head.textContent;
      var active = g.items.some(function (a) {
        return (a.getAttribute("href") || "").toLowerCase() === here;
      });
      g.head.textContent = "";
      var cv = document.createElement("span");
      cv.className = "cv"; cv.textContent = "▶";
      var lb = document.createElement("span");
      lb.className = "lbl"; lb.textContent = label;
      g.head.appendChild(cv); g.head.appendChild(lb);
      if (active) {
        var dot = document.createElement("span");
        dot.className = "hasact";
        dot.title = "current page is in this group";
        g.head.appendChild(dot);
      }
      g.head.setAttribute("role", "button");
      g.head.setAttribute("tabindex", "0");

      // COLLAPSED by default; only a remembered choice opens it
      var open = state[name] === true;
      function apply() {
        box.classList.toggle("open", open);
        g.head.classList.toggle("open", open);
        g.head.setAttribute("aria-expanded", open ? "true" : "false");
      }
      apply();

      function toggle() {
        open = !open;
        state[name] = open;
        save(state);
        apply();
      }
      g.head.addEventListener("click", toggle);
      g.head.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });

    // rail collapse toggle
    var tog = document.createElement("div");
    tog.className = "railtog";
    tog.title = "Collapse / expand sidebar";
    nav.appendChild(tog);
    // narrow viewport starts minimised (icons only) unless the user chose
    // otherwise. Guard against a 0 width — some embedded/headless viewers
    // report that before first paint and it must not mean "phone".
    var vw = window.innerWidth || document.documentElement.clientWidth || 0;
    var min = state.__rail === true ||
              (state.__rail === undefined && vw > 0 && vw <= 760);
    function paint() {
      nav.classList.toggle("railmin", min);
      tog.textContent = min ? "»" : "«";
    }
    paint();
    tog.addEventListener("click", function () {
      min = !min; state.__rail = min; save(state); paint();
    });
  }

  function init() {
    var nav = document.querySelector("aside.nav") || document.querySelector(".navrail");
    if (!nav) return;
    if (!nav.classList.contains("nav")) nav.classList.add("nav");   // fpi.html uses .navrail
    css();
    try { build(nav); } catch (e) { /* never break a page over chrome */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
