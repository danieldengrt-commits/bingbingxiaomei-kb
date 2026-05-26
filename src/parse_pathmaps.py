#!/usr/bin/env python3
"""路径图 Markdown → JSON 解析器
读取 docs/ 下 9 个路径图 Markdown 文件 + concepts.json，输出 site/data/pathmaps.json
路径图 = 知识架构 = 网站架构，概念融入对应路径图"""

import json
import re
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "docs"
CONCEPTS_JSON = BASE_DIR / "site" / "data" / "concepts.json"
OUTPUT = BASE_DIR / "site" / "data" / "pathmaps.json"

# 路径图文件 → 元数据映射
PATHMAPS = [
    {"file": "核心价值观/国运/国运概念路径图.md", "id": "guoyun", "name": "国运", "category": "核心价值观"},
    {"file": "核心价值观/流动性/流动性概念路径图.md", "id": "liudongxing", "name": "流动性", "category": "核心价值观"},
    {"file": "核心价值观/情绪体系/情绪体系概念路径图.md", "id": "qingxitixi", "name": "情绪体系", "category": "核心价值观"},
    {"file": "核心价值观/宏观全球/宏观全球概念路径图.md", "id": "hongguan", "name": "宏观全球", "category": "核心价值观"},
    {"file": "风险和交易/风险与防守/风险与防守概念路径图.md", "id": "fengxian", "name": "风险与防守", "category": "风险和交易"},
    {"file": "风险和交易/交易行为/交易行为概念路径图.md", "id": "jiaoyi", "name": "交易行为", "category": "风险和交易"},
    {"file": "风险和交易/三要素与交易行为/三要素与交易行为路径图.md", "id": "sanyaosu", "name": "三要素与交易行为", "category": "风险和交易"},
    {"file": "风险和交易/市场参与者/市场参与者概念路径图.md", "id": "shichang", "name": "市场参与者", "category": "风险和交易"},
    {"file": "产业个股/产业个股概念路径图.md", "id": "chanye", "name": "产业个股", "category": "产业个股"},
]

# 概念 → 路径图映射（concepts.json 的概念融入哪个路径图）
CONCEPT_TO_PATHMAP = {
    # 底层信仰 → 国运
    "guoyun": "guoyun",
    "shiti": "guoyun",
    "bainian": "guoyun",
    "touji": "guoyun",
    "touji_type": "guoyun",
    # 三要素框架 → 三要素与交易行为
    "jingzhenggeju": "sanyaosu",
    "liudongxing": "sanyaosu",
    "qingxuweizhi": "sanyaosu",
    "sanyaosuliandong": "sanyaosu",
    # 情绪体系 → 情绪体系
    "qingxubiao": "qingxitixi",
    "qingxuzhouqi": "qingxitixi",
    "qingxuyijia": "qingxitixi",
    "qingxubingdian": "qingxitixi",
    "qingxugaochao": "qingxitixi",
    "tanyukongju": "qingxitixi",
    # 风险与防守 → 风险与防守
    "kuiqianxiaoying": "fengxian",
    "zhengqianxiaoying": "fengxian",
    "jiaxiang": "fengxian",
    "zuokong": "fengxian",
    "fengxian": "fengxian",
    # 交易行为 → 交易行为
    "huimai": "jiaoyi",
    "taoli": "jiaoyi",
    "longtou": "jiaoyi",
    "jiazhitouji": "jiaoyi",
    "dengdai": "jiaoyi",
    "bingdianfaxianmei": "jiaoyi",
    "fencang": "jiaoyi",
    "kongcang": "jiaoyi",
    # 市场参与者 → 市场参与者
    "youzi": "shichang",
    "sanhu": "shichang",
    "jigou": "shichang",
    "lianghua": "shichang",
    # 宏观全球 → 宏观全球
    "meiyuan": "hongguan",
    "huilv": "hongguan",
    "tongzhanglilv": "hongguan",
    "guanshui": "hongguan",
    # 产业个股 → 产业个股
    "chanyelijie": "chanye",
    "xinnengyuan": "chanye",
    "bandaoti_ai": "chanye",
    "xiaofeichuhai": "chanye",
    "etf": "chanye",
    "baotuanlundong": "chanye",
    "zhouqi": "chanye",
}


def extract_quotes(text):
    """提取 > 「...」 格式的引用"""
    quotes = []
    for m in re.finditer(r'>\s*「(.+?)」', text, re.DOTALL):
        q = m.group(1).strip()
        if len(q) > 5:
            quotes.append(q)
    return quotes


def extract_overview(section_text):
    """从概览节提取文本和引用"""
    lines = section_text.strip().split('\n')
    text_parts = []
    quotes = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('> ') and '「' in line:
            # 引用行，提取引号内容
            for m in re.finditer(r'「(.+?)」', line):
                quotes.append(m.group(1).strip())
        elif line.startswith('> ') or line.startswith('#'):
            continue
        elif line.startswith('```'):
            continue
        elif not re.match(r'^\s*$', line):
            text_parts.append(line)

    return '\n'.join(text_parts), quotes


