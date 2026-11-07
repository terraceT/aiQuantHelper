"""运维手册检索：docs/runbook.md 按「## R编号 标题」切块，BM25 打分（中文按字的 2-gram，英文/数字按词）。

手册文件改了会自动重建索引（按修改时间判断），不用重启。
"""
import math
import re
import threading
from collections import Counter

from .config import ROOT

RUNBOOK = ROOT / "docs" / "runbook.md"


def tokenize(text):
    text = text.lower()
    toks = re.findall(r"-?\d+|[a-z][a-z0-9_]*", text)
    toks += [t.lstrip("-") for t in toks if t.startswith("-")]          # -2015 同时能被 2015 搜到
    for run in re.findall(r"[一-鿿]+", text):
        toks += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return toks


class Runbook:
    def __init__(self, path=RUNBOOK):
        self.path, self._mtime, self._lock = path, None, threading.Lock()
        self.chunks = []

    def _build(self):
        text = self.path.read_text(encoding="utf-8")
        parts = re.split(r"(?m)^## ", text)[1:]
        self.chunks = []
        for p in parts:
            head, _, body = p.partition("\n")
            m = re.match(r"(R\d+)\s+(.*)", head.strip())
            cid, title = (m.group(1), m.group(2)) if m else ("", head.strip())
            self.chunks.append({"id": cid, "title": title, "text": body.strip()})
        self.docs = [tokenize((c["title"] + " ") * 2 + c["text"]) for c in self.chunks]   # 标题加权
        self.tf = [Counter(d) for d in self.docs]
        n = len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.avgdl = sum(len(d) for d in self.docs) / max(n, 1)

    def _ensure(self):
        with self._lock:
            m = self.path.stat().st_mtime
            if m != self._mtime:
                self._build()
                self._mtime = m

    def search(self, query, k=3):
        self._ensure()
        q = tokenize(query)
        ids = {w.upper() for w in re.findall(r"\b[rR]\d+\b", query)}
        k1, b = 1.5, 0.75
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            s = sum(self.idf.get(t, 0) * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * dl / self.avgdl))
                    for t in q if t in tf)
            if self.chunks[i]["id"] in ids:
                s += 100                     # 直接问编号（R5）就给那一条
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [{"id": self.chunks[i]["id"], "title": self.chunks[i]["title"], "score": round(s, 2),
                 "text": self.chunks[i]["text"]} for s, i in scored[:k]]
