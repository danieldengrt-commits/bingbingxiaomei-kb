#!/usr/bin/env python3
"""冰冰小美知识库 - 帖子自动打标

流程：
1. 加载posts.json
2. 规则打标（关键词匹配）— 快速、免费
3. AI精标（mimo API）— 对规则打标不到的帖子
4. 合并结果，输出tags.json
5. 写入帖子frontmatter
"""

import os
import re
import json
import time
import requests
from collections import Counter, defaultdict

from tag_schema import (
    CONCEPT_KEYWORDS, CONCEPT_CATEGORIES,
    ASSET_KEYWORDS, US_STOCKS, A_STOCKS,
)

# ============================================================
# 配置 — 从环境变量读取
# ============================================================

VAULT_PATH = os.environ.get("BBXM_VAULT_PATH", ".")
POSTS_JSON = os.path.join(VAULT_PATH, "posts.json")
POSTS_DIR = os.path.join(VAULT_PATH, "贴子")
OUTPUT_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tags.json")

# mimo API
MIMO_API_URL = "https://token-plan-cn.xiaomimimo.com/anthropic/v1/messages"
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "")
MIMO_MODEL = "mimo-v2.5-pro"

# ============================================================
# 第一步：规则打标
# ============================================================

def rule_tag_post(text):
    """用关键词匹配给单条帖子打标签

    返回: {
        "concepts": ["流动性", "风险"],  # 命中的概念
        "assets": ["黄金", "美元"],       # 命中的资产
        "entities": ["比亚迪"],           # 提取的实体
        "confidence": "high/medium/low",  # 置信度
    }
    """
    concepts = []
    assets = []
    entities = []

    # 概念匹配
    concept_hits = {}
    for concept, keywords in CONCEPT_KEYWORDS.items():
        count = 0
        for kw in keywords:
            count += text.count(kw)
        if count > 0:
            concept_hits[concept] = count

    # 按命中次数排序，取前3个
    sorted_concepts = sorted(concept_hits.items(), key=lambda x: -x[1])
    concepts = [c[0] for c in sorted_concepts[:3]]

    # 资产匹配
    asset_hits = {}
    for asset, keywords in ASSET_KEYWORDS.items():
        count = 0
        for kw in keywords:
            count += text.count(kw)
        if count > 0:
            asset_hits[asset] = count

    sorted_assets = sorted(asset_hits.items(), key=lambda x: -x[1])
    assets = [a[0] for a in sorted_assets[:5]]

    # A股实体提取
    for stock in A_STOCKS:
        if stock in text:
            entities.append(stock)

    # 美股实体提取
    for stock in US_STOCKS:
        if stock in text:
            entities.append(stock)

    # 去重
    entities = list(set(entities))

    # 置信度判断
    if len(concepts) >= 2:
        confidence = "high"
    elif len(concepts) == 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "concepts": concepts,
        "assets": assets,
        "entities": entities,
        "confidence": confidence,
    }


def rule_tag_all(posts):
    """对所有帖子做规则打标"""
    results = {}
    stats = {"high": 0, "medium": 0, "low": 0}

    for post in posts:
        post_id = post["id"]
        text = post.get("text", "")
        if not text or len(text) < 10:
            results[post_id] = {
                "concepts": [],
                "assets": [],
                "entities": [],
                "confidence": "skip",
                "method": "skip",
            }
            continue

        tag = rule_tag_post(text)
        tag["method"] = "rule"
        results[post_id] = tag
        stats[tag["confidence"]] += 1

    print(f"规则打标完成: {len(results)} 条")
    print(f"  高置信度: {stats['high']}")
    print(f"  中置信度: {stats['medium']}")
    print(f"  低置信度: {stats['low']}")

    return results


# ============================================================
# 第二步：AI精标（mimo API）
# ============================================================

def build_ai_prompt(text):
    """构建AI打标的prompt"""
    concept_list = "\n".join([f"- {c}" for c in CONCEPT_KEYWORDS.keys()])
    asset_list = "\n".join([f"- {a}" for a in ASSET_KEYWORDS.keys()])

    return f"""你是一个投资内容分析专家。请分析以下帖子内容，提取标签。

## 概念标签（从以下选择1-3个最相关的）
{concept_list}

## 资产类别（选择涉及的）
{asset_list}

## 实体标签（提取帖子中提及的具体个股/公司名称）

## 帖子内容
{text}

## 输出格式（严格JSON）
{{"concepts": ["概念1", "概念2"], "assets": ["资产1"], "entities": ["个股1"]}}

注意：
- concepts 最多3个，选择最核心的
- assets 只选帖子明确讨论的
- entities 提取具体提及的个股名称
- 如果帖子太短或无法判断，返回空数组
- 只输出JSON，不要其他文字"""


