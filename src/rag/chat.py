"""
AI 冰美聊天核心：多步RAG检索 + DeepSeek 流式生成 + 缓存
优化版：知识图谱链路追踪 + 冰美人设 + 正反案例对比
"""
import os
import re
import json
import hashlib
import time
import datetime
from pathlib import Path

# 香港服务器直连 huggingface.co，不设镜像；大陆服务器用 hf-mirror.com
# os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import chromadb
from sentence_transformers import SentenceTransformer

# ===== 配置 =====
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_DIR = str(PROJECT_ROOT / "data" / "rag_db")
VAULT_ROOT = PROJECT_ROOT / "vault"
MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 三层检索配额：概念卡片 > 重点文章 > 普通帖子
TOP_K_CONCEPTS = 5     # 概念卡片（type=concept）
TOP_K_ARTICLES = 3     # 重点文章（专栏/交易体系/三要素案例）
TOP_K_POSTS = 2        # 普通帖子（category=帖子）

MAX_LINKED_CONCEPTS = 5  # 二级检索最多追5张关联卡片
MAX_POST_CHARS = 2000     # 普通帖子截断上限（碎碎念少给空间）
MAX_ARTICLE_CHARS = 5000  # 重点文章截断上限（专栏/交易体系/三要素案例多给空间）

# 重点文章 category
KEY_ARTICLE_CATEGORIES = {"专栏", "交易体系", "三要素案例"}

# DeepSeek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY 环境变量未设置，无法启动 AI 聊天功能")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 缓存
_cache = {}
CACHE_MAX = 100
CACHE_TTL = 3600

# 时间敏感查询关键词
TIME_KEYWORDS = ["最近", "最新", "近期", "今天", "昨天", "本周", "这周", "最近几天", "这几天", "刚刚", "刚发"]


def _is_time_query(query: str) -> bool:
    """检测是否为时间敏感查询"""
    return any(kw in query for kw in TIME_KEYWORDS)


def _parse_date(date_str: str):
    """尝试解析帖子元数据中的日期，返回 datetime 或 None"""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    # 尝试常见格式
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.datetime.strptime(date_str[:19], fmt)
        except ValueError:
            continue
    # 从字符串中提取 YYYY-MM-DD 模式
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if match:
        try:
            return datetime.datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    return None


# 全局模型
_model = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=DB_DIR)
        _collection = client.get_collection("bingbingxiaomei")
    return _collection


# ===== 多步检索 =====

