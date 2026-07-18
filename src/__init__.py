"""个人 RIME 小狼毫配置小工具 - 源码包。

模块分层：
    config   路径探测 / 字段映射 / 全局路径常量
    utils    日志 / 编码（UTF-8 无 BOM）工具
    encoding 编码生成策略（手动 / 全拼）
    repo     数据仓储（词库 / 方案 / 符号表）
    service  业务服务（备份 / 拼音 / 部署 / 热键 / 自启 / 分组）
    ui       PySide6 界面
"""

__app_name__ = "RimeConfig"
__version__ = "0.6.5"
