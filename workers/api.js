/**
 * 冰冰小美知识库 Cloudflare Workers API
 *
 * 处理动态功能：
 * - 访问计数器 (D1)
 * - 留言板 (D1)
 * - AI 冰美直接由前端调用 api.bbxmkb.cn (CORS)，不经过 Workers
 *
 * 部署: wrangler deploy
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    // CORS 预检
    if (method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': 'https://bbxmkb.cn',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
          'Access-Control-Max-Age': '86400',
        },
      });
    }

    const corsHeaders = {
      'Access-Control-Allow-Origin': 'https://bbxmkb.cn',
      'Content-Type': 'application/json',
    };

    // ===== 访问计数器 =====
    if (path === '/api/counter') {
      if (method === 'GET') {
        try {
          const result = await env.DB.prepare(
            'SELECT count FROM counters WHERE id = 1'
          ).first();
          return Response.json({ count: result?.count ?? 0 }, { headers: corsHeaders });
        } catch (e) {
          return Response.json({ count: 0 }, { headers: corsHeaders });
        }
      }

      if (method === 'POST' || method === 'PUT') {
        try {
          // 自增
          await env.DB.prepare(
            'UPDATE counters SET count = count + 1 WHERE id = 1'
          ).run();
          const result = await env.DB.prepare(
            'SELECT count FROM counters WHERE id = 1'
          ).first();
          return Response.json({ count: result.count }, { headers: corsHeaders });
        } catch (e) {
          return Response.json({ error: 'Counter update failed' }, { status: 500, headers: corsHeaders });
        }
      }
    }

    // ===== 留言板 =====
    if (path === '/api/guestbook') {
      if (method === 'GET') {
        try {
          const { results } = await env.DB.prepare(
            'SELECT id, name, message, created_at FROM guestbook ORDER BY id DESC LIMIT 100'
          ).all();
          return Response.json(results ?? [], { headers: corsHeaders });
        } catch (e) {
          return Response.json([], { headers: corsHeaders });
        }
      }

      if (method === 'POST') {
        try {
          const body = await request.json();
          const name = (body.name || '').trim();
          const message = (body.message || '').trim();

          if (!name || !message) {
            return Response.json({ error: '请填写昵称和留言内容' }, { status: 400, headers: corsHeaders });
          }
          if (name.length > 50 || message.length > 2000) {
            return Response.json({ error: '内容过长' }, { status: 400, headers: corsHeaders });
          }

          // 速率限制：同一 IP 每分钟最多 3 条
          const ip = request.headers.get('cf-connecting-ip') || 'unknown';
          const recent = await env.DB.prepare(
            "SELECT COUNT(*) as cnt FROM guestbook WHERE ip = ? AND created_at > datetime('now', '-1 minute')"
          ).bind(ip).first();

          if (recent && recent.cnt >= 3) {
            return Response.json({ error: '发送太快，请稍后再试' }, { status: 429, headers: corsHeaders });
          }

          await env.DB.prepare(
            'INSERT INTO guestbook (name, message, ip) VALUES (?, ?, ?)'
          ).bind(name, message, ip).run();

          return Response.json({ success: true }, { headers: corsHeaders });
        } catch (e) {
          return Response.json({ error: '留言失败，请稍后再试' }, { status: 500, headers: corsHeaders });
        }
      }
    }

    // 404 for unknown API routes
    return Response.json({ error: 'Not Found' }, { status: 404, headers: corsHeaders });
  },
};
