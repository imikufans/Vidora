// Cloudflare Pages Function：把 /api/* 反向代理到解析后端
// 后端地址通过 Pages 环境变量 BACKEND_URL 配置，例如：
//   https://xxxx.trycloudflare.com   (本机 cloudflared 临时隧道)
//   https://api.yourdomain.com       (自有云服务器)

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Range',
};

// 允许的 API 路径，避免被当成开放代理
const ALLOWED = ['health', 'parse', 'stream', 'douyin'];

export async function onRequest(context) {
  const { request, env } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS });
  }

  const url = new URL(request.url);
  // /api/stream?url=...  或  /api/parse
  const parts = url.pathname.split('/').filter(Boolean); // ['api','parse'] / ['api','stream']
  const name = parts[1] || 'health';

  if (!ALLOWED.includes(name)) {
    return json({ ok: false, error: '不支持的 API：' + name }, 404);
  }

  const backend = (env.BACKEND_URL || '').replace(/\/+$/, '');
  if (!backend) {
    return json({
      ok: false,
      error: '后端未配置。请在 Cloudflare Pages 项目设置 → 环境变量 中添加 BACKEND_URL，指向运行「一键启动.bat」的机器（可用 cloudflared 隧道暴露）。详见包内部署说明.md'
    }, 503);
  }

  // 保留 /api/ 之后的完整子路径（如 douyin/status、douyin/check）
  const subPath = parts.slice(1).join('/');
  const target = backend + '/api/' + subPath + (url.search || '');
  const headers = new Headers();
  const ct = request.headers.get('Content-Type');
  if (ct) headers.set('Content-Type', ct);
  const range = request.headers.get('Range');
  if (range) headers.set('Range', range);

  try {
    const init = {
      method: request.method,
      headers,
      redirect: 'follow',
    };
    // m3u8 / ts / flv 都是 GET；只有 parse 是 POST
    if (request.method === 'POST' || request.method === 'PUT') {
      init.body = await request.arrayBuffer();
    }
    const upstream = await fetch(target, init);

    // 直播流（ts/flv/m3u8）：直接透传二进制，保留状态码与关键响应头
    const respHeaders = new Headers(upstream.headers);
    for (const [k, v] of Object.entries(CORS)) respHeaders.set(k, v);
    respHeaders.delete('Content-Encoding'); // 避免二次解压问题

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: respHeaders,
    });
  } catch (e) {
    return json({ ok: false, error: '连接后端失败：' + (e && e.message ? e.message : e) +
      '。请确认后端服务已启动、BACKEND_URL 配置正确。' }, 502);
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...CORS },
  });
}
