// 哨兵紀錄頁：PIN 登入 → 容量、錄影清單（截圖、回放、下載、刪除）、事件紀錄
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtTime = (t) => new Date(t * 1000).toLocaleString('zh-TW', { hour12: false });
const fmtMB = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`);

const FILTERS = [
  ['全部', ''],
  ['開鎖', 'unlock,lock'],
  ['訪客', 'guest_detected'],
  ['哨兵', 'stranger_detected,sentinel_saved'],
  ['異常', 'door_failed,pin_rejected,gesture_timeout'],
];
let lastClipId = null;
let eventFilter = '';
let openId = null;

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) { showLogin(); throw new Error('login'); }
  return r.json();
}

// ---------- 登入 ----------
function showLogin(msg = '') {
  $('login').hidden = false;
  $('main').hidden = true;
  $('loginMsg').textContent = msg;
}

async function login(pin) {
  pin = pin ?? $('pin').value;
  const r = await fetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
  });
  const d = await r.json();
  if (!d.success) { showLogin(d.message); return false; }
  try { localStorage.setItem('door_pin', pin); } catch (e) {}
  $('login').hidden = true;
  $('main').hidden = false;
  refresh();
  return true;
}

async function start() {
  const s = await (await fetch('/api/session')).json();
  if (!s.sentinel) { document.body.insertAdjacentHTML('beforeend', '<div class="card">哨兵功能未啟用（SENTINEL=0）</div>'); return; }
  if (!s.required || s.logged_in) { $('main').hidden = false; refresh(); return; }
  let saved = '';
  try { saved = localStorage.getItem('door_pin') || ''; } catch (e) {}
  if (!(saved && await login(saved))) showLogin();
}

// ---------- 容量 ----------
function renderUsage(u) {
  const pct = Math.min(100, (u.used_mb / u.limit_mb) * 100);
  const cls = pct > 90 ? 'bad' : pct > 70 ? 'warn' : 'ok';
  $('usage').innerHTML = `
    <div class="usage-head"><b>錄影容量</b><span>${fmtMB(u.used_mb)} / ${fmtMB(u.limit_mb)}</span></div>
    <div class="meter"><div class="${cls}" style="width:${pct.toFixed(1)}%"></div></div>
    <div class="muted">${u.count} 段 · 保留 ${u.keep_days} 天 · 超過上限自動刪最舊的 · SD 卡剩 ${fmtMB(u.disk_free_mb)}</div>`;
}

// ---------- 錄影 ----------
function clipCard(r) {
  const status = r.status === 'recording' ? '<span class="badge rec">● 錄影中</span>'
    : r.status === 'failed' ? '<span class="badge warn">不完整</span>' : '';
  const notified = r.notified ? '<span class="badge">已建議通知</span>' : '';
  const thumb = r.snapshot ? `<img class="thumb" loading="lazy" src="/records/${r.id}/snapshot.jpg" alt="">`
    : '<div class="thumb empty">無截圖</div>';
  const dist = r.min_distance == null ? '—' : r.min_distance.toFixed(0);
  return `<div class="clip" id="clip${r.id}">
    <div class="clip-row" onclick="toggle(${r.id}, '${r.status}')">
      ${thumb}
      <div class="clip-info">
        <b>${fmtTime(r.started)}</b>
        <div class="muted">#${r.id} · ${Math.round(r.duration)} 秒 · ${(r.size_bytes / 1048576).toFixed(1)} MB · 最小距離 ${dist}</div>
        <div>${status}${notified}</div>
      </div>
    </div>
    <div class="player" id="player${r.id}" hidden></div>
  </div>`;
}

function toggle(id, status) {
  if (openId !== null && openId !== id) closePlayer(openId);
  const p = $(`player${id}`);
  if (!p.hidden) { closePlayer(id); return; }
  openId = id;
  p.hidden = false;
  if (status === 'recording') {
    p.innerHTML = '<div class="muted">還在錄影中，結束後重新整理就能播放。即時畫面請看控制頁。</div>';
    return;
  }
  p.innerHTML = `<img id="video${id}" src="/records/${id}/play?t=${Date.now()}" alt="錄影回放">
    <div class="row">
      <button onclick="replay(${id}, 1)">重播</button>
      <button onclick="replay(${id}, 2)">2 倍速</button>
      <a class="btn" href="/records/${id}/download">下載 AVI</a>
      <button class="lock" onclick="removeClip(${id})">刪除</button>
    </div>`;
}

function closePlayer(id) {
  const p = $(`player${id}`);
  if (p) { p.innerHTML = ''; p.hidden = true; }      // 清掉 <img> 才會中斷串流
  if (openId === id) openId = null;
}

function replay(id, speed) {
  $(`video${id}`).src = `/records/${id}/play?speed=${speed}&t=${Date.now()}`;
}

async function removeClip(id) {
  if (!confirm(`確定刪除錄影 #${id}？刪除後無法復原。`)) return;
  const d = await api(`/api/records/${id}`, { method: 'DELETE' });
  if (d.success) { closePlayer(id); $(`clip${id}`).remove(); refreshUsage(); } else alert(d.message);
}

async function loadClips(more = false) {
  const url = '/api/records?limit=20' + (more && lastClipId ? `&before=${lastClipId}` : '');
  const d = await api(url);
  renderUsage(d.usage);
  const html = d.records.map(clipCard).join('');
  if (more) $('clipList').insertAdjacentHTML('beforeend', html);
  else $('clipList').innerHTML = html || '<div class="card muted">還沒有錄影。陌生人在門口超過設定秒數就會自動錄影。</div>';
  if (d.records.length) lastClipId = d.records[d.records.length - 1].id;
  $('moreClips').hidden = d.records.length < 20;
}

async function refreshUsage() {
  const d = await api('/api/records?limit=1');
  renderUsage(d.usage);
}

// ---------- 事件 ----------
function renderChips() {
  $('chips').innerHTML = FILTERS.map(([label, kinds]) =>
    `<button class="chip ${kinds === eventFilter ? 'active' : ''}" onclick="filterEvents('${kinds}')">${label}</button>`).join('');
}

function filterEvents(kinds) { eventFilter = kinds; renderChips(); loadEvents(); }

async function loadEvents() {
  const d = await api('/api/events?limit=200' + (eventFilter ? `&kinds=${eventFilter}` : ''));
  $('eventList').innerHTML = d.events.map((e) =>
    `<li><span class="muted">${fmtTime(e.time)}</span> ${esc(e.text)}</li>`).join('') || '<li class="muted">沒有紀錄</li>';
}

// ---------- 分頁 ----------
function showTab(tab) {
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $('clips').hidden = tab !== 'clips';
  $('events').hidden = tab !== 'events';
  if (tab === 'events') {
    if (openId !== null) closePlayer(openId);         // 切走時停止回放串流
    loadEvents();
  }
}

function refresh() {
  renderChips();
  loadClips().catch(() => {});
}

start();
