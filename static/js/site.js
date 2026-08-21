/* The Wisdom Crucible — vanilla JS, one IIFE per concern, wired via data-*
 * attributes so templates can add controls without touching JS. */

/* ---- Video lightbox -----------------------------------------------------
 * Any element with data-video-open="<youtubeId>" opens the global overlay.
 * The iframe is injected on open (nothing loads until the visitor chooses to
 * watch — no autoplay policy violation: playback is user-initiated) and
 * removed on close so audio always stops. */
(function () {
  var box, shell, frame, titleEl, pageLink, lastFocus = null;

  function init() {
    box = document.getElementById('video-lightbox');
    if (!box) return;
    shell = document.getElementById('video-lightbox-shell');
    frame = document.getElementById('video-lightbox-frame');
    titleEl = document.getElementById('video-lightbox-title');
    pageLink = document.getElementById('video-lightbox-page');

    document.addEventListener('click', function (e) {
      var opener = e.target.closest('[data-video-open]');
      if (opener) {
        e.preventDefault();
        open(opener.getAttribute('data-video-open'),
             opener.getAttribute('data-video-title') || '',
             opener.getAttribute('data-video-page') || '',
             opener.getAttribute('data-video-start') || 0,
             opener.getAttribute('data-video-kind') || 'teaching');
        return;
      }
      if (e.target.closest('[data-lightbox-close]')) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !box.classList.contains('hidden')) close();
    });
  }

  /* aria-modal promises AT the background is gone — make it true for the
   * keyboard too by inerting the page chrome while the dialog is open. */
  function setPageInert(on) {
    ['header', 'main', 'footer'].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el) { if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
    });
  }

  function open(videoId, title, pageUrl, start, kind) {
    lastFocus = document.activeElement;
    /* Vertical Shorts reshape the overlay to 9:16 (see .lightbox-shell-portrait). */
    if (shell) shell.classList.toggle('lightbox-shell-portrait', kind === 'short');
    titleEl.textContent = title;
    if (pageUrl) {
      pageLink.href = pageUrl;
      pageLink.hidden = false;
      /* Shorts get their own wording — 'Short' capitalised, matching YouTube. */
      pageLink.textContent = 'View full ' + (kind === 'short' ? 'Short' : 'teaching') + ' page →';
    }
    else { pageLink.hidden = true; }
    var src = 'https://www.youtube-nocookie.com/embed/' + videoId +
      '?autoplay=1&rel=0' + (start > 0 ? '&start=' + parseInt(start, 10) : '');
    frame.innerHTML = '<iframe width="100%" height="100%" src="' + src +
      '" title="' + title.replace(/"/g, '&quot;') +
      '" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>';
    box.classList.remove('hidden');
    box.classList.add('flex');
    document.body.style.overflow = 'hidden';
    setPageInert(true);
    var closeBtn = box.querySelector('button[data-lightbox-close]');
    if (closeBtn) closeBtn.focus();
  }

  function close() {
    frame.innerHTML = '';
    box.classList.add('hidden');
    box.classList.remove('flex');
    document.body.style.overflow = '';
    setPageInert(false);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

/* ---- Home atmosphere: rising blue-white sparks --------------------------
 * Ported from Vault-of-Ash site.js (the owner's reference project), physics
 * intact, recolored from ember orange to the crucible's blue-white flame.
 * Runs only where the #crucible-embers canvas exists (the home page), sits
 * behind all content, pauses with the tab, and stays OFF entirely under
 * prefers-reduced-motion (the static grain + vignette still paint). */
(function () {
  var canvas = document.getElementById('crucible-embers');
  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!canvas || reducedMotion) return;

  var ctx = canvas.getContext('2d');
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var particles = [];
  var running = true;
  var W = 0, H = 0;

  function resize() {
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var target = Math.round(Math.min(90, Math.max(34, (W * H) / 26000)));
    while (particles.length < target) particles.push(spawn(true));
    particles.length = target;
  }

  function spawn(anywhere) {
    var bright = Math.random() < 0.16;
    return {
      x: Math.random() * W,
      y: anywhere ? Math.random() * H : H + 8,
      r: bright ? 1.6 + Math.random() * 1.3 : 0.6 + Math.random() * 1.1,
      vy: 0.18 + Math.random() * 0.55,
      drift: (Math.random() - 0.5) * 0.25,
      phase: Math.random() * Math.PI * 2,
      wobble: 0.4 + Math.random() * 0.9,
      alpha: 0.25 + Math.random() * 0.55,
      bright: bright,
      life: 0
    };
  }

  var frame = 0;

  function tick() {
    if (!running) return;
    frame++;
    if (frame % 90 === 0 && (window.innerWidth !== W || window.innerHeight !== H)) {
      resize();
    }
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      p.life += 0.016;
      p.y -= p.vy;
      p.x += p.drift + Math.sin(p.life * p.wobble + p.phase) * 0.22;
      var fade = Math.min(1, (H - p.y) / (H * 0.12) + 0.15);
      var heightFade = Math.max(0, Math.min(1, p.y / (H * 0.55)));
      var a = p.alpha * fade * (0.25 + heightFade * 0.75);
      var flicker = 0.75 + 0.25 * Math.sin(p.life * 6 + p.phase);
      if (p.y < -10 || p.x < -20 || p.x > W + 20) {
        particles[i] = spawn(false);
        continue;
      }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      if (p.bright) {
        ctx.fillStyle = 'rgba(191, 219, 254, ' + (a * flicker) + ')';
        ctx.shadowColor = 'rgba(74, 181, 246, 0.9)';
        ctx.shadowBlur = 9;
      } else {
        ctx.fillStyle = 'rgba(122, 176, 230, ' + (a * flicker * 0.8) + ')';
        ctx.shadowBlur = 0;
      }
      ctx.fill();
      ctx.shadowBlur = 0;
    }
    requestAnimationFrame(tick);
  }

  document.addEventListener('visibilitychange', function () {
    var wasRunning = running;
    running = !document.hidden;
    if (running && !wasRunning) requestAnimationFrame(tick);
  });

  window.addEventListener('resize', resize);
  resize();
  requestAnimationFrame(tick);
})();

/* ---- Inline episode player ----------------------------------------------
 * The episode page renders a thumbnail facade ([data-inline-player]) that
 * swaps to the iframe on click. Chapter / transcript rows with
 * [data-seek="<seconds>"] load (or reload) the inline player at that
 * timestamp — a timestamped transcript you can click is the point. */
(function () {
  var host = null;

  function init() {
    host = document.querySelector('[data-inline-player]');
    if (!host) return;
    host.addEventListener('click', function (e) {
      var facade = e.target.closest('[data-player-facade]');
      if (facade) play(parseInt(host.getAttribute('data-start') || '0', 10));
    });
    document.addEventListener('click', function (e) {
      var seeker = e.target.closest('[data-seek]');
      if (!seeker) return;
      e.preventDefault();
      play(parseInt(seeker.getAttribute('data-seek'), 10));
      host.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }

  function play(start) {
    var id = host.getAttribute('data-video-id');
    var title = host.getAttribute('data-video-title') || '';
    host.innerHTML = '<iframe class="w-full h-full" src="https://www.youtube-nocookie.com/embed/' + id +
      '?autoplay=1&rel=0' + (start > 0 ? '&start=' + start : '') +
      '" title="' + title.replace(/"/g, '&quot;') +
      '" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>';
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