def extract_tree(section_text):
    """从代码块中提取 ASCII 树"""
    m = re.search(r'```\n(.*?)```', section_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def parse_table_rows(text):
    """解析 Markdown 表格行，返回列表"""
    rows = []
    for line in text.split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        # 跳过分隔行 |---|---|
        if re.match(r'^\|[\s\-:]+\|', line):
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if cells and any(c for c in cells):
            rows.append(cells)
    return rows


def parse_subsection(text):
    """解析一个 ### 子概念节"""
    lines = text.strip().split('\n')
    if not lines:
        return None

    # 第一行是子概念名称
    name = lines[0].strip().lstrip('#').strip()
    # 去掉编号前缀 如 "1.1 "
    name = re.sub(r'^\d+\.\d+\s*', '', name)

    body = '\n'.join(lines[1:])

    # 提取高频表达（表格）
    expressions = []
    table_rows = parse_table_rows(body)
    if table_rows:
        # 判断表头
        header = table_rows[0] if table_rows else []
        # 常见表头模式：
        # [高频表达, 来源] → 2列
        # [高频表达, 词频, 来源] → 3列
        # [概念, 词频, 状态, 文档/说明] → 4列
        # [层次, 含义, 冰冰小美的表达] → 3列
        data_rows = table_rows[1:] if len(table_rows) > 1 else table_rows

        for row in data_rows:
            if len(row) >= 4:
                # 4列: 概念/词频/状态/文档
                expressions.append({
                    "text": row[0],
                    "count": row[1] if row[1] != '—' else None,
                    "status": row[2] if row[2] not in ('—', '') else None,
                    "source": row[3] if len(row) > 3 and row[3] not in ('—', '') else None,
                })
            elif len(row) == 3:
                # 3列: 高频表达/词频/来源 或 概念/词频/来源
                count_val = row[1] if row[1] not in ('—', '') else None
                source_val = row[2] if row[2] not in ('—', '') else None
                expressions.append({
                    "text": row[0],
                    "count": count_val,
                    "source": source_val,
                })
            elif len(row) == 2:
                # 2列: 高频表达/来源
                expressions.append({
                    "text": row[0],
                    "source": row[1] if row[1] not in ('—', '') else None,
                })

    # 提取原话引用
    quotes = extract_quotes(body)

    # 提取描述文本（非表格、非引用的段落）
    desc_parts = []
    in_table = False
    in_quote = False
    for line in lines[1:]:
        line_stripped = line.strip()
        if line_stripped.startswith('|'):
            in_table = True
            continue
        if in_table and not line_stripped:
            in_table = False
            continue
        if in_table:
            continue
        if line_stripped.startswith('> '):
            continue
        if line_stripped.startswith('```'):
            continue
        if line_stripped.startswith('**') and line_stripped.endswith('**'):
            # 小标题如 **核心定义：** **典型原话：**
            continue
        if line_stripped and not line_stripped.startswith('#'):
            desc_parts.append(line_stripped)

    description = '\n'.join(desc_parts).strip()

    return {
        "name": name,
        "description": description if len(description) > 10 else None,
        "expressions": expressions,
        "quotes": quotes,
    }


def parse_section(text):
    """解析一个 ## 大节"""
    lines = text.strip().split('\n')
    if not lines:
        return None

    # 第一行是节标题
    title_line = lines[0].strip().lstrip('#').strip()
    # 去掉编号 如 "1. "
    title = re.sub(r'^\d+\.\s*', '', title_line)

    body = '\n'.join(lines[1:])

    # 提取"回答的问题"
    question = None
    q_match = re.search(r'回答的问题[：:]\s*(.+?)(?:\n|$)', body)
    if q_match:
        question = q_match.group(1).strip()

    # 按 ### 分割子概念
    subsections = []
    # 用正则分割 ### 开头的节
    parts = re.split(r'(?=^###\s)', body, flags=re.MULTILINE)

    for part in parts:
        part = part.strip()
        if part.startswith('### '):
            sub = parse_subsection(part)
            if sub:
                subsections.append(sub)

    # 如果没有 ### 子概念，但有表格，把表格行当作子概念
    if not subsections:
        table_rows = parse_table_rows(body)
        if len(table_rows) > 1:
            header = table_rows[0]
            for row in table_rows[1:]:
                if len(row) >= 4:
                    # 4列: 概念/词频/状态/文档或说明
                    sub = {
                        "name": row[0].replace('**', ''),
                        "description": row[3] if len(row) > 3 and row[3] not in ('—', '') else None,
                        "expressions": [{
                            "text": row[0].replace('**', ''),
                            "count": row[1] if row[1] not in ('—', '') else None,
                            "status": row[2] if row[2] not in ('—', '') else None,
                        }],
                        "quotes": extract_quotes(body),
                    }
                    subsections.append(sub)
                elif len(row) >= 2:
                    # 2-3列: 概念/词频/说明
                    sub = {
                        "name": row[0].replace('**', ''),
                        "description": row[2] if len(row) > 2 and row[2] not in ('—', '') else (row[1] if len(row) > 1 else None),
                        "expressions": [{
                            "text": row[0].replace('**', ''),
                            "count": row[1] if row[1] not in ('—', '') else None,
                        }],
                        "quotes": extract_quotes(body),
                    }
                    subsections.append(sub)

    return {
        "title": title,
        "question": question,
        "subsections": subsections,
    }


def parse_pathmap(filepath, meta):
    """解析单个路径图文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    result = {
        "id": meta["id"],
        "name": meta["name"],
        "category": meta["category"],
        "overview": None,
        "overviewQuotes": [],
        "tree": None,
        "sections": [],
        "topExpressions": [],
        "hierarchy": None,
        "fileIndex": [],
    }

    # 按 ## 分割大节
    parts = re.split(r'(?=^##\s)', content, flags=re.MULTILINE)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        first_line = part.split('\n')[0].strip()

        # 概览节
        if first_line.startswith('## 概览'):
            overview_text = part[len(first_line):].strip()
            # 提取树
            tree = extract_tree(overview_text)
            if tree:
                result["tree"] = tree
                # 去掉树的部分再提取文本
                overview_text_no_tree = re.sub(r'```\n.*?```', '', overview_text, flags=re.DOTALL)
            else:
                overview_text_no_tree = overview_text
            overview, quotes = extract_overview(overview_text_no_tree)
            # 补充提取所有 > 「...」 引用（包括跨行的）
            for m in re.finditer(r'>\s*「(.+?)」', overview_text, re.DOTALL):
                q = m.group(1).strip()
                if q not in quotes and len(q) > 5:
                    quotes.append(q)
            result["overview"] = overview
            result["overviewQuotes"] = quotes
            continue

        # 高频表达 TOP / 高频概念 TOP
        if re.match(r'^## 高频', first_line):
            table_rows = parse_table_rows(part)
            if len(table_rows) > 1:
                for row in table_rows[1:]:
                    if len(row) >= 3:
                        result["topExpressions"].append({
                            "rank": row[0],
                            "text": row[1],
                            "count": row[2],
                            "category": row[3] if len(row) > 3 else None,
                        })
            continue

        # 概念层级关系
        if '层级关系' in first_line:
            tree = extract_tree(part)
            if tree:
                result["hierarchy"] = tree
            continue

        # 核心文件索引
        if '文件索引' in first_line:
            table_rows = parse_table_rows(part)
            if len(table_rows) > 1:
                for row in table_rows[1:]:
                    if len(row) >= 2:
                        result["fileIndex"].append({
                            "file": row[0],
                            "content": row[1],
                        })
            continue

        # 普通大节（## N. xxx）
        if re.match(r'^## \d+\.', first_line):
            section = parse_section(part)
            if section:
                result["sections"].append(section)
            continue

    return result


def merge_concepts(pathmaps):
    """将 concepts.json 的概念合并到对应路径图"""
    if not CONCEPTS_JSON.exists():
        print("  [跳过] concepts.json 不存在")
        return

    with open(CONCEPTS_JSON, 'r', encoding='utf-8') as f:
        concepts = json.load(f)

    # 建立 pathmap id → 概念列表 的映射
    pm_map = {pm["id"]: pm for pm in pathmaps}

    # 初始化每个路径图的 concepts 列表
    for pm in pathmaps:
        pm["concepts"] = []

    matched = 0
    unmatched = 0
    for c in concepts:
        cid = c["id"]
        pm_id = CONCEPT_TO_PATHMAP.get(cid)
        if pm_id and pm_id in pm_map:
            pm_map[pm_id]["concepts"].append({
                "id": c["id"],
                "name": c["name"],
                "definition": c["definition"],
                "quote": c.get("quote"),
                "related": c.get("related", []),
            })
            matched += 1
        else:
            unmatched += 1
            print(f"  [未匹配] {c['name']} ({cid})")

    print(f"  概念合并: {matched}个匹配, {unmatched}个未匹配")


def main():
    pathmaps = []
    for meta in PATHMAPS:
        filepath = DOCS_DIR / meta["file"]
        if not filepath.exists():
            print(f"  [跳过] 文件不存在: {filepath}")
            continue

        print(f"  解析: {meta['name']} ({meta['file']})")
        pm = parse_pathmap(filepath, meta)
        pathmaps.append(pm)

        # 统计
        sec_count = len(pm["sections"])
        sub_count = sum(len(s["subsections"]) for s in pm["sections"])
        expr_count = sum(
            len(sub["expressions"])
            for s in pm["sections"]
            for sub in s["subsections"]
        )
        quote_count = sum(
            len(sub["quotes"])
            for s in pm["sections"]
            for sub in s["subsections"]
        ) + len(pm["overviewQuotes"])
        print(f"    → {sec_count}层, {sub_count}子概念, {expr_count}高频表达, {quote_count}引用")

    # 合并 concepts.json 的概念
    print("\n合并概念...")
    merge_concepts(pathmaps)

    # 输出
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(pathmaps, f, ensure_ascii=False, indent=2)

    print(f"\n完成! 输出: {OUTPUT}")
    print(f"共 {len(pathmaps)} 个路径图")


if __name__ == "__main__":
    main()
