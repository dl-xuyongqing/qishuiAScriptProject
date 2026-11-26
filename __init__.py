# -*- coding: utf-8 -*-
"""
汽水音乐 Android 自动化脚本 — 自动看广告获取免费听歌时长
====================================================
由 iOS 版(WebDriverAgent + ascript.ios.*)移植为 Android 版。

移植要点(与 iOS 版的差异):
  1. 命名空间: ascript.ios.*  ->  ascript.android.*
  2. 启动应用: iOS app_start(bundle_id)  ->  Android system.open(包名) + wait_for_package
  3. 屏幕尺寸: iOS screen_size()         ->  Android Device.display()
  4. OCR 引擎: iOS 的 vision             ->  Android 的 paddle(PP-OCR V5) / mlkit
  5. 新增无障碍控件树(Selector)兜底: Android 有控件树,比纯图色更稳,作为 OCR 失败后的第二层
  6. 点击通道: 图色模式(screen_only)下 action.click 无效,必须走 HID 插件(见 safe_click)

功能:
  1. 启动汽水音乐 App
  2. 跳过开屏广告
  3. 清理"续费会员"弹窗
  4. 等待广告倒计时结束(视频广告 / 直播间广告)
  5. 领取奖励(点"领取成功" -> 点"领取奖励" -> 自动进下一轮)

技术栈:
  - 图色/OCR: ascript.android.screen.Ocr / FindImages
  - 动作    : ascript.android.action
  - 系统    : ascript.android.system(R / Device / open / wait_for_package)
  - 控件    : ascript.android.node.Selector
"""

import os
import time
import traceback

# ============== 基础模块导入(Android) ==============
from ascript.android import action            # type: ignore
from ascript.android import system             # type: ignore
from ascript.android.system import R, Device   # type: ignore
from ascript.android.screen import Ocr, FindImages  # type: ignore
from ascript.android.node import Selector      # type: ignore


# ============== 全局配置 ==============

# 汽水音乐 App 名称 / 包名(二选一,包名优先且更快更稳)
APP_NAME = "汽水音乐"
APP_PACKAGE = "com.luna.music"
APP_LAUNCH_TIMEOUT = 10000      # 等待应用进入前台的超时(毫秒)

# 主循环控制
MAX_LOOP_COUNT = 50             # 最大循环次数(防止无限跑)
LOOP_INTERVAL = 3               # 每次主循环间隔(秒)
MAX_IDLE_ROUNDS = 10            # 连续多少轮没领到奖励后自动退出(判断当日额度用完)

# OCR 引擎(Android: "paddle"=PP-OCR V5 精度高 / "mlkit"=谷歌 ML Kit 速度快)
OCR_ENGINE = "paddle"

# 区域配置(基于屏幕百分比,适配不同分辨率)
REGION_TOP_RIGHT = None         # 运行时根据屏幕尺寸动态计算
REGION_FULL = None
# 直播间关闭按钮模板图是否存在(启动时检查一次)
HAS_LIVE_ROOM_TEMPLATE = False

# 运行模式: 1=Root 2=无障碍 3=图色 4=HID 控件(R.mode)
RUN_MODE = None
# 无障碍控件检索 mode: 无障碍=2 / Root=9 / HID=6 / 图色=None(无控件树)
SELECTOR_MODE = 2

_SCREEN = None


# ============== 工具函数 ==============

def log(msg: str):
    """统一日志打印"""
    ts = time.strftime("%H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def toast(msg: str):
    """Android 端轻提示(失败不影响主流程)"""
    try:
        from ascript.android.ui import Toast  # type: ignore
        Toast.show(msg)
    except Exception:
        pass


def screen_size():
    """获取屏幕分辨率 -> (w, h)。Android 用 Device.display()"""
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    try:
        d = Device.display()
        w, h = int(d.widthPixels), int(d.heightPixels)
        if w > 0 and h > 0:
            _SCREEN = (w, h)
            return _SCREEN
    except Exception as e:
        log(f"获取屏幕尺寸异常: {e}")
    _SCREEN = (1080, 2400)
    return _SCREEN


def detect_run_mode():
    """
    读取 R.mode 决定控件检索 mode 与点击能力:
      1=Root        -> Selector mode=9
      2=无障碍      -> Selector mode=2 (SIMPLE|FILTERED,实战默认)
      3=图色模式    -> 无控件树,Selector 不可用,click 无效(需 HID 插件)
      4=HID 控件    -> Selector mode=6(有控件树)
    """
    global RUN_MODE, SELECTOR_MODE
    try:
        RUN_MODE = R.mode
    except Exception:
        RUN_MODE = None
    SELECTOR_MODE = {1: 9, 2: 2, 4: 6}.get(RUN_MODE)
    mode_name = {1: "Root", 2: "无障碍", 3: "图色", 4: "HID 控件"}.get(RUN_MODE, f"未知({RUN_MODE})")
    log(f"运行模式: {mode_name}  (Selector mode={SELECTOR_MODE})")
    if RUN_MODE == 3:
        log("⚠️  当前是图色模式:无控件树,action.click 也不会生效。")
        log("   请改用 AScript HID 插件(id=103,ESP32 BLE HID)做点击,或切到无障碍模式。")


def safe_click(x: int, y: int, desc: str = ""):
    """
    安全点击:带日志 + 异常捕获
    说明:如果后续切换 HID 通道,只需修改此函数内部实现
    """
    try:
        x, y = int(x), int(y)
        log(f"点击 [{desc}] -> ({x}, {y})")
        action.click(x, y)
        time.sleep(0.8)  # 点击后等待界面响应
        return True
    except Exception as e:
        log(f"点击失败 [{desc}]: {e}")
        return False


def as_hit(item):
    """
    统一识别结果格式。
    Android 端 Ocr.paddleocr / mlkitocr_v2 返回的是 OcrText 对象数组(属性访问),
    Ocr.find / FindImages.find 返回 dict。这里统一归一成 dict:
      {text, center_x, center_y, confidence, rect}
    避免出现 'OcrText' object has no attribute 'get' 之类的问题。
    """
    if item is None:
        return None
    if isinstance(item, dict):
        item.setdefault("confidence", 1.0)
        return item
    d = {
        "text": str(getattr(item, "text", "") or ""),
        "center_x": getattr(item, "center_x", 0) or 0,
        "center_y": getattr(item, "center_y", 0) or 0,
        "rect": getattr(item, "rect", None),
    }
    try:
        d["confidence"] = float(getattr(item, "confidence", 1.0) or 1.0)
    except Exception:
        d["confidence"] = 1.0
    return d


def node_center(node):
    """取控件中心坐标,兼容 rect 对象 / rect 列表两种返回"""
    try:
        r = node.rect
        x1, y1, x2, y2 = int(r.x1), int(r.y1), int(r.x2), int(r.y2)
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        pass
    try:
        r = node.rect
        x1, y1, x2, y2 = int(r[0]), int(r[1]), int(r[2]), int(r[3])
        return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        return 0, 0


def selector_find(keywords, rect=None, label=""):
    """
    【Android 独有】用无障碍控件树兜底查找文字。
    Android 有控件树时,按文字精确匹配比 OCR 更准、更快,作为 OCR 之后的第二层。
    图色模式(RUN_MODE=3)无控件树,直接返回 None。
    keywords: 文字列表(精确匹配);rect: 限定区域,中心点不在区域内则忽略
    返回 {text, center_x, center_y, confidence, source} 或 None
    """
    if SELECTOR_MODE is None:
        return None
    for kw in keywords:
        node = None
        try:
            node = Selector(mode=SELECTOR_MODE).text(kw).find()
        except Exception as e:
            log(f"  控件查找(text) '{kw}' 异常: {e}")
        if not node:
            # 图标按钮常把语义放在 content-description 里,text 找不到时再查 desc
            try:
                node = Selector(mode=SELECTOR_MODE).desc(kw).find()
            except Exception:
                node = None
        if not node:
            continue
        cx, cy = node_center(node)
        if cx <= 0 or cy <= 0:
            continue
        if rect and not (rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]):
            continue
        log(f"  [{label or 'selector'}] 控件命中 '{kw}' @ ({cx},{cy})")
        return {
            "text": kw,
            "center_x": cx,
            "center_y": cy,
            "confidence": 1.0,
            "source": "selector",
        }
    return None


