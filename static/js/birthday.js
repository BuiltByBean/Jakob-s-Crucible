/* The Wisdom Crucible — birthday greeting.
 *
 * The server has already decided this is the day (services/birthday.py); this
 * file only opens the overlay, runs the fireworks, and remembers the dismissal
 * so the greeting shows ONCE rather than on every navigation. It is loaded
 * only on the day itself, so there is no other date to guard against.
 *
 * No inline script anywhere: the CSP carries no 'unsafe-inline', so a <script>
 * block in the template would be silently dropped. Server values arrive on
 * data-* attributes. */
(function () {
  var box, canvas, stamp = '';

  /* Private-mode Safari throws on localStorage rather than returning null, and
   * a birthday greeting must never be the thing that breaks a page. */
  function remembered(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function remember(key) {
    try { window.localStorage.setItem(key, '1'); } catch (e) { /* nothing to do */ }
  }

  function reducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function wantsFireworks() {
    return box.getAttribute('data-birthday-fireworks') !== 'off' && !reducedMotion();
  }

  /* aria-modal promises the rest of the page is gone — make that true for the
   * keyboard too, exactly as the video lightbox does. */
  function setPageInert(on) {
    ['header', 'main', 'footer'].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el) { if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
    });
  }

  function open() {
    box.classList.remove('hidden');
    box.classList.add('flex');
    setPageInert(true);
    document.body.style.overflow = 'hidden';
    var btn = box.querySelector('.btn-primary');
    if (btn) btn.focus();
    if (wantsFireworks()) start();
  }

  function close() {
    stop();
    box.classList.add('hidden');
    box.classList.remove('flex');
    setPageInert(false);
    document.body.style.overflow = '';
    /* Written on dismissal, not on open: closing the tab before reading it
     * should not cost Jakob the greeting. */
    if (stamp) remember('twc-birthday-' + stamp);
  }

  /* ---- fireworks -------------------------------------------------------
   * Artwork, so this palette is exempt from the blue/gray/white chrome rule —
   * the blue-white flame first, with the seal's gold behind it. */
  var COLORS = ['#e0f2fe', '#bfdbfe', '#7dd3fc', '#38bdf8', '#a5b4fc', '#d4af37', '#fbbf24'];
  var ctx, ro = null, raf = 0, rockets = [], sparks = [], nextLaunch = 0;
  var elapsed = 0, last = 0, w = 0, h = 0;
  var GRAVITY = 0.045, DRAG = 0.985, RUN_MS = 40000;

  /* Re-measured through a ResizeObserver, NOT just window.resize. The overlay
   * is un-hidden in the same tick that starts the animation, so the first
   * measurement can catch the canvas mid-layout — and a box can change size
   * with no window resize at all (a container reflow, a late web font, a
   * scrollbar appearing, a phone's URL bar sliding away). Measured once and
   * left alone, a 32px first reading aimed every rocket into the top-left
   * corner and nothing ever corrected it. */
  function size() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var nw = canvas.clientWidth || window.innerWidth || 0;
    var nh = canvas.clientHeight || window.innerHeight || 0;
    if (nw === w && nh === h) return;
    w = nw; h = nh;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    /* Whatever is in flight was aimed at the old box; setting canvas.width has
     * already wiped it off the canvas anyway. */
    rockets = []; sparks = [];
  }

  function pick() { return COLORS[(Math.random() * COLORS.length) | 0]; }

  function launch() {
    if (w < 2 || h < 2) return;  /* nothing sensible to aim at yet */
    rockets.push({
      x: w * (0.08 + Math.random() * 0.84),
      y: h + 10,
      vx: (Math.random() - 0.5) * 0.6,
      vy: -(h * 0.011 + Math.random() * h * 0.004),
      peak: h * (0.14 + Math.random() * 0.32),
      color: pick()
    });
  }

  function burst(x, y, color) {
    var n = 46 + ((Math.random() * 30) | 0);
    /* Even spokes plus a little jitter read as a shell; purely random angles
     * read as a smudge. */
    for (var i = 0; i < n; i++) {
      var a = (i / n) * Math.PI * 2 + Math.random() * 0.12;
      var speed = (1.4 + Math.random() * 2.9) * (h / 720);
      sparks.push({
        x: x, y: y,
        vx: Math.cos(a) * speed, vy: Math.sin(a) * speed,
        life: 1, decay: 0.008 + Math.random() * 0.012,
        color: Math.random() < 0.78 ? color : pick(),
        size: 1.1 + Math.random() * 1.7
      });
    }
    /* A brief white flash at the detonation point. */
    sparks.push({ x: x, y: y, vx: 0, vy: 0, life: 1, decay: 0.09, color: '#ffffff', size: 9 });
  }

  function frame(now) {
    raf = window.requestAnimationFrame(frame);

    /* The canvas is measured on the first real frame too: by then layout has
     * definitely run, even if it had not when start() was called. */
    if (w < 2 || h < 2) size();

    /* Fade the previous frame rather than clearing it: destination-out keeps
     * the canvas transparent (the scrim shows through) while leaving trails. */
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = 'rgba(0,0,0,0.20)';
    ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';

    /* Accumulated, not measured from a start instant: pausing in a hidden tab
     * must not hand the display another full run when it comes back. */
    elapsed += last ? Math.min(now - last, 100) : 0;
    last = now;

    /* Stop launching after a while — a greeting left open on a phone should
     * not keep the GPU busy all afternoon. Sparks in flight still finish. */
    if (elapsed < RUN_MS && now > nextLaunch) {
      launch();
      if (Math.random() < 0.45) launch();
      nextLaunch = now + 300 + Math.random() * 480;
    }

    var i, p;
    for (i = rockets.length - 1; i >= 0; i--) {
      p = rockets[i];
      p.x += p.vx; p.y += p.vy; p.vy += GRAVITY * 1.6;
      ctx.globalAlpha = 1;
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 1.9, 0, Math.PI * 2);
      ctx.fill();
      if (p.vy >= 0 || p.y <= p.peak) { burst(p.x, p.y, p.color); rockets.splice(i, 1); }
    }

    for (i = sparks.length - 1; i >= 0; i--) {
      p = sparks[i];
      p.x += p.vx; p.y += p.vy;
      p.vx *= DRAG; p.vy = p.vy * DRAG + GRAVITY;
      p.life -= p.decay;
      if (p.life <= 0) { sparks.splice(i, 1); continue; }
      ctx.globalAlpha = Math.max(0, p.life);
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function start() {
    if (!canvas || raf) return;
    ctx = canvas.getContext('2d');
    if (!ctx) return;
    size();
    window.addEventListener('resize', size);
    if (window.ResizeObserver) {
      ro = new window.ResizeObserver(size);
      ro.observe(canvas);
    }
    /* An opening volley, so the first thing on screen is already a firework. */
    launch(); launch(); launch();
    raf = window.requestAnimationFrame(frame);
  }

  function stop() {
    if (raf) { window.cancelAnimationFrame(raf); raf = 0; }
    window.removeEventListener('resize', size);
    if (ro) { ro.disconnect(); ro = null; }
    rockets = []; sparks = []; elapsed = 0; last = 0; nextLaunch = 0;
    if (ctx) ctx.clearRect(0, 0, w, h);
  }

  function init() {
    box = document.getElementById('birthday-greeting');
    if (!box) return;
    canvas = document.getElementById('birthday-fireworks');
    stamp = box.getAttribute('data-birthday-stamp') || '';

    box.addEventListener('click', function (e) {
      if (e.target.closest('[data-birthday-close]')) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !box.classList.contains('hidden')) close();
    });
    /* Don't animate into a tab nobody is looking at. */
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        if (raf) { window.cancelAnimationFrame(raf); raf = 0; }
      } else if (!raf && !box.classList.contains('hidden') && wantsFireworks()) {
        last = 0;  /* drop the gap spent hidden, keep the elapsed run */
        raf = window.requestAnimationFrame(frame);
      }
    });

    if (stamp && remembered('twc-birthday-' + stamp)) return;  /* already seen today */
    open();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
