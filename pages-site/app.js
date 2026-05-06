/* Red Team Kitchen — Stripe Checkout glue (~50 LOC core).
   Talks to the cortex-payments Worker which is mounted at /api/* on redteamkitchen.com. */
(function (global) {
  const API = '/api';
  // Replace with real Turnstile site key after creating it in Cloudflare dashboard.
  const TURNSTILE_SITEKEY = '0x4AAAAAAAREPLACE_ME';

  async function postJSON(path, body) {
    const r = await fetch(API + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  function renderTurnstile(containerId) {
    return new Promise((resolve) => {
      const tryRender = () => {
        if (!global.turnstile) { setTimeout(tryRender, 150); return; }
        global.turnstile.render('#' + containerId, {
          sitekey: TURNSTILE_SITEKEY,
          callback: (token) => resolve(token),
        });
      };
      tryRender();
    });
  }

  function initDonate({ presetsId, amountId, payBtnId, errId, turnstileId }) {
    const amt = document.getElementById(amountId);
    const err = document.getElementById(errId);
    document.getElementById(presetsId).addEventListener('click', (e) => {
      const b = e.target.closest('button[data-amt]');
      if (!b) return;
      [...e.currentTarget.querySelectorAll('button')].forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      amt.value = b.dataset.amt;
    });
    let tsToken = null;
    renderTurnstile(turnstileId).then(t => { tsToken = t; });
    document.getElementById(payBtnId).addEventListener('click', async (e) => {
      err.textContent = '';
      const v = parseInt(amt.value, 10);
      if (!v || v < 5 || v > 1000) { err.textContent = 'Amount must be $5–1000.'; return; }
      if (!tsToken) { err.textContent = 'Anti-bot check still loading. Try again in a sec.'; return; }
      e.target.disabled = true;
      try {
        const { url } = await postJSON('/checkout/donate', { amount: v, turnstile: tsToken });
        global.location.assign(url);
      } catch (ex) {
        err.textContent = ex.message;
        e.target.disabled = false;
      }
    });
  }

  function initAccess({ pickSelector, errId, turnstileId }) {
    const err = document.getElementById(errId);
    let tsToken = null;
    renderTurnstile(turnstileId).then(t => { tsToken = t; });
    document.querySelectorAll(pickSelector).forEach(btn => {
      btn.addEventListener('click', async () => {
        err.textContent = '';
        const tier = btn.dataset.tier;
        if (!tsToken) { err.textContent = 'Anti-bot check still loading. Try again.'; return; }
        btn.disabled = true;
        try {
          const { url } = await postJSON('/checkout/subscribe', { tier, turnstile: tsToken });
          global.location.assign(url);
        } catch (ex) {
          err.textContent = ex.message;
          btn.disabled = false;
        }
      });
    });
  }

  function initAccount() {
    const $ = (id) => document.getElementById(id);
    const stored = localStorage.getItem('rtk_token');
    const show = (token) => fetch(API + '/me', { headers: { authorization: 'Bearer ' + token } })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('Invalid token')))
      .then(d => {
        $('signin').hidden = true; $('account').hidden = false;
        $('email').textContent = d.email || '—';
        $('plan').textContent = d.plan || 'free';
        $('status').textContent = d.status || '—';
        const used = d.calls_used || 0, max = d.calls_max || 0;
        $('usage').textContent = max ? `${used} / ${max}` : `${used} (unlimited)`;
        $('reset').textContent = d.calls_reset_at ? new Date(d.calls_reset_at).toLocaleString() : '—';
        $('bar').style.width = max ? Math.min(100, (used / max) * 100) + '%' : '5%';
        $('portal').href = d.portal_url || '#';
      })
      .catch(e => { $('err').textContent = e.message; localStorage.removeItem('rtk_token'); });
    if (stored) show(stored);
    $('load').addEventListener('click', () => {
      const t = $('token').value.trim();
      if (!t) return;
      localStorage.setItem('rtk_token', t);
      show(t);
    });
    $('signout') && $('signout').addEventListener('click', () => {
      localStorage.removeItem('rtk_token'); location.reload();
    });
  }

  global.RTKPay = { initDonate, initAccess, initAccount };
})(window);
