/* 3D market-breadth particle field — pure canvas, no dependencies.
 *
 * One particle per stock, positioned on a sphere and rotated in 3D. Colour is
 * the only encoding that matters: advancing vs declining. The shape is not
 * decoration — the advancing particles are pulled toward the core and the
 * decliners pushed out, so a strong-breadth day visibly clusters and a weak one
 * visibly disperses. Reading the picture and reading the number agree.
 *
 * Deliberately dependency-free: this repo publishes a static site with a strict
 * offline story, and a WebGL/three.js build would be ~600KB for a scatter that
 * canvas draws in 8KB. It also keeps the CSP clean.
 *
 *   Breadth3D.mount(canvas, {stocks:[{code,pct,mcap}], onPick:fn})
 *
 * Honours prefers-reduced-motion (renders one static frame), pauses when the
 * tab is hidden or the canvas is scrolled out of view, and reads its colours
 * from the CSS variables so it follows the active theme.
 */
(function (global) {
  "use strict";

  var TAU = Math.PI * 2;

  function cssVar(el, name, fallback) {
    var v = getComputedStyle(el).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function hash(str) {                 // stable pseudo-random per code
    var h = 2166136261, i;
    for (i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return h / 4294967295;
  }

  function build(stocks, maxN) {
    // Sample DOWN if needed, but keep the advancing/declining ratio intact —
    // the whole point of the picture is that ratio, so a naive slice() of a
    // list that happens to be sorted would draw a lie.
    var up = [], down = [], i, s;
    for (i = 0; i < stocks.length; i++) {
      s = stocks[i];
      if (typeof s.pct !== "number" || !isFinite(s.pct)) continue;
      (s.pct >= 0 ? up : down).push(s);
    }
    var total = up.length + down.length;
    if (!total) return [];
    var keep = Math.min(total, maxN);
    var upKeep = Math.round(keep * (up.length / total));
    var downKeep = keep - upKeep;
    var picked = up.slice(0, upKeep).concat(down.slice(0, downKeep));

    var pts = [];
    for (i = 0; i < picked.length; i++) {
      s = picked[i];
      var seed = hash(s.code || String(i));
      var seed2 = hash((s.code || String(i)) + "y");
      // Fibonacci-ish sphere so points spread evenly rather than clumping at
      // the poles the way naive uniform angles do.
      var t = (i + 0.5) / picked.length;
      var phi = Math.acos(1 - 2 * t);
      var theta = Math.PI * (1 + Math.sqrt(5)) * i;
      var adv = s.pct >= 0;
      var mag = Math.min(Math.abs(s.pct) / 6, 1);
      // advancing pulls IN toward the core, declining pushes OUT
      var r = adv ? 0.52 + (1 - mag) * 0.30 : 0.95 + mag * 0.42;
      r *= 0.88 + seed * 0.24;
      pts.push({
        code: s.code, pct: s.pct, adv: adv, mag: mag,
        x: r * Math.sin(phi) * Math.cos(theta),
        y: r * Math.cos(phi),
        z: r * Math.sin(phi) * Math.sin(theta),
        size: 1 + Math.min((s.mcap ? Math.log10(s.mcap + 10) : 3) / 2.6, 2.4),
        tw: seed2 * TAU
      });
    }
    return pts;
  }

  function mount(canvas, opts) {
    opts = opts || {};
    var pts = build(opts.stocks || [], opts.max || 520);
    var ctx = canvas.getContext("2d");
    var rot = 0, raf = 0, running = true, hoverIdx = -1;
    var reduced = global.matchMedia &&
                  global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var W = 0, H = 0, proj = [];

    function resize() {
      var r = canvas.getBoundingClientRect();
      W = Math.max(1, r.width); H = Math.max(1, r.height);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw() {
      var upC = cssVar(canvas, "--green", "#4ade80");
      var dnC = cssVar(canvas, "--red", "#fca5a5");
      var lineC = cssVar(canvas, "--line", "#33456a");
      ctx.clearRect(0, 0, W, H);

      var cx = W / 2, cy = H / 2;
      var scale = Math.min(W, H) * 0.34;
      var cos = Math.cos(rot), sin = Math.sin(rot);
      var i, p, x, z, depth, k;

      proj.length = 0;
      for (i = 0; i < pts.length; i++) {
        p = pts[i];
        x = p.x * cos - p.z * sin;
        z = p.x * sin + p.z * cos;
        depth = 2.6 / (2.6 + z);          // perspective
        proj.push({
          i: i, sx: cx + x * scale * depth, sy: cy + p.y * scale * depth,
          d: depth, z: z, adv: p.adv, size: p.size, mag: p.mag
        });
      }
      proj.sort(function (a, b) { return a.z - b.z; });   // far first

      // edges: only between near neighbours, and only a bounded number, so the
      // cost stays linear-ish instead of O(n^2) drawing
      ctx.strokeStyle = lineC;
      ctx.globalAlpha = 0.30;
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      var step = proj.length > 320 ? 2 : 1;
      for (i = 0; i < proj.length; i += step) {
        var a = proj[i];
        for (k = i + 1; k < Math.min(i + 7, proj.length); k++) {
          var b = proj[k];
          var dx = a.sx - b.sx, dy = a.sy - b.sy;
          if (dx * dx + dy * dy < 2100) {
            ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy);
          }
        }
      }
      ctx.stroke();
      ctx.globalAlpha = 1;

      for (i = 0; i < proj.length; i++) {
        var q = proj[i];
        ctx.globalAlpha = 0.34 + q.d * 0.62;
        ctx.fillStyle = q.adv ? upC : dnC;
        ctx.beginPath();
        ctx.arc(q.sx, q.sy, Math.max(0.8, q.size * q.d * (q.i === hoverIdx ? 2.2 : 1)),
                0, TAU);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    function frame() {
      if (!running) return;
      rot += 0.0016;
      draw();
      raf = global.requestAnimationFrame(frame);
    }

    function start() {
      if (running && raf) return;
      running = true;
      if (reduced) { draw(); return; }
      raf = global.requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      if (raf) { global.cancelAnimationFrame(raf); raf = 0; }
    }

    resize();
    if (reduced) draw(); else start();

    var ro = global.ResizeObserver ? new ResizeObserver(function () {
      resize(); draw();
    }) : null;
    if (ro) ro.observe(canvas);

    // Do not burn a core animating something nobody is looking at.
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop(); else start();
    });
    if (global.IntersectionObserver) {
      new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) start(); else stop();
      }, { threshold: 0.02 }).observe(canvas);
    }

    canvas.addEventListener("mousemove", function (e) {
      var r = canvas.getBoundingClientRect();
      var mx = e.clientX - r.left, my = e.clientY - r.top, best = -1, bd = 144;
      for (var i = 0; i < proj.length; i++) {
        var dx = proj[i].sx - mx, dy = proj[i].sy - my, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = proj[i].i; }
      }
      hoverIdx = best;
      canvas.style.cursor = best >= 0 ? "pointer" : "default";
      canvas.title = best >= 0
        ? pts[best].code + "  " + (pts[best].pct >= 0 ? "+" : "") + pts[best].pct.toFixed(2) + "%"
        : "";
      if (reduced) draw();
    });
    canvas.addEventListener("mouseleave", function () {
      hoverIdx = -1; canvas.title = "";
    });
    canvas.addEventListener("click", function () {
      if (hoverIdx >= 0 && opts.onPick) opts.onPick(pts[hoverIdx]);
    });

    return { stop: stop, start: start, count: pts.length };
  }

  global.Breadth3D = { mount: mount, build: build };
})(window);
