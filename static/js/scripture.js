/* ---- ESV passage overlay (Statement of Faith) ---------------------------
 * Every reference chip carries data-ref="<passage key>"; the text for it is
 * already in window.TWC_SCRIPTURES (scriptures.js, loaded just before this),
 * so opening a passage costs no request and works offline.
 *
 * Same overlay mechanics as the video lightbox in site.js — backdrop / button /
 * Escape all close, the page chrome is inerted while it is open so the keyboard
 * cannot wander behind it, and focus returns to the chip that opened it. */
(function () {
  var box, titleEl, bodyEl, bookLink, bookLabel, lastFocus = null;

  function init() {
    box = document.getElementById('scripture-popup');
    if (!box || !document.querySelector('.ref-chip')) return;
    titleEl = document.getElementById('scripture-popup-title');
    bodyEl = document.getElementById('scripture-popup-body');
    bookLink = document.getElementById('scripture-popup-book');
    bookLabel = document.getElementById('scripture-popup-book-label');

    document.addEventListener('click', function (e) {
      var chip = e.target.closest('.ref-chip');
      if (chip) { open(chip); return; }
      if (e.target.closest('[data-scripture-close]')) close();
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

  function open(chip) {
    var data = window.TWC_SCRIPTURES || {};
    var passage = data[chip.getAttribute('data-ref')];
    /* The server only makes a chip out of a reference the bundle can render, so
     * this is belt-and-braces: a stale cached scriptures.js must not produce an
     * empty overlay. Leave the chip inert instead. */
    if (!passage) return;
    lastFocus = chip;

    /* Chapter labels only where the passage actually crosses a chapter
     * (Titus 1:5-2:1, Revelation 20:10-21:27) — repeating "Romans 5" above a
     * two-verse quotation is noise. */
    var verses = passage.v;
    var crossesChapter = verses.length > 1 && verses[0][0] !== verses[verses.length - 1][0];
    var frag = document.createDocumentFragment();
    var lastChapter = null;

    verses.forEach(function (verse) {
      if (crossesChapter && verse[0] !== lastChapter) {
        lastChapter = verse[0];
        /* Plain h3/p/sup, no classes: .scripture-passage styles them by element
         * because Tailwind purges any class that appears only in a JS file. */
        var head = document.createElement('h3');
        head.textContent = passage.b + ' ' + verse[0];
        frag.appendChild(head);
      }
      var p = document.createElement('p');
      var num = document.createElement('sup');
      num.textContent = verse[1];
      p.appendChild(num);
      /* textContent, never innerHTML: the verse text carries curly quotes and
       * em-dashes, and building it as a node means no escaping to get wrong. */
      p.appendChild(document.createTextNode(' ' + verse[2]));
      frag.appendChild(p);
    });

    titleEl.textContent = chip.getAttribute('data-ref').replace(/(\d)-(\d)/g, '$1–$2') + ' (ESV)';
    bodyEl.textContent = '';
    bodyEl.appendChild(frag);
    bodyEl.scrollTop = 0;

    /* The link through to Explore Scripture is on the chip only when that book
     * actually has teachings behind it (decided server-side). */
    var bookUrl = chip.getAttribute('data-book-url');
    if (bookUrl) {
      bookLink.href = bookUrl;
      bookLabel.textContent = 'Teachings in ' + chip.getAttribute('data-book-name');
      bookLink.hidden = false;
    } else {
      bookLink.hidden = true;
    }

    box.classList.remove('hidden');
    box.classList.add('flex');
    document.body.style.overflow = 'hidden';
    setPageInert(true);
    var closeBtn = box.querySelector('[data-scripture-close][aria-label]');
    if (closeBtn) closeBtn.focus();
  }

  function close() {
    box.classList.add('hidden');
    box.classList.remove('flex');
    document.body.style.overflow = '';
    setPageInert(false);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
