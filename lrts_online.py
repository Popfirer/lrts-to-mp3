# -*- coding: utf-8 -*-
"""
懒人听书官网在线核对模块
========================
用途
----
通过懒人听书官网的公开页面/接口, 查询每本小说在官网的:
  * 总集数      sections  (官网该书的全部章节数)
  * 更新状态    state     (1=连载中, 2=已完结)
  * 最近更新    update
再与手机本地缓存的文件数对比, 用来判断:
  * 这本书在官网是否已完结
  * 本地是否已经把官网的全部集数都缓存/下载下来了

接口(全部是官网公开资源, 不需要登录、不需要 AppKey)
---------------------------------------------------
1) 搜索
   GET https://www.lrts.me/search/book/<url编码的关键词>
   服务端渲染的 HTML, 结果条目形如:
       <a class="book-item-name" href="/book/5622" ...>斗破苍穹|大灰狼演播</a>
   注意: 必须带 Referer: https://www.lrts.me/ , 否则可能返回空内容。

2) 书详情
   GET https://m.lrts.me/ajax/getBookDetail?bookId=<id>
   返回 JSON:
       {"apiStatus":0,"data":{"bookDetail":{
            "name":"终宋|精品三播|...", "author":"怪诞的表哥",
            "announcer":"火爆鸡翅", "sections":699,
            "state":1, "update":"2024-07-27", ...}}}
   其中 sections = 总集数; state: 1=连载 2=完结。

独立使用示例
------------
    import lrts_online as online
    info = online.query_novel("大明官|搞笑传奇|史诗上演|爆笑逆袭")
    print(info["total"], info["state_label"], info["matched"])
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ------------------------- 可调参数 -------------------------
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 15                    # 单次请求超时(秒)
RETRY = 2                       # 单次请求失败重试次数
STATE_LABEL = {1: "连载中", 2: "已完结"}
CACHE_TTL = 24 * 3600           # 磁盘缓存有效期(秒)
def _app_dir():
    """可写根目录: 打包成 exe 后 = exe 所在目录(缓存必须落在真实磁盘上,
    不能是 PyInstaller 的临时解包目录), 否则 = 本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


CACHE_FILE = os.path.join(_app_dir(), "online_cache.json")
SEARCH_RETRY = 3                # 搜索返回空时的重试次数
SEARCH_BACKOFF = 2.5            # 搜索重试基础等待(秒), 逐次翻倍
MAX_DETAILS = 2                 # 每本书最多取几个候选的详情
COOLDOWN = 20                   # 连续多次空结果后, 整体冷却(秒)
COOLDOWN_EVERY = 2              # 连续几次空结果后触发冷却
# -----------------------------------------------------------

_LOG = None                     # 可选日志回调
_empty_streak = 0               # 连续空结果计数(用于限流冷却)


def set_logger(fn):
    """注入日志回调(签名 fn(str)), 便于在界面里显示进度。"""
    global _LOG
    _LOG = fn


def log(msg):
    if _LOG:
        try:
            _LOG(str(msg))
        except Exception:
            pass


