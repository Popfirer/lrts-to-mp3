# -*- coding: utf-8 -*-
"""
懒人听书缓存批量转换 —— 图形界面版
====================================
左右两个分区分别列出手机「内部存储」和「存储卡」上的懒人听书缓存小说,
点击行即可勾选, 点「开始转换」后自动完成三步:
    ① 批量并行复制缓存到本地 -> ② 调用转换工具解密 -> ③ 并行转码为 MP3
输出就保存在软件目录下的 workspace\\<书名>\\ 里。

联网核对后还会显示每本书的作者与主播; 勾选后可一键删除手机端的缓存文件夹。

启动: 双击「启动转换界面.bat」，或直接运行打包好的「LRTS to mp3.exe」
      （打包命令：打包exe.bat / 打包exe.py）
"""
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

if getattr(sys, "frozen", False):
    # 打包成 exe: 代码与依赖都在包内, 只需知道 exe 所在目录
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

import lanren_convert as core      # noqa: E402  复用已验证的核心逻辑

CHECKED = "☑"
UNCHECKED = "☐"
FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_LOG = ("Consolas", 9)
FONT_SMALL = ("Microsoft YaHei UI", 9)


class App:
    def __init__(self, root):
        self.root = root
        self.msgq = queue.Queue()
        self.books = {v: {} for v in core.VOLUMES}    # vol -> {书名: {...}}
        self.trees = {}
        self.frames = {}
        self.sel_var = {}
        self.busy = False
        self.ctl = None            # 当前转换任务的暂停/取消控制器

        self.output_var = tk.StringVar(value=core.OUTPUT_ROOT)
        self.fmt_var = tk.StringVar(value="MP3（转码）")
        self.status_var = tk.StringVar(value="就绪")
        self.phone_var = tk.StringVar(value="手机: 未连接")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._setup_style()
        self._build()

        core.LOG_SINK = self.post_log
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._poll)
        self.refresh()

    # ================= 界面构建 =================
    def _setup_style(self):
        st = ttk.Style()
        for theme in ("vista", "clam", "default"):
            try:
                st.theme_use(theme)
                break
            except tk.TclError:
                continue
        st.configure("Treeview", rowheight=26, font=FONT)
        st.configure("Treeview.Heading", font=FONT_BOLD)
        st.configure("TButton", font=FONT)
        st.configure("TLabel", font=FONT)
        st.configure("TLabelframe.Label", font=FONT_BOLD)

    def _build(self):
        root = self.root
        root.title("LRTS to mp3 —— 懒人听书缓存批量转换")
        root.geometry("1240x820")
        root.minsize(1060, 680)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # --- 标题 ---
        head = ttk.Frame(root, padding=(16, 14, 16, 2))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="懒人听书缓存批量转换", font=FONT_TITLE).grid(
            row=0, column=0, sticky="w")
        ttk.Label(head, textvariable=self.phone_var, font=FONT_SMALL,
                  foreground="#7a7a7a").grid(row=0, column=2, sticky="e")

        # --- 输出目录栏 ---
        bar = ttk.Frame(root, padding=(16, 8, 16, 8))
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="输出目录：").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.output_var).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bar, text="浏览…", width=8, command=self.choose_output).grid(
            row=0, column=2)
        ttk.Button(bar, text="打开目录", width=9, command=self.open_output).grid(
            row=0, column=3, padx=(6, 0))
        ttk.Label(bar, text="（默认=软件目录\\workspace，每本书一个子文件夹）",
                  font=FONT_SMALL, foreground="#7a7a7a").grid(
            row=0, column=4, sticky="w", padx=(10, 0))

        bar2 = ttk.Frame(bar)
        bar2.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.btn_refresh = ttk.Button(bar2, text="刷新列表", width=9,
                                      command=self.refresh)
        self.btn_refresh.grid(row=0, column=0)
        self.btn_online = ttk.Button(bar2, text="联网核对", width=9,
                                     command=self.online_check)
        self.btn_online.grid(row=0, column=1, padx=(6, 0))
        self.btn_delete = ttk.Button(bar2, text="删除手机缓存", width=13,
                                     command=self.delete_phones)
        self.btn_delete.grid(row=0, column=2, padx=(6, 0))
        ttk.Button(bar2, text="使用说明", width=9,
                   command=self.open_help).grid(row=0, column=3, padx=(6, 0))

        # --- 两个存储分区 ---
        lists = ttk.Frame(root, padding=(16, 0, 16, 6))
        lists.grid(row=2, column=0, sticky="nsew")
        lists.columnconfigure(0, weight=1, uniform="col")
        lists.columnconfigure(1, weight=1, uniform="col")
        lists.rowconfigure(0, weight=1)
        for col, vol in enumerate(core.VOLUMES):
            self._build_volume(lists, vol, col)

        # --- 集数范围(按小说指定只下载第几集~第几集) ---
        rngf = ttk.LabelFrame(
            root, text="集数范围（每本小说可单独指定要下载的集）", padding=(12, 6))
        rngf.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 6))
        ttk.Label(rngf, text="第").pack(side="left")
        self.rng_lo_var = tk.StringVar()
        self.rng_hi_var = tk.StringVar()
        ttk.Entry(rngf, textvariable=self.rng_lo_var, width=7,
                  justify="center").pack(side="left", padx=2)
        ttk.Label(rngf, text="集 ～ 第").pack(side="left")
        ttk.Entry(rngf, textvariable=self.rng_hi_var, width=7,
                  justify="center").pack(side="left", padx=2)
        ttk.Label(rngf, text="集").pack(side="left")
        self.btn_rng_apply = ttk.Button(rngf, text="应用到勾选本", width=13,
                                        command=self.apply_range)
        self.btn_rng_apply.pack(side="left", padx=(12, 0))
        ttk.Button(rngf, text="清除范围", width=10,
                   command=self.clear_range).pack(side="left", padx=(6, 0))
        ttk.Label(rngf,
                  text="（留空=全部；只填一端表示「≥」或「≤」；"
                       "也可双击列表某一行、或用该分区下方的「设范围…」单独设置）",
                  font=FONT_SMALL, foreground="#7a7a7a").pack(
            side="left", padx=(12, 0))

        # --- 操作栏 ---
        act = ttk.Frame(root, padding=(16, 6, 16, 6))
        act.grid(row=4, column=0, sticky="ew")
        act.columnconfigure(2, weight=1)
        actf = ttk.Frame(act)
        actf.grid(row=0, column=0)
        self.btn_start = ttk.Button(actf, text="开始转换", width=12,
                                    command=self.start)
        self.btn_start.pack(side="left")
        self.btn_pause = ttk.Button(actf, text="暂停转码", width=10,
                                    command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(6, 0))
        self.btn_cancel = ttk.Button(actf, text="取消转换", width=10,
                                     command=self.cancel_convert,
                                     state="disabled")
        self.btn_cancel.pack(side="left", padx=(6, 0))
        fmtf = ttk.Frame(act)
        fmtf.grid(row=0, column=1, padx=(12, 0))
        ttk.Label(fmtf, text="输出格式:").pack(side="left")
        ttk.Combobox(fmtf, textvariable=self.fmt_var,
                     values=("MP3（转码）", "MP4（原始）"),
                     state="readonly", width=11).pack(side="left", padx=4)
        self.auto_online = tk.BooleanVar(value=True)
        ttk.Checkbutton(fmtf, text="扫描后自动联网核对集数",
                        variable=self.auto_online).pack(side="left", padx=(12, 0))
        self.auto_delete = tk.BooleanVar(value=False)
        ttk.Checkbutton(fmtf, text="转换成功后删除手机缓存",
                        variable=self.auto_delete,
                        command=self._warn_auto_delete).pack(side="left", padx=(12, 0))
        ttk.Progressbar(act, variable=self.progress_var, maximum=100).grid(
            row=0, column=2, sticky="ew", padx=12)
        ttk.Label(act, textvariable=self.status_var, width=30,
                  font=FONT_SMALL).grid(row=0, column=3, sticky="w")

        # --- 日志 ---
        logf = ttk.LabelFrame(root, text="运行日志", padding=6)
        logf.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 14))
        logf.columnconfigure(0, weight=1)
        self.txt = tk.Text(logf, height=12, font=FONT_LOG, wrap="none",
                           state="disabled", background="#1b1c1f",
                           foreground="#d4d4d4", insertbackground="#d4d4d4",
                           relief="flat")
        self.txt.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(logf, orient="vertical", command=self.txt.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=vsb.set)
        self.txt.tag_configure("err", foreground="#ff7b72")
        self.txt.tag_configure("ok", foreground="#7ee787")
        self.txt.tag_configure("hl", foreground="#79c0ff")

    def _build_volume(self, parent, vol, col):
        lf = ttk.LabelFrame(parent, text="{} (加载中…)".format(vol), padding=8)
        lf.grid(row=0, column=col, sticky="nsew",
                padx=(0, 8) if col == 0 else (8, 0))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.frames[vol] = lf

        tree = ttk.Treeview(lf, columns=("sel", "name", "cnt", "ol", "st",
                                         "rng", "au", "sp"),
                            show="headings", selectmode="browse")
        tree.heading("sel", text="")
        tree.heading("name", text="小说名")
        tree.heading("cnt", text="本地")
        tree.heading("ol", text="官网")
        tree.heading("st", text="状态")
        tree.heading("rng", text="范围")
        tree.heading("au", text="作者")
        tree.heading("sp", text="主播")
        tree.column("sel", width=28, anchor="center", stretch=False)
        tree.column("name", width=160, anchor="w")
        tree.column("cnt", width=40, anchor="center", stretch=False)
        tree.column("ol", width=40, anchor="center", stretch=False)
        tree.column("st", width=68, anchor="center", stretch=False)
        tree.column("rng", width=62, anchor="center", stretch=False)
        tree.column("au", width=78, anchor="w", stretch=False)
        tree.column("sp", width=72, anchor="w", stretch=False)
        # 行颜色: 已完结下全=绿 / 完结但缺集=蓝 / 连载中=橙 / 未匹配=灰
        tree.tag_configure("fin", foreground="#1a7f37")
        tree.tag_configure("part", foreground="#0969da")
        tree.tag_configure("ser", foreground="#b25000")
        tree.tag_configure("unk", foreground="#8a8a8a")
        tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(lf, orient="vertical", command=tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vsb.set)
        tree.bind("<Button-1>", lambda e, v=vol: self._on_click(e, v))
        tree.bind("<space>", lambda e, v=vol: self._toggle_selected(v))
        tree.bind("<Double-1>", lambda e, v=vol: self._on_double(e, v))
        self.trees[vol] = tree

        bbar = ttk.Frame(lf)
        bbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bbar, text="全选", width=6,
                   command=lambda: self.set_all(vol, True)).pack(side="left")
        ttk.Button(bbar, text="全不选", width=7,
                   command=lambda: self.set_all(vol, False)).pack(side="left", padx=4)
        ttk.Button(bbar, text="反选", width=6,
                   command=lambda: self.invert(vol)).pack(side="left")
        ttk.Button(bbar, text="设范围…", width=8,
                   command=lambda: self._range_for_selection(vol)).pack(
            side="left", padx=(8, 0))
        self.sel_var[vol] = tk.StringVar(value="已选 0")
        ttk.Label(bbar, textvariable=self.sel_var[vol], font=FONT_SMALL,
                  foreground="#7a7a7a").pack(side="right")

    # ================= 勾选交互 =================
    def _set_busy(self, flag):
        """统一切换按钮可用状态。"""
        state = "disabled" if flag else "normal"
        for b in (self.btn_start, self.btn_refresh, self.btn_online,
                  self.btn_delete, getattr(self, "btn_rng_apply", None)):
            try:
                b.configure(state=state)
            except (tk.TclError, AttributeError):
                pass

    def _on_click(self, event, vol):
        tree = self.trees[vol]
        if tree.identify_region(event.x, event.y) != "cell":
            return
        iid = tree.identify_row(event.y)
        if iid:
            self.toggle(vol, iid)

    def _toggle_selected(self, vol):
        for iid in self.trees[vol].selection():
            self.toggle(vol, iid)

    def toggle(self, vol, name):
        info = self.books[vol].get(name)
        if info is None:
            return
        info["checked"] = not info["checked"]
        self._refresh_row(vol, name)
        self._update_sel(vol)

    def set_all(self, vol, value):
        for name, info in self.books[vol].items():
            info["checked"] = value
            self._refresh_row(vol, name)
        self._update_sel(vol)

    def invert(self, vol):
        for name, info in self.books[vol].items():
            info["checked"] = not info["checked"]
            self._refresh_row(vol, name)
        self._update_sel(vol)

    def _refresh_row(self, vol, name):
        info = self.books[vol][name]
        cnt = info["count"]
        cnt_txt = "…" if cnt is None else ("?" if cnt < 0 else str(cnt))
        mark = CHECKED if info["checked"] else UNCHECKED
        rng_txt = self._range_text(info.get("range"))

        ol_txt, st_txt, au_txt, sp_txt, tag = "—", "", "", "", ()
        inf = info.get("online")
        if inf is not None:
            detail = inf.get("info") or {}
            local = cnt if isinstance(cnt, int) and cnt > 0 else 0
            au_txt = (detail.get("author") or "")[:12]
            sp_txt = (detail.get("announcer") or "")[:12]
            if detail.get("total") is None:
                ol_txt, st_txt, tag = "未匹配", "—", ("unk",)
            else:
                label, _ = self._compare(local, detail)
                ol_txt = str(detail["total"])
                st_txt = label
                if detail.get("finished"):
                    tag = ("fin",) if local >= detail["total"] else ("part",)
                else:
                    tag = ("ser",)
        try:
            self.trees[vol].item(
                name, values=(mark, name, cnt_txt, ol_txt, st_txt, rng_txt,
                              au_txt, sp_txt),
                tags=tag)
        except tk.TclError:
            pass

    # ================= 集数范围 =================
    @staticmethod
    def _range_text(rng):
        """区间 -> 列表里显示的短文本。"""
        try:
            norm = core.norm_ep_range(rng)
        except ValueError:
            return "全部"
        if norm is None:
            return "全部"
        lo, hi = norm
        if lo is None:
            return "≤{}".format(hi)
        if hi is None:
            return "≥{}".format(lo)
        return "{}-{}".format(lo, hi)

    def _read_range_fields(self):
        """读工具条两个输入框 -> (起,止) / None(不限) / "ERR"(非法)。"""
        lo_s = self.rng_lo_var.get().strip()
        hi_s = self.rng_hi_var.get().strip()
        if not lo_s and not hi_s:
            return None
        try:
            lo = int(lo_s) if lo_s else None
            hi = int(hi_s) if hi_s else None
        except ValueError:
            return "ERR"
        try:
            return core.norm_ep_range((lo, hi))
        except ValueError:
            return "ERR"

    def apply_range(self):
        """把工具条的集数范围应用到所有勾选的小说。"""
        if self.busy:
            return
        rng = self._read_range_fields()
        if rng == "ERR":
            messagebox.showerror(
                "范围无效",
                "集号请填写正整数，例如 起始 700、结束 717。\n"
                "留空表示该端不限（两端都留空 = 全部）。")
            return
        targets = [(vol, name) for vol in core.VOLUMES
                   for name, info in self.books[vol].items() if info["checked"]]
        if not targets:
            messagebox.showinfo("提示", "请先在列表里勾选要设置范围的小说"
                                        "（点击列表中的行即可勾选）")
            return
        for vol, name in targets:
            self.books[vol][name]["range"] = rng
            self._refresh_row(vol, name)
        txt = self._range_text(rng)
        self._append("已把集数范围「{}」应用到 {} 本勾选小说".format(
            txt, len(targets)), "hl")
        self.status_var.set("集数范围已应用：{}（{} 本）".format(txt, len(targets)))

    def clear_range(self):
        """清除范围：有勾选就只清勾选的，否则清全部。"""
        if self.busy:
            return
        picked = [(vol, name) for vol in core.VOLUMES
                  for name, info in self.books[vol].items() if info["checked"]]
        targets = picked or [(vol, name) for vol in core.VOLUMES
                             for name in self.books[vol]]
        if not targets:
            messagebox.showinfo("提示", "列表为空，请先点「刷新列表」。")
            return
        for vol, name in targets:
            self.books[vol][name]["range"] = None
            self._refresh_row(vol, name)
        self.rng_lo_var.set("")
        self.rng_hi_var.set("")
        self._append("已清除 {} 本小说的集数范围（恢复为全部）".format(
            len(targets)), "hl")

    def _range_for_selection(self, vol):
        """给当前选中的那一行设置范围。"""
        sel = self.trees[vol].selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表里点选一本小说")
            return
        self._range_dialog(vol, sel[0])

    def _on_double(self, event, vol):
        tree = self.trees[vol]
        iid = tree.identify_row(event.y)
        if iid and iid in self.books[vol]:
            self._range_dialog(vol, iid)

    def _range_dialog(self, vol, name):
        """单本小说的集数范围设置对话框。"""
        info = self.books[vol].get(name)
        if info is None:
            return
        cur = info.get("range") or (None, None)
        dlg = tk.Toplevel(self.root)
        dlg.title("集数范围")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="《{}》".format(name),
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frm, text="只下载/转换第").grid(row=1, column=0, sticky="w",
                                                 pady=(10, 0))
        v1 = tk.StringVar(value="" if cur[0] is None else str(cur[0]))
        v2 = tk.StringVar(value="" if cur[1] is None else str(cur[1]))
        e1 = ttk.Entry(frm, textvariable=v1, width=8, justify="center")
        e1.grid(row=1, column=1, sticky="w", padx=3, pady=(10, 0))
        ttk.Label(frm, text="集 ～ 第").grid(row=1, column=2, sticky="w",
                                            pady=(10, 0))
        ttk.Entry(frm, textvariable=v2, width=8,
                  justify="center").grid(row=1, column=3, sticky="w", padx=3,
                                         pady=(10, 0))
        ttk.Label(frm, text="集　（两端留空 = 全部）", font=FONT_SMALL,
                  foreground="#7a7a7a").grid(row=2, column=0, columnspan=4,
                                             sticky="w", pady=(6, 0))
        foot = ttk.Frame(frm)
        foot.grid(row=3, column=0, columnspan=4, sticky="e", pady=(14, 0))

        def commit(rng):
            self.books[vol][name]["range"] = rng
            self._refresh_row(vol, name)
            self._append("《{}》集数范围：{}".format(
                name, self._range_text(rng)), "hl")
            dlg.destroy()

        def ok():
            s1, s2 = v1.get().strip(), v2.get().strip()
            if not s1 and not s2:
                commit(None)
                return
            try:
                rng = core.norm_ep_range((int(s1) if s1 else None,
                                          int(s2) if s2 else None))
            except ValueError:
                messagebox.showerror("范围无效", "集号请填写正整数，"
                                                 "或留空表示不限。", parent=dlg)
                return
            commit(rng)

        ttk.Button(foot, text="确定", width=8, command=ok).pack(side="left")
        ttk.Button(foot, text="清空(全部)", width=11,
                   command=lambda: commit(None)).pack(side="left", padx=(6, 0))
        ttk.Button(foot, text="取消", width=8,
                   command=dlg.destroy).pack(side="left", padx=(6, 0))
        dlg.bind("<Return>", lambda _e: ok())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        e1.focus_set()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - dlg.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - dlg.winfo_height()) // 3)
        dlg.geometry("+{}+{}".format(x, y))

    @staticmethod
    def _compare(local, info):
        if core.online is not None:
            return core.online.compare(local, info)
        return "—", ""

    def _update_sel(self, vol):
        total = len(self.books[vol])
        n = sum(1 for x in self.books[vol].values() if x["checked"])
        self.frames[vol].configure(
            text="{} ({} 本，已选 {})".format(vol, total, n))
        self.sel_var[vol].set("已选 {}".format(n))

    # ================= 扫描 =================
    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self._set_busy(True)
        for vol in core.VOLUMES:
            tree = self.trees[vol]
            children = tree.get_children()
            if children:
                tree.delete(*children)
            self.books[vol] = {}
            self.frames[vol].configure(text="{} (加载中…)".format(vol))
            self.sel_var[vol].set("已选 0")
        self.phone_var.set("手机: 正在连接 …")
        self.status_var.set("正在扫描手机缓存 …")
        self._clear_log()
        self.post_log("正在连接手机 {} …".format(core.PHONE_NAME))
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        import pythoncom
        pythoncom.CoInitialize()          # 子线程访问 COM(MTP) 必需
        try:
            self._scan_impl()
        except Exception:
            self.msgq.put(("log", "[异常] " + traceback.format_exc()))
            self.msgq.put(("status", "扫描失败"))
            self.msgq.put(("scan_abort",))
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _scan_impl(self):
        try:
            _, ph = core.connect_phone()
            if ph is None:
                self.msgq.put(("phone", "手机: 未检测到 {}".format(core.PHONE_NAME)))
                self.msgq.put(("log", "[错误] 未检测到手机「{}」。请用 USB 线连接，"
                                      "在手机上选择「传输文件」模式后点「刷新列表」。"
                              .format(core.PHONE_NAME)))
                self.msgq.put(("status", "手机未连接"))
                self.msgq.put(("scan_abort",))
                return
            self.msgq.put(("phone", "手机: {} 已连接 ✓".format(core.PHONE_NAME)))

            volumes = {}
            for vol in core.VOLUMES:
                self.msgq.put(("status", "正在读取{} …".format(vol)))
                items = core.scan_volume(ph, vol)
                volumes[vol] = items
                for name, item in items:
                    full = core.decode_name_full(item.Name)
                    self.msgq.put(("book", vol, name, full, item))
                self.msgq.put(("log", "{}: 发现 {} 本小说缓存".format(vol, len(items))))

            for vol in core.VOLUMES:
                items = volumes[vol]
                for i, (name, item) in enumerate(items, 1):
                    self.msgq.put(("status", "统计集数 {}/{}（{}）".format(
                        i, len(items), vol)))
                    n = core.count_cached_files(item)
                    self.msgq.put(("count", vol, name, n))

            self.msgq.put(("log", "扫描完成。点击列表中的行即可勾选／取消。"))
            self.msgq.put(("scan_done",))
        except Exception:
            self.msgq.put(("log", "[异常] " + traceback.format_exc()))
            self.msgq.put(("status", "扫描失败"))
            self.msgq.put(("scan_abort",))

    # ================= 联网核对 =================
    def online_check(self):
        """联网查询懒人听书官网, 核对每本书的总集数与连载/完结状态。

        若列表中已有勾选的书, 只核对勾选的; 否则核对全部。
        """
        if self.busy:
            return
        if core.online is None:
            messagebox.showerror("错误", "缺少 lrts_online 模块，无法联网核对。")
            return
        checked_any = any(info["checked"] for vol in core.VOLUMES
                          for info in self.books[vol].values())
        targets = []
        for vol in core.VOLUMES:
            for name, info in self.books[vol].items():
                if checked_any and not info["checked"]:
                    continue
                targets.append((vol, name, info.get("full") or name))
        if not targets:
            messagebox.showinfo("提示", "列表为空，请先点「刷新列表」扫描手机缓存。")
            return
        self.busy = True
        self._set_busy(True)
        self._append("")
        self._append("=" * 62)
        self._append("联网核对懒人听书官网集数（共 {} 本{}）…".format(
            len(targets), "，仅已勾选" if checked_any else ""), "hl")
        self._append("  颜色说明：绿=已完结且下全 / 蓝=已完结但缺集 / "
                     "橙=连载中 / 灰=未匹配")
        threading.Thread(target=self._online_worker, args=(targets,),
                         daemon=True).start()

    def _online_worker(self, targets):
        total = len(targets)
        hit = fin = ser = 0
        core.online.set_logger(lambda s: self.msgq.put(("log", s)))
        try:
            for i, (vol, name, full) in enumerate(targets, 1):
                self.msgq.put(("status", "联网核对 {}/{}：{}".format(i, total, name)))
                inf = core.online.query_novel(full)
                self.msgq.put(("online", vol, name, inf))
                if inf.get("matched"):
                    hit += 1
                    if inf.get("finished"):
                        fin += 1
                    else:
                        ser += 1
                time.sleep(1.0 if not inf.get("_transient") else 3.0)
        except Exception:
            self.msgq.put(("log", "[异常] " + traceback.format_exc()))
        self.msgq.put(("online_done", total, hit, fin, ser))

    # ================= 手机端删除 =================
    def _warn_auto_delete(self):
        """勾选「转换成功后删除手机缓存」时的风险确认。"""
        if not self.auto_delete.get():
            return
        if not messagebox.askyesno(
                "风险确认",
                "⚠️ 你勾选了「转换成功后删除手机缓存」。\n\n"
                "转换成功后，程序会把这本小说在手机上的缓存文件夹"
                "永久删除（手机回收站不适用，无法恢复）。\n\n"
                "确认开启此项功能吗？", icon="warning"):
            self.auto_delete.set(False)

    def delete_phones(self):
        """删除已勾选小说在手机上的缓存文件夹(需二次确认)。"""
        if self.busy:
            return
        targets, lines = [], []
        for vol in core.VOLUMES:
            for name, info in self.books[vol].items():
                if not info["checked"]:
                    continue
                targets.append((vol, name, info))
                n = info.get("count")
                lines.append("{}　[{}]　{} 个缓存文件".format(
                    name, vol, "?" if not n or n < 0 else n))
        if not targets:
            messagebox.showinfo("提示", "请先勾选要删除手机缓存的小说\n"
                                        "（点击列表中的行即可勾选）")
            return
        names = "、".join(sorted({n for _v, n, _i in targets}))
        if not messagebox.askyesno(
                "⚠️ 确认删除手机端缓存",
                "即将从手机上【永久删除】以下 {} 个缓存文件夹：\n\n{}\n\n"
                "涉及小说：{}\n\n"
                "⚠️ 删除后手机上的对应音频将不复存在，且无法恢复"
                "（本地已转换好的 MP3 不受影响）。\n\n"
                "确定要继续吗？".format(len(targets), "\n".join(lines), names),
                icon="warning"):
            return
        if not messagebox.askyesno(
                "再次确认",
                "这是最后确认：真的要删除手机上这 {} 个缓存文件夹吗？\n\n"
                "《{}》".format(len(targets), names), icon="warning"):
            return
        self.busy = True
        self._set_busy(True)
        self._append("")
        self._append("=" * 62)
        self._append("开始删除手机端缓存（{} 个文件夹）…".format(len(targets)), "hl")
        threading.Thread(target=self._delete_worker, args=(targets,),
                         daemon=True).start()

    def _delete_worker(self, targets):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            self._delete_impl(targets)
        except BaseException:
            self.msgq.put(("log", "[异常] " + traceback.format_exc()))
            self.msgq.put(("status", "删除失败"))
            self.msgq.put(("delete_done", 0, len(targets)))
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _delete_impl(self, targets):
        ok = 0
        for i, (vol, name, info) in enumerate(targets, 1):
            self.msgq.put(("status", "删除 {}/{}：{}".format(
                i, len(targets), name)))
            self.msgq.put(("log", ""))
            self.msgq.put(("log", "({}/{}) 《{}》　[{}]".format(
                i, len(targets), name, vol)))
            for it in info.get("items", []):
                done, err = core.delete_phone_folder(it)
                if done:
                    ok += 1
                    self.msgq.put(("log", "    已删除缓存文件夹 ✓"))
                else:
                    self.msgq.put(("log", "    [失败] " + str(err)))
        self.msgq.put(("delete_done", ok, len(targets)))

    # ================= 转换 =================
    def start(self):
        if self.busy:
            return
        selected = {}
        for vol in core.VOLUMES:
            for name, info in self.books[vol].items():
                if info["checked"]:
                    for it in info["items"]:
                        selected.setdefault(name, []).append((vol, it))
        if not selected:
            messagebox.showinfo("提示", "请先勾选要转换的小说\n（点击列表中的行即可勾选）")
            return
        out = self.output_var.get().strip()
        if not out:
            messagebox.showwarning("提示", "请先设置输出目录")
            return
        if not core.converter_available():
            messagebox.showerror(
                "错误", "找不到转换工具（软件目录 tools、内嵌资源与桌面均无）：\n"
                + core.EXE_NAME)
            return
        try:
            os.makedirs(out, exist_ok=True)
            os.makedirs(core.WORKSPACE, exist_ok=True)
        except OSError as e:
            messagebox.showerror("错误", "无法创建目录：\n{}".format(e))
            return
        core.OUTPUT_ROOT = out
        core.OUTPUT_FORMAT = ("mp3"
                              if self.fmt_var.get().startswith("MP3")
                              else "auto")
        if core.OUTPUT_FORMAT == "mp3" and core.find_ffmpeg() is None:
            core.OUTPUT_FORMAT = "auto"     # 真正退回原始输出, 与提示保持一致
            messagebox.showwarning(
                "提示",
                "未找到 ffmpeg，无法转码为真 MP3。\n"
                "本次将保持转换工具的原始输出（MP4）格式。\n\n"
                "如需真 MP3：把 ffmpeg.exe 放到软件目录（或软件目录\\tools\\）后重试。")

        names = sorted(selected.keys())
        # 每本小说的集数范围（有些书的 range 存在两个卷的记录里，取任一份）
        ranges = {}
        for vol in core.VOLUMES:
            for nm, inf in self.books[vol].items():
                if inf.get("range") and nm not in ranges:
                    ranges[nm] = tuple(inf["range"])
        # 供 ID3 标签使用的作者/主播(来自联网核对结果)
        online_info = {}
        for vol in core.VOLUMES:
            for nm, inf in self.books[vol].items():
                detail = (inf.get("online") or {}).get("info") or {}
                if detail.get("total") is not None:
                    online_info[nm] = detail
        delete_after = bool(self.auto_delete.get())
        self.busy = True
        self._set_busy(True)
        self.ctl = core.TranscodeControl()          # 本轮任务的暂停/取消控制器
        self.btn_pause.configure(text="暂停转码", state="normal")
        self.btn_cancel.configure(state="normal")
        self.progress_var.set(0)
        self._append("")
        self._append("=" * 62)
        self._append("开始转换 {} 本，输出目录：{}".format(len(names), out), "hl")
        if delete_after:
            self._append("⚠️ 已开启「转换成功后删除手机缓存」，转换完成即删除手机端文件夹",
                         "err")
        for vol in core.VOLUMES:
            picked = [n for n in names if any(v == vol for v, _ in selected[n])]
            if picked:
                self._append("  [{}] {} 本：{}".format(vol, len(picked),
                                                       "、".join(picked)))
        if ranges:
            self._append("集数范围（只复制/转换这些区间内的集）：", "hl")
            for nm in names:
                if nm in ranges:
                    self._append("  《{}》 {}".format(nm,
                                                    self._range_text(ranges[nm])))
        else:
            self._append("集数范围：全部（未限制）")
        threading.Thread(target=self._convert_worker,
                         args=(names, selected, online_info, delete_after,
                               self.ctl, ranges),
                         daemon=True).start()

    # ================= 暂停 / 取消 =================
    def toggle_pause(self):
        """暂停 / 继续当前转换（在「文件之间」生效，不打断已在转的那几集）。"""
        ctl = self.ctl
        if ctl is None:
            return
        if ctl.paused:
            ctl.resume()
            self.btn_pause.configure(text="暂停转码")
            self.status_var.set("已继续转码")
            self._append("  ▶ 已继续转码")
        else:
            ctl.pause()
            self.btn_pause.configure(text="继续转码")
            self.status_var.set("已暂停（在途任务收尾后停止启动新任务）")
            self._append("  ⏸ 已暂停转码：在途的几集转完后停下，"
                         "点「继续转码」恢复")

    def cancel_convert(self):
        """取消当前转换：终止在途 ffmpeg、清理半成品、保留已完成的成果。"""
        ctl = self.ctl
        if ctl is None or ctl.cancelled:
            return
        if not messagebox.askyesno(
                "确认取消转换",
                "确定要取消当前转换吗？\n\n"
                "• 已转好的 MP3 会全部保留\n"
                "• 正在转码的几集会被中断（半成品清理，源文件不受影响）\n"
                "• 若勾选了「转换成功后删除手机缓存」，取消后【不会】执行删除\n\n"
                "之后可直接再次点「开始转换」续跑。",
                icon="warning"):
            return
        ctl.cancel()
        self.btn_cancel.configure(state="disabled")
        self.btn_pause.configure(state="disabled", text="暂停转码")
        self.status_var.set("正在取消…")
        self._append("  ⏹ 正在取消：终止在途 ffmpeg 并清理半成品…", "err")

    def _convert_worker(self, names, selected, online_info, delete_after,
                        control=None, ranges=None):
        import pythoncom
        pythoncom.CoInitialize()          # 子线程访问 COM(MTP) 必需
        try:
            self._convert_impl(names, selected, online_info, delete_after,
                               control, ranges)
        except BaseException:
            # 兜底：任何未预期异常（含删除手机缓存阶段的 MTP 错误）都必须
            # 下发 task_done 解锁界面；否则线程静默退出，按钮会永久变灰，
            # 只能重启程序。_scan/_online/_delete 三个 worker 都有兜底，
            # 唯独这里原来没有，是「转换后按键被锁死」的直接原因。
            try:
                self.msgq.put(("log", "[异常] " + traceback.format_exc()))
                self.msgq.put(("task_done", 0, len(names),
                               [("(内部错误)", "见日志")], False))
            except Exception:
                pass
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _convert_impl(self, names, selected, online_info, delete_after,
                      control=None, ranges=None):
        total = len(names)
        ranges = ranges or {}
        ok = 0
        fails = []
        stopped = False
        for i, name in enumerate(names, 1):
            if control is not None and control.cancelled:
                stopped = True
                self.msgq.put(("log", "  [取消] 剩余 {} 本未处理".format(
                    total - i + 1)))
                break
            vols = "+".join(v for v, _ in selected[name])
            self.msgq.put(("progress", (i - 1) * 100.0 / total,
                           "({}/{}) 处理中：{}".format(i, total, name)))
            self.msgq.put(("log", ""))
            self.msgq.put(("log", "({}/{}) 《{}》　来源：{}".format(
                i, total, name, vols)))
            t0 = time.time()
            try:
                stat = core.process_novel(
                    name, selected[name],
                    info=online_info.get(name),
                    delete_after=delete_after,
                    ep_range=ranges.get(name),
                    on_progress=lambda stage, d, t, nm: self.msgq.put(
                        ("status", "{} {}/{}：{}".format(stage, d, t, nm))),
                    control=control)
            except Exception:
                stat = {"name": name, "copied": 0, "converted": 0, "mp3": 0,
                        "kept": 0, "dup": 0, "out_total": 0, "deleted": 0,
                        "delete_fail": 0,
                        "error": traceback.format_exc().splitlines()[-1]}
            stat["seconds"] = int(time.time() - t0)
            if stat.get("error") == "已取消":
                stopped = True
                self.msgq.put(("log", "    [取消] 《{}》 已停止（耗时 {} 秒）".format(
                    name, stat["seconds"])))
                continue
            if stat.get("error"):
                fails.append((name, stat["error"]))
                self.msgq.put(("log", "    [失败] {}（耗时 {} 秒）".format(
                    stat["error"], stat["seconds"])))
            else:
                ok += 1
                self.msgq.put(("log",
                               "    [完成] 复制 {} 个 / 解密 {} 集 / "
                               "MP3 {} 个（去重 {}） / 目录共 {} 个音频，耗时 {} 秒"
                               .format(stat.get("copied", 0),
                                       stat.get("converted", 0),
                                       stat.get("mp3", 0) + stat.get("kept", 0),
                                       stat.get("dup", 0),
                                       stat.get("out_total", 0),
                                       stat["seconds"])))
                if stat.get("ep_range"):
                    self.msgq.put(("log", "    集数范围：{}（范围外跳过 {} 个）".format(
                        core.ep_range_text(stat["ep_range"]),
                        stat.get("range_skipped", 0))))
                if stat.get("deleted") or stat.get("delete_fail"):
                    self.msgq.put(("log", "    手机端删除：成功 {} 个，失败 {} 个"
                                          .format(stat.get("deleted", 0),
                                                  stat.get("delete_fail", 0))))
            self.msgq.put(("progress", i * 100.0 / total,
                           "已完成 {}/{}".format(i, total)))
        if stopped:
            self.msgq.put(("log", "  [取消] 转换已由用户取消，未完成的文件保持原样，"
                                  "可直接再次点「开始转换」续跑。"))
        self.msgq.put(("task_done", ok, total, fails, stopped))

    # ================= 消息泵 =================
    def post_log(self, text):
        """供核心模块(可能在子线程)调用的日志入口。"""
        self.msgq.put(("log", text))

    def _poll(self):
        try:
            while True:
                msg = self.msgq.get_nowait()
                try:
                    self._handle(msg)
                except Exception:
                    # 单条消息处理失败绝不能终止轮询循环, 否则整个界面永久
                    # 锁死(日志不刷新、按钮不再恢复), 只能重启程序。
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "log":
            self._append(msg[1])
        elif kind == "phone":
            self.phone_var.set(msg[1])
        elif kind == "status":
            self.status_var.set(msg[1])
        elif kind == "book":
            _, vol, name, full, item = msg
            info = self.books[vol].get(name)
            if info is None:
                # 同名书可能有多个缓存分段文件夹, 合并为一条
                self.books[vol][name] = {"items": [item], "checked": False,
                                         "count": None, "full": full,
                                         "online": None, "range": None}
                self.trees[vol].insert("", "end", iid=name,
                                       values=(UNCHECKED, name, "…", "—", "",
                                               "全部", "", ""))
                self._update_sel(vol)
            else:
                info["items"].append(item)
                if full and len(full) > len(info.get("full") or ""):
                    info["full"] = full
        elif kind == "count":
            _, vol, name, n = msg
            info = self.books[vol].get(name)
            if info:
                info["count"] = (max(info["count"], 0) + max(n, 0)
                                 if info["count"] is not None else n)
                self._refresh_row(vol, name)
        elif kind == "scan_abort":
            self.busy = False
            self._set_busy(False)
        elif kind == "scan_done":
            self.busy = False
            self._set_busy(False)
            self.status_var.set("扫描完成，请勾选要转换的小说")
            if self.auto_online.get():
                self.root.after(200, self.online_check)
        elif kind == "online":
            _, vol, name, inf = msg
            info = self.books[vol].get(name)
            if info:
                info["online"] = {"info": inf}
                self._refresh_row(vol, name)
            local = info["count"] if info and info.get("count") else 0
            label, concl = self._compare(local, inf)
            if inf.get("matched"):
                self._append("  《{}》　{}　官网 {} 集 {}｜本地 {} 集 → {}".format(
                    name, inf.get("matched"), inf.get("total"),
                    inf.get("state_label"), local, label),
                    "ok" if inf.get("finished") else None)
                if inf.get("author") or inf.get("announcer"):
                    self._append("        作者：{}　主播：{}".format(
                        inf.get("author") or "—", inf.get("announcer") or "—"),
                        "hl")
            else:
                self._append("  《{}》　{}".format(
                    name, inf.get("error") or "未匹配到官网条目"), "err")
        elif kind == "online_done":
            _, total, hit, fin, ser = msg
            self.busy = False
            self._set_busy(False)
            self._append("联网核对完成：共 {} 本，匹配 {} 本"
                         "（已完结 {} 本 / 连载中 {} 本）".format(
                             total, hit, fin, ser), "hl")
            self.status_var.set("联网核对完成：匹配 {}/{}".format(hit, total))
        elif kind == "delete_done":
            _, ok, total = msg
            self.busy = False
            self._set_busy(False)
            self._append("删除完成：成功 {} / {} 个缓存文件夹".format(ok, total),
                         "ok" if ok == total else "err")
            self.status_var.set("删除完成：{}/{}".format(ok, total))
            messagebox.showinfo(
                "删除完成",
                "成功删除 {}/{} 个手机端缓存文件夹。\n"
                "点「刷新列表」可重新读取手机上剩余的缓存。".format(ok, total))
        elif kind == "progress":
            self.progress_var.set(msg[1])
            self.status_var.set(msg[2])
        elif kind == "task_done":
            _, ok, total, fails, stopped = msg
            self.busy = False
            self._set_busy(False)
            self.ctl = None
            self.btn_pause.configure(text="暂停转码", state="disabled")
            self.btn_cancel.configure(state="disabled")
            summary = "完成 {}/{} 本".format(ok, total)
            if stopped:
                summary += "（已取消）"
            if fails:
                summary += "，失败 {} 本".format(len(fails))
            self.status_var.set(summary)
            self._append("=" * 62)
            self._append(("已取消：" if stopped else "全部完成：") + summary,
                         "ok" if (not fails and not stopped) else "err")
            for n, e in fails:
                self._append("  [失败] 《{}》 {}".format(n, e), "err")
            if fails:
                messagebox.showwarning("转换结束", summary + "\n详情见日志。")
            elif stopped:
                messagebox.showinfo(
                    "已取消",
                    summary + "\n已完成的文件保留，"
                    "可直接再次点「开始转换」续跑。")
            else:
                messagebox.showinfo(
                    "转换结束",
                    summary + "\n输出目录：" + self.output_var.get())

    # ================= 日志与杂项 =================
    def _append(self, text, tag=None):
        self.txt.configure(state="normal")
        lines = str(text).splitlines() or [""]
        for line in lines:
            t = tag
            if t is None:
                if "[失败]" in line or "[错误]" in line or "[异常]" in line:
                    t = "err"
                elif "[完成]" in line:
                    t = "ok"
            self.txt.insert("end", line + "\n", t or "")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def choose_output(self):
        cur = self.output_var.get().strip()
        initial = cur if os.path.isdir(cur) else "F:\\"
        d = filedialog.askdirectory(title="选择输出目录", initialdir=initial)
        if d:
            self.output_var.set(os.path.normpath(d))

    def open_output(self):
        p = self.output_var.get().strip()
        if p and os.path.isdir(p):
            os.startfile(p)
        else:
            messagebox.showinfo("提示", "输出目录还不存在")

    def open_help(self):
        if os.path.exists(core.HELP_FILE):
            os.startfile(core.HELP_FILE)
        else:
            messagebox.showinfo("提示", "说明文件不存在：\n" + core.HELP_FILE)

    def _on_close(self):
        if self.busy and not messagebox.askyesno(
                "确认退出", "正在转换中，确定要退出吗？\n\n"
                "退出会中止正在进行的任务（已完成的文件会保留）。"):
            return
        if self.ctl is not None:
            self.ctl.cancel()      # 终止在途 ffmpeg，避免留下孤儿进程
            time.sleep(0.3)
        self.root.destroy()


