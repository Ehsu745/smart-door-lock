// 智慧門鎖單頁 App：門鎖 / 成員 / 紀錄 / 設定
// 連得到 /api/status 就用 Pi 的資料；連不到（例如直接用 http.server 預覽版面）就切到示範模式。
'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = (n) => String(n).padStart(2, '0');
const hm = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const gb = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);
const PAGES = ['door', 'people', 'history', 'settings'];
let DEMO = false;

/* =========================================================
   資料層：真實 API 與示範資料回傳相同格式
   ========================================================= */
class AuthError extends Error {}

async function http(url, opts = {}) {
  const r = await fetch(url, { cache: 'no-store', ...opts,
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (r.status === 401) throw new AuthError(data.message || '需要輸入 PIN');
  return { status: r.status, ok: r.ok, ...data };
}
const post = (url, body) => http(url, { method: 'POST', body: JSON.stringify(body || {}) });

const Real = {
  session: () => http('/api/session'),
  status: () => http('/api/status'),
  login: (pin) => post('/api/login', { pin }),
  logout: () => post('/api/logout'),
  action: (action, pin) => post('/api/action', { action, pin }),
  changePin: (old, pin) => post('/api/pin', { old, new: pin }),
  users: async () => (await http('/api/users')).users,
  events: async (kinds, before) => (await http(`/api/events?limit=60${kinds ? `&kinds=${kinds.join(',')}` : ''}${before ? `&before=${before}` : ''}`)).events,
  settings: (refresh) => http('/api/settings' + (refresh ? '?refresh=1' : '')),
  deleteRecord: (id) => http(`/api/records/${id}`, { method: 'DELETE' }),
  setRole: (name, role, pin) => post(`/api/users/${encodeURIComponent(name)}/role`, { role, pin }),
  deleteUser: (name, pin) => post(`/api/users/${encodeURIComponent(name)}/delete`, { pin, confirm: name }),
  photo: (name) => `/api/users/${encodeURIComponent(name)}/photo`,
  snapshot: (id) => `/records/${id}/snapshot.jpg`,
  play: (id, speed = 1) => `/records/${id}/play?speed=${speed}&t=${Date.now()}`,
  download: (id) => `/records/${id}/download`,
};

const Demo = (() => {
  const now = () => Date.now() / 1000;
  const t0 = now(), day = 86400;
  const st = { auto: true, unlockedUntil: 0, gestureUntil: t0 + 10, pin: '1234', loggedIn: false, pinVersion: 0 };
  let nextId = 100;
  const ev = (dt, kind, text, extra = {}) => ({ id: nextId--, time: t0 - dt, kind, text, data: {}, ...extra });
  const events = [
    ev(120, 'lock', '自動上鎖'),
    ev(128, 'unlock', '人臉 eric + 手勢 YA(Victory) → 已開鎖'),
    ev(1800, 'sentinel_saved', '哨兵：錄影 #12 已儲存（23 秒）', { clip: { id: 12, duration: 23, snapshot: false, status: 'done' } }),
    ev(3600, 'guest_detected', '偵測到訪客：amy，建議開鎖'),
    ev(3550, 'unlock', '手動解鎖（手機） → 已開鎖'),
    ev(3600 * 5, 'pin_rejected', '網頁 PIN 錯誤（192.168.1.23）'),
    ev(day + 600, 'gesture_timeout', 'yang 手勢逾時（8 秒），3 秒後可重試'),
    ev(day + 560, 'unlock', '人臉 yang + 手勢 讚(Thumb_Up) → 已開鎖'),
    ev(day + 7200, 'sentinel_saved', '哨兵：錄影 #11 已儲存（61 秒）', { clip: { id: 11, duration: 61, snapshot: false, status: 'done' } }),
    ev(9 * day, 'sentinel_saved', '哨兵：錄影 #3 已儲存（14 秒）', { clip: { id: 3, deleted: true } }),
    ev(2 * day + 4000, 'door_failed', '手動解鎖（手機） → UNLOCK 失敗：Arduino 沒有回應'),
  ];
  const demoUsers = [{ name: 'eric', role: 'master', photos: 30, last_seen: t0 - 128 }, { name: 'yang', role: 'master', photos: 30, last_seen: t0 - day - 560 },
    { name: 'amy', role: 'guest', photos: 30, last_seen: t0 - 3600 }, { name: 'kevin', role: 'guest', photos: 24, last_seen: null }];
  const push = (kind, text) => events.unshift({ id: 1000 + events.length, time: now(), kind, text, data: {} });
  const authed = () => st.loggedIn;
  const need = () => { if (!authed()) throw new AuthError('需要輸入 PIN'); };
  return {
    session: async () => ({ required: !!st.pin, logged_in: authed(), sentinel: true }),
    async status() {
      const p = (now() - t0) % 40;                     // 劇本：每 40 秒一輪，訪客 → 陌生人錄影
      const guest = p > 14 && p < 22, stranger = p > 25 && p < 37;
      const gesture = now() < st.gestureUntil;
      const left = Math.max(0, st.unlockedUntil - now());
      return {
        name: gesture ? 'eric' : guest ? 'amy' : stranger ? 'Unknown' : '無人臉',
        category: gesture ? 'master' : guest ? 'guest' : stranger ? 'Stranger' : '',
        visitor: guest && !left ? 'amy' : null, phase: gesture && !left ? 'gesture' : 'face', target: 'eric',
        countdown: Math.max(0, st.gestureUntil - now()), allowed: ['讚(Thumb_Up)', 'YA(Victory)'],
        unlocked: left > 0, unlock_left: left, unlock_seconds: 8, auto: st.auto, require_gesture: true,
        connected: true, arduino: '模擬模式（未接 Arduino）', pin_required: !!st.pin,
        sentinel: { state: stranger && p > 30 ? 'recording' : stranger ? 'watching' : 'idle', seconds: Math.round(p - 30) },
      };
    },
    async login(pin) {
      if (pin !== st.pin) return { ok: false, status: 403, message: 'PIN 錯誤' };
      st.loggedIn = true; return { ok: true, status: 200 };
    },
    async logout() { st.loggedIn = false; return { ok: true }; },
    async action(act, pin) {
      if (!authed()) {
        if (!pin) throw new AuthError('需要輸入 PIN');
        if (pin !== st.pin) return { ok: false, status: 403, message: 'PIN 錯誤' };
        st.loggedIn = true;
      }
      if (act === 'unlock') { st.unlockedUntil = now() + 8; st.gestureUntil = 0; push('unlock', '手動解鎖（手機） → 已開鎖'); return { ok: true, message: '已開鎖' }; }
      if (act === 'lock') { st.unlockedUntil = 0; push('lock', '手動上鎖（手機） → 已上鎖'); return { ok: true, message: '已上鎖' }; }
      st.auto = act === 'auto_on';
      return { ok: true, message: st.auto ? '自動解鎖已開啟' : '自動解鎖已關閉，只能用手機開門' };
    },
    async changePin(old, pin) {
      if (st.pin && old !== st.pin) return { ok: false, status: 403, message: 'PIN 錯誤' };
      st.pin = pin; st.loggedIn = true; push('pin_changed', '網頁變更 PIN（示範）');
      return { ok: true, message: 'PIN 已變更，其他手機需要重新輸入' };
    },
    async users() { need(); return demoUsers.map((u) => ({ ...u })); },
    async events(kinds, before) {
      need();
      return events.filter((e) => (!kinds || kinds.includes(e.kind)) && (!before || e.id < before)).sort((a, b) => b.time - a.time);
    },
    async settings() {
      need();
      return {
        line: { configured: true, limit: 200, used: 13, left: 187, reset: '12/1', basic_id: '@your_line_id', target: 'broadcast',
          notify: { sent: 3, last_sent: '今天 15:02', last_text: '【智慧門鎖】15:02 訪客 amy 在門口。要開門請到手機控制頁操作。', last_error: null } },
        storage: { count: 23, used_mb: 312, limit_mb: 2048, keep_days: 7, disk_free_mb: 18640, disk_total_mb: 29000 },
        sentinel: { enabled: true, trigger: 5, stop: 5, max: 60, cooldown: 300 },
        system: { arduino: '模擬模式（未接 Arduino）', connected: true, camera: 'Pi 相機（rpicam）', gesture: 'MediaPipe Gesture Recognizer',
          rule: '人臉＋手勢（讚(Thumb_Up) / YA(Victory)）', unlock_seconds: 8, version: '2.2.0（示範）' },
        security: { pin_required: !!st.pin, source: 'file' },
      };
    },
    async setRole(name, role, pin) {
      need();
      if (pin !== st.pin) return { ok: false, status: 403, message: 'PIN 錯誤' };
      const u = demoUsers.find((x) => x.name === name);
      u.role = role;
      push('role_changed', `網頁將 ${name} 改成${role === 'master' ? '主人' : '訪客'}（示範）`);
      return { ok: true, message: `${name} 已改成${role === 'master' ? '主人' : '訪客'}` };
    },
    async deleteUser(name, pin) {
      need();
      if (pin !== st.pin) return { ok: false, status: 403, message: 'PIN 錯誤' };
      if (demoUsers.length <= 1) return { ok: false, status: 400, message: '至少要保留一位成員，無法刪除' };
      demoUsers.splice(demoUsers.findIndex((x) => x.name === name), 1);
      push('member_deleted', `網頁刪除成員 ${name}（示範）`);
      return { ok: true, message: `已刪除 ${name}` };
    },
    async deleteRecord(id) { need(); const e = events.find((x) => x.clip && x.clip.id === id); if (e) e.clip = { id, deleted: true }; return { ok: true, message: `已刪除錄影 #${id}` }; },
    photo: () => '',
    snapshot: () => '',
    play: () => '',
    download: () => '#',
  };
})();

let Api = Real;

/* =========================================================
   分頁：點選、左右滑動、網址 #door / #people / #history / #settings
   ========================================================= */
const track = $('track'), viewport = $('viewport');
const tabs = [...document.querySelectorAll('.tab')];
const loaders = [null, () => renderPeople(), () => renderTimeline(true), () => renderSettings()];
let page = 0;

function go(i, animate = true) {
  page = Math.max(0, Math.min(3, i));
  track.classList.toggle('anim', animate);
  track.style.transform = `translate3d(${-page * 25}%,0,0)`;
  $('tabInd').style.transform = `translateX(${page * 100}%)`;
  tabs.forEach((t, k) => t.setAttribute('aria-selected', String(k === page)));
  setLive(page === 0 && !document.hidden);           // 離開門鎖頁就停止影像串流，省 Pi 的 CPU 和網路
  if (loaders[page]) loaders[page]();
  if (page === 2) $('histDot').hidden = true;
  history.replaceState(null, '', '#' + PAGES[page]);
}
tabs.forEach((t) => t.addEventListener('click', () => go(+t.dataset.i)));
window.addEventListener('hashchange', () => { const i = PAGES.indexOf(location.hash.slice(1)); if (i >= 0 && i !== page) go(i); });

(() => {
  let x0, y0, t0, dx, mode = null;
  viewport.addEventListener('touchstart', (e) => {
    if (e.target.closest('.chips, .dial')) return;
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = performance.now(); dx = 0; mode = 'pending';
  }, { passive: true });
  viewport.addEventListener('touchmove', (e) => {
    if (!mode) return;
    const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
    if (mode === 'pending') {
      if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
      mode = Math.abs(mx) > Math.abs(my) * 1.2 ? 'swipe' : null;    // 偏垂直就交給頁面內捲動
      if (!mode) return;
      track.classList.remove('anim');
    }
    dx = mx;
    const edge = (page === 0 && dx > 0) || (page === 3 && dx < 0) ? 0.3 : 1;
    track.style.transform = `translate3d(${-page * viewport.clientWidth + dx * edge}px,0,0)`;
  }, { passive: true });
  viewport.addEventListener('touchend', () => {
    if (mode !== 'swipe') { mode = null; return; }
    const v = Math.abs(dx) / (performance.now() - t0);
    let next = page;
    if (Math.abs(dx) > viewport.clientWidth * 0.25 || v > 0.5) next = page + (dx < 0 ? 1 : -1);
    mode = null;
    go(next);
  });
})();

document.addEventListener('keydown', (e) => {
  if ($('sheet').classList.contains('show') || e.target.closest('input')) return;
  if (e.key === 'ArrowRight') go(page + 1);
  if (e.key === 'ArrowLeft') go(page - 1);
});

/* =========================================================
   依手機實際高度分配門鎖頁：影像、狀態列、鎖、按鈕剛好塞滿一個畫面
   ========================================================= */
function fitDoor() {
  const pageEl = $('p-door');
  const H = viewport.clientHeight, W = pageEl.clientWidth - 32;       // 左右各 16px 邊距
  if (!H || !W) return;
  const compact = H < 520;
  pageEl.classList.toggle('compact', compact);
  const strip = compact ? 60 : 68, controls = 52, stateH = compact ? 44 : 52, pad = 16 + 12 + 16;
  const room = H - strip - controls - stateH - pad;                    // 影像 + 鎖 可以用的高度
  let camH = Math.min(W * 3 / 4, room * (compact ? 0.56 : 0.62));
  let dial = Math.min(160, W * 0.42, room - camH);
  if (dial < 96) { dial = 96; camH = Math.max(120, room - dial); }      // 鎖至少 96px，好按
  camH = Math.min(camH, W * 3 / 4);
  const tight = camH + dial > room + 1;
  pageEl.classList.toggle('tight', tight);
  document.documentElement.style.setProperty('--cam-w', `${Math.round(camH * 4 / 3)}px`);
  document.documentElement.style.setProperty('--dial', `${Math.round(dial)}px`);
}
if (window.ResizeObserver) new ResizeObserver(fitDoor).observe(viewport);
window.addEventListener('resize', fitDoor);
window.addEventListener('orientationchange', () => setTimeout(fitDoor, 300));
if (window.visualViewport) window.visualViewport.addEventListener('resize', fitDoor);

/* =========================================================
   1. 門鎖
   ========================================================= */
const dial = $('dial'), holdRing = $('holdRing'), timerRing = $('timerRing');
const RING = 289, HOLD_MS = 900;
let last = {};

const setText = (id, text) => { const el = $(id); if (el.textContent !== text) el.textContent = text; };
const show = (id, on) => { const el = $(id); if (el.hidden === on) el.hidden = !on; };

function whoLabel(d) {
  if (!d.name || d.name === '無人臉') return '無人';
  if (d.category === 'master') return `${d.name}（主人）`;
  if (d.category === 'guest') return `${d.name}（訪客）`;
  if (d.category === 'Stranger') return '陌生人';
  return d.name;                                          // 多人入鏡等訊息
}

function renderDoor(d) {
  // 只改有變的欄位，動畫才不會被每秒的更新打斷
  dial.classList.toggle('open', d.unlocked);
  if (d.unlocked) {
    setText('stateTitle', '已開鎖');
    const n = Math.ceil(d.unlock_left);
    if ($('stateSub').dataset.n !== String(n)) { $('stateSub').dataset.n = n; $('stateSub').innerHTML = `<span class="n">${n}</span> 秒後自動上鎖`; }
    timerRing.style.opacity = 1;
    timerRing.style.strokeDashoffset = RING * (1 - d.unlock_left / (d.unlock_seconds || 8));
    dial.setAttribute('aria-label', '已開鎖');
  } else {
    delete $('stateSub').dataset.n;
    setText('stateTitle', '已上鎖');
    setText('stateSub', d.auto ? '按住鎖 1 秒解鎖' : '按住鎖 1 秒解鎖（自動解鎖已關閉）');
    timerRing.style.opacity = 0;
    dial.setAttribute('aria-label', '按住解鎖');
  }
  const rec = d.sentinel?.state === 'recording';
  show('rec', rec);
  if (rec && !last.rec && page !== 2) $('histDot').hidden = false;
  last.rec = rec;
  renderStrip(d, rec);
  const t = $('autoToggle');
  if (t.getAttribute('aria-checked') !== String(d.auto)) t.setAttribute('aria-checked', String(d.auto));
  setText('autoSub', d.auto ? (d.require_gesture ? '人臉＋手勢' : '只需人臉') : '已關閉，只能用手機開');
  $('conn').classList.toggle('off', !d.connected);
  setText('connText', d.connected ? (DEMO ? 'Arduino 模擬' : 'Arduino 已連線') : 'Arduino 未連線');
}

// 門口狀態列：同一個位置依優先順序顯示（等手勢 > 訪客 > 哨兵 > 誰在門口）
function renderStrip(d, rec) {
  let kind = 'idle', title = `門口：${whoLabel(d)}`, sub = '', count = '';
  if (d.phase === 'gesture' && !d.unlocked) {
    kind = 'gesture'; title = `${d.target || ''} 人臉通過`;
    sub = `請比出手勢：${(d.allowed || []).join(' 或 ')}`; count = String(Math.ceil(d.countdown || 0));
  } else if (d.visitor && !d.unlocked) {
    kind = 'guest'; title = `訪客 ${d.visitor} 在門口`; sub = '訪客不會自動開門，要讓他進來就按住鎖';
  } else if (rec) {
    kind = 'alert'; sub = `陌生人逗留，哨兵錄影中 ${d.sentinel.seconds ?? ''} 秒`;
  } else if (d.sentinel?.state === 'watching') {
    sub = `陌生人停留 ${Math.round(d.sentinel.seconds || 0)} 秒`;
  }
  const strip = $('strip');
  if (strip.dataset.kind !== kind) strip.dataset.kind = kind;
  setText('stripTitle', title); setText('stripSub', sub); setText('stripCount', count);
}

// 按住解鎖：外圈填滿才送出，放開就取消
let holdStart = 0, holdRaf = 0;
function holdTick() {
  const p = Math.min(1, (performance.now() - holdStart) / HOLD_MS);
  holdRing.style.strokeDashoffset = RING * (1 - p);
  if (p < 1) { holdRaf = requestAnimationFrame(holdTick); return; }
  endHold(false);
  if (navigator.vibrate) navigator.vibrate(30);
  doAction('unlock');
}
function startHold(e) {
  if (dial.classList.contains('open')) return;
  e.preventDefault();
  holdStart = performance.now();
  cancelAnimationFrame(holdRaf);
  holdRaf = requestAnimationFrame(holdTick);
}
function endHold(hint = true) {
  if (!holdStart) return;
  const held = performance.now() - holdStart;
  holdStart = 0;
  cancelAnimationFrame(holdRaf);
  holdRing.style.transition = 'stroke-dashoffset .25s';
  holdRing.style.strokeDashoffset = RING;
  setTimeout(() => (holdRing.style.transition = ''), 260);
  if (hint && held < HOLD_MS) toast('按住久一點才會解鎖');
}
dial.addEventListener('pointerdown', startHold);
['pointerup', 'pointerleave', 'pointercancel'].forEach((n) => dial.addEventListener(n, () => endHold()));
dial.addEventListener('keydown', (e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) startHold(e); });
dial.addEventListener('keyup', (e) => { if (e.key === ' ' || e.key === 'Enter') endHold(); });
dial.addEventListener('contextmenu', (e) => e.preventDefault());
$('lockBtn').addEventListener('click', () => doAction('lock'));
$('autoToggle').addEventListener('click', () => doAction($('autoToggle').getAttribute('aria-checked') === 'true' ? 'auto_off' : 'auto_on'));

const ACT_NAME = { unlock: '解鎖', lock: '上鎖', auto_on: '開啟自動解鎖', auto_off: '關閉自動解鎖' };

async function doAction(act) {
  let r;
  try { r = await Api.action(act); } catch (e) {
    if (!(e instanceof AuthError)) { toast('連不到門鎖'); return; }
    r = { status: 401 };
  }
  if (r.status === 401 || r.status === 403 || r.status === 429) {
    // 還沒登入（或 PIN 被改過）：輸入 PIN 後重送同一個動作
    askPin({ title: `輸入 PIN 以${ACT_NAME[act]}`, message: r.status === 429 ? r.message : '',
      submit: (pin) => Api.action(act, pin), done: (res) => toast(res.message) });
    return;
  }
  toast(r.message || (r.ok ? '完成' : '失敗'));
  poll();
}

// 示範模式的假鏡頭（每秒 8 幀，很省）
let camTimer = 0;
function demoCam() {
  const c = $('demoCam'), g = c.getContext('2d'), d = last.status || {};
  g.fillStyle = '#3a4650'; g.fillRect(0, 0, 480, 360);
  g.fillStyle = '#56636c'; g.fillRect(150, 30, 180, 330);
  g.fillStyle = '#6b7a83'; g.fillRect(162, 42, 156, 318);
  g.fillStyle = '#b4862c'; g.fillRect(300, 190, 10, 26);
  if (d.category) {
    const x = 210 + Math.sin(Date.now() / 770) * 14;
    g.fillStyle = '#c9a98f'; g.beginPath(); g.ellipse(x + 30, 150, 34, 42, 0, 0, 7); g.fill();
    g.fillStyle = '#2b3138'; g.fillRect(x - 14, 192, 88, 170);
    g.strokeStyle = { master: '#55d38f', guest: '#c79be3' }[d.category] || '#ff6a4d'; g.lineWidth = 3; g.strokeRect(x - 10, 102, 80, 96);
  }
  g.fillStyle = 'rgba(255,255,255,.8)'; g.font = '14px monospace';
  g.fillText(new Date().toLocaleTimeString('zh-TW', { hour12: false }), 18, 30);
}
function setLive(on) {
  clearInterval(camTimer);
  if (DEMO) { if (on) { demoCam(); camTimer = setInterval(demoCam, 125); } return; }
  const img = $('live');
  clearTimeout(liveRetry);
  if (on && !img.getAttribute('src')) { img.src = '/video_feed?t=' + Date.now(); liveSince = Date.now(); }
  if (!on && img.getAttribute('src')) img.removeAttribute('src');       // 拿掉 src 才會中斷 MJPEG 串流
}
// 串流斷了（伺服器 10 分鐘換一條、Wi-Fi 閃斷、被同一支手機的新分頁取代）就自動重連
let liveRetry = 0, liveSince = 0;
function reconnectLive(delay = 2000) {
  clearTimeout(liveRetry);
  liveRetry = setTimeout(() => {
    if (page !== 0 || document.hidden || DEMO) return;
    $('live').removeAttribute('src');
    setLive(true);
  }, delay);
}
$('live').addEventListener('error', () => reconnectLive());
setInterval(() => { if (!DEMO && page === 0 && !document.hidden && Date.now() - liveSince > 9 * 60000) reconnectLive(0); }, 30000);
// 分頁被切走、關閉或放進背景時，一定要放掉串流，不然會一直佔著瀏覽器的連線名額
window.addEventListener('pagehide', () => setLive(false));

/* =========================================================
   需要登入的頁面
   ========================================================= */
function lockedView(el, reload) {
  el.innerHTML = `<div class="locked">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>
    紀錄、成員和設定含有家人的影像與資料，<br>輸入 PIN 後才能查看。
    <button class="btn lock">輸入 PIN</button></div>`;
  el.querySelector('button').onclick = () => askPin({ title: '輸入 PIN', submit: (pin) => Api.login(pin), done: reload });
}

async function guarded(el, fn) {
  try { await fn(); } catch (e) {
    if (e instanceof AuthError) lockedView(el, () => loaders[page] && loaders[page]());
    else el.innerHTML = '<div class="empty">連不到門鎖，請確認 Pi 有開機、手機和 Pi 在同一個網路</div>';
  }
}

/* =========================================================
   2. 成員
   ========================================================= */
function ago(t) {
  if (!t) return '還沒出現過';
  const s = Date.now() / 1000 - t;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分鐘前出現`;
  if (dayLabel(t) === '今天') return `今天 ${hm(t)} 出現`;
  if (dayLabel(t) === '昨天') return `昨天 ${hm(t)} 出現`;
  return `${Math.floor(s / 86400)} 天前出現`;
}
function avatar(p, cls = '') {
  const src = Api.photo(p.name);
  const img = src && p.photos ? `<img src="${src}" alt="" loading="lazy" onerror="this.remove()">` : '';
  return `<span class="avatar ${p.role} ${cls}">${esc(p.name[0].toUpperCase())}${img}</span>`;
}
let people = [];
function renderPeople() {
  return guarded($('peopleList'), async () => {
    people = await Api.users();
    const group = (role, title, note) => {
      const ps = people.filter((p) => p.role === role);
      if (!ps.length) return '';
      return `<div class="group-title"><span>${title}</span><span>${ps.length} 人</span></div>
        <div class="people">${ps.map((p, i) => `
          <button class="person" onclick="personSheet(${people.indexOf(p)})">
            ${avatar(p)}
            <span class="meta"><span class="name">${esc(p.name)}</span><br><span class="sub">${ago(p.last_seen)}</span></span>
            <span class="role ${role}">${role === 'master' ? '主人' : '訪客'}</span>
          </button>`).join('')}</div>
        <div class="hint-box" style="margin-top:6px">${note}</div>`;
    };
    $('peopleList').innerHTML = (people.length ? group('master', '主人', '人臉＋手勢通過就會自動開門') +
      group('guest', '訪客', '只會通知你有人在門口，不會自動開門') : '<div class="empty">還沒有成員</div>') +
      `<div class="hint-box">新增成員要在 Pi 上執行 <code>python app.py capture 名字</code>，再 <code>python app.py train</code>，然後重新啟動門鎖。</div>`;
  });
}
const ROLE_NAME = { master: '主人', guest: '訪客' };
function personSheet(i) {
  const p = people[i];
  const to = p.role === 'master' ? 'guest' : 'master';
  openSheet(`
    <div class="detail-head">${avatar(p)}
      <div><h3 style="margin:0">${esc(p.name)}</h3><span class="role ${p.role}">${ROLE_NAME[p.role]}</span></div></div>
    <div class="sec" style="padding:0 14px">
      <div class="row"><span>開門方式</span><span class="v">${p.role === 'master' ? '人臉＋手勢自動開門' : '只通知，不自動開門'}</span></div>
      <div class="row"><span>訓練照片</span><span class="v">${p.photos} 張</span></div>
      <div class="row"><span>最近出現</span><span class="v">${ago(p.last_seen)}</span></div>
    </div>
    <button class="btn ${to === 'guest' ? 'ghost' : 'lock'}" style="width:100%" onclick="changeRole(${i}, '${to}')">改成${ROLE_NAME[to]}</button>
    <div class="infobox">${to === 'guest' ? '改成訪客後，他到門口只會通知你，不會再自動開門。' : '改成主人後，他用人臉＋手勢就能自動開門。'}
      每次變更都要輸入 PIN，立即生效。</div>
    <button class="btn danger" style="width:100%;margin-top:10px" onclick="confirmDelete(${i})">刪除這位成員</button>`);
}
// 刪除前先顯示警告（不用 confirm()：LINE 內建瀏覽器可能擋掉），確認後再輸入 PIN
function confirmDelete(i) {
  const p = people[i];
  const last = people.length === 1;
  const lastMaster = p.role === 'master' && people.filter((x) => x.role === 'master').length === 1;
  openSheet(`<h3>刪除 ${esc(p.name)}？</h3>
    <div class="warnbox"><b>此操作無法復原。</b><br>
      會永久刪除他的 ${p.photos} 張訓練照片，並重新訓練人臉模型；之後他到門口會被當成陌生人。
      要再加回來，只能在 Pi 上重新拍照（capture）和訓練（train）。</div>
    ${lastMaster && !last ? '<div class="warnbox">他是最後一位主人，刪除後沒有人能自動開門，只能用手機開。</div>' : ''}
    ${last ? '<div class="infobox">他是唯一的成員，不能刪除（門鎖至少要有一位成員才能啟動）。</div>' : ''}
    <div class="acts-2">
      <button class="btn ghost" onclick="personSheet(${i})">取消</button>
      <button class="btn danger" ${last ? 'disabled' : ''} onclick="deleteMember(${i})">我了解，刪除</button>
    </div>`);
}
function deleteMember(i) {
  const p = people[i];
  askPin({
    title: `輸入 PIN 以刪除 ${p.name}`,
    note: '刪除後無法復原',
    submit: (pin) => Api.deleteUser(p.name, pin).catch((e) => ({ ok: false, message: e.message })),
    done: (r) => { toast(r.message); renderPeople(); },
  });
}
function changeRole(i, role) {
  const p = people[i];
  const lastMaster = role === 'guest' && people.filter((x) => x.role === 'master').length === 1;
  askPin({
    title: `輸入 PIN 把 ${p.name} 改成${ROLE_NAME[role]}`,
    note: lastMaster ? '他是最後一位主人，改完之後沒有人能自動開門' : '',
    submit: (pin) => Api.setRole(p.name, role, pin).catch((e) => ({ ok: false, message: e.message })),
    done: (r) => { toast(r.message); renderPeople(); },
  });
}

/* =========================================================
   3. 紀錄：依日期分組的時間軸
   ========================================================= */
const FILTERS = [['全部', null], ['開鎖', ['unlock', 'lock', 'door_failed']], ['訪客', ['guest_detected']],
  ['錄影', ['sentinel_saved']], ['異常', ['door_failed', 'pin_rejected', 'gesture_timeout']]];
const HIDE_IN_ALL = ['face_passed', 'stranger_detected', 'gesture_cancel', 'info'];   // 重複或太細的事件
const KIND_CLASS = { unlock: 'unlock', lock: 'lock', guest_detected: 'guest', sentinel_saved: 'sentinel',
  door_failed: 'alert', pin_rejected: 'alert', gesture_timeout: 'alert', pin_changed: 'lock', role_changed: 'lock', member_deleted: 'alert' };
let filter = 0, events = [], lastEventId = null;

function dayLabel(t) {
  const d = new Date(t * 1000), today = new Date();
  const diff = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  return diff === 0 ? '今天' : diff === 1 ? '昨天' : `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}
function renderChips() {
  $('chips').innerHTML = FILTERS.map(([label], i) =>
    `<button class="chip" aria-pressed="${i === filter}" onclick="filter=${i};renderTimeline(true)">${label}</button>`).join('');
}
function clipCard(c) {
  if (c.deleted) return '<div class="gone">錄影已依保存規則自動刪除</div>';
  const dur = Math.round(c.duration || 0);
  const thumb = c.snapshot && Api.snapshot(c.id) ? `background-image:url(${Api.snapshot(c.id)})`
    : `background-image:linear-gradient(135deg,hsl(${(c.id * 47) % 360} 22% 38%),hsl(${(c.id * 47 + 40) % 360} 18% 22%))`;
  return `<button class="clip" onclick="playClip(${c.id},${dur})">
    <div class="thumb" style="${thumb}"><div class="play"><span>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="var(--ink)"><path d="M8 5v14l11-7z"/></svg></span></div>
      <span class="dur">${Math.floor(dur / 60)}:${pad(dur % 60)}</span></div>
    <div class="cap"><span>錄影 #${c.id}${c.status === 'failed' ? '（不完整）' : ''}</span><span>點一下播放</span></div></button>`;
}
// 連續、相同內容、10 分鐘內的事件合併成一筆（例如主人站在門口一直沒比手勢）
function collapse(list) {
  const out = [];
  for (const e of list) {
    const prev = out[out.length - 1];
    if (prev && !e.clip && prev.kind === e.kind && prev.text === e.text && prev.first - e.time < 600) {
      prev.count += 1; prev.first = e.time;
    } else out.push({ ...e, count: 1, first: e.time });
  }
  return out;
}
function drawTimeline(hasMore) {
  const items = collapse(filter === 0 ? events.filter((e) => !HIDE_IN_ALL.includes(e.kind)) : events);
  if (!items.length) { $('timeline').innerHTML = '<div class="empty">這個分類目前沒有紀錄</div>'; return; }
  let html = '', curDay = '';
  for (const e of items) {
    const d = dayLabel(e.time);
    if (d !== curDay) { if (curDay) html += '</div>'; html += `<div class="day">${d}</div><div class="tl">`; curDay = d; }
    html += `<div class="ev ${KIND_CLASS[e.kind] || ''}"><time>${hm(e.time)}</time><span class="node"></span>
      <div class="txt">${esc(e.text)}${e.count > 1 ? `<span class="rep" title="${hm(e.first)} 起">×${e.count}</span>` : ''}${e.clip ? clipCard(e.clip) : ''}</div></div>`;
  }
  html += '</div>' + (hasMore ? '<button class="more" onclick="renderTimeline(false)">載入更早的紀錄</button>' : '');
  $('timeline').innerHTML = html;
}
function renderTimeline(reset) {
  renderChips();
  return guarded($('timeline'), async () => {
    if (reset) { events = []; lastEventId = null; }
    const scroll = $('timeline').scrollTop;
    const batch = await Api.events(FILTERS[filter][1], lastEventId);
    events = events.concat(batch);
    if (batch.length) lastEventId = batch[batch.length - 1].id;
    drawTimeline(!DEMO && batch.length >= 60);
    if (!reset) $('timeline').scrollTop = scroll;
  });
}
function playClip(id, dur) {
  openSheet(`<h3>錄影 #${id}</h3>
    <div class="player">
      ${DEMO ? `<div style="aspect-ratio:4/3;border-radius:12px;background:#1d262c;color:#9aa8ad;display:grid;place-items:center;font-size:14px">示範模式沒有實際影片（${dur} 秒）</div>`
        : `<img id="clipImg" src="${Api.play(id)}" alt="錄影回放">`}
      <div class="acts">
        <button class="btn ghost" onclick="replay(${id},1)">重播</button>
        <button class="btn ghost" onclick="replay(${id},2)">2 倍速</button>
        <button class="btn danger" onclick="removeClip(${id})">刪除</button>
      </div>
      <a class="btn ghost" style="margin-top:8px" href="${Api.download(id)}" download>下載影片（AVI）</a>
    </div>`);
}
function replay(id, speed) { const img = $('clipImg'); if (img) img.src = Api.play(id, speed); }
async function removeClip(id) {
  if (!confirm(`確定刪除錄影 #${id}？刪除後無法復原。`)) return;
  try {
    const r = await Api.deleteRecord(id);
    closeSheet(); toast(r.message || (r.ok ? '已刪除' : '刪除失敗'));
    renderTimeline(true);
  } catch (e) { toast('需要重新輸入 PIN'); }
}

/* =========================================================
   4. 設定
   ========================================================= */
function meter(used, limit) {
  const p = Math.min(100, (used / limit) * 100);
  return `<div class="meter"><i class="${p > 90 ? 'bad' : p > 70 ? 'warn' : ''}" style="width:${p.toFixed(1)}%"></i></div>`;
}
let settingsData = null;
function lineSection(l) {
  const qr = l.basic_id ? `<button class="row link" onclick="lineQrSheet()">
      <span>加入通知好友<small>用 LINE 掃描就會收到門口通知</small></span><span class="v">顯示 QR</span></button>` : '';
  if (!l.configured) {
    return `<div class="sec"><h3>LINE 通知</h3>
      <div class="infobox">還沒設定。在 Pi 的 <code>smartlock.env</code> 加上 <code>LINE_CHANNEL_TOKEN=</code>
      ${l.basic_id ? '' : '和 <code>LINE_BASIC_ID=@你的帳號</code>'}，再重新啟動門鎖。</div>${qr}</div>`;
  }
  if (l.error) return `<div class="sec"><h3>LINE 通知</h3><div class="warnbox">${esc(l.error)}</div>${qr}</div>`;
  const limited = l.limit != null, n = l.notify;
  const notifyRow = !n ? '<div class="row"><span>自動通知</span><span class="v">關閉（LINE_NOTIFY=0）</span></div>'
    : `<div class="row note"><span>最近一則通知<small>${n.last_text ? esc(n.last_text.replace('【智慧門鎖】', '')) : '啟動後還沒發過'}</small></span>
        <span class="v">${n.last_sent || '—'}</span></div>`;
  return `<div class="sec"><h3>LINE 通知</h3>
    ${n && n.last_error ? `<div class="warnbox">上一則通知沒送出：${esc(n.last_error)}</div>` : ''}
    <div class="meter-block">
      <div class="meter-head"><span class="big">${limited ? l.left : '不限'} <small>則可用</small></span>
        <span class="meter-note">${limited ? `本月已用 ${l.used} / ${l.limit}` : `本月已用 ${l.used}`}</span></div>
      ${limited ? meter(l.used, l.limit) : ''}
      <div class="meter-note">${l.reset} 重置。${l.target === 'broadcast' ? '目前發給所有好友，每位好友各算一則。' : '目前只發給指定的一個人。'}
        額度剩 20 則以下只發訪客和開鎖失敗。</div>
    </div>${notifyRow}${qr}</div>`;
}
function renderSettings(refresh = false) {
  return guarded($('settings'), async () => {
    const s = settingsData = await Api.settings(refresh);
    const st = s.storage, sy = s.system, sec = s.security;
    const pinState = !sec.pin_required ? '<span class="v bad">未設定</span>'
      : `<span class="v ok">已設定</span>`;
    $('settings').innerHTML = `
      ${!sec.pin_required ? '<div class="warnbox">沒有設定 PIN：同一個網路的任何人都能從這個網頁開門。請在下方「安全」設定 PIN。</div>' : ''}
      ${lineSection(s.line)}
      <div class="sec"><h3>錄影儲存空間</h3>
        ${st ? `<div class="meter-block">
          <div class="meter-head"><span class="big">${gb(st.used_mb)} <small>/ ${gb(st.limit_mb)}</small></span><span class="meter-note">${st.count} 段</span></div>
          ${meter(st.used_mb, st.limit_mb)}
          <div class="meter-note">保留 ${st.keep_days} 天，超過上限自動刪最舊的。SD 卡還剩 ${gb(st.disk_free_mb)}。</div></div>`
          : '<div class="infobox">哨兵錄影沒有啟用（SENTINEL=0）</div>'}
      </div>
      ${s.sentinel.enabled ? `<div class="sec"><h3>哨兵錄影</h3>
        <div class="row"><span>陌生人停留多久開始錄</span><span class="v">${s.sentinel.trigger} 秒</span></div>
        <div class="row"><span>離開多久停止</span><span class="v">${s.sentinel.stop} 秒</span></div>
        <div class="row"><span>單段最長</span><span class="v">${s.sentinel.max} 秒</span></div>
        <div class="row"><span>兩次通知間隔</span><span class="v">${Math.round(s.sentinel.cooldown / 60)} 分鐘</span></div></div>` : ''}
      <div class="sec"><h3>系統</h3>
        <div class="row"><span>Arduino</span><span class="v ${sy.connected ? 'ok' : 'bad'}">${esc(sy.arduino)}</span></div>
        <div class="row"><span>相機</span><span class="v">${esc(sy.camera || '—')}</span></div>
        <div class="row"><span>手勢辨識</span><span class="v">${esc(sy.gesture || '—')}</span></div>
        <div class="row"><span>開門條件</span><span class="v">${esc(sy.rule)}</span></div>
        <div class="row"><span>開鎖後自動上鎖</span><span class="v">${sy.unlock_seconds} 秒</span></div>
        <div class="row"><span>版本</span><span class="v">${esc(sy.version)}</span></div></div>
      <div class="sec"><h3>安全</h3>
        <div class="row"><span>網頁 PIN</span>${pinState}</div>
        <button class="row link" onclick="changePinFlow(${sec.pin_required})">
          <span>${sec.pin_required ? '變更 PIN' : '設定 PIN'}<small>${sec.pin_required ? '需要先輸入目前的 PIN；改完其他手機要重新輸入' : '設定後開鎖和看紀錄都要輸入'}</small></span>
          <span class="v">›</span></button>
        ${sec.pin_required ? `<button class="row link" onclick="logout()"><span>登出這支手機<small>下次開鎖或看紀錄要重新輸入 PIN</small></span><span class="v">›</span></button>` : ''}
      </div>`;
  });
}

function qrSvg(text) {
  if (!window.qrcode) return '<div class="off">QR 元件沒有載入，請改用下方按鈕</div>';
  const qr = qrcode(0, 'M');
  qr.addData(text); qr.make();
  const n = qr.getModuleCount(), q = 2;
  let d = '';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + q} ${r + q}h1v1h-1z`;
  return `<svg viewBox="0 0 ${n + 2 * q} ${n + 2 * q}" shape-rendering="crispEdges"><path d="${d}" fill="#25323C"/></svg>`;
}
function lineQrSheet() {
  const l = settingsData.line, id = l.basic_id;
  const url = `https://line.me/R/ti/p/${encodeURIComponent(id)}`;
  openSheet(`<h3 style="text-align:center">加入門鎖通知</h3>
    <div class="qr" role="img" aria-label="LINE 加好友 QR code">${qrSvg(url)}</div>
    <div class="line-id">${esc(id)}</div>
    <div class="line-note">用 LINE 掃描加好友，就會收到<b>訪客到訪、陌生人錄影、開鎖失敗</b>的通知。
      ${l.target === 'broadcast' ? '每多一位好友，每則通知就多用一則額度，只給家人或組員掃。' : ''}</div>
    <div class="two">
      <a class="btn line" href="${url}" target="_blank" rel="noopener">在這支手機加入</a>
      <button class="btn ghost" onclick="copyLink('${url}')">複製連結</button>
    </div>`);
}
async function copyLink(url) {
  try { await navigator.clipboard.writeText(url); toast('已複製加好友連結'); }
  catch (e) { prompt('複製這個連結', url); }
}

