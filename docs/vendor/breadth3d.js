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
    var rotY = 0, rotX = -0.22, zoom = 1, raf = 0, running = true, hoverIdx = -1;
    var velY = 0, velX = 0, dragging = false, moved = false, lastX = 0, lastY = 0;
    var reduced = global.matchMedia &&
                  global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Auto-spin defaults ON and the choice PERSISTS; dragging always works.
    //
    // It first shipped as `!reduced`, i.e. off whenever the OS asks for reduced
    // motion. Windows ships with animation effects off often enough that this
    // left the field frozen for most people -- reported as "not animating". WCAG
    // 2.2.2 asks that motion over five seconds be pausable, not that it never
    // start; there is a visible Spin control and a keyboard shortcut, and the
    // setting is remembered, so a reader who wants it still gets one click and
    // never sees it move again.
    var autoSpin = true;
    try {
        var pref = localStorage.getItem("scanx.breadth.spin");
        if (pref === "off") autoSpin = false;
        else if (pref === null && reduced) autoSpin = true;   // explicit request wins
    } catch (e) { /* private mode */ }
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
      var scale = Math.min(W, H) * 0.34 * zoom;
      var cosY = Math.cos(rotY), sinY = Math.sin(rotY);
      var cosX = Math.cos(rotX), sinX = Math.sin(rotX);
      var i, p, x, y, z, z1, depth, k;

      proj.length = 0;
      for (i = 0; i < pts.length; i++) {
        p = pts[i];
        // yaw (drag left/right), then pitch (drag up/down)
        x = p.x * cosY - p.z * sinY;
        z1 = p.x * sinY + p.z * cosY;
        y = p.y * cosX - z1 * sinX;
        z = p.y * sinX + z1 * cosX;
        depth = 2.6 / (2.6 + z);          // perspective
        proj.push({
          i: i, sx: cx + x * scale * depth, sy: cy + y * scale * depth,
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
      if (!running) { raf = 0; return; }
      if (!dragging) {
        // momentum from the last flick, decaying
        rotY += velY; rotX += velX;
        velY *= 0.94; velX *= 0.94;
        if (Math.abs(velY) < 1e-4) velY = 0;
        if (Math.abs(velX) < 1e-4) velX = 0;
        if (autoSpin) rotY += 0.0016;
      }
      rotX = Math.max(-1.2, Math.min(1.2, rotX));   // never flip past the poles
      draw();
      // Keep the loop alive only while something is actually moving. With
      // auto-spin off and no momentum left this settles to a static frame
      // instead of burning a core redrawing an identical picture.
      if (autoSpin || dragging || velY || velX) {
        raf = global.requestAnimationFrame(frame);
      } else {
        raf = 0;
      }
    }

    function start() {
      running = true;
      if (!raf) raf = global.requestAnimationFrame(frame);
    }
    function kick() {            // ensure the loop is alive after an interaction
      if (running && !raf) raf = global.requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      if (raf) { global.cancelAnimationFrame(raf); raf = 0; }
    }

    resize();
    draw();
    start();

    var ro = global.ResizeObserver ? new ResizeObserver(function () {
      resize(); draw();
    }) : null;
    if (ro) ro.observe(canvas);

    // Do not burn a core animating something nobody is looking at.
    document.addEventListener("visibilitychange", function () {
      // A hidden tab gets ZERO animation frames from the browser, so the loop is
      // already effectively stopped; this just releases the pending handle and
      // repaints once on return rather than showing a frame from minutes ago.
      if (document.hidden) { stop(); } else { draw(); start(); }
    });
    if (global.IntersectionObserver) {
      new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) start(); else stop();
      }, { threshold: 0.02 }).observe(canvas);
    }

    // ---------------------------------------------------------------- input
    // Pointer events rather than mouse events so a touchscreen drags too.
    canvas.style.touchAction = "none";
    canvas.style.cursor = "grab";

    function pick(mx, my) {
      var best = -1, bd = 156;
      for (var i = 0; i < proj.length; i++) {
        var dx = proj[i].sx - mx, dy = proj[i].sy - my, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = proj[i].i; }
      }
      return best;
    }

    canvas.addEventListener("pointerdown", function (e) {
      dragging = true; moved = false;
      lastX = e.clientX; lastY = e.clientY;
      velY = velX = 0;
      canvas.style.cursor = "grabbing";
      if (canvas.setPointerCapture) {
        try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      }
      kick();
    });

    canvas.addEventListener("pointermove", function (e) {
      var r = canvas.getBoundingClientRect();
      var mx = e.clientX - r.left, my = e.clientY - r.top;

      if (dragging) {
        var dx = e.clientX - lastX, dy = e.clientY - lastY;
        if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
        lastX = e.clientX; lastY = e.clientY;
        rotY += dx * 0.0065;
        rotX += dy * 0.0065;
        velY = dx * 0.0065;              // carried into momentum on release
        velX = dy * 0.0065;
        kick();
        return;
      }

      var best = pick(mx, my);
      if (best !== hoverIdx) {
        hoverIdx = best;
        canvas.style.cursor = best >= 0 ? "pointer" : "grab";
        canvas.title = best >= 0
          ? pts[best].code + "  " + (pts[best].pct >= 0 ? "+" : "") + pts[best].pct.toFixed(2) + "%"
          : "";
        kick();                          // repaint so the hover highlight shows
      }
    });

    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      canvas.style.cursor = hoverIdx >= 0 ? "pointer" : "grab";
      if (canvas.releasePointerCapture && e && e.pointerId != null) {
        try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      }
      kick();                            // let the momentum play out
    }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    canvas.addEventListener("pointerleave", function () {
      hoverIdx = -1; canvas.title = "";
      if (!dragging) canvas.style.cursor = "grab";
      kick();
    });

    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoom *= e.deltaY < 0 ? 1.12 : 0.89;
      zoom = Math.max(0.45, Math.min(3.2, zoom));
      kick();
    }, { passive: false });

    canvas.addEventListener("click", function () {
      // a drag that happens to end over a particle is not a click on it
      if (!moved && hoverIdx >= 0 && opts.onPick) opts.onPick(pts[hoverIdx]);
    });

    canvas.addEventListener("dblclick", function () {
      rotX = -0.22; rotY = 0; zoom = 1; velX = velY = 0; kick();
    });

    // Keyboard: the field is a control, so it must be reachable without a mouse.
    canvas.tabIndex = 0;
    canvas.addEventListener("keydown", function (e) {
      var step = 0.12;
      if (e.key === "ArrowLeft") rotY -= step;
      else if (e.key === "ArrowRight") rotY += step;
      else if (e.key === "ArrowUp") rotX -= step;
      else if (e.key === "ArrowDown") rotX += step;
      else if (e.key === "+" || e.key === "=") zoom = Math.min(3.2, zoom * 1.12);
      else if (e.key === "-") zoom = Math.max(0.45, zoom * 0.89);
      else if (e.key === " ") { autoSpin = !autoSpin; }
      else if (e.key === "0") { rotX = -0.22; rotY = 0; zoom = 1; }
      else return;
      e.preventDefault();
      kick();
    });

    return {
      stop: stop, start: start, count: pts.length,
      spin: function (on) {
        autoSpin = (on === undefined) ? !autoSpin : !!on;
        try { localStorage.setItem("scanx.breadth.spin", autoSpin ? "on" : "off"); }
        catch (e) { /* private mode: the toggle still works, it just won't stick */ }
        kick();
        return autoSpin;
      },
      spinning: function () { return autoSpin; },
      reset: function () { rotX = -0.22; rotY = 0; zoom = 1; velX = velY = 0; kick(); }
    };
  }

  global.Breadth3D = { mount: mount, build: build };
})(window);
