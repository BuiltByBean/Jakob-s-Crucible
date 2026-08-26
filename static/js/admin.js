/* Admin-only Alpine components.
 *
 * These live in a file rather than in a <script> block because the site's CSP
 * allows 'unsafe-eval' (Alpine needs it to evaluate x-* expressions) but not
 * 'unsafe-inline' — an inline <script> in a template is silently blocked.
 * Loaded with defer BEFORE alpine.min.js, so the globals exist by the time
 * Alpine walks the DOM.
 */
(function () {
  'use strict';

  /* Progress for "Re-check all videos".
   *
   * The sweep runs on a background thread inside whichever gunicorn worker
   * handled the POST, and writes its progress to the database — so this poll
   * gets a straight answer whichever worker happens to serve it.
   */
  window.recheckPanel = function () {
    return {
      running: false,
      state: {},
      init: function () {
        var url = this.$el.dataset.statusUrl;
        if (!url) return;
        this.statusUrl = url;
        this.load(false);
      },
      load: function (reloadWhenDone) {
        var self = this;
        fetch(this.statusUrl, { credentials: 'same-origin' })
          .then(function (r) { return r.json(); })
          .then(function (s) {
            var wasRunning = self.running;
            self.state = s || {};
            self.running = !!(s && s.running);
            if (self.running) {
              setTimeout(function () { self.load(true); }, 1500);
            } else if (wasRunning && reloadWhenDone) {
              // Titles and thumbnails in the list below may have just changed.
              window.location.reload();
            }
          })
          .catch(function () { self.running = false; });
      },
      percent: function () {
        var s = this.state;
        if (!s || !s.total) return 0;
        return Math.round((s.done || 0) / s.total * 100);
      },
      summary: function () {
        var s = this.state || {}, bits = [];
        if (s.updated) bits.push(s.updated + ' updated');
        if (s.unchanged) bits.push(s.unchanged + ' unchanged');
        if ((s.missing || []).length) bits.push(s.missing.length + ' missing');
        if ((s.failed || []).length) bits.push(s.failed.length + ' failed');
        return bits.join(' · ');
      },
      headline: function () {
        var s = this.state || {};
        if (this.running) return 'Checking ' + (s.done || 0) + ' of ' + s.total + '…';
        if (!s.total) return '';
        return 'Last check: ' + (s.done || 0) + ' of ' + s.total + ' videos';
      },
    };
  };
})();