def find_text(pattern, rect, label="", selector_keywords=None):
    """
    统一的"找文字"入口:先 OCR(paddleocr 小区域精准识别),失败再用控件树兜底。
    pattern 是正则;selector_keywords 是需要精确匹配的文字列表(None=不启用控件兜底)。
    """
    item = paddle_find_first(pattern, rect, label)
    if item:
        return item
    if selector_keywords:
        return selector_find(selector_keywords, rect, label)
    return None


def click_center():
    """点击屏幕中心(用于关闭弹窗的兜底策略)"""
    w, h = screen_size()
    if w > 0 and h > 0:
        safe_click(w // 2, h // 2, "屏幕中心")
    else:
        safe_click(500, 1000, "默认中心")


def init_regions():
    """根据屏幕尺寸初始化搜索区域,并检查资源文件"""
    global REGION_TOP_RIGHT, REGION_FULL, HAS_LIVE_ROOM_TEMPLATE
    w, h = screen_size()
    log(f"屏幕尺寸: {w} x {h}")
    # 右上角区域:x 从 60%~100%,y 从 0%~20%
    REGION_TOP_RIGHT = [int(w * 0.6), 0, w, int(h * 0.2)]
    REGION_FULL = [0, 0, w, h]
    # 检查直播间关闭按钮模板图是否存在
    template_path = R.img("img_zhibojian-close01.png")
    HAS_LIVE_ROOM_TEMPLATE = os.path.exists(template_path)
    log(f"直播间关闭模板图: {'存在 ' + template_path if HAS_LIVE_ROOM_TEMPLATE else '不存在'}"
        f"(本版改用右上角 X 关闭,模板仅作参考)")


# ============== 阶段一:启动 App ==============

def launch_app() -> bool:
    """启动汽水音乐 App(用包名启动,并等待其进入前台)"""
    log("=" * 50)
    log("阶段一:启动汽水音乐 App")
    try:
        system.open(APP_PACKAGE)
    except Exception as e:
        log(f"启动 App 异常: {e}")
        return try_click_app_icon()

    try:
        if system.wait_for_package(APP_PACKAGE, APP_LAUNCH_TIMEOUT):
            log("汽水音乐已在前台,开始监控开屏跳过...")
            return True
        log("启动超时(未进入前台),再等 2s 确认一次...")
        time.sleep(2)
        if system.wait_for_package(APP_PACKAGE, 5000):
            log("汽水音乐已在前台(二次确认)")
            return True
    except Exception as e:
        log(f"等待应用前台异常: {e}")
    log("App 未能进入前台,尝试回桌面找图点击图标...")
    return try_click_app_icon()


def try_click_app_icon() -> bool:
    """兜底:回桌面,通过找图点击汽水音乐图标"""
    icon_path = R.img("qishuiyinyue.png")
    log(f"尝试回桌面并找图点击图标: {icon_path}")
    try:
        action.Key.home()
        time.sleep(1.5)
    except Exception as e:
        log(f"  回到桌面失败(可忽略): {e}")
    try:
        for attempt in range(3):
            pos = FindImages.find(icon_path, confidence=0.8)
            if pos:
                return safe_click(pos["center_x"], pos["center_y"], "汽水音乐图标")
            log(f"  第 {attempt + 1} 次未找到图标,重试...")
            time.sleep(1)
        log("未找到汽水音乐图标,请检查图片或屏幕内容。")
        return False
    except Exception as e:
        log(f"找图点击图标异常: {e}")
        return False


# ============== 阶段二:全局弹窗清理 ==============

# 开屏「跳过」按钮常见文案(带倒计时的 3 秒跳过 / 跳过广告)
SPLASH_SKIP_KEYWORDS = ["跳过广告", "跳过"]


def skip_splash_ad(timeout=5) -> bool:
    """
    启动 App 后检测并跳过开屏广告。
    用 paddleocr 在右上角小区域精准识别「跳过」,失败再用控件树兜底。
    rect 按屏幕比例动态计算:x:80%~97%, y:6%~10%。
    """
    log("阶段一B:检测并跳过开屏广告(paddleocr 精准识别中...)")
    w, h = screen_size()
    rect_skip = [int(w * 0.80), int(h * 0.06), int(w * 0.97), int(h * 0.10)]

    deadline = time.time() + timeout
    scan_count = 0

    while time.time() < deadline:
        scan_count += 1
        item = find_text(r"跳过", rect_skip, f"开屏跳过#{scan_count}", SPLASH_SKIP_KEYWORDS)
        if item:
            cx, cy = item["center_x"], item["center_y"]
            log(f"  [扫描#{scan_count} 命中] '{item.get('text', '')}' ({cx},{cy}) → 点击跳过")
            safe_click(cx, cy, "开屏跳过")
            time.sleep(0.2)
            return True
        time.sleep(0.1)
    log(f"  扫描结束(共 {scan_count} 次),未检测到开屏跳过按钮")
    return False


def ocr_find_text(keywords, rect=None, timeout=0):
    """
    在屏幕上查找匹配关键词列表的文字(引擎用全局 set_engine 设定)
    返回第一个匹配的 OCR 结果: {text, confidence, center_x, center_y} 或 None
    """
    start = time.time()
    while True:
        try:
            for kw in keywords:
                result = as_hit(Ocr.find(kw, rect=rect))
                if result:
                    log(f"  OCR 命中关键词: '{kw}' -> 实际文本: '{result.get('text', '')}' "
                        f"位置: ({result.get('center_x', 0)}, {result.get('center_y', 0)})")
                    return result
            hit = selector_find(keywords, rect, "OCR 未命中")
            if hit:
                return hit
        except Exception as e:
            log(f"  OCR 识别异常: {e}")
        if timeout <= 0 or (time.time() - start) >= timeout:
            return None
        time.sleep(0.8)


def ocr_find_mid_first(keywords, selector_keywords=None):
    """
    【弹窗优先查找】先在屏幕中间区域(高度 20%~85%,弹窗集中区)搜索关键词,
    找不到再全屏搜。返回第一个命中的结果或 None。

    为什么要这样做:
      - 弹窗按钮(领取奖励 / 继续观看 / 坚持退出)都在屏幕中间
      - 顶部状态栏有「领取成功×」等小字,和弹窗按钮同字会导致先命中错按钮
    """
    w, h = screen_size()
    mid_rect = [0, int(h * 0.2), w, int(h * 0.85)]
    try:
        for kw in keywords:
            r = as_hit(Ocr.find(kw, rect=mid_rect))
            if r:
                log(f"  [中间区命中] '{kw}' -> '{r.get('text', '')}' 位置({r.get('center_x', 0)},{r.get('center_y', 0)})")
                return r
        for kw in keywords:
            r = as_hit(Ocr.find(kw, rect=REGION_FULL))
            if r:
                log(f"  [全屏命中] '{kw}' -> '{r.get('text', '')}' 位置({r.get('center_x', 0)},{r.get('center_y', 0)})")
                return r
        if selector_keywords:
            for kw in selector_keywords:
                r = selector_find([kw], mid_rect, "弹窗控件兜底")
                if r:
                    return r
    except Exception as e:
        log(f"  OCR 中间优先查找异常: {e}")
    return None


def paddle_find_first(pattern, rect, log_label=""):
    """
    用 Ocr.paddleocr 在指定小区域精准识别关键词,返回第一个有效命中结果 dict 或 None。
    pattern 是正则;rect = [x1, y1, x2, y2]。
    """
    try:
        results = Ocr.paddleocr(rect=rect, pattern=pattern)
        if results and isinstance(results, list):
            for raw in results:
                item = as_hit(raw)          # OcrText 对象 / dict 统一转 dict
                cx = item.get("center_x", 0)
                cy = item.get("center_y", 0)
                if cx > 0 and cy > 0:
                    if log_label:
                        log(f"  [{log_label}] '{pattern}' -> '{item.get('text', '')}' "
                            f"置信度={item.get('confidence', 0):.2f} ({cx},{cy})")
                    return item
    except Exception as e:
        log(f"  paddle_find '{pattern}' 异常: {e}")
    return None


# 【弹窗内优先点击的按钮】出现这些按钮时,不要打叉关闭,而是优先点击它们
#   - 领取奖励 / 领取成功 是用户明确要求点的
#   - 注意:"继续观看"不在这里!它有"中间区(恢复广告)"和"底部(下载诱导)"两种,
#           必须由 check_and_click_continue_watching 在中间区单独识别,避免误点底部下载按钮
PRIORITY_CLICK_KEYWORDS = ["领取奖励", "领取成功", "立即领取", "确认领取"]


def close_membership_popup() -> bool:
    """
    检测并关闭"续费会员"弹窗(开屏进入播放界面后唯一需要处理的弹窗)。
    判定特征:底部出现"元开通"文字(如"12元开通"、"连续包月9元开通")。
    处理:点击弹窗外部遮罩区域(屏幕中上方)关闭弹窗。
    返回:是否检测到并执行了关闭。
    """
    w, h = screen_size()
    # 原 iOS 实测 x:4.4%~95.3%, y:87.7%~96.3%,按屏宽高比例动态计算
    bottom_rect = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]
    item = find_text(r"元开通", bottom_rect, "续费会员弹窗", ["元开通"])
    if item:
        log(f"  [续费会员弹窗] 检测到底部 '{item.get('text', '')}',点击弹窗外部关闭")
        try:
            action.click(int(w * 0.5), int(h * 0.30))
        except Exception as e:
            log(f"  点击关闭异常: {e}")
        time.sleep(1.2)
        return True
    return False

