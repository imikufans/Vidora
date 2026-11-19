# -*- coding: utf-8 -*-
"""新解析引擎适配层：基于 DouyinLiveRecorder 的 spider/stream 模块。

该引擎仍在维护（2026 年），解析逻辑远比 2021 年的 real-url 新，
支持抖音短链、虎牙/斗鱼新签名、B站原画等。

对外只暴露 parse(platform_key, user_input) -> dict，由 runner.py 调用。
"""

import asyncio
import os
import re
import sys

import requests

DLR_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'DLR'))
if DLR_DIR not in sys.path:
    sys.path.insert(0, DLR_DIR)

from src import spider, stream  # noqa: E402


def _douyin_url(x):
    """把抖音的房间号/各种分享链接归一化成引擎能识别的 URL。"""
    x = x.strip()
    if not x.startswith('http'):
        return 'https://live.douyin.com/' + x
    # v.douyin.com 短链、含 reflow 的分享链：原样传给引擎（引擎会跟随重定向）
    if 'v.douyin.com' in x or 'reflow' in x or 'sec_user_id' in x:
        return x
    # www.douyin.com/follow/live/xxxxx?... -> 提取房间号，改用 live.douyin.com
    m = re.search(r'/live/(\d{8,})', x)
    if m:
        return 'https://live.douyin.com/' + m.group(1)
    # 链接中任意长数字串（room_id）
    m = re.search(r'(\d{9,})', x)
    if m:
        return 'https://live.douyin.com/' + m.group(1)
    return x


# 用户输入的房间号/ID 拼成各平台的标准直播间 URL
URL_BUILDERS = {
    'bilibili': lambda x: x if x.startswith('http') else 'https://live.bilibili.com/' + x,
    'douyu':    lambda x: x if x.startswith('http') else 'https://www.douyu.com/' + x,
    'huya':     lambda x: x if x.startswith('http') else 'https://www.huya.com/' + x,
    'douyin':   _douyin_url,
    'kuaishou': lambda x: x if x.startswith('http') else ('https://live.kuaishou.com/u/' + x
                if not x.isdigit() else 'https://live.kuaishou.com/' + x),
    'yy':       lambda x: x if x.startswith('http') else 'https://www.yy.com/' + x,
    'iqiyi':    lambda x: x,
    'acfun':    lambda x: x if x.startswith('http') else 'https://live.acfun.cn/live/' + x,
    'cc':       lambda x: x if x.startswith('http') else 'https://cc.163.com/' + x,
    'kugou':    lambda x: x if x.startswith('http') else 'https://fanxing2.kugou.com/' + x,
    'maoer':    lambda x: x if x.startswith('http') else 'https://fm.missevan.com/live/' + x,
    'twitch':   lambda x: x if x.startswith('http') else 'https://www.twitch.tv/' + x,
    'huajiao':  lambda x: x if x.startswith('http') else 'https://www.huajiao.com/user/' + x,
    'inke':     lambda x: x if x.startswith('http') else 'https://www.inke.cn/liveroom/index.html?id=' + x,
    'jd':       lambda x: x if x.startswith('http') else x,
    'migu':     lambda x: x if x.startswith('http') else 'https://www.miguvideo.com/p/live/' + x,
}

# 平台 -> (spider 取数据函数, stream 转换函数 或 None)
# stream=None 表示 spider 函数已直接返回最终 URL 字符串
SPIDER_FUNCS = {
    'bilibili': ('get_bilibili_stream_data', None),
    'douyin':   ('get_douyin_app_stream_data', 'get_douyin_stream_url'),
    'kuaishou': ('get_kuaishou_stream_data', 'get_kuaishou_stream_url'),
    'huya':     ('get_huya_stream_data', 'get_huya_stream_url'),
    'douyu':    ('get_douyu_info_data', 'get_douyu_stream_url'),
    'yy':       ('get_yy_stream_data', 'get_yy_stream_url'),
    'acfun':    ('get_acfun_stream_data', None),
    'cc':       ('get_netease_stream_data', 'get_netease_stream_url'),
    'kugou':    ('get_kugou_stream_url', None),
    'maoer':    ('get_maoerfm_stream_url', None),
    'twitch':   ('get_twitchtv_stream_data', None),
    'huajiao':  ('get_huajiao_stream_url', None),
    'inke':     ('get_yingke_stream_url', None),
    'jd':       ('get_jd_stream_url', None),
    'migu':     ('get_migu_stream_url', None),
}

# 需要 stream.get_stream_url 通用转换的平台（spider 返回 play_url_list 结构）
GENERIC_STREAM = {
    'cc', 'acfun', 'kugou', 'maoer', 'twitch', 'huajiao', 'inke', 'jd', 'migu',
}

