# GUI「按键被锁定，不能再次执行」— 根因与修复说明

- **文件**：`F:\desktop\懒人听书转换\lanren_gui.py`
- **备份**：`F:\desktop\懒人听书转换\_backup\lanren_gui.py.1004_152514.bak`
- **修复时间**：2026-10-04 15:25
- **验证脚本**：`workspace\北宋大丈夫\_reports\_test_gui_unlock.py`（8 项断言全过）

---

## 一、现象

删除手机缓存后（或在勾选「转换成功后删除手机缓存」的转换结束后），
界面按钮变灰，**无法再次执行任何操作**，严重时连日志都不再刷新。

---

## 二、根因：两条独立的"锁死"路径

界面按钮的锁定/解锁全靠 `busy` 标志 + `_set_busy()`。四个 worker 线程
（扫描 / 联网核对 / 删除 / 转换）跑完后各自下发一条 `*_done` 消息，
由消息泵 `_poll()` → `_handle()` 恢复按钮。问题出在两处：

### 缺陷 1｜转换线程没有异常兜底（致命）

四个 worker 中，**唯独 `_convert_worker` 没有 `except`**：

```python
# 修复前
def _convert_worker(self, names, selected, online_info, delete_after):
    pythoncom.CoInitialize()
    try:
        self._convert_impl(...)
    finally:
        pythoncom.CoUninitialize()      # 只有 finally，没有 except
```

一旦 `_convert_impl` 抛出**未被内部捕获**的异常（例如删除手机缓存阶段的
MTP 错误、`traceback.format_exc().splitlines()[-1]` 之类的边界崩溃），
线程会**静默退出**，`task_done` 永远发不出去 →
`self.busy` 永远停在 `True`、按钮永远灰。

对比：`_scan_worker` / `_online_worker` / `_delete_worker` **都有兜底**
（`scan_abort` / `online_done` / `delete_done`），只有转换这条缺了。

> 勾选「转换成功后删除手机缓存」会显著提高该路径的异常概率——
> 删除动作发生在 `_convert_impl` 内，MTP 操作更容易失败。

### 缺陷 2｜完成分支漏恢复按钮（高频但轻）

`task_done` 分支只恢复了 2 个按钮：

```python
# 修复前
elif kind == "task_done":
    _, ok, total, fails = msg
    self.busy = False
    self.btn_start.configure(state="normal")     # 只恢复 start
    self.btn_refresh.configure(state="normal")   # 只恢复 refresh
    # ← btn_online(联网核对)、btn_delete(删除手机缓存) 没有恢复！
```

而 `_set_busy(True)` 在开始时是**锁全部 4 个**的。`scan_abort` /
`scan_done` / `online_done` 三个分支同样漏了 `btn_delete`。

**表现**：转换一结束，「删除手机缓存」「联网核对」两个按钮就一直是灰的，
无法再次执行——正是用户描述的症状。

### 缺陷 3｜消息泵无异常隔离（隐患）

```python
# 修复前
def _poll(self):
    try:
        while True:
            self._handle(self.msgq.get_nowait())
    except queue.Empty:
        pass
    self.root.after(80, self._poll)      # 只有上一行没抛异常才会执行
```

`_handle` 只捕获 `queue.Empty`。若某条消息处理时抛异常（例如 `task_done`
里弹模态框失败、`_append` 的文本异常），异常穿出 `while`，
**`root.after` 不再注册 → 定时轮询永久停止** → 日志冻结、按钮再不更新，
只能重启程序。

---

## 三、修复内容

| # | 位置 | 改动 |
|---|---|---|
| 1 | `_convert_worker` | 加 `except BaseException`，任何异常都下发 `task_done` 解锁界面 + 记录日志 |
| 2 | `task_done` 分支 | 改为 `self._set_busy(False)`，恢复全部 4 个按钮 |
| 3 | `scan_abort` / `scan_done` / `online_done` | 同上，统一走 `_set_busy(False)` |
| 4 | `refresh()` / `online_check()` 起点 | 原为逐锁 3 个按钮，改为 `self._set_busy(True)`（顺带锁住 btn_delete，防扫描中误触删除） |
| 5 | `_poll()` | 单条消息处理包独立 `try/except`，坏消息不再终止轮询 |
| 6 | `_scan_worker` / `_delete_worker` | `except` 统一为 `BaseException`；`finally` 的 `CoUninitialize()` 加保护 |
| 7 | `_scan_worker` | 兜底补发 `scan_abort`（原只 log，不解锁） |

### 核心逻辑（新增的兜底）

```python
def _convert_worker(self, names, selected, online_info, delete_after):
    import pythoncom
    pythoncom.CoInitialize()
    try:
        self._convert_impl(names, selected, online_info, delete_after)
    except BaseException:
        try:
            self.msgq.put(("log", "[异常] " + traceback.format_exc()))
            self.msgq.put(("task_done", 0, len(names),
                           [("(内部错误)", "见日志")]))
        except Exception:
            pass
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
```

设计原则：**任何 worker 无论怎么退出，都必须下发一条 `*_done` 消息解锁界面。**

---

## 四、验证结果

`_test_gui_unlock.py`（用系统 Python 3.12 运行，8 项断言）：

```
=== 用例1 转换线程异常 -> 必须下发 task_done 解锁 ===
  [PASS] 异常时收到 task_done -> ['log', 'task_done']
  [PASS] task_done 参数正确
  [PASS] 异常时记录日志
=== 用例2 正常完成 -> 兜底不误触发 ===
  [PASS] 正常时兜底不下发消息 -> []
=== 用例3 消息泵遇到坏消息仍继续 ===
  [PASS] 三条消息都被处理(含坏消息后) -> ['ok1', 'BOOM', 'ok2']
  [PASS] 轮询继续注册(界面不冻结) -> [80]
=== 用例4 所有完成分支统一调用 _set_busy(False) ===
  [PASS] _set_busy(False) 恢复全部 4 个按钮
  [PASS] _set_busy(True) 锁定全部 4 个按钮

通过 8 项，失败 0 项 —— 全部通过 ✅
```

---

## 五、怎么让它生效

1. **必须重启界面**：`lanren_gui.py` 的改动对**已在运行**的窗口无效
   （Python 只在启动时加载代码）。
   → 关掉当前转换窗口，重新双击 `启动转换界面.bat`（或运行 GUI 的入口）。
2. 重启后，即使转换/删除过程中出异常，界面也会自动解锁，
   并在日志里留下 `[异常] ...` 的完整回溯，便于定位。
3. 若按钮仍异常，可查看日志是否存在 `[异常]` 行——现在异常**必然**被记录，
   不会再"无声无息地锁死"。

---

*本修复只改界面控制流，不触碰转码/解密/删除的任何业务逻辑。*
