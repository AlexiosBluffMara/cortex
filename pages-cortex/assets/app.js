(function () {
  const mode = (document.currentScript && document.currentScript.dataset.mode) || 'landing';

  function setDot(id, up) {
    const d = document.getElementById(id);
    if (!d) return;
    d.className = 'dot ' + (up ? 'up' : 'down');
  }
  function setText(id, t) {
    const e = document.getElementById(id);
    if (e) e.textContent = t;
  }

  async function pollHealth() {
    try {
      const r = await fetch('/api/healthz', { cache: 'no-store' });
      const j = await r.json();
      const up = !!(j && (j['5090'] || j.ok || j.status === 'ok'));
      setDot('dot-5090', up);
      setText('s-5090', up ? 'healthy' : 'down');
      setDot('dot-inf', up);
      setText('s-inf', up ? 'healthy' : 'down');
      setText('l-inf', JSON.stringify(j));
      setDot('dot-tunnel', up);
      setText('s-tunnel', up ? 'connected' : 'unreachable');
      setText('l-tunnel', up ? 'rtk-5090 reachable via edge' : 'no response from origin');
    } catch (err) {
      setDot('dot-5090', false);
      setText('s-5090', 'unreachable');
      setDot('dot-inf', false);
      setText('s-inf', 'unreachable');
      setDot('dot-tunnel', false);
      setText('s-tunnel', 'unreachable');
      setText('l-tunnel', String(err));
    }
  }

  async function pollModels() {
    const el = document.getElementById('l-models');
    if (!el) return;
    try {
      const r = await fetch('/api/tags', { cache: 'no-store' });
      const j = await r.json();
      const names = (j.models || []).map(m => m.name || m.model).filter(Boolean);
      el.textContent = names.length ? names.join('\n') : '(none loaded)';
    } catch (err) {
      el.textContent = 'error: ' + err;
    }
  }

  async function pollNarrations() {
    const el = document.getElementById('l-narr');
    if (!el) return;
    try {
      const r = await fetch('/api/narrations?limit=10', { cache: 'no-store' });
      if (!r.ok) throw new Error('http ' + r.status);
      const j = await r.json();
      const items = j.items || [];
      el.innerHTML = '';
      if (!items.length) {
        const li = document.createElement('li');
        li.textContent = '(no narrations yet)';
        el.appendChild(li);
        return;
      }
      for (const it of items) {
        const li = document.createElement('li');
        li.textContent = (it.ts || '') + ' - ' + (it.text || it.summary || '');
        el.appendChild(li);
      }
    } catch (err) {
      el.innerHTML = '<li>error: ' + String(err) + '</li>';
    }
  }

  function bindForm() {
    const f = document.getElementById('f');
    if (!f) return;
    const log = document.getElementById('log');
    const go = document.getElementById('go');
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const file = document.getElementById('file').files[0];
      if (!file) return;
      go.disabled = true;
      log.textContent = 'uploading ' + file.name + ' (' + file.size + ' bytes)...';
      const fd = new FormData();
      fd.append('file', file);
      fd.append('tier', '4');
      try {
        const r = await fetch('/api/scan', { method: 'POST', body: fd });
        const j = await r.json().catch(() => ({}));
        if (j.scan_id) {
          log.textContent = 'submitted: scan_id=' + j.scan_id;
        } else {
          log.textContent = JSON.stringify(j);
        }
      } catch (err) {
        log.textContent = 'error: ' + err;
      } finally {
        go.disabled = false;
      }
    });
  }

  if (mode === 'kiosk') {
    pollHealth();
    pollModels();
    pollNarrations();
    setInterval(pollHealth, 2000);
    setInterval(pollModels, 5000);
    setInterval(pollNarrations, 5000);
  } else {
    pollHealth();
    setInterval(pollHealth, 5000);
    bindForm();
  }
})();
