# -*- coding: utf-8 -*-
"""自动采集样本语料：全市场、不限板块、不限数量 —— 给「逻辑自己」用的那份数据。

为什么要有这个脚本
------------------
`ai_stock_terminal.py` 是 Streamlit 脚本，只有人打开网页**并点按钮**才会采集。
而样本语料的作用是「让这套逻辑不断被验证」，它必须是**无人值守、每天自动跑**的。
所以采集被拆出来做成这个无头脚本：不需要 streamlit，既能在本机定时跑，
也能丢进 GitHub Actions 每天收盘后跑一次。

★ 它**不复制**任何指标算法：用 AST 从 ai_stock_terminal.py 里抽出真实的
  `_calculate_band_metrics / _band_score / _band_status / band_sample_build ...` 来执行。
  所以网页端与本脚本产出的样本**逐字段同构** —— 不存在「两套口径」的问题。
  （这也是为什么本文件里看不到任何指标公式。）

用法
----
    # 1) 前瞻采集：把「今天」全市场每只股票的那一根K线采成样本（每天收盘后跑）
    python sample_harvest.py --forward

    # 2) 前瞻采集 + 加密落盘（GitHub Actions 用；需要环境变量 BAND_KEY）
    python sample_harvest.py --forward --store enc --file band_samples.enc

    # 3) 历史回填：全市场逐日重放，攒第一批样本（单进程约 40~90 分钟）
    python sample_harvest.py --backfill 450 --shard 0/8     # 分片并行
    python sample_harvest.py --merge                        # 全部跑完后合并
    python sample_harvest.py --stats                        # 看报表

    # 4) 把云端产出的加密语料拉回本机明文库
    python sample_harvest.py --pull band_samples.enc

设计红线（改之前先读）
----------------------
· **样本与用户名单完全分开**：这里只写 band_samples.*，绝不碰 band_memory.json。
· **回填 ≠ 前瞻**：两者在报表里永不合并，回填只用来提假设。
· **全收 + 可交易标记**：不按板块/数量预筛，但每条都记下成交额/价格/ST 代理。
· **确定性**：对照日抽样走 md5（`_sample_hash_hit`），绝不用 random，保证可复现。
· **失败必须可见**：取数失败逐代码留痕并计入 report，不许静默跳过。
"""
import argparse
import ast
import base64
import gzip
import json
import os
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(HERE, "ai_stock_terminal.py")

PLAIN_FILE = os.path.join(HERE, "band_samples.jsonl.gz")     # 本地明文（已 gitignore）
ENC_FILE = os.path.join(HERE, "band_samples.enc")            # 加密语料（可进仓库/缓存）
CACHE_DIR = os.environ.get("SAMPLE_CACHE_DIR") or os.path.join(HERE, ".sample_cache")
PART_FMT = os.path.join(CACHE_DIR, "_part_{i}.jsonl.gz")
FORWARD_FILE = os.path.join(HERE, "band_samples_forward.enc")  # 前瞻语料快照（发布给网页端读）

# 前瞻语料快照的条数上限。★ 为什么只导出 forward、还要设上限：
#   ① forward 才是「真实判断、能当成绩用」的那一类，backfill 自带前视/生存者偏差，
#      只能提假设 —— 网页端统计前瞻那几栏只需要它；
#   ② 快照每天都会被发布一次，条数越多传输越慢，而超过一定量之后统计口径不再变化
#      （band_sample_lift 要的是分桶样本量，不是全部历史）。超限时保留**最新**的那批。
FORWARD_EXPORT_MAX = 20000

# ---------- 要从主应用里抽出来执行的符号（与 _debug_probe 的回归测试同一套集合）----------
NET_FUNCS = {
    "_log", "_http_get_json", "_http_get_text", "_quote_prefix", "_get_code",
    "_safe_float", "_safe_float_or_none", "_is_st_or_risk", "_diff_to_list",
    "fetch_market_page", "_normalize_kline_rows", "_fetch_kline_qq", "_fetch_kline_sina",
    "_get_daily_history", "_load_json",
    # ★ `_http_get_json/_http_get_text` 内部会调 `_http_session()`；不抽它出来，
    #   这两个函数在**本文件（Actions 夜跑）**里每次请求都会 NameError，
    #   被 `_http_session` 的兜底吞掉 → 连接复用静默失效 + 每次请求刷一行 stderr。
    #   表现为"采集慢且日志刷屏"，极难定位。新增网络内部依赖时务必同步加这里。
    "_http_session",
}
NET_CONSTS = {"_REQUEST_HEADERS", "_EM_HOSTS", "_HTTP_HEADERS", "_QQ_APP_HOSTS",
              "KLINE_CACHE_FILE", "BASE_DIR", "CONFIG_FILE", "_ENC_FIELD", "_ENC_VERSION",
              "_TLS_LOCAL"}
