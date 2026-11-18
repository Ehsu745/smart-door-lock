"""對講機：找音效卡、語音檔、聽眾佇列、播放、網頁 API。不需要音效卡（假麥克風 / 假喇叭）。"""
import threading
import wave

import numpy as np
import pytest

from smartlock.config import Config
from smartlock.events import EventBus
from smartlock.intercom import (CHUNK_BYTES, LISTEN_EVENT_GAP, TALK_IDLE, Intercom, card_index, clip_label,
                                find_card, make_intercom, read_wav, set_speaker_volume, to_stereo)

from conftest import FakeClock, Harness

ARECORD_L = """**** List of CAPTURE Hardware Devices ****
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
card 2: Device [USB PnP Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""


def write_wav(path, seconds=0.2, rate=16000, channels=1, width=2):
    n = int(seconds * rate)
    data = (np.sin(np.arange(n * channels) / 5) * 8000).astype('<i2')
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data.tobytes() if width == 2 else bytes(n * channels))


class FakeMic:
    opened = 0

    def __init__(self):
        self.stop = threading.Event()

    def open(self):
        FakeMic.opened += 1

    def read(self):
        self.stop.wait(0.01)
        return b'\x01\x00' * (CHUNK_BYTES // 2)

    def close(self):
        pass


class FakeStream:
    def __init__(self, rate):
        self.rate, self.data, self.closed = rate, bytearray(), False

    def write(self, pcm):
        self.data += pcm

    def close(self):
        self.closed = True


class FakeSpeaker:
    def __init__(self):
        self.played = []
        self.streams = []
        self.release = threading.Event()

    def play(self, pcm, rate):
        self.played.append((pcm, rate))
        self.release.wait(2)

    def stream(self, rate):
        self.streams.append(FakeStream(rate))
        return self.streams[-1]


def join_talk():
    for t in threading.enumerate():
        if t.name == 'intercom-talk':
            t.join(3)


@pytest.fixture
def ic(tmp_path):
    sounds = tmp_path / 'sounds'
    sounds.mkdir()
    write_wav(sounds / '01 請稍候.wav')
    write_wav(sounds / '02 包裹請放門口.wav', channels=2)
    events = []
    bus = EventBus()
    bus.subscribe('*', events.append)
    clock = FakeClock()
    intercom = Intercom(sounds, bus, FakeMic, FakeSpeaker(), clock=clock)
    intercom.events, intercom.test_clock = events, clock
    yield intercom
    intercom.speaker.release.set()


# ==========================================
# 找音效卡、語音檔
# ==========================================
def test_find_usb_card():
    assert find_card(ARECORD_L) == 'plughw:2,0'
    assert find_card('card 0: vc4hdmi0 [vc4-hdmi-0], device 0: x [y]') is None
    assert find_card('') is None


def test_prefers_pnp_card():
    text = ('card 1: Webcam [USB Camera Audio], device 0: USB Audio [USB Audio]\n'
            'card 3: Device [USB PnP Audio Device], device 0: USB Audio [USB Audio]\n')
    assert find_card(text) == 'plughw:3,0'


def test_clip_label_strips_order_prefix(tmp_path):
    assert clip_label(tmp_path / '01 請稍候，馬上來.wav') == '請稍候，馬上來'
    assert clip_label(tmp_path / '2_包裹.wav') == '包裹'
    assert clip_label(tmp_path / '包裹.wav') == '包裹'


def test_read_wav_stereo_to_mono(tmp_path):
    write_wav(tmp_path / 'a.wav', channels=2, rate=22050)
    mono, rate = read_wav(tmp_path / 'a.wav')
    assert rate == 22050 and mono.dtype == np.dtype('<i2') and len(mono) == int(0.2 * 22050)


def test_read_wav_rejects_8bit(tmp_path):
    write_wav(tmp_path / 'a.wav', width=1)
    with pytest.raises(ValueError):
        read_wav(tmp_path / 'a.wav')


def test_to_stereo_routes_channel():
    mono = np.array([100, -200], '<i2')
    left = np.frombuffer(to_stereo(mono, 'left'), '<i2').reshape(-1, 2)
    assert left[:, 0].tolist() == [100, -200] and left[:, 1].tolist() == [0, 0]
    right = np.frombuffer(to_stereo(mono, 'right'), '<i2').reshape(-1, 2)
    assert right[:, 0].tolist() == [0, 0] and right[:, 1].tolist() == [100, -200]
    both = np.frombuffer(to_stereo(mono, 'both'), '<i2').reshape(-1, 2)
    assert both[:, 0].tolist() == both[:, 1].tolist() == [100, -200]


def test_clips_sorted_with_labels(ic):
    assert ic.clips() == [{'id': '01 請稍候', 'label': '請稍候'}, {'id': '02 包裹請放門口', 'label': '包裹請放門口'}]


def test_no_sounds_dir(tmp_path):
    assert Intercom(tmp_path / 'nope').clips() == []


def test_make_intercom_disabled_and_sim(tmp_path):
    assert make_intercom(Config(base=tmp_path, intercom='0')) is None
    sim = make_intercom(Config(base=tmp_path, intercom='sim'))
    assert sim.can_listen and sim.can_play


def test_make_intercom_without_card(tmp_path, monkeypatch):
    import smartlock.intercom as m
    monkeypatch.setattr(m, 'detect_devices', lambda: (None, None))
    assert make_intercom(Config(base=tmp_path)) is None


# ==========================================
# 喇叭音量（這張卡預設 62% 幾乎沒聲音）
# ==========================================
def test_card_index():
    assert card_index('plughw:2,0') == '2'
    assert card_index('hw:3,0') == '3'
    assert card_index('plughw:CARD=Device,DEV=0') == 'Device'
    assert card_index('default') is None
    assert card_index('') is None


class _FakeAmixer:
    def __init__(self, ok_controls=('Speaker',)):
        self.ok, self.calls = ok_controls, []

    def __call__(self, args, **kw):
        import subprocess
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0 if args[5] in self.ok else 1, '', '')


@pytest.fixture
def amixer(monkeypatch):
    import smartlock.intercom as m
    fake = _FakeAmixer()
    monkeypatch.setattr(m.shutil, 'which', lambda tool: f'/usr/bin/{tool}')
    monkeypatch.setattr(m.subprocess, 'run', fake)
    return fake


def test_set_speaker_volume(amixer):
    assert set_speaker_volume('plughw:2,0', 100)
    assert amixer.calls == [['amixer', '-q', '-c', '2', 'sset', 'Speaker', '100%', 'unmute']]


def test_set_speaker_volume_falls_back_to_pcm(amixer):
    amixer.ok = ('PCM',)
    assert set_speaker_volume('plughw:2,0', 80)
    assert [c[5] for c in amixer.calls] == ['Speaker', 'PCM']
    amixer.ok = ()
    assert not set_speaker_volume('plughw:2,0')


def test_set_speaker_volume_skips_default_device(amixer):
    assert not set_speaker_volume('default')
    assert amixer.calls == []


def test_make_intercom_sets_volume(tmp_path, monkeypatch, amixer):
    import smartlock.intercom as m
    monkeypatch.setattr(m, 'detect_devices', lambda: ('plughw:2,0', 'plughw:2,0'))
    assert make_intercom(Config(base=tmp_path, speaker_volume=90)) is not None
    assert amixer.calls == [['amixer', '-q', '-c', '2', 'sset', 'Speaker', '90%', 'unmute']]


# ==========================================
# 聽門口
# ==========================================
def test_listener_gets_audio_and_mic_stops_when_alone(ic):
    q = ic.subscribe('1.2.3.4')
    assert len(q.get(timeout=2)) == CHUNK_BYTES
    assert ic.status()['listeners'] == 1
    ic.unsubscribe(q)
    thread = ic._mic_thread
    if thread is not None:
        thread.join(2)
    assert ic._mic_thread is None and ic.status()['listeners'] == 0


def test_two_listeners_share_one_mic(ic):
    FakeMic.opened = 0
    a, b = ic.subscribe('a'), ic.subscribe('b')
    assert a.get(timeout=2) and b.get(timeout=2)
    assert FakeMic.opened == 1
    ic.unsubscribe(a)
    ic.unsubscribe(b)


def test_listen_event_not_repeated_on_reconnect(ic):
    q = ic.subscribe('1.2.3.4')
    ic.unsubscribe(q)
    q = ic.subscribe('1.2.3.4')                 # 10 分鐘換一條串流 = 重連，不再記一次
    ic.unsubscribe(q)
    assert [e.kind for e in ic.events] == ['intercom_listen']
    ic.test_clock.t += LISTEN_EVENT_GAP
    ic.unsubscribe(ic.subscribe('1.2.3.4'))
    assert [e.kind for e in ic.events] == ['intercom_listen'] * 2


def test_slow_listener_drops_oldest(ic):
    from smartlock.intercom import LISTENER_QUEUE, _offer
    from queue import Queue
    q = Queue(LISTENER_QUEUE)
    for i in range(LISTENER_QUEUE + 5):
        _offer(q, i)
    assert q.qsize() == LISTENER_QUEUE and q.get() == 5


# ==========================================
# 播放
# ==========================================
def test_play_left_channel_and_event(ic):
    ok, msg = ic.play('01 請稍候', '1.2.3.4')
    assert ok and '請稍候' in msg and ic.status()['playing'] == '請稍候'
    ic.speaker.release.set()
    for t in threading.enumerate():
        if t.name == 'intercom-play':
            t.join(2)
    pcm, rate = ic.speaker.played[0]
    stereo = np.frombuffer(pcm, '<i2').reshape(-1, 2)
    assert rate == 16000 and np.abs(stereo[:, 0]).max() > 0 and not stereo[:, 1].any()
    assert ic.status()['playing'] is None
    assert ic.events[-1].kind == 'intercom_reply' and '請稍候' in ic.events[-1].text


def test_play_busy(ic):
    assert ic.play('01 請稍候')[0]
    ok, msg = ic.play('02 包裹請放門口')
    assert not ok and '正在播放' in msg


@pytest.mark.parametrize('bad', ['../pin', 'nope', '.hidden', 'a/b'])
def test_play_rejects_unknown_or_path(ic, bad):
    assert ic.play(bad) == (False, '找不到這段語音')


def test_no_speaker(tmp_path):
    assert Intercom(tmp_path, mic_factory=FakeMic).play('x') == (False, '沒有喇叭')


# ==========================================
# 按住說話
# ==========================================
PCM = (np.arange(1600) % 200 * 50).astype('<i2').tobytes()      # 0.1 秒


def test_talk_plays_on_left_channel_and_logs_seconds(ic):
    ok, _, tid = ic.talk_start('1.2.3.4')
    assert ok and ic.status()['talking']
    for _ in range(10):
        assert ic.talk_feed(tid, PCM) == (True, '')
    assert ic.talk_end(tid)
    join_talk()
    stream = ic.speaker.streams[0]
    stereo = np.frombuffer(bytes(stream.data), '<i2').reshape(-1, 2)
    assert stream.rate == 16000 and stream.closed and len(stereo) == 16000
    assert stereo[:, 0].any() and not stereo[:, 1].any()
    assert not ic.status()['talking']
    assert ic.events[-1].kind == 'intercom_talk' and '1 秒' in ic.events[-1].text


def test_talk_feed_after_end_is_rejected(ic):
    _, _, tid = ic.talk_start('a')
    ic.talk_end(tid)
    join_talk()
    assert ic.talk_feed(tid, PCM)[0] is False
    assert ic.talk_feed('wrong', PCM)[0] is False
    assert [e.kind for e in ic.events] == []                     # 沒有聲音就不記事件


def test_one_talker_at_a_time_and_no_clip_while_talking(ic):
    _, _, tid = ic.talk_start('a')
    ok, msg, _ = ic.talk_start('b')
    assert not ok and '另一支手機' in msg
    ok, msg = ic.play('01 請稍候')
    assert not ok and '說話' in msg
    ic.talk_end(tid)
    join_talk()
    ok, _, tid = ic.talk_start('b')
    assert ok
    ic.talk_end(tid)
    join_talk()


def test_same_phone_can_restart_talk(ic):
    _, _, old = ic.talk_start('a')
    ok, _, new = ic.talk_start('a')                               # 上一次的「放開」沒送到
    assert ok and new != old
    assert ic.talk_feed(old, PCM)[0] is False and ic.talk_feed(new, PCM)[0]
    ic.talk_end(new)
    join_talk()


def test_no_talk_while_clip_playing(ic):
    assert ic.play('01 請稍候')[0]
    ok, msg, tid = ic.talk_start('a')
    assert not ok and '正在播放' in msg and tid is None


def test_talk_ends_when_phone_goes_quiet(ic):
    _, _, tid = ic.talk_start('a')
    ic.talk_feed(tid, PCM)
    ic.test_clock.t += TALK_IDLE + 0.1
    join_talk()
    assert not ic.talking and ic.speaker.streams[0].closed
    assert ic.talk_feed(tid, PCM)[0] is False


def test_talk_max_seconds(ic):
    ic.talk_max = 10
    _, _, tid = ic.talk_start('a')
    ic.test_clock.t += 11
    ok, msg = ic.talk_feed(tid, PCM)
    assert not ok and '10 秒' in msg
    join_talk()


def test_listeners_hear_nothing_while_talking(ic):
    from queue import Empty
    q = ic.subscribe('a')
    assert q.get(timeout=2)
    _, _, tid = ic.talk_start('b')
    threading.Event().wait(0.05)
    while not q.empty():
        q.get_nowait()
    with pytest.raises(Empty):
        q.get(timeout=0.2)
    ic.talk_end(tid)
    join_talk()
    assert q.get(timeout=2)
    ic.unsubscribe(q)


def test_speaker_without_stream_cannot_talk(tmp_path):
    class PlayOnly:
        def play(self, pcm, rate):
            pass
    ic = Intercom(tmp_path, speaker=PlayOnly())
    assert ic.can_play and not ic.can_talk
    assert ic.talk_start('a')[0] is False


def test_sim_stream_plays_after_release():
    from smartlock.intercom import SimStream
    played = []

    class Spk:
        def play(self, pcm, rate):
            played.append((len(pcm), rate))
    s = SimStream(Spk(), 16000)
    s.write(b'\0' * 400)
    s.write(b'\0' * 400)
    assert played == []
    s.close()
    assert played == [(800, 16000)]


# ==========================================
# 網頁 API
# ==========================================
def web(ic, pin=''):
    pytest.importorskip('flask')
    from smartlock.web import create_web
    h = Harness(Config(control_pin=pin))
    ic.bus = h.system.bus
    return h, create_web(h.system, intercom=ic).test_client()


def test_status_shows_intercom_without_listener_count(ic):
    _, c = web(ic)
    d = c.get('/api/status').get_json()['intercom']
    assert d == {'listen': True, 'reply': True, 'talk': True, 'talking': False, 'playing': None}


def test_status_without_intercom():
    pytest.importorskip('flask')
    from smartlock.web import create_web
    c = create_web(Harness().system).test_client()
    assert 'intercom' not in c.get('/api/status').get_json()
    assert c.get('/api/intercom').status_code == 404


def test_reply_needs_login_but_listen_does_not(ic):
    h, c = web(ic, '4321')
    assert c.get('/api/intercom').status_code == 401
    r = c.get('/audio_feed', buffered=False)                # 聆聽跟影像一樣不用 PIN
    assert r.status_code == 200
    r.close()
    assert c.post('/api/intercom/play', json={'clip': '01 請稍候'}).status_code == 401
    assert c.post('/api/intercom/play', json={'clip': '01 請稍候', 'pin': '0000'}).status_code == 403
    assert ic.speaker.played == [] and 'pin_rejected' in h.kinds()


def test_intercom_info_and_play_with_pin(ic):
    h, c = web(ic, '4321')
    r = c.post('/api/intercom/play', json={'clip': '01 請稍候', 'pin': '4321'})
    assert r.status_code == 200 and '請稍候' in r.get_json()['message']
    assert 'intercom_reply' in h.kinds()
    d = c.get('/api/intercom').get_json()                  # 剛剛附 PIN 已登入
    assert [x['label'] for x in d['clips']] == ['請稍候', '包裹請放門口'] and d['playing'] == '請稍候'
    assert c.post('/api/intercom/play', json={'clip': '02 包裹請放門口'}).status_code == 409


def test_audio_feed_streams_pcm(ic):
    h, c = web(ic)
    r = c.get('/audio_feed', buffered=False)
    assert r.status_code == 200 and r.headers['X-Sample-Rate'] == '16000'
    first = next(r.response)
    assert len(first) == CHUNK_BYTES
    r.close()
    assert ic.status()['listeners'] == 0
    assert 'intercom_listen' in h.kinds()


def test_settings_shows_intercom(ic):
    _, c = web(ic)
    assert c.get('/api/settings').get_json()['intercom'] == {'device': '', 'channel': 'left', 'clips': 2}


def test_talk_api_needs_login_then_streams(ic):
    h, c = web(ic, '4321')
    assert c.post('/api/intercom/talk', json={}).status_code == 401
    assert c.post('/api/intercom/talk', json={'pin': '0000'}).status_code == 403
    r = c.post('/api/intercom/talk', json={'pin': '4321'})
    d = r.get_json()
    assert r.status_code == 200 and d['rate'] == 16000 and d['max_seconds'] == 120
    tid = d['id']
    r = c.post(f'/api/intercom/talk/{tid}', data=PCM, content_type='application/octet-stream')
    assert r.status_code == 200
    assert c.get('/api/status').get_json()['intercom']['talking'] is True
    assert c.post(f'/api/intercom/talk/{tid}/end').status_code == 200
    join_talk()
    assert c.post(f'/api/intercom/talk/{tid}', data=PCM).status_code == 410   # 前端看到 410 就停止送
    assert 'intercom_talk' in h.kinds()


def test_talk_api_busy_and_too_large(ic):
    _, c = web(ic)
    tid = c.post('/api/intercom/talk', json={}).get_json()['id']
    other = web(ic)[1]
    other.environ_base['REMOTE_ADDR'] = '10.0.0.9'
    r = other.post('/api/intercom/talk', json={})
    assert r.status_code == 409 and '另一支手機' in r.get_json()['message']
    from smartlock.intercom import TALK_MAX_BYTES
    assert c.post(f'/api/intercom/talk/{tid}', data=b'\0' * (TALK_MAX_BYTES + 2)).status_code == 413
    c.post(f'/api/intercom/talk/{tid}/end')
    join_talk()


def test_talk_feed_needs_login(ic):
    _, c = web(ic, '4321')
    _, _, tid = ic.talk_start('127.0.0.1')
    assert c.post(f'/api/intercom/talk/{tid}', data=PCM).status_code == 401
    ic.talk_end(tid)
    join_talk()
