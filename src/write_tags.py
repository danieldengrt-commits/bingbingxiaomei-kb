#!/usr/bin/env python3
"""冰冰小美知识库 - 将打标结果写入帖子frontmatter

读取tags.json，将标签写入每个帖子的md文件头部
"""

import os
import re
import json

# 路径 — 从环境变量读取
VAULT_PATH = os.environ.get("BBXM_VAULT_PATH", ".")
POSTS_DIR = os.path.join(VAULT_PATH, "贴子")
TAGS_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tags.json")
POSTS_JSON = os.path.join(VAULT_PATH, "posts.json")

# 概念分类映射
from tag_schema import CONCEPT_CATEGORIES


def load_tags():
    """加载打标结果"""
    with open(TAGS_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_posts_map():
    """加载posts.json，建立id->fileName的映射"""
    with open(POSTS_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {p['id']: p['fileName'] for p in data['posts']}


def build_frontmatter(tag):
    """构建frontmatter字符串"""
    concepts = tag.get('concepts', [])
    assets = tag.get('assets', [])
    entities = tag.get('entities', [])
    primary = tag.get('primary', '')
    method = tag.get('method', '')

    # 确定primary分类（取第一个概念的一级分类）
    if concepts and not primary:
        primary = CONCEPT_CATEGORIES.get(concepts[0], '')

    lines = ['---']
    lines.append(f'concepts: {json.dumps(concepts, ensure_ascii=False)}')
    lines.append(f'assets: {json.dumps(assets, ensure_ascii=False)}')
    lines.append(f'entities: {json.dumps(entities, ensure_ascii=False)}')
    if primary:
        lines.append(f'primary: {primary}')
    if method:
        lines.append(f'method: {method}')
    lines.append('---')
    return '\n'.join(lines)


def has_frontmatter(content):
    """检查文件是否已有frontmatter"""
    return content.startswith('---\n') or content.startswith('---\r\n')


def update_frontmatter(content, tag):
    """更新已有frontmatter或添加新的"""
    new_fm = build_frontmatter(tag)

    if has_frontmatter(content):
        # 替换已有的frontmatter
        # 找到第二个 --- 的位置
        second_dash = content.find('---', 4)
        if second_dash > 0:
            return new_fm + '\n' + content[second_dash + 4:]
    else:
        # 添加新的frontmatter
        return new_fm + '\n\n' + content

    return content


def process_file(filepath, tag):
    """处理单个文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 跳过太短的帖子（没有标签的）
    if tag.get('confidence') == 'skip':
        return False

    new_content = update_frontmatter(content, tag)

    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False


def main():
    print("=" * 50)
    print("写入帖子frontmatter")
    print("=" * 50)

    # 加载数据
    tags = load_tags()
    posts_map = load_posts_map()

    print(f"打标结果: {len(tags)} 条")
    print(f"文件映射: {len(posts_map)} 条")

    # 处理每个帖子
    updated = 0
    skipped = 0
    errors = 0

    for post_id, tag in tags.items():
        # 获取文件名
        filename = posts_map.get(post_id)
        if not filename:
            errors += 1
            continue

        filepath = os.path.join(POSTS_DIR, filename)
        if not os.path.exists(filepath):
            errors += 1
            continue

        try:
            if process_file(filepath, tag):
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  错误: {post_id} - {e}")
            errors += 1

        if (updated + skipped) % 1000 == 0:
            print(f"  已处理: {updated + skipped}")

    print(f"\n完成!")
    print(f"  更新: {updated}")
    print(f"  跳过: {skipped}")
    print(f"  错误: {errors}")


if __name__ == "__main__":
    main()