def _extract_wiki_links(text: str) -> list[str]:
    """从概念卡片内容中提取 [[wiki-link]] 指向的概念名"""
    # 匹配 [[path|display]] 或 [[path]] 格式
    links = []
    for match in re.finditer(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', text):
        target = match.group(1).strip()
        # 从路径中提取概念名：概念卡片_XXX.md → XXX
        name = Path(target).stem
        # 去掉"概念卡片_" 前缀
        if name.startswith("概念卡片_"):
            name = name[len("概念卡片_"):]
        # 过滤掉帖子路径（含"输入/"、"贴子/"等）
        if any(kw in target for kw in ["输入/", "贴子", "专栏", "交易体系", "三要素案例"]):
            continue
        if name and len(name) >= 2:
            links.append(name)
    return links


def _resolve_concept_file(concept_name: str) -> Path | None:
    """根据概念名找到 vault 中的概念卡片文件路径"""
    search_dirs = [
        VAULT_ROOT / "1-核心概念",
        VAULT_ROOT / "2-产业标的",
        VAULT_ROOT / "3-专题整理",
    ]
    candidates = [
        f"概念卡片_{concept_name}.md",
        f"{concept_name}.md",
    ]
    for d in search_dirs:
        if not d.exists():
            continue
        for fname in candidates:
            for fpath in d.rglob(fname):
                if fpath.exists():
                    return fpath
    return None


def _clean_relative_time(text: str, date_str: str) -> str:
    """将帖子中的相对时间词替换为绝对日期（基于发帖日期）"""
    parsed = _parse_date(date_str)
    if not parsed:
        return text

    y, m, d = parsed.year, parsed.month, parsed.day
    from calendar import monthrange

    prev_month = 12 if m == 1 else m - 1
    prev_month_year = y - 1 if m == 1 else y
    next_month = 1 if m == 12 else m + 1
    next_month_year = y + 1 if m == 12 else y

    # 从具体到模糊的顺序替换，避免重复替换
    # 注意：lambda 参数名为 _ 或 match，避免与局部变量 m(月) 冲突
    replacements = [
        (rf"(?:今年)(\d{{1,2}})月", lambda match: f"{y}年{match.group(1)}月"),
        (rf"(?:去年)(\d{{1,2}})月", lambda match: f"{y-1}年{match.group(1)}月"),
        (r"今年(?!\d)", lambda _: f"{y}年"),
        (r"去年(?!\d)", lambda _: f"{y-1}年"),
        (r"明年(?!\d)", lambda _: f"{y+1}年"),
        (r"上个月", lambda _: f"{prev_month_year}年{prev_month}月"),
        (r"下个月", lambda _: f"{next_month_year}年{next_month}月"),
        (r"昨天", lambda _: f"{y}年{m:02d}月{d-1:02d}日" if d > 1 else f"{prev_month_year}年{prev_month:02d}月{monthrange(prev_month_year, prev_month)[1]:02d}日"),
        (r"明天", lambda _: f"{y}年{m:02d}月{d+1:02d}日" if d < monthrange(y, m)[1] else f"{next_month_year}年{next_month:02d}月01日"),
        (r"上周", lambda _: f"{y}年{m:02d}月（上周）"),
        (r"下周", lambda _: f"{y}年{m:02d}月（下周）"),
        (r"本周", lambda _: f"{y}年{m:02d}月（本周）"),
    ]

    for pattern, replacer in replacements:
        text = re.sub(pattern, replacer, text)
    return text


def _read_file_content(filepath: Path, max_chars: int | None = None) -> str:
    """读取并解析 markdown 文件内容（去掉 frontmatter）"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2].strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return text
    except Exception:
        return ""


def retrieve(query: str) -> list[dict]:
    """
    三层检索（优先级递减）：
    1. 概念卡片（type=concept）：取 TOP_K_CONCEPTS 篇，全文不截断
    2. 重点文章（专栏/交易体系/三要素案例）：取 TOP_K_ARTICLES 篇
    3. 普通帖子（category=帖子）：取 TOP_K_POSTS 篇，严格截断
    4. 从概念卡片中提取 wiki-link，二级检索关联概念
    """
    model = _get_model()
    collection = _get_collection()

    query_embedding = model.encode([query], normalize_embeddings=True).tolist()

    # 多取一些，按类分组后各取 top-n
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=TOP_K_CONCEPTS + TOP_K_ARTICLES + TOP_K_POSTS + 10,
    )

    all_sources = []
    primary_concept_ids = set()
    for doc_id, doc_text, meta, distance in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        similarity = 1 - distance
        if similarity < 0.3:
            continue
        src = {
            "id": doc_id,
            "type": meta.get("type", "post"),
            "category": meta.get("category", ""),
            "title": meta.get("title", ""),
            "date": meta.get("date", ""),
            "url": meta.get("url", ""),
            "path": meta.get("path", ""),
            "content": doc_text,
            "similarity": round(similarity, 3),
            "linked_from": None,
        }
        all_sources.append(src)
        if src["type"] == "concept":
            primary_concept_ids.add(doc_id)

    # 按三层分组
    concepts = [s for s in all_sources if s["type"] == "concept"]
    key_articles = [s for s in all_sources if s["type"] == "post" and s["category"] in KEY_ARTICLE_CATEGORIES]
    regular_posts = [s for s in all_sources if s["type"] == "post" and s["category"] not in KEY_ARTICLE_CATEGORIES]

    # 时间敏感查询：提升近期帖子权重
    if _is_time_query(query):
        now = datetime.datetime.now()
        for lst in [key_articles, regular_posts]:
            for p in lst:
                date = _parse_date(p.get("date", ""))
                if date:
                    days_ago = max(0, (now - date).days)
                    if days_ago <= 7:
                        p["similarity"] = min(0.99, p["similarity"] + 0.35)
                    elif days_ago <= 30:
                        p["similarity"] = min(0.99, p["similarity"] + 0.25)
                    elif days_ago <= 90:
                        p["similarity"] = min(0.99, p["similarity"] + 0.15)
                else:
                    p["similarity"] = max(0.30, p["similarity"] - 0.10)
        key_articles.sort(key=lambda x: x["similarity"], reverse=True)
        regular_posts.sort(key=lambda x: x["similarity"], reverse=True)

    # 各取 top-n
    concepts = concepts[:TOP_K_CONCEPTS]
    key_articles = key_articles[:TOP_K_ARTICLES]
    regular_posts = regular_posts[:TOP_K_POSTS]

    # 重新从源文件读取概念卡片全文（ChromaDB建索引时已截断）
    for c in concepts:
        if c.get("path"):
            fpath = VAULT_ROOT / c["path"]
            if fpath.exists():
                full = _read_file_content(fpath)
                if full:
                    c["content"] = full

    # 第2步：从概念卡片中提取 wiki-link，二级检索关联概念
    linked_sources = []
    linked_titles = set()
    for c in concepts:
        wiki_links = _extract_wiki_links(c["content"])
        for link_name in wiki_links[:3]:
            if link_name in linked_titles:
                continue
            fpath = _resolve_concept_file(link_name)
            if not fpath:
                continue
            content = _read_file_content(fpath)
            if not content or len(content) < 50:
                continue
            linked_titles.add(link_name)
            linked_sources.append({
                "id": f"linked_{link_name}",
                "type": "concept",
                "category": "关联概念",
                "title": link_name,
                "date": "",
                "url": "",
                "path": str(fpath),
                "content": content,
                "similarity": 0.85,
                "linked_from": c["title"],
            })

    # 组装：概念卡片全文 → 关联概念全文 → 重点文章 → 普通帖子
    final_sources = []

    # 概念卡片：全文（不截断）
    for s in concepts:
        final_sources.append({**s, "content": s["content"]})

    # 关联概念：全文
    for s in linked_sources[:MAX_LINKED_CONCEPTS]:
        final_sources.append(s)

    # 重点文章：用 MAX_ARTICLE_CHARS 截断
    for s in key_articles:
        content = s["content"]
        if len(content) > MAX_ARTICLE_CHARS:
            content = content[:MAX_ARTICLE_CHARS] + "\n...(文章较长，已截断)"
        final_sources.append({**s, "content": content})


    # 普通帖子：用 MAX_POST_CHARS 截断
    for s in regular_posts:
        content = s["content"]
        if len(content) > MAX_POST_CHARS:
            content = content[:MAX_POST_CHARS] + "\n...(帖子较长，已截断，建议阅读原帖)"
        final_sources.append({**s, "content": content})

    return final_sources


# ===== Prompt 构建 =====

SYSTEM_PROMPT = """你是「AI 冰美」，一个基于雪球博主冰冰小美的知识体系来教读者思考的 AI 助手。你的任务不是复述冰美做了什么操作，而是**帮读者理解她怎么看市场、怎么用三要素框架分析问题**。

## 你的身份

你是冰美知识体系的讲解者，不是冰美本人。冰美是雪球上的交易者，你在帮助读者理解她的投资哲学和实操理论。你被她的概念卡片、专栏文章、交易体系文章和三要素案例所训练。你的价值在于**教会读者怎么思考**，而不是告诉读者"冰美在什么时候买了什么"。

## 核心框架：三要素

冰美分析一切问题都用这个框架，你教读者时也必须始终围绕它：
1. **竞争格局的比较优势**（管方向）— 买什么赛道？谁有比较优势？
2. **流动性辩证分析**（管时机）— 钱够不够？什么时候进场？中观为主，宏观为辅
3. **情绪位置的变化**（管买卖点）— 现在该贪婪还是恐惧？以亏钱效应为判断节点

三者同步有利，才能买入不败。

## 表达规则（必须遵守）

### 禁止
- **禁止描述具体买卖操作**：不能说"冰美在19元推荐""她在XX价位买入/卖出"
- **禁止以冰美第一人称叙述操作**：不能说"我当时买了""我推荐过"
- **禁止编造具体数字和日期**：引用的数字必须来自资料
- **禁止编造概念卡片名称**：回答中引用的概念卡片，必须与资料中的 [概念1][关联1] 标签完全一致。资料里没有的卡片不要自己编名字

### 必须
- **引用格式**："冰美在《XXX》中讨论过这个框架..."、"她在XX帖子（20XX年X月）里分析过..."
- **教学导向**：解释"为什么这么看"，不是"她做了什么"
- **时间标注**：引用帖子时，所有相对时间词都以帖子发帖日期为基准。如果帖子写于2024年3月，文中的"今年6月"指的是2024年6月，"下个月"指的是2024年4月。回答中遇到时间词必须自己换算成绝对日期。
- **术语使用**：冰点、高潮点、让利、报团、三要素共振、情绪标、亏钱效应、挣钱效应
- **诚实**：资料充足就展开；概念卡片命中不足时，诚实说"这个概念作者还没整理成知识卡片"，列出相关帖子，邀请读者去留言板提议整理

### 5秒窗口
- **核心观点必须放在回答最前面，30-50字内说清楚**。读者只有5秒决定要不要继续看
- 使用小标题 + 短段落 + 加粗关键词，让读者能快速扫描结构
- 资料充足可展开到 800-1200 字；资料不足宁可短，不要硬编

## 回答结构（先判断问题类型）

所有类型都可以用三要素框架分析——即使问"有没有提过XX"，也要从三要素角度告诉读者思考什么。

**类型A：事实查询**（"最近说了什么""有没有提过XX""对XX的看法"）
- 先直接回答事实（有/没有、什么时候、说了什么）
- 用三要素框架展开分析
- 引用具体帖子时间和内容

**类型B：概念解释**（"什么是XX""怎么理解XX"）
1. 核心定义（30-50字，读者5秒可读完）
2. 用三要素框架拆解
3. 举一个冰美讨论过的具体案例
4. 正反对比（原理层面的对错）
5. 推荐相关概念卡片和原帖

**类型C：深度分析**（"怎么看XX""XX的框架是什么"）
1. 核心观点（30-50字，直接用冰美的分析框架亮态度）
2. 用三要素框架逐层分析
3. 具体案例（什么时间、什么板块/标的、体现了什么原理——不是操作记录）
4. 正反对比（原理层面的对错，不是盈亏数字）
5. 怎么观察/怎么用（实操框架，不是买卖建议）
6. 推荐相关概念卡片和原帖

## Few-Shot 参考格式

以下是高质量回答的格式参考：

### 示例1：概念解释 — 情绪标
```
# 情绪标：市场的星星之火

## 核心观点
情绪标不是涨得好的股票，而是贯穿全局、代表市场总体情绪、不断新高的个股[概念1]。

## 真假情绪标对比
| 真情绪标 | 伪情绪标 |
|---------|---------|
| 带动指数信心共振 | 一家独大强行封板 |
| 次日有溢价效应 | 次日无溢价 |
| 市场用真金白银投票 | 人气靠自媒体传播 |

真例：建设机械（冰点转势板+指数共振→确立情绪标）[概念1]
伪例：恒为科技（卡位最高板但无带动→核按钮跌停，散户接盘游资跑路）[帖子2]

## 怎么观察
1. 冰点找逆市涨停 2. 次日看溢价 3. 检验指数共振
```

### 示例2：概念对比 — 恐惧 vs 风险
```
# 恐惧和风险有什么区别？

## 核心观点
恐惧是情绪位置，风险是客观条件。恐惧时可能风险已出清，风险累积时反而没人恐惧[帖子1]。

## 对比
| 维度 | 恐惧 | 风险 |
|------|------|------|
| 本质 | 情绪位置 | 客观条件 |
| 来源 | 亏钱效应扩散 | 竞争格局恶化/流动性衰竭 |
| 应对 | 反人性买入 | 减仓/空仓 |

冰美：阶段新高的点，恐惧十足。这种恐惧应该区别于风险[帖子1]。
```

### 示例3：深度分析 — 仓位管理
```
# 冰美的仓位管理逻辑

## 核心观点
仓位是情绪位置、竞争格局、流动性三要素共振后的数学结果，不是凭感觉[概念1]。

## 实操链条
1. 识位定策 → 2. 分仓下注 → 3. 择时借势 → 4. 动态调整 → 5. 风控防守[概念1]

## 具体配置
竞争格局越清晰方向仓位越重：江淮汽车16%、比亚迪12%、赛力斯2%（利润仓）[关联1]

## 核心口诀
行情好多做，行情不好少做。空仓=最高防守[概念1]。
```

## 价值观

- 不亏钱是第一位
- 买入不败，不是挣大钱
- 做多中国，但要在冰点做多
- 体系建立在熊市基础上，控制风险第一
- 不做傻事比做聪明事更重要"""


def build_prompt(question: str, sources: list[dict]) -> tuple[str, str]:
    """构建 system prompt 和 user prompt"""

    # 按类型分组组装
    primary_concepts = [s for s in sources if s["type"] == "concept" and s.get("linked_from") is None]
    linked_concepts = [s for s in sources if s["type"] == "concept" and s.get("linked_from") is not None]
    key_articles = [s for s in sources if s["type"] == "post" and s["category"] in KEY_ARTICLE_CATEGORIES]
    # 反例功能已移除，告诉读者去看相关帖子即可
    regular_posts = [s for s in sources if s["type"] == "post" and s["category"] not in KEY_ARTICLE_CATEGORIES]

    context_parts = []

    # 核心概念卡片（最重要，放最前面，全文不截断）
    for i, src in enumerate(primary_concepts):
        label = f"[概念{i+1}]"
        date_info = f"（整理于 {src['date']}）" if src.get("date") else ""
        context_parts.append(
            f"{label} 【{src['category']}】{src['title']} {date_info}\n"
            f"{src['content']}"
        )

    # 关联概念（二级检索到的）
    for i, src in enumerate(linked_concepts):
        label = f"[关联{i+1}]"
        linked_info = f"（从「{src['linked_from']}」关联而来）" if src.get("linked_from") else ""
        context_parts.append(
            f"{label} 【关联概念{linked_info}】{src['title']}\n"
            f"{src['content']}"
        )

    # 重点文章（专栏/交易体系/三要素案例）
    for i, src in enumerate(key_articles):
        label = f"[重点{i+1}]"
        date_str = src.get("date", "")
        readable_date = ""
        if date_str:
            parsed = _parse_date(date_str)
            if parsed:
                readable_date = f"（冰美发表于 {parsed.year}年{parsed.month:02d}月{parsed.day:02d}日）"
            else:
                readable_date = f"（发表日期：{date_str}）"
        # 清洗相对时间词
        content = _clean_relative_time(src["content"], date_str) if date_str else src["content"]
        context_parts.append(
            f"{label} 【{src['category']}】{src['title']} {readable_date}\n"
            f"{content}"
        )

    # 普通帖子（作为补充细节）
    for i, src in enumerate(regular_posts):
        label = f"[帖子{i+1}]"
        date_str = src.get("date", "")
        readable_date = ""
        if date_str:
            parsed = _parse_date(date_str)
            if parsed:
                readable_date = f"（冰美发表于 {parsed.year}年{parsed.month:02d}月{parsed.day:02d}日）"
        content = _clean_relative_time(src["content"], date_str) if date_str else src["content"]
        context_parts.append(
            f"{label} 【帖子】{src['title']} {readable_date}\n"
            f"{content}"
        )

    user_prompt = f"""以下是冰冰小美的知识体系资料，按重要性排列（概念卡片 > 重点文章 > 帖子）：

{chr(10).join(context_parts)}

---
用户问题：{question}

请用冰美的知识体系来教读者思考。记住：
- 你是教师，不是操盘手——教框架、教方法，不要说"冰美做了什么操作"
- 每条观点必须标注来源：[概念1]/[重点2]/[帖子1] 等标签。没有资料支持就说"冰美没详细聊过"
- 引用时明确出处（概念卡片名/文章标题 + 发表日期）
- 回答结尾必须列出建议阅读的概念卡片和原帖链接
- 结构：核心观点 → 三要素逐层分析 → 具体案例（原理层面） → 正反对比 → 实操框架 → 相关概念推荐 → 建议阅读原文
- 你只能引用上面资料中出现的 [概念1][关联1][重点1][帖子1] 标签，不要创造新的概念卡片名称
- 如果上面的概念卡片数量不足（少于2条），说明这个概念作者还没整理成知识卡片。请诚实告诉读者："这个概念作者还没整理成知识卡片，建议阅读以下冰美原帖找到属于自己的知识链接。也非常欢迎去留言板告诉我们你想要整理的概念。"然后列出帖子即可，不要自己展开分析
- 核心观点放在最前面，30-50字内说清楚（读者只有5秒决定是否继续看）
- 资料充足可展开到800-1200字；资料不足宁可短，不要硬编"""

    return SYSTEM_PROMPT, user_prompt


# ===== 缓存 =====

def get_cached(question: str) -> dict | None:
    h = hashlib.md5(question.strip().encode()).hexdigest()
    if h in _cache:
        entry = _cache[h]
        if time.time() - entry["ts"] < CACHE_TTL:
            return entry
        del _cache[h]
    return None


def set_cache(question: str, answer: str, sources: list[dict]):
    global _cache
    h = hashlib.md5(question.strip().encode()).hexdigest()
    _cache[h] = {"answer": answer, "sources": sources, "ts": time.time()}
    if len(_cache) > CACHE_MAX:
        oldest = min(_cache, key=lambda k: _cache[k]["ts"])
        del _cache[oldest]


# ===== 流式生成 =====

def generate_stream(question: str):
    """生成器：逐 token yield SSE 事件"""
    import requests

    # 1. 检查缓存
    cached = get_cached(question)
    if cached:
        yield f"data: {json.dumps({'type': 'cached'}, ensure_ascii=False)}\n\n"
        for char in cached["answer"]:
            yield f"data: {json.dumps({'type': 'token', 'text': char}, ensure_ascii=False)}\n\n"
            time.sleep(0.003)
        yield f"data: {json.dumps({'type': 'sources', 'sources': cached['sources']}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # 2. 多步检索
    t0 = time.time()
    sources = retrieve(question)
    t_retrieve = time.time() - t0
    print(f"[AI冰美] 检索耗时: {t_retrieve:.1f}s, 来源数: {len(sources)}")
    if not sources:
        msg = "冰美好像没详细聊过这个话题，换个问题试试？"
        yield f"data: {json.dumps({'type': 'token', 'text': msg}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'sources', 'sources': []}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    yield f"data: {json.dumps({'type': 'sources_found', 'count': len(sources)}, ensure_ascii=False)}\n\n"

    # 3. 构建 prompt 并调用 DeepSeek
    system_prompt, user_prompt = build_prompt(question, sources)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,  # 限制输出长度，减少编造空间
        "stream": True,
    }

    try:
        t_api_start = time.time()
        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=120,
            stream=True,
        )

        full_answer = ""
        first_token = True
        t_first_token = None

        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if first_token:
                            t_first_token = time.time() - t_api_start
                            first_token = False
                        full_answer += content
                        yield f"data: {json.dumps({'type': 'token', 'text': content}, ensure_ascii=False)}\n\n"
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

        t_total = time.time() - t_api_start
        print(f"[AI冰美] DeepSeek API: 首token={t_first_token:.1f}s, 总计={t_total:.1f}s, 回答长度={len(full_answer)}字")

        # 缓存
        if full_answer.strip():
            set_cache(question, full_answer, [
                {"title": s["title"], "url": s["url"], "date": s["date"],
                 "category": s["category"], "similarity": s["similarity"],
                 "path": s.get("path", ""), "linked_from": s.get("linked_from"),
                 "src_type": s["type"]}
                for s in sources
            ])

        # 发送来源
        yield f"data: {json.dumps({'type': 'sources', 'sources': [{'title': s['title'], 'url': s['url'], 'date': s['date'], 'category': s['category'], 'similarity': s['similarity'], 'path': s.get('path', ''), 'linked_from': s.get('linked_from'), 'src_type': s['type']} for s in sources]}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': f'AI 调用失败：{str(e)[:200]}'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
