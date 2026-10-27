# 实现与复现手册

本文对应仓库中的 v0.2 Windows 桌面程序。公开的是自主编写的 Python 源码、规则研究记录、模板提取程序、测试与构建步骤。游戏 APK、原版图案、实测截图、校准文件、操作日志和本机打包的 exe 不在仓库中；这些文件的路径已写入 `.gitignore`。源码采用仓库根目录的 MIT 许可证，游戏素材不受该许可证授权。

## 从公开源码运行

在 Windows PowerShell 中进入仓库根目录：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[desktop,dev]"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python -m pytest -q
$env:PYTHONPATH='src'
.venv\Scripts\python scripts\benchmark_synthetic.py
```

公开测试使用程序生成的牌面；`tests/test_vision.py` 中依赖原版模板的附加测试，在没有本地模板时自动跳过。要运行视觉 GUI，先在有权研究的本地环境准备经典模板：

```powershell
.venv\Scripts\python scripts\extract_apk_tiles.py "<本地 APK 的绝对路径>"
.venv\Scripts\python -m sheep_solver.gui.app
```

提取脚本只读 APK ZIP：它查找 Cocos `assets/assets/resources/config.json` 中 `block/daily/block_1` 至 `block_16` 的 `cc.SpriteFrame` 条目，展开压缩 UUID，读取对应 SpriteFrame JSON 与 PNG，再按 `rect` 裁切。生成的 `assets/tiles/classic/manifest.json` 保留内部编号、原图尺寸、偏移和来源记录。编号在运行中保持 `1` 至 `16`，不臆造未经确认的图案名称。提取失败会在清单的 `skipped` 中记录原因。提取的图片只留在本机。

打包前安装 PyInstaller，随后运行：

```powershell
.venv\Scripts\python -m pip install pyinstaller
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

产物是 `dist/SheepVisionSolver.exe`。构建脚本检查 `manifest.json`，把本机模板嵌入 exe；这个本地 exe 及模板不会自动上传到仓库。打包后用户数据写在 exe 同级的 `data/`，只读内置资源从 PyInstaller 的资源目录读取。

## 桌面操作顺序

1. 打开游戏和程序，点击「选择游戏区域」，在全屏覆盖层拖动框住游戏内容。DPI 感知在创建 Tk 窗口前设置；覆盖层使用虚拟桌面的物理坐标，支持多显示器。游戏窗口移动或显示缩放变化后必须重新框选。
2. 点击「获取截图」。控制窗口若挡住选区，会先移到选区外再截图；没有足够位置时拒绝继续。程序用 Pillow `ImageGrab.grab` 获取 Windows 全屏或指定区域，保存的截图只用于本机调试。
3. 在预览图中分别框选「主牌区」和「卡槽区」。主牌区应只覆盖可操作的普通牌，避开盲盒、广告、道具和弹窗。两个区域都以截图左上角为原点，且不能重叠。
4. 点击「识别当前画面」，检查调试框。绿色表示候选可点，黄色表示需要核实；颜色只是程序判断，不能保证游戏实际接受点击。图案不确定时标记 `unknown`，卡槽不确定时禁止自动操作。
5. 可在列表中选择一张牌执行人工单步，也可点击「自动选择并点击一步」。连续操作只能由「开始基础自动操作」启动。暂停、停止及 `Ctrl+Alt+S` 紧急停止会中断等待；如果恰好停在点击途中，等动画结束后重新获取截图并识别。

离线图片可用「打开本地截图」或命令行识别；离线模式不能点击桌面：

```powershell
.venv\Scripts\python -m sheep_solver.vision.cli "<截图路径>" --board 170,180,530,590 --tray 60,1080,760,140 --scale 0.68 --output "<调试图路径>"
```

`--board`、`--tray` 的四个数是相对于截图的 `left,top,width,height`，示例值只演示格式，须按自己的画面重新标定。

## 模块与数据边界

| 模块 | 实际职责 |
|---|---|
| `capture/screen.py`、`capture/selector.py` | 物理像素截图、区域框选、局部坐标到桌面坐标转换；`CapturedFrame.pixels` 按需缓存一次 NumPy RGB 数组。 |
| `vision/detector.py`、`vision/frame_diff.py`、`vision/debug.py` | 模板识别、遮挡保守判断、卡槽识别、轻量帧差与调试叠图。 |
| `core/models.py`、`core/memory.py` | `Observation`、稳定可见牌 ID、`full`/`partial`、待确认点击和模拟卡槽。模拟器专用 `FullState` 不进入桌面求解链。 |
| `planning/basic.py` | 只读取 `Observation` 的轻量决策，返回可解释的 `Action` 或停止原因。 |
| `control/mouse.py`、`control/loop.py` | 坐标与窗口安全检查、Windows 点击、逐步反馈确认、局部更新、异常同步、JSON Lines 决策日志。 |
| `gui/app.py` | 校准、预览、人工与自动单步、单工作线程连续模式、暂停/停止/紧急停止。 |
| `simulator/` | 人工关卡离线规则实验，不参与实时桌面点击。 |

实时链路只从 Windows 截图构造 `Observation`。`FullState` 可包含隐藏牌，但只供本地模拟器；视觉、规划和控制模块不调用 APK 内部状态、网络通关接口或 ADB。

