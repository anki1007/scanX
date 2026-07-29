/* scanX theme picker — progressive enhancement, no dependencies.
 *
 * Applies the saved theme BEFORE first paint (the top of this file runs
 * synchronously, so load it with a plain <script src> in <head>, not defer).
 * A deferred load would paint the default palette first and then repaint,
 * which reads as a flash on every navigation of a multi-page static site.
 *
 * The picker itself is built after DOM ready. If JS is off, the page keeps the
 * default dark theme and stays fully usable — the stylesheet carries it.
 */
(function () {
  "use strict";

  var KEY = "scanx.theme.v1";
  var LEGIBLE_KEY = "scanx.legible.v1";
  var THEMES = [
    { id: "dark",          label: "Midnight",      swatch: "#0c111d" },
    { id: "emerald-blue",  label: "Emerald Blue",  swatch: "#0d2135" },
    { id: "emerald-green", label: "Emerald Green", swatch: "#0c2018" },
    { id: "light-beige",   label: "Light Beige",   swatch: "#f4efe4" }
  ];

  function ids() {
    var out = {};
    for (var i = 0; i < THEMES.length; i++) out[THEMES[i].id] = 1;
    return out;
  }

  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; }
    catch (e) { return fallback; }          // private mode / storage disabled
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* not fatal */ }
  }

  // ---- applied immediately, before the body renders
  var saved = read(KEY, "dark");
  if (!ids()[saved]) saved = "dark";
  document.documentElement.setAttribute("data-theme", saved);

  // Larger-text mode defaults ON: the pages carry 176 font-size rules below
  // 12px (11px x102, 10px x54, 9px x13), which is the single biggest reason
  // small labels read as faint. Anyone who prefers the tighter type can turn
  // it off and the choice sticks.
  var legible = read(LEGIBLE_KEY, "on") !== "off";
  if (legible) document.documentElement.classList.add("t-legible");

  function css() {
    return [
      ".t-picker{position:fixed;right:14px;bottom:14px;z-index:9999;font:500 13px/1.2 " +
        "system-ui,-apple-system,Segoe UI,Roboto,sans-serif}",
      ".t-btn{display:flex;align-items:center;gap:7px;background:var(--panel);" +
        "color:var(--text);border:1px solid var(--line);border-radius:9px;" +
        "padding:8px 11px;cursor:pointer;font:inherit;box-shadow:0 2px 10px rgba(0,0,0,.28)}",
      ".t-btn:hover{border-color:var(--accent)}",
      ".t-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}",
      ".t-dot{width:13px;height:13px;border-radius:50%;border:1px solid var(--line);flex:0 0 auto}",
      ".t-menu{display:none;position:absolute;right:0;bottom:46px;background:var(--panel);" +
        "border:1px solid var(--line);border-radius:11px;padding:6px;min-width:196px;" +
        "box-shadow:0 8px 26px rgba(0,0,0,.36)}",
      ".t-menu[data-open]{display:block}",
      ".t-item{display:flex;align-items:center;gap:9px;width:100%;background:none;" +
        "border:0;color:var(--text);padding:9px 10px;border-radius:7px;cursor:pointer;" +
        "font:inherit;text-align:left}",
      ".t-item:hover{background:var(--panel2)}",
      ".t-item:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}",
      ".t-item[aria-checked=true]{color:var(--accent);font-weight:600}",
      ".t-sep{height:1px;background:var(--line);margin:6px 4px}",
      "@media print{.t-picker{display:none}}"
    ].join("");
  }

  function build() {
    if (document.querySelector(".t-picker")) return;

    var style = document.createElement("style");
    style.textContent = css();
    document.head.appendChild(style);

    var wrap = document.createElement("div");
    wrap.className = "t-picker";

    var menu = document.createElement("div");
    menu.className = "t-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Colour theme");

    var btn = document.createElement("button");
    btn.className = "t-btn";
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");

    var dot = document.createElement("span");
    dot.className = "t-dot";
    var btnText = document.createElement("span");
    btn.appendChild(dot);
    btn.appendChild(btnText);

    function current() {
      var now = document.documentElement.getAttribute("data-theme") || "dark";
      for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === now) return THEMES[i];
      return THEMES[0];
    }
    function paintButton() {
      var t = current();
      dot.style.background = t.swatch;
      btnText.textContent = t.label;
      var items = menu.querySelectorAll(".t-item[data-theme]");
      for (var i = 0; i < items.length; i++) {
        items[i].setAttribute("aria-checked",
          items[i].getAttribute("data-theme") === t.id ? "true" : "false");
      }
    }

    THEMES.forEach(function (t) {
      var it = document.createElement("button");
      it.className = "t-item";
      it.type = "button";
      it.setAttribute("role", "menuitemradio");
      it.setAttribute("data-theme", t.id);
      var d = document.createElement("span");
      d.className = "t-dot";
      d.style.background = t.swatch;
      var label = document.createElement("span");
      label.textContent = t.label;
      it.appendChild(d);
      it.appendChild(label);
      it.addEventListener("click", function () {
        document.documentElement.setAttribute("data-theme", t.id);
        write(KEY, t.id);
        paintButton();
        close();
      });
      menu.appendChild(it);
    });

    menu.appendChild(Object.assign(document.createElement("div"), { className: "t-sep" }));

    var big = document.createElement("button");
    big.className = "t-item";
    big.type = "button";
    big.setAttribute("role", "menuitemcheckbox");
    function paintBig() {
      var on = document.documentElement.classList.contains("t-legible");
      big.setAttribute("aria-checked", on ? "true" : "false");
      big.textContent = (on ? "✓  " : "  ") + "Larger, higher-contrast text";
    }
    big.addEventListener("click", function () {
      var on = document.documentElement.classList.toggle("t-legible");
      write(LEGIBLE_KEY, on ? "on" : "off");
      paintBig();
    });
    paintBig();
    menu.appendChild(big);

    function close() {
      menu.removeAttribute("data-open");
      btn.setAttribute("aria-expanded", "false");
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = menu.hasAttribute("data-open");
      if (open) { close(); return; }
      menu.setAttribute("data-open", "1");
      btn.setAttribute("aria-expanded", "true");
    });
    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) close();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    wrap.appendChild(menu);
    wrap.appendChild(btn);
    document.body.appendChild(wrap);
    paintButton();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
