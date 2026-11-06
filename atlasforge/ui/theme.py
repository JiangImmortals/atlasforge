"""统一桌面视觉：工作区、表单和浮窗共享清晰的颜色与间距。"""
STYLE = """
QWidget {font-family:'Microsoft YaHei UI';font-size:13px;color:#dce5ef;}
QMainWindow,QDialog {background:#101722;}
QTabWidget::pane {border:1px solid #29384a;border-radius:8px;background:#151f2d;}
QTabBar::tab {padding:12px 24px;background:#101722;color:#8e9fb4;}
QTabBar::tab:selected {color:#8fceff;background:#1c2a3c;}
QTextBrowser,QPlainTextEdit,QLineEdit,QListWidget,QSpinBox,QComboBox {
background:#111a27;border:1px solid #2b3d52;border-radius:7px;padding:9px;selection-background-color:#345c80;}
QPushButton {background:#24384f;border:1px solid #354e69;border-radius:7px;padding:9px 15px;}
QPushButton:hover {background:#304c6a;} QPushButton:disabled {color:#687b90;}
QLabel#heading {font-size:24px;font-weight:600;color:#f1f6fc;}
QLabel#muted {color:#91a4ba;}
QListWidget::item {padding:10px;} QListWidget::item:selected {background:#28415b;}
QStatusBar {color:#9bb1c8;background:#111a27;}
"""