## 识别与状态更新的具体算法

`TemplateDetector` 读取本地 `manifest.json` 和 `block_*.png`，按校准比例缩放模板及 Alpha 掩码，在主牌区或卡槽区运行 `cv2.matchTemplate(..., TM_CCOEFF_NORMED, mask=...)`。局部峰值去重后，把中心相近的候选归为一张牌。两个不同类型分数差不超过 `ambiguity_margin=0.025` 时，该牌类型改为 `unknown`。缩放模板缓存最多 128 项，背景缓存最多 64 项，OpenCV 匹配线程数为 1。

可点击判定还要求图案分数、牌边框与模板背景误差、中心落在主牌区，以及没有与其他可信候选明显重叠。GUI 目前使用图案阈值 `0.83`、点击阈值 `0.88`；边框误差上限默认 `55`，候选重叠门槛为 `0.08`。这些是视觉启发式阈值，不是已证实的游戏内部规则。卡槽首次通过模板读取类型与顺序，并用亮色连通区域数量做交叉检查；不确定时 `tray_confidence=0`，自动决策停止。正常点击后，卡槽类型由代码模拟，每次只做轻量占用数核对。

首次完整识别按位置给可见牌分配 `tile_0001` 等稳定 ID。以后只重识别点击牌周边一个牌宽左右的区域；交并比至少 `0.35` 的目标沿用旧 ID，类型冲突触发完整同步。新出现的牌分配新 ID；先前完全露出的牌若无故消失，也触发冲突。`full` 和 `partial` 从当前对象集合派生，类型计数不单独维护。完全覆盖的牌不在可见内存中。

`tray_after_click` 将新牌放到最后一张同类型牌后面；若已有两张连续同类型牌，则三张一起从卡槽移除。卡槽容量按 7 格处理。这是已观察到的普通牌规则的本地模拟，特殊道具和盲盒尚未完整复刻。待确认点击仅保存预测卡槽；只有屏幕反馈通过后才提交。

## 一步点击事务

`VisualController.run_once()` 持有独占锁，同一时刻最多一笔点击：

1. 从当前可见内存构造局面。若没有内存，完整截图并识别；GUI 刚完成的人工识别可直接复用。
2. `BasicPlanner` 优先选择直接三消，再选择与卡槽配对、当前可见同类较多的牌。卡槽剩两格且无直接三消时，只允许尝试一张已在卡槽出现、并遮挡一个已成对的已识别部分可见牌的候选；卡槽到六格后仍没有直接三消则停止。所有动作都记录选择理由。
3. 点击前重新截图。主牌区或卡槽相对上次帧变化超过 `0.03`，或目标类型、中心、边框复核失败，就停止并要求重新同步。
4. 创建 `PendingClick`，但不改卡槽。`SafeMouse` 检查目标位于主牌区和用户选定游戏区域内，核对局部坐标与桌面绝对坐标，并保证最短 `0.25 s` 点击间隔。Windows 后端检查目标坐标下的窗口不是控制程序，聚焦该窗口，然后用 `SendInput` 发送一次左键按下和释放。
5. 以默认 `40 ms` 间隔观察，最多 `1.6 s`。检测原牌位置与卡槽是否发生变化；沿主牌区到卡槽范围检查局部运动。只有卡槽占用数等于预测值、运动趋静、连续至少 3 次安静观察、安静时间至少 `120 ms` 且首次变化后至少 `150 ms`，才进入确认。短暂停在半路的飞行牌不会立即被当作新层叠牌识别。
6. 再确认目标已经消失，局部识别周边，关联稳定 ID，提交卡槽。若局部状态冲突，执行一次完整识别并比对预测卡槽；无法确认、点击未生效、动画超时或中途停止时不提交预测状态，也不继续点击。

每步在 `data/logs/actions.jsonl` 记录候选、动作、理由、前后卡槽和截图、完整识别、局部识别、反馈、完整周期等耗时。自动模式不逐步保存截图或调试图；手动「获取截图」和「识别」才保存用于核查的图片。

## 测试、实测与限制

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.venv\Scripts\python -m pytest -q
```

本机带授权模板时为 43 项通过；39 项不依赖原版模板，另 4 项附加测试在全新克隆环境中会跳过。其他测试使用合成图像和模拟鼠标。2026-10-27 的 Windows/MuMu 桌面调试中，更新输入链路后 1 次单步和随后 11 次连续点击得到屏幕确认；停止时待确认状态被丢弃，重新截图识别到真实卡槽。这个短时实验说明当前窗口中能连续操作，不代表独立测试通关率。输入 API 与窗口聚焦同时改动，无法单独断言哪一项修复了此前的点击无响应。

已知限制：当前可点击判断仍是图案、边框与候选重叠启发式，部分遮挡、新皮肤和窗口尺寸变化可能误判；必须看调试图并重新校准。首次完整识别与局部冲突后的完整同步仍慢，现场一次完整同步约 `8.1 s`。不支持自动点击盲盒、道具、广告、购买或复活按钮，也不保证通关。性能基线、测量条件和历史结果见 [`incremental_v02.md`](incremental_v02.md)；APK 规则的证据等级见 [`reverse_findings.md`](reverse_findings.md)。
