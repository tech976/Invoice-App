/* Shared helpers. Amounts use Indian digit grouping throughout, because that
   is how every figure on the source bills is printed. */

const fmtINR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtQty = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 3 });

function money(v) { return (v === null || v === undefined || v === '') ? '—' : fmtINR.format(v); }
function qty(v)   { return (v === null || v === undefined || v === '') ? '—' : fmtQty.format(v); }
function num(v)   { return (v === null || v === undefined || v === '') ? '—' : v; }

function dmy(iso) {
  if (!iso) return '—';
  const d = new Date(iso + (iso.length === 10 ? 'T00:00:00' : ''));
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(message, kind = '') {
  const host = document.getElementById('toast');
  const el = document.createElement('div');
  el.className = kind;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 3500);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  });
  if (!res.ok) {
    let detail;
    try {
      const body = await res.json();
      detail = body.detail;
      if (detail && typeof detail === 'object') detail = detail.message || JSON.stringify(detail);
    } catch { detail = res.statusText; }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

const STATUS_TONE = {
  confirmed: 'ok', extracted: 'mute', needs_review: 'warn',
  failed: 'err', processing: 'info', queued: 'mute',
  uploaded: 'mute', duplicate: 'warn', rejected: 'err',
  unpaid: 'mute', partial: 'warn', paid: 'ok',
};

const statusBadge = (s) =>
  `<span class="badge ${STATUS_TONE[s] || 'mute'}">${esc(String(s || '').replace(/_/g, ' '))}</span>`;

/* A summary rail row: label on the left, figure on the right. */
const summaryRow = (label, value, cls = '') =>
  `<div class="summary-row ${cls}"><span class="k">${esc(label)}</span><span class="v">${value}</span></div>`;

/* Rupee figure for display. Amounts on these bills are always INR. */
const rupees = (v) => (v === null || v === undefined || v === '') ? '—' : '₹' + fmtINR.format(v);

/* Values shown inside editable amount boxes.
   The server's parse_amount() strips grouping on the way back in, so a
   formatted value round-trips safely — and an unformatted 5846893 is
   genuinely hard to read against a bill printed as 58,46,893.00. */
function fmtField(v, kind) {
  if (v === null || v === undefined || v === '') return '';
  if (kind === 'money') return fmtINR.format(v);
  if (kind === 'qty') return fmtQty.format(v);
  return v;
}

/* First letter(s) of a name, for the placeholder square where there is no image. */
function initials(name) {
  if (!name) return '?';
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
}

function partyLink(p) {
  if (!p) return '<span class="muted">—</span>';
  if (!p.id) return esc(p.name || '—');
  return `<a href="/parties/${p.id}">${esc(p.name)}</a>`;
}

function qs(params) {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v !== null && v !== undefined) out.set(k, v);
  }
  return out.toString();
}

function debounce(fn, ms = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* --------------------------------------------------------------------------
   Confirmation before anything irreversible.

   A browser confirm() cannot say which bill is about to go, nor what will
   remain afterwards, and those are exactly the things somebody needs to read
   before deleting a document their ledger was built from.

   Resolves to the chosen option's value, or null if the person backed out.
   Escape and a click on the backdrop both count as backing out.
   -------------------------------------------------------------------------- */
function confirmDialog({ title, message, detail = '', warning = '',
                         choices = [], confirmLabel = 'Confirm',
                         danger = true } = {}) {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';

    const options = choices.map((c, i) => `
      <label>
        <input type="radio" name="modal-choice" value="${esc(c.value)}"
               ${i === 0 ? 'checked' : ''}>
        <span><b>${esc(c.label)}</b>${c.hint ? `<span class="hint">${esc(c.hint)}</span>` : ''}</span>
      </label>`).join('');

    backdrop.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <h3>${esc(title)}</h3>
        ${message ? `<p>${esc(message)}</p>` : ''}
        ${detail ? `<div class="detail">${detail}</div>` : ''}
        ${choices.length ? `<div class="choices">${options}</div>` : ''}
        ${warning ? `<span class="warn-note">${esc(warning)}</span>` : ''}
        <div class="actions">
          <button type="button" data-act="cancel">Cancel</button>
          <button type="button" class="${danger ? 'danger' : 'primary'}"
                  data-act="ok">${esc(confirmLabel)}</button>
        </div>
      </div>`;

    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      backdrop.remove();
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(null);
      if (e.key === 'Enter') accept();
    };
    const accept = () => {
      const picked = backdrop.querySelector('input[name="modal-choice"]:checked');
      close(choices.length ? (picked ? picked.value : null) : true);
    };

    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) close(null);
      if (e.target.dataset.act === 'cancel') close(null);
      if (e.target.dataset.act === 'ok') accept();
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(backdrop);
    // Focus the way out, not the destructive button: nobody should delete a
    // bill by hitting the key they were already pressing.
    backdrop.querySelector('[data-act="cancel"]').focus();
  });
}