METRIC_FUNCS = {"_calculate_band_metrics", "_band_score", "_band_status", "_band_evaluate"}

SAMPLE_FUNCS = {
    "_sample_key", "_sample_hash_hit", "_sample_tier", "_sample_prefilter", "_sample_tradable",
    "_sample_bench_asof", "_sample_change_pct", "_sample_meta_add", "_sample_pending_outcome",
    "band_evaluate_asof", "band_sample_build", "_sample_fetch_kline", "band_samples_harvest",
    # 并发改造后，单只股票的处理拆到了这个函数里
    "_sample_harvest_one",
    "band_samples_harvest_forward", "load_band_samples", "save_band_samples",
    "band_samples_merge", "band_samples_range", "band_sample_summary", "band_sample_lift",
    "_sample_usable", "band_samples_pending_keys", "band_samples_refresh_pending",
}
OUTCOME_FUNCS = {
    "_bench_history", "_get_index_history", "_bench_above_ma20", "_band_entry_context",
    "band_outcome_compute", "_bucket_defs",
}
CRYPTO_FUNCS = {"band_crypto_key", "band_crypto_enabled", "_fernet",
                "band_encrypt_obj", "band_decrypt_obj", "load_config"}

SAMPLE_CONSTS = {
    "SAMPLE_FILE", "SAMPLE_CTRL_EVERY", "SAMPLE_MAX_ROWS", "SAMPLE_MIN_BARS",
    "SAMPLE_MIN_AMOUNT_YI", "SAMPLE_MIN_PRICE", "SAMPLE_EVENT_DEDUP", "SAMPLE_VOL_UNIT",
    "SAMPLE_SOURCES", "SAMPLE_SOURCE_LABEL", "SAMPLE_TIER_LABEL", "SAMPLE_MAX_STALE_DAYS",
    "SAMPLE_FETCH_WORKERS",
}
REVIEW_CONSTS = {
    "REVIEW_RULE_VERSION", "REVIEW_BENCH_SYMBOL", "REVIEW_TP_PCT", "REVIEW_MAX_HOLD_DAYS",
    "REVIEW_MFE_GOOD", "REVIEW_MIN_SAMPLE", "CLOSE_REASON_LABEL", "VERDICT_LABEL",
    "BAND_STATUS_LEVEL", "_BENCH_CACHE", "EXCLUDE_PREFIXES",
    # ★ daily_digest.py 的日报要用这两个集合来数「启动 / 结束预警」。
    #   必须从主应用抽，不能在日报脚本里另抄一份 —— 状态名一改就会静默数成 0。
    "BAND_ALERT_STATUSES", "BAND_ENTRY_STATUSES",
}

WANT_FUNCS = NET_FUNCS | METRIC_FUNCS | SAMPLE_FUNCS | OUTCOME_FUNCS | CRYPTO_FUNCS
WANT_CONSTS = NET_CONSTS | SAMPLE_CONSTS | REVIEW_CONSTS


# ============================ 一、把主应用当"库"来加载 ============================

def _cn_now():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8)))


