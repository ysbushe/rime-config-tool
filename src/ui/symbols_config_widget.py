"""符号表配置页（SymbolsConfigWidget）。

分类（左侧）+ 符号条目（右侧）CRUD。
字段由 FieldMap 决定（分类键来自 symbols:）。
写回前经 BackupService 备份。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.repo.symbols_repo import SymbolsRepo
from src.service.backup_service import BackupService
from src.service.deploy_service import DeployService
from src.settings import Settings
from src.ui.delete_confirm_dialog import confirm_delete


class SymbolsConfigWidget(QWidget):
    """symbols_v.yaml 可视化管理界面。"""

    def __init__(self, repo: SymbolsRepo, backup: BackupService,
                 settings: Settings | None = None,
                 deploy: DeployService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._backup = backup
        self._settings = settings
        self._deploy = deploy
        self._current_category = ""
        self._dirty = False
        self._build_ui()
        self.refresh_categories()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setSpacing(12)

        # 左侧分类
        left = QVBoxLayout()
        left.addWidget(QLabel("分类"))
        self._cats = QListWidget()
        self._cats.currentItemChanged.connect(self._on_category_changed)
        left.addWidget(self._cats)
        cat_actions = QHBoxLayout()
        self._btn_add_cat = QPushButton("+ 分类")
        self._btn_del_cat = QPushButton("删除")
        self._btn_del_cat.setObjectName("Danger")
        self._btn_add_cat.clicked.connect(self._on_add_category)
        self._btn_del_cat.clicked.connect(self._on_del_category)
        cat_actions.addWidget(self._btn_add_cat)
        cat_actions.addWidget(self._btn_del_cat)
        left.addLayout(cat_actions)
        root.addLayout(left, 1)

        # 右侧符号
        right = QVBoxLayout()
        self._cat_title = QLabel("符号条目")
        right.addWidget(self._cat_title)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["符号", "操作"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 200)
        right.addWidget(self._table, 1)

        sym_actions = QHBoxLayout()
        self._btn_add_sym = QPushButton("+ 符号")
        self._btn_add_sym.setObjectName("Primary")
        self._btn_add_sym.clicked.connect(self._on_add_symbol)
        self._btn_save = QPushButton("保存符号表")
        self._btn_save.setObjectName("Primary")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        sym_actions.addWidget(self._btn_add_sym)
        sym_actions.addWidget(self._btn_save)
        right.addLayout(sym_actions)

        self._status = QLabel("")
        self._status.setProperty("role", "info")
        right.addWidget(self._status)
        root.addLayout(right, 2)

    # ------------------------------------------------------------------ #
    def refresh_categories(self) -> None:
        self._cats.clear()
        for c in self._repo.categories():
            self._cats.addItem(c)
        if self._cats.count() > 0:
            self._cats.setCurrentRow(0)

    def _on_category_changed(self, current: QListWidgetItem, _prev) -> None:
        if current is None:
            return
        self._current_category = current.text()
        self._cat_title.setText(f"符号条目 · {self._current_category}")
        self._refresh_symbols()

    def _refresh_symbols(self) -> None:
        self._table.setRowCount(0)
        if not self._current_category:
            return
        for sym in self._repo.get_symbols(self._current_category):
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(sym))
            btn = QPushButton("删除")
            btn.setObjectName("Danger")
            btn.clicked.connect(lambda _=False, s=sym: self._on_del_symbol(s))
            self._table.setCellWidget(row, 1, btn)

    # ------------------------------------------------------------------ #
    def _on_add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "新建分类", "分类键（如 /fh）：")
        if ok and name.strip():
            self._repo.add_category(name.strip())
            self.refresh_categories()

    def _on_del_category(self) -> None:
        if not self._current_category:
            return
        if not confirm_delete(
            self,
            "删除分类",
            f"确认删除分类「{self._current_category}」及其全部符号？",
        ):
            return
        self._repo.remove_category(self._current_category)
        self.refresh_categories()

    def _on_add_symbol(self) -> None:
        if not self._current_category:
            QMessageBox.information(self, "提示", "请先选择分类。")
            return
        sym, ok = QInputDialog.getText(self, "新增符号", "符号字符：")
        if ok and sym.strip():
            self._repo.add_symbol(self._current_category, sym.strip())
            self._refresh_symbols()
            self._mark_dirty()

    def _on_del_symbol(self, sym: str) -> None:
        if not self._current_category:
            return
        if not confirm_delete(
            self,
            "删除符号",
            f"确认删除分类「{self._current_category}」中的符号「{sym}」？\n"
            "（删除后仍需点『保存符号表』才会写盘）",
        ):
            return
        self._repo.remove_symbol(self._current_category, sym)
        self._refresh_symbols()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._btn_save.setEnabled(True)
        self._status.setText("● 有未保存的修改（点『保存符号表』写盘）")

    def _on_save(self) -> None:
        if not self._dirty:
            return
        try:
            self._backup.backup("symbols_v.yaml")
            self._repo.save()
            self._dirty = False
            self._btn_save.setEnabled(False)
            self._status.setText(f"已保存。{self._deploy_message()}")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"写入符号表失败：{exc}")

    def _deploy_message(self) -> str:
        if self._settings is None or self._deploy is None:
            return ""
        if self._settings.sandbox_mode:
            return "沙盒模式，未触发真实部署。"
        if not self._settings.auto_deploy:
            return "未开启自动部署。"
        ok, msg = self._deploy.deploy()
        return "已自动部署。" if ok else f"部署提示：{msg}"