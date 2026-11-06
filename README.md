# AtlasForge · 擎界

### 让一个上下文，指挥一支执行队伍。

以 Pi 为主框架、AIpy 为执行子 Agent 的桌面实验工作台。

我们希望个人也能组织复杂执行：让主 Agent 保持清晰，让执行者独立探索，让结果有证据、任务有边界、协作可追溯。

> **0.1.0 · 开源 Demo / Experimental**：这是从个人便携工具箱拆出的独立项目，代表方向与起点，不是已经成熟的产品，也不声称达到商业 Agent 或专业输入法的可靠性。

## 现在可以做什么

- 官方 Pi SDK 驱动主 Agent，保留工具调用和会话能力。
- AIpy 在独立进程执行子任务，主 Agent 接收摘要与产物路径。
- 默认最多重做 5 次，保留尝试与产物，主 Agent 判断取舍。
- 历史对话删除与撤销、文件／图片附件、可编辑人格。
- 可选本地或云端 API；本地托管服务按需启动、空闲回收。

## 从源码启动

Windows 10/11，Python **3.12**。三个仓库互不依赖，建议分别创建虚拟环境。

```powershell
git clone https://github.com/JiangImmortals/atlasforge.git
cd atlasforge
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Node.js 22+ 和 Git for Windows 也需要安装，然后执行：

```powershell
npm ci --prefix agent_runtime
```

```powershell
.venv\Scripts\python main.py
```

配置在应用设置页保存到当前项目 `data/`。该目录不进入 Git。每个 Demo 有自己的进程、配置和启动入口，不需要运行另外两个项目。

## 本地模型与资产

仓库不包含模型权重、私人词典、API Key、历史对话或数 GB 的便携运行环境。不要将“git clone”理解为下载全部模型；Python/Node 依赖和模型资产需要另行准备。

如果你已有原便携工具箱，可只在本机导入本项目需要的资产：

```powershell
.venv\Scripts\python tools/import_assets.py --from "原便携工具箱所在目录"
```

导入的是你本地已有文件，不会上传；第三方模型与词典的使用和再分发仍需遵守原许可。全新用户可按 `THIRD_PARTY.md` 的上游链接准备资源。具体路径见 `docs/ASSETS.md`。

## 当前边界

模型能力决定任务可靠性。没有 API/本地服务时只可浏览界面；发起 Agent 任务需要先完成模型配置和 npm 安装。官方 SDK 升级可能破坏接口，不能保证任意未来版本兼容。

本项目的语音、快捷键、终端、云端 API 和长任务并非在每种机器环境中都已验证。当前验证记录见 `docs/VALIDATION.md`。请用真实案例推动改进，而不是把愿景误读成完成度。

## 未来要走的路

1. 先把触发、取消、错误提示和资源回收做可靠。
2. 建立可复现的跨软件回归与性能基准。
3. 改善模型／资产安装和便携打包，让第一次使用更简单。
4. 接受社区贡献，逐步扩展兼容范围和可观测性。

## 参与与许可

欢迎 Issue 与 PR。项目自身代码使用 MIT；第三方依赖、词典、模型遵循各自许可，见 `THIRD_PARTY.md`。

**今天是一颗种子。未来的影响力，靠每一次可验证的改进来赢得。**
