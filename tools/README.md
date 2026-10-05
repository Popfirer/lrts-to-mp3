# tools —— 外部解密工具目录

本目录用于放置**解密工具**，它**不随本仓库分发**（已在 `.gitignore` 中排除）。

## 需要什么

| 文件 | 说明 |
| :--- | :--- |
| `懒人听书音频批量转换.exe` | 负责把手机端的加密缓存解密为可播放的音频（原始产物是 MP4/AAC 容器） |
| `ffmpeg.exe` | **可选**。用于把上一步的产物转码成真正的 MP3（96kbps / 44.1kHz） |

> 本仓库的代码**不包含**任何解密实现，只负责「自动驱动」这个外部工具。
> 请自行从合法渠道获取该工具，并确认你的使用行为符合当地法律法规。

## 程序如何查找这些文件

`ffmpeg.exe` 按以下顺序在候选路径中查找，命中即用：

1. `工具目录\tools\ffmpeg.exe`
2. `工具目录\ffmpeg.exe`
3. `D:\ffmpeg-2025-03-17-git-5b9356f18e-full_build\bin\ffmpeg.exe`
4. `D:\FFmpeg\bin\ffmpeg.exe`
5. `D:\FFmpeg-250706\bin\ffmpeg.exe`
6. 系统 `PATH`

> ⚠️ 注意：部分 `ffmpeg.exe` 是**共享库版**（几百 KB），必须和同目录的一堆
> `avcodec-*.dll` / `avformat-*.dll` 一起使用，单独拷走会启动失败。
> 建议使用**静态编译版**（单个 exe 约 100 MB+，可独立运行）。

解密工具 `懒人听书音频批量转换.exe` 按以下顺序查找：

1. `工具目录\tools\懒人听书音频批量转换.exe`
2. `F:\desktop\懒人听书音频批量转换.exe`（历史路径，仅作兜底）

找不到时程序会在启动时报错提示。

## 验证是否就绪

```bat
"LRTS to mp3.exe" --selftest
```

会在 exe 同目录生成 `_selftest.txt`，列出各路径、解密工具与 ffmpeg 的查找结果。