def load_app_namespace(app_path=APP_PATH, base_dir=None):
    """AST 抽取主应用的模块级常量与函数，返回可执行的命名空间。

    ★ 两条必须遵守的规则（都踩过坑）：
      1. **常量必须先注入再注入函数**：Python 的默认参数在 def 时求值，
         `def f(x=SAMPLE_MIN_BARS)` 若常量还没进命名空间就直接 NameError。
      2. **模块级别名要预置**（`import time as _time_module` 之类），
         否则调用链深处抛 NameError，而 `errs` 列表会把它吞成"取不到数据"。

    ★ now_cn / now_cn_str 用**北京时区**覆盖：GitHub Actions 的 runner 是 UTC，
      用本机时间算日期会让"今天"错一天（整个前瞻采集的 date 字段就全歪了）。
    """
    src = open(app_path, encoding="utf-8").read()
    tree = ast.parse(src)
    st_stub = types.SimpleNamespace(
        cache_data=lambda *a, **k: (lambda f: f),
        cache_resource=lambda *a, **k: (lambda f: f),
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
        info=lambda *a, **k: None, caption=lambda *a, **k: None,
        progress=lambda *a, **k: types.SimpleNamespace(progress=lambda *a, **k: None,
                                                      empty=lambda *a, **k: None),
        secrets=types.SimpleNamespace(get=lambda *a, **k: ""),
    )
    here = base_dir or HERE
    ns = {
        "st": st_stub, "pd": pd, "np": np, "requests": requests,
        "re": __import__("re"), "os": os, "json": json, "sys": sys,
        "base64": base64, "math": __import__("math"),
        "threading": __import__("threading"),      # _http_session 的线程本地 Session 要用
        "traceback": __import__("traceback"), "time": __import__("time"),
        "_time_module": __import__("time"), "datetime": __import__("datetime"),
        "ThreadPoolExecutor": ThreadPoolExecutor, "as_completed": as_completed,
        "hashlib": __import__("hashlib"), "gzip": gzip,
        "BASE_DIR": here,
        "KLINE_CACHE_FILE": os.path.join(here, "kline_cache.json"),
        "BAND_MEMORY_FILE": os.path.join(here, "band_memory.json"),
        "BAND_BATCHES_FILE": os.path.join(here, "band_batches.json"),
        # ★ 兜底预置：`_TLS_LOCAL` 是 `_http_session` 的线程本地连接池。
        #   正常路径下由 NET_CONSTS 抽取真实定义覆盖；这里再给一份，
        #   是为了在**抽取失败**（改名 / 改成 `x: T = ...` 注解式赋值，ast.Assign 抓不到）时
        #   仍能工作，而不是退化成"每次请求 NameError + 刷日志"。
        "_TLS_LOCAL": __import__("threading").local(),
        "now_cn": _cn_now,
        "now_cn_str": lambda fmt='%Y-%m-%d %H:%M:%S': _cn_now().strftime(fmt),
    }
    for node in tree.body:                                   # 规则 1
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in WANT_CONSTS:
                    try:
                        exec(compile(ast.Module([node], []), app_path, "exec"), ns)
                    except Exception as e:
                        sys.stderr.write(f"[harvest] 常量注入失败 {t.id}: {e}\n")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in WANT_FUNCS:
            exec(compile(ast.Module([node], []), app_path, "exec"), ns)
    missing = sorted(WANT_FUNCS - set(ns.keys()))
    if missing:
        sys.stderr.write(f"[harvest] !! 以下函数未找到（主应用改名了？）：{missing}\n")
    return ns, missing


# ============================ 二、语料库读写（明文 / 加密）============================

class StoreError(RuntimeError):
    pass


def _read_plain(ns, path):
    if not os.path.exists(path):
        return {}
    return ns["load_band_samples"](path)


def _unwrap_to_plain(ns, src, dst):
    """把加密语料解成明文 JSONL.gz。**解不开就抛错，绝不当作空数据。**"""
    try:
        raw = open(src, encoding="utf-8").read()
        data = json.loads(raw)
    except Exception as e:
        raise StoreError(f"加密语料不是合法 JSON：{e}") from e
    ok, obj, err = ns["band_decrypt_obj"](data)
    if not ok:
        raise StoreError(f"解密失败：{err}")
    b64 = (obj or {}).get("gz_b64")
    if not b64:
        raise StoreError("解密成功但内容里没有 gz_b64 字段（文件类型不对？）")
    try:
        blob = base64.b64decode(b64)
    except Exception as e:
        raise StoreError(f"base64 解码失败：{e}") from e
    with open(dst, "wb") as f:
        f.write(blob)
    return dst


