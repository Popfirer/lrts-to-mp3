# -*- coding: utf-8 -*-
"""
懒人听书缓存 -> MP3 批量转换脚本
================================
流程(三步):
  1. 通过 MTP 读取手机(华为 Mate 20)上懒人听书的缓存目录:
       内部存储\\Android\\data\\bubei.tingshu\\files\\down
       存储卡\\Android\\data\\bubei.tingshu\\files\\down
     缓存文件夹名是 URL-safe Base64 编码, 解码得到「小说名|副标题」。
     【提速】把选中小说的缓存"批量并行"复制到本地文件夹 —— 一次投递多个
     复制任务, 并用本地磁盘扫描判断完成(不再逐文件枚举 MTP 目标目录);
     复制与转换彻底分离, 先把文件全复制到本地, 再统一转换。
  2. 自动驱动「懒人听书音频批量转换.exe」完成解密转换
     (程序启动后弹出「选择文件夹」对话框, 脚本自动填入路径并点击确认)。
  3. 就地转码为真正的 MP3(多个 ffmpeg 进程并行, 可写入作者/主播 ID3 标签),
     结果就留在软件目录下的 workspace\\<书名>\\ 中。

   附加:
     --online        联网核对官网集数、连载/完结状态, 并记录作者与主播。
     --delete-phone  转换成功后删除手机端缓存文件夹(不可恢复, 慎用)。

用法:
  双击「启动转换界面.bat」打开图形界面(推荐)
  命令行:
    python lanren_convert.py --list              只列出手机上的小说
    python lanren_convert.py --list --online     列出并联网核对集数与是否完结
    python lanren_convert.py --all               全部转换
    python lanren_convert.py --name 大明官        转换名称包含"大明官"的小说
    python lanren_convert.py --name 大明官 --limit 3   每本最多复制转换 3 集(测试用)
    python lanren_convert.py --name 大明官 --range 700-717  只处理第700~717集
    python lanren_convert.py --all --delete-phone      转换完并删除手机端缓存
"""
import argparse
import base64
import concurrent.futures
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

import win32com.client
import win32con
import win32gui

try:                                        # 在线核对模块(可选)
    import lrts_online as online
except Exception:                           # noqa: BLE001
    online = None

# ==================== 配置(按需修改) ====================
PHONE_NAME = "Mate 20"                      # 「此电脑」里显示的手机名
VOLUMES = ["内部存储", "存储卡"]             # 手机上的两个存储位置
APP_PATH = ["Android", "data", "bubei.tingshu", "files", "down"]
EXE_NAME = "懒人听书音频批量转换.exe"
HELP_NAME = "使用说明.txt"
ICON_NAME = "图标.ico"


def _app_dir():
    """可写根目录。

    打包成 exe 后 = **exe 所在目录**（不能用 __file__, 那是临时解包目录,
    退出即删, 会把 workspace/缓存 全写丢）；源码运行时 = 源码目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _res_dir():
    """只读资源目录: 打包后 = PyInstaller 解包目录(_MEIPASS), 否则 = 源码目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return os.path.abspath(meipass)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()            # 可写: workspace / tools / 各类缓存都落这里
RES_DIR = _res_dir()            # 只读: 打包进 exe 的内置资源
BASE_DIR = APP_DIR              # 兼容旧名(历史脚本可能引用)
TOOLS_DIR = os.path.join(APP_DIR, "tools")               # 项目内工具目录
CONVERTER_EXE = os.path.join(TOOLS_DIR, EXE_NAME)        # 嵌入的转换工具
CONVERTER_EXE_BUNDLED = os.path.join(RES_DIR, "tools", EXE_NAME)  # 打包内嵌副本
CONVERTER_EXE_FALLBACK = os.path.join(r"F:\desktop", EXE_NAME)   # 桌面副本(备用)
WORKSPACE = os.path.join(APP_DIR, "workspace")           # 本地工作区
OUTPUT_ROOT = WORKSPACE                     # 输出目录: 软件目录\workspace\<书名>\
DIALOG_TITLE = "选择文件夹"                  # 转换工具弹出的对话框标题
COPY_TIMEOUT = 1800                         # 单文件复制超时(秒)
COPY_WINDOW = 8                             # 同时投递的复制任务数(并行提速)
CONVERT_TIMEOUT = 4 * 3600                  # 单本转换超时(秒)
OUTPUT_FORMAT = "mp3"                       # "mp3"=转码为真MP3; "auto"=保持工具原始输出(MP4)
MP3_BITRATE = "96k"                         # MP3 码率(有声书 96k 足够)
TRANSCODE_WORKERS = 6                       # 并行转码进程数
def find_help_file():
    """定位《使用说明.txt》: 优先软件目录(用户可自行修改), 其次内嵌资源。"""
    for p in (os.path.join(APP_DIR, HELP_NAME),
              os.path.join(RES_DIR, HELP_NAME)):
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, HELP_NAME)


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


HELP_FILE = find_help_file()
ICON_FILE = _first_existing(os.path.join(APP_DIR, ICON_NAME),
                            os.path.join(RES_DIR, ICON_NAME))
# ========================================================
LOG_SINK = None     # 由图形界面注入的日志回调; 为 None 时输出到控制台


def log(msg=""):
    """统一日志出口: 界面注入回调时走回调, 否则打印到控制台。"""
    s = str(msg)
    if LOG_SINK is not None:
        try:
            LOG_SINK(s)
            return
        except Exception:
            pass
    print(s, flush=True)


def decode_name(encoded):
    """解码懒人听书的 URL-safe Base64 名称, 返回小说名(取 | 前的部分)。"""
    dec = decode_name_full(encoded)
    if not dec:
        return None
    return dec.split("|")[0].split("｜")[0].strip() or dec


def decode_name_full(encoded):
    """解码并返回完整名称(含 | 后的副标题/标签), 用于与官网条目精确比对。"""
    if not encoded:
        return None
    t = encoded.lstrip(".")
    t = t.replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    try:
        dec = base64.b64decode(t).decode("utf-8")
    except Exception:
        return None
    dec = dec.strip()
    return dec or None