# 兼容旧调用名
check_and_back_from_membership_page = close_membership_popup


def check_and_close_thirdparty_download() -> bool:
    """
    检测是否跳转到了第三方应用下载页/应用商店(广告倒计时结束后可能触发)。
    Android 端:优先识别"打开/安装/下载"类文字,识别不到再按返回键退回汽水音乐。
    返回:是否执行了关闭动作(True=已尝试退出该页面)。
    """
    w, h = screen_size()
    rect_done = [int(w * 0.02), int(h * 0.075), int(w * 0.19), int(h * 0.115)]
    item = find_text(r"完成|关闭", rect_done, "第三方下载页", ["完成", "关闭"])
    if item:
        log(f"  [第三方下载页] 检测到左上角 '{item.get('text', '')}',点击关闭")
        safe_click(item["center_x"], item["center_y"], "'完成/关闭'按钮")
        time.sleep(1.0)
        return True
    try:
        app = system.get_foreground_app()
        if app and app != APP_PACKAGE:
            log(f"  当前前台是 {app}(非汽水音乐),按返回键退回")
            action.Key.back()
            time.sleep(1.2)
            return True
    except Exception as e:
        log(f"  第三方下载页检测异常: {e}")
    return False


def dismiss_popups(max_rounds=3) -> int:
    """
    弹窗清理(精简版):只处理"续费会员"弹窗(底部"元开通")。
    返回:已处理的弹窗数量
    """
    log("阶段二:弹窗清理(仅处理续费会员弹窗)")
    handled = 0
    for _ in range(max_rounds):
        if close_membership_popup():
            handled += 1
            time.sleep(1.0)
            continue
        break
    if handled == 0:
        log("  未检测到续费会员弹窗,无需清理。")
    log(f"弹窗清理结束,共处理 {handled} 个")
    return handled


