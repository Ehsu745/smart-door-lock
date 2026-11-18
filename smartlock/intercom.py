"""對講機：手機聽門口聲音、按住說話（門外喇叭播出）、按鈕播放預錄語音。

硬體：Waveshare USB TO AUDIO（Pi 上顯示為「USB PnP Audio Device」，內建 2 顆麥克風）＋兩顆喇叭，
左聲道接門外、右聲道接室內。用系統的 arecord / aplay（alsa-utils，Pi OS 內建），不需要額外的 Python 套件。

- 收音：有人在聽才開麥克風（arecord），最後一個人離開就關掉；每個聽眾一個佇列，慢的聽眾丟最舊的資料
- 播放：sounds/*.wav（16-bit PCM），檔名就是按鈕文字（開頭的「01 」之類的編號會拿掉、用來排序）；
  預設只從左聲道（門外）播；同一時間只播一段
- 按住說話（半雙工）：手機麥克風 → 16 kHz 單聲道 PCM 分段 POST 上來 → aplay 串流播出；
  說話時門口麥克風不送給聽眾（不會聽到自己的回音）；同一時間只有一支手機能說話，也不能和預錄語音同時播
- 模擬（INTERCOM=sim，Mac 開發用）：麥克風送輕微的測試音，播放只寫 log（Mac 有 afplay 就真的播）
"""
from __future__ import annotations

import logging
import re
import secrets
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
TALK_IDLE = 2.0                    # 說話中超過 2 秒沒收到聲音（手機斷線、切到背景）就自動結束
TALK_MAX_BYTES = RATE * 2 * 5      # 單次上傳最多 5 秒的聲音
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


def card_index(device: str):
    """'plughw:2,0' / 'hw:2,0' → '2'；'default' 之類沒有卡號的回傳 None。"""
    m = re.match(r'(?:plug)?hw:(?:CARD=)?([^,]+)', device or '')
    return m.group(1) if m else None


