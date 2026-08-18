/* The Wisdom Crucible — vanilla JS, one IIFE per concern, wired via data-*
 * attributes so templates can add controls without touching JS. */

/* ---- Video lightbox -----------------------------------------------------
 * Any element with data-video-open="<youtubeId>" opens the global overlay.
 * The iframe is injected on open (nothing loads until the visitor chooses to
 * watch — no autoplay policy violation: playback is user-initiated) and
 * removed on close so audio always stops. */
(function () {
  var box, frame, titleEl, pageLink, lastFocus = null;

  function init() {
    box = document.getElementById('video-lightbox');
    if (!box) return;
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
             opener.getAttribute('data-video-start') || 0);
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

  function open(videoId, title, pageUrl, start) {
    lastFocus = document.activeElement;
    titleEl.textContent = title;
    if (pageUrl) { pageLink.href = pageUrl; pageLink.hidden = false; }
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