def _wrap_plain(ns, src, dst):
    """把明文 JSONL.gz 包成加密语料。未配 BAND_KEY 时**直接报错**（fail-closed）。"""
    if not ns["band_crypto_enabled"]():
        raise StoreError("要求加密存储，但本端没有 BAND_KEY —— 拒绝落盘为明文")
    blob = open(src, "rb").read()
    env = ns["band_encrypt_obj"]({"v": 1, "kind": "band_samples",
                                  "gz_b64": base64.b64encode(blob).decode("ascii")})
    token = env.get(ns["_ENC_FIELD"]) if isinstance(env, dict) else None
    if not token:
        raise StoreError("band_encrypt_obj 没返回密文（不该发生）")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(env, ensure_ascii=False))
    os.replace(tmp, dst)
    return dst


def load_store(ns, path, enc=False):
    """读语料库 → {key: row}。enc=True 表示 path 是密文。

    ★ 文件**不存在**＝首次运行，返回空库（合法状态，**不是**错误）；
      文件**存在但读不出/解不开**＝照旧抛 StoreError，绝不静默当空数据（fail-closed 不变）。
    踩过的坑（run 35440036252）：云端首次运行时没有历史语料，这里直接抛
    「加密语料不是合法 JSON：[Errno 2] No such file or directory: 'band_samples.enc'」，
    把刚采完的 3711 条全丢了、退出码 3。**首次运行必须能正常起步。**
    """
    if not os.path.exists(path):
        return {}
    if enc:
        tmp = os.path.join(CACHE_DIR, "_unwrap_work.jsonl.gz")
        os.makedirs(CACHE_DIR, exist_ok=True)
        _unwrap_to_plain(ns, path, tmp)
        try:
            return _read_plain(ns, tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return _read_plain(ns, path)


def save_store(ns, rows, path, enc=False):
    """写语料库。返回写入行数。

    ★ 一律先写临时明文文件再搬过去：
      ① 明文路径直接落盘时用 os.replace，保证不会留下半截文件；
      ② 加密路径则在临时文件上加密，然后把明文删掉 —— 明文一刻都不能留在最终位置上。
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if enc:
        work = os.path.join(CACHE_DIR, "_wrap_work.jsonl.gz")
        os.makedirs(CACHE_DIR, exist_ok=True)
        n = ns["save_band_samples"](rows, work)
        if not n:
            raise StoreError("写临时语料失败，已中止（不会覆盖已有文件）")
        _wrap_plain(ns, work, path)
        try:
            os.remove(work)
        except OSError:
            pass
        return n
    return ns["save_band_samples"](rows, path)


# ============================ 三、全市场清单 ============================

def market_universe(ns, verbose=True):
    """全市场清单（代码 → 名称）。沪深靠东财分页，北交所单独补一段。

    ★ 与选股的**唯一也是关键**的差别：这里不套 EXCLUDE_PREFIXES。
      采样一旦按板块预筛，就等于把创业板/科创板/北交所的规律提前排除在样本之外，
      而"这部分是给机器验证逻辑用的"，预筛等于自己蒙自己的眼睛。
    """
    codes = {}
    for pn in range(1, 41):
        page = ns["fetch_market_page"](pn)
        if not page:
            break
        for s in page:
            c = str(s.get("f12") or "").zfill(6)
            if len(c) == 6 and c.isdigit():
                codes[c] = str(s.get("f14") or "")
        if verbose:
            print(f"  沪深清单：第 {pn} 页累计 {len(codes)} 只", flush=True)
    try:      # 北交所
        r = ns["requests"].get(f"{ns['_EM_HOSTS'][0]}/api/qt/clist/get",
                               params={"pn": "1", "pz": "1000", "po": "1", "np": "1",
                                       "fltt": "2", "invt": "2", "fid": "f62",
                                       "fs": "m:0+t:81+s:2048",
                                       "fields": "f12,f14,f2,f20,f62"},
                               timeout=10, headers=ns["_REQUEST_HEADERS"])
        for s in ns["_diff_to_list"]((r.json().get("data") or {}).get("diff")):
            c = str(s.get("f12") or "").zfill(6)
            if len(c) == 6 and c.isdigit():
                codes[c] = str(s.get("f14") or "")
        if verbose:
            print(f"  含北交所合计 {len(codes)} 只", flush=True)
    except Exception as e:
        print(f"  北交所清单失败（不影响主流程）：{e}", flush=True)
    return codes


def fetch_bench(ns, verbose=True):
    """基准（沪深300）日线。取不到必须**显式告知** —— 没有它就永远算不出超额收益。"""
    sym = ns["REVIEW_BENCH_SYMBOL"]
    for fn in (ns["_fetch_kline_qq"], ns["_fetch_kline_sina"]):
        try:
            df, src, _e = fn(sym, limit=700)
            if df is not None and len(df) >= 60:
                if verbose:
                    print(f"  基准 {sym} 取自 {src}，{len(df)} 根", flush=True)
                return df
        except Exception as e:
            print(f"  基准取数失败：{e}", flush=True)
    print("  !! 基准（沪深300）取不到：本次样本的超额收益会全是 None，"
          "只能看绝对收益，先解决网络再重跑", flush=True)
    return None


def _cached_fetch(ns, use_cache=True):
    """按代码缓存日线（含成交量单位）→ 可中断续跑。

    ★ 单位必须一起缓存：腾讯按「手」（×100），新浪按「股」（×1）。
      丢了单位，成交额会差 100 倍，「可交易」标记整片失真。
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    def fetch(code, limit=700):
        p = os.path.join(CACHE_DIR, f"{code}.jsonl.gz")
        if use_cache and os.path.exists(p):
            try:
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    obj = json.loads(f.read())
                df = pd.DataFrame(obj["rows"],
                                  columns=["Date", "Open", "Close", "High", "Low", "Volume"])
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                return df, float(obj.get("unit", 1.0))
            except Exception as e:
                ns["_log"](f"harvest/cache_read/{code}", e)
        df, unit = ns["_sample_fetch_kline"](code, limit)
        if df is not None and use_cache:
            try:
                with gzip.open(p, "wt", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "unit": unit,
                        "rows": [[str(r["Date"])[:10], float(r["Open"]), float(r["Close"]),
                                  float(r["High"]), float(r["Low"]), float(r["Volume"])]
                                 for _, r in df.iterrows()],
                    }, ensure_ascii=False))
            except Exception as e:
                ns["_log"](f"harvest/cache_write/{code}", e)
        return df, unit

    return fetch


