"""
冰冰小美知识库 Flask 应用
"""

from flask import Flask, render_template, abort, request, jsonify, Response, redirect, url_for, session, stream_with_context
from pathlib import Path
from urllib.parse import quote
import markdown
import re
import zipfile
import io
import os
import time
import threading
import unicodedata
from markdown.extensions.tables import TableExtension
from markdown.extensions.fenced_code import FencedCodeExtension

import secrets as _secrets

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _secrets.token_hex(32)

# ===== 访问量计数 =====
VISIT_COUNT_FILE = Path(__file__).parent / "data" / "visit_count.txt"

def _read_visit_count():
    try:
        return int(open(VISIT_COUNT_FILE, encoding="utf-8").read().strip())
    except Exception:
        return 0

def _write_visit_count(n):
    VISIT_COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VISIT_COUNT_FILE, "w", encoding="utf-8") as f:
        f.write(str(n))

@app.before_request
def _count_visit():
    # 只统计页面访问，跳过 static、API、Ajax
    if request.endpoint and request.endpoint != "static":
        path = request.path
        if not path.startswith("/api/") and not path.startswith("/static/"):
            if not session.get("_counted"):
                session["_counted"] = True
                _write_visit_count(_read_visit_count() + 1)

# 部署路径前缀 — 服务器上 Nginx 代理 /kb/ → Flask /
# 通过 WSGI 中间件设 SCRIPT_NAME，让 url_for() 生成 /kb/... 链接
class _PrefixMiddleware:
    def __init__(self, wsgi_app, prefix):
        self.wsgi_app = wsgi_app
        self.prefix = prefix
    def __call__(self, environ, start_response):
        if self.prefix:
            environ["SCRIPT_NAME"] = self.prefix
        return self.wsgi_app(environ, start_response)

_kb_prefix = os.environ.get("KB_APP_ROOT", "")
if _kb_prefix:
    app.wsgi_app = _PrefixMiddleware(app.wsgi_app, _kb_prefix)
    app.config["APPLICATION_ROOT"] = _kb_prefix

# 专栏文章系列分类 — 关键词匹配标题自动打标
# 一篇文章可匹配多个系列，顺序决定 UI 标签排列
COLUMN_SERIES = [
    ("信息的金融意义", {
        "label": "信息的金融意义",
        "match_title": [r"信息的金融意义"],
    }),
    ("月报/定期复盘", {
        "label": "月报/定期复盘",
        "match_title": [r"月报", r"半月报", r"总结", r"季度展望"],
    }),
    ("美国/中美", {
        "label": "美国/中美",
        "match_title": [r"美国", r"美元", r"美联储", r"美帝", r"米股", r"^米$", r"G2", r"新门罗", r"关税", r"锚定美股"],
    }),
    ("AI/科技", {
        "label": "AI/科技",
        "match_title": [r"(?<![a-zA-Z])(?:AI|Ai)(?![a-zA-Z])", r"人工智能", r"智能体", r"科技[^板]"],
        "match_content": [r"人工智能", r"大模型", r"ChatGPT", r"DeepSeek", r"智能体", r"open\s*ai", r"算力"],
    }),
    ("交易方法论", {
        "label": "交易方法论",
        "match_title": [r"交易心理", r"交易理念", r"交易的风控", r"情绪体系", r"情绪交易",
                        r"短线.*情绪标", r"行情不等于风险", r"波动风险", r"风险变化",
                        r"如何选择成长股", r"当风险突然降临", r"收益率.*平常心",
                        r"个股情绪.*整体情绪", r"交易决策", r"风险对冲", r"心智夺取"],
    }),
    ("宏观/经济", {
        "label": "宏观/经济",
        "match_title": [r"宏观", r"流动性", r"时间窗口", r"经济的正向循环",
                        r"资金引", r"顺周期", r"经济危机", r"正向循环",
                        r"竞争格局", r"^循环$", r"货币体系", r"金融中心",
                        r"重大事件"],
    }),
    ("国运/历史/哲学", {
        "label": "国运/历史/哲学",
        "match_title": [r"国运", r"历史[^的]", r"历史.*经济学", r"历史.*危机",
                        r"长期主义", r"时代[,，]", r"成败论英雄",
                        r"泡沫.*历史", r"困境与抉择", r"陷阱与风波",
                        r"视角与战略", r"机遇.*认知", r"风险.*机遇",
                        r"哀其不幸", r"错综复杂", r"历史转折", r"历史的真实"],
    }),
    ("产业/个股", {
        "label": "产业/个股",
        "match_title": [r"^铜(?:[^与及、]|$)", r"铜与铝", r"猪周期", r"华为", r"汽车",
                        r"碳中和", r"商业航天", r"传统产业", r"新兴产业",
                        r"自动化", r"设备周期", r"ETF基金", r"大宗商品",
                        r"产业[^标]", r"金丹圆满", r"金融诱骗", r"白银"],
    }),
]

def _match_column_series(title, content=None):
    """匹配专栏文章的系列标签。返回标签 key 列表。"""
    matched = []
    for key, cfg in COLUMN_SERIES:
        # 标题匹配
        for pattern in cfg.get("match_title", []):
            if re.search(pattern, title):
                matched.append(key)
                break
        else:
            # 标题未命中，尝试正文匹配
            if content and cfg.get("match_content"):
                for pattern in cfg["match_content"]:
                    if re.search(pattern, content):
                        matched.append(key)
                        break
    return matched

# 笔记库根目录
VAULT_ROOT = Path(__file__).parent / "vault"

