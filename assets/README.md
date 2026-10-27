# 本地经典牌素材

`tiles/classic/` 由 `scripts/extract_apk_tiles.py` 从用户提供的 APK 离线提取，包含 `block_1.png`–`block_16.png`、方块背景、遮挡蒙层及 `manifest.json`。编号来自 APK 的 Cocos 资源路径，未把素材复制到公开分发包。

重新提取：

```powershell
.venv\Scripts\python scripts\extract_apk_tiles.py third_party\SheepMatchPlanet\SheepMatchPlanet_1.27.5.apk
```

只在有权使用的本地研究环境中保留这些原始游戏素材。正式运行时视觉模块只读取本地模板和 Windows 屏幕图像，不读取 APK 的游戏状态。