# ============== 阶段三:监测"免"字按钮并进入广告 ==============


def light_background_cleanup() -> bool:
    """
    轻量级背景清理:只检测续费会员弹窗(底部"元开通")→ 点外部关闭。
    其他弹窗不处理。paddleocr 小区域识别 ~0.6s,不阻塞主识别节奏。
    """
    return close_membership_popup()


def wait_for_mian_button(timeout=60, allow_mian=True) -> bool:
    """
    监测进广告入口。按 allow_mian 决定是否搜索右上角"免"字:
      - allow_mian=True  (第1轮/开屏后场景):
          1) 先搜"立即解锁"/"立即领取"弹窗按钮 → 点击
          2) 再搜右上角"免"字 → 点击
          3) 都没有 → 每 8s 清理一次续费会员弹窗
      - allow_mian=False (第2轮及以后):
          只搜 1) 和 3),**不搜"免"字**。

    timeout: 最长等待秒数(0 表示只检查一次不等待)
    返回: 是否成功点击进入广告
    """
    if allow_mian:
        log("阶段三:监测进广告入口(优先'立即解锁/领取'弹窗,其次'免'字)")
    else:
        log("阶段三:监测进广告入口(仅'立即解锁/领取'弹窗,不再找'免'字)")
    w, h = screen_size()
    rect_mian = [int(w * 0.65), int(h * 0.04), int(w * 0.92), int(h * 0.12)]
    rect_unlock = [int(w * 0.029), int(h * 0.52), int(w * 0.981), int(h * 0.982)]
    rect_pay = [int(w * 0.044), int(h * 0.877), int(w * 0.953), int(h * 0.963)]

    deadline = time.time() + timeout if timeout > 0 else 0
    check_count = 0
    last_err_log = 0.0
    last_cleanup = 0.0
    CLEANUP_INTERVAL = 8
    REVERIFY_WAIT = 0.7

    while True:
        check_count += 1

        # ========== 1) 最高优先:"立即解锁"/"立即领取"(弹窗进广告按钮) ==========
        unlock_hit = find_text(r"立即(解锁|领取)", rect_unlock, f"解锁按钮#{check_count}")
        if unlock_hit is not None:
            cx, cy = unlock_hit["center_x"], unlock_hit["center_y"]
            text = unlock_hit.get("text", "")
            log(f"  [扫描#{check_count} 命中] '{text}' ({cx},{cy}) → 点击进入广告")
            safe_click(cx, cy, f"'{text}'按钮")
            time.sleep(1.5)
            return True

        # ========== 2) 只有 allow_mian=True 时才搜"免"字按钮 ==========
        if allow_mian:
            mian_hit = find_text(r"免", rect_mian, f"'免'字#{check_count}", ["免"])
            if mian_hit is not None:
                cx, cy = mian_hit["center_x"], mian_hit["center_y"]
                text = mian_hit.get("text", "")

                # 【关键策略】先看有没有续费会员弹窗,决定重试次数:
                #   有弹窗 → MAX_RETRY = 2(点击+验证)
                #   无弹窗 → MAX_RETRY = 1(点一次就返回)
                has_popup = _detect_membership_popup(rect_pay)
                max_retry = 2 if has_popup else 1
                log(f"  [扫描#{check_count} 命中] '免' -> '{text}' ({cx},{cy})"
                    f"  检测到续费弹窗={'是' if has_popup else '否'} → 重试上限={max_retry}")

                for attempt in range(1, max_retry + 1):
                    log(f"  [点击尝试 {attempt}/{max_retry}] '免'字按钮 ({cx},{cy})")
                    safe_click(cx, cy, f"'免'字按钮(尝试{attempt})")

                    if max_retry == 1:
                        log("  [成功] 无续费会员弹窗,点击 1 次即生效,进入广告")
                        time.sleep(1.0)
                        return True

                    # max_retry>=2:验证免字是否消失
                    time.sleep(REVERIFY_WAIT)
                    verify_hit = None
                    try:
                        vr = Ocr.paddleocr(rect=rect_mian, pattern=r"免")
                        if vr and isinstance(vr, list) and len(vr) > 0:
                            vi = as_hit(vr[0])
                            vx = vi.get("center_x", 0)
                            vy = vi.get("center_y", 0)
                            if vx > 0 and vy > 0:
                                verify_hit = (vx, vy, vi.get("confidence", 0))
                    except Exception:
                        verify_hit = None

                    if verify_hit is None:
                        log(f"  [成功] 点击后'免'字消失(尝试 {attempt} 次生效)")
                        time.sleep(0.8)
                        return True

                    vx, vy, vconf = verify_hit
                    log(f"  [未生效] 点击后'免'字仍在屏上 ({vx},{vy}) 置信度={vconf:.2f},继续重试点击")

                log(f"  [重试耗尽] 连续 {max_retry} 次点击'免'字未触发跳转,本轮放弃")

        # ========== 3) 都没命中 → 每 8s 清理一次续费会员弹窗 ==========
        now = time.time()
        if now - last_cleanup >= CLEANUP_INTERVAL:
            try:
                light_background_cleanup()
            except Exception as e:
                log(f"  背景清理异常: {e}")
            last_cleanup = now

        if timeout > 0 and time.time() >= deadline:
            log(f"  等待进广告入口超时 ({timeout}s),本轮跳过")
            return False
        if timeout == 0:
            log("  单次检查:未发现进广告入口")
            return False
        time.sleep(0.2)


def _detect_membership_popup(rect_pay) -> bool:
    """内部辅助:检测底部是否有'元开通'续费会员弹窗。返回 True=有弹窗。"""
    return bool(find_text(r"元开通", rect_pay, "续费弹窗判定", ["元开通"]))


