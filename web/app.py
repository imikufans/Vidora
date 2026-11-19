# -*- coding: utf-8 -*-
"""直播源解析 Web 服务，基于 wbt5/real-url 的解析脚本。"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urljoin, urlparse, quote, unquote

import requests
from flask import Flask, jsonify, render_template, request, Response

from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None

from platforms import PLATFORMS, PLATFORM_MAP

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', 'real-url'))
RUNNER = os.path.join(HERE, 'runner.py')

app = Flask(__name__)
URL_RE = re.compile(r'https?://[^\s"\'<>]+')
TIMEOUT = 30


def extract_url(text):
    """从一段文本中提取第一个 http(s) 链接。"""
    match = URL_RE.search(str(text))
    if not match:
        return None
    return match.group(0).rstrip('.,;:!?，。；：！？)]}')


META_KEYS = {'@@anchor@@': 'anchor', '@@title@@': 'title'}


QUALITY_TAG_RE = re.compile(r'^@Q\[(\d+)\|([^\]]*)\]@(.*)$')


def flatten(result, prefix='', out=None, meta=None):
    """把脚本返回的 dict / list / str 归一化成 [{name, url, level?, quality?}]。"""
    if out is None:
        out = []
    if meta is None:
        meta = {}
    if isinstance(result, dict):
        for key, value in result.items():
            if key in META_KEYS:
                meta[META_KEYS[key]] = str(value)
            else:
                flatten(value, ('%s · %s' % (prefix, key)) if prefix else str(key), out, meta)
    elif isinstance(result, (list, tuple)):
        for i, value in enumerate(result):
            flatten(value, '%s #%d' % (prefix, i + 1), out, meta)
    else:
        url = extract_url(result)
        if url:
            raw_name = prefix or '直播源'
            item = {'name': raw_name, 'url': url}
            # 还原清晰度标记 @Q[level|quality]@name
            m = QUALITY_TAG_RE.match(raw_name)
            if m:
                item['level'] = int(m.group(1))
                item['quality'] = m.group(2)
                item['name'] = m.group(3) or item['quality'] or '直播源'
            # 兜底：剥离任意位置的残留内部标记（兼容历史版本数据）
            item['name'] = re.sub(r'@Q\[\d*\|[^\]]*\]@', '', item['name']).strip()
            # 去掉与清晰度重复的开头注释（如「原画 · FLV」→「FLV」）
            if item.get('quality'):
                item['name'] = re.sub(
                    r'^' + re.escape(item['quality']) + r'\s*[·\-—_/]?\s*', '', item['name']).strip(' ·-—_/')
            if not item['name']:
                item['name'] = item.get('quality') or '直播源'
            out.append(item)
    return out, meta


def do_parse(key, rid):
    """在子进程中调用对应平台脚本进行解析。"""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    proc = subprocess.run(
        [sys.executable, RUNNER, key, rid],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=TIMEOUT,
    )
    marker = '@@RESULT@@'
    if marker not in proc.stdout:
        detail = (proc.stderr or proc.stdout or '').strip()[-600:]
        raise RuntimeError('解析器异常退出：' + (detail or '退出码 %s' % proc.returncode))
    payload = json.loads(proc.stdout.split(marker, 1)[1].strip().splitlines()[0])
    if isinstance(payload, dict) and '__error__' in payload:
        raise RuntimeError(payload['__error__'])
    return flatten(payload)


@app.route('/')
def index():
    return render_template('index.html', platforms=PLATFORMS)


@app.route('/api/health')
def health():
    return jsonify(ok=True, service='running', engine='new+legacy', proxy=True)


# ---------- 抖音扫码登录：获取 cookie 以解锁原画 ----------
@app.route('/api/douyin/status')
def douyin_status():
    try:
        import douyin_login
        return jsonify(ok=True, **douyin_login.cookie_status())
    except Exception as e:
        return jsonify(ok=False, error=str(e))


@app.route('/api/douyin/qrcode', methods=['POST'])
def douyin_qrcode():
    try:
        import douyin_login
        r = douyin_login.get_qrcode()
        if r.get('ok'):
            r['qrcode_png'] = douyin_login.make_qrcode_png(r['qrcode_url'])
        return jsonify(r)
    except Exception as e:
        return jsonify(ok=False, message='生成二维码失败：%s' % e)


@app.route('/api/douyin/check', methods=['POST'])
def douyin_check():
    data = request.get_json(silent=True) or {}
    token = str(data.get('token', '')).strip()
    if not token:
        return jsonify(ok=False, message='缺少 token')
    try:
        import douyin_login
        return jsonify(douyin_login.check_qrconnect(token))
    except Exception as e:
        return jsonify(ok=False, status='error', message=str(e))


@app.route('/api/douyin/browser-cookie', methods=['POST'])
def douyin_browser_cookie():
    """通过 CDP 从独立登录浏览器读取抖音 cookie 并保存（网页版主方案）。"""
    try:
        import douyin_login
        try:
            import cdp_login
            r = cdp_login.read_login_cookie()
        except ImportError:
            import browser_cookie
            r = browser_cookie.extract_douyin_cookie()
        if r.get('ok') and r.get('cookie'):
            douyin_login.save_cookie(r['cookie'])
            r.update(douyin_login.cookie_status())
        return jsonify(r)
    except Exception as e:
        return jsonify(ok=False, message='提取失败：%s' % e)


@app.route('/api/douyin/open-login', methods=['POST'])
def douyin_open_login():
    """启动独立真实浏览器打开抖音登录页（真实 TLS 指纹，不会被 4031 风控）。"""
    try:
        import cdp_login
        return jsonify(cdp_login.start_login_browser())
    except Exception as e:
        # 回退：用系统默认浏览器打开
        try:
            import webbrowser
            webbrowser.open('https://live.douyin.com/')
        except Exception:
            pass
        return jsonify(ok=False, message='启动登录浏览器失败：%s' % e)


@app.route('/api/douyin/close-login', methods=['POST'])
def douyin_close_login():
    """关闭 CDP 登录浏览器并清理临时 profile。"""
    try:
        import cdp_login
        return jsonify(cdp_login.close_browser())
    except Exception as e:
        return jsonify(ok=False, message=str(e))


@app.route('/api/douyin/cookie', methods=['POST', 'DELETE'])
def douyin_cookie():
    try:
        import douyin_login
        if request.method == 'DELETE':
            douyin_login.clear_cookie()
            return jsonify(ok=True, message='已清除 cookie')
        data = request.get_json(silent=True) or {}
        cookie = str(data.get('cookie', '')).strip()
        if not cookie:
            return jsonify(ok=False, message='cookie 不能为空')
        douyin_login.save_cookie(cookie)
        return jsonify(ok=True, message='cookie 已保存', **douyin_login.cookie_status())
    except Exception as e:
        return jsonify(ok=False, message=str(e))


# ---------- 直播流代理：浏览器同源访问，绕开跨域/鉴权限制 ----------
_proxy_session = requests.Session()
_proxy_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
})
if Retry is not None:
    _proxy_session.mount('http://', HTTPAdapter(max_retries=Retry(total=1, connect=1, read=1)))
    _proxy_session.mount('https://', HTTPAdapter(max_retries=Retry(total=1, connect=1, read=1)))


def _proxy_url(target):
    """把一个绝对 URL 包装成本地代理地址。"""
    return '/api/stream?url=' + quote(target, safe='')


def _rewrite_m3u8(body, base_url):
    """重写 m3u8 中的 URI（分片、子 m3u8、key）为本地代理地址。"""
    out_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith('#'):
            # 处理 #EXT-X-KEY:URI="xxx" 与 #EXT-X-MAP:URI="xxx"
            def repl(m):
                uri = m.group(2)
                if re.match(r'^https?://', uri, re.I):
                    abs_uri = uri
                else:
                    abs_uri = urljoin(base_url, uri)
                return m.group(1) + _proxy_url(abs_uri) + m.group(3)
            line = re.sub(r'(URI=")([^"]+)(")', repl, line)
            out_lines.append(line)
        else:
            abs_uri = stripped if re.match(r'^https?://', stripped, re.I) else urljoin(base_url, stripped)
            out_lines.append(_proxy_url(abs_uri))
    return '\n'.join(out_lines)


@app.route('/api/potplayer/status')
def api_potplayer_status():
    """检测本机是否安装 PotPlayer，供前端决定是否显示播放按钮。"""
    try:
        import potplayer
        return jsonify(potplayer.status())
    except Exception as e:
        return jsonify(ok=False, installed=False, message=str(e))


@app.route('/api/potplayer/play', methods=['POST'])
def api_potplayer_play():
    """用本机 PotPlayer 打开直播流（生成临时 m3u8，拉起独立进程）。"""
    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    quality = str(data.get('quality', '')).strip()[:40]
    if not url or not re.match(r'^https?://', url, re.I):
        return jsonify(ok=False, message='无效的直播地址')
    try:
        import potplayer
        return jsonify(potplayer.play(url, quality))
    except Exception as e:
        return jsonify(ok=False, message='启动 PotPlayer 失败：%s' % e)


@app.route('/api/stream')
def api_stream():
    """直播流代理端点。m3u8 文本重写分片地址，二进制流直接透传。"""
    target = request.args.get('url', '')
    if not target or not re.match(r'^https?://', target, re.I):
        return Response('invalid url', status=400)
    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return Response('invalid url', status=400)

    # 把浏览器请求携带的 Range / 关键查询参数透传给源站
    fwd_headers = {}
    rng = request.headers.get('Range')
    if rng:
        fwd_headers['Range'] = rng
    try:
        upstream = _proxy_session.get(target, headers=fwd_headers, timeout=(8, 20), stream=True,
                                      allow_redirects=True)
    except Exception as exc:
        return Response('upstream error: %s' % exc, status=502)

    ctype = (upstream.headers.get('Content-Type') or '').lower()
    is_playlist = (('mpegurl' in ctype)
                   or target.split('?')[0].lower().endswith(('.m3u8', '.m3u'))
                   or 'm3u8' in parsed.path.lower())

    if is_playlist:
        try:
            body = upstream.content.decode('utf-8', 'replace')
        except Exception:
            body = upstream.text
        body = _rewrite_m3u8(body, upstream.url)
        resp = Response(body, status=upstream.status_code)
        resp.headers['Content-Type'] = 'application/vnd.apple.mpegurl; charset=utf-8'
    else:
        # 二进制分片/FLV：流式透传
        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()
        resp = Response(generate(), status=upstream.status_code)
        ct = upstream.headers.get('Content-Type')
        resp.headers['Content-Type'] = ct or 'video/mp2t'
        cr = upstream.headers.get('Content-Range')
        if cr:
            resp.headers['Content-Range'] = cr
        ar = upstream.headers.get('Accept-Ranges')
        if ar:
            resp.headers['Accept-Ranges'] = ar

    # 关键：允许浏览器读取（同源其实不需要，但保留以防 iframe/scenario）
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/api/parse', methods=['POST'])
def api_parse():
    data = request.get_json(silent=True) or {}
    key = str(data.get('platform', '')).strip()
    rid = str(data.get('rid', '')).strip()
    start = time.time()

    if key not in PLATFORM_MAP:
        return jsonify(ok=False, error='不支持的平台')
    if not rid:
        return jsonify(ok=False, error='请输入房间号或链接')
    if len(rid) > 300 or any(ord(ch) < 32 for ch in rid):
        return jsonify(ok=False, error='输入内容不合法')

    name = PLATFORM_MAP[key][0]
    try:
        urls, meta = do_parse(key, rid)
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, error='解析超时（%d 秒），该平台接口可能已失效或网络不通' % TIMEOUT,
                       elapsed=round(time.time() - start, 2))
    except RuntimeError as exc:
        return jsonify(ok=False, error=str(exc), elapsed=round(time.time() - start, 2))
    except Exception as exc:
        return jsonify(ok=False, error='服务端错误：%s' % exc,
                       elapsed=round(time.time() - start, 2))

    if not urls:
        return jsonify(ok=False,
                       error='未获取到直播地址：可能未开播、房间号错误，或该平台接口已变更失效。',
                       elapsed=round(time.time() - start, 2))
    return jsonify(ok=True, platform=name, rid=rid, urls=urls, meta=meta,
                   elapsed=round(time.time() - start, 2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8899)
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    args = parser.parse_args()

    url = 'http://%s:%d/' % ('127.0.0.1' if args.host == '0.0.0.0' else args.host, args.port)
    print('=' * 56)
    print(' 直播源解析 Web 工具  (based on wbt5/real-url)')
    print(' 访问地址: ' + url)
    print(' 按 Ctrl+C 停止服务')
    print('=' * 56)
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
