#!/usr/bin/env python3
"""
冰冰小美知识库 静态站点生成器
将 Flask/Jinja2 动态页面预渲染为静态 HTML，用于 Cloudflare Pages 部署。

用法:
  python3 scripts/build_static.py          # 完整构建
  python3 scripts/build_static.py --fast   # 快速构建（跳过帖子索引，仅核心页面）
"""
import sys
import os
import shutil
import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# 确保能找到 app.py 和 src/
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import (
    app,
    VAULT_ROOT,
    DATA_DIR,
    scan_all_files,
    build_post_index,
    post_index_built,
    post_index,
    core_article_pool,
    pinned_post_data,
    NODE_ORDER,
    CHILD_CONCEPTS,
    NODE_CONCEPTS,
    INDUSTRY_ORDER,
    INDUSTRY_FILES,
    INDUSTRY_CHILD_CONCEPTS,
    INDUSTRIES,
    LAYERS,
    get_node_info,
    get_child_concept,
    get_child_concepts_for_parent,
    get_core_nodes_nav,
    get_industry_nodes_nav,
    get_topic_nodes_nav,
    _get_topics,
    get_topic_info,
    get_topic_child_concept,
    get_topic_child_concepts_for_parent,
    get_industry_info,
    get_industry_child_concept,
    get_industry_child_concepts_for_parent,
    render_markdown,
    parse_wiki_links,
    style_meta_notes,
    strip_frontmatter,
    extract_card_meta,
    resolve_link,
    get_node_id_from_path,
    get_url_for_path,
    get_random_core_post,
    get_posts_page,
    get_word_freq_data,
    _parse_post_meta,
    get_post_html,
    COLUMN_SERIES,
    _match_column_series,
    COLUMN_DIR,
    TRADE_SYS_DIR,
    THREE_ELEM_DIR,
    PINNED_POST,
    link_graph,
)

SITE_DIR = BASE_DIR / "site"
BUILD_TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

FAST_MODE = "--fast" in sys.argv


def log(msg):
    print(f"  {msg}")


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def write_html(filepath, html):
    """写入 HTML 文件，确保目录存在。html 可以是 str 或 bytes。"""
    out = SITE_DIR / filepath
    ensure_dir(out.parent)
    if isinstance(html, str):
        out.write_text(html, encoding="utf-8")
    else:
        out.write_bytes(html)
    return out


# ===== Step 1: 初始化 =====
print("=" * 50)
print("冰冰小美知识库 静态站点生成器")
print("=" * 50)

print("\n1. 初始化缓存...")
scan_all_files()
log(f"链接图: {len(link_graph)} 个链接关系")

if not FAST_MODE:
    build_post_index()
    log(f"帖子索引: {len(post_index)} 篇帖子, {len(core_article_pool)} 篇核心")
else:
    log("快速模式：跳过帖子索引")

# ===== Step 2: 使用 test_client 渲染核心页面 =====
print("\n2. 渲染核心页面...")

# 统计
stats = defaultdict(int)

