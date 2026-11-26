# 汽水音乐 Android 自动化脚本

> 分支约定：`main` 维护 iOS 版，`dev` 维护 Android 版。
> Android 代码已从旧目录 `qishuiyinyue` 迁入 `qishuiAScriptProject`，后续统一在本工程的 `dev` 分支开发。

基于 AScript 框架的 Android 端自动化脚本，用于自动观看汽水音乐广告以获取免费听歌时长。

> 本工程由 iOS 版（WebDriverAgent + `ascript.ios.*`）移植而来，命名空间、启动方式、屏幕尺寸获取、OCR 引擎均已改为 Android 实现。

## 功能特性

- 自动启动汽水音乐 App（按包名启动 + 等待进入前台）
- 自动跳过开屏广告（paddleocr 精准识别，控件树兜底）
- 支持两种广告类型：视频广告 + 直播间广告
- 广告结束后自动领取奖励（两步：点「领取成功」→ 点「领取奖励」）
- 无限循环领取模式，直到当日额度用完
- OCR 为主，无障碍控件树（Selector）为兜底

## 技术栈

| 模块                            | 用途                                  |
| ------------------------------- | ------------------------------------- |
| `ascript.android.system`        | 应用启动 / 屏幕尺寸 / 资源路径 / 前台判断 |
| `ascript.android.action`        | 点击、系统按键（home / back）          |
| `ascript.android.screen.Ocr`    | 文字识别（paddle PP-OCR V5 / mlkit 双引擎） |
| `ascript.android.screen.FindImages` | 图像模板匹配（直播间关闭按钮等）    |
| `ascript.android.node.Selector` | 无障碍控件树检索（OCR 失败时兜底）     |

## 运行环境

- **平台**：Android（AScript App 已安装并连接）
- **运行模式**：推荐 **无障碍模式**（`R.mode=2`），点击、控件检索都可用
- **点击通道**：若设备是 **图色模式**（`R.mode=3`），无控件树且 `action.click` 不生效，需要配置 ESP32 BLE HID（插件 id=103）等外接点击通道
- **OCR 引擎**：`paddle`（PP-OCR V5，默认，识别率高）或 `mlkit`（谷歌 ML Kit，速度更快）
- **App 包名**：`com.luna.music`（汽水音乐）

## 项目结构

```
qishuiAScriptProject/
├── __init__.py          # 脚本主入口（AScript 工程约定，必须叫这个名字）
├── res/
│   └── img/
│       ├── logo.png
│       ├── qishuiyinyue.png           # 汽水音乐桌面图标（兜底启动用）
│       ├── img_zhibojian-close01.png  # 直播间关闭按钮模板图
│       └── img_1785263403257.png
├── .vscode/
│   └── settings.json       # 项目配置（platform=Android / 设备地址 / Python 路径）
├── .trae/
│   └── rules/
│       └── ascript.md      # AScript AI 编码规则
└── .gitignore
```

## 快速开始

### 1. 环境检查

确保 Android 设备已安装 AScript App，且与开发机在同一局域网（或通过 ADB 连接）。

```bash
# 查看设备状态（AScript MCP 工具）
get_device_status()
```

> `get_device_status()` 返回的 `run_mode.code` 很关键：`accessibility` / `root` / `hid` 有控件树，`screen_only`（图色）没有，脚本启动时也会打印当前模式。

### 2. 设备连接

```bash
# 自动连接（推荐）
auto_connect()

# 或手动连接（扫描后用 IP）
scan_devices()
connect_device(host="192.168.1.2", port=9096)
```

### 3. 运行脚本

```bash
# 方式一：直接运行工程
run_project(project="qishuiAScriptProject")

# 方式二：先上传再运行
upload_file(...)
run_project(...)
```

## 脚本流程

```
启动 App（system.open 包名 + wait_for_package）
    ↓
[用户手动进入第一个广告] 或 [脚本自动找入口]
    ↓
┌───────────────────────────────────────┐
│         核心广告领取循环              │
│                                       │
│  等待广告倒计时结束（通常最长 30s）    │
│    ├─ 视频广告：等「领取成功」出现     │
│    ├─ 直播入口：等倒计时自动进入 → 退出 │
│    │   退出后重新等倒计时，最长 30s   │
│    └─ 互动广告：点「继续互动」后等领取 │
│          ↓                            │
│  步骤 1：点右上角「领取成功」          │
│          ↓                            │
│  步骤 2：点弹窗「领取奖励」→ 进下一轮 │
│          ↓                            │
│  成功次数 +1，重复循环                │
│                                       │
│  连续 10 轮无广告 → 自动退出          │
└───────────────────────────────────────┘
```

## 关键配置

修改 `__init__.py` 头部常量即可：

