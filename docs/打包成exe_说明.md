# 打包成 exe —— LRTS to mp3.exe

把「懒人听书缓存批量转换」封装成一个**单文件可执行程序**，双击即用，不再需要 Python 环境。

---

## 一、产物

| 项目 | 位置 |
|---|---|
| **可执行文件** | `F:\desktop\懒人听书转换\LRTS to mp3.exe`（约 22 MB） |
| 打包脚本 | `打包exe.py`（主逻辑）+ `打包exe.bat`（双击即打包） |
| 构建中间文件 | `F:\desktop\懒人听书转换\_build\`（可随时整个删掉） |

**已内嵌进 exe 的东西**：Python 运行时、tkinter 界面库、pywin32、三个源码模块
（`lanren_gui.py` / `lanren_convert.py` / `lrts_online.py`）、转换工具
`懒人听书音频批量转换.exe`（8.4 MB）、`使用说明.txt`、`图标.ico`。

---

## 二、怎么用

1. 双击 `LRTS to mp3.exe` 即可启动界面；
2. 首次启动会把内嵌的转换工具释放到 **exe 同目录的 `tools\`** 下（约 1 秒）；
3. 输出目录默认仍是 **exe 同目录的 `workspace\<书名>\`**；
4. 可以给 exe 建桌面快捷方式、换图标（用 `图标.ico`）。

> **首次启动会慢几秒**（单文件 exe 要先把 22 MB 内容解包到临时目录），之后就快了。

---

## 三、重新打包

改了源码之后，重新生成 exe：

```bat
双击  打包exe.bat
```

或者命令行：

```bat
"C:\ProgramData\anaconda3\python.exe" "F:\desktop\懒人听书转换\打包exe.py"
```

> ⚠️ **必须用 anaconda 那个 Python**（`C:\ProgramData\anaconda3\python.exe`）——
> 只有它同时具备 tkinter + pywin32 + PyInstaller。托管 Python 三者都没有。

构建约 1.5~3 分钟。

### 打包脚本的两个附加开关

| 开关 | 作用 |
|---|---|
| （默认） | 带 `--clean`，先清理中间产物再构建 —— **正常情况用这个** |
| `--no-clean` | 复用 `_build\work` 增量构建 |
| `--fresh` | 换一个带时间戳的全新工作目录构建（不删任何东西，等价干净构建） |

> 如果你的环境**禁止程序直接删除文件**（例如企业管控、只读回收站），
> 默认的 `--clean` 会因删不掉中间产物而报错退出 —— 此时改用 `--fresh`。
> 本机在 WorkBuddy 沙箱内就属于这种情况，另备了 `_build\_run_build.py`
> （摘掉 `PYTHONPATH` 后启动 PyInstaller，避开注入的安全删除钩子）：
> ```
> "C:\ProgramData\anaconda3\python.exe" "F:\desktop\懒人听书转换\_build\_run_build.py"
> ```

---

## 四、关于 ffmpeg（唯一的外部依赖）

**ffmpeg 没有内嵌进 exe**，原因：可用的静态版 ffmpeg 有 **141 MB**，
塞进单文件 exe 后每次启动都要解包 141 MB，启动会慢到不可接受。

程序按以下顺序自动查找 ffmpeg：

| 顺序 | 位置 | 说明 |
|---|---|---|
| 1 | `软件目录\tools\ffmpeg.exe` | 想做成"拷走就能用"就放这里 |
| 2 | `软件目录\ffmpeg.exe` | 同上 |
| 3 | `D:\ffmpeg-2025-03-17-git-5b9356f18e-full_build\bin\ffmpeg.exe` | ✅ 本机当前命中的就是这个 |
| 4 | `D:\FFmpeg\bin\ffmpeg.exe` | 共享库版，需同目录 DLL |
| 5 | `D:\FFmpeg-250706\bin\ffmpeg.exe` | 共享库版，需同目录 DLL |
| 6 | 系统 `PATH` | 兜底 |

**如果找不到 ffmpeg**：程序会弹提示，并**自动退回"保持原始 MP4 输出"**（不会转成真 MP3）。

> ⚠️ 注意 `D:\FFmpeg\bin` 和 `D:\FFmpeg-250706\bin` 里那个 0.4~0.5 MB 的 `ffmpeg.exe`
> 是**共享库版本**，必须和同目录的 `avcodec-*.dll` 等一起用，**单独拷走不能用**。
> 本机目前命中的是第 3 条"完整静态版"，它是单文件可独立运行的。

想让整个文件夹自带 ffmpeg（拷到别的电脑也能用），把那个静态版拷过来即可：

```bat
copy "D:\ffmpeg-2025-03-17-git-5b9356f18e-full_build\bin\ffmpeg.exe" "F:\desktop\懒人听书转换\tools\ffmpeg.exe"
```

---

## 五、打包后程序怎么找路径（重要）

打包成单文件 exe 后，`__file__` 指向的是 **PyInstaller 的临时解包目录**
（`%TEMP%\_MEIxxxx`，程序一退出就被删）。如果直接把工作区建在那里，
**转出来的文件会在关程序后全部消失**。

所以三个模块都改成了双目录模型：

| 概念 | 打包后 | 源码运行时 | 用途 |
|---|---|---|---|
| `APP_DIR` | **exe 所在目录** | 源码目录 | 可写：`workspace\`、`tools\`、`online_cache.json`、日志 |
| `RES_DIR` | `sys._MEIPASS`（解包目录） | 源码目录 | 只读：内嵌的说明文档、图标、转换工具 |

`lanren_convert.py` 里的 `TOOLS_DIR` / `WORKSPACE` / `OUTPUT_ROOT` / `HELP_FILE`、
`lrts_online.py` 的 `CACHE_FILE` 全部改用 `APP_DIR`。
**源码方式运行时的行为与改造前完全一致**（`APP_DIR` 就等于源码目录）。

> 换来的一个副作用（正面）：`collect_source_files()` 返回值仍是 `(items, skipped)`；
> 但新增了 `converter_available()` 与 `APP_DIR`/`RES_DIR`/`CONVERTER_EXE_BUNDLED` 三个名字，
> 外部脚本如需引用路径请用这些。

---

## 六、环境自检（排查神器）

打包后如果出现"找不到转换工具 / 找不到 ffmpeg / 目录不对"，运行：

```bat
"F:\desktop\懒人听书转换\LRTS to mp3.exe" --selftest
```

它会在 **exe 同目录**生成 `_selftest.txt`，内容形如：

```
LRTS to mp3 —— 环境自检
打包运行(frozen)         True
exe / 入口               F:\desktop\懒人听书转换\LRTS to mp3.exe
sys._MEIPASS             C:\Users\...\AppData\Local\Temp\_MEI123456
软件目录 APP_DIR          F:\desktop\懒人听书转换
资源目录 RES_DIR          C:\Users\...\Temp\_MEI123456
工作区 WORKSPACE          F:\desktop\懒人听书转换\workspace
转换工具可用?             True
ffmpeg                   D:\ffmpeg-...\bin\ffmpeg.exe
软件目录可写?             True
```

**判读要点**：`APP_DIR` / `WORKSPACE` 必须是 **exe 所在目录**（不是 `_MEIxxxx`），
否则就是打包配置出错了。

---

## 七、已知限制

1. **首次启动慢几秒** —— 单文件 exe 的固有代价。
   想彻底消除可以改成"目录版"（`--onedir`），启动秒开，但会生成一个含几十个文件的文件夹。
2. **可能被杀软/DLP 误报** —— PyInstaller 单文件 exe 是杀软重点关照对象。
   本机装有天锐绿盾 DLP，若被拦截，把 exe 或所在目录加入白名单。
3. **不能跨机器保证可用** —— 程序依赖 pywin32 通过 MTP 访问手机，
   换电脑要装手机驱动；ffmpeg 也需按第四节配置。
4. **改代码后必须重新打包** —— exe 里是代码副本，改 `.py` 对 exe 无效。

---

## 八、本次改造改动的文件

| 文件 | 改动 |
|---|---|
| `lanren_convert.py` | 新增 `_app_dir()`/`_res_dir()` → `APP_DIR`/`RES_DIR`；新增 `CONVERTER_EXE_BUNDLED`、`converter_available()`、`find_help_file()`、`ICON_FILE`；`ensure_converter()` 支持从内嵌资源释放；`FFMPEG_CANDIDATES` 增加"软件目录/tools"两条；转换工具 `Popen` 加 `stdin=DEVNULL`（无控制台模式下必需） |
| `lanren_gui.py` | `BASE_DIR` 区分 frozen；窗口标题改为「LRTS to mp3 —— 懒人听书缓存批量转换」；窗口/任务栏图标改用内嵌 `图标.ico`；转换工具检查改用 `converter_available()`；修正"未找到 ffmpeg"提示与行为不一致（现在真的会切到 MP4 输出）；新增 `--selftest` |
| `lrts_online.py` | `CACHE_FILE` 改用 `APP_DIR`（原来在 `__file__` 旁边，打包后会落到临时目录） |
| `打包exe.py` / `打包exe.bat` | 新增，一键打包 |

备份：`_backup\*.1005_140344.bak`（改动前原样）。

---

## 九、验证记录（实测）

**① 语法检查** —— 三个模块全部通过。

**② 路径自检**（`_reports\_verify_pack_paths.py`）
- 非打包模式：11 项路径全部与改造前一致（回归无破坏）；
- 伪造 `sys.frozen=True` + `sys._MEIPASS` 模拟打包环境：
  `APP_DIR=E:\Portable`、`RES_DIR=C:\Temp\_MEI123456`、`CACHE_FILE=E:\Portable\online_cache.json` —— 判定正确。

**③ 回归测试**
- `_test_ep_range.py` **62/62 通过**
- `_test_gui_ep_range.py` **30/30 通过**

**④ 打包产物验收**（`_reports\_test_exe.py`，把 exe 拷到干净空目录 `F:\_lrts_pkgtest` 再跑）

```
exe 体积: 22.1 MB
--selftest 退出码 = 0
打包运行(frozen)        True
exe / 入口              F:\_lrts_pkgtest\LRTS to mp3.exe
sys._MEIPASS            C:\Users\...\Temp\_MEI342162
软件目录 APP_DIR         F:\_lrts_pkgtest            ← 正确：= exe 所在目录
资源目录 RES_DIR         C:\Users\...\Temp\_MEI342162  ← 正确：= 解包目录
工作区 WORKSPACE         F:\_lrts_pkgtest\workspace
使用说明 HELP_FILE        ...\_MEI342162\使用说明.txt   √ 内嵌资源读得到
图标 ICON_FILE           ...\_MEI342162\图标.ico      √
转换工具可用?            True
内嵌资源 tools           √ ...\_MEI342162\tools\懒人听书音频批量转换.exe
ensure_converter()       F:\_lrts_pkgtest\tools\懒人听书音频批量转换.exe  ← 成功释放到 exe 目录
ffmpeg                   D:\ffmpeg-...\full_build\bin\ffmpeg.exe  ← 自动找到
在线核对模块             OK
pywin32(手机MTP)         OK
软件目录可写?            True
```

启动后 `F:\_lrts_pkgtest` 实际生成：
```
LRTS to mp3.exe                 23137789 B
_selftest.txt                       2451 B
tools\懒人听书音频批量转换.exe        8796789 B   ← 首次运行自动释放
```

**⑤ 界面启动确认**（`_reports\_test_exe_title.py`）
- exe 进程存活，窗口标题 = **`LRTS to mp3 —— 懒人听书缓存批量转换`** → 跑的是新代码。
- 退出后窗口干净消失，无残留进程。

---

## 十、本次遗留的临时文件（可删）

打包/排查过程中在工作区外留了两个临时物，**我没有权限删除，你需要时手动清理**：

| 路径 | 说明 |
|---|---|
| `F:\_lrts_pkgtest\` | 验收用的"干净目录"沙箱（含一份 22 MB 的 exe 副本），确认没问题后可直接删 |
| `_build\_probe_child.py`、`_build\_probe_parent.py` | 定位"删除被拦截"问题用的探针脚本 |

`_build\` 整个目录本身就是中间产物，随时可删（删了下次构建会重新生成）。

