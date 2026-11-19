# -*- coding: utf-8 -*-
"""子进程解析运行器。

用法：python runner.py <平台key> <房间号/链接>

在独立进程中导入 real-url 仓库的对应脚本并调用其 get_real_url()，
把结果以 @@RESULT@@<JSON> 的形式输出到 stdout，供 Web 层解析。
进程隔离可以避免不同平台脚本之间的依赖冲突与全局变量污染。
"""

import importlib
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', 'real-url'))
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

MARKER = '@@RESULT@@'


def run(key, rid):
    # 1) 优先使用新引擎（DouyinLiveRecorder，2026 年仍在维护）
    try:
        import new_engine
        if key in new_engine.SUPPORTED:
            result = new_engine.parse(key, rid)
            if result.get('ok'):
                data = result['data']
                # 加上主播/标题信息，前端可展示
                out = {}
                if data.get('anchor'):
                    out['@@anchor@@'] = data['anchor']
                if data.get('title'):
                    out['@@title@@'] = data['title']
                for u in data['urls']:
                    # 用不可见标记把清晰度元数据随线路名一起传出，供 Web 层还原；
                    # 无元数据的线路保持原样，兼容旧逻辑。
                    name = u['name']
                    if u.get('level') or u.get('quality'):
                        tag = '@Q[%s|%s]@' % (u.get('level', 0), u.get('quality', ''))
                        name = tag + name
                    if u['url'] in [v for k, v in out.items() if k.startswith('@Q[')]:
                        continue
                    out[name] = u['url']
                return out
            # 新引擎明确返回"未开播"等，直接采用，不再回退旧引擎
            raise RuntimeError(result.get('error', '新引擎解析失败'))
    except RuntimeError:
        raise
    except ImportError:
        pass  # 新引擎依赖缺失，回退
    except Exception:
        pass  # 新引擎异常，回退旧引擎

    # 2) 回退到旧引擎（real-url，2021 年）
    if key == 'douyu':
        module = importlib.import_module('douyu')
        return module.DouYu(rid).get_real_url()

    if key == 'huya':
        from adapters import huya_get_real_url
        return huya_get_real_url(rid)

    if key == 'douyin':
        from adapters import douyin_get_real_url
        return douyin_get_real_url(rid)

    module = importlib.import_module(key)
    if not hasattr(module, 'get_real_url'):
        raise Exception('该平台脚本没有 get_real_url 函数')
    return module.get_real_url(rid)


def main():
    if len(sys.argv) < 3:
        result = {'__error__': '参数不足'}
    else:
        key, rid = sys.argv[1], sys.argv[2]
        try:
            result = run(key, rid)
        except ModuleNotFoundError as exc:
            result = {'__error__': '缺少依赖模块：%s（请重新运行一键启动.bat 安装依赖）' % exc.name}
        except Exception as exc:
            msg = str(exc).strip() or exc.__class__.__name__
            # 只保留末尾几行，避免把完整 traceback 暴露给前端
            result = {'__error__': msg[:500]}
        except BaseException:
            result = {'__error__': traceback.format_exc(limit=3)[-500:]}

    sys.stdout.write('\n' + MARKER + json.dumps(result, ensure_ascii=False, default=str) + '\n')
    sys.stdout.flush()


if __name__ == '__main__':
    main()
