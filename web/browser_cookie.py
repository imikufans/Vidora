# -*- coding: utf-8 -*-
"""从本机浏览器（Chrome / Edge）已登录的抖音会话中提取 cookie。

背景：抖音二维码接口对数据中心/程序请求返回 4031 风控，无法在软件内生成二维码。
解决思路：让用户在自己的浏览器里正常登录抖音（真实 TLS 指纹 + 家庭 IP，不会触发风控），
然后本模块从浏览器的 Cookie 数据库里读取 douyin.com 的登录态，供原画解析使用。

支持 Windows 下 Chrome / Edge 的 v10/v11 AES-GCM 加密 cookie（DPAPI 保护主密钥）。
仅依赖 ctypes + pycryptodome（项目已自带 Crypto），无需额外安装扩展。
"""

import base64
import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
from ctypes import wintypes

# 关键登录态字段（提取到这些才算真正登录）
LOGIN_KEYS = ('sessionid', 'sessionid_ss', 'sid_tt', 'passport_csrf_token')
# 抖音解析原画所需的完整字段（尽量都带上的基础字段）
BASE_KEYS = ('ttwid', 'msToken', 'odin_tt', 'uid_tt', 'sid_tt', 'sessionid',
             'sessionid_ss', 'passport_csrf_token', 'store-region', 'store-region-src')


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _dpapi_unprotect(data):
    """调用 Windows DPAPI 解密数据（当前用户作用域）。"""
    try:
        crypt32 = ctypes.windll.crypt32
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        blob_in = _DATA_BLOB(len(data), ctypes.cast(
            ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte)))
        blob_out = _DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            return b''
        out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out
    except Exception:
        return b''


def _aes_key_from_local_state(local_state_path):
    """从 Local State 提取并解密 AES 主密钥。"""
    try:
        with open(local_state_path, 'r', encoding='utf-8') as f:
            state = json.load(f)
        key_b64 = state['os_crypt']['encrypted_key']
        raw = base64.b64decode(key_b64)
        if raw.startswith(b'DPAPI'):
            raw = raw[5:]
        return _dpapi_unprotect(raw)
    except Exception:
        return b''


def _decrypt_value(enc, aes_key):
    """解密单条 cookie 值。返回 str 或 None。"""
    if not enc:
        return None
    try:
        if isinstance(enc, str):
            return enc
        if enc[:3] in (b'v10', b'v11') and aes_key:
            from Crypto.Cipher import AES
            payload = enc[3:]
            nonce, cipher = payload[:12], payload[12:]
            plaintext = AES.new(aes_key, AES.MODE_GCM, nonce=nonce).decrypt(cipher)
            return plaintext.decode('utf-8', 'replace')
        # v20 = App-bound Encryption，无法用简单 DPAPI 解；Chrome 老版本明文
        if enc[:3] == b'v20':
            return None
    except Exception:
        return None
    return None


def _browser_profiles():
    """列出本机 Chrome / Edge 的用户配置目录（Local State + Cookies）。"""
    local = os.environ.get('LOCALAPPDATA', '')
    if not local:
        return []
    candidates = [
        ('Chrome', os.path.join(local, 'Google', 'Chrome', 'User Data')),
        ('Edge', os.path.join(local, 'Microsoft', 'Edge', 'User Data')),
    ]
    found = []
    for name, base in candidates:
        state = os.path.join(base, 'Local State')
        if not os.path.isfile(state):
            continue
        # 只取最近使用的 Default / Profile *
        dirs = []
        default = os.path.join(base, 'Default', 'Network', 'Cookies')
        if os.path.isfile(default):
            dirs.append(os.path.join(base, 'Default'))
        try:
            for entry in sorted(os.listdir(base)):
                if entry.startswith('Profile '):
                    cookie = os.path.join(base, entry, 'Network', 'Cookies')
                    if os.path.isfile(cookie):
                        dirs.append(os.path.join(base, entry))
        except Exception:
            pass
        for d in dirs:
            found.append((name, os.path.basename(d), state,
                          os.path.join(d, 'Network', 'Cookies')))
    return found


def extract_douyin_cookie():
    """尝试从所有浏览器配置中提取抖音登录 cookie。

    返回 dict: {ok, cookie, source, keys, message}
    """
    profiles = _browser_profiles()
    if not profiles:
        return {'ok': False, 'message': '未在本机找到 Chrome / Edge 浏览器配置'}

    best = None  # 优先选包含 sessionid 的结果
    errors = []
    for name, profile, state_path, cookie_path in profiles:
        tmpdir = tempfile.mkdtemp(prefix='dyc_')
        try:
            # 复制文件避免占用锁（浏览器运行时数据库被锁定）
            tmp_cookie = os.path.join(tmpdir, 'Cookies')
            tmp_state = os.path.join(tmpdir, 'Local State')
            shutil.copy2(cookie_path, tmp_cookie)
            shutil.copy2(state_path, tmp_state)
            # WAL 文件中可能有最新写入的 cookie，一并复制；被占用则忽略，不影响主库
            for suffix in ('-wal', '-shm'):
                src = cookie_path + suffix
                if os.path.isfile(src):
                    try:
                        shutil.copy2(src, tmp_cookie + suffix)
                    except Exception:
                        pass

            aes_key = _aes_key_from_local_state(tmp_state)
            if not aes_key:
                errors.append('%s/%s: 无法解密主密钥(App-bound Encryption?)' % (name, profile))
                continue

            conn = sqlite3.connect('file:' + tmp_cookie.replace('\\', '/') + '?mode=ro', uri=True)
            rows = conn.execute(
                "SELECT name, encrypted_value, value FROM cookies "
                "WHERE host_key LIKE '%douyin.com%' ORDER BY host_key, name"
            ).fetchall()
            conn.close()

            values = {}
            for key, enc, plain in rows:
                val = _decrypt_value(enc, aes_key) or (plain or '')
                if val and key not in values:
                    values[key] = val

            has_login = any(k in values for k in LOGIN_KEYS)
            if not has_login:
                errors.append('%s/%s: 未找到已登录的抖音会话' % (name, profile))
                continue

            # 组装 cookie：优先基础字段，再带上其余全部 douyin 字段
            ordered = [k for k in BASE_KEYS if k in values]
            ordered += [k for k in values if k not in ordered]
            cookie = '; '.join('%s=%s' % (k, values[k]) for k in ordered)
            result = {
                'ok': True, 'cookie': cookie, 'source': '%s (%s)' % (name, profile),
                'keys': ordered[:16], 'length': len(cookie),
                'message': '已从 %s %s 提取到抖音登录态' % (name, profile),
            }
            if any(k in values for k in ('sessionid', 'sessionid_ss')):
                return result
            best = best or result
        except Exception as e:
            errors.append('%s/%s: %s' % (name, profile, str(e)[:80]))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if best:
        return best
    return {'ok': False, 'message': '浏览器里没有已登录的抖音会话。请在 Chrome/Edge 打开 live.douyin.com 并登录后再试。'
            + ('（' + '；'.join(errors[:2]) + '）' if errors else '')}


if __name__ == '__main__':
    print(json.dumps(extract_douyin_cookie(), ensure_ascii=False, indent=2))
