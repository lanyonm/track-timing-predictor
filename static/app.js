// Page behaviour for every template. Loaded from <head> without defer so the theme is set
// before first paint; everything else waits for DOMContentLoaded. Inline scripts and
// on* attributes are blocked by the Content-Security-Policy (app.main.security_headers).
// The theme follows the system preference unless the theme cookie overrides it.
var systemDark = window.matchMedia('(prefers-color-scheme: dark)');

function systemTheme() {
  return systemDark.matches ? 'dark' : 'light';
}

function savedTheme() {
  var match = document.cookie.match(/(?:^|;\s*)theme=(light|dark)/);
  return match ? match[1] : null;
}

document.documentElement.setAttribute('data-theme', savedTheme() || systemTheme());

document.addEventListener('DOMContentLoaded', function () {
  initThemeToggle();
  initAutoSubmit();
  initLoadingForm();
  initScheduleRefresh();
  initPalmaresEdit();
  initCopyLink();
  initCsvExport();
});

function initThemeToggle() {
  var toggle = document.getElementById('theme-toggle');
  if (!toggle) return;
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    toggle.checked = theme === 'dark';
  }
  toggle.checked = document.documentElement.getAttribute('data-theme') === 'dark';
  toggle.addEventListener('change', function () {
    var theme = toggle.checked ? 'dark' : 'light';
    apply(theme);
    // Choosing the system's theme clears the override, so the page follows the system again.
    var maxAge = theme === systemTheme() ? 0 : 31536000;
    document.cookie = 'theme=' + theme + ';path=/;max-age=' + maxAge + ';SameSite=Lax';
  });
  systemDark.addEventListener('change', function () {
    if (!savedTheme()) apply(systemTheme());
  });
}

// Checkboxes that submit their form when toggled (the form has a <noscript> submit button).
function initAutoSubmit() {
  document.querySelectorAll('[data-autosubmit]').forEach(function (el) {
    el.addEventListener('change', function () { el.form.submit(); });
  });
}

// The index form shows a spinner while the schedule loads.
function initLoadingForm() {
  var form = document.getElementById('load-form');
  if (!form) return;
  form.addEventListener('submit', function () {
    document.getElementById('load-btn').disabled = true;
    document.getElementById('load-spinner').classList.remove('hidden');
    document.getElementById('load-text').textContent = 'Loading…';
  });
}

// Keep each session's open/closed state across HTMX refreshes of the schedule.
function initScheduleRefresh() {
  var container = document.getElementById('schedule-container');
  if (!container) return;
  var openSessions = new Set();
  var closedByUser = new Set();

  function label(el) {
    return el.querySelector('summary .session-label').textContent.trim();
  }

  container.addEventListener('htmx:beforeSwap', function () {
    openSessions.clear();
    container.querySelectorAll('details').forEach(function (el) {
      if (el.hasAttribute('open')) {
        openSessions.add(label(el));
      } else if (el.hasAttribute('data-pending-racer')) {
        closedByUser.add(label(el));
      }
    });
  });

  container.addEventListener('htmx:afterSwap', function () {
    container.querySelectorAll('details').forEach(function (el) {
      if (openSessions.has(label(el))) {
        el.setAttribute('open', '');
      } else {
        el.removeAttribute('open');
      }
    });

    container.querySelectorAll('details').forEach(function (el) {
      if (el.hasAttribute('data-pending-racer') && !closedByUser.has(label(el))) {
        el.setAttribute('open', '');
      }
    });

    var generatedAt = document.getElementById('schedule-generated-at');
    if (generatedAt) {
      document.getElementById('last-updated').textContent = generatedAt.dataset.generatedAt;
    }
  });
}

// Palmares: Edit/Cancel toggle the rename form; Remove opens its confirmation modal.
function initPalmaresEdit() {
  document.querySelectorAll('[data-edit-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var compId = btn.dataset.editToggle;
      var title = document.getElementById('title-' + compId);
      var form = document.getElementById('edit-' + compId);
      if (form.classList.contains('hidden')) {
        title.classList.add('hidden');
        form.classList.remove('hidden');
        form.querySelector('input[name="name"]').focus();
      } else {
        form.classList.add('hidden');
        title.classList.remove('hidden');
      }
    });
  });

  document.querySelectorAll('[data-remove-modal]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      e.preventDefault();
      document.getElementById(link.dataset.removeModal).showModal();
    });
  });
}

function initCopyLink() {
  var btn = document.getElementById('copy-link-btn');
  if (!btn) return;
  btn.addEventListener('click', function () {
    var url = btn.dataset.url;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () {
        showFeedback();
      });
    } else {
      var input = document.createElement('input');
      input.value = url;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
      showFeedback();
    }
  });
  function showFeedback() {
    var toast = document.getElementById('toast-container');
    toast.classList.remove('hidden');
    setTimeout(function () { toast.classList.add('hidden'); }, 2500);
  }
}

function initCsvExport() {
  document.querySelectorAll('.export-btn').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var auditUrl = btn.dataset.auditUrl;
      var teamName = btn.dataset.teamName;
      var racerParam = btn.dataset.racer;
      if (!racerParam) return;

      btn.classList.add('is-loading');

      var exportUrl = '/palmares/export?audit_url=' + encodeURIComponent(auditUrl) + '&r=' + racerParam;
      if (teamName) exportUrl += '&team_name=' + encodeURIComponent(teamName);
      fetch(exportUrl)
        .then(function (resp) {
          if (!resp.ok) throw new Error('Export failed');
          return resp.blob();
        })
        .then(function (blob) {
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = auditUrl.split('/').pop().replace('-AUDIT-R.htm', '') + '.csv';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(a.href);
          btn.classList.remove('is-loading');
          btn.classList.add('is-done');
          setTimeout(function () { btn.classList.remove('is-done'); }, 2000);
        })
        .catch(function () {
          btn.classList.remove('is-loading');
          btn.classList.add('is-error');
          btn.title = 'Could not load audit data';
          setTimeout(function () {
            btn.classList.remove('is-error');
            btn.title = 'Export CSV';
          }, 10000);
        });
    });
  });
}
