# -*- coding: utf-8 -*-
"""抖音扫码登录：在网页上展示二维码，用户用抖音 App 扫码后获取登录态 cookie。

工作原理：
  1. GET  get_qrcode  -> 拿到 qrcode_index_url(二维码内容) 与 token
  2. 前端把 qrcode_index_url 渲染成二维码图片
  3. 前端轮询 check_qrconnect(token) -> new / scanned / expired / success
  4. success 后从跳转响应与会话中提取完整 cookie，持久化到本地文件

cookie 文件供 new_engine.py 在解析抖音时读取并注入 spider，从而解锁原画线路。

注意：抖音接口会做风控（数据中心 IP 常返回 4031）。本模块保留“手动粘贴 cookie”
作为兜底，且所有网络请求直连（trust_env=False）以避开本机代理导致的 502。
"""

import base64
import io
import json
import os
import time
import urllib.parse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
COOKIE_FILE = os.path.join(DATA_DIR, 'douyin_cookie.txt')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

# 尝试加载 DLR 自带的 a_bogus 签名实现（可选，提高通过率）
_ab_sign = None
try:
    import sys
    _DLR = os.path.normpath(os.path.join(HERE, '..', 'DLR'))
    if _DLR not in sys.path:
        sys.path.insert(0, _DLR)
    from src.ab_sign import ab_sign as _ab_sign  # noqa: E402
except Exception:
    _ab_sign = None


# ---------------- cookie 持久化 ----------------

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def get_cookie():
    """读取已保存的抖音 cookie 字符串；没有则返回空串。"""
    try:
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def save_cookie(cookie):
    """保存 cookie 字符串到本地文件。"""
    ensure_data_dir()
    cookie = (cookie or '').strip()
    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        f.write(cookie)
    return bool(cookie)


def clear_cookie():
    try:
        os.remove(COOKIE_FILE)
    except OSError:
        pass


def cookie_status():
    """返回 cookie 状态摘要（不泄露完整 cookie）。"""
    cookie = get_cookie()
    if not cookie:
        return {'has_cookie': False, 'length': 0, 'preview': ''}
    keys = [kv.split('=', 1)[0].strip() for kv in cookie.split(';') if '=' in kv]
    logged = any(k in keys for k in ('sessionid', 'sessionid_ss', 'sid_tt', 'passport_csrf_token'))
    return {
        'has_cookie': True,
        'length': len(cookie),
        'logged_in': logged,
        'preview': cookie[:40] + '...',
        'keys': keys[:12],
    }


# ---------------- 会话与请求 ----------------

