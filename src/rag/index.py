"""
建索引：读取所有帖子/专栏/体系/案例 + 概念卡片，向量化存入 ChromaDB
"""
import os
import re
import yaml
from pathlib import Path

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import chromadb
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

# ===== 配置 =====
PROJECT_ROOT = Path(__file__).parent.parent.parent  # bingbingxiaomei-kb/
VAULT_ROOT = PROJECT_ROOT / "vault"

# 帖子/输入类目录
INPUT_DIRS = {
    "帖子": VAULT_ROOT / "输入" / "贴子",
    "专栏": VAULT_ROOT / "输入" / "专栏",
    "交易体系": VAULT_ROOT / "输入" / "交易体系",
    "三要素案例": VAULT_ROOT / "输入" / "三要素案例",
}

# 概念卡片目录
CONCEPT_DIRS = {
    "核心概念": VAULT_ROOT / "1-核心概念",
    "产业标的": VAULT_ROOT / "2-产业标的",
    "专题整理": VAULT_ROOT / "3-专题整理",
}

DB_DIR = str(PROJECT_ROOT / "data" / "rag_db")
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
BATCH_SIZE = 100


def parse_md(filepath):
    """解析 markdown 文件，返回 (metadata_dict, content_string)"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    meta = {}
    content = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                pass
            content = parts[2].strip()

    return meta, content


def collect_files(base_dirs, file_type):
    """递归收集目录下的所有 .md 文件"""
    files = []
    for category, dir_path in base_dirs.items():
        if not dir_path.exists():
            print(f"  [跳过] {category}: 目录不存在 ({dir_path})")
            continue
        for fpath in dir_path.rglob("*.md"):
            if fpath.name.startswith("."):
                continue
            files.append((category, fpath, file_type))
    return files


def build_url(file_type, category, rel_path, meta):
    """根据文件类型生成网站内 URL"""
    if file_type == "concept":
        # 概念卡片的 URL
        fname = rel_path.stem
        # 尝试从文件名推断 node_id
        return f"/article/{rel_path.as_posix()}"
    else:
        return f"/posts/read?path={rel_path.as_posix()}"


def main():
    print("Loading embedding model...")
    model = SentenceTransformer(MODEL_NAME)

    print("Initializing ChromaDB...")
    client = chromadb.PersistentClient(path=DB_DIR)

    # 删除旧 collection 重建（确保 schema 一致）
    try:
        client.delete_collection("bingbingxiaomei")
        print("  已删除旧索引，重建中...")
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name="bingbingxiaomei",
        metadata={"hnsw:space": "cosine"},
    )

    # 收集所有文件
    all_files = []
    post_files = collect_files(INPUT_DIRS, "post")
    concept_files = collect_files(CONCEPT_DIRS, "concept")
    all_files = post_files + concept_files

    print(f"帖子/输入: {len(post_files)} 篇")
    print(f"概念卡片: {len(concept_files)} 张")
    print(f"总计: {len(all_files)} 个文件")

    # 逐批处理
    for i in tqdm(range(0, len(all_files), BATCH_SIZE), desc="索引进度"):
        batch = all_files[i : i + BATCH_SIZE]
        ids = []
        documents = []
        metadatas = []

        for category, filepath, file_type in batch:
            meta, content = parse_md(filepath)
            if not content or len(content.strip()) < 20:
                continue

            rel_path = filepath.relative_to(VAULT_ROOT)
            doc_id = f"{file_type}_{category}_{rel_path.stem}"
            # 清理 id（ChromaDB 不接受特殊字符）
            doc_id = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff-]", "_", doc_id)

            url = build_url(file_type, category, rel_path, meta)
            title = str(meta.get("title", rel_path.stem))

            ids.append(doc_id)
            # 截断文档内容，避免太长（ChromaDB 建议 < 512 tokens per doc 但不强制）
            documents.append(content[:3000])
            metadatas.append({
                "type": file_type,
                "category": category,
                "filename": rel_path.name,
                "path": rel_path.as_posix(),
                "date": str(meta.get("published", meta.get("created", ""))),
                "title": title,
                "source": str(meta.get("source", "")),
                "url": url,
                "tags": ", ".join(str(t) for t in meta.get("tags", []) if t) if isinstance(meta.get("tags"), list) else "",
            })

        if not documents:
            continue

        embeddings = model.encode(documents, normalize_embeddings=True).tolist()
        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    print(f"\n索引完成！共 {collection.count()} 条记录")


if __name__ == "__main__":
    main()