# ============== 阶段四:等待广告倒计时结束 ==============

COUNTDOWN_KEYWORDS = ["秒后可领取", "秒后领取", "秒后关闭", "秒后获得"]


def check_and_click_continue_watching() -> bool:
    """
    检测并点击"继续观看"按钮——【仅限屏幕中间显眼位置】。

    重要区分(两种"继续观看"绝不能混):
      - 中间显眼区(y:25%~68%):广告中途误点关闭后弹出的恢复对话框 → 该点,继续看广告
      - 屏幕底部:广告商诱导下载应用按钮 → 绝不点,会跳下载页

    所以只在中间区域检测,绝不在底部/全屏检测。
    返回 True 表示已点击中间的"继续观看"。
    """
    w, h = screen_size()
    rect_continue = [int(w * 0.07), int(h * 0.25), int(w * 0.93), int(h * 0.68)]
    item = find_text(r"继续观看", rect_continue, "继续观看(中间区)", ["继续观看"])
    if item:
        ok = safe_click(item["center_x"], item["center_y"], "继续观看(中间区)")
        if ok:
            time.sleep(2.5)
            return True
    return False


# ---------- 直播间广告处理(Android 增强版) ----------

# 直播间判定:出现"直播"字样即视为已进入直播间(用户实测可用)
LIVE_TAG_PATTERN = r"直播"
# 已进入直播间时的关闭方式:点右上角 X(坐标比例可调)
CLOSE_X_X_RATIO = 0.94
CLOSE_X_Y_RATIO = 0.075
# 直播间入口可能被其他弹窗遮挡:等待引导倒计时结束、自动进入直播间后再退出
LIVE_ENTRY_PATTERN = r"进入直播间"
LIVE_AD_TIMEOUT = 90          # 检测到直播入口/直播间后,额外等待的最长秒数(仅延长一次)
# 直播间广告的互动关卡:屏幕上出现"完成互动"提示,倒计时会卡住,
# 必须点"继续互动"按钮,广告倒计时才会继续跑(不点就一直等不到"领取成功")
INTERACT_TASK_PATTERN = r"完成互动"     # 互动任务提示文字(仅用于诊断/兜底提示)
INTERACT_BTN_PATTERN = r"继续互动"      # 真正需要点的按钮
INTERACT_MAX_CLICK = 3         # 本轮最多点几次"继续互动"(防止死循环空点)
INTERACT_GRACE = 5             # 发现直播间后先观察几秒,看会不会弹出"完成互动"(有互动就不点X)
# 直播间自动关闭总开关(排查问题时置 False 即可完全不干扰视频广告)
LIVE_ROOM_AUTO_CLOSE = True
# 诊断日志只打一次(避免每轮刷屏)
LIVE_DEBUG_ONCE = True


def _live_rects():
    """直播间相关区域(按屏幕比例,适配任意分辨率)"""
    w, h = screen_size()
    return {
        # 右上角整体区(兼容旧逻辑/诊断用)
        "top_right": [int(w * 0.55), int(h * 0.03), w, int(h * 0.22)],
        # 右上角关闭 X 的聚焦搜索区
        "close_x": [int(w * 0.80), int(h * 0.02), w, int(h * 0.14)],
        # 直播间底部互动区
        "bottom": [int(w * 0.05), int(h * 0.55), int(w * 0.95), int(h * 0.92)],
        # 顶部条(直播间"直播"标识多在头像/昵称附近)
        "live_tag_top": [0, int(h * 0.02), w, int(h * 0.22)],
        # 直播间入口引导搜索区
        "entry": [int(w * 0.02), int(h * 0.25), int(w * 0.98), int(h * 0.96)],
        # 直播间互动关卡("完成互动"提示 + "继续互动"按钮)搜索区,通常在中下部
        "interact": [int(w * 0.02), int(h * 0.40), int(w * 0.98), int(h * 0.96)],
        # 右上角"领取成功"
        "claim_top": [int(w * 0.55), int(h * 0.03), w, int(h * 0.14)],
    }


def dump_text_area(rect, label=""):
    """诊断用:把指定区域 OCR 到的文字全部打印出来(适配直播间 UI 时看这个)"""
    try:
        items = Ocr.find_all(rect=rect) or []
        if not items:
            log(f"  [诊断] {label} 区域未识别到任何文字")
            return
        parts = []
        for raw in items[:12]:
            it = as_hit(raw)
            parts.append(f"{it.get('text', '')}({it.get('center_x', 0)},{it.get('center_y', 0)})")
        log(f"  [诊断] {label} 区域文字: " + " | ".join(parts))
    except Exception as e:
        log(f"  [诊断] {label} 区域识别异常: {e}")


def live_room_hint():
    """
    判断"已经进入直播间":屏幕上出现"直播"字样即算(实测可用的判定方式)。
    先排除"点击进入直播间"引导页——等待自动进入后,才允许关闭直播间。
    """
    r = _live_rects()
    if live_entry_hint():
        return None
    hit = paddle_find_first(LIVE_TAG_PATTERN, r["live_tag_top"], "直播间标识(顶部)")
    if hit:
        return hit
    return paddle_find_first(LIVE_TAG_PATTERN, r["bottom"], "直播间标识(底部)")


def live_room_closed_now(claim_rect=None):
    """判定直播间是否已关闭:出现"领取成功" 或 直播间特征消失"""
    if claim_rect and paddle_find_first(r"领取成功", claim_rect, "领取成功(关闭后)"):
        return True
    return live_room_hint() is None


def live_entry_hint():
    """
    只识别屏上可见的直播入口引导,不点击弹窗或"取消"。
    返回 OCR 命中结果或 None;由主轮询等待自动进入直播间。
    """
    return paddle_find_first(LIVE_ENTRY_PATTERN, _live_rects()["entry"], "直播入口提示")