def enable_dpi_awareness():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _writable_of(d):
    """目录是否可写(真建一个临时文件试一下)。"""
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "._wtest")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(p)
        return True
    except Exception:
        return False


def selftest():
    """环境自检: 报告写在软件目录的 _selftest.txt（打包后排查问题专用）。

    运行:  LRTS to mp3.exe --selftest
    """
    import datetime
    import platform

    lines = []

    def add(k, v):
        lines.append("{:<24} {}".format(k, v))

    lines.append("LRTS to mp3 —— 环境自检")
    lines.append("时间: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("=" * 62)
    add("打包运行(frozen)", getattr(sys, "frozen", False))
    add("exe / 入口", sys.executable)
    add("sys._MEIPASS", getattr(sys, "_MEIPASS", "(无, 非打包)"))
    add("Python", sys.version.split()[0] + "  " + platform.machine())
    lines.append("-" * 62)
    add("软件目录 APP_DIR", core.APP_DIR)
    add("资源目录 RES_DIR", core.RES_DIR)
    add("工作区 WORKSPACE", core.WORKSPACE)
    add("输出根 OUTPUT_ROOT", core.OUTPUT_ROOT)
    add("使用说明 HELP_FILE", core.HELP_FILE)
    add("  ↑ 存在?", os.path.exists(core.HELP_FILE))
    add("图标 ICON_FILE", core.ICON_FILE)
    add("  ↑ 存在?", bool(core.ICON_FILE and os.path.exists(core.ICON_FILE)))
    lines.append("-" * 62)
    add("转换工具可用?", core.converter_available())
    for label, p in (("  tools/", core.CONVERTER_EXE),
                     ("  内嵌资源", core.CONVERTER_EXE_BUNDLED),
                     ("  桌面副本", core.CONVERTER_EXE_FALLBACK)):
        add(label, "{} {}".format("√" if os.path.exists(p) else "×", p))
    try:
        add("ensure_converter()", core.ensure_converter() or "× 未找到")
    except Exception as e:                                   # noqa: BLE001
        add("ensure_converter()", "异常 {!r}".format(e))
    lines.append("-" * 62)
    add("ffmpeg", core.find_ffmpeg() or "× 未找到")
    for p in core.FFMPEG_CANDIDATES:
        add("  候选", "{} {}".format("√" if os.path.exists(p) else "×", p))
    lines.append("-" * 62)
    try:
        import lrts_online as _ol
        add("在线核对模块", "OK  " + _ol.__file__)
        add("  缓存文件", _ol.CACHE_FILE)
    except Exception as e:                                   # noqa: BLE001
        add("在线核对模块", "导入失败 {!r}".format(e))
    try:
        import win32com.client                                   # noqa: F401
        add("pywin32(手机MTP)", "OK")
    except Exception as e:                                   # noqa: BLE001
        add("pywin32(手机MTP)", "导入失败 {!r}".format(e))
    add("软件目录可写?", _writable_of(core.APP_DIR))
    lines.append("=" * 62)
    lines.append("以上路径若都指向 exe 所在目录, 说明打包正常。")

    text = "\n".join(lines)
    out = os.path.join(core.APP_DIR, "_selftest.txt")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:                                        # noqa: BLE001
        pass
    print(text, flush=True)
    return out


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    try:                                   # 窗口/任务栏图标(打包后为内嵌图标)
        if core.ICON_FILE:
            root.iconbitmap(default=core.ICON_FILE)
    except Exception:
        pass
    try:
        app = App(root)          # noqa: F841
    except Exception:
        messagebox.showerror("启动失败", traceback.format_exc())
        raise
    # 窗口居中
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry("{}x{}+{}+{}".format(w, h, (sw - w) // 2, (sh - h) // 3))
    root.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:          # 环境自检(打包后排查用)
        selftest()
    else:
        main()
