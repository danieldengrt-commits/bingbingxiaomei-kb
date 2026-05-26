"""
评测脚本：跑 12 道测试题，收集系统回答供人工审查
用法: python3 src/rag/eval_questions.py
输出: data/eval_results.json
"""
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.rag.chat import generate_stream, get_cached

QUESTIONS = [
    # A类 - 简单事实查询
    ("A", "冰美有没有提过西部矿业？"),
    ("A", "冰美在2024年3月左右的帖子主要说了什么？"),
    ("A", "冰美对铜的看法是什么？"),
    # B类 - 概念解释
    ("B", "什么是亏钱效应？怎么观察？"),
    ("B", "什么是情绪标？怎么用它判断市场？"),
    ("B", "假象是什么意思？冰美怎么讲假象的？"),
    ("B", "恐惧和风险有什么区别？"),
    # C类 - 深度分析
    ("C", "怎么看现在的市场情绪位置？"),
    ("C", "冰美怎么选股的？框架是什么？"),
    ("C", "怎么理解主动买亏这个交易理念？"),
    ("C", "双供应链格局下，怎么选标的？"),
    ("C", "冰美的仓位管理逻辑是什么？"),
]

OUTPUT = Path(__file__).parent.parent.parent / "data" / "eval_results.json"


def run_one(qtype: str, question: str) -> dict:
    """运行一个问题，收集完整回答和元数据"""
    print(f"\n{'='*60}")
    print(f"[{qtype}] {question}")
    print(f"{'='*60}")

    t0 = time.time()
    full_answer = ""
    sources = []
    error = None

    try:
        for event in generate_stream(question):
            if event.startswith("data: "):
                data_str = event[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    if data.get("type") == "token":
                        full_answer += data.get("text", "")
                    elif data.get("type") == "sources":
                        sources = data.get("sources", [])
                    elif data.get("type") == "error":
                        error = data.get("message", "")
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        error = str(e)

    elapsed = time.time() - t0
    print(f"耗时: {elapsed:.1f}s")
    print(f"长度: {len(full_answer)} 字")
    print(f"来源: {len(sources)} 条")
    if error:
        print(f"错误: {error}")
    print(f"预览: {full_answer[:200]}...")

    return {
        "type": qtype,
        "question": question,
        "answer": full_answer,
        "sources": sources,
        "error": error,
        "elapsed": round(elapsed, 1),
        "chars": len(full_answer),
    }


def main():
    # 清理缓存，确保每次都是真实检索
    from src.rag.chat import _cache
    _cache.clear()

    print("=" * 60)
    print("AI 冰美 评测集 — 12 题")
    print("=" * 60)

    results = []
    for i, (qtype, question) in enumerate(QUESTIONS):
        print(f"\n[{i+1}/12]")
        result = run_one(qtype, question)
        results.append(result)
        # 错开请求，避免 API 限流
        if i < len(QUESTIONS) - 1:
            time.sleep(1)

    # 保存结果
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n结果已保存到 {OUTPUT}")

    # 快速统计
    print(f"\n{'='*60}")
    print("统计")
    print(f"{'='*60}")
    by_type = {"A": [], "B": [], "C": []}
    for r in results:
        by_type[r["type"]].append(r)
    for t in ["A", "B", "C"]:
        items = by_type[t]
        avg_chars = sum(r["chars"] for r in items) / len(items) if items else 0
        avg_sources = sum(len(r["sources"]) for r in items) / len(items) if items else 0
        avg_time = sum(r["elapsed"] for r in items) / len(items) if items else 0
        errors = sum(1 for r in items if r.get("error"))
        print(f"  类型{t}: 平均{avg_chars:.0f}字, {avg_sources:.1f}来源, {avg_time:.1f}s, {errors}错误")


if __name__ == "__main__":
    main()