// 變更 PIN：目前 PIN → 新 PIN → 再輸入一次
function changePinFlow(hasPin) {
  const askNew = (old) => askPin({ title: '輸入新的 PIN', note: '4 到 8 位數字', submit: async (pin) => ({ ok: true, pin }),
    done: (r1) => askPin({ title: '再輸入一次新的 PIN', submit: async (pin) => {
      if (pin !== r1.pin) return { ok: false, status: 400, message: '兩次輸入不一樣，請重新設定' };
      return Api.changePin(old, pin);
    }, done: (r) => { toast(r.message); renderSettings(); } }) });
  if (!hasPin) { askNew(''); return; }
  askPin({ title: '輸入目前的 PIN', submit: async (pin) => {
    const r = await Api.login(pin);                       // 先驗證，錯了就停在這一步
    return r.ok ? { ok: true, pin } : r;
  }, done: (r) => askNew(r.pin) });
}
async function logout() {
  await Api.logout();
  toast('這支手機已登出');
  renderSettings();
}

/* =========================================================
   底部抽屜、PIN 鍵盤、提示
   ========================================================= */
function openSheet(html) {
  $('sheetBody').innerHTML = html;
  $('sheet').classList.add('show'); $('scrim').classList.add('show');
}
function closeSheet() {
  pinFlow = null;
  $('sheet').classList.remove('show'); $('scrim').classList.remove('show');
  setTimeout(() => { if (!$('sheet').classList.contains('show')) $('sheetBody').innerHTML = ''; }, 320);  // 清掉才會停止回放串流
}
$('scrim').addEventListener('click', closeSheet);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