def sanitize(name):
    """去掉文件名非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "未命名"


def find_item(folder, name):
    for it in folder.Items():
        if it.Name == name:
            return it
    return None


def list_files(folder):
    return [x for x in folder.Items() if not x.IsFolder]


def connect_phone():
    """连接手机, 返回 (shell, phone_folder)；失败返回 (None, None)。"""
    shell = win32com.client.Dispatch("Shell.Application")
    pc = shell.NameSpace(17)  # 此电脑
    phone = find_item(pc, PHONE_NAME)
    if phone is None:
        return None, None
    return shell, phone.GetFolder


def get_down_folder(ph, vol):
    """定位某个存储位置的懒人听书缓存目录, 返回 (folder, 错误信息)。"""
    it = find_item(ph, vol)
    if it is None:
        return None, "手机上未找到存储位置: " + vol
    d = it.GetFolder
    for p in APP_PATH:
        nxt = find_item(d, p)
        if nxt is None:
            return None, "{} 下缺少目录 {}".format(vol, "/".join(APP_PATH))
        d = nxt.GetFolder
    return d, None


def scan_volume(ph, vol):
    """扫描单个存储位置, 返回按书名排序的 [(书名, folder_item), ...]"""
    d, err = get_down_folder(ph, vol)
    if d is None:
        log("  [警告] " + err)
        return []
    res = []
    for item in d.Items():
        if not item.IsFolder:
            continue
        name = decode_name(item.Name)
        if not name:
            continue
        res.append((name, item))
    res.sort(key=lambda x: x[0])
    return res


def count_cached_files(folder_item):
    """统计一本小说的缓存音频文件数(不计 .cache)。取不到返回 -1。"""
    try:
        n = 0
        for x in folder_item.GetFolder.Items():
            if not x.IsFolder:
                n += 1
        return n
    except Exception:
        return -1


def scan_novels(ph):
    """扫描两个存储位置, 返回 {书名: [(卷名, folder_item), ...]}"""
    novels = {}
    for vol in VOLUMES:
        for name, item in scan_volume(ph, vol):
            novels.setdefault(name, []).append((vol, item))
    return novels


def novel_full_name(sources):
    """取一本小说缓存文件夹的完整解码名(含副标题), 优先取名字最长的那个。"""
    best = None
    for _vol, item in sources:
        full = decode_name_full(item.Name)
        if full and (best is None or len(full) > len(best)):
            best = full
    return best


def novel_local_count(sources):
    """统计一本小说在手机上的缓存音频文件总数(所有存储位置/分段相加)。"""
    total = 0
    for _vol, item in sources:
        n = count_cached_files(item)
        if n > 0:
            total += n
    return total


def check_online(novels, on_line=None):
    """联网核对每本小说在懒人听书官网的集数与连载状态。

    novels: scan_novels() 的返回值 {书名: [(卷名, item), ...]}
    返回 {书名: info}, info 见 lrts_online.query_novel()
    """
    if online is None:
        log("[提示] 未找到 lrts_online 模块, 跳过联网核对")
        return {}
    online.set_logger(log)
    names = sorted(novels.keys())
    out = {}
    for i, name in enumerate(names, 1):
        sources = novels[name]
        full = novel_full_name(sources) or name
        local = novel_local_count(sources)
        if on_line:
            on_line(i, len(names), name)
        info = online.query_novel(full)
        info["_local"] = local
        out[name] = info
        label, concl = online.compare(local, info)
        if info.get("matched"):
            log("  [{:>3}/{}] 《{}》 官网 {} 集 / {} -> 本地 {} 集：{}".format(
                i, len(names), name, info.get("total"),
                info.get("state_label"), local, label))
        else:
            log("  [{:>3}/{}] 《{}》 {}".format(i, len(names), name, concl))
    return out


COPY_FLAGS = 4 | 16 | 512 | 1024    # 无进度框 / 全部是 / 不确认建目录 / 无界面


def _local_files(d):
    """扫描本地目录 -> {文件名: 大小}。本地 IO, 比逐文件枚举 MTP 快几个数量级。"""
    out = {}
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    out[e.name] = e.stat().st_size
                except OSError:
                    out[e.name] = -1
    except OSError:
        pass
    return out


def _item_size(item):
    """取手机上某个文件的字节数; 取不到返回 -1。"""
    try:
        s = item.Size
        return int(s) if s and s > 0 else -1
    except Exception:
        return -1


def _norm_name(s):
    """归一化名称用于「本地是否已有」判断: 去扩展名、去空白、"N集"->"N"。"""
    if not s:
        return ""
    if "." in s:
        s = os.path.splitext(s)[0]
    s = re.sub(r"(?<=\d)\s*集", "", s)
    return re.sub(r"[\s_　]+", "", s)


# ---------- 集数区间(按小说指定只下载第几集~第几集) ----------
_EP_PATTERNS = (
    re.compile(r"第\s*(\d+)\s*集"),     # 第001集_天下 / 黄金瞳-第717集
    re.compile(r"(\d+)"),               # 北宋大丈夫001  邙山上全是坟堆
)


def parse_episode(text):
    """从解码后的集名里解析集号; 解析不出返回 None。"""
    if not text:
        return None
    for pat in _EP_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                return None
    return None


def in_ep_range(disp_name, ep_range):
    """判断一个集名是否落在 ep_range=(起, 止) 里。

    - ep_range 为 None 或 (None, None) 时不做限制(全部通过)
    - 两端可以为 None, 表示「不限这一端」(如 (700, None) = 第700集起)
    - **解析不出集号的条目(片花、番外等)一律保留**, 避免误删非正片内容
    """
    norm = norm_ep_range(ep_range)
    if norm is None:
        return True
    lo, hi = norm
    n = parse_episode(disp_name)
    if n is None:
        return True
    if lo is not None and n < lo:
        return False
    if hi is not None and n > hi:
        return False
    return True


def norm_ep_range(ep_range):
    """规范化区间: 去空、交换倒置的起止; 全不限时返回 None。

    接受 (起, 止) 或 [起, 止]; 元素可为 None 或字符串数字。
    """
    if not ep_range:
        return None
    if len(ep_range) != 2:
        raise ValueError("集数区间必须是 (起, 止) 两个元素")
    lo, hi = ep_range
    lo = int(lo) if lo not in (None, "") else None
    hi = int(hi) if hi not in (None, "") else None
    if lo is None and hi is None:
        return None
    if lo is not None and lo <= 0:
        raise ValueError("集号必须为正整数")
    if hi is not None and hi <= 0:
        raise ValueError("集号必须为正整数")
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def ep_range_text(ep_range):
    """区间 -> 展示文本(不限时返回「全部」)。"""
    norm = norm_ep_range(ep_range)
    if norm is None:
        return "全部"
    lo, hi = norm
    if lo is None:
        return "第1~{}集".format(hi)
    if hi is None:
        return "第{}集起".format(lo)
    return "第{}~{}集".format(lo, hi)


def parse_range_arg(s):
    """解析区间字符串(命令行 --range / 输入框用)。

    支持 "700-717" / "700~717" / "700-至-717" / "700-" (700起) / "-717" (到717)。
    返回 (起, 止)(元素可为 None); 无法解析或两端皆空时抛 ValueError。
    """
    m = re.match(r"^\s*(\d*)\s*[-~～至]\s*(\d*)\s*$", s or "")
    if not m or (not m.group(1) and not m.group(2)):
        raise ValueError("格式应为「起-止」, 例如 700-717 / 700- / -717")
    return norm_ep_range((m.group(1) or None, m.group(2) or None))


def collect_source_files(sources, limit=None, ep_range=None):
    """汇总一本书在手机上的全部缓存文件(跨存储位置去重)。

    ep_range: (起, 止) 或 None —— 只收集该区间内的集;
              解析不出集号的条目(片花/番外)一律保留。
    返回 (items, skipped) —— items 是 [(显示名, FolderItem), ...],
    skipped 是因超出区间而被跳过的文件数。
    """
    items, seen = [], set()
    skipped = 0
    for vol, folder_item in sources:
        try:
            files = list_files(folder_item.GetFolder)
        except Exception as e:
            log("    [警告] 读取{}缓存目录失败: {!r}".format(vol, e))
            continue
        files.sort(key=lambda x: x.Name)
        for f in files:
            if f.Name in seen:            # 内部存储/存储卡同名文件只取一份
                continue
            seen.add(f.Name)
            disp = decode_name(f.Name) or f.Name
            if not in_ep_range(disp, ep_range):
                skipped += 1
                continue
            items.append((disp, f))
            if limit is not None and len(items) >= limit:
                return items, skipped
    return items, skipped



def copy_files_parallel(dest_ns, workdir, items, on_progress=None):
    """把手机上的文件批量复制到本地目录, 返回 (成功数, 失败列表)。

    提速要点:
      1. 一次性投递多个 CopyHere, 让 Shell 复制队列持续有活干(并行传输);
      2. 用本地目录扫描判断"复制完成", 不再逐个枚举 MTP 目标目录;
      3. 本地已存在且大小正常的文件直接跳过(增量续传)。
    """
    local = _local_files(workdir)
    # 本地已有文件用「解码名归一化」匹配: 转换工具会把缓存改名(去掉点/去"集"),
    # 只按原始编码名判断会导致重复复制。
    local_norm = {_norm_name(f) for f in local}
    todo, skipped = [], 0
    for disp, it in items:
        if local.get(it.Name, 0) > 0:
            skipped += 1
            continue
        full = decode_name_full(it.Name)
        if full and _norm_name(full) in local_norm:
            skipped += 1
            continue
        todo.append((disp, it))
    if not todo:
        return 0, [], skipped

    done_ok = 0
    failed = []
    pending = {}          # 文件名 -> {"disp", "exp", "last", "t0"}
    total = len(todo)

    def verify():
        """检查已投递任务, 返回本次确认完成的名字列表。"""
        cur = _local_files(workdir)
        finished = []
        for nm, meta in pending.items():
            sz = cur.get(nm, -1)
            if sz <= 0:
                meta["last"] = -1
                continue
            exp = meta["exp"]
            if exp > 0:
                if sz == exp:
                    finished.append(nm)
            elif sz == meta["last"]:            # 取不到源大小时, 连续两次不变即认为完成
                finished.append(nm)
            else:
                meta["last"] = sz
        return finished

    def drain(block_all=False):
        nonlocal done_ok
        while pending:
            time.sleep(0.3)
            for nm in verify():
                meta = pending.pop(nm)
                done_ok += 1
                if on_progress:
                    on_progress(done_ok + len(failed), total, meta["disp"])
            now = time.time()
            for nm, meta in list(pending.items()):
                if now - meta["t0"] > COPY_TIMEOUT:
                    pending.pop(nm)
                    failed.append((meta["disp"], "复制超时"))
                    log("      [超时] {} 复制超过 {} 秒, 跳过".format(
                        meta["disp"], COPY_TIMEOUT))
            if not block_all and len(pending) < COPY_WINDOW:
                return

    for disp, it in todo:
        try:
            dest_ns.CopyHere(it, COPY_FLAGS)
        except Exception as e:
            failed.append((disp, "投递失败 {!r}".format(e)))
            continue
        pending[it.Name] = {"disp": disp, "exp": _item_size(it),
                            "last": -1, "t0": time.time()}
        if on_progress and len(pending) == 1:
            on_progress(done_ok + len(failed), total, disp)
        drain()                                # 队列满时阻塞等待

    drain(block_all=True)
    return done_ok, failed, skipped


# ==================== 手机端删除(不可恢复) ====================
CONFIRM_TITLE_KEYS = ("删除", "Delete", "确认", "Confirm")
YES_BTN_PREFIX = ("是", "确定", "Yes", "OK")


def _enum_dialogs():
    """列出当前所有可见的标准对话框窗口句柄。"""
    res = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) \
                    and win32gui.GetClassName(h) == "#32770":
                res.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return res


def _confirm_loop(stop_event, counter, interval=0.4, max_wait=180):
    """后台线程: 只要出现「删除确认」对话框就点"是"。

    删除动词 DoIt() 会阻塞到对话框中按钮被按下, 因此必须由另一个线程
    (只用 win32gui, 不碰 COM) 来点掉它。
    """
    t0 = time.time()
    while not stop_event.is_set() and time.time() - t0 < max_wait:
        for h in _enum_dialogs():
            try:
                title = win32gui.GetWindowText(h) or ""
            except Exception:
                continue
            if not any(k in title for k in CONFIRM_TITLE_KEYS):
                continue
            btns = find_children(h, cls="Button")
            ok_btn = None
            for bh, _c, text in btns:
                if (text or "").strip().startswith(YES_BTN_PREFIX):
                    ok_btn = bh
                    break
            if ok_btn is None and btns:
                ok_btn = btns[-1][0]        # 兜底: 一般是最后一个按钮
            if ok_btn:
                try:
                    win32gui.SendMessage(ok_btn, win32con.BM_CLICK, 0, 0)
                    counter.append(title)
                    log("      已自动确认删除对话框：「{}」".format(title))
                except Exception:
                    pass
        time.sleep(interval)


def delete_phone_folder(folder_item, verify_timeout=20):
    """永久删除手机上的一个缓存文件夹。返回 (ok, 错误信息)。"""
    try:
        name = folder_item.Name
    except Exception as e:
        return False, "取不到文件夹名: {!r}".format(e)
    try:
        parent = folder_item.Parent
    except Exception:
        parent = None
    verb, vnames = None, []
    try:
        for v in folder_item.Verbs():
            nm = v.Name or ""
            vnames.append(nm)
            if ("删除" in nm) or ("移除" in nm) or ("delete" in nm.lower()):
                verb = v
                break
    except Exception as e:
        return False, "无法枚举右键动词: {!r}".format(e)
    if verb is None:
        return False, "未找到删除动词(可用: {})".format("、".join(vnames) or "无")

    clicks = []
    stop = threading.Event()
    watcher = threading.Thread(target=_confirm_loop, args=(stop, clicks),
                               daemon=True)
    watcher.start()
    try:
        verb.DoIt()          # 会阻塞, 直到确认对话框被点掉
    except Exception as e:
        stop.set()
        return False, "执行删除失败: {!r}".format(e)
    finally:
        stop.set()
    watcher.join(timeout=5)
    if not clicks:
        log("      [提示] 未捕获到删除确认框(可能系统未弹窗, 直接删除)")

    if parent is not None:
        t0 = time.time()
        while time.time() - t0 < verify_timeout:
            if not _item_exists(parent, name):
                return True, None
            time.sleep(0.6)
        return False, "执行后仍能在手机上找到该文件夹"
    return True, None


def _item_exists(parent_folder, name):
    try:
        return find_item(parent_folder, name) is not None
    except Exception:
        return False


def delete_phone_sources(sources):
    """删除一本书在手机上的全部缓存文件夹, 返回 (成功数, 失败列表)。"""
    ok, fails = 0, []
    for vol, item in sources:
        nm = decode_name(item.Name) or item.Name
        log("    [删除] {}：{}".format(vol, nm))
        good, err = delete_phone_folder(item)
        if good:
            ok += 1
            log("      已从手机删除 ✓")
        else:
            fails.append((vol, nm, err))
            log("      [失败] {}".format(err))
    return ok, fails


def find_dialog(title, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        res = []

        def cb(h, _):
            try:
                if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
                    res.append(h)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(cb, None)
        if res:
            return res[0]
        time.sleep(0.5)
    return None


def find_children(hwnd, cls=None, text=None):
    res = []

    def cb(h, _):
        try:
            c = win32gui.GetClassName(h)
            t = win32gui.GetWindowText(h)
            if (cls is None or c == cls) and (text is None or text in t):
                res.append((h, c, t))
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(hwnd, cb, None)
    return res


def run_converter(folder_path):
    """驱动转换工具处理 folder_path, 返回 (returncode, 完整日志文本)。"""
    exe = ensure_converter()
    if not exe:
        raise RuntimeError("找不到转换工具(项目 tools 目录与桌面均无): "
                           + EXE_NAME)
    log_path = os.path.join(WORKSPACE, "_convert_log.txt")
    logf = open(log_path, "wb")
    pr = subprocess.Popen([exe], stdin=subprocess.DEVNULL,
                          stdout=logf, stderr=subprocess.STDOUT)
    try:
        dlg = find_dialog(DIALOG_TITLE, 90)
        if dlg is None:
            raise RuntimeError("转换工具未弹出「选择文件夹」对话框")
        time.sleep(1.5)
        edits = find_children(dlg, cls="Edit")
        if not edits:
            raise RuntimeError("对话框中未找到路径输入框")
        win32gui.SendMessage(edits[0][0], win32con.WM_SETTEXT, 0, folder_path)
        time.sleep(0.8)
        btns = find_children(dlg, cls="Button", text="选择文件夹")
        if btns:
            win32gui.SendMessage(btns[0][0], win32con.BM_CLICK, 0, 0)
        else:
            win32gui.SendMessage(dlg, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
            win32gui.SendMessage(dlg, win32con.WM_KEYUP, win32con.VK_RETURN, 0)

        # 等待转换完成, 实时转发工具日志
        t0 = time.time()
        pos = 0
        while pr.poll() is None:
            time.sleep(2)
            try:
                size = os.path.getsize(log_path)
            except OSError:
                size = 0
            if size > pos:
                with open(log_path, "rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read(size - pos)
                pos = size
                log("      " + chunk.decode("utf-8", "replace").rstrip("\r\n"))
            if time.time() - t0 > CONVERT_TIMEOUT:
                pr.kill()
                raise RuntimeError("转换超时(超过 {:.0f} 小时)".format(CONVERT_TIMEOUT / 3600))
        # 读取剩余日志
        try:
            size = os.path.getsize(log_path)
            if size > pos:
                with open(log_path, "rb") as fh:
                    fh.seek(pos)
                    tail = fh.read(size - pos).decode("utf-8", "replace")
                log("      " + tail.rstrip("\r\n"))
        except OSError:
            pass
    finally:
        logf.close()
    with open(log_path, "rb") as fh:
        return pr.returncode, fh.read().decode("utf-8", "replace")


def converter_available():
    """是否找得到转换工具(浅检查, 不实际复制)。"""
    return any(os.path.exists(p) for p in
               (CONVERTER_EXE, CONVERTER_EXE_BUNDLED, CONVERTER_EXE_FALLBACK))


def ensure_converter():
    """确保转换工具有可用副本; 不在则自动从打包内嵌资源/桌面释放一份。"""
    if os.path.exists(CONVERTER_EXE):
        return CONVERTER_EXE
    for src in (CONVERTER_EXE_BUNDLED, CONVERTER_EXE_FALLBACK):
        if not os.path.exists(src):
            continue
        try:
            os.makedirs(TOOLS_DIR, exist_ok=True)
            shutil.copy2(src, CONVERTER_EXE)
            log("已释放转换工具到: " + CONVERTER_EXE)
            return CONVERTER_EXE
        except Exception as e:
            # 软件目录不可写(只读介质/无权限)时, 直接用原位置副本
            log("[警告] 释放转换工具失败({!r}), 直接使用原有副本".format(e))
            return src
    return None


FFMPEG_CANDIDATES = [
    os.path.join(TOOLS_DIR, "ffmpeg.exe"),           # 软件目录\tools\ffmpeg.exe
    os.path.join(APP_DIR, "ffmpeg.exe"),             # 软件目录\ffmpeg.exe
    os.path.join(RES_DIR, "tools", "ffmpeg.exe"),    # 打包内嵌(若将来塞进去)
    r"D:\ffmpeg-2025-03-17-git-5b9356f18e-full_build\bin\ffmpeg.exe",
    r"D:\FFmpeg\bin\ffmpeg.exe",
    r"D:\FFmpeg-250706\bin\ffmpeg.exe",
]


def find_ffmpeg():
    """定位 ffmpeg.exe, 用于把 AAC/MP4 音频转码为真正的 MP3。"""
    for p in FFMPEG_CANDIDATES:
        if os.path.exists(p):
            return p
    return shutil.which("ffmpeg")


class TranscodeControl:
    """转码任务的暂停 / 取消控制器(线程安全)。

    - ``pause()`` / ``resume()``：在「文件之间」生效——已经启动的 ffmpeg 会把
      当前这一集转完，之后不再启动新的一集；界面点「继续」立刻恢复。
    - ``cancel()``：立即终止所有在途 ffmpeg 进程，并让主循环不再提交新任务。
      未完成的半成品 ``.__tmp.mp3`` 会被清理，**源文件不受影响**，之后直接重跑即可。
    """

    def __init__(self):
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._procs = set()
        self._lock = threading.Lock()

    # ---------- 状态 ----------
    @property
    def cancelled(self):
        return self._cancel.is_set()

    @property
    def paused(self):
        return self._pause.is_set()

    # ---------- 控制 ----------
    def pause(self):
        if not self._cancel.is_set():
            self._pause.set()

    def resume(self):
        self._pause.clear()

    def cancel(self):
        self._cancel.set()
        self._pause.clear()
        self._terminate_all()

    # ---------- 供工作线程调用 ----------
    def wait_if_paused(self):
        """暂停期间阻塞；被取消时立即返回 False。False 表示应中止本任务。"""
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.15)
        return not self._cancel.is_set()

    def register(self, proc):
        """登记一个在途 ffmpeg 进程。返回 False 表示已被取消(调用方应立即放弃)。"""
        with self._lock:
            self._procs.add(proc)
            cancelled = self._cancel.is_set()
        if cancelled:                      # 取消发生在登记之前 -> 立刻杀掉
            self._terminate(proc)
        return not cancelled

    def unregister(self, proc):
        with self._lock:
            self._procs.discard(proc)

    # ---------- 内部 ----------
    @staticmethod
    def _terminate(proc):
        try:
            proc.terminate()
        except Exception:
            pass

    def _terminate_all(self):
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            self._terminate(p)


def transcode_to_mp3(src, dst, ffmpeg, tags=None, control=None):
    """把音频文件转码为 MP3(默认 96k), 可写入 ID3 标签。成功返回 True。

    control: TranscodeControl, 传入后支持转码中途取消(立即终止该 ffmpeg 进程)。
    """
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-vn",
           "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-id3v2_version", "3"]
    for k, v in (tags or {}).items():
        if v:
            cmd += ["-metadata", "{}={}".format(k, v)]
    cmd.append(dst)
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)   # CREATE_NO_WINDOW
    except Exception:
        return False
    if control is not None:
        if not control.register(p):        # 已被取消
            control.unregister(p)
            return False
    try:
        rc = p.wait()
    except Exception:
        rc = -1
    finally:
        if control is not None:
            control.unregister(p)
    return rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


def episode_tags(stem, novel=None, author=None, announcer=None):
    """生成 ID3 标签: 标题=集名, 专辑=书名, 艺术家=主播, 作曲=作者。"""
    tags = {"title": stem}
    if novel:
        tags["album"] = novel
    if announcer:
        tags["artist"] = announcer
    if author:
        tags["composer"] = author
    return tags


def mp3_cbr_duration(path, bitrate_bps=None):
    """按 CBR 码率估算 MP3 时长(秒)。无法识别返回 None。

    bitrate_bps 为 None 时取文件帧头里的码率; 仅认 MPEG1 Layer3。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(10)
            off = 0
            if head[:3] == b"ID3":                       # 跳过 ID3v2 标签
                off = ((head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 |
                       (head[8] & 0x7F) << 7 | (head[9] & 0x7F)) + 10
            fh.seek(off)
            b = fh.read(4)
    except OSError:
        return None
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    if (b[1] >> 3) & 0x03 != 3 or (b[1] >> 1) & 0x03 != 1:   # MPEG1 + Layer3
        return None
    if bitrate_bps is None:
        table = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        bi = (b[2] >> 4) & 0x0F
        if not 1 <= bi <= 14:
            return None
        bitrate_bps = table[bi] * 1000
    return (size - off) * 8 / bitrate_bps


def mp4_duration(path):
    """从 MP4 的 mvhd box 读出时长(秒)。失败返回 None。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    starts = [0] if size <= (8 << 20) else [0, max(0, size - (8 << 20))]
    for start in starts:
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read()
        except OSError:
            continue
        i = data.find(b"mvhd")
        while i >= 0:
            p = i + 4
            try:
                if data[p] == 0:
                    ts, du = struct.unpack(">II", data[p + 12:p + 20])
                else:
                    ts, du = struct.unpack(">IQ", data[p + 20:p + 32])
                if ts:
                    return du / ts
            except (IndexError, struct.error):
                pass
            i = data.find(b"mvhd", i + 1)
    return None


def tmp_is_complete(tmp_path, src_path, tol=5.0):
    """判断 .__tmp.mp3 是否是"完整"的转码成果(时长与源文件一致)。

    返回 False 时调用方必须丢弃它并重新转码 —— 宁可重转一遍, 也绝不能让
    被中断的残次品覆盖掉完好的源文件(会丢后半段音频)。
    """
    try:
        br = int(str(MP3_BITRATE).lower().rstrip("k")) * 1000
    except ValueError:
        br = None
    d_tmp = mp3_cbr_duration(tmp_path, br)
    if d_tmp is None:
        return False
    d_src = mp4_duration(src_path) if detect_audio_ext(src_path) == ".mp4" \
        else mp3_cbr_duration(src_path)
    if d_src is None:
        return False
    return abs(d_tmp - d_src) <= tol


def safe_replace(tmp, dst, tries=5, delay=0.5):
    """把 tmp 归位到 dst, 对"文件被瞬时占用"做退避重试。

    Windows 上 os.replace 覆盖已存在文件时, 若目标正被杀软 / DLP(天锐绿盾) /
    索引器打开, 会抛 PermissionError(ERROR_ACCESS_DENIED / SHARING_VIOLATION),
    而这种占用通常只持续零点几秒。原来失败一次就放弃 -> 转好的 MP3 会永远卡在
    .__tmp.mp3 名下。这里只对"占用类"错误重试, 其它错误立即返回 False
    (调用方须保留 tmp, 等下次运行自动归位)。
    """
    # 5=拒绝访问 32=共享冲突 33=锁冲突 19=介质写保护 1224=被用户映射占用
    RETRYABLE = {5, 19, 32, 33, 1224}
    last = None
    for i in range(max(1, tries)):
        try:
            os.replace(tmp, dst)
            return True
        except OSError as e:
            last = e
            winerr = getattr(e, "winerror", None)
            # Windows 上 EACCES(13)/EAGAIN(11) 对应上面的占用类错误
            if not (winerr in RETRYABLE or getattr(e, "errno", None) in (11, 13)):
                break
            time.sleep(delay * (i + 1))
    log("      归位失败 {} -> {}: {!r}".format(
        os.path.basename(tmp), os.path.basename(dst), last))
    return False


def transcode_in_place(workdir, novel=None, author=None, announcer=None,
                       on_progress=None, control=None, ep_range=None):
    """把目录里的音频就地规整/转码为真正的 MP3(多进程并行)。

    返回 {"ok", "kept", "dup", "fail", "swapfail", "cancelled", "total",
          "skipped"}
    swapfail = 转码成功但改名落盘失败(文件被占用)的数量, 这些会保留
    .__tmp.mp3 不被删除, 下次运行本程序时自动归位。
    cancelled = 因用户取消而未完成的数量(半成品已清理, 源文件保留)。
    control: TranscodeControl, 传入后支持转码中途「暂停 / 继续 / 取消」。
    ep_range: (起, 止) —— 只转码这个区间内的集; 区间外的文件**原样保留**、
              不计入转码任务(skipped)。None 表示不限(与旧行为一致)。
    """
    ffmpeg = find_ffmpeg() if OUTPUT_FORMAT == "mp3" else None
    stat = {"ok": 0, "kept": 0, "dup": 0, "fail": 0, "swapfail": 0,
            "cancelled": 0, "total": 0, "skipped": 0}
    try:
        ep_range = norm_ep_range(ep_range)
    except ValueError:
        ep_range = None
    todo = []
    for f in sorted(os.listdir(workdir)):
        if f.startswith("."):
            continue                       # 尚未解密的缓存
        src = os.path.join(workdir, f)
        if not os.path.isfile(src):
            continue
        # 集数区间限制: 区间外的文件一概不碰(含上次遗留的临时成果)
        if not in_ep_range(os.path.splitext(f)[0], ep_range):
            stat["skipped"] += 1
            continue
        # 上次中断遗留的临时转码成果: 校验完整后归位, 不完整的丢弃重转
        if f.endswith(".__tmp.mp3"):
            base = f[: -len(".__tmp.mp3")]
            dst2 = os.path.join(workdir, base + ".mp3")
            try:
                if os.path.exists(dst2) and detect_audio_ext(dst2) == ".mp3":
                    os.remove(src)
                    stat["dup"] += 1
                elif tmp_is_complete(src, dst2):
                    if safe_replace(src, dst2):   # 覆盖掉同名 MP4 容器
                        stat["ok"] += 1
                        log("      恢复上次中断的转码成果: " + base)
                    else:
                        stat["swapfail"] += 1
                else:
                    # 被中断的残次品: 丢弃, 稍后由同名源文件重新转码
                    os.remove(src)
                    log("      丢弃不完整的转码残次品, 稍后重转: " + base)
            except OSError as e:
                log("      恢复失败 {}: {!r}".format(f, e))
            continue
        stem, ext = os.path.splitext(f)
        real = detect_audio_ext(src) or ext.lower() or ".mp4"
        if OUTPUT_FORMAT != "mp3" or real == ".mp3":
            if ext.lower() != real:        # 扩展名与实际格式不符 -> 改正
                try:
                    os.replace(src, os.path.join(workdir, stem + real))
                except OSError:
                    pass
            stat["kept"] += 1
            continue
        dst = os.path.join(workdir, stem + ".mp3")
        if os.path.exists(dst) and os.path.getsize(dst) > 0 \
                and detect_audio_ext(dst) == ".mp3":
            os.remove(src)                 # 已有同名真 MP3, 这份是重复的
            stat["dup"] += 1
            continue
        # 源文件扩展名可能本身就是 .mp3(容器其实是 MP4), 需要临时名中转
        tmp = dst if os.path.abspath(dst) != os.path.abspath(src) \
            else os.path.join(workdir, stem + ".__tmp.mp3")
        todo.append((f, src, dst, tmp, stem))

    if not todo:
        return stat
    if ffmpeg is None:
        stat["fail"] = len(todo)
        return stat

    def _one(src, dst, tmp, tags):
        """单个文件的"转码 + 归位", 全部在各自的工作线程里完成。

        归位(os.replace)必须放在工作线程里: 若它被 DLP/杀软 阻塞, 只会卡住
        这一条线程; 放在主线程则会让整个收尾循环停摆, 而 6 个 ffmpeg 仍在
        不停产出 .__tmp.mp3 —— 这正是之前一次运行会留下几百个 tmp 的原因。
        """
        # 开工前先看暂停/取消: 取消 -> 放弃; 暂停 -> 在此挂起直到「继续」
        if control is not None and not control.wait_if_paused():
            return "cancelled"
        if not transcode_to_mp3(src, tmp, ffmpeg, tags, control):
            if control is not None and control.cancelled:
                return "cancelled"
            return "fail"
        if os.path.abspath(tmp) != os.path.abspath(dst) \
                and not safe_replace(tmp, dst):       # 覆盖同名 MP4 容器
            return "swapfail"
        if os.path.abspath(src) != os.path.abspath(dst):
            try:
                os.remove(src)                        # 源与目标不同名时才清理
            except OSError:
                pass
        # 只有确认目标确实已是真 MP3 才算成功
        return "ok" if detect_audio_ext(dst) == ".mp3" else "swapfail"

    workers = max(1, min(TRANSCODE_WORKERS, len(todo)))
    log("      并行转码 {} 个文件（{} 个 ffmpeg 进程）…".format(len(todo), workers))
    done = 0
    pending = list(todo)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futs = {}

    def _submit_more():
        """保持最多 workers 个在途任务(滑动窗口)。

        原来是「一次性提交全部」——那样一旦提交就无法停止启动新任务, 也就
        无法实现暂停/取消。改成滑动窗口后, 暂停/取消只需停止补位即可即时生效。
        """
        while pending and len(futs) < workers:
            f, src, dst, tmp, stem = pending.pop(0)
            tags = episode_tags(stem, novel, author, announcer)
            futs[ex.submit(_one, src, dst, tmp, tags)] = (f, src, dst, tmp)

    def _cleanup_tmp(tmp, src):
        if os.path.exists(tmp) and os.path.abspath(tmp) != os.path.abspath(src):
            try:
                os.remove(tmp)                 # 清掉半成品, 源文件保留
            except OSError:
                pass

    try:
        _submit_more()
        while futs:
            finished, _ = concurrent.futures.wait(
                futs, return_when=concurrent.futures.FIRST_COMPLETED)
            for fu in finished:
                f, src, dst, tmp = futs.pop(fu)
                try:
                    res = fu.result()
                except Exception:
                    res = "fail"
                if res == "ok":
                    stat["ok"] += 1
                elif res == "swapfail":
                    # 不清 tmp: 下次运行会在开头把它自动归位
                    stat["swapfail"] += 1
                    log("      [未归位] {} 已转码但改名落盘失败, 已保留 .__tmp.mp3, "
                        "下次运行会自动恢复".format(f))
                elif res == "cancelled":
                    stat["cancelled"] += 1
                    _cleanup_tmp(tmp, src)
                else:
                    stat["fail"] += 1
                    _cleanup_tmp(tmp, src)
                    log("      [失败] 转码 {} 失败, 保留原始文件".format(f))
                done += 1
                if on_progress:
                    on_progress(done, len(todo), f)

            # ---------- 在「文件之间」检查暂停 / 取消 ----------
            if control is not None:
                if control.cancelled:
                    continue         # 不再补位, 等在途的清空后自然退出
                if control.paused:
                    log("      ⏸ 已暂停转码，等待在途 {} 个任务收尾…".format(len(futs)))
                    if not control.wait_if_paused():
                        continue     # 暂停期间被取消
                    log("      ▶ 已继续转码")
            _submit_more()
    finally:
        ex.shutdown(wait=True)

    if stat["cancelled"]:
        log("      ⏹ 已取消：{} 个文件未完成(半成品已清理，源文件保留)。"
            .format(stat["cancelled"]))
    if stat["swapfail"]:
        log("      ⚠ 有 {} 个文件已转码成功但改名归位失败(文件被杀软/DLP 占用), "
            "已保留其 .__tmp.mp3; 重新运行本程序会自动恢复。".format(stat["swapfail"]))
    if stat["skipped"]:
        log("      集数区间外 {} 个文件原样保留(未转码)".format(stat["skipped"]))
    stat["total"] = len(todo)
    return stat


def detect_audio_ext(path):
    """探测真实音频格式(转换工具有时扩展名与实际容器不符)。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return ".mp4"          # MP4/M4A 容器
    if head[:3] == b"ID3":
        return ".mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return ".mp3"
    return None


def process_novel(name, sources, limit=None, info=None, delete_after=False,
                  on_progress=None, control=None, ep_range=None):
    """处理一本小说: ①复制到本地 -> ②解密转换 -> ③就地转码为 MP3。

    输出就放在 OUTPUT_ROOT\\<书名>\\（默认 = 软件目录\\workspace\\<书名>\\）
    info: lrts_online.query_novel() 的结果(取作者/主播写入 ID3), 可为 None
    on_progress: fn(阶段, 已完成, 总数, 名称)
    control: TranscodeControl, 传入后支持转码阶段的「暂停 / 继续 / 取消」。
             取消后阶段 2 的透明转换不再发起、且**绝不会**删除手机缓存。
    ep_range: (起始集, 结束集) —— 只把手机端这个区间内的集复制/转换到本地;
              两端可为 None 表示不限该端, None/空 表示不限(整本)。解析不出
              集号的条目(片花、番外)不受限制, 始终会被复制。
    """
    stat = {"name": name, "copied": 0, "skipped": 0, "converted": 0,
            "mp3": 0, "kept": 0, "dup": 0, "failed_files": 0,
            "out_total": 0, "deleted": 0, "delete_fail": 0,
            "cancelled": 0, "error": None, "ep_range": None, "range_skipped": 0}
    try:
        stat["ep_range"] = norm_ep_range(ep_range)
    except ValueError as e:
        stat["error"] = "集数区间无效: {}".format(e)
        return stat
    safe = sanitize(name)
    workdir = os.path.join(OUTPUT_ROOT, safe)   # 本地工作区 = 输出目录
    os.makedirs(workdir, exist_ok=True)

    author = (info or {}).get("author")
    announcer = (info or {}).get("announcer")
    if (info or {}).get("matched"):
        log("    作者: {}　|　主播: {}".format(author or "—", announcer or "—"))

    def _prog(stage, done, total, nm):
        if on_progress:
            on_progress(stage, done, total, nm)

    def _aborted():
        """用户是否已取消。取消后不再推进任何阶段，尤其**绝不**删除手机缓存。"""
        return control is not None and control.cancelled

    def _bail():
        if _aborted():
            stat["error"] = "已取消"
            log("    已取消，停止后续阶段（此前已完成的文件保留）。")
            return True
        return False

    # ---------- 阶段 1/3: 把手机文件复制到本地 ----------
    if stat["ep_range"]:
        log("    集数范围：只处理 {}".format(ep_range_text(stat["ep_range"])))
    items, range_skipped = collect_source_files(
        sources, limit=limit, ep_range=stat["ep_range"])
    stat["range_skipped"] = range_skipped
    if stat["ep_range"]:
        log("    阶段 1/3　复制到本地：范围内 {} 个缓存文件"
            "（范围外跳过 {} 个）".format(len(items), range_skipped))
    else:
        log("    阶段 1/3　复制到本地：手机端共 {} 个缓存文件".format(len(items)))
    if stat["ep_range"] and not items:
        log("    [提示] 该区间内手机上没有任何缓存文件，请检查集号或范围设置。")
    shell = win32com.client.Dispatch("Shell.Application")
    dest_ns = shell.NameSpace(workdir)
    if dest_ns is None:
        stat["error"] = "无法打开目录 " + workdir
        return stat
    ok_n, failed, skipped = copy_files_parallel(
        dest_ns, workdir, items,
        on_progress=lambda d, t, n: _prog("复制", d, t, n))
    stat["copied"], stat["skipped"] = ok_n, skipped
    log("    复制完成：新复制 {} 个，已存在跳过 {} 个，失败 {} 个".format(
        ok_n, skipped, len(failed)))
    for disp, err in failed[:10]:
        log("      [失败] {}　{}".format(disp, err))

    # ---------- 阶段 2/3: 解密转换 ----------
    if _bail():
        return stat
    encrypted = [f for f in os.listdir(workdir) if f.startswith(".")]
    if encrypted:
        log("    阶段 2/3　解密转换：待处理 {} 个缓存文件（耗时较长，请勿关界面）…"
            .format(len(encrypted)))
        try:
            rc, logtext = run_converter(workdir)
            m = re.search(r"转换完成文件名共计(\d+)个", logtext)
            stat["converted"] = int(m.group(1)) if m else -1
            if rc != 0:
                stat["error"] = "转换工具退出码 {}".format(rc)
        except Exception as e:
            stat["error"] = str(e)
            return stat
    else:
        log("    阶段 2/3　解密转换：无待转换缓存，跳过")

    # ---------- 阶段 3/3: 就地转码为真正的 MP3 ----------
    if _bail():
        return stat
    if OUTPUT_FORMAT == "mp3" and find_ffmpeg() is None:
        log("    [提示] 未找到 ffmpeg，无法转码为真 MP3，保持工具原始输出")
    log("    阶段 3/3　转码 MP3（就地并行）…")
    ts = transcode_in_place(workdir, novel=safe, author=author,
                            announcer=announcer,
                            on_progress=lambda d, t, f: _prog("转码", d, t, f),
                            control=control, ep_range=stat["ep_range"])
    stat["mp3"], stat["kept"] = ts["ok"], ts["kept"]
    stat["dup"], stat["failed_files"] = ts["dup"], ts["fail"]
    stat["cancelled"] = ts.get("cancelled", 0)
    stat["out_total"] = len([x for x in os.listdir(workdir)
                             if not x.startswith(".")
                             and x.lower().endswith((".mp3", ".mp4", ".m4a"))])
    log("    MP3 输出：新转码 {} 个，原已是 MP3 {} 个，去重清理 {} 个，失败 {} 个"
        .format(ts["ok"], ts["kept"], ts["dup"], ts["fail"]))
    log("    输出目录：{}（共 {} 个音频）".format(workdir, stat["out_total"]))

    # ---------- 可选: 删除手机端缓存 ----------
    # 注意：用户中途取消时**绝不能**执行删除，否则会丢掉手机上的原始音频
    if delete_after and stat["out_total"] > 0 and not stat["error"] \
            and not _aborted():
        log("    本地已有成品，开始删除手机端缓存文件夹 …")
        ok_del, fails = delete_phone_sources(sources)
        stat["deleted"], stat["delete_fail"] = ok_del, len(fails)
        log("    手机端删除：成功 {} 个，失败 {} 个".format(ok_del, len(fails)))
    return stat


def parse_selection(s, n):
    s = s.strip().lower()
    if s in ("a", "all", "全部"):
        return list(range(1, n + 1))
    picked = set()
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-~]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            for i in range(a, b + 1):
                if 1 <= i <= n:
                    picked.add(i)
        elif part.isdigit():
            i = int(part)
            if 1 <= i <= n:
                picked.add(i)
    return sorted(picked)


