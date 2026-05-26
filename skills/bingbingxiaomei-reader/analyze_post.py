#!/usr/bin/env python3
"""调用DeepSeek分析冰冰小美的帖子，给出解读。"""

import json
import sys
import requests
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "configs" / "deepseek_config.json"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def analyze_post(post_content: str, date: str, time: str) -> str:
    config = load_config()
    api_key = config["api_key"]
    model = config.get("model", "deepseek-chat")

    prompt = f"""你是冰冰小美的投资分析助手。冰冰小美是一位雪球博主，擅长产业分析、情绪判断和宏观推演。

请分析以下帖子，用简洁的中文回答：

1. **核心观点**：这条帖子想说什么（1-2句话）
2. **产业逻辑**：涉及哪些产业链环节，逻辑是什么
3. **情绪信号**：冰美在表达什么情绪（乐观/警惕/中性）
4. **关键概念**：涉及哪些关键概念，简要解释

帖子日期：{date} {time}

帖子内容：
{post_content}

要求：简洁直接，不要废话，不要用"首先""其次"这种连接词。"""

    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 500
    }

    response = requests.post(url, headers=headers, json=data, timeout=30)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"]


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("用法: python analyze_post.py <帖子内容> <日期> <时间>")
        sys.exit(1)

    content = sys.argv[1]
    date = sys.argv[2]
    time = sys.argv[3]

    analysis = analyze_post(content, date, time)
    print(analysis)
