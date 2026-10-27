# APK 与 REA 逆向发现记录

研究对象：`third_party/SheepMatchPlanet/SheepMatchPlanet_1.27.5.apk`，SHA-256 `43724eb0e41000a9c5ece390c7c598b43b6a202e29775e05c0fa85b2f5ee60da`。`1.27.5` 来自文件名，尚未解码 AndroidManifest 确认包版本。源码片段位于 `data/research/modules/`，由 APK 的 `assets/assets/main/index.js` 按模块切分，片段行号只为阅读便利；原始 APK 是最终依据。本文件所述 APK、切片、截图和日志仅保存在本地，公开仓库不包含这些原版材料。

## F01：实际文件结构

- **结论**：APK 是 ZIP 容器，含 `AndroidManifest.xml`、7 个 `classes*.dex`、Cocos2d-JS native 库、`assets/assets/main/index.js`、资源配置和大量 UUID 命名资源。
- **依据**：APK 归档清单；`main/index.js` 的 Cocos 模块注册；`resources/config.json` 的 `paths/types/uuids`。
- **状态**：已验证的静态文件事实；未运行 APK。
- **用途**：直接解析 JS 游戏逻辑与资源映射，不依赖安卓实时接口。

## F02：REA 使用与限制

- **结论**：提供的 `rea-rea-agents-6.8.0` 是 6.8.0 源码快照，需 Node 22.23.4+/24.22.1+ 等。APK 类反编译需 JDK 17+ 与 JADX headless MCP。当前系统自带 Node 24.16.0 不满足要求；Codex 打包的 Node 24.22.1 可运行 REA 6.8.0。未发现 JDK/JADX。
- **依据**：REA `README.md`、`package.json`、`docs/android-analysis.md`；本地运行版本检查。
- **操作**：从官方 npm 安装 `rea-agents@6.8.0` 到 `third_party/rea-runtime`，使用 `--ignore-scripts`；对从 APK 提取的 `main/index.js` 调用 `analyze-javascript-application`。
- **结果**：整份 `main/index.js` 在 1024 MiB JS worker 堆限制处停止，`parsed_javascript_files=0`，证据保存在 `data/research/rea-main-evidence.json`。随后将 `shared-index`、`match-state-machine`、`block-slot-data-manager` 三个相关模块切成约 70 KB 的独立 JS，REA 静态解析成功，证据保存在 `data/research/rea-slices-evidence.json`。REA 报告语法完整，但语义覆盖仅为部分；不能由此证明实际运行行为。
- **状态**：REA 对相关模块的静态解析已完成；整包 JS 未完成解析，APK 未运行。
- **用途**：针对经典每日挑战保留可复现的静态证据及工具边界。

## F03：三消、七格与终局

- **结论**：共享状态模块中三消常量为 3、卡槽容量为 7。点击牌时会查找卡槽已有同类牌，使同类牌相邻；若已有相邻两张同类牌，则三张消除。全部牌计入已消除时报告通关；卡槽达到 7 时进入失败或复活分支。
- **依据**：`assets/assets/main/index.js` 的 `shared-index` 模块；研究切片 `data/research/modules/shared-index.js` 约第 420 行常量 `O=3, R=7`、第 473–535 行 `crushBlock`、第 567–577 行终局判定。`match-state-machine.js` 第 655–664 行还显示卡槽稳定后播放三消动画。
- **状态**：APK 静态代码已确认；服务端与实际运行行为未验证。
- **用途**：基础策略优先即时三消；无须也不得读取内部运行状态。

## F04：遮挡与可点击

- **结论**：主牌区上层牌与下层牌的行列范围重叠时，下层牌视为被遮挡并禁用交互。辅助移出区按列仅最后一张可交互。
- **依据**：`match-state-machine.js` 第 348–365 行 `refreshMoveOutInteractive`/`refreshAndEnableInteractive`、第 744–755 行 `isBlockOccluded`；`crush-block.js` 中 `refreshOccludeState` 控制黑色遮罩。
- **状态**：APK 静态代码已确认；截图中的遮挡分类尚无人工逐牌标注。
- **用途**：视觉模块把“识别出图案”和“可点击”分开。重叠、变暗或边框不完整的候选牌默认不可自动点击。

## F05：经典牌图资源

- **结论**：默认每日挑战牌路径使用 `block/daily/block_<编号>`；编号 1–16 是换肤映射涉及的基础槽位。APK 中存在 `block_1` 到 `block_40`，本轮仅将 1–16 作为经典图案提取和识别目标。另提取 `block_bg`、遮挡黑罩及两种背景变体。
- **依据**：`block-slot-data-manager.js` 第 133–149 行（1–16 的槽位逻辑与 17+ 的直接路径）；`match-state-machine.js` 第 757–801 行资源加载与路径；`assets/assets/resources/config.json` 和对应 UUID `import/*.json`、`native/*.png`。提取映射记录在 `assets/tiles/classic/manifest.json`。
- **状态**：资源路径、编号与 PNG 提取已验证；1–16 对当前用户画面的逐个视觉对应仍需标注确认。素材仅用于本地研究，不公开再分发。
- **用途**：OpenCV 模板匹配素材库，模板类型编号直接来自资源逻辑路径。

## F06：用户截图的离线识别

- **结论**：对用户提供的 888×1428 截图，在人工指定主牌区 `(190,190,510,580)` 和卡槽内区 `(80,1090,730,125)` 后，初版识别器得到 20 个图案候选，其中 17 个被当前启发式规则标记为可点击；卡槽为空。基础策略选择截图坐标约 `(485,242)` 的 10 号图案。已生成 `data/screenshots/daily-2026-10-27-debug-classic.png`。两侧盲盒铃铛被排除在主牌区之外。
- **依据**：该截图、提取的经典模板、`python -m sheep_solver.vision.cli` 的运行结果。
- **状态**：算法输出已复现；**不是识别准确率或点击合法性的人工标注验证**，没有执行实际点击。
- **用途**：第一份真实离线调试样本；后续需要逐牌标注和点击录像做精度评估。

## F07：当前 Windows 游戏窗口的单步验证

- **结论**：2026-10-27 的当前游戏窗口中，主牌宽度约为卡槽牌宽度的 0.81 倍。原算法两处共用模板缩放，漏检卡槽中的 8 号牌，安全地停止自动决策。改用独立卡槽缩放后，保存截图被识别为卡槽 `[]` 与 `[8]`，并执行了两次真实单步点击：卡槽 `[8] → [8,8] → []`。第二次点击记录为“直接完成三消”；两次点击反馈均报告画面变化已确认。
- **依据**：`dist/data/screenshots/capture-20261027-134339.png`、`capture-20261027-134915.png`、`capture-20261027-135451.png`、`capture-20261027-135929.png`；`dist/data/logs/actions.jsonl` 最后两条成功记录，包括目标屏幕坐标 `(643,328)` 与 `(808,657)`、决策理由和点击前后卡槽。
- **状态**：已在当前运行画面验证上述两次点击和一次三消；卡槽缩放比例约 1.23 仅适用于本次校准，未评估其他分辨率或游戏皮肤的准确率。
- **用途**：修正卡槽识别，并保留可复查的真实交互证据。没有读取模拟器内部牌面或隐藏数据。

## 未确认问题

- 盲盒/辅助区细则、特殊牌型与道具。
- 隐藏牌生成时机、关卡重开一致性、是否保证无道具可解。
- 当前 APK 与用户实时运行版本是否一致。
- 模板在其他屏幕缩放、换肤、动画、半遮挡下的准确率。
- 不同牌型、卡槽占用数量及窗口尺寸下的卡槽识别准确率。