# ============================ 四、采集主流程 ============================

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def harvest_codes(ns, codes, names, source, lookback_days, fetch, bench, workers,
                  progress_tag=""):
    """把代码分块：并行取数 → 串行评估。返回 (rows, report)。

    ★ 为什么要分块：一次把全市场日线全缓存在内存里约 150MB+，
      分块后内存占用只跟块大小有关，且并行度不受影响。
    ★ 评估必须串行：_calculate_band_metrics 是 CPU 密集的 pandas 运算，
      在 Python GIL 下多线程并不会更快，反而让取数被挤慢。
    """
    rows, report = [], {"codes": 0, "fetched": 0, "failed": 0, "stale": 0, "kept": 0,
                        "evaluated": 0, "fail_examples": []}
    t0 = time.time()
    done = 0
    total = len(codes)
    for blk in _chunks(list(codes), max(1, workers * 50)):
        cache = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(fetch, c, 700): c for c in blk}
            for fu in as_completed(futs):
                c = futs[fu]
                try:
                    cache[c] = fu.result()
                except Exception as e:
                    ns["_log"](f"harvest/prefetch/{c}", e)
                    cache[c] = (None, 1.0)

        def _get(code, limit=700, _c=cache):
            return _c.get(code, (None, 1.0))

        r, rep = ns["band_samples_harvest"](
            blk, source=source, lookback_days=lookback_days, fetch=_get,
            bench_df=bench, names=names)
        for k in ("codes", "fetched", "failed", "stale", "kept", "evaluated"):
            report[k] += rep.get(k, 0)
        report["fail_examples"] = (report["fail_examples"]
                                   + list(rep.get("fail_examples") or []))[:8]
        rows.extend(r)
        done += len(blk)
        el = time.time() - t0
        eta = (el / done * (total - done)) / 60 if done else 0
        print(f"  [{done}/{total}]{progress_tag} 已用 {el/60:.1f} 分钟 "
              f"累计样本 {len(rows)} 条 预计还需 {eta:.1f} 分钟", flush=True)
    return rows, report


