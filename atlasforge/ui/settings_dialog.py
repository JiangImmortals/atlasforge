"""最小设置页：只暴露本项目使用的配置，不加载其他产品模块。"""
from dataclasses import replace
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QCheckBox,QSpinBox,QPlainTextEdit,QDialogButtonBox,QMessageBox

class SettingsDialog(QDialog):
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.settings=settings;self.result_settings=None
        self.setWindowTitle('设置');self.resize(620,600)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form);self.fields={}
        labels=[('backend', '后端 managed / external / cloud'), ('base_url', 'API 地址'), ('model', '模型名称'), ('api_key', 'API Key'), ('context_size', '上下文长度'), ('vision', '图片能力'), ('model_path', '托管模型文件'), ('projector_path', '视觉模型'), ('server_path', 'llama-server 程序'), ('main_system_prompt', '主 Agent 提示词'), ('sub_system_prompt', '子 Agent 提示词'), ('subagent_retries', '重做上限 1～5'), ('pi_provider', 'Pi provider'), ('aipy_provider', 'AIpy provider'), ('engine_auto_update', '自动检查上游升级'), ('close_action', '关闭行为 minimize / tray / exit')]
        for name,label in labels:
            value=getattr(settings,name)
            if isinstance(value,bool):
                widget=QCheckBox();widget.setChecked(value)
            elif isinstance(value,int):
                widget=QSpinBox();widget.setRange(0,262144);widget.setValue(value)
            elif name.endswith('system_prompt'):
                widget=QPlainTextEdit(value);widget.setMinimumHeight(70)
            else:
                widget=QLineEdit(', '.join(value) if isinstance(value,list) else value)
                if name=='api_key':widget.setEchoMode(QLineEdit.Password)
            self.fields[name]=widget;form.addRow(label,widget)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def save(self):
        try:
            values={}
            for name,widget in self.fields.items():
                if isinstance(widget,QCheckBox):value=widget.isChecked()
                elif isinstance(widget,QSpinBox):value=widget.value()
                elif isinstance(widget,QPlainTextEdit):value=widget.toPlainText().strip()
                else:value=widget.text().strip()
                if isinstance(getattr(self.settings,name),list):value=[part.strip() for part in value.split(',')]
                values[name]=value
            self.result_settings=replace(self.settings,**values);self.result_settings.save();self.accept()
        except Exception as exc:QMessageBox.warning(self,'设置无效',str(exc))
