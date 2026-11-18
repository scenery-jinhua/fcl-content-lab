"""RAG 检索层：把品牌规范切成块，按商品/角度召回最相关的几段，拼进 prompt。

为什么不把整份品牌规范直接塞进 system prompt：
1. 太长 → token 成本高，而且上下文一长模型注意力会被稀释（「迷失在中间」现象）；
2. 换个商品该带哪段说明是不一样的，全塞等于没重点；
3. 几千字的规范塞进去，每调一次模型就烧一遍，批量 100 条就是 100 倍浪费。

检索策略：有 embedding 就用余弦相似度；没有就退化成关键词加权（简化版 BM25）。
「能退化」这件事很重要——演示环境没有向量库也能跑，这是工程上的可降级设计。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from ..config import settings


@dataclass
class Chunk:
    id: str
    title: str
    text: str
    tokens: int
    vector: list[float] | None = None


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) * 0.6))


class Retriever:
    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self._df: dict[str, int] = {}
        self._loaded = False

    # ---------------- 建索引 ----------------

    def load(self, path: str | None = None) -> None:
        path = path or settings.guidelines_path
        if not os.path.exists(path):
            self.chunks = []
            self._loaded = True
            return
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        self.chunks = self._split(raw)
        self._build_df()
        self._loaded = True

    def _split(self, raw: str) -> list[Chunk]:
        """按二级标题切大段，再按段落聚成 ~chunk_size 字的小块，块间保留 1 段重叠。"""
        sections = re.split(r"\n##\s+", raw)
        chunks: list[Chunk] = []
        for sec in sections:
            if not sec.strip():
                continue
            lines = sec.strip().splitlines()
            title = lines[0].lstrip("# ").strip() if lines else "通用"
            body = "\n".join(lines[1:]).strip()
            if not body:
                continue
            paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
            buf: list[str] = []
            buf_len = 0
            idx = 0
            for p in paras:
                # 列表项很短，单独成块没有意义，跟着上一段走
                if buf_len + len(p) > settings.rag_chunk_size and buf:
                    text = "\n".join(buf)
                    chunks.append(Chunk(f"{title}-{idx}", title, text, _approx_tokens(text)))
                    idx += 1
                    buf = buf[-1:] if len(buf) > 1 else []  # 1 段重叠，避免语义被切断
                    buf_len = sum(len(x) for x in buf)
                buf.append(p)
                buf_len += len(p)
            if buf:
                text = "\n".join(buf)
                chunks.append(Chunk(f"{title}-{idx}", title, text, _approx_tokens(text)))
        return chunks

    def _build_df(self) -> None:
        self._df = {}
        for c in self.chunks:
            for term in set(_terms(c.text)):
                self._df[term] = self._df.get(term, 0) + 1

    async def build_vectors(self, provider) -> None:
        """用 provider 的 embed 一次性把所有块向量化，启动时算一次。"""
        if not self.chunks:
            return
        try:
            vectors = await provider.embed([c.text for c in self.chunks])
            for c, v in zip(self.chunks, vectors):
                c.vector = _normalize(v)
        except Exception:  # noqa: BLE001 - embedding 不可用是预期情况，降级即可
            for c in self.chunks:
                c.vector = None

    # ---------------- 检索 ----------------

    def retrieve(self, query: str, k: int | None = None, query_vector: list[float] | None = None) -> list[Chunk]:
        if not self._loaded:
            self.load()
        k = k or settings.rag_top_k
        if not self.chunks:
            return []
        if query_vector and all(c.vector for c in self.chunks):
            scored = [(c, _cosine(query_vector, c.vector)) for c in self.chunks]
            scored.sort(key=lambda x: x[1], reverse=True)
            return [c for c, s in scored[:k] if s > 0.05]
        # 退化路径：关键词加权
        scored = [(c, _bm25_lite(query, c.text, self._df, len(self.chunks))) for c in self.chunks]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, s in scored[:k] if s > 0]

    def stats(self) -> dict:
        return {
            "chunks": len(self.chunks),
            "indexed": sum(1 for c in self.chunks if c.vector),
            "avg_tokens": round(sum(c.tokens for c in self.chunks) / max(1, len(self.chunks)), 1),
        }


# ---------------- 数学小工具 ----------------

def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]{2,4}|[a-zA-Z]{2,}")


def _terms(text: str) -> list[str]:
    """中文没有空格，粗略按 2-4 字滑窗 + 英文单词切。够用于关键词检索。"""
    return _TOKEN_RE.findall(text)


def _bm25_lite(query: str, doc: str, df: dict[str, int], n_docs: int) -> float:
    q = _terms(query)
    d = _terms(doc)
    if not d:
        return 0.0
    score = 0.0
    for term in set(q):
        tf = d.count(term) / len(d)
        idf = math.log(1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        score += tf * idf
    return score


retriever = Retriever()
