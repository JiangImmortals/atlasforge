"""AtlasForge 宿主：只有 Agent 工作台，不创建翻译或全局输入服务。"""
import argparse,sys,logging
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from .paths import DATA
from .settings import Settings
from .models import ModelService
from .ui.window import Window
from .ui.theme import STYLE
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-test',action='store_true');args=parser.parse_args()
    DATA.mkdir(parents=True,exist_ok=True);logging.basicConfig(filename=DATA/'atlasforge.log',level=logging.INFO,encoding='utf-8')
    app=QApplication(sys.argv[:1]);app.setQuitOnLastWindowClosed(False);app.setStyleSheet(STYLE)
    settings=Settings.load();models=ModelService(settings);window=Window(settings,models)
    window.statusBar().showMessage('Agent Demo 就绪 · 请先设置本地服务或云端 API')
    jobs=[]
    if settings.engine_auto_update and not args.smoke_test:
        from .agents.engines import automatic_update
        from .ui.workers import Job
        job=Job(automatic_update);jobs.append(job)
        job.failed.connect(lambda error:window.statusBar().showMessage('上游升级未切换：'+error))
        QTimer.singleShot(10000,job.start)
    def shutdown():
        if any(job.isRunning() for job in jobs):
            window.statusBar().showMessage('等待上游升级安全结束后退出…');QTimer.singleShot(300,shutdown);return
        models.stop();app.quit()
    window.exiting.connect(shutdown)
    window.show()
    if args.smoke_test:QTimer.singleShot(1200,window.exit_app)
    return app.exec()
