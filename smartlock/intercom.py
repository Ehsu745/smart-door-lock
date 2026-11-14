"""對講機（階段 1、2）：手機聽門口聲音、按鈕播放預錄語音。

硬體：Waveshare USB TO AUDIO（Pi 上顯示為「USB PnP Audio Device」，內建 2 顆麥克風）＋兩顆喇叭，
左聲道接門外、右聲道接室內。用系統的 arecord / aplay（alsa-utils，Pi OS 內建），不需要額外的 Python 套件。

- 收音：有人在聽才開麥克風（arecord），最後一個人離開就關掉；每個聽眾一個佇列，慢的聽眾丟最舊的資料
- 播放：sounds/*.wav（16-bit PCM），檔名就是按鈕文字（開頭的「01 」之類的編號會拿掉、用來排序）；
  預設只從左聲道（門外）播；同一時間只播一段
- 模擬（INTERCOM=sim，Mac 開發用）：麥克風送輕微的測試音，播放只寫 log（Mac 有 afplay 就真的播）
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from queue import Empty, Full, Queue

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000                       # 收音取樣率；手機端用同一個值建立 AudioBuffer
CHUNK = RATE // 10                 # 每 0.1 秒一塊（mono S16LE = 3200 bytes）
CHUNK_BYTES = CHUNK * 2
LISTENER_QUEUE = 30                # 每個聽眾最多累積 3 秒，再慢就丟最舊的（延遲不會越來越大）
LISTEN_EVENT_GAP = 900.0           # 同一支手機 15 分鐘內重新連線（串流 10 分鐘會換一條）不重複記「開始聆聽」
MAX_CLIP_SECONDS = 30.0
CHANNELS = ('left', 'right', 'both')
_LABEL_PREFIX = re.compile(r'^\d+[\s._-]*')


# ==========================================
# 找音效卡
# ==========================================
def find_card(listing: str):
    """從 `arecord -l` / `aplay -l` 的輸出找 USB 音效卡，回傳 'plughw:卡號,裝置號'；找不到回傳 None。
    plughw 會自動轉換取樣率與聲道數（卡是立體聲，我們收單聲道）。"""
    best = None
    for line in listing.splitlines():
        m = re.match(r'card (\d+): .*?\[(.*?)\], device (\d+):', line)
        if not m:
            continue
        name = m.group(2)
        if 'USB' in name.upper():
            dev = f'plughw:{m.group(1)},{m.group(3)}'
            if 'PNP' in name.upper():
                return dev                      # Waveshare 那張卡
            best = best or dev
    return best


def detect_devices():
    """(收音裝置, 播放裝置)；沒有 alsa-utils 或找不到 USB 卡時是 None。"""
    out = []
    for tool in ('arecord', 'aplay'):
        if not shutil.which(tool):
            out.append(None)
            continue
        try:
            text = subprocess.run([tool, '-l'], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            text = ''
        out.append(find_card(text))
    return tuple(out)


# ==========================================
# 語音檔
# ==========================================
def clip_label(path: Path) -> str:
    return _LABEL_PREFIX.sub('', path.stem).strip() or path.stem


def read_wav(path: Path):
    """讀 16-bit PCM WAV，回傳 (單聲道 int16 陣列, 取樣率)。格式不對就丟 ValueError。"""
    with wave.open(str(path), 'rb') as w:
        if w.getsampwidth() != 2:
            raise ValueError('只支援 16-bit PCM WAV')
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        if n > rate * MAX_CLIP_SECONDS:
            raise ValueError(f'超過 {MAX_CLIP_SECONDS:.0f} 秒')
        data = np.frombuffer(w.readframes(n), '<i2')
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype('<i2')
    return data, rate


def to_stereo(mono: np.ndarray, channel: str) -> bytes:
    """單聲道 → 立體聲交錯（L R L R…），只放到指定的聲道；另一邊靜音。"""
    left = mono if channel in ('left', 'both') else np.zeros_like(mono)
    right = mono if channel in ('right', 'both') else np.zeros_like(mono)
    return np.column_stack([left, right]).astype('<i2').tobytes()


# ==========================================
# 收音 / 播放後端
# ==========================================
class AlsaMic:
    """arecord 單聲道 16 kHz raw PCM。"""

    def __init__(self, device):
        self.device = device
        self.proc = None

    def open(self):
        self.proc = subprocess.Popen(['arecord', '-q', '-D', self.device, '-f', 'S16_LE', '-r', str(RATE), '-c', '1',
                                      '-t', 'raw'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self) -> bytes:
        return self.proc.stdout.read(CHUNK_BYTES) if self.proc else b''

    def close(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


class AlsaSpeaker:
    def __init__(self, device):
        self.device = device

    def play(self, pcm: bytes, rate: int):
        subprocess.run(['aplay', '-q', '-D', self.device, '-f', 'S16_LE', '-r', str(rate), '-c', '2', '-t', 'raw'],
                       input=pcm, stderr=subprocess.DEVNULL, timeout=MAX_CLIP_SECONDS + 10)


class SimMic:
    """模擬麥克風：輕微雜訊，每 2 秒一聲短嗶，確認手機端有在播。照實際時間送出。"""

    def __init__(self):
        self.t = 0
        self.rng = np.random.default_rng()

    def open(self):
        self.next = time.monotonic()

    def read(self) -> bytes:
        self.next += CHUNK / RATE
        time.sleep(max(0.0, self.next - time.monotonic()))
        n = np.arange(self.t, self.t + CHUNK)
        self.t += CHUNK
        x = self.rng.normal(0, 150, CHUNK)
        beep = (n % (RATE * 2)) < RATE // 5
        x += beep * 3000 * np.sin(2 * np.pi * 660 * n / RATE)
        return np.clip(x, -32768, 32767).astype('<i2').tobytes()

    def close(self):
        pass


class SimSpeaker:
    """Mac：有 afplay 就真的播（不分聲道），沒有就只等同樣的時間。"""

    def play(self, pcm: bytes, rate: int):
        stereo = np.frombuffer(pcm, '<i2').reshape(-1, 2)
        if shutil.which('afplay'):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.wav') as f:
                with wave.open(f.name, 'wb') as w:
                    w.setnchannels(2)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(stereo.tobytes())
                subprocess.run(['afplay', f.name], timeout=MAX_CLIP_SECONDS + 10)
        else:
            time.sleep(len(stereo) / rate)


# ==========================================
# 對講機
# ==========================================
class Intercom:
    def __init__(self, sounds_dir: Path, bus=None, mic_factory=None, speaker=None, channel='left',
                 clock=time.monotonic, name=''):
        self.sounds_dir = Path(sounds_dir)
        self.bus = bus
        self.mic_factory = mic_factory          # None = 沒有麥克風（只能播放或完全停用）
        self.speaker = speaker
        self.channel = channel if channel in CHANNELS else 'left'
        self.clock = clock
        self.name = name                        # 裝置名稱，顯示在設定頁
        self._listeners = []
        self._lock = threading.Lock()
        self._mic_thread = None
        self._mic_busy = threading.Lock()       # 同一時間只開一個 arecord（音效卡是獨占的）
        self._last_listen = {}                  # ip → 上次記「開始聆聽」的時間
        self.playing = None                     # 正在播的語音（文字）

    @property
    def can_listen(self) -> bool:
        return self.mic_factory is not None

    @property
    def can_play(self) -> bool:
        return self.speaker is not None

    def status(self) -> dict:
        with self._lock:
            listeners = len(self._listeners)
        return {'listen': self.can_listen, 'reply': self.can_play, 'playing': self.playing, 'listeners': listeners}

    def _emit(self, kind, text, **data):
        if self.bus is not None:
            from .events import Event
            self.bus.publish(Event(kind, text, data))

    # ---------- 聽門口 ----------
    def subscribe(self, ip=''):
        """登記一個聽眾，回傳佇列（每塊 CHUNK_BYTES bytes；None = 麥克風停了）。用完一定要 unsubscribe。"""
        if not self.can_listen:
            raise RuntimeError('沒有麥克風')
        q = Queue(LISTENER_QUEUE)
        with self._lock:
            self._listeners.append(q)
            if self._mic_thread is None or not self._mic_thread.is_alive():
                self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True, name='intercom-mic')
                self._mic_thread.start()
        now = self.clock()
        if now - self._last_listen.get(ip, -LISTEN_EVENT_GAP) >= LISTEN_EVENT_GAP:
            self._emit('intercom_listen', f'手機開始聆聽門口（{ip}）', remote=ip)
        self._last_listen[ip] = now
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _mic_loop(self):
        mic = self.mic_factory()
        self._mic_busy.acquire()                # 上一個收音執行緒還在關 arecord 時先等它
        try:
            mic.open()
            log.info('對講機：開始收音')
            while True:
                with self._lock:
                    listeners = list(self._listeners)
                    if not listeners:
                        self._mic_thread = None
                        break
                data = mic.read()
                if not data:
                    log.warning('對講機：麥克風沒有資料（音效卡被拔掉或被占用？）')
                    break
                for q in listeners:
                    _offer(q, data)
        except Exception:
            log.exception('對講機收音錯誤')
        finally:
            mic.close()
            self._mic_busy.release()
            with self._lock:
                if self._mic_thread is threading.current_thread():
                    self._mic_thread = None
                for q in self._listeners:          # 異常結束：讓聽眾知道（前端會重連）
                    _offer(q, None)
            log.info('對講機：停止收音')

    # ---------- 預錄回覆 ----------
    def clips(self):
        if not self.sounds_dir.is_dir():
            return []
        return [{'id': p.stem, 'label': clip_label(p)} for p in sorted(self.sounds_dir.glob('*.wav'))]

    def play(self, clip_id: str, ip=''):
        """在背景播放；回傳 (成功, 訊息)。"""
        if not self.can_play:
            return False, '沒有喇叭'
        path = self.sounds_dir / f'{clip_id}.wav'
        if '/' in clip_id or '\\' in clip_id or clip_id.startswith('.') or not path.is_file():
            return False, '找不到這段語音'
        try:
            mono, rate = read_wav(path)
        except (ValueError, wave.Error, EOFError) as e:
            return False, f'語音檔格式不對：{e}'
        label = clip_label(path)
        with self._lock:
            if self.playing:
                return False, f'正在播放「{self.playing}」'
            self.playing = label
        self._emit('intercom_reply', f'門口播放：{label}（{ip}）', remote=ip, clip=clip_id)
        threading.Thread(target=self._play, args=(to_stereo(mono, self.channel), rate),
                         daemon=True, name='intercom-play').start()
        return True, f'正在門口播放「{label}」'

    def _play(self, pcm, rate):
        try:
            self.speaker.play(pcm, rate)
        except Exception:
            log.exception('對講機播放錯誤')
        finally:
            with self._lock:
                self.playing = None


def _offer(q, item):
    """放進佇列；滿了就丟最舊的一塊。"""
    while True:
        try:
            q.put_nowait(item)
            return
        except Full:
            try:
                q.get_nowait()
            except Empty:
                pass


def make_intercom(cfg, bus=None):
    """依設定建立對講機；沒有音效卡（或 INTERCOM=0）時回傳 None，網頁就不顯示對講按鈕。"""
    mode = cfg.intercom
    if mode == '0':
        return None
    sounds = cfg.sounds_dir
    if mode == 'sim':
        return Intercom(sounds, bus, SimMic, SimSpeaker(), cfg.intercom_channel, name='模擬（測試音）')
    if cfg.audio_device:
        rec = play = cfg.audio_device
    else:
        rec, play = detect_devices()
    if not rec and not play:
        if mode == '1':
            log.warning('對講機：找不到 USB 音效卡（arecord -l 看不到），先停用')
        return None
    ic = Intercom(sounds, bus, (lambda: AlsaMic(rec)) if rec else None, AlsaSpeaker(play) if play else None,
                  cfg.intercom_channel, name=rec or play)
    log.info('對講機：收音 %s、播放 %s（%s聲道）；語音 %d 段（%s）', rec or '無', play or '無',
             {'left': '左', 'right': '右', 'both': '雙'}[ic.channel], len(ic.clips()), sounds)
    return ic