def _get(url, referer="https://www.lrts.me/"):
    """带 UA/Referer 的 GET, 失败重试, 返回解码后的文本; 抛异常表示彻底失败。"""
    last = None
    for attempt in range(RETRY + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_PC,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": referer,
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:          # noqa: BLE001
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise last


# ------------------------------------------------------------------
# 1. 搜索
# ------------------------------------------------------------------
def _parse_search(html):
    """从搜索结果页 HTML 解析 [{"bookId","name"}, ...]。"""
    items, seen = [], set()
    # 结果条目: class="book-item-name" ... href="/book/<id>" ...>名称</a>
    pats = [
        r'class="[^"]*book-item-name[^"]*"[^>]*href="/book/(\d+)"[^>]*>'
        r'\s*([^<]+?)\s*<',
        r'href="/book/(\d+)"[^>]*class="[^"]*book-item-name[^"]*"[^>]*>'
        r'\s*([^<]+?)\s*<',
        r'href="/book/(\d+)"[^>]*>\s*([^<]{1,60}?)\s*<',
    ]
    for pat in pats:
        for m in re.finditer(pat, html or ""):
            bid, name = m.group(1), m.group(2).strip()
            if bid not in seen and name:
                seen.add(bid)
                items.append({"bookId": bid, "name": name})
        if items:
            break
    return items


def search_book(keyword, limit=30):
    """按名称搜索官网书籍, 返回 [{"bookId": "5622", "name": "完整书名"}, ...]。

    搜索页可能因访问过快被临时限流而返回空; 这里做退避重试, 仍为空则返回 []。
    """
    if not keyword:
        return []
    url = "https://www.lrts.me/search/book/" + urllib.parse.quote(keyword.strip())
    for attempt in range(SEARCH_RETRY + 1):
        try:
            html = _get(url)
        except Exception as e:          # noqa: BLE001
            log("  [在线] 搜索「{}」请求失败: {!r}".format(keyword, e))
            html = ""
        items = _parse_search(html)
        if items:
            return items[:limit]
        if attempt < SEARCH_RETRY:      # 空结果 -> 等待后重试(疑似限流)
            wait = SEARCH_BACKOFF * (2 ** attempt)
            log("  [在线] 搜索「{}」无结果，{:.0f}s 后重试({}/{}) …".format(
                keyword, wait, attempt + 1, SEARCH_RETRY))
            time.sleep(wait)
    return []


# ------------------------------------------------------------------
# 2. 详情
# ------------------------------------------------------------------
def get_book_detail(book_id):
    """取单本书详情, 返回 dict; 失败返回 None。"""
    url = "https://m.lrts.me/ajax/getBookDetail?bookId=" + str(book_id)
    try:
        txt = _get(url)
        js = json.loads(txt)
    except Exception as e:              # noqa: BLE001
        log("  [在线] 取详情 {} 失败: {!r}".format(book_id, e))
        return None
    bd = (js.get("data") or {}).get("bookDetail")
    if not bd:
        return None
    return bd


# ------------------------------------------------------------------
# 3. 匹配与查询
# ------------------------------------------------------------------
_BASE_SEP = re.compile(r"[|｜丨/／、,，]")
_BRACKET_TAIL = re.compile(r"[\s]*[（(【\[][^（()）【\[\]】]*[)）】\]]\s*$")


def _base_name(s):
    """取完整名的“基础书名”部分: 按 | ｜ 丨 / 、 , 等分隔符取第一段。"""
    return _BASE_SEP.split((s or "").strip())[0].strip()


def _split_parts(s):
    """把 “书名|标签1｜标签2/标签3” 拆成词条列表。"""
    return [p.strip() for p in re.split(r"[|｜丨/／、,，\s]+", s or "") if p.strip()]


def _score(cand_name, want_full):
    """给候选书名打分; 返回 (分数, 是否精确匹配); None 表示与目标无关。"""
    cand_base = _base_name(cand_name)
    want_base = _base_name(want_full)
    if not want_base:
        return None
    exact = False
    if cand_base == want_base:
        base_score, exact = 100, True
    elif len(want_base) >= 4 and (cand_base.startswith(want_base)
                                 or cand_base.endswith(want_base)):
        base_score = 55                 # 官网基础名更长(带前后缀), 视为近似
    elif len(cand_base) >= 3 and cand_base in (want_full or ""):
        base_score = 35                 # 官网基础名是本地完整名的一段
    else:
        return None                     # 基础书名对不上, 直接淘汰
    # 副标题/标签重合度(忽略基础名本身)
    ct = set(_split_parts(cand_name)) - {cand_base}
    wt = set(_split_parts(want_full)) - {want_base}
    overlap = len(ct & wt)
    return base_score + overlap * 10, exact


def _search_with_fallback(name):
    """搜索书名; 无结果时去掉尾部括号说明再搜一次。"""
    cands = search_book(name)
    if cands:
        return cands
    alt = _BRACKET_TAIL.sub("", name).strip()
    if alt and alt != name:
        log("  [在线] 用简化名「{}」重试搜索 …".format(alt))
        return search_book(alt)
    return []


def query_novel(full_name, use_cache=True):
    """查询一本小说在官网的信息。

    full_name: 缓存文件夹解码后的完整名(可含 | 副标题), 如
               "大明官|搞笑传奇|史诗上演|爆笑逆袭"
    返回 dict:
        {
          "query": 原始名, "matched": 官网匹配到的完整书名, "bookId": ...,
          "total": 总集数, "state": 1/2, "state_label": "连载中"/"已完结",
          "finished": True/False, "author", "announcer", "update",
          "candidates": [ {bookId,name,total,state} ... ],
          "error": None 或 错误说明
        }
    """
    base = _base_name(full_name)
    res = {"query": full_name, "matched": None, "bookId": None,
           "total": None, "state": None, "state_label": None,
           "finished": None, "author": None, "announcer": None,
           "update": None, "candidates": [], "error": None,
           "confidence": None}

    cached = _cache_get(full_name) if use_cache else None
    if cached:
        return cached

    global _empty_streak
    cands = _search_with_fallback(base)
    if not cands:
        _empty_streak += 1
        if _empty_streak % COOLDOWN_EVERY == 0:
            log("  [在线] 连续 {} 次无结果，冷却 {:.0f}s 以避免被限流 …".format(
                _empty_streak, COOLDOWN))
            time.sleep(COOLDOWN)
        res["error"] = "官网未返回结果（可能未收录，或访问过快被限流）"
        res["_transient"] = True         # 临时失败: 不写缓存, 下次重试
        return res
    _empty_streak = 0

    scored = []
    for c in cands:
        s = _score(c["name"], full_name)
        if s is not None:
            scored.append((s[0], s[1], c))
    if not scored:
        res["error"] = "官网结果与「{}」不匹配".format(base)
        res["candidates"] = cands[:5]
        _cache_put(full_name, res)
        return res

    # 精确匹配优先, 其次按分数
    scored.sort(key=lambda x: (-x[0], 0 if x[1] else 1))
    # 详情: 只取若干个最优候选, 减少请求量
    picked = None
    for sc, exact, c in scored[:MAX_DETAILS]:
        bd = get_book_detail(c["bookId"])
        if not bd:
            continue
        row = {"bookId": c["bookId"], "name": bd.get("name") or c["name"],
               "total": bd.get("sections"), "state": bd.get("state"),
               "author": bd.get("author"), "announcer": bd.get("announcer"),
               "update": bd.get("update"), "score": sc, "exact": exact}
        res["candidates"].append(row)
        if picked is None:
            picked = row
    if picked is None:
        res["error"] = "取详情失败"
        _cache_put(full_name, res)
        return res
    res.update({
        "matched": picked["name"], "bookId": picked["bookId"],
        "total": picked["total"], "state": picked["state"],
        "state_label": STATE_LABEL.get(picked["state"], "未知"),
        "finished": (picked["state"] == 2),
        "author": picked["author"], "announcer": picked["announcer"],
        "update": picked["update"],
        "confidence": "exact" if picked["exact"] else "approx",
    })
    _cache_put(full_name, res)
    return res


# ------------------------------------------------------------------
# 4. 与本地缓存对比
# ------------------------------------------------------------------
def compare(local_count, info):
    """根据本地缓存文件数与官网信息, 返回一句话结论 + 状态标签。

    返回 (状态标签, 结论文本)
    """
    if not info or info.get("error") or info.get("total") is None:
        if info and info.get("_transient"):
            return "未查得", "本次未查到官网数据（可能未收录或被限流），可稍后重试"
        return "—", (info or {}).get("error") or "未查询"
    total = info["total"]
    state = info["state"]
    local = local_count if (local_count or 0) > 0 else 0
    near = "≈" if info.get("confidence") == "approx" else ""
    if state == 2:                                    # 已完结
        if local >= total:
            return near + "已完结", "官网共 {} 集, 已完结; 本地 {} 集, 已下全 ✓".format(
                total, local)
        return near + "完结·缺{}".format(total - local), \
            "官网共 {} 集, 已完结; 本地仅 {} 集, 缺 {} 集".format(
                total, local, total - local)
    # 连载中
    return near + "连载中", "官网已更新 {} 集, 仍在连载; 本地 {} 集".format(total, local)


# ------------------------------------------------------------------
# 5. 批量
# ------------------------------------------------------------------
def probe_all(items, on_progress=None, on_result=None, pause=1.0):
    """批量查询。

    items: [ (full_name, local_count), ... ]
    返回 {full_name: info}
    """
    out = {}
    n = len(items)
    for i, (full_name, local_count) in enumerate(items, 1):
        if on_progress:
            on_progress(i, n, full_name)
        info = query_novel(full_name)
        out[full_name] = info
        if on_result:
            on_result(full_name, info, local_count)
        # 临时失败(疑似限流)时多歇一会儿
        time.sleep(pause * (3 if info.get("_transient") else 1))
    return out


# ------------------------------------------------------------------
# 磁盘缓存(避免重复联网)
# ------------------------------------------------------------------
def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                   # noqa: BLE001
        return {}


def _save_cache(data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:                   # noqa: BLE001
        pass


def _cache_get(key):
    d = _load_cache()
    rec = d.get(key)
    if rec and time.time() - rec.get("_ts", 0) < CACHE_TTL:
        return rec.get("data")
    return None


def _cache_put(key, info):
    if info.get("_transient"):          # 临时失败不入缓存, 便于下次重试
        return
    d = _load_cache()
    d[key] = {"_ts": time.time(), "data": info}
    # 控制体量: 最多保留 500 条
    if len(d) > 500:
        for k in sorted(d, key=lambda x: d[x].get("_ts", 0))[:len(d) - 500]:
            d.pop(k, None)
    _save_cache(d)


def clear_cache():
    _save_cache({})


if __name__ == "__main__":
    import sys
    names = sys.argv[1:] or ["大明官", "终宋", "斗破苍穹"]
    for nm in names:
        inf = query_novel(nm)
        print("=" * 60)
        print("查询:", nm)
        print("匹配:", inf.get("matched"), "| bookId:", inf.get("bookId"))
        print("总集数:", inf.get("total"), "| 状态:", inf.get("state_label"),
              "| 更新:", inf.get("update"))
        print("作者:", inf.get("author"), "| 播音:", inf.get("announcer"))
        print("候选:", [(c.get("name"), c.get("total"), c.get("state"))
                        for c in inf.get("candidates", [])])
        print("结论:", compare(0, inf)[1])