with app.test_client() as client:
    # --- 顶层页面 ---
    top_pages = [
        ("/", "index.html"),
        ("/about", "about.html"),
        ("/knowledge-base", "knowledge-base.html"),
        ("/search", "search.html"),
        ("/ai", "ai.html"),
        ("/guestbook", "guestbook.html"),
        ("/posts", "posts.html"),
        ("/posts/random", "posts/random.html"),
        ("/posts/timeline", "posts/timeline.html"),
        ("/posts/core", "posts/core.html"),
    ]

    for route, filename in top_pages:
        log(f"GET {route} → {filename}")
        resp = client.get(route)
        if resp.status_code == 200:
            write_html(filename, resp.data)
            stats["top_pages"] += 1
        elif resp.status_code == 302:
            # redirect (e.g. /changelog → /about)
            log(f"  → 302 重定向到 {resp.location}")
        else:
            log(f"  ⚠ HTTP {resp.status_code}")

    # --- 概念节点页面（父卡） ---
    log("\n--- 概念节点 ---")
    for node_id in NODE_ORDER:
        route = f"/node/{node_id}"
        log(f"GET {route}")
        resp = client.get(route)
        if resp.status_code == 200:
            write_html(f"node/{node_id}/index.html", resp.data)
            stats["node_pages"] += 1

        # 子卡
        children = get_child_concepts_for_parent(node_id)
        for child in children:
            child_id = child["id"]
            route = f"/node/{node_id}/{child_id}"
            resp = client.get(route)
            if resp.status_code == 200:
                write_html(f"node/{node_id}/{child_id}/index.html", resp.data)
                stats["child_node_pages"] += 1

    log(f"节点: {stats['node_pages']} 父卡 + {stats['child_node_pages']} 子卡")

    # --- 产业标的页面 ---
    log("\n--- 产业标的 ---")
    for ind_id in INDUSTRY_ORDER:
        route = f"/industry/{ind_id}"
        resp = client.get(route)
        if resp.status_code == 200:
            write_html(f"industry/{ind_id}/index.html", resp.data)
            stats["industry_pages"] += 1

        children = get_industry_child_concepts_for_parent(ind_id)
        for child in children:
            child_id = child["id"]
            route = f"/industry/{ind_id}/{child_id}"
            resp = client.get(route)
            if resp.status_code == 200:
                write_html(f"industry/{ind_id}/{child_id}/index.html", resp.data)
                stats["child_industry_pages"] += 1

    log(f"产业: {stats['industry_pages']} 父卡 + {stats['child_industry_pages']} 子卡")

    # --- 专题整理页面 ---
    log("\n--- 专题整理 ---")
    topics = _get_topics()
    for topic_id in topics:
        route = f"/topic/{topic_id}"
        resp = client.get(route)
        if resp.status_code == 200:
            write_html(f"topic/{topic_id}/index.html", resp.data)
            stats["topic_pages"] += 1

        children = get_topic_child_concepts_for_parent(topic_id)
        for child in children:
            child_id = child["id"]
            route = f"/topic/{topic_id}/{child_id}"
            resp = client.get(route)
            if resp.status_code == 200:
                write_html(f"topic/{topic_id}/{child_id}/index.html", resp.data)
                stats["child_topic_pages"] += 1

    log(f"专题: {stats['topic_pages']} 父卡 + {stats['child_topic_pages']} 子卡")

    # --- 单篇文章页面（概念/产业/专题下的 .md 文件） ---
    log("\n--- 文章页面 ---")
    article_dirs = [
        VAULT_ROOT / "1-核心概念",
        VAULT_ROOT / "2-产业标的",
        VAULT_ROOT / "3-专题整理",
    ]

    for article_dir in article_dirs:
        if not article_dir.exists():
            continue
        for md_file in article_dir.rglob("*.md"):
            if md_file.name.startswith("."):
                continue
            if md_file.name == "_模板.md" or md_file.name == "README.md":
                continue

            rel = md_file.relative_to(VAULT_ROOT)
            article_path = str(rel).replace(".md", "")
            route = f"/article/{article_path}"
            resp = client.get(route)
            if resp.status_code == 200:
                write_html(f"article/{article_path}/index.html", resp.data)
                stats["article_pages"] += 1
            else:
                # 有些文章可能渲染失败
                if stats["article_pages"] < 5:  # 只打印前几个错误
                    log(f"  ⚠ {article_path}: HTTP {resp.status_code}")

    log(f"文章: {stats['article_pages']} 篇")

    # --- 单篇帖子页面（仅渲染核心文章池中的帖子，不是全部9203篇） ---
    if not FAST_MODE and core_article_pool:
        log("\n--- 核心帖子页面 ---")
        for post in core_article_pool[:200]:  # 最多渲染200篇核心帖子
            filepath = post.get("filepath", "")
            if not filepath:
                continue
            route = f"/posts/read?path={filepath}"
            resp = client.get(route)
            if resp.status_code == 200:
                # 路径中的特殊字符处理
                safe_path = filepath.replace(".md", "").replace(" ", "_")
                write_html(f"posts/article/{safe_path}/index.html", resp.data)
                stats["post_article_pages"] += 1

        log(f"帖子文章: {stats['post_article_pages']} 篇")


# ===== Step 3: 预生成预览 JSON =====
print("\n3. 预生成预览 JSON...")
preview_count = 0
article_dirs = [
    VAULT_ROOT / "1-核心概念",
    VAULT_ROOT / "2-产业标的",
    VAULT_ROOT / "3-专题整理",
]

