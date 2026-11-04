"""LINE 通知：全部用假的 opener，不會真的打 LINE API。"""
import io
import json
import urllib.error

from smartlock.events import Event, EventBus
from smartlock.line import LineClient, LineError, LineNotifier, format_event


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeLine:
    """模擬 LINE API：記錄送出的訊息，額度可調，可以指定下一次回應的錯誤。"""

    def __init__(self, limit=200, used=0):
        self.limit, self.used = limit, used
        self.sent = []          # (path, body, retry_key)
        self.fail = []          # 依序要丟出的錯誤（HTTP 狀態碼或 'net'）

    def __call__(self, req, timeout):
        assert req.headers['Authorization'] == 'Bearer T'
        url = req.full_url
        if req.get_method() == 'GET':
            body = {'type': 'limited', 'value': self.limit} if url.endswith('/quota') else {'totalUsage': self.used}
            return FakeResp(json.dumps(body).encode())
        if self.fail:
            err = self.fail.pop(0)
            if err == 'net':
                raise urllib.error.URLError('no route')
            raise urllib.error.HTTPError(url, err, 'err', {}, io.BytesIO(b'{"message":"boom"}'))
        self.sent.append((url.rsplit('/', 1)[1], json.loads(req.data.decode()), req.headers['X-line-retry-key']))
        self.used += 1
        return FakeResp(b'{}')


def make(limit=200, used=0, user_id='', cooldown=300.0):
    api = FakeLine(limit, used)
    t = {'now': 0.0}
    client = LineClient('T', opener=api, clock=lambda: t['now'])
    bus = EventBus()
    n = LineNotifier(client, bus, user_id=user_id, cooldown=cooldown, clock=lambda: t['now'], start=False)
    return api, n, bus, t


def drain(n):
    while not n.queue.empty():
        n.process(*n.queue.get_nowait())


def test_format_messages_have_no_unlock_link():
    texts = [format_event('guest_detected', {'name': 'amy'}, '15:02:11'),
             format_event('door_failed', {'command': 'UNLOCK', 'message': '沒有回應'}, '15:02:11'),
             format_event('stranger_detected', {'record_id': 7, 'notify': True}, '15:02:11')]
    assert texts[0] == '【智慧門鎖】15:02 訪客 amy 在門口。要開門請到手機控制頁操作。'
    assert '開鎖指令失敗：沒有回應' in texts[1]
    assert '#7' in texts[2]
    assert all('http' not in t for t in texts)
    assert format_event('stranger_detected', {'record_id': 8, 'notify': False}, '15:02:11') is None   # 哨兵冷卻中
    assert format_event('unlock', {}, '15:02:11') is None


def test_broadcast_by_default_push_with_user_id():
    api, n, bus, _ = make()
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    drain(n)
    path, body, _ = api.sent[0]
    assert path == 'broadcast' and 'to' not in body and body['messages'][0]['type'] == 'text'
    api, n, bus, _ = make(user_id='U123')
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    drain(n)
    path, body, _ = api.sent[0]
    assert path == 'push' and body['to'] == 'U123'
    assert n.client.quota()['used'] == 1                 # push：本地直接加用量


def test_only_subscribed_events_and_stranger_respects_sentinel_cooldown():
    api, n, bus, _ = make()
    for kind in ('unlock', 'lock', 'face_passed', 'pin_rejected'):
        bus.publish(Event(kind, 'x', {}))
    bus.publish(Event('stranger_detected', 'x', {'record_id': 1, 'notify': False}))
    assert n.queue.empty()
    bus.publish(Event('stranger_detected', 'x', {'record_id': 2, 'notify': True}))
    drain(n)
    assert len(api.sent) == 1


def test_guest_cooldown_per_name():
    api, n, bus, t = make(cooldown=300)
    for _ in range(3):
        bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    bus.publish(Event('guest_detected', 'x', {'name': 'kevin'}))      # 不同人不受影響
    drain(n)
    assert len(api.sent) == 2
    t['now'] = 301
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    drain(n)
    assert len(api.sent) == 3


def test_low_quota_only_important():
    api, n, bus, _ = make(limit=200, used=185)          # 剩 15 則
    bus.publish(Event('stranger_detected', 'x', {'record_id': 1, 'notify': True}))
    bus.publish(Event('door_failed', 'x', {'command': 'UNLOCK', 'message': 'timeout'}))
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    drain(n)
    kinds = [b['messages'][0]['text'] for _, b, _ in api.sent]
    assert len(kinds) == 2 and not any('陌生人' in k for k in kinds)


def test_quota_exhausted_sends_nothing():
    api, n, bus, _ = make(limit=200, used=200)
    bus.publish(Event('door_failed', 'x', {'command': 'UNLOCK', 'message': 'timeout'}))
    drain(n)
    assert api.sent == [] and '額度' in n.status()['last_error']


def test_retry_once_with_same_retry_key():
    api, n, _, _ = make()
    api.fail = [500]
    n.client.send('hi')
    assert len(api.sent) == 1
    api.fail = ['net', 'net']
    try:
        n.client.send('hi')
        assert False, '應該丟 LineError'
    except LineError as exc:
        assert '連不到 LINE' in str(exc)


def test_retry_key_reused_between_attempts():
    keys = []

    def opener(req, timeout):
        keys.append(req.headers['X-line-retry-key'])
        if len(keys) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, 'x', {}, io.BytesIO(b''))
        return FakeResp(b'{}')
    LineClient('T', opener=opener).send('hi')
    assert len(keys) == 2 and keys[0] == keys[1]


def test_failures_are_recorded_not_raised():
    api, n, bus, _ = make()
    api.fail = [401]
    bus.publish(Event('door_failed', 'x', {'command': 'LOCK', 'message': 'timeout'}))
    drain(n)                                            # 不會丟例外
    assert 'token 無效' in n.status()['last_error'] and n.status()['sent'] == 0
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    drain(n)
    assert n.status()['last_error'] is None and n.status()['sent'] == 1


def test_background_thread_sends_and_close_flushes():
    api = FakeLine()
    bus = EventBus()
    n = LineNotifier(LineClient('T', opener=api), bus)
    bus.publish(Event('guest_detected', 'x', {'name': 'amy'}))
    n.close()
    assert len(api.sent) == 1


def test_publish_never_blocks_when_queue_full():
    api, n, bus, _ = make(cooldown=0)
    for i in range(50):
        bus.publish(Event('guest_detected', 'x', {'name': f'p{i}'}))    # 背景執行緒沒在跑，佇列會滿
    assert n.queue.qsize() == 20