def refresh_pending(ns, rows_map, fetch, bench, max_items=4000):
    """把「跟踪中」的样本重算一遍。返回更新条数；出错不阻断采集。"""
    try:
        _, n = ns["band_samples_refresh_pending"](rows_map, fetch=fetch,
                                                  bench_df=bench, max_items=max_items)
        return n
    except Exception as e:
        ns["_log"]("harvest/refresh_pending", e)
        print(f"  !! 补算跟踪中样本出错（不影响新样本）：{e}", flush=True)
        return 0


def merge_and_save(ns, rows_map, rows_new, path, enc):
    merged, added, updated, trimmed = ns["band_samples_merge"](rows_map, rows_new)
    n = save_store(ns, merged, path, enc=enc)
    return merged, n, added, updated, trimmed


def print_stats(ns, merged, source=None):
    rows = list(merged.values())
    rng = ns["band_samples_range"](rows)
    print(json.dumps(rng, ensure_ascii=False))
    print(json.dumps(ns["band_sample_summary"](rows), ensure_ascii=False, indent=2))
    for src in ((source,) if source else ns["SAMPLE_SOURCES"]):
        lift = ns["band_sample_lift"](rows, source=src, tradable_only=True)
        b = lift["baseline"]
        print(f"\n=== 来源 {ns['SAMPLE_SOURCE_LABEL'].get(src, src)} 的基准率 ===")
        print(f"  可交易已结案样本 n={b['n']}  平均超额={b['avg_excess']}%  "
              f"胜率={b['win_rate']}%  平均收益={b['avg_ret']}%")
        if not lift["rows"]:
            print("  （没有可分桶的样本：要么还没结案，要么入场上下文缺失）")
            continue
        for r in sorted(lift["rows"], key=lambda x: -x["n"]):
            if r["n"] < ns["REVIEW_MIN_SAMPLE"]:
                continue
            print(f"  {r['dim']}/{r['bucket']}: n={r['n']} 胜率={r['win_rate']}% "
                  f"超额={r['avg_excess']}% 相对基准={r['lift']} "
                  f"样本外={r['oos_excess']}%(n={r['oos_n']}) 稳健={r['stable']}")


# ============================ 五、CLI ============================

