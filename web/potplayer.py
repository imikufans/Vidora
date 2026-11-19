# -*- coding: utf-8 -*-
"""PotPlayer 本地播放支持。

- 检测 Windows 上是否安装 PotPlayer（常见路径 + 注册表 + 环境变量）。
- 生成临时 .m3u8 播放列表，用 PotPlayer 打开原始直播 URL。
  直播源本身带签名/鉴权参数，交给 PotPlayer 直接请求比经浏览器代理更稳。
"""

import os
import re
import subprocess
import tempfile
import time

# 常见安装路径（按优先级）
_COMMON_PATHS = [
    r'C:\Program Files\Daum\PotPlayer\PotPlayerMini64.exe',
    r'C:\Program Files (x86)\Daum\PotPlayer\PotPlayerMini.exe',
    r'C:\Program Files\Daum\PotPlayer\PotPlayerMini.exe',
    r'C:\Program Files (x86)\Daum\PotPlayer\PotPlayerMini64.exe',
]

# 注册表候选位置
_REG_KEYS = [
    (r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PotPlayerMini64.exe', None),
    (r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\PotPlayerMini.exe', None),
    (r'SOFTWARE\Daum\PotPlayer', 'InstallPath'),
    (r'SOFTWARE\WOW6432Node\Daum\PotPlayer', 'InstallPath'),
]

_cache = None  # None=未检测, str=路径, ''=未安装


def _from_registry():
    """从 Windows 注册表查询 PotPlayer 路径（仅 Windows，失败返回空列表）。"""
    found = []
    if os.name != 'nt':
        return found
    try:
        import winreg
    except Exception:
        return found
    hives = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
    for hive in hives:
        for sub, valname in _REG_KEYS:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    # App Paths 的默认值即 exe 全路径；Daum 项取 InstallPath 值
                    val, _ = winreg.QueryValueEx(key, valname if valname else '')
                    val = str(val).strip().strip('"')
                    if val.lower().endswith('.exe'):
                        if os.path.isfile(val):
                            found.append(val)
                    elif os.path.isdir(val):
                        for exe in ('PotPlayerMini64.exe', 'PotPlayerMini.exe'):
                            p = os.path.join(val, exe)
                            if os.path.isfile(p):
                                found.append(p)
            except Exception:
                pass
    return found


def find_potplayer():
    """返回 PotPlayer 可执行文件绝对路径；未安装返回空字符串。结果缓存。"""
    global _cache
    if _cache is not None:
        return _cache
    candidates = list(_COMMON_PATHS) + _from_registry()
    # 环境变量里的自定义安装目录
    for env in ('POTPLAYER', 'POTPLAYER_PATH'):
        v = os.environ.get(env, '').strip().strip('"')
        if v:
            candidates.insert(0, v)
    seen = set()
    for p in candidates:
        p = os.path.normpath(p) if p else ''
        if not p or p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            _cache = p
            return p
    _cache = ''
    return ''


def status():
    """给前端的检测结果。"""
    exe = find_potplayer()
    return {
        'ok': True,
        'installed': bool(exe),
        'path': exe or '',
        'name': os.path.basename(exe) if exe else '',
    }


def _safe_name(text, maxlen=24):
    """把线路名处理成安全的文件名片段。"""
    text = re.sub(r'[\\/:*?"<>|\s]+', '_', str(text or 'live')).strip('_')
    return (text or 'live')[:maxlen]


def make_playlist(url, quality=''):
    """生成临时 m3u8 播放列表文件，返回其路径。

    用 #EXTVLCOPT 附带常见请求头，帮助部分需要 Referer 的源正常播放
    （PotPlayer 兼容该写法；不识别时忽略，不影响主流程）。
    """
    tmpdir = os.path.join(tempfile.gettempdir(), 'live_parser_m3u8')
    os.makedirs(tmpdir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S') + '_%03d' % (int(time.time() * 1000) % 1000)
    path = os.path.join(tmpdir, '%s_%s.m3u8' % (_safe_name(quality), ts))
    lines = [
        '#EXTM3U',
        '#EXT-X-VERSION:3',
        '#EXTINF:-1,%s' % (quality or '直播流'),
        '#EXTVLCOPT:http.user-agent=Mozilla/5.0',
        url,
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    # 只保留最近 20 个播放列表，避免临时目录堆积
    try:
        files = sorted(
            (os.path.join(tmpdir, n) for n in os.listdir(tmpdir) if n.endswith('.m3u8')),
            key=os.path.getmtime, reverse=True)
        for old in files[20:]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception:
        pass
    return path


def play(url, quality=''):
    """用 PotPlayer 打开直播流。返回 {ok, message, ...}。"""
    exe = find_potplayer()
    if not exe:
        return {'ok': False, 'message': '未检测到 PotPlayer，请确认已安装（默认路径 C:\\Program Files\\Daum\\PotPlayer）'}
    if not url or not url.startswith('http'):
        return {'ok': False, 'message': '无效的直播地址'}
    try:
        playlist = make_playlist(url, quality)
        # DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP：让 PotPlayer 脱离本服务独立运行，
        # 关闭 Web 服务或 exe 时不会连带关掉播放器。
        flags = 0
        if os.name == 'nt':
            flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        subprocess.Popen([exe, playlist], creationflags=flags)
        return {'ok': True, 'message': '已用 PotPlayer 打开：%s' % (quality or '直播流'),
                'path': exe, 'playlist': playlist}
    except Exception as e:
        return {'ok': False, 'message': '启动 PotPlayer 失败：%s' % e}


if __name__ == '__main__':
    print(status())