def _safe_vault_path(relative_path):
    """安全解析 vault 内路径，防止路径遍历攻击。
    返回 resolved Path，如果路径逃逸则返回 None。"""
    candidate = (VAULT_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(VAULT_ROOT.resolve())
    except ValueError:
        return None  # 路径越界
    return candidate
# 网站数据目录（留言板等）
DATA_DIR = Path(__file__).parent / "data"
# 留言板管理密码
GUESTBOOK_ADMIN_KEY = os.environ.get("GUESTBOOK_ADMIN_KEY", "admin")

# 缓存：存储所有文件的链接关系（用于双向链接）
link_graph = {}  # { "目标文件": ["来源文件1", "来源文件2", ...] }


def scan_all_files():
    """扫描所有 markdown 文件，构建链接图"""
    global link_graph
    link_graph = {}

    # VAULT_ROOT 下的子目录是 symlink，rglob 默认不跟随
    # 先遍历顶层目录，再在每个真实目录内 rglob
    for top_dir in VAULT_ROOT.iterdir():
        if not top_dir.is_dir():
            continue
        for md_file in top_dir.rglob("*.md"):
            if md_file.name.startswith("."):
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # 提取所有 wiki-link
            links = re.findall(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]', content)
            for link_target in links:
                # 解析链接目标
                resolved = resolve_link(link_target, md_file)
                if resolved:
                    if resolved not in link_graph:
                        link_graph[resolved] = []
                    if str(md_file.relative_to(VAULT_ROOT)) not in link_graph[resolved]:
                        link_graph[resolved].append(str(md_file.relative_to(VAULT_ROOT)))


def resolve_link(link_target, source_file):
    """解析 wiki-link 目标，返回相对于 vault 的路径"""
    # 处理相对路径
    if link_target.startswith("./") or link_target.startswith("../"):
        # 用 vault 相对路径做纯路径计算，避免 symlink 干扰 ..
        try:
            source_rel_dir = str(source_file.parent.relative_to(VAULT_ROOT))
        except ValueError:
            source_rel_dir = ""

        if source_rel_dir:
            combined = os.path.normpath(os.path.join(source_rel_dir, link_target))
            # 有些概念卡多写了一层 ../（symlink 导致的层级混淆）
            # 如果路径以 ../ 开头说明跑出了 vault，尝试去掉一层 ../ 修正
            while combined.startswith("../") or combined.startswith("..\\"):
                combined = combined[3:] if combined.startswith("../") else combined[3:]
            # 现在用修正后的路径查找文件
            target = VAULT_ROOT / combined
            if target.exists():
                return combined
            target_md = VAULT_ROOT / (combined + ".md")
            if target_md.exists():
                return combined + ".md"

    # 处理绝对路径（从 vault 根目录开始）
    if "/" in link_target:
        # 可能是 "输入/交易体系/情绪交易总篇" 这样的路径
        candidates = [
            VAULT_ROOT / link_target,
            VAULT_ROOT / (link_target + ".md"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate.relative_to(VAULT_ROOT))

    # 处理纯文件名（在同一目录或子目录中搜索）
    # 不直接用 VAULT_ROOT.rglob（不跟随顶层 symlink），改为遍历子目录
    for top_dir in VAULT_ROOT.iterdir():
        if not top_dir.is_dir():
            continue
        for md_file in top_dir.rglob(f"**/{link_target}.md"):
            return str(md_file.relative_to(VAULT_ROOT))
        for md_file in top_dir.rglob(f"**/{link_target}"):
            if md_file.suffix == ".md":
                return str(md_file.relative_to(VAULT_ROOT))

    return None


def parse_wiki_links(html_content, source_file):
    """解析 HTML 中的 wiki-link，转换成可点击的链接"""
    def replace_link(match):
        full_match = match.group(0)
        link_target = match.group(1)
        link_label = match.group(2) if match.group(2) else link_target

        # 解析链接目标
        resolved = resolve_link(link_target, source_file)
        if resolved:
            # 判断是概念节点还是文章
            if "1-核心概念" in resolved and "概念卡片_" in resolved:
                # 概念节点（父卡或子卡）
                node_result = get_node_id_from_path(resolved)
                if node_result:
                    if isinstance(node_result, tuple):
                        # 子卡
                        pid, cid = node_result
                        return f'<a href="/node/{pid}/{cid}" class="wiki-link" data-path="{resolved}">{link_label}</a>'
                    else:
                        # 父卡
                        return f'<a href="/node/{node_result}" class="wiki-link" data-path="{resolved}">{link_label}</a>'
            # 检查是否是帖子路径（输入/贴子、专栏、交易体系、三要素案例）
            if resolved.startswith("输入/贴子/") or resolved.startswith("输入/专栏/") or resolved.startswith("输入/交易体系/") or resolved.startswith("输入/三要素案例/"):
                return f'<a href="/posts/read?path={quote(resolved)}" class="wiki-link post-link" data-path="{resolved}">{link_label}</a>'
            # 文章
            # 确保路径是相对于 vault 的
            if not resolved.startswith("/"):
                article_path = resolved.replace(".md", "")
            else:
                # 如果是绝对路径，尝试转换为相对路径
                try:
                    article_path = str(Path(resolved).relative_to(VAULT_ROOT)).replace(".md", "")
                except ValueError:
                    article_path = resolved.replace(".md", "")
            return f'<a href="/article/{article_path}" class="wiki-link article-link" data-path="{article_path}">{link_label}</a>'
        else:
            # 未找到链接目标
            return f'<span class="wiki-link-unresolved" title="页面未创建: {link_target}">{link_label}</span>'

    # 匹配 [[目标|显示文本]] 或 [[目标]]
    pattern = r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]'
    return re.sub(pattern, replace_link, html_content)


def get_node_id_from_path(file_path):
    """从文件路径获取节点 ID（父卡返回 node_id，子卡返回 (parent_id, child_id)）"""
    # 父卡映射
    node_mapping = {
        "概念卡片_国运": "guoyun",
        "概念卡片_货币与信用周期": "huobi",
        "概念卡片_人性与行为周期": "renxing",
        "概念卡片_竞争格局": "jzgg",
        "概念卡片_流动性辩证分析": "ldx",
        "概念卡片_风险体系": "fxtx",
        "概念卡片_市场参与者": "sccyz",
        "概念卡片_情绪交易体系": "qingxu",
        "概念卡片_三要素联动": "sanyao",
    }

    filename = Path(file_path).stem
    parent_id = node_mapping.get(filename)
    if parent_id:
        return parent_id

    # 子卡：查找 CHILD_CONCEPTS
    relative = str(file_path) if isinstance(file_path, Path) else file_path
    for (pid, cid), info in CHILD_CONCEPTS.items():
        if info["file"] in relative or relative.endswith(info["file"]):
            return (pid, cid)

    # 产业标的：父卡映射
    for iid, ifile in INDUSTRY_FILES.items():
        if ifile in relative or relative.endswith(ifile):
            return ("industry", iid)  # 返回 page_type + id 的元组
    # 产业标的：子卡
    for (pid, cid), info in INDUSTRY_CHILD_CONCEPTS.items():
        if info["file"] in relative or relative.endswith(info["file"]):
            return ("industry", pid, cid)

    # 专题整理：父卡映射
    for tid, tdata in _get_topics().items():
        if tdata["parent_file"] in relative or relative.endswith(tdata["parent_file"]):
            return ("topic", tid)
    # 专题整理：子卡
    for tid, tdata in _get_topics().items():
        for c in tdata["children"]:
            if c["file"] in relative or relative.endswith(c["file"]):
                return ("topic", tid, c["id"])

    return None


def get_core_nodes_nav():
    """构建核心概念级联导航数据（所有页面通用）"""
    nodes = []
    for nid in NODE_ORDER:
        info = get_node_info(nid)
        if info:
            concepts = NODE_CONCEPTS.get(nid, [])
            nodes.append({
                "id": nid,
                "name": info["name"],
                "layer": info["layer"],
                "layer_name": info["layer_name"],
                "concepts": [{"name": c["name"]} for c in concepts],
            })
    return nodes


def get_industry_nodes_nav():
    """构建产业标的级联导航数据（所有页面通用）"""
    nodes = []
    for iid in INDUSTRY_ORDER:
        info = get_industry_info(iid)
        if info:
            children = get_industry_child_concepts_for_parent(iid)
            nodes.append({
                "id": iid,
                "name": info["name"],
                "layer": 0,
                "layer_name": "",
                "concepts": [{"name": c["name"]} for c in children],
            })
    return nodes


def get_topic_nodes_nav():
    """构建专题整理级联导航数据（所有页面通用）"""
    nodes = []
    for tid in _get_topics():
        info = get_topic_info(tid)
        if info:
            children = get_topic_child_concepts_for_parent(tid)
            nodes.append({
                "id": tid,
                "name": info["name"],
                "layer": 0,
                "layer_name": "",
                "concepts": [{"name": c["name"]} for c in children],
            })
    return nodes


def get_node_id_from_dir(dir_name):
    """从目录名获取节点 ID"""
    dir_mapping = {
        "节点1-国运": "guoyun",
        "节点2-货币与信用周期": "huobi",
        "节点3-人性与行为周期": "renxing",
        "节点4-竞争格局": "jzgg",
        "节点5-流动性": "ldx",
        "节点6-风险体系": "fxtx",
        "节点7-市场参与者": "sccyz",
        "节点8-情绪交易体系": "qingxu",
        "节点9-三要素联动": "sanyao",
    }
    return dir_mapping.get(dir_name)


def strip_frontmatter(content):
    """剥离 YAML frontmatter（Obsidian 格式：--- ... ---）"""
    if content.startswith("---"):
        # 找到第二个 ---
        end_idx = content.find("---", 3)
        if end_idx != -1:
            return content[end_idx + 3:].lstrip("\n")
    return content


def extract_card_meta(content):
    """从 markdown 正文提取：冰美签名引用 + 词频数据"""
    import re
    result = {"signature_quote": None, "signature_attr": None, "word_freq": None}

    # 提取词频
    freq_m = re.search(r'全局加权词频[：:]\s*([\d,]+)\s*\|\s*关键文章出现[：:]\s*(\d+)\s*次\s*/\s*(\d+)\s*篇', content)
    if freq_m:
        result["word_freq"] = f"全局加权词频：{freq_m.group(1)} | 关键文章出现：{freq_m.group(2)} 次 / {freq_m.group(3)} 篇"

    # 提取签名引用：第一个 ## 标题后的第一条 blockquote
    h2_match = re.search(r'^##\s+[一二三四五六七八九十、]+.*$', content, re.MULTILINE)
    if h2_match:
        after_h2 = content[h2_match.end():].lstrip('\n')
        bq_match = re.match(r'((?:>\s*.*(?:\n|$))+)', after_h2)
        if bq_match:
            bq_text = bq_match.group(1)
            lines = bq_text.strip().split('\n')
            quote_lines = []
            attr_line = None
            for line in lines:
                stripped = re.sub(r'^>\s*', '', line).strip()
                if stripped.startswith('——') or stripped.startswith('--'):
                    attr_line = stripped.lstrip('——- ').strip()
                elif stripped:
                    quote_lines.append(stripped)
            if quote_lines:
                raw_quote = ' '.join(quote_lines)
                # 用 markdown 渲染 inline（处理 **bold** 等）
                md_inline = markdown.Markdown()
                result["signature_quote"] = md_inline.convert(raw_quote).replace('<p>', '').replace('</p>', '')
            if attr_line:
                result["signature_attr"] = attr_line

    return result


def style_meta_notes(html):
    """将备注类语句包裹为灰色小字（处理 <p> 和 <blockquote> 两种包裹形式）"""
    import re
    patterns = [
        r'判断规则[：:][^<]*',
        r'其余.*文章.*省略[^<]*',
        r'这些表达.*融入[^<]*',
        r'从关键文章共现分析得出[^<]*',
        r'关联强度[：:].*强.*中.*弱[^<]*',
        r'弱关联文章省略不列[^<]*',
        r'生成于[^<]*',
    ]
    for pat in patterns:
        # 1) 在 <blockquote> 中的（Obsidian > 写法），先拆掉 blockquote 改成普通段落（必须在包 span 之前）
        html = re.sub(
            r'<blockquote>\s*<p>(' + pat + r')</p>\s*</blockquote>',
            r'<p><span class="meta-note">\1</span></p>',
            html
        )
        # 2) 已经在 <p> 中的（正常段落），直接包 span
        html = re.sub(
            r'(<p>)(' + pat + r')(</p>)',
            r'\1<span class="meta-note">\2</span>\3',
            html
        )
    return html


def render_markdown(content):
    """渲染 Markdown 为 HTML"""
    content = strip_frontmatter(content)
    md = markdown.Markdown(extensions=[
        TableExtension(),
        FencedCodeExtension(),
        'toc',
        'attr_list',
    ])
    return md.convert(content)


def get_backlinks(file_path):
    """获取反向链接（谁链接到了当前文件）"""
    relative_path = str(file_path.relative_to(VAULT_ROOT))
    backlinks = link_graph.get(relative_path, [])

    result = []
    for bl in backlinks:
        bl_path = VAULT_ROOT / bl
        if bl_path.exists():
            try:
                content = bl_path.read_text(encoding="utf-8")
                # 提取标题
                title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else Path(bl).stem
                result.append({
                    "path": bl,
                    "title": title,
                    "url": get_url_for_path(bl),
                })
            except Exception:
                continue

    return result


def get_url_for_path(file_path):
    """根据文件路径生成 URL（支持父卡和子卡）"""
    result = get_node_id_from_path(file_path)
    if result is None:
        pass
    elif isinstance(result, tuple):
        if len(result) == 3 and result[0] == "industry":
            # 产业标的子卡: ("industry", pid, cid)
            return f"/industry/{result[1]}/{result[2]}"
        elif len(result) == 2 and result[0] == "industry":
            # 产业标的父卡: ("industry", iid)
            return f"/industry/{result[1]}"
        elif len(result) == 3 and result[0] == "topic":
            # 专题整理子卡: ("topic", pid, cid)
            return f"/topic/{result[1]}/{result[2]}"
        elif len(result) == 2 and result[0] == "topic":
            # 专题整理父卡: ("topic", tid)
            return f"/topic/{result[1]}"
        else:
            # 概念子卡: (parent_id, child_id)
            return f"/node/{result[0]}/{result[1]}"
    else:
        # 概念父卡: node_id
        return f"/node/{result}"
    # 检查是否是帖子路径
    if file_path.startswith("输入/贴子/") or file_path.startswith("输入/专栏/") or file_path.startswith("输入/交易体系/") or file_path.startswith("输入/三要素案例/"):
        return f"/posts/read?path={quote(file_path)}"
    return f"/article/{file_path.replace('.md', '')}"


# 节点顺序（用于 prev/next 导航和知识库页面）
NODE_ORDER = ["guoyun", "huobi", "renxing", "jzgg", "ldx", "fxtx", "sccyz", "qingxu", "sanyao"]

# 子概念卡片完整映射：(parent_id, child_id) → {name, file, desc}
# file 路径相对于 vault 根目录
CHILD_CONCEPTS = {
    # === 节点2-货币与信用周期 ===
    ("huobi", "huilv"):              {"name": "汇率", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/概念卡片_汇率.md"},
    ("huobi", "lilv"):               {"name": "利率", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/概念卡片_利率.md"},
    ("huobi", "tongzhang"):          {"name": "通胀/通缩", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/概念卡片_通胀_通缩.md"},
    ("huobi", "zhaiwu"):             {"name": "债务", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/概念卡片_债务.md"},
    ("huobi", "meiyuan-tixi"):       {"name": "美元体系", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/美元体系/概念卡片_美元体系.md"},
    ("huobi", "meiyuan"):            {"name": "美元", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/美元体系/概念卡片_美元.md"},
    ("huobi", "meizhai"):            {"name": "美债", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/美元体系/概念卡片_美债.md"},
    ("huobi", "meilianchu"):         {"name": "美联储", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/美元体系/概念卡片_美联储.md"},
    ("huobi", "renminbi-tixi"):      {"name": "人民币体系", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/人民币体系/概念卡片_人民币体系.md"},
    ("huobi", "renminbi"):           {"name": "人民币", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/人民币体系/概念卡片_人民币.md"},
    ("huobi", "yangma"):             {"name": "央妈", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/人民币体系/概念卡片_央妈.md"},
    ("huobi", "guozhai"):            {"name": "国债", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/人民币体系/概念卡片_国债.md"},
    ("huobi", "zhongyang-jiaganggan"): {"name": "中央加杠杆", "file": "1-核心概念/1-根本决定层/节点2-货币与信用周期/人民币体系/概念卡片_中央加杠杆.md"},
    # === 节点3-人性与行为周期 ===
    ("renxing", "tanlan-kongju"):    {"name": "贪婪与恐惧", "file": "1-核心概念/1-根本决定层/节点3-人性与行为周期/概念卡片_贪婪恐惧.md"},
    ("renxing", "jiaxiang"):         {"name": "假象", "file": "1-核心概念/1-根本决定层/节点3-人性与行为周期/概念卡片_假象.md"},
    ("renxing", "xinxin"):           {"name": "信心", "file": "1-核心概念/1-根本决定层/节点3-人性与行为周期/概念卡片_信心.md"},
    # === 节点4-竞争格局 ===
    ("jzgg", "bijiao-youshi"):       {"name": "比较优势", "file": "1-核心概念/2-核心驱动层/节点4-竞争格局/概念卡片_比较优势.md"},
    ("jzgg", "anquan"):              {"name": "安全", "file": "1-核心概念/2-核心驱动层/节点4-竞争格局/概念卡片_安全.md"},
    ("jzgg", "guoji-boyi"):          {"name": "国际博弈", "file": "1-核心概念/2-核心驱动层/节点4-竞争格局/概念卡片_国际博弈.md"},
    # === 节点5-流动性 ===
    ("ldx", "hongguan-ldx"):         {"name": "宏观流动性", "file": "1-核心概念/2-核心驱动层/节点5-流动性/概念卡片_宏观流动性.md"},
    ("ldx", "zhongguan-ldx"):        {"name": "中观流动性", "file": "1-核心概念/2-核心驱动层/节点5-流动性/概念卡片_中观流动性.md"},
    ("ldx", "weiguan-ldx"):          {"name": "微观流动性", "file": "1-核心概念/2-核心驱动层/节点5-流动性/概念卡片_微观流动性.md"},
    ("ldx", "ldx-yijia"):            {"name": "流动性溢价", "file": "1-核心概念/2-核心驱动层/节点5-流动性/概念卡片_流动性溢价.md"},
    # === 节点6-风险体系 ===
    ("fxtx", "kuiqian-xiaoying"):    {"name": "亏钱效应", "file": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_亏钱效应.md"},
    ("fxtx", "paomo"):               {"name": "泡沫", "file": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_泡沫.md"},
    ("fxtx", "weiji"):               {"name": "危机", "file": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_危机.md"},
    ("fxtx", "zuokong"):             {"name": "做空", "file": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_做空.md"},
    ("fxtx", "touji"):               {"name": "投机", "file": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_投机.md"},
    # === 节点7-市场参与者 ===
    ("sccyz", "youzi"):              {"name": "游资", "file": "1-核心概念/2-核心驱动层/节点7-市场参与者/概念卡片_游资.md"},
    ("sccyz", "lianghua"):           {"name": "量化", "file": "1-核心概念/2-核心驱动层/节点7-市场参与者/概念卡片_量化.md"},
    ("sccyz", "sanhu"):              {"name": "散户", "file": "1-核心概念/2-核心驱动层/节点7-市场参与者/概念卡片_散户.md"},
    ("sccyz", "jigou"):              {"name": "机构", "file": "1-核心概念/2-核心驱动层/节点7-市场参与者/概念卡片_机构.md"},
    # === 节点8-情绪交易体系 ===
    ("qingxu", "zhengqian-xiaoying"): {"name": "挣钱效应", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_挣钱效应.md"},
    ("qingxu", "qingxu-biao"):       {"name": "情绪标", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_情绪标.md"},
    ("qingxu", "qingxu-weizhi"):     {"name": "情绪位置变化", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_情绪位置变化.md"},
    ("qingxu", "qingxu-zhouqi"):     {"name": "情绪周期", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_情绪周期.md"},
    ("qingxu", "jiaoyi-xingwei"):    {"name": "交易行为", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_交易行为.md"},
    ("qingxu", "duichong"):          {"name": "对冲", "file": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_对冲.md"},
}

def get_child_concepts_for_parent(parent_id):
    """获取某父节点下的所有子概念列表（按 CHILD_CONCEPTS 中的顺序）"""
    result = []
    for (pid, cid), info in CHILD_CONCEPTS.items():
        if pid == parent_id:
            result.append({"id": cid, "name": info["name"], "file": info["file"]})
    return result

def get_child_concept(parent_id, child_id):
    """获取单个子概念信息"""
    return CHILD_CONCEPTS.get((parent_id, child_id))


# ===== 产业标的数据结构 =====

INDUSTRY_ORDER = ["fangfalun", "duibiao", "qiche", "youse", "huagong", "ai", "bandaoti", "hangtian", "jinke", "qita"]

INDUSTRY_FILES = {
    "fangfalun": "2-产业标的/概念卡片_产业方法论.md",
    "duibiao": "2-产业标的/概念卡片_全球对标体系.md",
    "qiche": "2-产业标的/01_汽车产业链/概念卡片_汽车产业链.md",
    "youse": "2-产业标的/02_有色与资源/概念卡片_有色与资源.md",
    "huagong": "2-产业标的/03_化工/概念卡片_化工.md",
    "ai": "2-产业标的/04_AI与人工智能/概念卡片_AI与人工智能.md",
    "bandaoti": "2-产业标的/05_半导体与芯片/概念卡片_半导体与芯片.md",
    "hangtian": "2-产业标的/06_商业航天与卫星/概念卡片_商业航天与卫星.md",
    "jinke": "2-产业标的/07_金融科技/概念卡片_金融科技.md",
    "qita": "2-产业标的/08_其他产业/概念卡片_其他产业.md",
}

def get_industry_info(ind_id):
    """获取产业标的节点信息"""
    nodes = {
        "fangfalun": {"name": "产业方法论", "subtitle": "冰美看产业的方法论框架——四阶段划分与四类资金分配方向。"},
        "duibiao": {"name": "全球对标体系", "subtitle": "全球资本市场对标——美股映射与日韩半导体经验借鉴。"},
        "qiche": {"name": "汽车产业链", "subtitle": "新能源汽车产业链全景——比亚迪、赛力斯、宁德时代、江淮汽车。"},
        "youse": {"name": "有色与资源", "subtitle": "有色资源板块——紫金矿业、西部矿业、黄金、白银、石油。"},
        "huagong": {"name": "化工", "subtitle": "化工板块——万华化学为龙头。"},
        "ai": {"name": "AI与人工智能", "subtitle": "AI算力产业链——中际旭创为核心标的。"},
        "bandaoti": {"name": "半导体与芯片", "subtitle": "半导体产业链——中芯国际、长电科技。"},
        "hangtian": {"name": "商业航天与卫星", "subtitle": "商业航天板块——中国卫通、信维通信。"},
        "jinke": {"name": "金融科技", "subtitle": "金融科技与数字资产——比特币为宏观观察窗口。"},
        "qita": {"name": "其他产业", "subtitle": "跨行业标的——隆基绿能、柳工等。"},
    }
    return nodes.get(ind_id)

INDUSTRY_CHILD_CONCEPTS = {
    # 全球对标体系
    ("duibiao", "meigu"): {"name": "美股映射", "file": "2-产业标的/概念卡片_美股映射.md"},
    ("duibiao", "rihan"): {"name": "日韩半导体映射", "file": "2-产业标的/概念卡片_日韩半导体映射.md"},
    # 汽车产业链
    ("qiche", "byd"): {"name": "比亚迪", "file": "2-产业标的/01_汽车产业链/概念卡片_比亚迪.md"},
    ("qiche", "seres"): {"name": "赛力斯", "file": "2-产业标的/01_汽车产业链/概念卡片_赛力斯.md"},
    ("qiche", "catl"): {"name": "宁德时代", "file": "2-产业标的/01_汽车产业链/概念卡片_宁德时代.md"},
    ("qiche", "jac"): {"name": "江淮汽车", "file": "2-产业标的/01_汽车产业链/概念卡片_江淮汽车.md"},
    # 有色与资源
    ("youse", "zijin"): {"name": "紫金矿业", "file": "2-产业标的/02_有色与资源/概念卡片_紫金矿业.md"},
    ("youse", "xibu"): {"name": "西部矿业", "file": "2-产业标的/02_有色与资源/概念卡片_西部矿业.md"},
    ("youse", "huangjin"): {"name": "黄金", "file": "2-产业标的/02_有色与资源/概念卡片_黄金.md"},
    ("youse", "baiyin"): {"name": "白银", "file": "2-产业标的/02_有色与资源/概念卡片_白银.md"},
    ("youse", "shiyou"): {"name": "石油", "file": "2-产业标的/02_有色与资源/概念卡片_石油.md"},
    ("youse", "tong"): {"name": "铜", "file": "2-产业标的/02_有色与资源/概念卡片_铜.md"},
    # 化工
    ("huagong", "wanhua"): {"name": "万华化学", "file": "2-产业标的/03_化工/概念卡片_万华化学.md"},
    # AI与人工智能
    ("ai", "xuchuang"): {"name": "中际旭创", "file": "2-产业标的/04_AI与人工智能/概念卡片_中际旭创.md"},
    # 半导体与芯片
    ("bandaoti", "smic"): {"name": "中芯国际", "file": "2-产业标的/05_半导体与芯片/概念卡片_中芯国际.md"},
    ("bandaoti", "changdian"): {"name": "长电科技", "file": "2-产业标的/05_半导体与芯片/概念卡片_长电科技.md"},
    # 商业航天与卫星
    ("hangtian", "weitong"): {"name": "中国卫通", "file": "2-产业标的/06_商业航天与卫星/概念卡片_中国卫通.md"},
    ("hangtian", "xinwei"): {"name": "信维通信", "file": "2-产业标的/06_商业航天与卫星/概念卡片_信维通信.md"},
    # 金融科技
    ("jinke", "btc"): {"name": "比特币", "file": "2-产业标的/07_金融科技/概念卡片_比特币.md"},
    # 其他产业
    ("qita", "longji"): {"name": "隆基绿能", "file": "2-产业标的/08_其他产业/概念卡片_隆基绿能.md"},
    ("qita", "liugong"): {"name": "柳工", "file": "2-产业标的/08_其他产业/概念卡片_柳工.md"},
}

def get_industry_child_concepts_for_parent(parent_id):
    result = []
    for (pid, cid), info in INDUSTRY_CHILD_CONCEPTS.items():
        if pid == parent_id:
            result.append({"id": cid, "name": info["name"], "file": info["file"]})
    return result

def get_industry_child_concept(parent_id, child_id):
    return INDUSTRY_CHILD_CONCEPTS.get((parent_id, child_id))


# ===== 专题整理数据结构（自动发现） =====

TOPICS_DIR = VAULT_ROOT / "3-专题整理"

# 旧英文 ID → 目录名（向后兼容旧 URL）
LEGACY_TOPIC_IDS = {
    "xinxi": "信息的金融意义",
    "hongguan": "宏观分析框架",
    "kuoqian": "亏钱效应复盘",
    "ai": "冰美看AI",
}


def _discover_topics():
    """自动扫描 3-专题整理/ 目录，发现所有专题。每次调用都重新扫描以保证实时同步。"""
    topics = {}  # topic_id -> {name, dir, parent_file, children, subtitle}

    if not TOPICS_DIR.exists():
        return topics

    for topic_dir in sorted(TOPICS_DIR.iterdir()):
        if not topic_dir.is_dir():
            continue

        dir_name = topic_dir.name

        # 确定 topic_id：优先用旧英文 ID（向后兼容），否则用目录名
        topic_id = None
        for legacy_id, legacy_dir in LEGACY_TOPIC_IDS.items():
            if legacy_dir == dir_name:
                topic_id = legacy_id
                break
        if topic_id is None:
            topic_id = dir_name  # 新专题直接用目录名作为 ID

        # 扫描 .md 文件
        md_files = list(topic_dir.glob("*.md"))
        if not md_files:
            continue

        # 分类：父卡（概念卡片）→ 子卡 → 工具（工作台/操作手册/仪表盘）
        parent_candidates = []
        child_cards = []
        tool_files = []

        for f in md_files:
            name = f.stem
            if any(kw in name for kw in ["工作台", "操作手册", "观察仪表盘"]):
                tool_files.append(f)
            elif name.startswith("概念卡片_"):
                parent_candidates.append(f)
            else:
                child_cards.append(f)

        # 确定父卡：概念卡片中选名称最匹配目录名的
        if not parent_candidates:
            parent_candidates = [md_files[0]]
            child_cards = md_files[1:]

        # 选最佳父卡：名字包含目录核心词的最优先
        best_parent = parent_candidates[0]
        dir_core = dir_name.replace("专题", "").strip()
        for pc in parent_candidates:
            if dir_core in pc.stem:
                best_parent = pc
                break
        parent_file = best_parent

        # 其余概念卡片归入子卡
        for f in parent_candidates:
            if f != parent_file:
                child_cards.append(f)

        # 读父卡获取副标题
        subtitle = ""
        try:
            raw = parent_file.read_text(encoding="utf-8")
            for line in raw.split("\n"):
                if line.startswith("> 定位："):
                    subtitle = line[4:].strip()
                    break
        except Exception:
            pass

        topics[topic_id] = {
            "name": dir_name,
            "dir": topic_dir,
            "parent_file": str(parent_file.relative_to(VAULT_ROOT)),
            "children": [],
            "subtitle": subtitle,
        }

        # 处理子卡
        for f in child_cards:
            child_id = f.stem
            child_name = child_id
            try:
                raw = f.read_text(encoding="utf-8")
                for line in raw.split("\n"):
                    if line.startswith("title: "):
                        child_name = line.split("title: ", 1)[1].strip().strip('"')
                        break
            except Exception:
                pass
            topics[topic_id]["children"].append({
                "id": child_id,
                "name": child_name,
                "file": str(f.relative_to(VAULT_ROOT)),
            })

    return topics


def _get_topics():
    """获取所有专题（每次调用重新扫描，保证实时同步 Obsidian 变更）"""
    return _discover_topics()


def get_topic_info(topic_id):
    """获取专题基本信息"""
    topics = _get_topics()
    if topic_id not in topics:
        return None
    t = topics[topic_id]
    return {"name": t["name"], "subtitle": t["subtitle"]}


def get_topic_child_concepts_for_parent(topic_id):
    """获取某专题的所有子卡"""
    topics = _get_topics()
    if topic_id not in topics:
        return []
    return topics[topic_id]["children"]


def get_topic_child_concept(topic_id, child_id):
    """获取单个子卡信息"""
    children = get_topic_child_concepts_for_parent(topic_id)
    for c in children:
        if c["id"] == child_id:
            return c
    return None

def get_topic_child_concept(parent_id, child_id):
    children = get_topic_child_concepts_for_parent(parent_id)
    for c in children:
        if c["id"] == child_id:
            return c
    return None

NODE_CONCEPTS = {
    "guoyun": [],
    "huobi": [
        {"name": "汇率", "desc": "货币的对外价格，全世界用真金白银投出来的信用票。", "quote": None, "related": ["美元体系", "人民币体系", "利率"]},
        {"name": "美元体系", "desc": "美元霸权框架下的全球信用分配体系。美元+美债+美联储三位一体。", "quote": None, "related": ["美元", "美债", "美联储"]},
        {"name": "美元", "desc": "全球储备货币。\"美元是债务\"——每一张美元都是美联储的负债。", "quote": None, "related": ["美元体系", "汇率", "美债"]},
        {"name": "美债", "desc": "全球资产定价锚，无风险收益率的基准。利率红线4.5%/5%/6%。", "quote": None, "related": ["美元体系", "美联储", "利率"]},
        {"name": "美联储", "desc": "全球流动性的总阀门。加息缩表=全球失血，降息扩表=全球放水。冰美强调跟踪缩表进度＞跟踪利率变化。", "quote": None, "related": ["美元体系", "美债", "宏观流动性"]},
        {"name": "人民币体系", "desc": "中国货币主权架构。人民币+央妈+国债三件套。主权信用型体系。", "quote": None, "related": ["人民币", "央妈", "汇率"]},
        {"name": "人民币", "desc": "主权信用货币。\"人民币是很值钱的\"。锚从美元外汇→长期国债。", "quote": None, "related": ["人民币体系", "央妈", "汇率"]},
        {"name": "央妈", "desc": "中国流动性的总闸门。\"信央妈，信国运，永远赢\"。中观流动性为主，现在宏观引导越来越明显。", "quote": "信央妈，信国运，永远赢。", "related": ["人民币体系", "国债", "中央加杠杆"]},
        {"name": "国债", "desc": "人民币发行的新锚。化债+发展=两只手。国债提供流动性来源。", "quote": None, "related": ["央妈", "利率", "中央加杠杆"]},
        {"name": "中央加杠杆", "desc": "发展模式的根本转变——从居民加杠杆推动房地产，转向中央加杠杆推动新质生产力。押注中央加杠杆不允许失败。", "quote": None, "related": ["国债", "央妈", "通胀/通缩"]},
        {"name": "利率", "desc": "货币的对内价格。实际利率 = 名义利率 - 通胀率。降息不一定利好。", "quote": "实际利率 = 名义利率 - 通胀率。降息不一定利好。", "related": ["通胀/通缩", "债务", "美债"]},
        {"name": "通胀/通缩", "desc": "信用周期的价格信号。通缩比通胀更可怕——通缩意味着债务压力上升。", "quote": None, "related": ["利率", "债务", "央妈"]},
        {"name": "债务", "desc": "信用周期的存量变量。债务不可怕，可怕的是天花板。", "quote": None, "related": ["通胀/通缩", "利率", "中央加杠杆"]},
    ],
    "renxing": [
        {"name": "贪婪与恐惧", "desc": "驱动市场波动的原始情绪力量。短线核心就是引导人性的贪婪与恐惧。恐慌极致=冰点=买点，贪婪极致=高潮=卖点。", "quote": "短线的核心，引导人性的贪婪与恐惧。", "related": ["假象", "信心", "情绪周期"]},
        {"name": "假象", "desc": "市场参与者集体制造的认知偏差，索罗斯反身性的体现。\"假象超过真相就是假象\"。情绪越贪婪假象时间越长，恐惧时假象一天就破灭。", "quote": "假象超过真相就是假象。", "related": ["泡沫", "贪婪与恐惧", "信心"]},
        {"name": "信心", "desc": "心理地基。\"市场不缺流动性，缺信心\"。提振不了市场信心的，大概率就是假象上涨。信心足够→贪婪可以放心启动。", "quote": "市场不缺流动性，缺信心。", "related": ["假象", "贪婪与恐惧", "亏钱效应"]},
    ],
    "jzgg": [
        {"name": "比较优势", "desc": "竞争格局的核心工具。效率vs公平两个方向。七个要素量一个产业。向下比较=交易安全。跟随国运。", "quote": "竞争格局的比较优势 ＞ 流动性辩证分析 ＞ 情绪位置的变化。", "related": ["国际博弈", "安全", "国运"]},
        {"name": "安全", "desc": "约束条件。安全溢价已成为资产定价的重要因子。安全与发展天平：当天平倾向安全→选公平端；当天平倾向发展→选效率端。", "quote": "今年最大的竞争格局是五个字：安全与发展。", "related": ["国际博弈", "比较优势", "国运"]},
        {"name": "国际博弈", "desc": "外部变量。大国竞争格局下的产业与金融博弈。关税在加还是减？制裁在收紧还是放松？冰美以冷战类比框架理解中美AI竞赛。", "quote": "时代的主流就是竞争。", "related": ["比较优势", "安全", "国运"]},
    ],
    "ldx": [
        {"name": "宏观流动性", "desc": "全球资金大环境。美联储加息/降息/缩表=全球阀门。美元周期→新兴市场资金进出。影响所有人和群体。", "quote": "宏观流动性，降息缩表，中性。", "related": ["央妈", "美联储", "中观流动性"]},
        {"name": "中观流动性", "desc": "国内资金格局（为主！）。央妈放水+国债发行+中央加杠杆。M2→M1传导=牛熊根基。中观流动性为主，宏观为辅。这是整个流动性框架的基石。", "quote": "中观流动性为主，宏观流动性为辅。", "related": ["宏观流动性", "微观流动性", "机构"]},
        {"name": "微观流动性", "desc": "场内资金行为。ETF申赎+杠杆融资+基金申赎。游资/散户/量化/机构之间的资金博弈。影响游资与散户。", "quote": None, "related": ["中观流动性", "游资", "情绪标"]},
        {"name": "流动性溢价", "desc": "局部报团的超额收益。三层流动性是土壤，流动性溢价是果实。找到报团最紧的地方——这就是挣钱的入口。", "quote": None, "related": ["微观流动性", "亏钱效应", "危机"]},
    ],
    "fxtx": [
        {"name": "亏钱效应", "desc": "冰美体系的认知起点。全库词频第一（2,527次）。14期亏钱认知系列覆盖所有亏钱模式：预期差、模式失效、化工陷阱、粉丝亏损复盘……先建立完整的亏钱地图。", "quote": "认识亏钱效应是一切交易的开端。避开亏钱的可能性，就是买入不败。", "related": ["做空", "危机", "泡沫"]},
        {"name": "泡沫", "desc": "资产价格脱离基本面的自我强化过程。五次历史泡沫对比。泡沫=流动性危机+投机杠杆。泡沫一定会破灭，但破灭的时机不可预测。", "quote": None, "related": ["假象", "流动性溢价", "危机"]},
        {"name": "危机", "desc": "流动性枯竭引发的系统性风险事件。五类危机机制。\"流动性与情绪\"是危机演绎的两个核心。08年次贷危机深刻塑造了冰美的风险认知。", "quote": None, "related": ["亏钱效应", "做空", "流动性溢价"]},
        {"name": "做空", "desc": "交易前的第一道认知工序。A股完整做空产业链：量化高频、游资砸盘、鼠仓、减持、协议接盘、股指期货做空、融券做空。散户在每个环节都处于劣势。", "quote": "一切交易前，都是做空风险的深度认知。一切交易后，都是流动性情绪溢价的结果。", "related": ["亏钱效应", "对冲", "市场参与者"]},
        {"name": "投机", "desc": "风险体系的加速力量。投机=做空。A股投机根源：T+1+涨停榜+龙虎榜。自媒体流量+投机资金的产业链模式。劣币驱逐良币→3000点难以突破。", "quote": "投机等于做空。投机活跃市场，投资夯实指数信心。", "related": ["泡沫", "做空", "假象", "游资"]},
    ],
    "sccyz": [
        {"name": "游资", "desc": "情绪引导者。分两种流派。核心手法：引导情绪→分歧转一致→制造假象→挡刀。核心目标：争取流动性溢价。", "quote": None, "related": ["情绪标", "微观流动性", "散户"]},
        {"name": "量化", "desc": "程序化交易，助涨助跌的加速器。规模2万亿。毫秒级买卖。量化改变了游资生态——一旦识别亢奋反而无情砸盘。", "quote": None, "related": ["微观流动性", "游资", "做空"]},
        {"name": "散户", "desc": "市场合力最关键的力量。T+1单向交易、信息劣势、工具劣势、制度劣势。冰美给了5条出路：重视亏钱效应、理解行情主力、认清情绪标、清楚套利、关注历史常识。", "quote": None, "related": ["游资", "量化", "做空"]},
        {"name": "机构", "desc": "最为复杂。基金、量化私募、外资、险资、国家队、金融资本——各自不同的资金属性和行为逻辑。基金已成为卖盘/做空主力。", "quote": "基金已经成为卖盘或者做空主力。", "related": ["中观流动性", "竞争格局", "散户"]},
    ],
    "qingxu": [
        {"name": "挣钱效应", "desc": "与亏钱效应相反。挣钱效应扩散→吸引增量资金→推动行情持续。情绪标带动挣钱效应扩散，星星之火可以燎原。", "quote": None, "related": ["亏钱效应", "情绪周期", "情绪标"]},
        {"name": "情绪标", "desc": "市场情绪的晴雨表。以情绪标为观察市场的角度，是本体系的重要特征。标志性个股的涨跌直接反映整体市场情绪。", "quote": None, "related": ["情绪位置变化", "游资", "挣钱效应"]},
        {"name": "情绪位置变化", "desc": "定位地图。三要素的第三要素。冰点↔高潮之间定位当下位置。10种情绪现象。情绪是虚无缥缈的，股价也是假象。", "quote": None, "related": ["情绪周期", "情绪标", "交易行为"]},
        {"name": "情绪周期", "desc": "时间框架。假象的时间形成轮回，就是一个情绪周期。区分螺旋上升 vs 螺旋下降。离开三要素就是伪命题。", "quote": "情绪周期，如果离开流动性辩证分析与竞争格局的比较优势，就是一个伪命题。", "related": ["情绪位置变化", "贪婪与恐惧", "挣钱效应"]},
        {"name": "交易行为", "desc": "执行层。位置→操作一体。分仓/借势/等待/买卖/空仓/管心=六层决策链。15期交易篇全部融入。", "quote": None, "related": ["情绪周期", "情绪位置变化", "亏钱效应"]},
        {"name": "对冲", "desc": "交易层面的操作工具。从风险体系迁移至此。对冲不是消除风险，是用一个头寸保护另一个头寸。仓位管理>对冲，空仓=最高防守。", "quote": None, "related": ["做空", "风险体系", "交易行为"]},
    ],
    "sanyao": [
        {"name": "三要素联动", "desc": "冰美体系的判断操作系统。竞争格局排除错误答案，流动性找到溢价方向，情绪判断动手时机。长中短三条线用同一个框架，但各自选择不同的天平。", "quote": "竞争格局的比较优势 ＞ 流动性辩证分析 ＞ 情绪位置的变化。", "related": ["竞争格局", "流动性", "情绪交易体系"]},
    ],
}

# 产业标的数据（用于知识库页面和级联下拉）
INDUSTRIES = [
    {"id": "fangfalun", "name": "产业方法论", "subs": "四阶段划分 · 四类资金分配方向"},
    {"id": "duibiao", "name": "全球对标体系", "subs": "美股映射 · 日韩半导体映射"},
    {"id": "qiche", "name": "汽车产业链", "subs": "比亚迪 · 赛力斯 · 宁德时代 · 江淮汽车"},
    {"id": "youse", "name": "有色与资源", "subs": "紫金矿业 · 西部矿业 · 黄金 · 白银 · 石油 · 铜"},
    {"id": "huagong", "name": "化工", "subs": "万华化学"},
    {"id": "ai", "name": "AI与人工智能", "subs": "中际旭创"},
    {"id": "bandaoti", "name": "半导体与芯片", "subs": "中芯国际 · 长电科技"},
    {"id": "hangtian", "name": "商业航天与卫星", "subs": "中国卫通 · 信维通信"},
    {"id": "jinke", "name": "金融科技", "subs": "比特币"},
    {"id": "qita", "name": "其他产业", "subs": "隆基绿能 · 柳工"},
]

# 专题整理数据（用于知识库页面和级联下拉）
def _get_topics_list():
    """自动生成 TOPICS 列表（用于知识库页面的专题卡片）"""
    result = []
    topics = _get_topics()
    for tid in topics:
        t = topics[tid]
        info = get_topic_info(tid)
        children = t["children"]
        card_count = 1 + len(children)  # 父卡 + 子卡
        subs = " · ".join(c["name"] for c in children) if children else ""
        desc = info["subtitle"] if info and info["subtitle"] else ""
        if desc and len(desc) > 50:
            desc = desc[:50]
        result.append({
            "id": tid,
            "name": t["name"],
            "desc": desc,
            "subs": subs,
            "card_count": card_count,
        })
    return result


TOPICS = _get_topics_list()

# 层级信息
LAYERS = {
    1: {"name": "根本决定层", "color": "var(--accent)", "className": "l1"},
    2: {"name": "核心驱动层", "color": "var(--accent2)", "className": "l2"},
    3: {"name": "可观测表层", "color": "var(--accent3)", "className": "l3"},
}


# ===== 路由 =====

@app.route("/")
def index():
    """首页"""
    return render_template("index.html",
                           show_home_link=False,
                           current_page="home")


@app.route("/node/<node_id>", defaults={"child_id": None})
@app.route("/node/<node_id>/<child_id>")
def node_page(node_id, child_id):
    """概念节点页面（父卡 + 子卡）"""
    # 节点 ID 到文件路径的映射（父卡）
    node_files = {
        "guoyun": "1-核心概念/1-根本决定层/节点1-国运/概念卡片_国运.md",
        "huobi": "1-核心概念/1-根本决定层/节点2-货币与信用周期/概念卡片_货币与信用周期.md",
        "renxing": "1-核心概念/1-根本决定层/节点3-人性与行为周期/概念卡片_人性与行为周期.md",
        "jzgg": "1-核心概念/2-核心驱动层/节点4-竞争格局/概念卡片_竞争格局.md",
        "ldx": "1-核心概念/2-核心驱动层/节点5-流动性/概念卡片_流动性辩证分析.md",
        "fxtx": "1-核心概念/2-核心驱动层/节点6-风险体系/概念卡片_风险体系.md",
        "sccyz": "1-核心概念/2-核心驱动层/节点7-市场参与者/概念卡片_市场参与者.md",
        "qingxu": "1-核心概念/3-可观测表层/节点8-情绪交易体系/概念卡片_情绪交易体系.md",
        "sanyao": "1-核心概念/3-可观测表层/节点9-三要素联动/概念卡片_三要素联动.md",
    }

    # 节点顺序（用于 prev/next 导航）
    # 使用模块级 NODE_ORDER

    # ===== 子概念卡片 =====
    if child_id:
        child = get_child_concept(node_id, child_id)
        if not child:
            abort(404)
        file_path = VAULT_ROOT / child["file"]
        if not file_path.exists():
            abort(404)

        parent_info = get_node_info(node_id)
        if not parent_info:
            abort(404)

        node_info = {
            "name": child["name"],
            "layer": parent_info["layer"],
            "layer_name": parent_info["layer_name"],
            "subtitle": "",
            "is_child": True,
            "parent_id": node_id,
            "parent_name": parent_info["name"],
        }

        # prev/next 在同级子概念之间
        siblings = get_child_concepts_for_parent(node_id)
        sib_ids = [s["id"] for s in siblings]
        sib_idx = sib_ids.index(child_id) if child_id in sib_ids else -1
        prev_node = None
        next_node = None
        if sib_idx > 0:
            prev_sib = siblings[sib_idx - 1]
            prev_node = {"id": prev_sib["id"], "name": prev_sib["name"], "parent_id": node_id, "is_child": True}
        if sib_idx >= 0 and sib_idx < len(siblings) - 1:
            next_sib = siblings[sib_idx + 1]
            next_node = {"id": next_sib["id"], "name": next_sib["name"], "parent_id": node_id, "is_child": True}

        # 子卡没有子概念卡片
        node_concepts = []

    # ===== 父概念卡片 =====
    else:
        if node_id not in node_files:
            abort(404)

        file_path = VAULT_ROOT / node_files[node_id]
        if not file_path.exists():
            abort(404)

        node_info = get_node_info(node_id)
        if not node_info:
            abort(404)

        # prev / next 在父节点之间
        idx = NODE_ORDER.index(node_id) if node_id in NODE_ORDER else -1
        prev_node = None
        next_node = None
        if idx > 0:
            prev_id = NODE_ORDER[idx - 1]
            prev_node = get_node_info(prev_id)
            prev_node["id"] = prev_id
        if idx >= 0 and idx < len(NODE_ORDER) - 1:
            next_id = NODE_ORDER[idx + 1]
            next_node = get_node_info(next_id)
            next_node["id"] = next_id

        # 获取当前节点的子概念卡片（带 URL + 完整渲染正文）
        node_concepts = []
        raw_concepts = NODE_CONCEPTS.get(node_id, [])
        children = get_child_concepts_for_parent(node_id)
        child_map = {c["name"]: c["id"] for c in children}
        for rc in raw_concepts:
            cid = child_map.get(rc["name"])
            entry = {
                "name": rc["name"],
                "desc": rc["desc"],
                "quote": rc.get("quote"),
                "related": rc.get("related", []),
            }
            if cid:
                entry["url"] = f"/node/{node_id}/{cid}"
                # 读取子卡完整 markdown 并渲染
                child_info = get_child_concept(node_id, cid)
                if child_info:
                    child_file = VAULT_ROOT / child_info["file"]
                    if child_file.exists():
                        try:
                            child_raw = child_file.read_text(encoding="utf-8")
                            child_html = render_markdown(child_raw)
                            child_html = style_meta_notes(child_html)
                            child_html = parse_wiki_links(child_html, child_file)
                            entry["content"] = child_html
                        except Exception:
                            entry["content"] = None
            node_concepts.append(entry)

    # ===== 通用渲染（父卡和子卡共用） =====
    try:
        raw_content = file_path.read_text(encoding="utf-8")
    except Exception:
        abort(500)

    stripped = strip_frontmatter(raw_content)
    card_meta = extract_card_meta(stripped)
    html_content = render_markdown(raw_content)
    html_content = style_meta_notes(html_content)
    html_content = parse_wiki_links(html_content, file_path)
    backlinks = []

    # 级联下拉导航数据
    all_nodes_for_nav = get_core_nodes_nav()

    return render_template("node.html",
                           page_type="node",
                           node_id=node_id,
                           child_id=child_id,
                           node_info=node_info,
                           content=html_content,
                           card_meta=card_meta,

                           prev_node=prev_node,
                           next_node=next_node,
                           node_concepts=node_concepts,
                           all_nodes_for_nav=all_nodes_for_nav,
                           core_nodes_for_nav=get_core_nodes_nav(),
                           ind_nodes_for_nav=get_industry_nodes_nav(),
                           topic_nodes_for_nav=get_topic_nodes_nav(),
                           industries=INDUSTRIES,
                           topics=_get_topics_list(),
                           layers=LAYERS,
                           show_home_link=True,
                           is_first_in_section=(not child_id and node_id == NODE_ORDER[0]),
                           current_page="knowledge_base")


@app.route("/industry/<industry_id>", defaults={"child_id": None})
@app.route("/industry/<industry_id>/<child_id>")
def industry_page(industry_id, child_id):
    """产业标的页面（父卡 + 子卡）"""
    # ===== 子卡 =====
    if child_id:
        child = get_industry_child_concept(industry_id, child_id)
        if not child:
            abort(404)
        file_path = VAULT_ROOT / child["file"]
        if not file_path.exists():
            abort(404)

        parent_info = get_industry_info(industry_id)
        if not parent_info:
            abort(404)

        node_info = {
            "name": child["name"],
            "layer": 0,
            "layer_name": "",
            "subtitle": "",
            "is_child": True,
            "parent_id": industry_id,
            "parent_name": parent_info["name"],
        }

        siblings = get_industry_child_concepts_for_parent(industry_id)
        sib_ids = [s["id"] for s in siblings]
        sib_idx = sib_ids.index(child_id) if child_id in sib_ids else -1
        prev_node = None
        next_node = None
        if sib_idx > 0:
            prev_sib = siblings[sib_idx - 1]
            prev_node = {"id": prev_sib["id"], "name": prev_sib["name"], "parent_id": industry_id, "is_child": True}
        if sib_idx >= 0 and sib_idx < len(siblings) - 1:
            next_sib = siblings[sib_idx + 1]
            next_node = {"id": next_sib["id"], "name": next_sib["name"], "parent_id": industry_id, "is_child": True}

        node_concepts = []

    # ===== 父卡 =====
    else:
        if industry_id not in INDUSTRY_FILES:
            abort(404)

        file_path = VAULT_ROOT / INDUSTRY_FILES[industry_id]
        if not file_path.exists():
            abort(404)

        node_info = get_industry_info(industry_id)
        if not node_info:
            abort(404)
        node_info["layer"] = 0
        node_info["layer_name"] = ""

        idx = INDUSTRY_ORDER.index(industry_id) if industry_id in INDUSTRY_ORDER else -1
        prev_node = None
        next_node = None
        if idx > 0:
            prev_id = INDUSTRY_ORDER[idx - 1]
            prev_info = get_industry_info(prev_id)
            prev_node = {"id": prev_id, "name": prev_info["name"]}
        if idx >= 0 and idx < len(INDUSTRY_ORDER) - 1:
            next_id = INDUSTRY_ORDER[idx + 1]
            next_info = get_industry_info(next_id)
            next_node = {"id": next_id, "name": next_info["name"]}

        node_concepts = []
        children = get_industry_child_concepts_for_parent(industry_id)
        for c in children:
            entry = {
                "name": c["name"],
                "desc": "",
            }
            child_info = get_industry_child_concept(industry_id, c["id"])
            if child_info:
                entry["url"] = f"/industry/{industry_id}/{c['id']}"
                child_file = VAULT_ROOT / child_info["file"]
                if child_file.exists():
                    try:
                        child_raw = child_file.read_text(encoding="utf-8")
                        child_html = render_markdown(child_raw)
                        child_html = style_meta_notes(child_html)
                        child_html = parse_wiki_links(child_html, child_file)
                        entry["content"] = child_html
                    except Exception:
                        entry["content"] = None
            node_concepts.append(entry)

    # ===== 通用渲染 =====
    try:
        raw_content = file_path.read_text(encoding="utf-8")
    except Exception:
        abort(500)

    stripped = strip_frontmatter(raw_content)
    card_meta = extract_card_meta(stripped)
    html_content = render_markdown(raw_content)
    html_content = style_meta_notes(html_content)
    html_content = parse_wiki_links(html_content, file_path)
    backlinks = []

    all_ind_for_nav = get_industry_nodes_nav()

    return render_template("node.html",
                           page_type="industry",
                           node_id=industry_id,
                           child_id=child_id,
                           node_info=node_info,
                           content=html_content,
                           card_meta=card_meta,

                           prev_node=prev_node,
                           next_node=next_node,
                           node_concepts=node_concepts,
                           all_nodes_for_nav=all_ind_for_nav,
                           core_nodes_for_nav=get_core_nodes_nav(),
                           ind_nodes_for_nav=get_industry_nodes_nav(),
                           topic_nodes_for_nav=get_topic_nodes_nav(),
                           industries=INDUSTRIES,
                           topics=_get_topics_list(),
                           layers=LAYERS,
                           show_home_link=True,
                           is_first_in_section=(not child_id and industry_id == INDUSTRY_ORDER[0]),
                           current_page="knowledge_base")


@app.route("/topic/<topic_id>", defaults={"child_id": None})
@app.route("/topic/<topic_id>/<child_id>")
def topic_page(topic_id, child_id):
    """专题整理页面（父卡 + 子卡）"""
    # ===== 子卡 =====
    if child_id:
        child = get_topic_child_concept(topic_id, child_id)
        if not child:
            abort(404)
        file_path = VAULT_ROOT / child["file"]
        if not file_path.exists():
            abort(404)

        parent_info = get_topic_info(topic_id)
        if not parent_info:
            abort(404)

        node_info = {
            "name": child["name"],
            "layer": 0,
            "layer_name": "",
            "subtitle": "",
            "is_child": True,
            "parent_id": topic_id,
            "parent_name": parent_info["name"],
        }

        siblings = get_topic_child_concepts_for_parent(topic_id)
        sib_ids = [s["id"] for s in siblings]
        sib_idx = sib_ids.index(child_id) if child_id in sib_ids else -1
        prev_node = None
        next_node = None
        if sib_idx > 0:
            prev_sib = siblings[sib_idx - 1]
            prev_node = {"id": prev_sib["id"], "name": prev_sib["name"], "parent_id": topic_id, "is_child": True}
        if sib_idx >= 0 and sib_idx < len(siblings) - 1:
            next_sib = siblings[sib_idx + 1]
            next_node = {"id": next_sib["id"], "name": next_sib["name"], "parent_id": topic_id, "is_child": True}

        node_concepts = []

    # ===== 父卡 =====
    else:
        topics = _get_topics()
        if topic_id not in topics:
            abort(404)

        file_path = VAULT_ROOT / topics[topic_id]["parent_file"]
        if not file_path.exists():
            abort(404)

        node_info = get_topic_info(topic_id)
        if not node_info:
            abort(404)
        node_info["layer"] = 0
        node_info["layer_name"] = ""

        topic_order = list(topics.keys())
        idx = topic_order.index(topic_id) if topic_id in topic_order else -1
        prev_node = None
        next_node = None
        if idx > 0:
            prev_id = topic_order[idx - 1]
            prev_info = get_topic_info(prev_id)
            prev_node = {"id": prev_id, "name": prev_info["name"]}
        if idx >= 0 and idx < len(topic_order) - 1:
            next_id = topic_order[idx + 1]
            next_info = get_topic_info(next_id)
            next_node = {"id": next_id, "name": next_info["name"]}

        node_concepts = []
        children = get_topic_child_concepts_for_parent(topic_id)
        for c in children:
            entry = {
                "name": c["name"],
                "desc": "",
            }
            child_info = get_topic_child_concept(topic_id, c["id"])
            if child_info:
                entry["url"] = f"/topic/{topic_id}/{c['id']}"
                child_file = VAULT_ROOT / child_info["file"]
                if child_file.exists():
                    try:
                        child_raw = child_file.read_text(encoding="utf-8")
                        child_html = render_markdown(child_raw)
                        child_html = style_meta_notes(child_html)
                        child_html = parse_wiki_links(child_html, child_file)
                        entry["content"] = child_html
                    except Exception:
                        entry["content"] = None
            node_concepts.append(entry)

    # ===== 通用渲染 =====
    try:
        raw_content = file_path.read_text(encoding="utf-8")
    except Exception:
        abort(500)

    stripped = strip_frontmatter(raw_content)
    card_meta = extract_card_meta(stripped)
    html_content = render_markdown(raw_content)
    html_content = style_meta_notes(html_content)
    html_content = parse_wiki_links(html_content, file_path)
    backlinks = []

    all_topics_for_nav = get_topic_nodes_nav()

    return render_template("node.html",
                           page_type="topic",
                           node_id=topic_id,
                           child_id=child_id,
                           node_info=node_info,
                           content=html_content,
                           card_meta=card_meta,

                           prev_node=prev_node,
                           next_node=next_node,
                           node_concepts=node_concepts,
                           all_nodes_for_nav=all_topics_for_nav,
                           core_nodes_for_nav=get_core_nodes_nav(),
                           ind_nodes_for_nav=get_industry_nodes_nav(),
                           topic_nodes_for_nav=get_topic_nodes_nav(),
                           industries=INDUSTRIES,
                           topics=_get_topics_list(),
                           layers=LAYERS,
                           show_home_link=True,
                           is_first_in_section=(not child_id and topic_id == list(_get_topics().keys())[0] if _get_topics() else False),
                           current_page="knowledge_base")


@app.route("/article/<path:article_path>")
def article_page(article_path):
    """文章页面"""
    file_path = _safe_vault_path(article_path if article_path.endswith(".md") else article_path + ".md")
    if not file_path or not file_path.exists():
        abort(404)

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        abort(500)

    # 渲染 Markdown
    html_content = render_markdown(content)

    # 解析 wiki-link
    html_content = parse_wiki_links(html_content, file_path)

    # 获取反向链接
    backlinks = []

    # 提取标题
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_match.group(1) if title_match else Path(article_path).stem

    return render_template("article.html",
                           title=title,
                           content=html_content,

                           article_path=article_path,
                           source_path=str(file_path.relative_to(VAULT_ROOT)),
                           show_home_link=True,
                           current_page="posts")


@app.route("/api/preview/<path:article_path>")
def preview_article(article_path):
    """API：获取文章预览（用于浮窗）"""
    file_path = _safe_vault_path(article_path if article_path.endswith(".md") else article_path + ".md")
    if not file_path or not file_path.exists():
        return jsonify({"error": "文件不存在"}), 404

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return jsonify({"error": "读取失败"}), 500

    # 渲染 Markdown
    html_content = render_markdown(content)

    # 解析 wiki-link
    html_content = parse_wiki_links(html_content, file_path)

    # 提取标题
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_match.group(1) if title_match else Path(article_path).stem

    # 截取前 2000 字符作为预览
    preview_content = html_content[:2000]
    if len(html_content) > 2000:
        preview_content += "..."

    # 根据路径类型返回正确的 full_url
    vault_rel = str(file_path.relative_to(VAULT_ROOT))
    if vault_rel.startswith("输入/贴子/") or vault_rel.startswith("输入/专栏/") or vault_rel.startswith("输入/交易体系/") or vault_rel.startswith("输入/三要素案例/"):
        from urllib.parse import quote
        full_url = f"/posts/read?path={quote(vault_rel)}"
    else:
        full_url = f"/article/{vault_rel.replace('.md', '')}"

    return jsonify({
        "title": title,
        "content": preview_content,
        "full_url": full_url,
    })


@app.route("/knowledge-base")
def knowledge_base():
    """知识库总览页面"""
    nodes = []
    for node_id in NODE_ORDER:
        info = get_node_info(node_id)
        if info:
            info["id"] = node_id
            nodes.append(info)
    return render_template("knowledge_base.html",
                           nodes=nodes,
                           show_home_link=True,
                           current_page="knowledge_base")


@app.route("/knowledge-base/export")
def knowledge_base_export():
    """导出全部知识库内容为 zip 压缩包，保留原始目录结构"""
    from datetime import datetime

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
                # 保持原始相对路径（相对于知识库根目录）
                arcname = str(md_file.relative_to(VAULT_ROOT))
                zf.writestr(arcname, content)

    buf.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=bingbingxiaomei_knowledge_base_{timestamp}.zip"}
    )


@app.route("/search")
def search():
    """搜索页面"""
    query = request.args.get("q", "").strip()
    if not query:
        return render_template("search.html", query="", results=[])

    results = []
    for top_dir in VAULT_ROOT.iterdir():
        if not top_dir.is_dir():
            continue
        for md_file in top_dir.rglob("*.md"):
            if md_file.name.startswith("."):
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            if query.lower() in content.lower():
                # 提取标题
                title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                title = title_match.group(1) if title_match else md_file.stem

                # 提取匹配的上下文
                lines = content.split("\n")
                context = ""
                for line in lines:
                    if query.lower() in line.lower():
                        context = line[:100]
                        break

                results.append({
                    "path": str(md_file.relative_to(VAULT_ROOT)),
                    "title": title,
                    "context": context,
                    "url": get_url_for_path(str(md_file.relative_to(VAULT_ROOT))),
                })

    return render_template("search.html", query=query, results=results)


@app.route("/guestbook", methods=["GET", "POST"])
def guestbook():
    """留言板"""
    import json
    from datetime import datetime, timezone, timedelta
    cst = timezone(timedelta(hours=8))

    guestbook_file = DATA_DIR / "guestbook.json"
    messages = []
    if guestbook_file.exists():
        try:
            messages = json.loads(guestbook_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            messages = []

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            import uuid
            msg = {
                "id": uuid.uuid4().hex[:8],
                "time": datetime.now(cst).strftime("%Y-%m-%d %H:%M"),
                "content": content
            }
            messages.append(msg)
            guestbook_file.parent.mkdir(parents=True, exist_ok=True)
            guestbook_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        return redirect(url_for("guestbook"))

    return render_template("guestbook.html",
                           show_home_link=True,
                           current_page="guestbook",
                           messages=list(reversed(messages)),
                           is_admin=session.get("is_admin", False))


@app.route("/guestbook/delete/<msg_id>", methods=["POST"])
def guestbook_delete(msg_id):
    """删除留言（需管理员登录）"""
    if not session.get("is_admin"):
        return "请先登录管理后台", 403
    import json
    guestbook_file = DATA_DIR / "guestbook.json"
    messages = []
    if guestbook_file.exists():
        try:
            messages = json.loads(guestbook_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            messages = []
    messages = [m for m in messages if m.get("id") != msg_id]
    guestbook_file.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    return redirect(url_for("guestbook"))


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    """管理员登录"""
    error = ""
    if request.method == "POST":
        if request.form.get("password") == GUESTBOOK_ADMIN_KEY:
            session["is_admin"] = True
            return redirect(url_for("guestbook"))
        error = "密码错误"
    return render_template("admin_login.html",
                           show_home_link=True,
                           error=error)


@app.route("/admin/logout")
def admin_logout():
    """退出管理"""
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/about")
def about():
    """关于本站 & 作者"""
    return render_template("about.html",
                           show_home_link=True,
                           current_page="about")


@app.route("/changelog")
def changelog():
    """更新日志 - 已合并到关于本站"""
    return redirect(url_for("about"))


@app.route("/ai")
def chat_page():
    """AI 冰美聊天页面"""
    return render_template("chat.html",
                           show_home_link=True,
                           current_page="chat")


# 简易内存速率限制（AI 聊天接口）
_chat_rate = {}  # {ip: [timestamp, ...]}
_CHAT_RATE_LIMIT = 10   # 每分钟最多请求数
_CHAT_RATE_WINDOW = 60  # 窗口秒数
_CHAT_MAX_QUESTION_LEN = 2000  # 问题最大字符数

def _check_chat_rate(ip):
    """返回 True 表示未超限，False 表示超限"""
    now = time.time()
    if ip not in _chat_rate:
        _chat_rate[ip] = []
    # 清理过期记录
    _chat_rate[ip] = [t for t in _chat_rate[ip] if now - t < _CHAT_RATE_WINDOW]
    if len(_chat_rate[ip]) >= _CHAT_RATE_LIMIT:
        return False
    _chat_rate[ip].append(now)
    # 定期清理整个字典（最多保留10000个IP）
    if len(_chat_rate) > 10000:
        for k in list(_chat_rate.keys()):
            _chat_rate[k] = [t for t in _chat_rate[k] if now - t < _CHAT_RATE_WINDOW]
            if not _chat_rate[k]:
                del _chat_rate[k]
    return True

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """AI 冰美 SSE 聊天接口"""
    # 速率限制
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    if not _check_chat_rate(client_ip):
        return jsonify({"error": "请求太频繁，请稍后再试"}), 429

    from src.rag.chat import generate_stream
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "请求格式错误"}), 400
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if len(question) > _CHAT_MAX_QUESTION_LEN:
        return jsonify({"error": f"问题过长，最多{_CHAT_MAX_QUESTION_LEN}字符"}), 400

    def stream():
        for event in generate_stream(question):
            yield event

    return Response(stream_with_context(stream()),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    })


def get_node_info(node_id):
    """获取节点信息"""
    nodes = {
        "guoyun": {
            "name": "国运",
            "layer": 1,
            "layer_name": "根本决定层",
            "subtitle": "冰美体系的最高层天花板。投资是基于国运红利的分配史，投机是基于人性扭曲的封建史。",
        },
        "huobi": {
            "name": "货币与信用周期",
            "layer": 1,
            "layer_name": "根本决定层",
            "subtitle": "钱的总闸门，三要素中流动性的根本决定层。货币是股市的灵魂——信央妈，信国运，永远赢。",
        },
        "renxing": {
            "name": "人性与行为周期",
            "layer": 1,
            "layer_name": "根本决定层",
            "subtitle": "驱动市场波动的原始力量。人性不变，周期永存——贪婪与恐惧的钟摆在每个市场反复摆动。",
        },
        "jzgg": {
            "name": "竞争格局",
            "layer": 2,
            "layer_name": "核心驱动层",
            "subtitle": "冰美体系的第一要素，三要素之首。竞争格局的比较优势决定方向——先回答'这个方向会不会死'，再谈其他。",
        },
        "ldx": {
            "name": "流动性",
            "layer": 2,
            "layer_name": "核心驱动层",
            "subtitle": "冰美体系的第二要素，方向仪。钱在不在往这边流？流动性三层模型：宏观 → 中观 → 微观。",
        },
        "fxtx": {
            "name": "风险体系",
            "layer": 2,
            "layer_name": "核心驱动层",
            "subtitle": "冰美体系的风险认知层，管底线。认识亏钱效应是一切交易的开端——交易前的第一道认知工序。",
        },
        "sccyz": {
            "name": "市场参与者",
            "layer": 2,
            "layer_name": "核心驱动层",
            "subtitle": "市场是由人组成的。不同参与者的行为模式、资金属性、时间维度决定了市场的分层结构。",
        },
        "qingxu": {
            "name": "情绪交易体系",
            "layer": 3,
            "layer_name": "可观测表层",
            "subtitle": "冰美体系的日常操作层。回答三个问题：什么时候买？买什么？怎么买？情绪位置决定胜负概率。",
        },
        "sanyao": {
            "name": "三要素联动",
            "layer": 3,
            "layer_name": "可观测表层",
            "subtitle": "冰美体系的判断操作系统。每天打开盘面第一步看什么、第二步看什么。三层滤网逐级传导，决定行情方向与节奏。",
        },
    }
    return nodes.get(node_id)


@app.context_processor
def inject_globals():
    """注入全局变量到模板"""
    return {
        "visit_count": _read_visit_count(),
        "vault_root": VAULT_ROOT,
        "node_list": [
            {"id": "guoyun", "name": "国运", "layer": 1},
            {"id": "huobi", "name": "货币与信用周期", "layer": 1},
            {"id": "renxing", "name": "人性与行为周期", "layer": 1},
            {"id": "jzgg", "name": "竞争格局", "layer": 2},
            {"id": "ldx", "name": "流动性", "layer": 2},
            {"id": "fxtx", "name": "风险体系", "layer": 2},
            {"id": "sccyz", "name": "市场参与者", "layer": 2},
            {"id": "qingxu", "name": "情绪交易体系", "layer": 3},
            {"id": "sanyao", "name": "三要素联动", "layer": 3},
        ],
        "industries": INDUSTRIES,
        "topics": TOPICS,
        "layers": LAYERS,
    }


# ===== 帖子系统 =====
import yaml
import random as random_mod
from datetime import datetime

POSTS_DIR = VAULT_ROOT / "输入" / "贴子"
COLUMN_DIR = VAULT_ROOT / "输入" / "专栏"
TRADE_SYS_DIR = VAULT_ROOT / "输入" / "交易体系"
THREE_ELEM_DIR = VAULT_ROOT / "输入" / "三要素案例"
PINNED_POST = "输入/贴子/帖子_290584274_2024-05-19_13-51-20.md"
WORD_FREQ_FILE = VAULT_ROOT / ".." / ".." / ".." / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / "想想冰美怎么做" / "知识体系 DeepSeek 生成" / "知识体系" / "3-专题整理" / "概念词频与高频表达.md"
CORE_ARTICLE_FILE = VAULT_ROOT / ".." / ".." / ".." / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / "想想冰美怎么做" / "知识体系 DeepSeek 生成" / "知识体系" / "3-专题整理" / "核心文章目录.md"

# 直接读取词频和核心文章文件（绕过symlink问题）
import os as _os
_obsidian_base = Path(_os.path.expanduser("~/Library/Mobile Documents/iCloud~md~obsidian/Documents/想想冰美怎么做"))
WORD_FREQ_FILE = _obsidian_base / "知识体系 DeepSeek 生成/知识体系/3-专题整理/概念词频与高频表达.md"
CORE_ARTICLE_FILE = _obsidian_base / "知识体系 DeepSeek 生成/知识体系/3-专题整理/核心文章目录.md"

# 帖子元数据缓存
post_index = []          # 全部帖子 [{filepath, date, snippet, tags, char_count}]
core_article_pool = []   # 核心文章池（filepath列表）
pinned_post_data = None  # 置顶帖渲染后的HTML
post_index_built = False


def build_post_index():
    """扫描所有帖子目录，构建索引缓存"""
    global post_index, core_article_pool, pinned_post_data, post_index_built

    if post_index_built:
        return

    print("正在扫描帖子目录...")

    # 第一步：扫描 输入/贴子/ 的所有帖子
    if POSTS_DIR.exists():
        for post_file in POSTS_DIR.glob("*.md"):
            meta = _parse_post_meta(post_file)
            if meta:
                post_index.append(meta)

    # 按日期降序排列
    post_index.sort(key=lambda p: p.get("date_sort", ""), reverse=True)
    print(f"  全部帖子: {len(post_index)} 篇")

    # 第二步：构建核心文章池
    core_set = set()

    # 专栏（101篇）
    if COLUMN_DIR.exists():
        for f in COLUMN_DIR.glob("*.md"):
            core_set.add(str(f.relative_to(VAULT_ROOT)))

    # 交易体系（20篇）
    if TRADE_SYS_DIR.exists():
        for f in TRADE_SYS_DIR.glob("*.md"):
            core_set.add(str(f.relative_to(VAULT_ROOT)))

    # 三要素案例（42篇）
    if THREE_ELEM_DIR.exists():
        for f in THREE_ELEM_DIR.glob("*.md"):
            core_set.add(str(f.relative_to(VAULT_ROOT)))

    # 置顶帖
    pinned_path = VAULT_ROOT / PINNED_POST
    if pinned_path.exists():
        core_set.add(PINNED_POST)

    # 深度帖子（字数第一档）
    for p in post_index:
        tags = p.get("tags", [])
        if "字数第一档" in tags or "#字数第一档" in tags:
            key = str(p["filepath"])
            if key not in core_set:
                core_set.add(key)

    core_article_pool = list(core_set)
    print(f"  核心文章: {len(core_article_pool)} 篇")

    # 第三步：预渲染置顶帖
    pinned_path = VAULT_ROOT / PINNED_POST
    if pinned_path.exists():
        try:
            raw = pinned_path.read_text(encoding="utf-8")
            html_out = parse_wiki_links(render_markdown(raw), pinned_path)
            pinned_post_data = {
                "title": "置顶 · 危机，变局，与新生的中国",
                "date": "2024.05.19 13:51 · 深圳欢乐海岸",
                "html": html_out,
                "source": "置顶帖 · #字数第一档",
            }
        except Exception:
            pinned_post_data = None

    post_index_built = True


def _parse_post_meta(filepath):
    """解析单篇帖子的元数据"""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    # 解析 frontmatter
    frontmatter = {}
    fm_raw_text = ""  # 保留原始 YAML 文本，用于 YAML 解析失败时兜底提取 title
    body_start = 0
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_raw_text = parts[1]
            try:
                frontmatter = yaml.safe_load(fm_raw_text) or {}
            except Exception:
                pass
            body_start = len(parts[0]) + len(parts[1]) + 6

    body = content[body_start:].strip()

    # 提取日期
    date_str = ""
    date_sort = ""
    published = frontmatter.get("published")
    created = frontmatter.get("created")

    if published:
        if isinstance(published, datetime):
            date_str = published.strftime("%Y.%m.%d %H:%M")
            date_sort = published.strftime("%Y%m%d%H%M")
        elif isinstance(published, str):
            date_str = published[:16] if len(published) >= 16 else published
            date_sort = published.replace("-", "").replace(" ", "").replace(":", "")[:12]
    elif created:
        if isinstance(created, str):
            date_str = created
            date_sort = created.replace("-", "") + "0000"
    else:
        # 从文件名提取
        fname = filepath.stem
        match = re.search(r'(\d{4})[-_](\d{2})[-_](\d{2})', fname)
        if match:
            date_str = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
            date_sort = f"{match.group(1)}{match.group(2)}{match.group(3)}0000"

    # 文件名也没日期的话，从正文提取 "发布时间"（交易体系/三要素案例文件专用）
    if not date_str:
        pub_match = re.search(r'发布时间[：:]\s*(\d{4})[-/](\d{2})[-/](\d{2})\s*(\d{2}):(\d{2})', body)
        if pub_match:
            date_str = f"{pub_match.group(1)}.{pub_match.group(2)}.{pub_match.group(3)} {pub_match.group(4)}:{pub_match.group(5)}"
            date_sort = f"{pub_match.group(1)}{pub_match.group(2)}{pub_match.group(3)}{pub_match.group(4)}{pub_match.group(5)}"

    # 提取摘要（保留段落呼吸感，生成HTML片段）
    body_clean = _clean_post_markdown(body)
    # 去掉残留的 html break 标签用于纯文本snippet
    body_clean = re.sub(r'<br\s*/?>', ' ', body_clean)
    body_clean = re.sub(r'^#\s+.*$', '', body_clean, flags=re.MULTILINE)
    lines = [l.strip() for l in body_clean.split('\n') if l.strip() and not l.strip().startswith('|') and not l.strip().startswith('>')]
    # 取前几段
    snippet_parts = []
    total_len = 0
    for line in lines:
        if total_len > 200:
            break
        snippet_parts.append(line)
        total_len += len(line)
    snippet = '<br>'.join(snippet_parts)
    if len(body_clean) > 200:
        snippet += "..."

    # 标签
    tags = frontmatter.get("tags", [])
    if tags is None:
        tags = []
    if isinstance(tags, str):
        tags = [tags]

    # 字数
    char_count = len(body_clean)

    # 标题：优先 frontmatter title → h1 → 文件名 → 正文首句
    fm_title = frontmatter.get("title", "")
    if fm_title and isinstance(fm_title, str) and fm_title.strip() and fm_title.strip() != "冰冰小美":
        title = fm_title.strip()
    else:
        # YAML 解析失败时，用正则从 frontmatter 文本兜底提取 title
        fm_re_match = re.search(r'^title:\s*"?(.+?)"?$', fm_raw_text, re.MULTILINE) if fm_raw_text else None
        if fm_re_match:
            raw_t = fm_re_match.group(1).strip()
            if raw_t and raw_t not in ('"', '""') and raw_t != "冰冰小美":
                title = raw_t
            else:
                title = _extract_post_title(content, str(filepath.relative_to(VAULT_ROOT)))
        else:
            title = _extract_post_title(content, str(filepath.relative_to(VAULT_ROOT)))
    # 统一 NFKC 规范化（CJK 兼容表意文字 → 标准形式）
    title = unicodedata.normalize('NFKC', title)
    # NFKC 不覆盖 CJK 部首补充字符（U+2E80–U+2EFF），手动替换
    _cjk_rad_map = str.maketrans({
        '\u2edb': '风', '\u2ec6': '角', '\u2ed3': '长', '\u2ed4': '门',
        '\u2ed8': '马', '\u2edc': '飞', '\u2ed0': '车', '\u2ecf': '贝',
        '\u2ed9': '鱼', '\u2ed1': '见', '\u2ec9': '鸟', '\u2ecc': '龙',
    })
    title = title.translate(_cjk_rad_map)

    return {
        "filepath": str(filepath.relative_to(VAULT_ROOT)),
        "date": date_str,
        "date_sort": date_sort,
        "snippet": snippet,
        "tags": tags,
        "char_count": char_count,
        "title": title,
        "raw_body": body,
    }


def get_post_html(filepath_str):
    """读取并渲染单篇帖子的完整HTML"""
    filepath = _safe_vault_path(filepath_str)
    if not filepath or not filepath.exists():
        return None

    try:
        raw = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    # 先清理原始内容再渲染
    cleaned = _clean_post_markdown(raw)
    html = render_markdown(cleaned)
    html = parse_wiki_links(html, filepath)
    html = _clean_post_html(html)

    # 提取元数据
    frontmatter = {}
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except Exception:
                pass

    published = frontmatter.get("published")
    created = frontmatter.get("created")
    date_str = ""
    if published:
        if isinstance(published, datetime):
            date_str = published.strftime("%Y.%m.%d %H:%M")
        elif isinstance(published, str):
            date_str = published[:16]
    elif created:
        if isinstance(created, str):
            date_str = created

    tags = frontmatter.get("tags", [])
    if tags is None:
        tags = []
    if isinstance(tags, str):
        tags = [tags]

    title = _extract_post_title(raw, filepath_str)

    return {
        "html": html,
        "date": date_str,
        "tags": tags,
        "filepath": filepath_str,
        "title": title,
    }


def _clean_post_markdown(raw):
    """清理帖子原始 md：去掉 ## 帖子基础信息 块、多余 frontmatter、帖子正文 标题"""
    # 剥离首段 frontmatter
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    # 去掉 "## 帖子基础信息" 到下一个 "## " 或 "---" 之间的内容
    body = re.sub(r'\n## 帖子基础信息\s*\n(?:- [^\n]*\n)*', '\n', body)

    # 去掉 "## 帖子正文" 标题本身
    body = re.sub(r'\n## 帖子正文\s*\n', '\n', body)

    # 去掉 "# 冰冰小美 的帖子" 行（包含发布时间的那一整块）
    body = re.sub(r'\n# 冰冰小美 的帖子\s*\n> [^\n]*\n', '\n', body)

    # 去掉尾部的 --- concepts: ... --- frontmatter 块（帖子合并文件的第二个fm）
    body = re.sub(r'\n---\s*\n(?:concepts|assets|entities|primary|method|tags):[\s\S]*?---\s*$', '', body)

    # 去掉残留的 frontmatter 键值行（concepts:, assets: 等）
    body = re.sub(r'\n(?:concepts|assets|entities|primary|method):[^\n]*', '', body)
    body = re.sub(r'\ntags:\s*\n(?:  - [^\n]*\n)*', '', body)

    # Unicode NFKC 规范化（CJK 兼容表意文字 → 标准形式），统一字符表示
    body = unicodedata.normalize('NFKC', body)
    # 去掉 "来自 冰冰小美的雪球专栏" 行
    body = re.sub(r'\n来自 冰冰小美的雪球专栏[ \t]*\n?', '\n', body)

    return body.strip()


def _clean_post_html(html):
    """清理帖子 HTML：去掉剩余的帖子基础信息、帖子正文标题、冰冰小美标题行"""
    # 去掉 <h2>帖子基础信息</h2> 到下一个 <h2> 或 <hr> 之间的内容
    html = re.sub(r'<h2>帖子基础信息</h2>\s*<ul>.*?</ul>', '', html, flags=re.DOTALL)
    # 去掉 <h2>帖子正文</h2>
    html = html.replace('<h2>帖子正文</h2>', '')
    # 去掉 <h1>冰冰小美 的帖子</h1> 和紧随的 <blockquote>
    html = re.sub(r'<h1>冰冰小美 的帖子</h1>\s*<blockquote>\s*<p>[^<]*</p>\s*</blockquote>', '', html)
    return html


def _extract_post_title(raw, filepath_str):
    """提取帖子标题：有 # 标题就用，没有就生成"""
    # 尝试找 markdown 标题
    title_match = re.search(r'^#\s+(.+)$', raw, re.MULTILINE)
    if title_match:
        t = title_match.group(1).strip()
        # 过滤掉 "冰冰小美" 开头的默认格式标题（如 "冰冰小美 的帖子"）
        if t and '冰冰小美' not in t:
            return t

    # 专栏/交易体系/三要素案例：h1 被过滤后用文件名（文件名即标题）
    name = Path(filepath_str).stem
    if any(filepath_str.startswith(prefix) for prefix in ['输入/专栏/', '输入/交易体系/', '输入/三要素案例/']):
        for prefix in ['概念卡片_', '观察仪表盘_', '亏钱效应复盘_', '信息处理工作台_']:
            if name.startswith(prefix):
                name = name[len(prefix):]
        return name

    # 帖子：从正文提取第一句有意义的话
    body = _clean_post_markdown(raw)
    # 取第一段非空的文本
    for line in body.split('\n'):
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('>') and not line.startswith('-') and not line.startswith('|'):
            # 清理 <br/> 标签
            cleaned = re.sub(r'<br\s*/?>', '', line).strip()
            if len(cleaned) >= 6:
                return cleaned[:50] + ('...' if len(cleaned) > 50 else '')

    return name[:40]


def get_posts_page(page, per_page=30, filter_type="all", search_query=None):
    """分页获取帖子"""
    if not post_index_built:
        build_post_index()

    if filter_type == "key":
        # 重点文章：从核心文章池中筛选
        source = [p for p in post_index if str(p["filepath"]) in core_article_pool]
    else:
        source = post_index

    # 搜索过滤：在 snippet 中搜索关键词
    if search_query:
        q = search_query.strip()
        source = [p for p in source if q in p.get("snippet", "") or q in str(p.get("tags", []))]

    total = len(source)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = source[start:end]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "has_more": end < total,
        "search_query": search_query,
    }


def get_random_core_post():
    """从核心文章池随机抽取一篇"""
    if not post_index_built:
        build_post_index()

    if not core_article_pool:
        return None

    chosen = random_mod.choice(core_article_pool)
    return get_post_html(chosen)


def get_word_freq_data():
    """解析词频文件，返回词频列表"""
    if not WORD_FREQ_FILE.exists():
        return []

    try:
        content = WORD_FREQ_FILE.read_text(encoding="utf-8")
    except Exception:
        return []

    concepts = []
    in_table = False
    for line in content.split("\n"):
        stripped = line.strip()
        # 检测表格行
        if stripped.startswith("|") and "|" in stripped[1:]:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cells) >= 2:
                # 跳过表头和分隔行
                if cells[0] in ("概念", "---", "------"):
                    continue
                try:
                    freq = int(cells[1].replace(",", ""))
                except ValueError:
                    freq = 0
                concepts.append({"name": cells[0], "freq": freq})
    return concepts


# ===== 帖子路由 =====

@app.route("/posts")
def posts_landing():
    """看看帖子 - 首页直展：搜索 + 随便看看 + 时间线 + 词频"""
    if not post_index_built:
        build_post_index()

    search_query = request.args.get("q", "").strip() or None

    # 随便看看：随机一篇核心文章
    random_post = get_random_core_post()

    # 时间线：首页加载第一页（全部+重点各一页）
    first_page_all = get_posts_page(1, per_page=30, filter_type="all", search_query=search_query)
    first_page_key = get_posts_page(1, per_page=30, filter_type="key", search_query=search_query)

    # 词频 top 20
    word_freq = get_word_freq_data()
    top_freq = word_freq[:20] if word_freq else []

    # 核心文章总数（用于"看看重点"卡片）
    total_core = len(core_article_pool) if core_article_pool else 350
    total_posts = len(post_index)

    return render_template("posts.html",
                           random_post=random_post,
                           first_page_all=first_page_all,
                           first_page_key=first_page_key,
                           top_freq=top_freq,
                           all_freq=word_freq,
                           total_core=total_core,
                           total_posts=total_posts,
                           search_query=search_query,
                           show_home_link=True,
                           current_page="posts")


@app.route("/posts/random")
def posts_random():
    """随便看看 - 卡片叠放"""
    post_data = get_random_core_post()
    return render_template("posts_random.html",
                           post=post_data,
                           core_total=len(core_article_pool) if post_index_built else 350,
                           show_home_link=True,
                           current_page="posts")


@app.route("/posts/timeline")
def posts_timeline():
    """时间线阅读"""
    if not post_index_built:
        build_post_index()

    search_query = request.args.get("q", "").strip() or None

    # 首页加载第一页
    first_page_all = get_posts_page(1, per_page=30, filter_type="all", search_query=search_query)
    first_page_key = get_posts_page(1, per_page=30, filter_type="key", search_query=search_query)

    return render_template("posts_timeline.html",
                           first_page_all=first_page_all,
                           first_page_key=first_page_key,
                           pinned_post=pinned_post_data,
                           search_query=search_query,
                           show_home_link=True,
                           current_page="posts")


@app.route("/posts/read")
def posts_read():
    """阅读单篇帖子全文"""
    path = request.args.get("path", "").strip()
    if not path:
        abort(404)

    post_data = get_post_html(path)
    if not post_data:
        abort(404)

    return render_template("posts_read.html",
                           post=post_data,
                           path=path,
                           show_home_link=True,
                           current_page="posts")


@app.route("/posts/core")
def posts_core():
    """核心文章目录页面"""
    if not post_index_built:
        build_post_index()

    # 按来源分组
    groups = {
        "pinned": {"name": "置顶帖", "icon": "&#x2606;", "articles": []},
        "column": {"name": "专栏文章", "icon": "&#x25A3;", "articles": []},
        "trade": {"name": "交易体系", "icon": "&#x25C9;", "articles": []},
        "three_elem": {"name": "三要素案例", "icon": "&#x25B3;", "articles": []},
        "deep": {"name": "深度帖子", "icon": "&#x25CB;", "articles": []},
    }

    # 置顶帖
    pinned_path = VAULT_ROOT / PINNED_POST
    if pinned_path.exists():
        meta = _parse_post_meta(pinned_path)
        if meta:
            # 置顶帖标题特殊处理：正文第一句才是真正标题
            meta["title"] = "危机，变局，与新生的中国"
            groups["pinned"]["articles"].append(meta)
        else:
            groups["pinned"]["articles"].append({
                "filepath": PINNED_POST,
                "date": "2024.05.19",
                "snippet": "危机，变局，与新生的中国",
                "tags": ["置顶帖"],
                "char_count": 0,
            })

    # 专栏 — 自动打系列标签
    column_series_tags = set()  # 收集所有出现过的标签
    if COLUMN_DIR.exists():
        for f in sorted(COLUMN_DIR.glob("*.md"), key=lambda x: x.name):
            meta = _parse_post_meta(f)
            if meta:
                title = meta.get("title", f.stem)
                content = meta.get("raw_body", "")
                meta["series_tags"] = _match_column_series(title, content)
                for t in meta["series_tags"]:
                    column_series_tags.add(t)
                groups["column"]["articles"].append(meta)
        groups["column"]["articles"].sort(key=lambda x: x.get("date_sort", ""), reverse=True)

    # 交易体系
    if TRADE_SYS_DIR.exists():
        for f in sorted(TRADE_SYS_DIR.glob("*.md"), key=lambda x: x.name):
            meta = _parse_post_meta(f)
            if meta:
                groups["trade"]["articles"].append(meta)
        groups["trade"]["articles"].sort(key=lambda x: x.get("date_sort", ""), reverse=True)

    # 三要素案例
    if THREE_ELEM_DIR.exists():
        for f in sorted(THREE_ELEM_DIR.glob("*.md"), key=lambda x: x.name):
            meta = _parse_post_meta(f)
            if meta:
                groups["three_elem"]["articles"].append(meta)
        groups["three_elem"]["articles"].sort(key=lambda x: x.get("date_sort", ""), reverse=True)

    # 深度帖子（字数第一档）
    for p in post_index:
        tags = p.get("tags", [])
        if "字数第一档" in tags or "#字数第一档" in tags:
            # 排除已在其他组的
            fp = str(p["filepath"])
            if fp == PINNED_POST:
                continue
            groups["deep"]["articles"].append(p)
    groups["deep"]["articles"].sort(key=lambda x: x.get("date_sort", ""), reverse=True)

    # 去重
    seen = set()
    for p in groups["column"]["articles"]:
        seen.add(str(p["filepath"]))
    for p in groups["trade"]["articles"]:
        seen.add(str(p["filepath"]))
    for p in groups["three_elem"]["articles"]:
        seen.add(str(p["filepath"]))
    groups["deep"]["articles"] = [p for p in groups["deep"]["articles"] if str(p["filepath"]) not in seen]

    total_core = sum(len(g["articles"]) for g in groups.values())

    return render_template("posts_core.html",
                           groups=groups,
                           total_core=total_core,
                           column_series=COLUMN_SERIES,
                           column_series_tags=sorted(column_series_tags),
                           show_home_link=True,
                           current_page="posts")


# ===== 帖子 API =====

@app.route("/api/posts/page/<int:page>")
def api_posts_page(page):
    """API：分页获取帖子（JSON）"""
    filter_type = request.args.get("filter", "all")
    search_query = request.args.get("q", "").strip() or None
    result = get_posts_page(page, per_page=30, filter_type=filter_type, search_query=search_query)
    return jsonify(result)


@app.route("/api/posts/random")
def api_posts_random():
    """API：随机获取一篇核心文章"""
    post_data = get_random_core_post()
    if not post_data:
        return jsonify({"error": "没有可用文章"}), 404
    return jsonify(post_data)


@app.route("/api/posts/load")
def api_posts_load():
    """API：按路径加载单篇帖子完整内容"""
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "缺少路径参数"}), 400
    post_data = get_post_html(path)
    if not post_data:
        return jsonify({"error": "文件不存在"}), 404
    return jsonify({"html": post_data["html"], "date": post_data["date"]})


# ===== 文件监控：实时同步线下 Obsidian 变更 =====

_vault_watcher = None
_rebuild_timer = None


def _rebuild_all_caches():
    """重建所有缓存（链接图 + 帖子索引）"""
    global post_index_built
    post_index_built = False
    post_index.clear()
    core_article_pool.clear()
    global pinned_post_data
    pinned_post_data = None
    link_graph.clear()

    print("[实时同步] 检测到文件变更，重建缓存...")
    scan_all_files()
    build_post_index()
    print(f"[实时同步] 完成 — 链接 {len(link_graph)} 个, 帖子 {len(post_index)} 篇")


def _debounced_rebuild():
    """防抖：合并 2 秒内的多次变更，只重建一次"""
    global _rebuild_timer
    if _rebuild_timer:
        _rebuild_timer.cancel()
    _rebuild_timer = threading.Timer(2.0, _rebuild_all_caches)
    _rebuild_timer.daemon = True
    _rebuild_timer.start()


def start_file_watcher():
    """启动文件监控线程"""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("[实时同步] watchdog 未安装，跳过文件监控")
        return

    class VaultHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory and event.src_path.endswith(".md"):
                _debounced_rebuild()

        def on_modified(self, event):
            if not event.is_directory and event.src_path.endswith(".md"):
                _debounced_rebuild()

        def on_deleted(self, event):
            if not event.is_directory and event.src_path.endswith(".md"):
                _debounced_rebuild()

        def on_moved(self, event):
            if not event.is_directory and event.dest_path.endswith(".md"):
                _debounced_rebuild()

    observer = Observer()
    observer.schedule(VaultHandler(), str(VAULT_ROOT), recursive=True)
    observer.daemon = True
    observer.start()
    print("[实时同步] 文件监控已启动，线下变更将自动同步")
    return observer


if __name__ == "__main__":
    print("正在扫描笔记库，构建链接图...")
    scan_all_files()
    print(f"扫描完成，共找到 {len(link_graph)} 个链接关系")
    print("正在构建帖子索引...")
    build_post_index()
    print("帖子索引构建完成")
    _vault_watcher = start_file_watcher()
    app.run(debug=True, port=5004)
