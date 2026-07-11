"""PhraseRepo 数据层测试：冲突合并、搜索、排序、写出回读。"""
from __future__ import annotations

from pathlib import Path

from src.repo.phrase_repo import PhraseRepo, DEFAULT_WEIGHT


def test_load_counts(phrase_repo: PhraseRepo) -> None:
    # 样例含 13 条数据行（不含注释/空行）
    assert phrase_repo.count() == 13


def test_conflict_keeps_existing_weight_without_explicit_change(phrase_repo: PhraseRepo) -> None:
    """同 text+code 冲突不再静默加权，并返回 conflict=True。"""
    before = phrase_repo.find("焊接", "hanj").weight
    phrase, is_new, conflict = phrase_repo.upsert("焊接", "hanj")
    assert is_new is False
    assert conflict is True
    assert phrase.weight == before


def test_upsert_new(phrase_repo: PhraseRepo) -> None:
    """不同 code → 新增，不冲突。"""
    phrase, is_new, conflict = phrase_repo.upsert("焊接", "hanjie")
    assert is_new is True
    assert conflict is False
    assert phrase.code == "hanjie"
    assert phrase_repo.count() == 14


def test_different_code_same_text_not_conflict(phrase_repo: PhraseRepo) -> None:
    """同 text 不同 code（如 工程 gch / gongch）互不冲突，均视为新增。"""
    # 用 fixture 中不存在的新词条，避免与样例（已含 工程 gch/gongch）冲突
    e1, n1, c1 = phrase_repo.upsert("同词不同码", "code_a")
    e2, n2, c2 = phrase_repo.upsert("同词不同码", "code_b")
    assert c1 is False and c2 is False
    assert n1 is True and n2 is True
    assert phrase_repo.count() == 15


def test_search(phrase_repo: PhraseRepo) -> None:
    res = phrase_repo.search("箱")
    assert len(res) == 2
    res_code = phrase_repo.search("wh")
    assert any(p.text == "武汉" for p in res_code)


def test_sort_by_weight_desc(phrase_repo: PhraseRepo) -> None:
    ordered = phrase_repo.sort_by("weight", reverse=True)
    weights = [p.weight for p in ordered]
    assert weights == sorted(weights, reverse=True)


def test_save_roundtrip(temp_rime_dir) -> None:
    """写出后再读回，条目数一致（不丢数据）。"""
    path = str(temp_rime_dir / "custom_phrase.txt")
    repo = PhraseRepo(path)
    repo.upsert("测试词", "cs", 3)
    repo.save()
    repo2 = PhraseRepo(path)
    assert repo2.find("测试词", "cs") is not None
    assert repo2.find("测试词", "cs").weight == 3


def test_tab_discipline(temp_rime_dir) -> None:
    """写出格式严格为 文本<Tab>编码<Tab>权重，无空格分隔。"""
    path = str(temp_rime_dir / "custom_phrase.txt")
    repo = PhraseRepo(path)
    repo.upsert("一个词", "ig", 5)
    repo.save()
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    last = lines[-1]
    parts = last.split("\t")
    assert len(parts) == 3
    assert parts[0] == "一个词" and parts[1] == "ig" and parts[2] == "5"
