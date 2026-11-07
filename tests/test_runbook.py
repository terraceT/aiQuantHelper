import pytest

from app.runbook import Runbook

rb = Runbook()


@pytest.mark.parametrize("q,expect", [
    ("-2015 Invalid API-key", "R5"),
    ("2015 错误怎么办", "R5"),
    ("签名不对 Signature", "R6"),
    ("时间不同步 recvWindow", "R7"),
    ("连不上币安 怎么配置代理", "R8"),
    ("强平价是什么意思", "R11"),
    ("资金费怎么算", "R12"),
    ("DeepSeek 402 余额不足", "R10"),
    ("端口被占用 页面打不开", "R15"),
    ("怎么修改演示数据", "R16"),
    ("R19", "R19"),
])
def test_top3_hit(q, expect):
    ids = [r["id"] for r in rb.search(q, 3)]
    assert expect in ids, (q, ids)


def test_nonsense_query_returns_nothing():
    assert rb.search("zzqxw qqvvk", 3) == []


def test_rebuilds_when_file_changes(tmp_path):
    p = tmp_path / "rb.md"
    p.write_text("# x\n## R1 苹果\n苹果很好吃\n", encoding="utf-8")
    r = Runbook(p)
    assert r.search("香蕉") == []
    import os, time
    p.write_text("# x\n## R1 苹果\n苹果很好吃\n## R2 香蕉\n香蕉是黄色的\n", encoding="utf-8")
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert r.search("香蕉")[0]["id"] == "R2"
