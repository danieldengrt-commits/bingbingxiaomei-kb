"""
检索：用自然语言搜冰美相关内容
用法：python3 query.py "亏钱效应是什么"
"""
import sys
import os

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import chromadb
from sentence_transformers import SentenceTransformer

DB_DIR = os.path.expanduser("~/bingbingxiaomei-kb/data/rag_db")
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
TOP_K = 10


def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("请输入查询: ")

    print(f"正在加载模型...")
    model = SentenceTransformer(MODEL_NAME)

    print(f"正在检索: {query}\n")
    client = chromadb.PersistentClient(path=DB_DIR)
    collection = client.get_collection("bingbingxiaomei")

    query_embedding = model.encode([query], normalize_embeddings=True).tolist()

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=TOP_K,
    )

    for i, (doc_id, doc_text, meta, distance) in enumerate(zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )):
        similarity = 1 - distance
        print(f"{'='*60}")
        print(f"[{i+1}] 相似度: {similarity:.3f} | 日期: {meta['date']} | 类型: {meta['category']}")
        print(f"{'='*60}")
        print(doc_text[:500])
        print()


if __name__ == "__main__":
    main()
