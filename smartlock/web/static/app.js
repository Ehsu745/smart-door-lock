// 控制頁：每秒向 /api/status 取得狀態並重畫；按鈕呼叫 /api/action
const $ = (id) => document.getElementById(id);

try { const saved = localStorage.getItem('door_pin'); if (saved) $('pin').value = saved; } catch (e) {}

async function act(action) {
  const pin = $('pin').value;
  try { localStorage.setItem('door_pin', pin); } catch (e) {}
  try {
    const r = await fetch('/api/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, pin }),
    });
    const d = await r.json();
    flash(d.message);
  } catch (e) {
    flash('伺服器連線失敗');
  }
}

// 按鈕結果顯示 4 秒
function flash(message) {
  const s = $('s');
  s.dataset.msg = message;
  setTimeout(() => { delete s.dataset.msg; }, 4000);
  poll();
}

function visitorCard(d) {
  if (!d.visitor) return '';
  return `<div class="visitor"><b>訪客在門口：${d.visitor}</b>（${d.visitor_since}）<br>建議開鎖
    <br><button onclick="act('unlock')">開門讓訪客進來</button></div>`;
}

function gestureHint(d) {
  if (d.phase !== 'gesture') return '';
  return `<div class="hint">請比出手勢：${d.allowed.join(' 或 ')}（剩 ${d.countdown} 秒）
    <br>目前偵測：${d.gesture ?? '—'} ${d.gesture_score ?? ''}</div>`;
}

function sentinelLine(d) {
  const st = d.sentinel;
  if (!st) return '';
  const text = st.state === 'recording' ? `<span class="rec">● 錄影中 ${st.seconds} 秒</span>`
    : st.state === 'watching' ? `<span class="warn">偵測到陌生人 ${st.seconds} 秒</span>` : '監看中';
  return `<br>哨兵：${text}　<a class="link" href="/records">紀錄 ›</a>`;
}

async function poll() {
  try {
    const d = await (await fetch('/api/status')).json();
    const s = $('s');
    $('pinbox').style.display = d.pin_required ? 'block' : 'none';
    const rule = d.require_gesture ? `人臉 + 手勢（${d.allowed.join(' / ')}）` : '只需人臉';
    const lock = d.unlocked ? '<span class="ok">[開鎖中]</span>' : '[上鎖]';
    s.innerHTML = `${visitorCard(d)}${gestureHint(d)}
      辨識：<b>${d.name}</b> ${d.category} ${d.distance ?? ''}
      <br>開鎖條件：${rule}
      <br>Arduino：<span class="${d.connected ? 'ok' : 'bad'}">${d.arduino}</span>
      <br>門鎖：${lock}　自動解鎖：${d.auto ? '開' : '關'}${sentinelLine(d)}
      <br>${s.dataset.msg || d.message}`;
    $('ev').innerHTML = d.events.map((e) => `<li>${e}</li>`).join('');
  } catch (e) {}
}

setInterval(poll, 1000);
poll();
