"""Browser-side widgets for the commissioner's live draft and FA auction.

Both run as Streamlit v2 components: searching, tapping and all bid math
happen in the browser with no rerun, so they stay instant while owners are
calling out picks and bids. Python is only woken for the write itself,
through a trigger value (``draft`` or ``award``), and checks it again before
touching the database.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

_SHARED_CSS = """
:host{--bg:#061311;--card:#0C1917;--card2:#0f201b;--line:rgba(200,155,74,.28);--line2:rgba(245,235,215,.09);
--gold:#E2BC5B;--gold2:#C89B4A;--text:#F5EBD7;--muted:#9DA89C;--red:#e0716b;--redbg:#2a1414;--green:#5fd39c;
font-family:Poppins,'Source Sans Pro',sans-serif;color:var(--text);display:block}
*{box-sizing:border-box}
button{font:inherit;cursor:pointer}
.card{background:var(--card);border:1px solid var(--line2);border-radius:18px;padding:16px}
.lbl{font-weight:600;font-size:11px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase;margin:16px 0 8px}
.pos{font-weight:700;font-size:10.5px;border:1px solid var(--gold2);color:var(--gold);border-radius:999px;padding:3px 8px;min-width:38px;text-align:center;flex:none}
.search{width:100%;background:#112B20;border:1px solid var(--line);border-radius:14px;padding:13px 14px 13px 40px;font-size:16px;color:var(--text);outline:none;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' fill='none' stroke='%239DA89C' stroke-width='2' viewBox='0 0 24 24'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.5-3.5'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:13px center}
.search:focus{border-color:var(--gold)}
.search::-webkit-search-cancel-button{-webkit-appearance:none}
.search::placeholder{color:var(--muted)}
.res{display:flex;flex-direction:column;gap:6px;margin-top:8px}
.r{display:flex;align-items:center;gap:10px;background:var(--card2);border:1px solid var(--line2);border-radius:12px;padding:9px 10px 9px 12px;cursor:pointer}
.r.sel{border-color:var(--gold)}
.nm{flex:1;min-width:0;font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nm small{display:block;color:var(--muted);font-weight:400;font-size:11.5px}
.gbtn{background:var(--gold);color:#1a1405;font-weight:700;font-size:12.5px;border:0;border-radius:999px;padding:8px 14px;flex:none}
.gbtn.confirm{background:var(--green);color:#06210f}
.big{width:100%;background:var(--gold);color:#1a1405;font-weight:700;border:0;border-radius:14px;padding:14px;font-size:15px;margin-top:14px}
.big.confirm{background:var(--green);color:#06210f}
.big:disabled{background:#2b332f;color:#6c776f;cursor:not-allowed}
.flash{border-radius:12px;padding:10px 12px;font-size:13px;margin-bottom:10px}
.flash.ok{background:rgba(19,148,94,.15);border:1px solid rgba(95,211,156,.45);color:#c9f1db}
.flash.err{background:var(--redbg);border:1px solid var(--red);color:#f6c9c6}
.empty{color:var(--muted);font-size:13px;padding:6px 2px}
.tag{font-size:10.5px;font-weight:600;color:var(--gold);background:rgba(226,188,91,.13);border-radius:999px;padding:2px 8px;margin-left:6px;white-space:nowrap}
"""

_SHARED_JS = r"""
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (v) => '$' + (Math.round(Number(v) * 100) / 100).toString();
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]/g, '');
function rank(players, q, skip, limit) {
  const n = norm(q).trim();
  if (!n) return [];
  const out = [];
  for (const p of players) {
    if (skip && skip.has(p.id)) continue;
    const name = p._n || (p._n = norm(p.name));
    const i = name.indexOf(n);
    if (i < 0) continue;
    // Word-start matches first ("jer" finds Jeremiah before Roger).
    const score = (i === 0 ? 0 : name[i - 1] === ' ' ? 1 : 2) * 1000 + (p.order ?? 999);
    out.push([score, p]);
  }
  out.sort((a, b) => a[0] - b[0]);
  return out.slice(0, limit).map(x => x[1]);
}
function root(parentElement, cls) {
  let el = parentElement.querySelector('.' + cls);
  if (!el) { el = document.createElement('div'); el.className = cls; parentElement.appendChild(el); }
  return el;
}
"""

# ---------------------------------------------------------------- draft room

_DRAFT_CSS = _SHARED_CSS + """
.wrap{display:grid;grid-template-columns:1fr;gap:22px}
.tabs{display:flex;gap:18px;border-bottom:1px solid var(--line2);margin-bottom:14px}
.tabs button{background:none;border:0;color:var(--muted);font-size:14px;padding:0 0 9px}
.tabs button.on{color:var(--gold);border-bottom:2px solid var(--gold)}
.clock{background:linear-gradient(160deg,#16301f 0%,#0C1917 70%);border:1px solid var(--line);border-radius:20px;padding:16px}
.k{font-weight:600;font-size:12px;color:var(--gold);letter-spacing:.08em}
.who{font-weight:700;font-size:24px;margin:4px 0 2px;display:flex;align-items:center;flex-wrap:wrap}
.deal{font-size:12.5px;color:var(--muted)}.deal b{color:var(--gold);font-weight:600}
.foot{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:10px}
.list{display:flex;flex-direction:column;gap:6px}
.li{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;background:var(--card);border:1px solid var(--line2);font-size:13px}
.li .n{font-weight:600;font-size:12px;color:var(--gold);width:34px;flex:none}
.li .o{flex:1;min-width:0;font-weight:600}.li .o small{display:block;color:var(--muted);font-weight:400;font-size:11px}
.li .p{text-align:right;font-size:12.5px;min-width:0}.li .p small{display:block;color:var(--muted);font-size:11px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.gh{font-weight:600;font-size:11px;letter-spacing:.12em;color:var(--muted);text-align:center;text-transform:uppercase}
.t{background:var(--card);border:1px solid var(--line2);border-radius:14px;padding:9px 10px;min-height:66px;min-width:0}
.t .n{font-weight:600;font-size:11px;color:var(--gold)}
.t .o{font-size:12.5px;font-weight:600;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.t .o small{color:var(--muted);font-weight:400}
.t .pl{font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.t .pl small{color:var(--muted)}
.t.now{border:1.5px solid var(--gold);background:linear-gradient(160deg,#1d3524,#0C1917)}
.t.open .pl{color:#5e6a63}
.done{text-align:center;padding:26px 10px}.done .who{justify-content:center}
@media(min-width:960px){.wrap{grid-template-columns:380px 1fr}.tabs{display:none}.pane{display:block!important}}
"""

_DRAFT_JS = _SHARED_JS + r"""
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  const el = root(parentElement, 'ld-draft');
  const st = parentElement.__ld || (parentElement.__ld = { q: '', tab: 'clock', sel: null, armed: null, token: null });
  if (st.token !== data.token) { st.q = ''; st.sel = null; st.armed = null; st.token = data.token; }
  const taken = new Set(data.taken || []);
  const clock = data.clock;

  const slotRow = (s) => `<div class="li"><span class="n">${esc(s.label)}</span>
    <div class="o">${esc(s.owner)}${s.via ? `<span class="tag">via ${esc(s.via)}</span>` : ''}
    ${s.player ? `<small>${money(s.salary)} · ${s.years} yr${s.years > 1 ? 's' : ''}</small>` : ''}</div>
    ${s.player ? `<span class="pos">${esc(s.pos || '')}</span><div class="p">${esc(s.player)}</div>` : `<div class="p" style="color:var(--muted)">${money(s.salary)}</div>`}</div>`;

  function clockHtml() {
    if (!clock) return `<div class="clock done"><div class="k">${esc(data.year)} ROOKIE DRAFT</div><div class="who">Draft complete</div><div class="deal">All ${data.board.length} picks are in.</div></div>`;
    return `<div class="clock"><div class="k">ON THE CLOCK · PICK ${esc(clock.label)}</div>
      <div class="who">${esc(clock.owner)}${clock.via ? `<span class="tag">via ${esc(clock.via)}</span>` : ''}</div>
      <div class="deal">Rookie deal: <b>${money(clock.salary)} · ${clock.years} yr${clock.years > 1 ? 's' : ''}</b> + ${money(clock.option)} option year</div>
      <div style="margin-top:12px"><input class="search" type="search" autocomplete="off" placeholder="Search ${esc(data.year)} rookies" value="${esc(st.q)}"></div>
      <div class="res"></div>
      <div class="foot"><span>${clock.via ? `Originally ${esc(clock.via)}'s pick` : 'Tap a name, then Draft'}</span><span>${esc(data.year)} rookies only · ${data.left} left</span></div></div>`;
  }
  function boardHtml() {
    let s = '<div class="grid">' + [1,2,3].map(r => `<div class="gh">Round ${r}</div>`).join('');
    const rounds = [1,2,3].map(r => data.board.filter(x => x.round === r));
    const rows = Math.max(...rounds.map(r => r.length));
    for (let i = 0; i < rows; i++) for (const r of rounds) {
      const x = r[i]; if (!x) { s += '<div></div>'; continue; }
      const now = clock && x.label === clock.label;
      s += `<div class="t ${now ? 'now' : ''} ${x.player ? '' : 'open'}"><div class="n">${esc(x.label)}${now ? ' · ON CLOCK' : ''}</div>
        <div class="o">${esc(x.owner)}${x.via ? ` <small>via ${esc(x.via)}</small>` : ''}</div>
        <div class="pl">${x.player ? `${esc(x.player)} <small>${esc(x.pos || '')}</small>` : money(x.salary)}</div></div>`;
    }
    return s + '</div>';
  }
  const upcoming = data.board.filter(x => !x.player && (!clock || x.label !== clock.label)).slice(0, 3);
  const made = data.board.filter(x => x.player).reverse();
  el.innerHTML = `${data.flash ? `<div class="flash ${data.flash.kind}">${esc(data.flash.text)}</div>` : ''}
    <div class="tabs"><button data-tab="clock" class="${st.tab === 'clock' ? 'on' : ''}">On the clock</button><button data-tab="board" class="${st.tab === 'board' ? 'on' : ''}">Full board</button></div>
    <div class="wrap"><div class="pane" style="display:${st.tab === 'clock' ? 'block' : 'none'}">${clockHtml()}
      ${upcoming.length ? `<div class="lbl">Up next</div><div class="list">${upcoming.map(slotRow).join('')}</div>` : ''}
      ${made.length ? `<div class="lbl">Made</div><div class="list">${made.map(slotRow).join('')}</div>` : ''}</div>
    <div class="pane" style="display:${st.tab === 'board' ? 'block' : 'none'}">${boardHtml()}</div></div>`;

  el.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { st.tab = b.dataset.tab; render(); });
  function render() {
    el.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === st.tab));
    const panes = el.querySelectorAll('.pane');
    if (window.matchMedia('(min-width:960px)').matches) return;
    panes[0].style.display = st.tab === 'clock' ? 'block' : 'none';
    panes[1].style.display = st.tab === 'board' ? 'block' : 'none';
  }
  if (!clock) return;
  const input = el.querySelector('.search');
  const res = el.querySelector('.res');
  function draw() {
    const hits = rank(data.rookies, st.q, null, 8);
    if (!st.q.trim()) { res.innerHTML = ''; return; }
    if (!hits.length) { res.innerHTML = '<div class="empty">No rookie in this class matches.</div>'; return; }
    res.innerHTML = hits.map(p => {
      const gone = taken.has(p.id);
      const armed = st.armed === p.id;
      return `<div class="r ${st.sel === p.id ? 'sel' : ''}" data-id="${esc(p.id)}"><span class="pos">${esc(p.pos)}</span>
        <div class="nm">${esc(p.name)}<small>${esc([p.college, p.team].filter(Boolean).join(' · '))}${gone ? ' · already drafted' : ''}</small></div>
        ${gone ? '<span style="font-size:11px;color:var(--muted)">taken</span>' : `<button class="gbtn ${armed ? 'confirm' : ''}" data-draft="${esc(p.id)}">${armed ? 'Confirm' : 'Draft'}</button>`}</div>`;
    }).join('');
    res.querySelectorAll('[data-draft]').forEach(b => b.onclick = (e) => { e.stopPropagation(); arm(b.dataset.draft); });
    res.querySelectorAll('.r').forEach(r => r.onclick = () => { st.sel = r.dataset.id; draw(); });
  }
  function arm(id) {
    if (taken.has(id)) return;
    if (st.armed === id) {
      st.armed = null;
      res.querySelectorAll('[data-draft]').forEach(b => { b.disabled = true; });
      const b = res.querySelector(`[data-draft="${CSS.escape(id)}"]`); if (b) b.textContent = 'Saving…';
      setTriggerValue('draft', { player_id: id, round: clock.round, pick: clock.pick, owner_id: clock.owner_id });
      return;
    }
    st.armed = id; st.sel = id; draw();
    clearTimeout(st.timer);
    st.timer = setTimeout(() => { if (st.armed === id) { st.armed = null; draw(); } }, 5000);
  }
  input.oninput = () => { st.q = input.value; st.sel = null; st.armed = null; draw(); };
  input.onkeydown = (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const first = rank(data.rookies, st.q, taken, 1)[0];
    const target = st.armed || st.sel || (first && first.id);
    if (target) arm(target);
  };
  draw();
  if (window.matchMedia('(min-width:960px)').matches || st.tab === 'clock') {
    try { input.focus({ preventScroll: true }); const v = input.value; input.value = ''; input.value = v; } catch (e) {}
  }
}
"""

# ---------------------------------------------------------------- FA auction

_AUCTION_CSS = _SHARED_CSS + """
.cols{display:grid;grid-template-columns:1fr;gap:12px}
@media(min-width:900px){.cols{grid-template-columns:1fr 1fr;gap:18px}}
.pl{display:flex;align-items:center;gap:10px;background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:10px 12px}
.x{background:none;border:0;color:var(--muted);font-size:20px;line-height:1;padding:2px 4px}
.bid{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:16px 0 10px}
.pm{width:56px;height:56px;border-radius:16px;border:1px solid var(--line);background:transparent;color:var(--gold);font-size:28px;flex:none}
.amtwrap{flex:1;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:50px;letter-spacing:-.02em}
.amtwrap span{color:inherit}
.amt{width:3.2ch;background:transparent;border:0;color:inherit;font:inherit;text-align:left;outline:none;padding:0;caret-color:var(--gold)}
.bad .amtwrap{color:var(--red)}
.chips{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.chips button{background:transparent;border:1px solid var(--line);border-radius:999px;padding:8px 0;font-size:13px;font-weight:600;color:var(--gold)}
.yseg{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.yseg button{background:transparent;border:1px solid var(--line);border-radius:999px;padding:10px 0;font-weight:600;font-size:14px;color:var(--text)}
.yseg button.on{background:var(--gold);color:#1a1405;border-color:var(--gold)}
.yseg button.badon{background:#5a2020;border-color:var(--red);color:#fff}
.yseg button:disabled{opacity:.3;cursor:not-allowed}
.warn{margin-top:10px;background:var(--redbg);border:1px solid var(--red);color:#f6c9c6;border-radius:12px;padding:9px 12px;font-size:12.5px}
.yrs{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.y{border:1px solid var(--line2);background:var(--card2);border-radius:14px;padding:10px 4px;text-align:center;color:var(--text)}
.y .k{font-weight:600;font-size:10.5px;letter-spacing:.1em;color:var(--muted)}
.y .v{font-weight:700;font-size:22px;margin-top:2px}.y .c{font-size:10.5px;color:var(--muted)}
.y.on{border:1.5px solid var(--gold);background:linear-gradient(160deg,#1d3524,#0f201b)}.y.on .k{color:var(--gold)}
.y.badon{border:1.5px solid var(--red);background:var(--redbg)}.y.badon .k,.y.badon .c{color:var(--red)}
.y.lock{cursor:default}.y.lock .v{color:#55605a;font-size:17px}
.owners{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}
.ow{border:1px solid var(--line2);background:transparent;border-radius:12px;padding:8px 2px;text-align:center;font-size:12.5px;font-weight:600;color:var(--text);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ow small{display:block;color:var(--muted);font-weight:400;font-size:10px}
.ow.on{background:var(--gold);color:#1a1405;border-color:var(--gold)}.ow.on small{color:#4a3a10}
.ow.short{opacity:.4}.ow.short.on{opacity:1}
.note{text-align:center;font-size:11.5px;color:var(--muted);margin-top:8px}
.list{display:flex;flex-direction:column;gap:6px}
.li{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:12px;background:var(--card2);border:1px solid var(--line2);font-size:13px}
.li .o{flex:1;min-width:0;font-weight:600}.li .o small{display:block;color:var(--muted);font-weight:400;font-size:11px}
"""

_AUCTION_JS = _SHARED_JS + r"""
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  const el = root(parentElement, 'ld-auction');
  const R = data.rules;
  const st = parentElement.__ld || (parentElement.__ld = { q: '', player: null, bid: 1, years: 1, owner: null, armed: false, token: null });
  if (st.token !== data.token) {
    // A finished award starts a fresh auction; an error keeps everything.
    if (data.flash && data.flash.kind === 'ok') Object.assign(st, { q: '', player: null, bid: 1, years: 1, owner: null });
    st.armed = false; st.token = data.token;
  }
  const minFor = (y) => Number(R.minimums[String(y)] || 0);
  const valid = (b, y) => y >= 1 && y <= R.max_years && b >= 1 && b >= minFor(y);
  const equiv = (b, y, t) => {
    if (b < minFor(t)) return null;
    const v = b * Math.pow(1 + R.discount_pct / 100, y - t);
    return Math.ceil(Math.round(v * 1e6) / 1e6);
  };
  const ownerById = Object.fromEntries(data.owners.map(o => [o.id, o]));

  el.innerHTML = `${data.flash ? `<div class="flash ${data.flash.kind}">${esc(data.flash.text)}</div>` : ''}
    <div class="cols"><div class="card" data-a="bidcard"></div><div class="card" data-a="wincard"></div></div>`;
  const bidCard = el.querySelector('[data-a=bidcard]');
  const winCard = el.querySelector('[data-a=wincard]');

  function playerBlock() {
    if (st.player) {
      const p = st.player;
      return `<div class="pl"><span class="pos">${esc(p.pos)}</span><div class="nm">${esc(p.name)}<small>${esc([p.team || 'FA', 'free agent'].join(' · '))}</small></div><button class="x" data-a="clear" aria-label="Clear player">✕</button></div>`;
    }
    return `<input class="search" type="search" autocomplete="off" placeholder="Search free agents" value="${esc(st.q)}"><div class="res"></div>`;
  }
  // The amount box is drawn once per full redraw; typing in it only redraws
  // the parts below, so the caret stays where the user is typing.
  function drawBid() {
    bidCard.innerHTML = `${playerBlock()}
      <div class="bid"><button class="pm" data-step="-1" aria-label="Lower bid">−</button>
        <div class="amtwrap"><span>$</span><input class="amt" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="off" value="${st.bid}" aria-label="Bid"></div>
        <button class="pm" data-step="1" aria-label="Raise bid">+</button></div>
      <div data-a="rest"></div>`;
    const amt = bidCard.querySelector('.amt');
    const size = () => { amt.style.width = Math.max(1, amt.value.length) + 0.4 + 'ch'; };
    size();
    // Tapping the box selects the old bid, so typing replaces it.
    amt.onfocus = amt.onclick = () => setTimeout(() => { try { amt.select(); } catch (e) {} }, 0);
    amt.oninput = () => {
      const digits = amt.value.replace(/[^0-9]/g, '').replace(/^0+(?=\d)/, '').slice(0, 4);
      if (digits !== amt.value) amt.value = digits;
      st.bid = digits ? Number(digits) : 0;
      st.armed = false;
      size(); drawRest(); drawWin();
    };
    amt.onkeydown = (e) => { if (e.key === 'Enter') amt.blur(); };
    amt.onblur = () => { if (!amt.value) { amt.value = String(st.bid || 0); size(); } };
    bidCard.querySelectorAll('.bid [data-step]').forEach(b => b.onclick = () => step(Number(b.dataset.step)));
    const clear = bidCard.querySelector('[data-a=clear]');
    if (clear) clear.onclick = () => { st.player = null; st.q = ''; st.armed = false; drawBid(); drawWin(); focusSearch(); };
    wireSearch();
    drawRest();
  }
  function step(n) {
    st.bid = Math.max(1, st.bid + n); st.armed = false;
    const amt = bidCard.querySelector('.amt');
    amt.value = String(st.bid); amt.style.width = Math.max(1, amt.value.length) + 0.4 + 'ch';
    drawRest(); drawWin();
  }
  function drawRest() {
    const ok = valid(st.bid, st.years);
    let tiles = '';
    for (let y = 1; y <= 4; y++) {
      const allowed = y <= R.max_years;
      const cur = y === st.years;
      const v = equiv(st.bid, st.years, y);
      const label = `${y} YR${y > 1 ? 'S' : ''}`;
      if (!allowed) tiles += `<div class="y lock"><div class="k">${label}</div><div class="v">—</div><div class="c">not allowed</div></div>`;
      else if (cur && !ok) tiles += `<button class="y badon" data-y="${y}"><div class="k">${label}</div><div class="v">${money(st.bid)}</div><div class="c">needs ${money(minFor(y))}+</div></button>`;
      else if (v === null) tiles += `<div class="y lock"><div class="k">${label}</div><div class="v">🔒</div><div class="c">at ${money(minFor(y))}</div></div>`;
      else tiles += `<button class="y ${cur ? 'on' : ''}" data-y="${y}" data-v="${v}"><div class="k">${label}</div><div class="v">${money(cur ? st.bid : v)}</div><div class="c">${cur ? 'current' : 'same value'}</div></button>`;
    }
    bidCard.className = 'card' + (ok ? '' : ' bad');
    const rest = bidCard.querySelector('[data-a=rest]');
    rest.innerHTML = `<div class="chips">${[1,2,5,10].map(n => `<button data-step="${n}">+${n}</button>`).join('')}</div>
      <div class="lbl">Bid is for</div>
      <div class="yseg">${[1,2,3,4].map(y => `<button data-yrs="${y}" ${y > R.max_years ? 'disabled' : ''} class="${y === st.years ? (ok ? 'on' : 'badon') : ''}">${y} yr${y > 1 ? 's' : ''}</button>`).join('')}</div>
      ${ok ? '' : `<div class="warn">✕ Not a valid bid. ${st.bid < 1 ? 'Bids start at $1.' : `A ${st.years}-year deal needs at least ${money(minFor(st.years))}. Raise the bid or pick fewer years.`}</div>`}
      <div class="lbl">Same bid at other lengths · tap to switch</div><div class="yrs">${tiles}</div>`;
    rest.querySelectorAll('[data-step]').forEach(b => b.onclick = () => step(Number(b.dataset.step)));
    rest.querySelectorAll('[data-yrs]').forEach(b => b.onclick = () => { st.years = Number(b.dataset.yrs); st.armed = false; drawRest(); drawWin(); });
    rest.querySelectorAll('.y[data-v]').forEach(b => b.onclick = () => {
      st.bid = Number(b.dataset.v); st.years = Number(b.dataset.y); st.armed = false;
      const amt = bidCard.querySelector('.amt');
      amt.value = String(st.bid); amt.style.width = Math.max(1, amt.value.length) + 0.4 + 'ch';
      drawRest(); drawWin();
    });
  }
  function wireSearch() {
    const input = bidCard.querySelector('.search');
    if (!input) return;
    const res = bidCard.querySelector('.res');
    const draw = () => {
      const hits = rank(data.players, st.q, null, 8);
      res.innerHTML = !st.q.trim() ? '' : hits.length ? hits.map(p => `<div class="r" data-id="${esc(p.id)}"><span class="pos">${esc(p.pos)}</span><div class="nm">${esc(p.name)}<small>${esc(p.team || 'FA')}</small></div></div>`).join('') : '<div class="empty">No free agent matches. Players under contract are left out.</div>';
      res.querySelectorAll('.r').forEach(r => r.onclick = () => pick(r.dataset.id));
    };
    const pick = (id) => { st.player = data.players.find(p => p.id === id) || null; st.armed = false; drawBid(); drawWin(); };
    input.oninput = () => { st.q = input.value; draw(); };
    input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); const first = rank(data.players, st.q, null, 1)[0]; if (first) pick(first.id); } };
    draw();
  }
  function focusSearch() { const i = bidCard.querySelector('.search'); if (i) try { i.focus({ preventScroll: true }); } catch (e) {} }
  function drawWin() {
    const ok = valid(st.bid, st.years);
    const owner = st.owner && ownerById[st.owner];
    const ready = ok && st.player && owner;
    const over = owner && owner.room !== null && st.bid > owner.room;
    let label = 'Pick a player, bid and winner';
    if (!ok) label = 'Fix the bid to award';
    else if (ready) label = st.armed ? `Tap again to confirm · ${owner.name} gets ${st.player.name}` : `Award ${st.player.name} to ${owner.name} · ${money(st.bid)} × ${st.years} yr${st.years > 1 ? 's' : ''}`;
    else if (st.player && !owner) label = 'Tap the winning owner';
    else if (!st.player) label = 'Search for a player first';
    winCard.innerHTML = `<div class="lbl" style="margin-top:0">Winning bidder</div>
      <div class="owners">${data.owners.map(o => `<button class="ow ${o.id === st.owner ? 'on' : ''} ${o.room !== null && st.bid > o.room ? 'short' : ''}" data-o="${esc(o.id)}">${esc(o.name)}<small>${o.room === null ? '—' : money(o.room) + ' room'}</small></button>`).join('')}</div>
      ${over ? `<div class="warn">${esc(owner.name)} only has ${money(owner.room)} of cap room.</div>` : ''}
      <button class="big ${st.armed ? 'confirm' : ''}" data-a="award" ${ready ? '' : 'disabled'}>${esc(label)}</button>
      <div class="note">${ready ? (st.armed ? 'This adds the contract to their roster.' : 'Asks once to confirm, then adds the contract to their roster.') : ''}</div>
      ${data.recent && data.recent.length ? `<div class="lbl">Last awarded</div><div class="list">${data.recent.map(r => `<div class="li"><span class="pos">${esc(r.pos || '')}</span><div class="o">${esc(r.player)}<small>${esc(r.owner)} · ${money(r.salary)} × ${r.years} yr${r.years > 1 ? 's' : ''}</small></div></div>`).join('')}</div>` : ''}`;
    winCard.querySelectorAll('[data-o]').forEach(b => b.onclick = () => { st.owner = b.dataset.o; st.armed = false; drawWin(); });
    const award = winCard.querySelector('[data-a=award]');
    award.onclick = () => {
      if (!(valid(st.bid, st.years) && st.player && st.owner)) return;
      if (!st.armed) {
        st.armed = true; drawWin();
        clearTimeout(st.timer); st.timer = setTimeout(() => { if (st.armed) { st.armed = false; drawWin(); } }, 6000);
        return;
      }
      st.armed = false;
      award.disabled = true; award.textContent = 'Saving…';
      setTriggerValue('award', { player_id: st.player.id, team_id: st.owner, salary: st.bid, years: st.years });
    };
  }
  drawBid(); drawWin();
  if (!st.player) focusSearch();
}
"""

_draft_room = st.components.v2.component(
    "ld_draft_room", css=_DRAFT_CSS, js=_DRAFT_JS,
)
_fa_auction = st.components.v2.component(
    "ld_fa_auction", css=_AUCTION_CSS, js=_AUCTION_JS,
)


def draft_room(data: dict[str, Any], *, key: str) -> Any:
    """Mount the draft room. Returns the ``draft`` trigger payload or None."""
    result = _draft_room(data=data, key=key, on_draft_change=lambda: None)
    return getattr(result, "draft", None)


def fa_auction(data: dict[str, Any], *, key: str) -> Any:
    """Mount the FA auction. Returns the ``award`` trigger payload or None."""
    result = _fa_auction(data=data, key=key, on_award_change=lambda: None)
    return getattr(result, "award", None)