def _new_session():
    """直连会话：避开本机代理对 douyin 的 502 干扰。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8',
        'Accept': 'application/json, text/plain, */*',
    })
    return s


def _prime_session(s):
    """访问直播间页面，拿到 ttwid 等风控基础 cookie。"""
    try:
        s.get('https://live.douyin.com/', timeout=12)
    except Exception:
        pass


# ---------------- 扫码登录流程 ----------------

QR_API = 'https://login.douyin.com/passport/web/get_qrcode/'
CHECK_API = 'https://login.douyin.com/passport/web/check_qrconnect'


def get_qrcode():
    """生成登录二维码。返回 {ok, qrcode_url, token, message}。"""
    s = _new_session()
    _prime_session(s)
    s.headers['Referer'] = 'https://www.douyin.com/'

    params = {'aid': '6383', 'next': 'https://www.douyin.com'}
    query = urllib.parse.urlencode(params)
    url = QR_API + '?' + query
    if _ab_sign:
        try:
            url += '&a_bogus=' + _ab_sign(query, UA)
        except Exception:
            pass

    try:
        r = s.get(url, timeout=20)
        js = r.json()
    except Exception as e:
        return {'ok': False, 'message': '请求二维码失败：%s' % e}

    data = js.get('data') or {}
    if js.get('message') == 'error' or data.get('error_code'):
        desc = data.get('description') or js.get('message') or '未知错误'
        code = data.get('error_code')
        return {'ok': False, 'message': '抖音风控拦截（%s）：%s' % (code, desc)}

    qrcode_url = data.get('qrcode_index_url') or ''
    token = data.get('token') or ''
    if not qrcode_url or not token:
        return {'ok': False, 'message': '二维码接口返回异常，请稍后重试'}

    # 把 token 与关键请求信息暂存到 session 缓存，供轮询复用
    _SESSION_CACHE[token] = {
        'session': s,
        'qrcode_url': qrcode_url,
        'created': time.time(),
    }
    return {'ok': True, 'qrcode_url': qrcode_url, 'token': token}


# token -> {session, qrcode_url, created}
_SESSION_CACHE = {}


def check_qrconnect(token):
    """轮询扫码状态。返回 {ok, status, message}。
    status: new(待扫码) / scanned(已扫码待确认) / success(成功) / expired(过期)
    """
    cached = _SESSION_CACHE.get(token)
    if not cached:
        return {'ok': False, 'status': 'expired', 'message': '二维码已失效，请重新获取'}
    s = cached['session']
    if time.time() - cached['created'] > 180:
        _SESSION_CACHE.pop(token, None)
        return {'ok': False, 'status': 'expired', 'message': '二维码已过期，请重新获取'}

    params = {'aid': '6383', 'next': 'https://www.douyin.com'}
    post_data = {
        'need_logo': 'false',
        'need_short_url': 'false',
        'is_frontier': 'true',
        'token': token,
        'is_new_login': '1',
        'next': 'https://www.douyin.com',
    }
    try:
        r = s.post(CHECK_API, params=params, data=post_data, timeout=20)
        js = r.json()
    except Exception as e:
        return {'ok': False, 'status': 'error', 'message': '查询扫码状态失败：%s' % e}

    data = js.get('data') or {}
    status = data.get('status') or ''
    message = data.get('description') or ''

    if status == 'new':
        return {'ok': True, 'status': 'new', 'message': '等待扫码'}
    if status == 'scanned':
        return {'ok': True, 'status': 'scanned', 'message': '已扫码，请在手机上确认登录'}
    if status == 'expired':
        _SESSION_CACHE.pop(token, None)
        return {'ok': True, 'status': 'expired', 'message': '二维码已过期'}
    if status in ('success', 'confirmed') or js.get('status_code') == 0 and data.get('redirect_url'):
        cookie = _finalize_login(s, data)
        if cookie:
            save_cookie(cookie)
            _SESSION_CACHE.pop(token, None)
            return {'ok': True, 'status': 'success', 'message': '登录成功，cookie 已保存', 'cookie': cookie}
        return {'ok': False, 'status': 'error', 'message': '登录成功但未能提取 cookie'}

    # 兼容：部分返回直接带 login cookie
    if data.get('cookie_string') or data.get('cookies'):
        cookie = data.get('cookie_string') or data.get('cookies')
        save_cookie(cookie)
        _SESSION_CACHE.pop(token, None)
        return {'ok': True, 'status': 'success', 'message': '登录成功，cookie 已保存', 'cookie': cookie}

    return {'ok': True, 'status': status or 'unknown', 'message': message or '等待扫码'}


def _finalize_login(session, data):
    """扫码成功后，访问 redirect_url 完成登录并提取完整 cookie。"""
    redirect = data.get('redirect_url') or data.get('url') or ''
    if redirect:
        try:
            if redirect.startswith('//'):
                redirect = 'https:' + redirect
            elif redirect.startswith('/'):
                redirect = 'https://www.douyin.com' + redirect
            session.get(redirect, timeout=20, allow_redirects=True)
        except Exception:
            pass
    return _dump_cookie(session)


def _dump_cookie(session):
    """把 session 的 cookie 导出成 'k=v; k=v' 字符串。"""
    try:
        return '; '.join('%s=%s' % (k, v) for k, v in session.cookies.get_dict().items())
    except Exception:
        return ''


def make_qrcode_png(qrcode_url):
    """把二维码内容渲染成 PNG 的 base64（供前端 <img> 直接显示）。"""
    try:
        import qrcode
        img = qrcode.make(qrcode_url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        return ''


if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'qr'
    if cmd == 'qr':
        print(json.dumps(get_qrcode(), ensure_ascii=False, indent=2))
    elif cmd == 'status':
        print(json.dumps(cookie_status(), ensure_ascii=False, indent=2))
    elif cmd == 'set' and len(sys.argv) > 2:
        save_cookie(sys.argv[2]); print('saved')
    elif cmd == 'clear':
        clear_cookie(); print('cleared')