| 常量                | 默认值             | 说明                                        |
| ------------------- | ------------------ | ------------------------------------------- |
| `APP_NAME`          | `"汽水音乐"`       | App 名称（兜底用）                          |
| `APP_PACKAGE`       | `"com.luna.music"` | 包名（优先使用，启动更快更稳）              |
| `APP_LAUNCH_TIMEOUT`| `10000`            | 等待应用进入前台超时（毫秒）                |
| `MAX_LOOP_COUNT`    | `50`               | 最大循环次数（防死循环）                    |
| `LOOP_INTERVAL`     | `3`                | 主循环间隔秒数                              |
| `OCR_ENGINE`        | `"paddle"`         | OCR 引擎：`"paddle"` / `"mlkit"`            |
| `MAX_IDLE_ROUNDS`   | `10`               | 连续多少轮没领到奖励后自动退出              |
| `LIVE_AD_TIMEOUT`   | `90`               | 识别到直播入口/直播间后延长等待，仅延长一次  |

## iOS → Android 移植对照

| 能力        | iOS（原版）                             | Android（本版）                                   |
| ----------- | --------------------------------------- | ------------------------------------------------- |
| 命名空间    | `ascript.ios.*`                         | `ascript.android.*`                               |
| 启动应用    | `app_start(bundle_id="com.soda.music")` | `system.open("com.luna.music")` + `wait_for_package` |
| 屏幕尺寸    | `screen_size()`                         | `Device.display().widthPixels / heightPixels`     |
| OCR 引擎    | `Ocr.set_engine("vision")` + `Ocr.find(mode=...)` | `Ocr.set_engine("paddle")`，`Ocr.find()` 用默认引擎 |
| 找图        | `FindImages.find(path, rect=, confidence=)` | 同名同参，行为一致                            |
| 控件兜底    | `Selector().label("关闭")`（WDA）        | `Selector(mode=2).text("关闭")`（无障碍控件树）   |
| 通知        | `notify(msg)`                           | `Toast.show(msg)`（失败静默）                     |
| 悬浮/息屏   | 无                                      | `Device.keep_screen_on/off()`                     |

## OCR 区域设计原则

所有搜索区域都**基于屏幕百分比动态计算**，适配不同分辨率（从 720P 到 2K 屏均可）。

| 区域             | 用途                           | 坐标范围近似         |
| ---------------- | ------------------------------ | -------------------- |
| 开屏跳过按钮     | 跳过广告                       | x:80%~97%, y:6%~10%  |
| 中间「继续观看」 | 广告中途恢复（防误点底部下载） | x:7%~93%, y:25%~68%  |
| 弹窗「领取奖励」 | 奖励按钮（仅限中间弹窗区）     | x:13%~87%, y:25%~70% |
| 「继续互动」按钮 | 直播间互动关卡（点通才续播）   | x:2%~98%, y:40%~96%  |

## 注意事项

1. **点击通道**：Android 无障碍 / Root / HID 控件模式下 `action.click` 正常；**图色模式（`screen_only`）下点击无效**，需配 ESP32 BLE HID（插件 id=103）或其他外接 HID 方案。脚本启动时会打印当前模式并给出提示。
2. **直播间广告**：出现「进入直播间」引导时，等待倒计时结束、自动进入直播间。确认已进入后，点右上角 X 退出，再等待广告倒计时结束，出现「领取成功」后领取。入口前的其他弹窗保持等待，不点击「取消」。
   - **等待时间**：识别到直播入口或直播间时，等待时间延长一次（`LIVE_AD_TIMEOUT`，默认 90 秒）；退出直播间后重新给予 30 秒等待领奖倒计时。
   - **入口被遮挡**：引导识别不限于广告开头；若一直未看到引导、已直接进入直播间，则沿用观察 `INTERACT_GRACE`（默认 5 秒）后的关闭流程。关闭优先用控件树/OCR 定位，找不到再点右上角近似坐标 `0.94W / 0.075H`。
   - **单独的互动广告**：未经过上述入口引导、出现「完成互动」提示时，仍点击「继续互动」（`INTERACT_BTN_PATTERN`）恢复倒计时；通过后继续等「领取成功」。
3. **不要写 `if __name__ == "__main__"`**：AScript 工程入口不等于 `__main__`，主体代码直接写在顶层即可。
4. **资源文件**：`res/img/` 下的图标和模板图是 iOS 分辨率裁的，Android 上请按实际设备 UI 重新裁剪，分辨率不匹配会降低识别率。
5. **当日额度**：汽水音乐每日有广告领取上限，连续 10 轮没领到会自动退出，不要反复空跑。
6. **包名变化**：若你的安装包不是 `com.luna.music`，改 `APP_PACKAGE` 即可。

## 相关文档

- [AScript 编码规则](.trae/rules/ascript.md)
- 主脚本：`__init__.py`
