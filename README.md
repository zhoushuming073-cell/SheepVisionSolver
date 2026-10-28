# SheepVisionSolver

Windows 桌面视觉原型。实时输入仅为屏幕截图，交互仅为 Windows 鼠标单击；研究 APK 得到的源码和资源不会在运行时提供隐藏牌信息。当前只识别经典 `block_1`–`block_16` 图案。

公开源码仓库只包含程序、文档和构建脚本。游戏 APK、提取的原版牌面、实测截图与日志、编译后的 exe 均保留在本地，不随源码发布。新克隆仓库需用自己有权研究的 APK 提取模板后，才能运行视觉识别或构建 exe；没有模板时视觉测试会跳过。

完整的[实现与复现手册](docs/implementation.md)公开了各模块职责、模板提取、识别阈值、增量状态、点击事务、错误恢复、测试及打包步骤。项目自有源码按 [MIT 许可证](LICENSE) 开源；原版游戏素材不在该许可范围内。

## 直接打开 Windows 程序

在已完成本地构建的电脑上，双击 `dist/SheepVisionSolver_v04_live.exe`。这是包含本轮现场修复的单文件版本，已内置 Python 运行时、识别依赖和经典牌模板；使用 exe 无需先安装 Python。首次启动需等待解包。校准配置、截图和操作日志会写入 exe 旁边的 `data/` 文件夹，因此请把 exe 放在可写入的目录。

当前源码中的 GUI 首选「框选游戏区域（自动分区）」：只框整个游戏画面，程序自行标定主牌、左右牌堆和七格卡槽，并自动截图识别。也可用「自动发现游戏窗口」。只有自动分区不可靠时，才在预览图上手动修正主牌区或卡槽区。确认调试框后，使用「自动选择并点击一步」。连续操作需主动点击启动按钮，`Ctrl+Alt+S` 为紧急停止。

历史 v0.2 exe 曾在 Windows 游戏窗口验证 1 次单步及随后 11 次连续有效点击。当前 `v04_live` 源码又经过 8 次真实确认点击，其中 5 次来自同一 GUI 连续运行；这些是短时功能测试，不代表通关率。停止时若有未完成的点击，等动画结束后重新截图、识别，再启动。

本地待审的 [v0.3 遮挡视觉升级](docs/occlusion_v03.md)根据 APK 黑罩纹理及遮挡代码识别部分可见暗牌，并在调试图标注明暗与可见程度；暗牌始终不自动点击。

本地待审的[连续交互升级](docs/reliability_v04.md)加入单次框选自动分区、目标遮挡复核、卡槽冲突独立复核和有界全量恢复，已包含在 `SheepVisionSolver_v04_live.exe`。辅助牌堆仅被观察，不参与自动点击；胜负结算仍需实机样本验证后接入。

## 环境

- Windows 10/11，Python 3.11+。
- GUI：Tkinter（通常随 Windows Python 安装）、Pillow、OpenCV、NumPy、pynput。
- REA/JADX 不是桌面程序的运行依赖；仅用于离线 APK 研究。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[desktop,dev]"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python -m pytest -q
$env:PYTHONPATH='src'
.venv\Scripts\python scripts\benchmark_synthetic.py
```

## 启动 GUI

```powershell
.venv\Scripts\python -m sheep_solver.gui.app
```

开发者重新打包：

```powershell
# 先按下方“重新提取经典模板”步骤准备本地素材
.venv\Scripts\python -m pip install pyinstaller
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

1. 点「框选游戏区域（自动分区）」，拖动框住整个游戏画面；程序会自动截屏、区分主牌与左右牌堆、定位七格卡槽并识别。若定位失败，再在预览图上修正主牌区或卡槽区。
2. 检查绿色（候选可点）和黄色（需核实）的框；可调整“模板缩放”后重试。截图与调试图保存在 `data/screenshots/`。
3. 可从列表选择牌并单步点击，也可点「自动选择并点击一步」。连续模式必须手动点「开始基础自动操作」。暂停、停止或 **Ctrl+Alt+S** 可停止。

首次完整识别后，自动流程维护稳定方块 ID 和模拟卡槽。每步复核目标的牌型与可点击性；点击后监测画面稳定，再识别局部区域。卡槽快速计数冲突会触发独立模板复核；可恢复的视觉冲突有界全量同步，未确认的点击绝不重放。连续模式复用单一工作线程，避免逐步生成调试图；卡槽余两格时仅尝试有可见证据支持的解锁动作。控制窗口会移出游戏截图区域；窗口平移自动更新坐标，缩放则重新校准。自动点击坐标只允许在所选游戏区域与主牌区交集内。架构和实测结果见 [v0.2 增量视觉说明](docs/incremental_v02.md)及[连续交互升级](docs/reliability_v04.md)。

## 离线识别用户截图

```powershell
.venv\Scripts\python -m sheep_solver.vision.cli data\screenshots\daily-2026-10-27-sample.png --board 190,190,510,580 --tray 80,1090,730,125 --scale 0.68 --output data\screenshots\daily-debug.png
```

坐标格式为 `left,top,width,height`，相对于截图左上角。离线模式只产出识别 JSON 和调试图，不点击桌面。
上述示例截图仅存在于开发机本地；从公开仓库克隆后请换成自己的截图，并重新标定两个区域。

## 重新提取经典模板

```powershell
.venv\Scripts\python scripts\extract_apk_tiles.py "<你有权研究的游戏 APK 路径>"
```

素材输出到 `assets/tiles/classic/`，包含来源 UUID 与裁切记录。游戏素材只供授权范围内的本地研究。

## 当前限制

覆盖不完整的方块可能不会被检出；被遮挡或重叠的候选默认不能自动点击。不同皮肤、窗口缩放与动画可能需要重新校准模板缩放和识别阈值。主牌与卡槽使用不同模板缩放，当前卡槽比例来自一次现场画面，其他窗口尺寸仍需核对。局部状态冲突会回退到完整识别，现场一次约耗时 8.1 秒；当前没有独立测试通关率。

原先的人工小牌局模拟器仍在 `src/sheep_solver/simulator/`，仅供离线测试；它不参与桌面自动点击。
