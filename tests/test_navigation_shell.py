from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QWidget

from src.ui.navigation_shell import NavigationShell


def test_navigation_shell_keeps_sidebar_and_stack_in_sync(qapp):
    shell = NavigationShell()
    first = QWidget()
    second = QWidget()
    shell.addTab(first, "个人词库")
    shell.addTab(second, "设置与维护")
    changed = QSignalSpy(shell.currentChanged)

    shell.setCurrentIndex(1)

    assert shell.currentIndex() == 1
    assert shell._sidebar.currentRow() == 1
    assert shell.widget(1) is second
    assert changed.count() == 1


def test_navigation_shell_replaces_lazy_page_at_stable_index(qapp):
    shell = NavigationShell()
    shell.addTab(QWidget(), "个人词库")
    placeholder = QWidget()
    shell.addTab(placeholder, "设置与维护")
    replacement = QWidget()
    shell.setCurrentIndex(1)

    shell.blockSignals(True)
    shell.removeTab(1)
    shell.insertTab(1, replacement, "设置与维护")
    shell.setCurrentIndex(1)
    shell.blockSignals(False)

    assert shell.currentIndex() == 1
    assert shell.widget(1) is replacement
    assert shell._sidebar.item(1).text() == "设置与维护"
