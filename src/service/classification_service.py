"""自动分组规则（ClassificationService）。

依据词条文本自动推断分组，供「自动分组」按钮一次性归类。设计原则：
    - 确定、可解释，便于用户在分组侧栏手动微调；
    - 默认只归类「未分组」条目，绝不覆盖用户已有的手动分组。

判定优先级：
    1. 英文     : 含 ASCII 字母（如 hello / user@example.com / API）
    2. 符号数字 : 无汉字、无字母（如 123 / ★ / →）
    3. 单字     : 恰好 1 个汉字（如 一）
    4. 地名     : 含地名词素（省/市/县/路/街/桥/江/湖…）或 国/州 后缀
    5. 人名     : 以《百家姓》常见姓氏开头 + 长度 2~4 字 + 非地名词尾
    6. 其他     : 其余中文词/短语（默认组）
"""
from __future__ import annotations

import re

# 分组名常量
G_ENGLISH = "英文"
G_SYMBOL = "符号数字"
G_SINGLE = "单字"
G_PLACE = "地名"
G_PERSON = "人名"
G_OTHER = "其他"

# 自动分组（顺序即优先级）
AUTO_GROUPS = [G_ENGLISH, G_SYMBOL, G_SINGLE, G_PLACE, G_PERSON, G_OTHER]

# 含汉字
_CJK = re.compile(r"[\u4e00-\u9fff]")
# 含 ASCII 字母
_HAS_LATIN = re.compile(r"[A-Za-z]")

# 地名词素（单字集合）：省/市/县/区/镇/乡/村/街/道/路/巷/弄/里/坊/桥/江/河/湖/海/
# 山/岛/港/湾/津/渡/场/站/园/苑/庄/岭/岗/屯/堡/寨/坪/坝/口/岸/州/府/卫/所/墟/圩/埠
_PLACE_MORPHEMES = set(
    "省市县区镇乡村街道路巷弄里坊桥江河湖海山岛港湾津渡场站园苑庄岭岗"
    "屯堡寨坪坝口岸州府卫所墟圩埠"
)
# 地名词尾（整词后缀）
_PLACE_SUFFIX = ("国", "州", "府", "县", "区", "镇", "乡", "村", "街", "路", "道", "桥", "港", "岛")

# 常见姓氏（百家姓前 ~100），用于人名启发式
_SURNAMES = set(
    "王李张刘陈杨黄赵周吴徐孙朱马胡郭林何高梁郑罗宋谢唐韩曹许邓萧冯曾程"
    "蔡彭潘袁于董余苏叶吕魏蒋田杜丁沈姜范江傅钟卢汪戴崔任陆廖姚方金邱"
    "夏谭韦贾邹石熊孟秦阎薛侯雷白龙段郝孔邵史毛常万顾赖武康贺严尹钱施牛洪龚欧"
)


class ClassificationService:
    """词条自动归类。"""

    @staticmethod
    def auto_groups() -> list[str]:
        """返回全部自动分组名（供 UI 初始化分组列表用）。"""
        return list(AUTO_GROUPS)

    @classmethod
    def classify(cls, text: str) -> str:
        """返回文本应归入的分组名。空文本归『其他』。"""
        t = (text or "").strip()
        if not t:
            return G_OTHER

        has_cjk = bool(_CJK.search(t))
        has_latin = bool(_HAS_LATIN.search(t))

        # 1. 英文
        if has_latin:
            return G_ENGLISH
        # 2. 符号数字（无汉字、无字母）
        if not has_cjk:
            return G_SYMBOL
        # 3. 单字
        if len(t) == 1:
            return G_SINGLE
        # 4. 地名
        if cls._is_place(t):
            return G_PLACE
        # 5. 人名
        if cls._is_person(t):
            return G_PERSON
        # 6. 其他
        return G_OTHER

    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_place(t: str) -> bool:
        if any(ch in _PLACE_MORPHEMES for ch in t):
            return True
        return t.endswith(_PLACE_SUFFIX)

    @staticmethod
    def _is_person(t: str) -> bool:
        if not (2 <= len(t) <= 4):
            return False
        if t[0] not in _SURNAMES:
            return False
        # 不以地名词尾结尾（避免『王路』之类被误判为人名）
        return not t.endswith(_PLACE_SUFFIX)
