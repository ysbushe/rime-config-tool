"""自动分组规则单元测试（不依赖任何 Rime 配置 / 文件 IO）。"""
from src.service.classification_service import (
    G_ENGLISH,
    G_OTHER,
    G_PERSON,
    G_PLACE,
    G_SINGLE,
    G_SYMBOL,
    ClassificationService as CS,
)


def test_english():
    assert CS.classify("hello") == G_ENGLISH
    assert CS.classify("user@example.com") == G_ENGLISH
    assert CS.classify("API") == G_ENGLISH


def test_symbol_number():
    assert CS.classify("123") == G_SYMBOL
    assert CS.classify("★") == G_SYMBOL
    assert CS.classify("→") == G_SYMBOL


def test_single_char():
    assert CS.classify("一") == G_SINGLE
    assert CS.classify("的") == G_SINGLE


def test_place():
    assert CS.classify("武汉市") == G_PLACE
    assert CS.classify("北京市朝阳区") == G_PLACE
    assert CS.classify("长江路") == G_PLACE


def test_person():
    assert CS.classify("张三") == G_PERSON
    assert CS.classify("李娜") == G_PERSON
    assert CS.classify("欧阳修") == G_PERSON


def test_other():
    assert CS.classify("焊接") == G_OTHER
    assert CS.classify("工程") == G_OTHER


def test_empty():
    assert CS.classify("") == G_OTHER
    assert CS.classify("   ") == G_OTHER


def test_auto_groups_ordered():
    groups = CS.auto_groups()
    assert groups[0] == G_ENGLISH
    assert groups[-1] == G_OTHER
