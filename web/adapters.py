# -*- coding: utf-8 -*-
"""特殊平台适配器。

real-url 仓库中的 huya.py / douyin.py 在模块顶层直接调用 input()，
无法作为模块导入，因此这里按原脚本逻辑改造成可调用的函数。
"""

import base64
import hashlib
import re
import time
import urllib.parse

import requests


# ---------------- 虎牙（源自 huya.py） ----------------

def _huya_live(e):
    i, b = e.split('?')
    r = i.split('/')
    s = re.sub(r'.(flv|m3u8)', '', r[-1])
    c = b.split('&', 3)
    c = [x for x in c if x != '']
    n = {x.split('=')[0]: x.split('=')[1] for x in c}
    fm = urllib.parse.unquote(n['fm'])
    u = base64.b64decode(fm).decode('utf-8')
    p = u.split('_')[0]
    f = str(int(time.time() * 1e7))
    l = n['wsTime']
    h = '_'.join([p, '0', s, f, l])
    m = hashlib.md5(h.encode('utf-8')).hexdigest()
    y = c[-1]
    return "{}?wsSecret={}&wsTime={}&u=0&seqid={}&{}".format(i, m, l, f, y)


def huya_get_real_url(room_id):
    room_url = 'https://m.huya.com/' + str(room_id).strip()
    header = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/75.0.3770.100 Mobile Safari/537.36',
    }
    try:
        response = requests.get(url=room_url, headers=header, timeout=15).text
        live_line_url = re.findall(r'"liveLineUrl":"([\s\S]*?)",', response)[0]
        liveline = base64.b64decode(live_line_url).decode('utf-8')
        if not liveline:
            raise Exception('未开播或直播间不存在')
        if 'replay' in liveline:
            raise Exception('该直播间当前是录像回放：' + liveline)
        url = _huya_live(liveline)
        return {"flv": ("https:" + url).replace("hls", "flv").replace("m3u8", "flv")}
    except Exception as exc:
        if str(exc).startswith('该直播间'):
            raise
        raise Exception('未开播或直播间不存在（虎牙接口可能已变更）') from exc


# ---------------- 抖音（源自 douyin.py） ----------------

def douyin_get_real_url(text):
    text = str(text).strip()
    headers = {
        'authority': 'v.douyin.com',
        'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 10_3_1 like Mac OS X) '
                      'AppleWebKit/603.1.30 (KHTML, like Gecko) Version/10.0 '
                      'Mobile/14E304 Safari/602.1',
    }
    if re.match(r'\d{19}', text):
        room_id = text
    else:
        try:
            m = re.search(r'(https?://[^\s]+)', text)
            if not m:
                raise ValueError('请输入 19 位 room_id 或分享链接')
            response = requests.head(m.group(1), headers=headers, timeout=15, allow_redirects=True)
            room_id = re.search(r'\d{19}', response.url).group(0)
        except Exception as exc:
            raise Exception('获取 room_id 失败') from exc

    try:
        headers.update({
            'authority': 'webcast.amemv.com',
            'cookie': '_tea_utm_cache_1128={%22utm_source%22:%22copy%22%2C%22utm_medium%22:%22android%22%2C%22utm_campaign%22:%22client_share%22}',
        })
        params = (
            ('type_id', '0'),
            ('live_id', '1'),
            ('room_id', room_id),
            ('app_id', '1128'),
        )
        response = requests.get(
            'https://webcast.amemv.com/webcast/room/reflow/info/',
            headers=headers, params=params, timeout=15,
        ).json()
        stream_url = response['data']['room']['stream_url']
        return {
            'rtmp': stream_url['rtmp_pull_url'],
            'hls': stream_url['hls_pull_url'],
        }
    except Exception as exc:
        raise Exception('获取真实地址失败（抖音接口可能已变更）') from exc
