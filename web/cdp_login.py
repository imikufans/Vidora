# -*- coding: utf-8 -*-
"""网页版抖音登录：通过 Chrome DevTools Protocol 启动独立真实浏览器登录并读取 Cookie。

为什么这样做：
  - 软件内生成二维码 -> 抖音 4031 风控（数据中心/程序化特征）
  - 直接读用户浏览器数据库 -> Chrome v20 App-Bound 加密解不开 / Edge 文件被占用
  - CDP 方案：启动一个独立 profile 的真实浏览器（真实 TLS 指纹，不触发风控），
    用户在窗口里扫码登录，再通过调试协议读取「已解密」的 Cookie。
    不依赖任何第三方库（纯标准库实现 WebSocket 客户端）。
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

LOGIN_KEYS = ('sessionid', 'sessionid_ss', 'sid_tt')
PRIORITY = ('sessionid', 'sessionid_ss', 'sid_tt', 'ttwid', 'msToken',
            'passport_csrf_token', 'odin_tt', 'uid_tt', 'store-region', 'store-region-src')

# 运行时状态：当前由本模块启动的浏览器进程与 profile
_state = {'proc': None, 'profile': None, 'port': None, 'started': 0}


# ---------------- 浏览器定位 ----------------

def find_browser():
    """返回 (exe_path, name)；优先 Edge，其次 Chrome。"""
    local = os.environ.get('LOCALAPPDATA', '')
    pf = os.environ.get('ProgramFiles', r'C:\Program Files')
    pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    candidates = [
        (os.path.join(pf86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'), 'Edge'),
        (os.path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'), 'Edge'),
        (os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'), 'Chrome'),
        (os.path.join(pf86, 'Google', 'Chrome', 'Application', 'chrome.exe'), 'Chrome'),
        (os.path.join(local, 'Microsoft', 'Edge', 'Application', 'msedge.exe'), 'Edge'),
        (os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'), 'Chrome'),
    ]
    for path, name in candidates:
        if os.path.isfile(path):
            return path, name
    return None, None


# ---------------- 极简 WebSocket 客户端（标准库实现） ----------------

class _WS:
    """够用的 WebSocket 客户端：文本帧收发、自动处理 ping/pong、分块读取。"""

    def __init__(self, url, timeout=10):
        u = urllib.parse.urlparse(url)
        host = u.hostname or '127.0.0.1'
        port = u.port or 80
        path = u.path or '/'
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        req = (
            'GET %s HTTP/1.1\r\n'
            'Host: %s:%d\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            'Sec-WebSocket-Key: %s\r\n'
            'Sec-WebSocket-Version: 13\r\n\r\n'
        ) % (path, host, port, key)
        self.sock.sendall(req.encode('ascii'))
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError('WebSocket 握手失败')
            buf += chunk
        head, self.buf = buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise IOError('WebSocket 握手被拒绝: %s' % head[:120].decode('latin1', 'replace'))

    def _exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise IOError('WebSocket 连接已关闭')
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def send_text(self, text):
        payload = text.encode('utf-8')
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack('!H', n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack('!Q', n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + masked)

    def _send_pong(self, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x8A, 0x80 | n]) if n < 126 else bytes([0x8A, 0x80 | 126]) + struct.pack('!H', n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_text(self):
        while True:
            b1, b2 = self._exact(2)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._exact(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._exact(8))[0]
            mask = self._exact(4) if masked else None
            payload = self._exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self._send_pong(payload)
                continue
            if opcode == 0x8:  # close
                raise IOError('WebSocket 被对端关闭')
            if opcode in (0x1, 0x0):  # text / continuation
                return payload.decode('utf-8', 'replace')

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------- CDP 操作 ----------------

def _http_json(url, timeout=5):
    req = urllib.request.Request(url, headers={'Host': '127.0.0.1'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _cdp_get_cookies(ws_url, timeout=10):
    ws = _WS(ws_url, timeout=timeout)
    try:
        ws.send_text(json.dumps({'id': 1, 'method': 'Storage.getCookies'}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv_text())
            if msg.get('id') == 1:
                return msg.get('result', {}).get('cookies', [])
        return []
    finally:
        ws.close()


def _cleanup():
    proc = _state.get('proc')
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    profile = _state.get('profile')
    if profile:
        # 稍等浏览器完全退出再删目录
        time.sleep(1)
        shutil.rmtree(profile, ignore_errors=True)
    _state.update({'proc': None, 'profile': None, 'port': None, 'started': 0})


def start_login_browser():
    """启动独立真实浏览器并打开抖音首页。返回 {ok, message}。"""
    if _state.get('proc') and _state['proc'].poll() is None:
        return {'ok': True, 'message': '登录浏览器已打开，请在窗口中扫码登录', 'already': True}

    exe, name = find_browser()
    if not exe:
        return {'ok': False, 'message': '未找到 Chrome / Edge 浏览器，无法启动登录窗口'}

    profile = tempfile.mkdtemp(prefix='dy_login_')
    args = [
        exe,
        '--remote-debugging-port=0',
        '--user-data-dir=' + profile,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=Translate,OptimizationHints',
        '--new-window',
        'https://www.douyin.com/',
    ]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        shutil.rmtree(profile, ignore_errors=True)
        return {'ok': False, 'message': '启动浏览器失败：%s' % e}

    # 等待 DevToolsActivePort 文件出现（内含随机调试端口）
    port_file = os.path.join(profile, 'DevToolsActivePort')
    port = None
    for _ in range(60):  # 最多 ~12 秒
        if os.path.isfile(port_file):
            try:
                with open(port_file, 'r', encoding='utf-8') as f:
                    port = int(f.readline().strip())
                break
            except Exception:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    if not port:
        _state.update({'proc': proc, 'profile': profile})
        _cleanup()
        return {'ok': False, 'message': '浏览器调试端口启动失败，请重试'}

    _state.update({'proc': proc, 'profile': profile, 'port': port, 'started': time.time()})
    return {'ok': True, 'message': '已打开 %s 登录窗口，请用手机抖音 App 扫码登录' % name,
            'browser': name, 'port': port}


def _browser_ws_url():
    port = _state.get('port')
    if not port:
        return None
    try:
        ver = _http_json('http://127.0.0.1:%d/json/version' % port, timeout=5)
        url = ver.get('webSocketDebuggerUrl') or ''
        if not url:
            return None
        # Edge/Chrome 的 webSocketDebuggerUrl 有时省略端口（ws://127.0.0.1/devtools/...），需补全
        parsed = urllib.parse.urlparse(url)
        if parsed.port is None:
            netloc = '%s:%d' % (parsed.hostname or '127.0.0.1', port)
            url = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path,
                                           parsed.params, parsed.query, parsed.fragment))
        return url
    except Exception:
        return None


def build_cookie(cookies):
    """过滤 douyin.com cookie 并组装成请求头字符串。"""
    mp = {}
    for c in cookies:
        domain = c.get('domain', '')
        if 'douyin.com' in domain and c.get('name') and c.get('value') is not None:
            mp.setdefault(c['name'], c['value'])
    names = [k for k in PRIORITY if k in mp] + [k for k in mp if k not in PRIORITY]
    return '; '.join('%s=%s' % (k, mp[k]) for k in names), names


def read_login_cookie():
    """通过 CDP 读取登录态。返回 {ok, cookie, keys, message}。"""
    if not (_state.get('proc') and _state['proc'].poll() is None):
        return {'ok': False, 'message': '登录浏览器未启动，请先点击“打开抖音登录页”'}
    ws_url = _browser_ws_url()
    if not ws_url:
        return {'ok': False, 'message': '无法连接登录浏览器调试接口，请重新打开登录页'}
    try:
        cookies = _cdp_get_cookies(ws_url, timeout=10)
    except Exception as e:
        return {'ok': False, 'message': '读取 Cookie 失败：%s' % e}

    logged = any(c.get('name') in LOGIN_KEYS and c.get('value') for c in cookies
                 if 'douyin.com' in c.get('domain', ''))
    if not logged:
        return {'ok': False, 'status': 'waiting', 'message': '尚未检测到登录，请在浏览器窗口里扫码并确认登录'}

    cookie, keys = build_cookie(cookies)
    if not cookie:
        return {'ok': False, 'message': '检测到登录但未读取到 Cookie，请重试'}
    result = {'ok': True, 'cookie': cookie, 'keys': keys, 'length': len(cookie),
              'message': '登录成功，Cookie 已获取'}
    _cleanup()
    return result


def close_browser():
    _cleanup()
    return {'ok': True, 'message': '已关闭登录浏览器'}


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if cmd == 'start':
        print(json.dumps(start_login_browser(), ensure_ascii=False, indent=2))
    elif cmd == 'read':
        print(json.dumps(read_login_cookie(), ensure_ascii=False, indent=2))
    elif cmd == 'close':
        print(json.dumps(close_browser(), ensure_ascii=False, indent=2))
