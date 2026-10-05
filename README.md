<div align="center">

<img src="图标.jpg" alt="LRTS to mp3" width="140" height="140">

# LRTS to mp3

**懒人听书缓存批量转换工具**

把手机端「懒人听书」App 的加密缓存，一键解密、批量转码成可直接播放的真 MP3

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#-环境要求)
[![GUI](https://img.shields.io/badge/GUI-Tkinter-orange.svg)](#-界面功能)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 📖 项目简介

本项目是一个面向 **Windows** 的图形化小工具，用于把华为等安卓手机上「懒人听书」App 的**加密下载缓存**，
批量解密并转码成**真正的 MP3**，输出到电脑上，方便离线收听、长期归档或在其它播放器里使用。

整个流程全自动：**从手机读取 → 复制到本地 → 解密 → 转码为真 MP3 → 写入 ID3 标签**。
全程图形界面操作，双击即可运行；也支持命令行与单文件 exe 打包。

> 🎧 适用的缓存目录（脚本会自动扫描两个位置）：
> `内部存储\Android\data\bubei.tingshu\files\down`
> `存储卡\Android\data\bubei.tingshu\files\down`

---

## ✨ 功能特性

| 特性 | 说明 |
| :--- | :--- |
| 🔍 **自动扫描手机缓存** | 通过 MTP 读取手机上的小说缓存，缓存文件夹名是 URL-safe Base64，脚本自动解码还原书名 |
| ⚡ **批量并行复制** | 一次投递多个复制任务，Shell 复制队列持续有活干；复制与转换彻底分离，避免边复制边等 |
| 🔓 **自动解密转换** | 自动驱动外部解密工具完成解密，转换完的中间产物自动清除（天然增量，重跑自动续传） |
| 🎵 **转码为真 MP3** | 用 ffmpeg 把假 MP3（实为 MP4/AAC 容器）转成 96kbps / 44.1kHz 的真 MP3，多进程并行 |
| 🏷️ **写入 ID3 标签** | 自动写入 标题=集名 / 专辑=书名 / 艺术家=主播 / 作曲=作者 |
| 🌐 **联网核对集数** | 查询懒人听书官网的**总集数、连载/完结状态、作者、主播**，与本地对比并提示缺集（免登录） |
| 🎯 **按集数范围下载** | 可为每本小说单独指定「只处理第几集 ~ 第几集」，省空间、省时间 |
| ⏸️ **暂停 / 恢复 / 取消** | 转码过程中可随时暂停、继续、取消，已完成的成果不会白做 |
| 🧹 **删除手机缓存** | 转换成功后可一键删除手机端缓存（**不可恢复，默认关闭**，需二次确认） |
| 📦 **单文件打包** | 一条命令打包成独立 exe，无需目标机器安装 Python |
| 🩺 **环境自检** | `--selftest` 一条命令列出各路径、ffmpeg、工具状态，便于排查 |

---

## 🔄 工作流程

```mermaid
flowchart LR
    A["📱 手机缓存<br/>MTP 读取 · Base64 解码"] --> B["📦 复制到本地<br/>批量并行 · 断点续传"]
    B --> C["🔓 解密转换<br/>外部解密工具"]
    C --> D["🎵 转码真 MP3<br/>ffmpeg × 6 并行"]
    D --> E["📂 workspace/书名/<br/>第NNN集_标题.mp3"]

    style A fill:#e3f2fd,stroke:#1976d2,color:#0d47a1
    style B fill:#e8f5e9,stroke:#388e3c,color:#1b5e20
    style C fill:#fff3e0,stroke:#f57c00,color:#e65100
    style D fill:#f3e5f5,stroke:#7b1fa2,color:#4a148c
    style E fill:#fce4ec,stroke:#c2185b,color:#880e4f
```

<details>
<summary><b>点开看每一步到底发生了什么</b></summary>

**第 1 步 · 复制到本地（提速关键）**
- 把选中小说的缓存文件从手机复制到电脑本地文件夹
- 采用「批量并行复制」：一次投递多个复制任务，Shell 复制队列持续有活干；
  同时用本地磁盘扫描判断完成，不再逐个去枚举手机目录（那样最慢）
- 复制完成后才进入第 2 步；已复制过的文件自动跳过，中断后重跑可续传

**第 2 步 · 解密转换**
- 自动调用解密工具；工具启动后会弹出「选择文件夹」对话框，
  脚本自动填入路径并点击确认，解密完成后工具自行退出
- ⚠️ 转换工具的原始产物扩展名虽然叫 `.mp3`，**实际是 MP4/AAC 容器**（这也是本项目要转码的原因）

**第 3 步 · 转码为真正的 MP3（并行）**
- 用 ffmpeg 转成真正的 MP3（默认 96kbps）
- 同时写入 ID3 标签；多个 ffmpeg 进程并行（默认 6 个），比逐个转码快数倍
- 转码成功即删掉中间产物，输出目录只留 MP3

</details>

---

## 📋 环境要求

| 项目 | 要求 |
| :--- | :--- |
| 操作系统 | Windows 10 / 11（依赖 `win32com` 访问 MTP，不支持 macOS / Linux） |
| Python | 3.8 及以上（源码运行需要） |
| 依赖库 | `pywin32`、`tkinter`（Python 自带）、`Pillow`（仅打包图标时需要） |
| ffmpeg | 转码为真 MP3 所需，缺省时程序会退化为输出 MP4 容器 |
| 手机 | 安卓手机 + 数据线，USB 用途需切到「传输文件(MTP)」 |

安装依赖：

```bat
pip install pywin32
```

---

## 🚀 快速开始

### 方式一：使用打包好的 exe（推荐，无需 Python）

1. 下载 `LRTS to mp3.exe`（见 **Releases** 页面）
2. 双击运行，界面自动打开
3. 左下勾选小说 → 点「开始转换」

> exe 已内嵌 Python 运行时、tkinter、pywin32、解密工具、使用说明与图标。
> 唯一的外部依赖是 **ffmpeg**，程序会自动在 `软件目录\tools\`、`软件目录\`、`D:\` 常见位置及 `PATH` 中查找。

### 方式二：源码运行

```bat
:: 1. 克隆仓库
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>

:: 2. 安装依赖
pip install pywin32

:: 3. 启动图形界面
双击「启动转换界面.bat」
:: 或命令行
python lanren_gui.py
```

> 📌 `启动转换界面.bat` 里写的是本机 Python 绝对路径，**换机器请改成你自己的路径**
> （例如 `pythonw.exe` 所在目录），或直接执行 `pythonw lanren_gui.py`。

---

## 📁 目录结构

```
懒人听书转换/
├── lanren_gui.py            # 图形界面（tkinter）
├── lanren_convert.py        # 核心逻辑：复制 / 解密 / 转码 + 命令行入口
├── lrts_online.py           # 联网核对模块（官网集数、状态、作者、主播）
├── 启动转换界面.bat          # 双击启动界面
├── lanren_convert.bat       # 命令行模式启动
├── 打包exe.py / 打包exe.bat  # 一键打包成单文件 exe
├── 使用说明.txt              # 完整使用说明（软件内「使用说明」按钮打开）
├── 图标.ico / 图标.jpg       # 程序图标
├── docs/                    # 各功能的设计与修复说明文档
│   ├── 集数范围功能_说明.md
│   ├── 暂停取消功能_说明.md
│   ├── 转换中断问题_修复说明.md
│   ├── GUI按钮锁死_修复说明.md
│   └── 打包成exe_说明.md
├── assets/                  # 仓库展示资源（收款码等）
└── tools/                   # 放解密工具（不入库，见 tools/README.md）
```

运行时输出的 MP3 都落在：

```
软件目录\workspace\<书名>\第XXX集_剧集名.mp3
```

---

## 💻 命令行用法

```bat
python lanren_convert.py --list                 :: 只列出手机上的小说
python lanren_convert.py --list --online        :: 列出并联网核对集数 / 作者 / 主播
python lanren_convert.py --all                  :: 转换全部
python lanren_convert.py --name 大明官           :: 只转换名字含「大明官」的
python lanren_convert.py --name 大明官 --limit 3 :: 每本只处理前 3 集（测试用）
python lanren_convert.py --name 大明官 --range 700-717  :: 只处理第 700~717 集
python lanren_convert.py --range 700-           :: 只处理第 700 集之后的（≥700）
python lanren_convert.py --range -300           :: 只处理到第 300 集（≤300）
python lanren_convert.py --out D:\听书            :: 指定输出目录
python lanren_convert.py --all --delete-phone   :: 转换成功后删除手机端缓存
```

---

## 🎯 集数范围功能

手机空间不够、或只想先听某一段时，可以**只把指定集数**复制、转换到电脑。

**设置方式（三选一）**

| 方式 | 操作 |
| :--- | :--- |
| 批量 | 勾选若干本 → 在「集数范围」栏填第 `700` 集 ~ 第 `717` 集 → 点「应用到勾选本」 |
| 单本 ① | **双击**列表里那一行 → 弹窗填起止 |
| 单本 ② | 选中该行 → 点分区下方的「设范围…」 |

**填写规则**

- 两端都留空 = 全部（不限制）
- 只填起始 `700` = 第 700 集起（≥700）
- 只填结束 `300` = 到第 300 集为止（≤300）
- 起止填反了会自动交换；集号必须是正整数

**生效范围（重要）**

- 只把区间内的缓存从手机复制到电脑，**区间外的手机文件不动、不复制**
- 本地已有的区间外文件也**不会被转码、不会改名、不会删除**
- 解析不出集号的条目（片花、番外）**不受限制，始终会被复制**过来
- 范围只决定"这次处理哪些集"，不删除任何已转好的 MP3

---

## ❓ 常见问题

<details>
<summary><b>提示「未检测到手机」？</b></summary>

用 USB 线连接手机，在手机通知栏把 USB 用途选成「传输文件(MTP)」，再点「刷新列表」。
部分机型需同时开启「USB 调试」或解锁屏幕。
</details>

<details>
<summary><b>界面上集数是「…」或「?」？</b></summary>

「…」表示还在统计；「?」表示读取该文件夹失败（通常是手机端正在读写）。
</details>

<details>
<summary><b>日志提示找不到 ffmpeg，只能输出 MP4？</b></summary>

把 `ffmpeg.exe` 复制到本软件的 `tools\` 目录即可被自动识别；
没有 ffmpeg 时程序会保留解密工具的原始输出（MP4 容器，也能播放）。
</details>

<details>
<summary><b>转换到一半关掉了界面，会白做吗？</b></summary>

不会。已复制的缓存在 `workspace` 里，已转好的 MP3 也在。
重新勾选同一本书再跑，已存在的会自动跳过，只补没做完的。
</details>

<details>
<summary><b>设置了集数范围，以前转好的其它集会丢吗？</b></summary>

不会。范围只决定"这次从手机复制哪些集、转码哪些集"，
本地已有的、区间外的文件一律原样保留，既不删除也不改动。
</details>

<details>
<summary><b>为什么手机上同一本书有两个缓存文件夹？</b></summary>

内部存储与存储卡各有一份（有时存储卡上还有另一版本、集数编号不同）。
界面左右两个分区会分别列出；两边的文件都会被复制合并到同一个输出目录。
</details>

<details>
<summary><b>作者 / 主播列是空的？</b></summary>

需要先做一次「联网核对」；未匹配到官网条目的书不会显示作者与主播。
</details>

---

## ⚠️ 免责声明

- 本工具**仅供个人学习、研究与备份自己已购买/已下载的内容**使用。
- 请**勿**将转换后的音频用于任何商业用途或公开传播，由此产生的一切后果由使用者自行承担。
- 本项目**不包含**任何解密组件；解密能力由使用者自行提供的外部工具完成，与本仓库代码无关。
- 本项目与「懒人听书」官方**无任何关联**，所有版权归原权利人所有。
- 请在使用前确认你的行为符合当地法律法规及相关平台的服务条款。

---

## ☕ 支持开源

如果这个工具帮到了你，欢迎**扫下方二维码**请作者喝杯咖啡 —— 你的支持是我持续更新与维护的动力 ❤️

<div align="center">

<img src="assets/donate-alipay.jpg" alt="支付宝收款码" width="300">

*支付宝扫码支持 · 感谢每一位支持者*

</div>

**也欢迎用这些方式支持：**

- ⭐ 给本项目点一个 **Star**
- 🐛 提交 [Issue](../../issues) 反馈问题或建议
- 🔀 提交 Pull Request 一起改进代码
- 📢 把项目分享给有同样需求的朋友

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源，你可以自由使用、修改和分发，请保留原作者版权声明。

<div align="center">

**Made with ❤️ by Wang Qiang**

</div>