def handle_live_interaction() -> str:
    """
    直播间广告的互动关卡:屏幕出现"完成互动"提示后,广告倒计时是暂停的,
    必须点"继续互动"按钮,倒计时才会继续跑完 → 才能等到"领取成功"。

    优先控件树(快且准),没有再 OCR;只点"继续互动"按钮文字,
    不点"完成互动"这种任务描述文字(避免误跳进互动任务页)。

    返回:
      "none"  屏幕上没有互动关卡
      "wait"  有"完成互动"提示但没找到"继续互动"按钮(说明这轮是互动广告,先别点X关闭)
      "ok"    已点击并通过验证,倒计时应该已恢复
      "retry" 点了但按钮还在,需要稍后再点
    """
    r = _live_rects()
    rect = r["interact"]

    btn = selector_find(["继续互动"], rect, "继续互动按钮(控件)")
    if not btn:
        btn = paddle_find_first(INTERACT_BTN_PATTERN, rect, "继续互动(OCR)")
    if not btn:
        # 只看到任务描述、没看到可点的按钮:打日志供调区域,不乱点
        hint = paddle_find_first(INTERACT_TASK_PATTERN, rect, "完成互动提示")
        if hint:
            log(f"  ⚠️ 检测到'{hint.get('text', '')}'提示,但没找到'继续互动'按钮"
                f" @ ({hint['center_x']},{hint['center_y']}),请确认按钮区域/文案")
            return "wait"
        return "none"

    log(f"  检测到直播间互动关卡 '{btn.get('text', '')}' @ ({btn['center_x']},{btn['center_y']}) → 点击'继续互动'")
    safe_click(btn["center_x"], btn["center_y"], "'继续互动'")
    time.sleep(1.0)

    # 验证:按钮消失(或换成"完成互动"已完成态)即视为互动通过
    still = paddle_find_first(INTERACT_BTN_PATTERN, rect, "互动后验证")
    if not still:
        log("  ✅ 互动已完成,广告倒计时继续")
        return "ok"
    log("  ⚠️ 点击后'继续互动'仍在,视为未通过,稍后重试")
    return "retry"


def try_close_live_room() -> bool:
    """
    已进入直播间:点右上角的 X 关闭。
    三层都在右上角小区域,前一层没生效才往下退:
      1) 控件树 text/desc 命中 关闭 / close / ×
      2) OCR 识别 × ✕ ✖ 符号
      3) 右上角近似坐标点击(CLOSE_X_X_RATIO, CLOSE_X_Y_RATIO,可调)
    每层动作后验证:出现"领取成功" 或 "直播"字样消失 即算成功。
    """
    global LIVE_DEBUG_ONCE
    r = _live_rects()
    w, h = screen_size()
    close_rect = r["close_x"]
    claim_rect = r["claim_top"]

    # 动手前再确认一次:不是直播间就直接返回,绝不对普通视频广告做任何点击
    if not live_room_hint():
        log("  [预检] 未命中'直播'字样,放弃关闭动作(保护正常广告)")
        return False

    # 第一次进直播间时,把关键区域识别到的文字打出来,方便精调坐标
    if LIVE_DEBUG_ONCE:
        LIVE_DEBUG_ONCE = False
        dump_text_area(r["live_tag_top"], "直播间顶部(含'直播')")
        dump_text_area(close_rect, "右上角关闭X区")
        dump_text_area(claim_rect, "右上角领取区")

    def verify(tag):
        time.sleep(1.3)
        closed = live_room_closed_now(claim_rect)
        log(f"  [{tag}] 验证: {'✅ 直播间已关闭' if closed else '❌ 仍在直播间'}")
        return closed

    def claim_ready():
        """中途再查一次:"领取成功"已出现就立刻停止关闭动作"""
        if paddle_find_first(r"领取成功", claim_rect, "领取成功(中止关闭)"):
            log("  ⏹ '领取成功'已出现,中止关闭动作,转交领取流程")
            return True
        return False

    # 1/3 控件树 text/desc
    node = selector_find(["关闭", "close", "关闭直播间", "×"], close_rect, "关闭控件")
    if node:
        log(f"  [1/3 控件] 命中 '{node.get('text', '')}' @ ({node['center_x']},{node['center_y']}) → 点击")
        safe_click(node["center_x"], node["center_y"], "关闭X(控件)")
        if verify("1/3 控件"):
            return True

    # 2/3 OCR 识别 × 符号
    hit = paddle_find_first(r"[×✕✖✗✘]", close_rect, "右上角X(OCR)")
    if hit:
        log(f"  [2/3 OCR] 命中 '{hit.get('text', '')}' @ ({hit['center_x']},{hit['center_y']}) → 点击")
        safe_click(hit["center_x"], hit["center_y"], "关闭X(OCR)")
        if verify("2/3 OCR"):
            return True
    else:
        log("  [2/3 OCR] 右上角未识别到 × 符号")

    # 3/3 右上角近似坐标
    if claim_ready():
        return True
    ax, ay = int(w * CLOSE_X_X_RATIO), int(h * CLOSE_X_Y_RATIO)
    log(f"  [3/3 坐标] 点击右上角 ({ax},{ay}) → 关闭直播间")
    safe_click(ax, ay, "关闭X(近似坐标)")
    if verify("3/3 坐标"):
        return True

    return False