def main():
    ap = argparse.ArgumentParser(description="全市场样本自动采集（无头，可定时）")
    ap.add_argument("--forward", action="store_true",
                    help="前瞻采集：全市场最新一根K线（默认模式）")
    ap.add_argument("--backfill", type=int, default=0, metavar="N",
                    help="历史回填：回看最近 N 个交易日逐日重放")
    ap.add_argument("--shard", default="", metavar="i/N", help="回填分片，形如 0/8")
    ap.add_argument("--merge", action="store_true", help="把分片结果合并进语料库")
    ap.add_argument("--stats", action="store_true", help="只打印报表")
    ap.add_argument("--source", default="", help="报表只看某个来源（forward/backfill）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（试跑用）")
    ap.add_argument("--workers", type=int, default=4, help="取数并发数（默认 4）")
    ap.add_argument("--no-cache", action="store_true", help="回填时不走日线磁盘缓存")
    ap.add_argument("--no-refresh", action="store_true", help="不做「跟踪中样本」补算")
    ap.add_argument("--store", choices=("plain", "enc"), default="plain",
                    help="落盘形态：plain=明文 JSONL.gz（本地），enc=加密（需要 BAND_KEY）")
    ap.add_argument("--file", default="", help="语料库路径（默认按 --store 推导）")
    ap.add_argument("--wrap", nargs=2, metavar=("SRC", "DST"), help="把明文语料加密")
    ap.add_argument("--unwrap", nargs=2, metavar=("SRC", "DST"), help="把加密语料解成明文")
    ap.add_argument("--pull", default="", metavar="SRC",
                    help="把云端加密语料解出来并并入本机明文语料库")
    ap.add_argument("--export-forward", default="", metavar="OUT",
                    help="只把库里的前瞻(forward)样本导出一份加密快照，供网页端读取")
    args = ap.parse_args()

    t_all = time.time()
    ns, missing = load_app_namespace()
    if missing:
        print(f"!! 主应用符号抽取失败，请检查 {APP_PATH}：{missing}")
        return 2

    path = args.file or (ENC_FILE if args.store == "enc" else PLAIN_FILE)
    enc = args.store == "enc" or (bool(args.file) and str(args.file).endswith(".enc"))

    # ---- 纯转换类子命令（不需要网络）----
    if args.wrap:
        _wrap_plain(ns, args.wrap[0], args.wrap[1])
        print(f"✅ 已加密：{args.wrap[0]} → {args.wrap[1]}")
        return 0
    if args.unwrap:
        _unwrap_to_plain(ns, args.unwrap[0], args.unwrap[1])
        print(f"✅ 已解密：{args.unwrap[0]} → {args.unwrap[1]}")
        return 0
    if args.pull:
        rows_map = load_store(ns, PLAIN_FILE, enc=False)
        cloud = load_store(ns, args.pull, enc=True)
        merged, n, added, updated, trimmed = merge_and_save(
            ns, rows_map, list(cloud.values()), PLAIN_FILE, enc=False)
        print(f"✅ 已并入云端语料：{len(cloud)} 条 → 新增 {added}，更新结案 {updated}，"
              f"裁剪 {trimmed}，本机现有 {n} 条")
        return 0

    # ---- 导出前瞻语料快照（发布给网页端读的那一份）----
    if args.export_forward:
        rows_map = load_store(ns, path, enc=enc)
        fwd = [r for r in rows_map.values() if str(r.get("source") or "") == "forward"]
        fwd.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("code") or "")))
        total_fwd = len(fwd)
        if total_fwd > FORWARD_EXPORT_MAX:
            fwd = fwd[-FORWARD_EXPORT_MAX:]
            print(f"  前瞻样本 {total_fwd} 条，超过上限 {FORWARD_EXPORT_MAX}，"
                  f"只保留最新的 {len(fwd)} 条")
        if not fwd:
            # ★ 首次运行/当天没采到都会走到这里，属正常状态，**不能报错退出**：
            #   退出码非 0 会让 Actions 步骤变红，久了就没人信这个红灯。
            print(f"语料库里还没有前瞻样本（现有 {len(rows_map)} 条，均为回填），跳过导出。")
            return 0
        n = save_store(ns, fwd, args.export_forward, enc=True)
        print(f"✅ 已导出前瞻语料快照：{n} 条 → {args.export_forward}")
        return 0

    # ---- 报表 ----
    if args.stats:
        rows_map = load_store(ns, path, enc=enc)
        if not rows_map:
            print(f"语料库为空（{path}）。先跑 --forward 或 --backfill。")
            return 4
        print_stats(ns, rows_map, source=(args.source or None))
        return 0

    # ---- 合并分片 ----
    if args.merge:
        parts = [PART_FMT.format(i=i) for i in range(64)]
        parts = [p for p in parts if os.path.exists(p)]
        if not parts:
            print("没有找到任何分片文件，无需合并。")
            return 0
        rows_map = load_store(ns, path, enc=enc)
        rows_new = []
        for p in parts:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows_new.append(json.loads(line))
            print(f"  读入分片 {os.path.basename(p)}：{len(rows_new)} 条累计", flush=True)
        merged, n, added, updated, trimmed = merge_and_save(ns, rows_map, rows_new, path, enc)
        print(f"合并完成：{len(rows_map)} → {n} 条（新增 {added}，更新结案 {updated}，"
              f"裁剪 {trimmed}）")
        for p in parts:
            os.remove(p)
        return 0

    # ---- 采集 ----
    shard_i, shard_n = 0, 1
    if args.shard:
        try:
            a, b = args.shard.split("/")
            shard_i, shard_n = int(a), int(b)
        except Exception:
            print(f"!! --shard 格式应为 i/N，收到 {args.shard}")
            return 2

    if args.forward:
        limited = max(0, int(args.limit or 0))
        print("=" * 72)
        print(f"前瞻采集 ｜ 全市场最新一根K线 ｜ 落盘 {os.path.basename(path)}"
              f"（{'加密' if enc else '明文'}）"
              + (f" ｜ ⚠️ 冒烟测试：只取前 {limited} 只" if limited else ""))
        print("=" * 72)
        print("[1] 取基准（沪深300）")
        bench = fetch_bench(ns)
        print("[2] 全市场扫描 + 逐只取日线")
        try:
            rows, rep = ns["band_samples_harvest_forward"](
                bench_df=bench, progress_cb=None, limit=limited)
        except Exception as e:
            ns["_log"]("harvest/forward", e)
            print(f"!! 前瞻采集失败：{e}")
            return 4
        if rep.get("error"):
            print(f"!! {rep['error']}")
            return 4
        print(f"  全市场 {rep.get('universe')} 只，扫描 {rep.get('codes')} 只，"
              f"取数成功 {rep.get('fetched')}，失败 {rep.get('failed')} → {len(rows)} 条")
        if rep.get("limit"):
            print(f"  ⚠️ 本次为冒烟测试（limit={rep['limit']}），这批样本不代表全市场，"
                  f"正式采集请留空 limit")
        if rep["fail_examples"]:
            print(f"  取数失败示例：{rep['fail_examples']}")
        rows_map = load_store(ns, path, enc=enc)
        refreshed = 0 if args.no_refresh else refresh_pending(
            ns, rows_map, _cached_fetch(ns, use_cache=False), bench)
        merged, n, added, updated, trimmed = merge_and_save(ns, rows_map, rows, path, enc)
        print(f"[3] 落库：{n} 条（新增 {added}，更新结案 {updated + refreshed}，"
              f"裁剪 {trimmed}）")
        if not rows and not refreshed:
            print("!! 本次没采到任何样本，检查上面失败计数（非交易时段/接口不稳时会这样）")
        print_stats(ns, merged, source="forward")
        print(f"\n用时 {(time.time()-t_all)/60:.1f} 分钟")
        return 0

    # 回填
    print("=" * 72)
    print(f"历史回填 ｜ 分片 {shard_i+1}/{shard_n} ｜ 回看 {args.backfill} 个交易日"
          f" ｜ 落盘 {os.path.basename(path)}")
    print("=" * 72)
    print("[1] 全市场清单（含创业板/科创板/北交所，不套 EXCLUDE_PREFIXES）")
    codes = market_universe(ns)
    allc = sorted(codes.keys())
    print(f"  合计 {len(allc)} 只")
    if args.limit:
        allc = allc[:args.limit]
    if shard_n > 1:
        allc = [c for k, c in enumerate(allc) if k % shard_n == shard_i]
        print(f"  本分片负责 {len(allc)} 只")
    print("[2] 取基准")
    bench = fetch_bench(ns)
    print("[3] 逐日重放（首次跑要拉全市场日线，会慢；有磁盘缓存后续很快）")
    rows, rep = harvest_codes(ns, allc, codes, "backfill", args.backfill,
                              _cached_fetch(ns, use_cache=not args.no_cache),
                              bench, max(1, args.workers),
                              progress_tag=f" 分片{shard_i+1}")
    print(f"[4] 扫描 {rep['codes']} 只，取数成功 {rep['fetched']}，失败 {rep['failed']}，"
          f"陈旧序列剔除 {rep.get('stale', 0)}，"
          f"评估 {rep['evaluated']} 个交易日 → {len(rows)} 条样本")
    if rep["fail_examples"]:
        print(f"  取数失败示例：{rep['fail_examples']}")
    if not rows:
        print("!! 一条样本都没产出，检查上面的失败计数")
        return 4
    if shard_n > 1:
        os.makedirs(CACHE_DIR, exist_ok=True)
        p = PART_FMT.format(i=shard_i)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        print(f"  分片结果已写入 {os.path.relpath(p, HERE)}，全部跑完后执行 --merge")
    else:
        rows_map = load_store(ns, path, enc=enc)
        merged, n, added, updated, trimmed = merge_and_save(ns, rows_map, rows, path, enc)
        print(f"  已写入语料库：{n} 条（新增 {added}，更新结案 {updated}，裁剪 {trimmed}）")
    print(f"用时 {(time.time()-t_all)/60:.1f} 分钟")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StoreError as e:
        print(f"!! 语料存储错误：{e}")
        sys.exit(3)
    except KeyboardInterrupt:
        print("\n已中断。磁盘缓存保留，重跑会从断点继续。")
        sys.exit(130)