def set_speaker_volume(device: str, percent: int = 100) -> bool:
    """把 USB 卡的喇叭音量設成 percent%（並取消靜音），成功回傳 True。

    這張卡（0c76:1203）回報的音量刻度不對（核心訊息「cval->res is probably wrong」），
    插上去預設 62% 實際幾乎沒聲音，所以每次啟動都直接設到 100%；音量大小改用語音檔本身控制。"""
    card = card_index(device)
    if card is None or not shutil.which('amixer'):
        return False
    for control in ('Speaker', 'PCM'):            # Waveshare 這張是 Speaker；其他 USB 卡常見 PCM
        try:
            r = subprocess.run(['amixer', '-q', '-c', card, 'sset', control, f'{percent}%', 'unmute'],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode == 0:
            return True
    log.warning('對講機：無法調整 %s 的喇叭音量（amixer -c %s scontrols 看控制項名稱）', device, card)
    return False


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

    def stream(self, rate: int):
        return AlsaStream(self.device, rate)


class AlsaStream:
    """按住說話：開一個 aplay，聲音一段一段寫進 stdin。aplay 自己有 0.4 秒緩衝，網路抖動時斷音會自動接回。"""

    def __init__(self, device, rate):
        self.proc = subprocess.Popen(['aplay', '-q', '-D', device, '-f', 'S16_LE', '-r', str(rate), '-c', '2',
                                      '-t', 'raw', '--buffer-time=400000'],
                                     stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def write(self, pcm: bytes):
        self.proc.stdin.write(pcm)

    def close(self):
        try:
            self.proc.stdin.close()                 # EOF：aplay 把剩下的播完才結束
            self.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


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

    def stream(self, rate: int):
        return SimStream(self, rate)


class SimStream:
    """Mac 模擬：說完（放開按鈕）才一次播出來。"""

    def __init__(self, speaker, rate):
        self.speaker, self.rate, self.buf = speaker, rate, bytearray()

    def write(self, pcm: bytes):
        self.buf += pcm

    def close(self):
        if self.buf:
            self.speaker.play(bytes(self.buf), self.rate)


class TalkSession:
    def __init__(self, ip, now):
        self.id = secrets.token_urlsafe(12)
        self.ip = ip
        self.started = self.last = now
        self.queue = Queue()
        self.bytes = 0
        self.ended = False


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
        self.talk_max = 120.0                   # 按住說話單次最長秒數
        self._talk = None                       # 正在說話的手機（TalkSession）
        self._speaker_busy = threading.Lock()   # 音效卡是獨占的：上一次說話的 aplay 還在播完尾巴時，下一次要等

    @property
    def can_listen(self) -> bool:
        return self.mic_factory is not None

    @property
    def can_play(self) -> bool:
        return self.speaker is not None

    @property
    def can_talk(self) -> bool:
        return self.speaker is not None and hasattr(self.speaker, 'stream')

    @property
    def talking(self) -> bool:
        return self._talk is not None

    def status(self) -> dict:
        with self._lock:
            listeners = len(self._listeners)
        return {'listen': self.can_listen, 'reply': self.can_play, 'talk': self.can_talk, 'talking': self.talking,
                'playing': self.playing, 'listeners': listeners}

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
                if self._talk is not None:         # 半雙工：手機說話時不送門口的聲音（不然會聽到自己的回音）
                    continue
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
            if self._talk is not None:
                return False, '有人正在對門口說話'
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


    # ---------- 按住說話 ----------
    def talk_start(self, ip=''):
        """開始說話，回傳 (成功, 訊息, session id)。"""
        if not self.can_talk:
            return False, '沒有喇叭', None
        with self._lock:
            if self.playing:
                return False, f'正在播放「{self.playing}」', None
            if self._talk is not None:
                if self._talk.ip != ip:
                    return False, '另一支手機正在說話', None
                self._talk.queue.put(None)         # 同一支手機重按（上一次沒收到放開）：結束舊的
                self._talk.ended = True
                self._talk = None
            t = self._talk = TalkSession(ip, self.clock())
        threading.Thread(target=self._talk_loop, args=(t,), daemon=True, name='intercom-talk').start()
        return True, '說話中', t.id

    def talk_feed(self, tid, pcm: bytes):
        """收到一段手機的聲音（16 kHz 單聲道 S16LE）。回傳 (成功, 訊息)；失敗代表這次說話已結束。"""
        with self._lock:
            t = self._talk
            if t is None or t.id != tid or t.ended:
                return False, '說話已結束'
            now = self.clock()
            if now - t.started > self.talk_max:
                t.ended = True
                t.queue.put(None)
                return False, f'單次說話最長 {self.talk_max:.0f} 秒'
            t.last = now
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if pcm:
            t.bytes += len(pcm)
            t.queue.put(pcm)
        return True, ''

    def talk_end(self, tid):
        with self._lock:
            t = self._talk
            if t is None or t.id != tid:
                return False
            t.ended = True
        t.queue.put(None)
        return True

    def _talk_loop(self, t):
        stream = None
        self._speaker_busy.acquire()
        try:
            stream = self.speaker.stream(RATE)
            while True:
                try:
                    pcm = t.queue.get(timeout=0.5)
                except Empty:
                    if self.clock() - t.last > TALK_IDLE:
                        log.info('對講機：%.0f 秒沒收到聲音，結束說話', TALK_IDLE)
                        break
                    continue
                if pcm is None:
                    break
                stream.write(to_stereo(np.frombuffer(pcm, '<i2'), self.channel))
        except Exception:
            log.exception('對講機說話播放錯誤')
        finally:
            t.ended = True
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    log.exception('對講機：關閉喇叭串流錯誤')
            self._speaker_busy.release()
            with self._lock:
                if self._talk is t:
                    self._talk = None
            secs = t.bytes / 2 / RATE
            if secs > 0:
                self._emit('intercom_talk', f'手機對門口說話 {secs:.0f} 秒（{t.ip}）', remote=t.ip,
                           seconds=round(secs, 1))


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
        ic = Intercom(sounds, bus, SimMic, SimSpeaker(), cfg.intercom_channel, name='模擬（測試音）')
        ic.talk_max = cfg.talk_max_seconds
        return ic
    if cfg.audio_device:
        rec = play = cfg.audio_device
    else:
        rec, play = detect_devices()
    if not rec and not play:
        if mode == '1':
            log.warning('對講機：找不到 USB 音效卡（arecord -l 看不到），先停用')
        return None
    if play:
        set_speaker_volume(play, cfg.speaker_volume)
    ic = Intercom(sounds, bus, (lambda: AlsaMic(rec)) if rec else None, AlsaSpeaker(play) if play else None,
                  cfg.intercom_channel, name=rec or play)
    ic.talk_max = cfg.talk_max_seconds
    log.info('對講機：收音 %s、播放 %s（%s聲道）；語音 %d 段（%s）', rec or '無', play or '無',
             {'left': '左', 'right': '右', 'both': '雙'}[ic.channel], len(ic.clips()), sounds)
    return ic