def call_mimo_api(prompt, retries=3):
    """调用mimo API"""
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': MIMO_API_KEY,
        'anthropic-version': '2023-06-01'
    }
    payload = {
        'model': MIMO_MODEL,
        'max_tokens': 500,
        'messages': [
            {'role': 'user', 'content': prompt}
        ]
    }

    for attempt in range(retries):
        try:
            response = requests.post(MIMO_API_URL, headers=headers, json=payload, timeout=60)
            result = response.json()

            # 提取文本
            content = ""
            if 'content' in result and len(result['content']) > 0:
                for block in result['content']:
                    if block.get('type') == 'text':
                        content = block.get('text', '')
                        break
            elif 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')

            # 解析JSON
            content = content.strip()
            # 提取JSON部分
            json_match = re.search(r'\{[^{}]*\}', content)
            if json_match:
                return json.loads(json_match.group())

        except Exception as e:
            print(f"  API调用失败 (尝试 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2)

    return {"concepts": [], "assets": [], "entities": []}


def ai_tag_posts(posts, results, max_count=None):
    """对低置信度帖子做AI打标

    Args:
        posts: 帖子列表
        results: 规则打标结果
        max_count: 最大处理数量（None=全部）
    """
    # 筛选需要AI打标的帖子
    need_ai = []
    for post in posts:
        post_id = post["id"]
        if post_id in results and results[post_id]["confidence"] in ("low", "medium"):
            text = post.get("text", "")
            if len(text) >= 20:  # 太短的不值得用AI
                need_ai.append(post)

    if max_count:
        need_ai = need_ai[:max_count]

    print(f"\nAI精标: {len(need_ai)} 条待处理")

    processed = 0
    for post in need_ai:
        post_id = post["id"]
        text = post.get("text", "")

        prompt = build_ai_prompt(text)
        ai_result = call_mimo_api(prompt)

        # AI结果覆盖规则结果
        if ai_result.get("concepts"):
            results[post_id]["concepts"] = ai_result["concepts"]
        if ai_result.get("assets"):
            results[post_id]["assets"] = ai_result["assets"]
        if ai_result.get("entities"):
            results[post_id]["entities"] = list(set(
                results[post_id].get("entities", []) + ai_result["entities"]
            ))
        results[post_id]["method"] = "ai"
        results[post_id]["confidence"] = "high"

        processed += 1
        if processed % 50 == 0:
            print(f"  已处理: {processed}/{len(need_ai)}")

        # 限流：每秒最多2个请求
        time.sleep(0.5)

    print(f"AI精标完成: {processed} 条")


# ============================================================
# 第三步：输出结果
# ============================================================

def save_tags(results, output_path):
    """保存打标结果到JSON"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_path}")


def print_stats(results):
    """打印统计信息"""
    concept_counter = Counter()
    asset_counter = Counter()
    entity_counter = Counter()
    confidence_counter = Counter()

    for post_id, tag in results.items():
        confidence_counter[tag.get("confidence", "unknown")] += 1
        for c in tag.get("concepts", []):
            concept_counter[c] += 1
        for a in tag.get("assets", []):
            asset_counter[a] += 1
        for e in tag.get("entities", []):
            entity_counter[e] += 1

    print("\n" + "=" * 50)
    print("打标统计")
    print("=" * 50)

    print("\n--- 置信度分布 ---")
    for conf, count in confidence_counter.most_common():
        print(f"  {conf}: {count}")

    print("\n--- 概念标签 TOP 15 ---")
    for concept, count in concept_counter.most_common(15):
        print(f"  {concept}: {count}")

    print("\n--- 资产类别 TOP 10 ---")
    for asset, count in asset_counter.most_common(10):
        print(f"  {asset}: {count}")

    print("\n--- 实体标签 TOP 15 ---")
    for entity, count in entity_counter.most_common(15):
        print(f"  {entity}: {count}")


# ============================================================
# 主函数
# ============================================================

def load_posts():
    """加载posts.json"""
    with open(POSTS_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    posts = data.get("posts", [])
    print(f"加载帖子: {len(posts)} 条")
    return posts


def main():
    """主函数"""
    print("=" * 50)
    print("冰冰小美帖子自动打标")
    print("=" * 50)

    # 1. 加载帖子
    posts = load_posts()

    # 2. 规则打标
    results = rule_tag_all(posts)

    # 3. AI精标（默认不开启，先看规则效果）
    # ai_tag_posts(posts, results, max_count=100)

    # 4. 统计
    print_stats(results)

    # 5. 保存
    save_tags(results, OUTPUT_JSON)


if __name__ == "__main__":
    main()