SUPPORTED = set(SPIDER_FUNCS.keys())


def _extract_urls(obj, out):
    """递归从任意结构中提取所有 http(s) 地址。"""
    if isinstance(obj, str):
        if obj.startswith('http://') or obj.startswith('https://'):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _extract_urls(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _extract_urls(v, out)


# 清晰度档位：level 越大越清晰。前端据此做切换与自动选档。
QUALITY_TIERS = [
    (4, '原画', 'OD'),
    (3, '超清', 'HD'),
    (2, '高清', 'SD'),
    (1, '标清', 'LD'),
]
# 常见清晰度 key -> (level, 中文名)
_QUALITY_KEY_MAP = {
    'ORIGIN': (4, '原画'), 'FULL_HD1': (4, '原画'), 'OD': (4, '原画'),
    'FULL_HD2': (3, '超清'), 'HD1': (3, '超清'), 'HD': (3, '超清'),
    'UHD': (3, '超清'), 'BD': (3, '超清'),
    'SD1': (2, '高清'), 'SD': (2, '高清'), 'SD2': (1, '标清'),
    'HD2': (2, '高清'), 'LD': (1, '标清'), 'LD1': (1, '标清'),
}
# B站 qn -> 中文名
_BILI_QN = {10000: '原画', 12000: '4K', 4000: '超清', 2500: '超清', 2000: '超清',
            1600: '高清', 1500: '高清', 1200: '高清', 900: '高清', 800: '高清',
            600: '标清', 500: '标清', 400: '标清', 300: '标清', 250: '标清',
            200: '流畅', 80: '流畅'}


def _quality_of_key(key):
    """从清晰度 key 推断 (level, 名称)，无法识别返回 (0,'')。"""
    k = str(key or '').upper()
    if k in _QUALITY_KEY_MAP:
        return _QUALITY_KEY_MAP[k]
    for token, tier in _QUALITY_KEY_MAP.items():
        if token in k:
            return tier
    return (0, '')


def _tier_from_bili_qn(qn):
    """把 B站 qn 数值映射到最接近的档位 (level, 名称)。"""
    name = _BILI_QN.get(qn, '')
    if not name:
        if qn >= 4000:
            name = '超清'
        elif qn >= 800:
            name = '高清'
        elif qn >= 300:
            name = '标清'
        else:
            name = '流畅'
    for level, label, _ in QUALITY_TIERS:
        if label == name:
            return (level, label)
    return (2, name)


def _make_url(name, url, level=0, quality=''):
    """构造统一的线路 dict。"""
    d = {'name': name, 'url': url}
    if level:
        d['level'] = level
    if quality:
        d['quality'] = quality
    return d


def _extract_douyin_qualities(data, result):
    """从抖音 spider 原始数据展开所有清晰度。data['stream_url'] 里
    flv_pull_url / hls_pull_url_map 是以清晰度 key 为键的字典。"""
    stream = data.get('stream_url') or {}
    groups = [('hls_pull_url_map', 'm3u8'), ('flv_pull_url', 'flv')]
    found = []
    for field, proto in groups:
        mapping = stream.get(field) or {}
        if isinstance(mapping, dict):
            for key, url in mapping.items():
                if not (isinstance(url, str) and url.startswith('http')):
                    continue
                level, qname = _quality_of_key(key)
                if not qname:
                    qname, level = key, level or 2
                found.append((level, qname, proto, url))
    if not found:
        return False
    # 按清晰度降序，同档 m3u8 优先
    found.sort(key=lambda x: (-x[0], 0 if x[2] == 'm3u8' else 1))
    seen = set()
    urls = []
    for level, qname, proto, url in found:
        if url in seen:
            continue
        seen.add(url)
        urls.append(_make_url('%s · %s' % (qname, 'HLS' if proto == 'm3u8' else 'FLV'),
                              url, level, qname))
    result['urls'] = urls
    return True


def _extract_bili_qualities(urls):
    """给 B站 _bilibili_hls 生成的线路补上清晰度标签。
    该函数结果里的 url 本身按清晰度降序排列，按索引映射档位。"""
    levels = [4, 3, 2, 1]  # 原画/超清/高清/标清 依次
    enhanced = []
    for i, u in enumerate(urls):
        level = levels[i] if i < len(levels) else 1
        label = next((name for lv, name, _ in QUALITY_TIERS if lv == level), '')
        d = dict(u)
        d['level'] = level
        d['quality'] = d.get('quality') or label
        if d.get('name', '').startswith('m3u8'):
            d['name'] = label + ' · HLS'
        elif d.get('name', '').startswith('flv'):
            d['name'] = label + ' · FLV'
        enhanced.append(d)
    return enhanced


def _annotate_generic_urls(urls):
    """兜底：从线路名推断清晰度标签（其它平台）。"""
    enhanced = []
    for u in urls:
        d = dict(u)
        level, qname = _quality_of_key(d.get('name', ''))
        if qname:
            d['level'] = level
            d['quality'] = qname
        enhanced.append(d)
    return enhanced


def _result_from_info(info):
    """把 stream 转换后的 dict 归一化成 {anchor,title,urls,is_live}。"""
    is_live = bool(info.get('is_live')) if isinstance(info, dict) else False
    urls = []
    if isinstance(info, dict):
        # 优先取明确的流地址字段
        for key in ('m3u8_url', 'flv_url', 'record_url', 'play_url'):
            val = info.get(key)
            if isinstance(val, str) and val.startswith('http'):
                urls.append((key, val))
    if not urls:
        raw = []
        _extract_urls(info, raw)
        urls = [('直播源', u) for u in raw]
    # 去重保序
    seen, uniq = set(), []
    for name, url in urls:
        if url not in seen:
            seen.add(url)
            uniq.append({'name': name, 'url': url})
    return {
        'is_live': is_live,
        'anchor': info.get('anchor_name', '') if isinstance(info, dict) else '',
        'title': info.get('title', '') if isinstance(info, dict) else '',
        'urls': _annotate_generic_urls(uniq),
    }


def _bilibili_hls(user_input):
    """B 站专用解析：走 getRoomPlayInfo 接口，同时返回 m3u8(HLS) 与 flv 线路。

    B 站官方 getRoomPlayInfo 接口在 protocol=1 时提供 HLS(m3u8)，
    老 playUrl 接口只返回 flv。这里两者都取，m3u8 优先。
    返回 None 表示未开播/失败，交给上层回退到旧逻辑。
    """
    url = URL_BUILDERS['bilibili'](user_input)
    m = re.search(r'(\d+)', url.rstrip('/').rsplit('/', 1)[-1])
    if not m:
        return None
    room_id = m.group(1)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Referer': 'https://live.bilibili.com/',
    }
    result = {'is_live': False, 'anchor': '', 'title': '', 'urls': []}

    try:
        # --- 房间信息（主播、标题、开播状态）---
        # getInfoByRoom 常被 -352 风控，改用老的 get_info（code 0 且 live_status 在 data 顶层）
        info_api = 'https://api.live.bilibili.com/room/v1/Room/get_info'
        info = requests.get(info_api, params={'room_id': room_id}, headers=headers, timeout=12).json()
        room_info = info.get('data', {}) if info.get('code') == 0 else {}
        live_status = room_info.get('live_status', 0)
        result['title'] = room_info.get('title', '')
        result['is_live'] = bool(live_status)
        if not live_status:
            result['error'] = ('主播 %s 当前未开播' % result['anchor']) if result['anchor'] else '当前未开播或直播间不存在'
            return result

        # --- 播放地址：HLS(m3u8) 优先，flv 作为备选 ---
        play_api = 'https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo'
        base_params = {
            'room_id': room_id, 'codec': '0,1', 'qn': '10000', 'platform': 'web',
            'ptype': '8', 'dolby': '5', 'panorama': '1', 'hdr_type': '0,1',
        }

        def collect_urls(protocol, fmt):
            """返回 [{qn, url}]，按编码兼容性+清晰度降序。"""
            params = dict(base_params)
            params['protocol'] = protocol
            params['format'] = fmt
            entries = []
            data = requests.get(play_api, params=params, headers=headers, timeout=12).json()
            playurl = data.get('data', {}).get('playurl_info', {}).get('playurl', {})
            for stream in playurl.get('stream', []):
                for f in stream.get('format', []):
                    for codec in f.get('codec', []):
                        base = codec.get('base_url', '')
                        if not base:
                            continue
                        qn = codec.get('current_qn', 0) or 0
                        codec_name = codec.get('codec_name', '')  # avc / hev / av1
                        # 普通 H.264(avc) 兼容性最好，优先级最高；prohdr/minihevc 特殊编码排后面
                        codec_rank = 0 if codec_name == 'avc' else 1
                        url_infos = codec.get('url_info', [])
                        if url_infos:
                            for ui in url_infos:
                                host = ui.get('host', '')
                                extra = ui.get('extra', '')
                                if host.startswith('https'):
                                    full = host.rstrip('/') + base
                                    if extra:
                                        full += ('&' if '?' in full and not full.endswith('?') else '') + extra.lstrip('?')
                                    entries.append((codec_rank, -qn, qn, full))
                                    break
                        elif base.startswith('http'):
                            entries.append((codec_rank, -qn, qn, base))
            # 编码兼容性优先，其次清晰度降序，去重保序
            entries.sort(key=lambda x: (x[0], x[1]))
            seen, out = set(), []
            for rank, neg_qn, qn, u in entries:
                if u not in seen:
                    seen.add(u)
                    out.append({'qn': qn, 'url': u})
            return out

        def tag(entries, proto):
            out = []
            for i, e in enumerate(entries[:5]):
                level, qname = _tier_from_bili_qn(e['qn'])
                out.append({'name': '%s · %s' % (qname, 'HLS' if proto == 'm3u8' else 'FLV'),
                            'url': e['url'], 'level': level, 'quality': qname})
            return out

        m3u8s = collect_urls('1', '1,2')      # HLS
        flvs = collect_urls('0', '0')          # FLV
        result['urls'] = tag(m3u8s, 'm3u8') + tag(flvs, 'flv')
        return result if result['urls'] else None
    except Exception:
        # 出错则回退到 DLR 老逻辑
        return None