for article_dir in article_dirs:
    if not article_dir.exists():
        continue
    for md_file in article_dir.rglob("*.md"):
        if md_file.name.startswith("."):
            continue
        rel = md_file.relative_to(VAULT_ROOT)
        article_path = str(rel).replace(".md", "")

        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        html_content = render_markdown(content)
        html_content = parse_wiki_links(html_content, md_file)
        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else md_file.stem
        preview = html_content[:2000]
        if len(html_content) > 2000:
            preview += "..."

        json_file = SITE_DIR / "api" / "preview" / f"{article_path}.json"
        ensure_dir(json_file.parent)
        json_file.write_text(json.dumps({
            "title": title,
            "content": preview,
            "full_url": f"/article/{article_path}",
        }, ensure_ascii=False), encoding="utf-8")
        preview_count += 1

log(f"预览 JSON: {preview_count} 个")

# ===== Step 4: 预生成知识库导出 ZIP =====
print("\n4. 预生成知识库导出...")
import io
import zipfile

kb_dirs = [
    ("1-核心概念", VAULT_ROOT / "1-核心概念"),
    ("2-产业标的", VAULT_ROOT / "2-产业标的"),
    ("3-专题整理", VAULT_ROOT / "3-专题整理"),
]

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    for dir_label, dir_path in kb_dirs:
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.rglob("*.md")):
            name = md_file.name
            if name in ("_模板.md", "README.md", "待整理清单.md"):
                continue
            if name.endswith("_分析记录.md"):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            arcname = str(md_file.relative_to(VAULT_ROOT))
            zf.writestr(arcname, content)

buf.seek(0)
export_dir = SITE_DIR / "knowledge-base"
ensure_dir(export_dir)
(export_dir / "export.zip").write_bytes(buf.getvalue())
log(f"导出 ZIP: {buf.getbuffer().nbytes / 1024:.0f} KB")
stats["export_zip"] = 1

# ===== Step 5: 复制 static/ =====
print("\n5. 复制静态文件...")
static_src = BASE_DIR / "static"
static_dst = SITE_DIR / "static"
if static_dst.exists():
    shutil.rmtree(static_dst)
shutil.copytree(static_src, static_dst)
log(f"已复制 static/ → site/static/")

# ===== Step 6: 生成 Cloudflare 路由文件 =====
print("\n6. 生成 Cloudflare 配置文件...")

# _redirects: Cloudflare Pages 路由规则
redirects = """# Cloudflare Pages 路由规则
# 格式: <source> <destination> <status>

# API 路由由 Pages Functions 处理（functions/api/），不在此配置

# 旧重定向
/changelog  /about  301
"""
(SITE_DIR / "_redirects").write_text(redirects)
log("_redirects 已生成")

# _headers: 安全头 + 缓存策略
headers = """# Cloudflare Pages 安全头 + 缓存

/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin

# 静态资源强缓存
/static/*
  Cache-Control: public, max-age=31536000, immutable

# HTML 页面禁用缓存（方便更新）
/*.html
  Cache-Control: public, max-age=3600
"""
(SITE_DIR / "_headers").write_text(headers)
log("_headers 已生成")

# wrangler.toml 配置
wrangler_config = """name = "bingbingxiaomei-kb"
compatibility_date = "2026-05-25"
compatibility_flags = ["nodejs_compat"]

# 静态站点部署
pages_build_output_dir = "site"

[env.production]
# 绑定 Workers
workers = [
  { name = "api", service = "api" }
]
"""
(SITE_DIR / "wrangler.toml").write_text(wrangler_config)
log("wrangler.toml 已生成")

# ===== Step 7: 复制 Pages Functions =====
log("复制 Pages Functions...")
functions_src = BASE_DIR / "workers" / "functions"
if functions_src.exists():
    functions_dst = SITE_DIR / "functions"
    if functions_dst.exists():
        shutil.rmtree(functions_dst)
    shutil.copytree(functions_src, functions_dst)
    log(f"已复制 functions/")
else:
    log("functions/ 目录不存在，跳过")

# ===== 完成 =====
print("\n" + "=" * 50)
print("构建完成!")
print(f"  时间: {BUILD_TIMESTAMP}")
print(f"  输出: {SITE_DIR}")
print(f"  统计:")
for key, val in sorted(stats.items()):
    print(f"    {key}: {val}")
total = sum(stats.values())
print(f"    ---")
print(f"    总计: {total} 个资源文件")
print("=" * 50)