def wait_ad_countdown(timeout=30) -> bool:
    """
    阶段四:在广告界面等待倒计时结束。
    广告类型与处理方式:
      1) 视频广告:倒计时结束 → 右上角出现"领取成功" → 返回,交给 claim_reward 处理
      2) 直播间广告入口:等待引导倒计时结束、自动进入直播间 →
         点右上角 X 退出 → 重新等待广告倒计时结束,出现"领取成功"
      3) 直播间广告互动关卡:出现"完成互动"提示时倒计时会暂停,
         点"继续互动"按钮后倒计时才继续(必须点,否则卡住等不到"领取成功")
      4) 兜底(入口被弹窗遮挡、已经进了直播间且没有互动关卡):屏幕出现"直播"字样即判定已在直播间 →
         点右上角 X 关闭 → 关闭后等"领取成功"
    轮询优先级:领取成功 > 直播入口等待 > 继续互动 > 已在直播间则点右上角X > 秒后可领奖励(仅日志)
    """
    log("阶段四:等待广告倒计时结束(支持视频广告 & 直播间广告)")
    w, h = screen_size()
    # 右上角"领取成功"区域(视频广告结束 或 直播间关闭后)
    rect_claim_top = [int(w * 0.55), int(h * 0.03), w, int(h * 0.14)]
    # 右上角倒计时文案区域(仅打印日志)
    rect_countdown = [int(w * 0.58), int(h * 0.045), int(w * 0.97), int(h * 0.09)]

    deadline = time.time() + timeout
    round_start = time.time()
    last_sec = -1
    checks = 0
    is_live_room_logged = False
    live_entry_seen = False      # 已看到入口引导:自动进入后必须先退出直播间
    live_wait_extended = False   # 直播等待只延长一次,防止入口提示反复出现导致无限等待
    live_room_exited = False     # 退出后只等领奖倒计时,不再触发直播间操作
    live_close_attempts = 0       # 本轮最多尝试关闭直播间的次数(防止重复点击)
    interact_clicks = 0           # 本轮已点"继续互动"的次数
    interact_passed = False       # 互动关卡是否已通过(通过后不再点右上角X,免得把广告关掉)
    live_first_seen = 0.0         # 本轮第一次检测到"直播"的时刻(0=还没发现直播间)

    while time.time() < deadline:
        checks += 1

        # ========== 1) 最高优先:检测"领取成功"是否出现 ==========
        # 领取主链路只用 OCR(实测最稳),不掺控件兜底,避免误命中隐藏节点导致点错位置
        hit_claim = find_text(r"领取成功", rect_claim_top, "倒计时结束标志")
        if hit_claim:
            log(f"  检测到'领取成功'出现(轮询 {checks} 次)→ 返回,交给 claim_reward 执行两步点击")
            return True

        # ========== 2) 直播间入口:保持等待,直到引导倒计时结束、自动进入直播间 ==========
        # 全程检查,兼容其他弹窗消失后才显示引导;入口页不做任何点击。
        if LIVE_ROOM_AUTO_CLOSE and not live_room_exited:
            entry_hit = live_entry_hint()
            if entry_hit:
                if not live_entry_seen:
                    log("  检测到直播入口引导,等待倒计时结束后自动进入直播间,再退出")
                live_entry_seen = True
                if not live_wait_extended:
                    deadline = max(deadline, time.time() + LIVE_AD_TIMEOUT)
                    live_wait_extended = True
                time.sleep(0.8)
                continue

        # ========== 3) 直播间广告互动关卡:出现"完成互动"必须点"继续互动" ==========
        # 倒计时在此暂停;刚发现直播间时每轮都查(互动提示可能滞后几秒才弹),之后每 2 轮查一次
        in_grace = live_first_seen > 0 and (time.time() - live_first_seen) < INTERACT_GRACE
        if (LIVE_ROOM_AUTO_CLOSE and not live_entry_seen and not live_room_exited
                and interact_clicks < INTERACT_MAX_CLICK and (in_grace or checks % 2 == 1)):
            it_res = handle_live_interaction()
            if it_res == "ok":
                interact_clicks += 1
                interact_passed = True
                is_live_room_logged = True
                log(f"  已通过互动关卡({interact_clicks}/{INTERACT_MAX_CLICK}),倒计时继续,不再尝试关闭直播间")
                time.sleep(0.3)
                continue
            if it_res == "retry":
                interact_clicks += 1
                log(f"  互动未通过,稍后再点({interact_clicks}/{INTERACT_MAX_CLICK})")
                time.sleep(0.5)
                continue
            if it_res == "wait":
                # 有互动提示但按钮没识别到:说明这就是互动广告,先不点X,等下一轮再找按钮
                interact_passed = True
                is_live_room_logged = True
                time.sleep(0.5)
                continue

        # ========== 4) 已自动进入直播间 → 点右上角 X 退出 ==========
        # 只在明确命中"直播"字样时才动手;且每 3 次轮询才检测一次(降低 OCR 开销与误判概率)。
        # 已经点过"继续互动"的轮次不再关闭,避免把正在跑倒计时的互动广告打断。
        if (LIVE_ROOM_AUTO_CLOSE and not live_room_exited
                and (live_entry_seen or not interact_passed)
                and (live_entry_seen or checks % 3 == 1)):
            live_hit = live_room_hint()
            if live_hit:
                if not live_wait_extended:
                    deadline = max(deadline, time.time() + LIVE_AD_TIMEOUT)
                    live_wait_extended = True
                # 首次发现直播间:不立刻点X,先给 INTERACT_GRACE 秒观察"完成互动"是否弹出
                # 已明确经过入口引导的轮次按新流程直接退出,不触发互动点击。
                if not live_entry_seen and live_first_seen == 0.0:
                    live_first_seen = time.time()
                    is_live_room_logged = True
                    log(f"  检测到直播间广告(轮询 {checks} 次,命中 '{live_hit.get('text', '')}')"
                        f"→ 先观察 {INTERACT_GRACE}s 是否弹出'完成互动'(有互动就点'继续互动',不点X)")
                    time.sleep(0.4)
                    continue

                waited = time.time() - live_first_seen
                if not live_entry_seen and waited < INTERACT_GRACE:
                    log(f"  直播间观察中 {waited:.1f}s/{INTERACT_GRACE}s,暂无互动关卡 → 暂不点X")
                    time.sleep(0.4)
                    continue

                if live_close_attempts < 2:
                    live_close_attempts += 1
                    log(f"  ---- 已进入直播间,退出尝试 {live_close_attempts}/2 ----")
                    if try_close_live_room():
                        live_room_exited = True
                        deadline = time.time() + timeout
                        log(f"  ✅ 已退出直播间,重新等待广告倒计时结束(最多 {timeout}s) → '领取成功'")
                        time.sleep(1.0)
                        continue
                    log("  ⚠️ 直播间关闭失败,继续观察,不打断本轮等待")
                    time.sleep(0.6)
                    continue
                log("  ⚠️ 本轮直播间关闭已试 2 次仍失败,放弃本轮,避免反复误操作")
                time.sleep(0.6)
                continue

        # ========== 5) 最低优先:倒计时文案 → 仅打印日志 ==========
        cd_hit = paddle_find_first(r"秒后可领奖励", rect_countdown, "倒计时")
        if cd_hit:
            text = cd_hit.get("text", "")
            sec = None
            for ch in text:
                if ch.isdigit():
                    sec = int(ch)
                    break
            if sec is not None and sec != last_sec:
                log(f"  广告倒计时中... {text}")
                last_sec = sec

        time.sleep(0.8)

    log(f"  等待超时(累计 {time.time() - round_start:.1f}s),'领取成功'未出现(本轮可能没有广告可领)")
    return False


# ============== 阶段五:领取奖励 ==============