def parse(platform_key, user_input):
    """同步入口。返回 {ok, data, error}。"""
    if platform_key not in SUPPORTED:
        return {'ok': False, 'error': '新引擎不支持该平台: %s' % platform_key}

    build = URL_BUILDERS.get(platform_key, lambda x: x)
    url = build(user_input)

    spider_name, stream_name = SPIDER_FUNCS[platform_key]
    spider_fn = getattr(spider, spider_name)
    stream_fn = getattr(stream, stream_name) if stream_name else None

    # 抖音：读取扫码登录保存的 cookie，注入 spider 以解锁原画线路
    dy_cookie = ''
    if platform_key == 'douyin':
        try:
            import douyin_login
            dy_cookie = douyin_login.get_cookie()
        except Exception:
            dy_cookie = ''

    async def _run():
        # B 站优先走专用 HLS 解析（提供 m3u8），失败再回退到 DLR 老逻辑
        if platform_key == 'bilibili':
            hls = _bilibili_hls(user_input)
            if hls and hls.get('urls'):
                return hls
            if hls and not hls.get('urls'):
                return hls  # 明确未开播，直接返回
        if platform_key == 'douyin' and dy_cookie:
            # 传登录 cookie，抖音接口才会返回原画(FULL_HD1)线路
            data = await spider_fn(url=url, cookies=dy_cookie)
        else:
            data = await spider_fn(url=url)
        # spider 直接返回 URL 字符串
        if isinstance(data, str):
            return {'is_live': bool(data), 'urls': [{'name': '直播源', 'url': data}] if data else []}
        if not isinstance(data, dict):
            return {'is_live': False, 'urls': []}

        # 未开播 / 无流
        live_flag = data.get('is_live', data.get('live_status', data.get('status')))
        if live_flag in (False, 0, 4) and not data.get('stream_url') and not data.get('data'):
            return {
                'is_live': False,
                'anchor': data.get('anchor_name', ''),
                'error': '未开播或直播间不存在',
                'urls': [],
            }

        if stream_fn:
            if platform_key == 'douyin':
                info = await stream_fn(data, 'OD', None)
            elif platform_key in ('douyu',):
                info = await stream_fn(data, 'OD')
            else:
                info = await stream_fn(data, 'OD')
        elif platform_key in GENERIC_STREAM:
            info = await stream.get_stream_url(data, 'OD', spec=False)
        else:
            info = data

        result = _result_from_info(info)

        # 抖音：从原始数据展开所有清晰度档位（原画/超清/高清/标清）
        if platform_key == 'douyin':
            try:
                if _extract_douyin_qualities(data, result):
                    result['anchor'] = result.get('anchor') or info.get('anchor_name', '')
                    result['title'] = result.get('title') or info.get('title', '')
            except Exception:
                pass
        return result

    try:
        result = asyncio.run(_run())
        if result.get('error') and not result.get('urls'):
            return {'ok': False, 'error': result['error'], 'anchor': result.get('anchor', '')}
        if not result.get('urls'):
            anchor = result.get('anchor', '')
            if platform_key == 'douyin' and dy_cookie:
                return {
                    'ok': False,
                    'error': '抖音解析失败（可能 cookie 已失效或触发风控）。请点击"抖音登录"重新扫码，或清除 cookie 后重试。',
                    'anchor': anchor,
                    'cookie_invalid': True,
                }
            return {
                'ok': False,
                'error': ('主播 %s 当前未开播' % anchor) if anchor else '未获取到直播地址（可能未开播）',
                'anchor': anchor,
            }
        return {'ok': True, 'data': result}
    except Exception as exc:
        err = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        if platform_key == 'douyin' and dy_cookie:
            err += '（如持续失败，cookie 可能已失效，请重新扫码登录）'
        return {'ok': False, 'error': err}