def main():
    ap = argparse.ArgumentParser(description="懒人听书缓存批量转换")
    ap.add_argument("--list", action="store_true", help="只列出手机上的小说")
    ap.add_argument("--all", action="store_true", help="全部转换")
    ap.add_argument("--name", action="append", default=[],
                    help="按名称(包含匹配)转换, 可多次使用")
    ap.add_argument("--limit", type=int, default=None,
                    help="每本最多复制转换的集数(测试用)")
    ap.add_argument("--range", dest="ep_range", default=None,
                    help="只处理指定集数区间, 如 700-717 / 700-(700集起) / -717(到717)")
    ap.add_argument("--online", action="store_true",
                    help="联网核对每本书在懒人听书官网的集数与连载/完结状态")
    ap.add_argument("--out", default=None,
                    help="输出目录(默认: 软件目录\\workspace)")
    ap.add_argument("--delete-phone", action="store_true",
                    help="转换成功后删除手机端的缓存文件夹(不可恢复)")
    args = ap.parse_args()

    ep_range = None
    if args.ep_range:
        try:
            ep_range = parse_range_arg(args.ep_range)
        except ValueError as e:
            print("[错误] --range " + str(e))
            return 1

    global OUTPUT_ROOT
    if args.out:
        OUTPUT_ROOT = os.path.abspath(args.out)

    os.makedirs(WORKSPACE, exist_ok=True)
    os.makedirs(OUTPUT_ROOT, exist_ok=True)

    print("正在连接手机 {} ...".format(PHONE_NAME))
    shell, ph = connect_phone()
    if ph is None:
        print("[错误] 未检测到手机「{}」".format(PHONE_NAME))
        print("       请用 USB 线连接, 并在手机上选择「传输文件」模式后重试。")
        return 1

    print("正在扫描懒人听书缓存 ...")
    novels = scan_novels(ph)
    if not novels:
        print("[错误] 两个存储位置均未发现懒人听书缓存目录。")
        return 1

    names = sorted(novels.keys())
    print("\n发现 {} 本小说缓存:".format(len(names)))

    ol = {}
    if args.online:
        if online is None:
            print("[警告] 未找到 lrts_online 模块, 无法联网核对")
        else:
            print("\n正在联网核对懒人听书官网的集数与连载/完结状态 …")
            def _on_line(i, n, nm):
                sys.stdout.write("\r  核对 [{}/{}] {}".format(i, n, nm) + " " * 20)
                sys.stdout.flush()
            ol = check_online(novels, on_line=_on_line)
            print()

    for i, n in enumerate(names, 1):
        vols = "+".join(v for v, _ in novels[n])
        if args.online and ol:
            info = ol.get(n) or {}
            local = info.get("_local", novel_local_count(novels[n]))
            if info.get("total") is not None:
                label, _ = online.compare(local, info)
                extra = "  官网{:>5}集 {:<4} 本地{:>5}  [{}]".format(
                    info["total"], info.get("state_label") or "?",
                    local, label)
                if info.get("author") or info.get("announcer"):
                    extra += "\n       作者: {}　主播: {}".format(
                        info.get("author") or "—", info.get("announcer") or "—")
            else:
                extra = ("  官网:未查得(疑似限流,可重试)"
                         if info.get("_transient") else "  官网:未匹配")
            print("  {:>3}. {}   [{}]{}".format(i, n, vols, extra))
        else:
            print("  {:>3}. {}   [{}]".format(i, n, vols))

    if args.list:
        return 0

    # 选择
    if args.all:
        sel_names = names
    elif args.name:
        sel_names = [n for n in names if any(w in n for w in args.name)]
        if not sel_names:
            print("[错误] 没有匹配的小说: {}".format(args.name))
            return 1
    else:
        try:
            choice = input(
                "\n请选择要转换的编号(如 1,3,5-8 / a=全部 / q=退出): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice.lower() in ("q", "quit", "exit"):
            return 0
        idx = parse_selection(choice, len(names))
        if not idx:
            print("未选中任何小说, 退出。")
            return 1
        sel_names = [names[i - 1] for i in idx]

    print("\n待转换 {} 本: {}".format(len(sel_names), "、".join(sel_names)))
    print("输出目录: " + OUTPUT_ROOT)
    if ep_range:
        print("集数范围: {} (只复制/转换该区间内的集)".format(ep_range_text(ep_range)))
    if args.delete_phone:
        print("[注意] 已开启「转换成功后删除手机端缓存」, 该操作不可恢复!")
    print("=" * 56)

    results = []
    for k, name in enumerate(sel_names, 1):
        print("\n({}/{}) 《{}》".format(k, len(sel_names), name))
        t0 = time.time()
        stat = process_novel(name, novels[name], limit=args.limit,
                             info=ol.get(name) if ol else None,
                             delete_after=args.delete_phone,
                             ep_range=ep_range)
        stat["seconds"] = round(time.time() - t0)
        results.append(stat)
        flag = "OK" if not stat["error"] else "出错: " + stat["error"]
        print("    《{}》 完成 [{}] 耗时 {} 秒".format(name, flag, stat["seconds"]))

    print("\n" + "=" * 56)
    print("全部完成, 汇总:")
    ok_cnt = 0
    for st in results:
        if st["error"]:
            print("  [失败] 《{}》 {}".format(st["name"], st["error"]))
        else:
            ok_cnt += 1
            print("  [成功] 《{}》 新复制 {} 个, 工具解密 {} 集, "
                  "MP3 {} 个(去重 {}) -> {}\\".format(
                      st["name"], st["copied"], st["converted"],
                      st["mp3"] + st["kept"], st["dup"],
                      os.path.join(OUTPUT_ROOT, sanitize(st["name"]))))
            if st["deleted"] or st["delete_fail"]:
                print("         手机端删除: 成功 {} 个, 失败 {} 个".format(
                    st["deleted"], st["delete_fail"]))
    print("成功 {}/{} 本。".format(ok_cnt, len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