def claim_reward() -> int:
    """
    领取奖励流程(确定性两步走,无多余分支):
      倒计时结束 → 右上角出现"领取成功" → 点击它
        → **肯定会**弹出中间对话框包含"领取奖励"按钮
          → 点击"领取奖励" → **肯定会**进入新一轮广告

    返回值:1=两步都成功,已进入下一轮广告;0=超时失败
    """
    log("阶段五:领取奖励(确定两步:点领取成功 → 点领取奖励 → 进入下一轮广告)")
    w, h = screen_size()
    # 右上角"领取成功"专属区(倒计时结束标志)
    rect_claim_top = [int(w * 0.55), int(h * 0.03), w, int(h * 0.14)]
    # "领取奖励"弹窗按钮专属区(超出绝不点)
    rect_reward_btn = [int(w * 0.13), int(h * 0.25), int(w * 0.87), int(h * 0.70)]

    # ========== 第一步:等并点右上角"领取成功" ==========
    log("  [步骤 1/2] 等待右上角出现'领取成功'...")
    hit_claim = None
    deadline1 = time.time() + 20  # 倒计时刚结束,领取成功按钮应该很快出现,20s 足够
    checks1 = 0
    while time.time() < deadline1:
        checks1 += 1
        hit_claim = find_text(r"领取成功", rect_claim_top, "领取成功(右上角)")
        if hit_claim:
            break
        time.sleep(0.35)
    if not hit_claim:
        log(f"  [失败] 20s 内未等到右上角'领取成功'(轮询 {checks1} 次),本轮放弃")
        return 0
    cx1, cy1 = hit_claim["center_x"], hit_claim["center_y"]
    log(f"  [命中] '领取成功' @ ({cx1},{cy1}) → 点击")
    safe_click(cx1, cy1, "'领取成功'(右上角)")

    # ========== 第二步:点完"领取成功",肯定会弹"领取奖励"对话框,等它出现并点击 ==========
    log("  [步骤 2/2] 等待中间弹窗出现'领取奖励'按钮...")
    time.sleep(0.6)  # 弹窗动画留一点时间(不要上来就找,大概率第一帧还没画出来)
    hit_reward = None
    deadline2 = time.time() + 15  # 点击后弹窗 15s 内肯定出来
    checks2 = 0
    while time.time() < deadline2:
        checks2 += 1
        hit_reward = find_text(r"领取奖励", rect_reward_btn, "领取奖励(弹窗)")
        if hit_reward:
            break
        time.sleep(0.35)
    if not hit_reward:
        log(f"  [失败] 点击'领取成功'后 15s 内未等到'领取奖励'弹窗(轮询 {checks2} 次),本轮放弃")
        return 0
    cx2, cy2 = hit_reward["center_x"], hit_reward["center_y"]
    log(f"  [命中] '领取奖励' @ ({cx2},{cy2}) → 点击(进入下一轮广告)")
    safe_click(cx2, cy2, "'领取奖励'(弹窗)")
    time.sleep(1.5)  # 给 App 跳转新广告一点时间,下一轮 wait_ad_countdown 接着等

    log("  [成功] 两步领取完成,自动进入下一轮广告倒计时")
    return 1


# ============== 主流程 ==============

def run_ad_loop() -> int:
    """
    核心循环:用户手动进入广告后,脚本负责
      等广告倒计时结束 → 点"领取成功" → 点"领取奖励" → 自动进入下一轮广告 → 循环
    每次成功领取计 1 次,累计到 success_count。
    返回累计成功次数(失败时也返回已拿到的次数)。
    """
    log("\n" + "#" * 60)
    log("🎯 进入核心广告领取循环(按停止项目即可退出)")
    log("#" * 60)
    log("   ⚠️  请在 App 上手动进入一个广告(点右上角'免'字或'立即解锁'弹窗),脚本会自动接管后续所有轮次")
    success_count = 0
    round_idx = 0
    idle_count = 0

    while round_idx < MAX_LOOP_COUNT:
        round_idx += 1
        log(f"\n---- 第 {round_idx} 轮广告循环 ----")

        # 步骤 1:等广告倒计时结束(30s 超时)
        got_ad = wait_ad_countdown(timeout=30)
        if not got_ad:
            idle_count += 1
            log(f"  第 {round_idx} 轮:等超时,未检测到广告倒计时结束(连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮无广告)")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮没领到广告,可能当日额度已用完,自动退出循环")
                break
            time.sleep(3)
            continue

        # 步骤 2:两步领取(点领取成功 → 点领取奖励 → 自动进下一轮广告)
        claim_res = claim_reward()
        if claim_res == 1:
            success_count += 1
            idle_count = 0
            log(f"  ✅ 第 {round_idx} 轮领取成功!累计成功次数 = {success_count}")
        else:
            idle_count += 1
            log(f"  ⚠️  第 {round_idx} 轮领取失败(连续 {idle_count}/{MAX_IDLE_ROUNDS} 轮领取失败)")
            if idle_count >= MAX_IDLE_ROUNDS:
                log(f"  ⚠️  连续 {MAX_IDLE_ROUNDS} 轮领取失败,自动退出循环")
                break

        time.sleep(LOOP_INTERVAL)

    return success_count


def main():
    """脚本入口主函数"""
    log("🎵 汽水音乐自动看广告脚本(Android 版)启动")
    log("   核心功能:跳过开屏广告 → 等待用户手动进广告 → 无限循环领奖励")
    try:
        # 1. 初始化:运行模式 / 屏幕尺寸 / OCR 引擎
        detect_run_mode()
        init_regions()
        Ocr.set_engine(OCR_ENGINE)
        log(f"OCR 引擎: {OCR_ENGINE}")
        try:
            Device.keep_screen_on()  # 防息屏打断(失败不影响)
        except Exception:
            pass
        # 2. 启动 App
        if not launch_app():
            log("❌ 无法启动汽水音乐 App,脚本终止")
            toast("汽水音乐启动失败")
            return
        # 3. 跳开屏广告
        skip_splash_ad(timeout=5)
        # 4. 核心循环(用户手动进广告后自动接管)
        total = run_ad_loop()
        # 5. 结束
        log("=" * 60)
        log(f"✅ 脚本运行结束,共成功领取 {total} 次奖励")
        toast(f"汽水音乐脚本结束,成功 {total} 次")
    except Exception as e:
        log(f"❌ 脚本主流程异常: {e}")
        traceback.print_exc()
    finally:
        try:
            Device.keep_screen_off()
        except Exception:
            pass
        log("脚本退出")


# ============== 入口:AScript 工程约定顶层直接执行 ==============
main()