// PIN 鍵盤：4–8 位，滿 8 位自動送出，否則按「確定」
let pinBuf = '', pinFlow = null;
function askPin(flow) {
  pinBuf = ''; pinFlow = flow;
  openSheet(`<h3 style="text-align:center">${esc(flow.title)}</h3>
    <div class="pin-dots" id="dots"></div>
    <div class="pin-msg" id="pinMsg">${esc(flow.message || '')}</div>
    <div class="keypad">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button class="key" onclick="pinKey('${n}')">${n}</button>`).join('')}
      <button class="key fn" onclick="pinKey('del')">刪除</button><button class="key" onclick="pinKey('0')">0</button>
      <button class="key ok" id="pinOk" onclick="pinSubmit()" disabled>確定</button></div>
    <div class="pin-msg" style="color:var(--ink-2)">${esc(flow.note || '')}${DEMO ? '　示範 PIN：1234' : ''}</div>`);
  drawDots();
}
function drawDots() {
  const n = Math.max(4, pinBuf.length);
  $('dots').innerHTML = Array.from({ length: n }, (_, i) => `<i class="${i < pinBuf.length ? 'on' : ''}"></i>`).join('');
  $('pinOk').disabled = pinBuf.length < 4;
}
function pinKey(k) {
  if (!pinFlow) return;
  pinBuf = k === 'del' ? pinBuf.slice(0, -1) : (pinBuf + k).slice(0, 8);
  setText('pinMsg', '');
  drawDots();
  if (pinBuf.length === 8) pinSubmit();
}
async function pinSubmit() {
  if (!pinFlow || pinBuf.length < 4) return;
  const flow = pinFlow, pin = pinBuf;
  $('pinOk').disabled = true;
  let r;
  try { r = await flow.submit(pin); } catch (e) { r = { ok: false, message: '連不到門鎖' }; }
  if (pinFlow !== flow) return;                         // 使用者已經關掉
  if (!r.ok) {
    pinBuf = ''; drawDots();
    $('dots').classList.remove('shake'); void $('dots').offsetWidth; $('dots').classList.add('shake');
    setText('pinMsg', r.message || 'PIN 錯誤');
    if (navigator.vibrate) navigator.vibrate([40, 40, 40]);
    return;
  }
  pinFlow = null;
  closeSheet();
  setTimeout(() => flow.done && flow.done(r), 340);     // 等抽屜收起來再開下一個
}
document.addEventListener('keydown', (e) => {
  if (!pinFlow) return;
  if (/^[0-9]$/.test(e.key)) pinKey(e.key);
  else if (e.key === 'Backspace') pinKey('del');
  else if (e.key === 'Enter') pinSubmit();
});

let toastTimer;
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
}

/* =========================================================
   啟動與輪詢
   ========================================================= */
async function poll() {
  try {
    const d = await Api.status();
    last.status = d;
    renderDoor(d);
  } catch (e) {
    $('conn').classList.add('off'); setText('connText', '連不到門鎖');
  }
}

(async () => {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    DEMO = !r.ok;
  } catch (e) { DEMO = true; }
  if (DEMO) Api = Demo;
  $('demoTag').hidden = !DEMO;
  $('demoCam').hidden = !DEMO;
  $('live').hidden = DEMO;
  const start = Math.max(0, PAGES.indexOf(location.hash.slice(1)));
  fitDoor();
  go(start, false);
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 1000);
  document.addEventListener('visibilitychange', () => setLive(!document.hidden && page === 0));
})();
