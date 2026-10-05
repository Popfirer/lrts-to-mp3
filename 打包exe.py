# -*- coding: utf-8 -*-
"""
打包「LRTS to mp3.exe」
======================
把 lanren_gui.py 及其依赖打成**单文件** Windows 可执行程序。

- 必须用**系统 Python（anaconda3）**运行：它有 tkinter + pywin32 + PyInstaller；
  托管 Python 三者都没有。
- 用法：双击「打包exe.bat」，或命令行
      C:\\ProgramData\\anaconda3\\python.exe 打包exe.py

产物：F:\\desktop\\懒人听书转换\\LRTS to mp3.exe
中间文件：F:\\desktop\\懒人听书转换\\_build\\（可随时整个删掉）

ffmpeg 不内嵌（静态版 141 MB，塞进去后每次启动都要解包，太慢）——
程序会按以下顺序自动找它：
  软件目录\\tools\\ffmpeg.exe → 软件目录\\ffmpeg.exe → D:\\ 下的已知位置 → PATH
想做成"整个文件夹拷走就能用"，把 ffmpeg.exe 放到 软件目录\\tools\\ 即可。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\ProgramData\anaconda3\python.exe"      # 必须是有 tkinter+pywin32 的那个
APP_NAME = "LRTS to mp3"
ENTRY = os.path.join(ROOT, "lanren_gui.py")
ICON = os.path.join(ROOT, "图标.ico")
HELP = os.path.join(ROOT, "使用说明.txt")
CONVERTER = os.path.join(ROOT, "tools", "懒人听书音频批量转换.exe")
BUILD_DIR = os.path.join(ROOT, "_build")

# 一个都不用的重型库，显式排除以免被 anaconda 环境顺带拖进来
EXCLUDES = [
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "IPython",
    "pytest", "setuptools", "pip", "conda", "sqlite3", "unittest",
    "pydoc", "doctest", "lib2to3", "distutils",
]


def main():
    print("=" * 64)
    print("打包 {}".format(APP_NAME))
    print("=" * 64)

    for p, label in ((ENTRY, "入口 lanren_gui.py"),
                     (ICON, "图标 图标.ico"),
                     (HELP, "使用说明.txt"),
                     (CONVERTER, "转换工具")):
        if not os.path.exists(p):
            print("[错误] 缺少{}: {}".format(label, p))
            return 2
        print("  √ {}  ({} B)".format(label, os.path.getsize(p)))

    if not os.path.exists(PY):
        print("[错误] 找不到 anaconda python:", PY)
        return 2

    try:
        import PyInstaller                                   # noqa: F401
    except ImportError:
        print("[错误] 当前 Python 没装 PyInstaller，请先：")
        print('       "{}" -m pip install pyinstaller'.format(PY))
        return 2

    os.makedirs(BUILD_DIR, exist_ok=True)

    # --clean 会让 PyInstaller 递归删 workpath 与用户缓存；
    # 在"禁止直接删除"的受限环境里这步会被拦下并报错，故提供两个开关：
    #   --no-clean  复用 _build\work 增量构建
    #   --fresh     改用带时间戳的全新 workpath（不删任何东西，等价于干净构建）
    fresh = "--fresh" in sys.argv
    do_clean = not fresh and "--clean" not in sys.argv \
        and "--no-clean" not in sys.argv
    if fresh:
        workpath = os.path.join(
            BUILD_DIR, "work_" + time.strftime("%m%d_%H%M%S"))
        print("  (--fresh: 全新工作目录 {})".format(workpath))
    else:
        workpath = os.path.join(BUILD_DIR, "work")
        if not do_clean:
            print("  (已跳过 --clean: 复用 {} 增量构建)".format(workpath))

    cmd = [
        PY, "-m", "PyInstaller",
        "--noconfirm",
    ]
    if do_clean:
        cmd.append("--clean")
    cmd += [
        "--onefile",                     # 单文件
        "--windowed",                    # 不带黑色控制台窗口
        "--name", APP_NAME,
        "--icon", ICON,
        # 内嵌资源（打包后可在 RES_DIR = sys._MEIPASS 里读到）
        "--add-data", HELP + ";.",
        "--add-data", ICON + ";.",
        "--add-data", CONVERTER + ";tools",
        # pywin32 是动态加载的，显式声明免得漏
        "--hidden-import", "win32com",
        "--hidden-import", "win32com.client",
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "win32timezone",
        "--distpath", ROOT,
        "--workpath", workpath,
        "--specpath", BUILD_DIR,
        ENTRY,
    ]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]

    print("\n开始构建（首次约 1~3 分钟）…\n")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("\n[失败] PyInstaller 返回码 =", r.returncode)
        return r.returncode

    exe = os.path.join(ROOT, APP_NAME + ".exe")
    if not os.path.exists(exe):
        print("\n[失败] 没有生成", exe)
        return 3

    print("\n" + "=" * 64)
    print("构建完成，耗时 {:.1f} 秒".format(time.time() - t0))
    print("产物: {}  ({:.1f} MB)".format(exe, os.path.getsize(exe) / 1048576))
    print("中间文件: {}（可整个删除）".format(BUILD_DIR))
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
