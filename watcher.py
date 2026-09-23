#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
做T信号定时巡检推送器（watcher.py）

用途：不依赖 Streamlit 网页运行，由 GitHub Actions 每 5 分钟调用一次，
      盘中命中买卖点即通过 Server酱 推送微信。

环境变量（在 GitHub 仓库 Secrets 里配置）：
    SERVERCHAN_KEY   必填，例如 sct-xxxxx
    WATCHLIST        必填，逗号分隔的股票代码，例如 600176,515880,159915
    ALERT_WINDOW_MIN 选填，买点必须是"最近 N 分钟刚形成"才推送，默认 10 分钟
    FORCE_RUN         选填，设为 1 时忽略交易时段限制（仅供手动测试），默认 0
"""
import os
import json
import math
import re
import sys
import time as _time_module
from datetime import datetime, time, timedelta, timezone

import requests
import pandas as pd
import numpy as np

# ✅ 云服务器一律 UTC，所有时间判断统一换算成北京时间
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    return datetime.now(CN_TZ)

def _log(where, err):
    """统一的可观测性出口：把原本被 except 静默吞掉的异常写到 stderr。

    GitHub Actions 会把 stderr 收进运行日志，所以留痕 = 可排查。
    凡是要吞异常，必须先经过这里留痕，禁止裸 `except: pass`。"""
    try:
        print(f"[watcher][{where}] {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)
    except Exception:
        pass

SEND_KEY = os.environ.get("SERVERCHAN_KEY", "").strip()
WATCHLIST = [c.strip() for c in os.environ.get("WATCHLIST", "").replace("，", ",").split(",") if c.strip()]
# 额外的波段监控名单（可选）：不依赖网页端记忆文件，直接逗号分隔填代码。
BAND_WATCHLIST = [c.strip() for c in os.environ.get("BAND_WATCHLIST", "").replace("，", ",").split(",") if c.strip()]
LOG_FILE = os.environ.get("NOTIFY_LOG", "notify_log.json")
# 波段记忆文件：由网页端同步到仓库，这里只读取并回写状态变化。
# ★ 读写的 band_watch.json 是网页端本地记忆的**完整镜像**（2026-09-20 起不再裁剪字段），
#   所以回写时也必须整节点写回 —— 否则会把网页端刚推上来的备注/入选价裁掉。
#   本仓库是 public：要保密请配 BAND_KEY（提交的是密文），不要靠删字段。
BAND_MEMORY_FILE = os.environ.get("BAND_MEMORY_FILE", "band_watch.json")
WINDOW_MIN = int(os.environ.get("ALERT_WINDOW_MIN", "10"))
FORCE_RUN = os.environ.get("FORCE_RUN", "0").strip() == "1"

# ============ 波段状态常量 ============
# ⚠️ 必须与 ai_stock_terminal.py 中的同名常量保持一致，否则网页与微信的结论会不一致。
BAND_ALERT_STATUSES = ('顶背离预警', '跌破支撑')        # 波段结束预警
BAND_ENTRY_STATUSES = ('波段启动确认',)                 # 波段启动
# ★ 顶背离的「MACD 背离幅度」门槛（%）：第二个高点的 MACD 必须比第一个低这么多，才算真背离。
#   0 = 只看方向（2026-09-20 之前的旧行为）；30 = 要求 MACD 真的掉三成。
#
#   依据 `_debug_probe/probe_divergence_backtest.py`（3398 只票 / 27,352 次「进入顶背离预警」
#   事件 / 2023-11 起约 450 根日线 / 判据只用事件之后的走势）：在「之后 10 天内跌破 20 日线」
#   这个判据上，四个候选收紧方向里**只有这个方向是单调有效的** ——
#     门槛 0/5/10/15/20/25/30/40/50% → 保留组跌破率 68.3/69.1/70.0/70.7/71.2/72.1/72.9/74.3/75.3%
#   而「新高须 ≥N%」方向**相反**（68.3%→64.4% 单调下降：擦边新高反而更准，
#   照直觉收紧会先砍掉更准的那批）；「两高点间隔 ≥M 根」几乎无效
#   （现状本身就保证 ≥2 根，再抬高只是减少样本）。
#
#   ⚠️ 必须与 ai_stock_terminal.py 中的同名常量保持一致；不一致就会出现
#      「网页标了顶背离、微信不推」这种最难查的不对称。
BAND_DIVERGENCE_MACD_MIN_PCT = 30.0
BAND_STATUS_LEVEL = {'波段未形成': 0, '波段进行中': 1, '波段启动确认': 2,
                     '跌破支撑': 3, '顶背离预警': 4}
BAND_MEMORY_HISTORY_MAX = 40

# ============ 基础工具 ============

def is_trading_time():
    t = now_cn().time()
    return (time(9, 25) <= t <= time(11, 32)) or (time(12, 58) <= t <= time(15, 2))

# ---- 统一 HTTP 层（容错策略与 ai_stock_terminal.py 保持一致）----
_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://gu.qq.com/",
    "Accept": "*/*",
}
_QQ_HOSTS = ["https://web.ifzq.gtimg.cn", "https://ifzq.gtimg.cn"]   # 腾讯主/备域名
# 备用日线源（与网页端同顺序）：东财前复权（与腾讯同口径）→ 新浪不复权（最后兜底）
_EM_KLINE_HOSTS = ["https://push2his.eastmoney.com", "https://push2.eastmoney.com"]

def http_get(url, timeout=(5, 10), retries=2, encoding=None):
    """带 UA / 超时 / 重试的 GET。返回 (ok, text, err)，绝不抛异常。

    原先各处 requests.get(timeout=5) 无重试、无 UA，GitHub Actions 跑在海外节点，
    跨境访问腾讯接口偶发超时就整轮巡检失效。"""
    last = "未知错误"
    for i in range(max(1, retries)):
        try:
            r = requests.get(url, headers=_HTTP_HEADERS, timeout=timeout)
            if encoding:
                r.encoding = encoding
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                continue
            return True, r.text, ""
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
            if i < max(1, retries) - 1:
                _time_module.sleep(0.4 * (i + 1))
    return False, "", last

def http_get_json(url, timeout=(5, 10), retries=2):
    ok, text, err = http_get(url, timeout=timeout, retries=retries)
    if not ok:
        return False, None, err
    try:
        return True, json.loads(text), ""
    except Exception as e:
        return False, None, f"JSON 解析失败: {str(e)[:80]}"

# 行情代码前缀判定。⚠️ 必须与 ai_stock_terminal.py 的 _quote_prefix 保持完全一致，
# 改动时请同步两处：北交所（43/83/87/88/92）和沪市转债（110/111/113/118/119）
# 若被误判成 sz 前缀，接口会返回空数据，监控将静默跳过该股票。
_BJ_PREFIXES = ('43', '83', '87', '88', '92')
_SH_BOND_PREFIXES = ('110', '111', '113', '118', '119')

def em_secid(code):
    """东财 secid：沪市 `1.`，深市 / 北交所 `0.`。

    ⚠️ 必须与 ai_stock_terminal.py 的 `_em_secid` 保持完全一致（两边都在查东财，
    规则漂移会让网页端能取到、巡检取不到 —— 那就成了「网页正常、微信不动」）。
    """
    s = str(code).strip().lower()
    if s.startswith("sh"):
        return "1." + s[2:]
    if s.startswith(("sz", "bj")):
        return "0." + s[2:]
    if s.startswith(("5", "6", "9", "110", "111", "113", "118", "119")):
        return "1." + s
    return "0." + s


def quote_prefix(symbol):
    s = str(symbol).strip()
    if not re.fullmatch(r'\d{6}', s):
        return "sz"
    if s.startswith(_BJ_PREFIXES):
        return "bj"
    if s.startswith(_SH_BOND_PREFIXES):
        return "sh"
    if s.startswith('12'):
        return "sz"
    if s.startswith(('5', '6', '9')):
        return "sh"
    return "sz"

def get_code(symbol):
    return f"{quote_prefix(symbol)}{str(symbol).strip()}"

def get_stock_name(symbol):
    ok, text, _err = http_get(f"https://qt.gtimg.cn/q={get_code(symbol)}", encoding='gbk')
    if ok and "~" in text:
        parts = text.split("~")
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip('"')
    return symbol

def get_market_change():
    """上证指数涨跌幅(%)"""
    ok, text, _err = http_get("https://qt.gtimg.cn/q=sh000001", encoding='gbk')
    if ok and "~" in text:
        parts = text.split("~")
        if len(parts) > 32:
            try:
                return float(parts[32])
            except (TypeError, ValueError) as e:
                # 解析失败会静默变成 0.00%，进而影响"大盘暴跌不推送"的门槛判断
                _log("get_market_change:float(parts[32])", e)
    return 0.0

# ================= 微信推送「自动发送许可」+「每日限额账本」（2026-09-22）========
# 用户口径（两次澄清后）：
#   「因为我每天微信通知只有五条的限制，留下一条给六点的通知」
#   「剩下四条正常推送，不过是要我选定的股票才能自动推送」
# ⇒ 1 条留给 18:00 日报；剩 4 条由**白名单自动推送**与网页端手动点共用，用完都停。
#
# ★★ 白名单**只决定"能不能自动发"**，与监控范围无关 —— 用户原话：
#   「云端巡检如果是监控启动终止点以及日内买卖点的话，这些要正常进行，
#    需要约束的只是微信通知权限」。
#   所以本文件该盯的照旧盯（WATCHLIST 的分时 + 波段记忆的日线），
#   名单外的票照旧走 `_print_candidate()` 打进日志，等用户在网页端选。
#
# ★ 账本与网页端（ai_stock_terminal.py）**共用同一份 notify_budget.json**：
#   云端自动发了也要记账，否则网页端看到的「还能推 N 条」就是假的。
#   ⚠️ 下面 _clamp_int / notify_budget_* / notify_whitelist_* 与主应用**逐字同源**
#     （有测试用 ast.unparse 逐个比对）—— 口径漂移的代价是两边额度和名单对不上。
# ★ 解密纪律（两个方向刻意相反，别"统一"掉）：
#   · 账本文件在、却读不出来 → **禁止自动发**（当空账本＝把当日已用清零＝超发）；
#   · 白名单文件在、却读不出来 → 视为**空名单**（空名单＝谁都不自动发＝安全）。
_NOTIFY_BUDGET_FILE = os.environ.get("NOTIFY_BUDGET", "notify_budget.json")
_NOTIFY_WHITELIST_FILE = os.environ.get("NOTIFY_WHITELIST", "notify_whitelist.json")
NOTIFY_LIMIT_DEFAULT = 5        # 与 ai_stock_terminal.py 同值
NOTIFY_RESERVE_DEFAULT = 1
NOTIFY_LIMIT_MAX = 20
NOTIFY_WHITELIST_MAX = 20


# ================= 3.2b ★ 日内买卖点「置信度」与推送门槛（2026-09-22 用户要求）=====
# 用户口径：「只给我微信推送置信度高的买卖点」＋门槛要能自己调（下拉，默认「高」）。
# ⇒ 置信度**只此一份**：页面展示、网页端巡检、Actions 巡检三处共用
#   `intraday_point_confidence`，绝不再各写一份 if 链 ——
#   分时买卖点偏离系数 0.4/0.5 漂移那次就是这么漂出去的。
NOTIFY_CONF_LEVELS = ("高", "中", "低")     # 下拉顺序：从严到松
NOTIFY_MIN_CONF_DEFAULT = "高"              # 默认门槛：只推「高」（用户选定）
NOTIFY_CONF_ORDER = {"高": 2, "中": 1, "低": 0}


def intraday_point_confidence(kind, price, avg_price, deviation,
                              divergence_info="", market_change=0.0, macd_ok=False):
    """给一个日内买卖点打置信度：高 / 中 / 低。

    kind：'buy' 买点 / 'sell' 卖点；avg_price：当日分时均价；
    macd_ok：该点 MACD 是否同向拐头；divergence_info：`_intraday_divergence` 的返回串。

    ★ 大盘暴跌（上证 < -1.0%）时**买点直接判「低」**：这种环境下低吸胜率最差。
      卖点**刻意不受它影响** —— 暴跌里高抛恰恰是对的，一刀切会把对的信号也砍掉。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    try:
        if kind == 'buy' and float(market_change or 0.0) < -1.0:
            return "低"
        avg = float(avg_price or 0.0)
        if avg <= 0:
            return "低"
        price = float(price)
        dev = float(deviation or 0.0)
        tag = "底背离" if kind == 'buy' else "顶背离"
        if kind == 'buy':
            dev_pct = (avg - price) / avg
            scheme_a = price < avg * (1 - dev * 0.7)
        else:
            dev_pct = (price - avg) / avg
            scheme_a = price > avg * (1 + dev * 0.7)
        if dev_pct > dev * 1.5 or tag in (divergence_info or ""):
            return "高"
        if dev_pct > dev * 0.8 or (scheme_a and macd_ok):
            return "中"
        return "低"
    except Exception as e:
        _log("intraday_point_confidence", e)
        return "低"


def notify_conf_threshold(w):
    """取白名单里那份「最低推送置信度」。字段缺失/写歪 → 默认「高」。

    ★ 兜底方向刻意偏严：字段坏了只会**少推**，绝不会因为读歪而把门槛降到「低」。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    v = str(notify_whitelist_normalize(w).get("min_conf") or "").strip()
    return v if v in NOTIFY_CONF_LEVELS else NOTIFY_MIN_CONF_DEFAULT


def notify_conf_allowed(conf, threshold):
    """置信度 `conf` 够不够 `threshold` 这道门槛。

    未知/空的置信度一律**不放行**（-1 低于任何一档）——宁可少推，也不瞎推。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    _th = threshold if threshold in NOTIFY_CONF_LEVELS else NOTIFY_MIN_CONF_DEFAULT
    return (NOTIFY_CONF_ORDER.get(str(conf or "").strip(), -1)
            >= NOTIFY_CONF_ORDER.get(_th, 2))


def _clamp_int(v, lo, hi, default):
    """收敛进 [lo, hi]。坏值落到 default（**不是** lo）。

    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**
      （测试会比对两边 ast.unparse 后的整个函数）—— 它决定额度算不算得对。
    """
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def notify_budget_empty():
    """空账本。日期用**北京时间**（云端运行在 UTC，用 UTC 会在每天早上 8 点前算成昨天）。"""
    return {"date": now_cn().strftime('%Y-%m-%d'), "limit": NOTIFY_LIMIT_DEFAULT,
            "reserved": NOTIFY_RESERVE_DEFAULT, "sent": [], "updated_at": ""}


def notify_budget_normalize(b):
    """收敛成干净账本，**跨天自动清空 sent**。

    ★ 跨天必须清空：留着昨天的 sent，今天一开页面就是「额度已用完」。
    ★ reserved 必须 ≤ limit：否则额度算成负数，界面会出现"还能推 -1 条"。
    ★ sent 只保留结构正确的条目：坏条目会让计数与实际推送数不符（额度算错）。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    d = notify_budget_empty()
    if not isinstance(b, dict):
        return d
    limit = _clamp_int(b.get("limit"), 1, NOTIFY_LIMIT_MAX, NOTIFY_LIMIT_DEFAULT)
    reserved = _clamp_int(b.get("reserved"), 0, limit, NOTIFY_RESERVE_DEFAULT)
    out = {"date": d["date"], "limit": limit, "reserved": reserved,
           "sent": [], "updated_at": str(b.get("updated_at") or "")}
    if str(b.get("date") or "") == d["date"]:
        for it in (b.get("sent") or []):
            if isinstance(it, dict) and it.get("title"):
                out["sent"].append(it)
    return out


def notify_budget_used(b):
    return len(notify_budget_normalize(b)["sent"])


def notify_budget_left(b):
    """今日还能推几条 = 上限 − 预留（日报）− 已用。

    ★ 2026-09-22 起从 `notify_budget_manual_left` 改名：**自动推送（白名单）与手动点
      共用这一份额度**，叫「manual_left」已经名不副实。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    nb = notify_budget_normalize(b)
    return max(0, nb["limit"] - nb["reserved"] - len(nb["sent"]))


def notify_budget_line(b):
    """界面 / 日志用的一行话。数字只从账本算一次，不在别处另算。

    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    nb = notify_budget_normalize(b)
    left = notify_budget_left(nb)
    txt = (f"今日已用 **{len(nb['sent'])} / {nb['limit']}** 条"
           f"（{nb['reserved']} 条预留 18:00 日报）→ 还能推 **{left}** 条"
           f"（自动白名单 + 手动共用）")
    return txt + ("　⚠️ 今日额度已用完：自动和手动都停了" if left == 0 else "")


def notify_whitelist_empty():
    """空名单。**这就是"谁都不自动发"** —— 默认状态，也是读不出来时的兜底。

    ★ `min_conf`（最低推送置信度）与名单**同存一份文件**（用户要求跟白名单一起存），
      缺字段时按默认「高」处理 —— 老文件读出来照旧能用，不会因为新字段把名单读空。
    """
    return {"updated_at": "", "items": [], "min_conf": NOTIFY_MIN_CONF_DEFAULT}


def notify_whitelist_normalize(w):
    """收敛成干净名单：只留 6 位数字代码、去重、按代码升序。

    ★ 超上限时**截断**而不是整份丢掉：整份丢掉会让用户以为"我明明勾了却保存不上"。
    ★ `name` 只用于界面显示，丢了不影响判定（判定只认 code，见 notify_whitelist_has）。
    ★ `min_conf` 是**推送门槛**，不是名单的一部分：写歪了只落回默认「高」，
      绝不因为一个坏字段就把整份名单丢掉（那等于"我勾了却不生效"）。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    out = notify_whitelist_empty()
    if not isinstance(w, dict):
        return out
    out["updated_at"] = str(w.get("updated_at") or "")
    _mc = str(w.get("min_conf") or "").strip()
    out["min_conf"] = _mc if _mc in NOTIFY_CONF_LEVELS else NOTIFY_MIN_CONF_DEFAULT
    seen, items = set(), []
    for it in (w.get("items") or []):
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip()
        if not (len(code) == 6 and code.isdigit()) or code in seen:
            continue
        seen.add(code)
        items.append({"code": code, "name": str(it.get("name") or "").strip(),
                      "added_at": str(it.get("added_at") or "")})
    items.sort(key=lambda x: x["code"])
    out["items"] = items[:NOTIFY_WHITELIST_MAX]
    return out


def notify_whitelist_codes(w):
    """名单里的代码集合（判定只认它）。"""
    return {it["code"] for it in notify_whitelist_normalize(w)["items"]}


def notify_whitelist_has(w, code):
    """`code` 是否在白名单里 —— **自动推送的唯一判据**。

    ★ 空名单恒为 False：这是刻意的默认安全（谁都不自动发）。
    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    c = str(code or "").strip()
    return bool(c) and c in notify_whitelist_codes(w)


def _notify_json_read(path):
    """读 JSON 文件 → (对象, 文件是否存在)。

    ★ 必须把"不存在"和"读不动"分开：两者在账本上的含义完全相反
      （不存在＝首次运行，空账本正常；读不动＝不可信，必须禁止自动发）。
    """
    if not os.path.exists(path):
        return None, False
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), True
    except Exception as e:
        _log(f"_notify_json_read:{path}", e)
        return None, True


def notify_budget_state():
    """读账本 → (normalize 后的账本, trust)。trust=False ⇒ 文件在但读不出来 ⇒ 禁止自动发。"""
    raw, exists = _notify_json_read(_NOTIFY_BUDGET_FILE)
    if not exists:
        return notify_budget_empty(), True
    ok, obj, err = band_decrypt_obj(raw)
    if not ok or obj is None:
        print(f"  ⚠️ 账本 {_NOTIFY_BUDGET_FILE} 读不出来（{err}）→ 本次不自动发"
              f"（把它当空账本会把当日已用清零 → 超发）")
        return notify_budget_empty(), False
    return notify_budget_normalize(obj), True


def notify_budget_save(b):
    """加密写回账本。★ 加密失败就**不写**（绝不落明文），返回 False 由调用方打告警。"""
    try:
        payload = band_encrypt_obj(notify_budget_normalize(b))
    except Exception as e:
        _log("notify_budget_save:encrypt", e)
        return False
    try:
        with open(_NOTIFY_BUDGET_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log("notify_budget_save", e)
        return False


def notify_whitelist_load():
    """读白名单 → (normalize 后的名单, trust)。

    ★ 文件在却读不出来 → 视为**空名单**（谁都不自动发）+ 打醒目告警。
      方向与账本相反：账本读不出来要"拒发"，白名单读不出来"不发"本身就是安全侧，
      只是必须让人看得见（否则表现成"我明明勾了却不生效"，比报错难查）。
    """
    raw, exists = _notify_json_read(_NOTIFY_WHITELIST_FILE)
    if not exists:
        return notify_whitelist_empty(), True
    ok, obj, err = band_decrypt_obj(raw)
    if not ok or obj is None:
        print(f"  ⚠️ 白名单 {_NOTIFY_WHITELIST_FILE} 读不出来（{err}）→ 视为空名单："
              f"本次谁都不自动发（检查 BAND_KEY 是否与网页端一致）")
        return notify_whitelist_empty(), False
    return notify_whitelist_normalize(obj), True


# ★ 总闸（**紧急关闭开关**，正常不用动）：仓库 Settings → Secrets and variables →
#   Actions → Variables 里把 NOTIFY_AUTO_PUSH 设成 **0**，就退回"谁都不自动发"。
#   ★ 2026-09-22 语义**反过来了**：以前"不设＝关"，现在"不设＝开（但仍要过白名单）"。
#     为什么敢默认开：真正的闸是**白名单**，而白名单文件不存在时就是空名单＝谁都不发。
#     所以不配置任何东西时，行为与上一版完全一致（全手动），不会突然刷屏。
AUTO_PUSH = os.environ.get("NOTIFY_AUTO_PUSH", "1").strip() != "0"


def _print_candidate(title, content):
    """把「本该推送」的候选打进 Action 日志，一行一条。

    不记入去重日志：那份日志是「已推送」的账，把候选混进去会让
    日后真要恢复自动推送时少发。候选量本来就少（就是那几条以前会被推的），
    重复打印不会淹没其他信息。
    """
    first = ""
    for line in (content or "").splitlines():
        if line.strip():
            first = line.strip()
            break
    print(f"  📤 候选（未推送，等你在网页端选）：{title}　{first}")


def send_wechat(title, content, code="", name=""):
    """微信推送的**唯一出口**（巡检侧 4 个调用点全走这里）。

    ★ 2026-09-22 起三道闸，**顺序不许调**（有源码守卫盯着"闸门必须在 requests.post 之前"）：
      ① 总闸 AUTO_PUSH —— 仓库 Variable `NOTIFY_AUTO_PUSH=0` 时紧急全关；
      ② 白名单闸 —— `code` 不在名单里就只登记候选、**绝不发送**
         （名单为空＝谁都不自动发，这是默认状态）；
      ③ 额度闸 —— 账本剩余 ≤ 0 就不发（4 条与网页端手动点共用，1 条预留 18:00 日报）。
    返回 True 表示"真的发出去了" —— 调用方据此记去重日志、累加计数（含义与之前一致）。
    """
    if not AUTO_PUSH:
        _print_candidate(title, content)
        return False
    _wl, _wl_trust = notify_whitelist_load()
    if not notify_whitelist_has(_wl, code):
        # 名单外（或名单读不出来＝空名单）：只登记，不发送。
        # ★ 这里**不问**白名单为什么空 —— 空就是不发，方向永远偏安全。
        _print_candidate(title, content)
        return False
    _budget, _b_trust = notify_budget_state()
    if not _b_trust:
        print(f"  ⏸ 账本读不出来，无法确认今日已用条数 → 本次不自动发：{title}")
        return False
    if notify_budget_left(_budget) <= 0:
        print(f"  ⏸ 今日推送额度已用完（{len(_budget['sent'])}/{_budget['limit']} 条，"
              f"{_budget['reserved']} 条留给 18:00 日报）→ 本次不自动发：{title}")
        return False
    if not SEND_KEY:
        print("  ⏸ 未配置 SERVERCHAN_KEY → 不发（候选已打进上面的日志）")
        return False
    try:
        res = requests.post(f"https://sctapi.ftqq.com/{SEND_KEY}.send",
                            data={"title": title, "desp": content}, timeout=10)
        if res.status_code != 200:
            print(f"  推送 HTTP {res.status_code}: {res.text[:160]}")
            return False
    except Exception as e:
        print(f"  推送异常: {e}")
        return False
    # ★ 失败不扣额度（上面已经 return False 了）；**成功才记账**。
    #   记账/写盘失败**不回滚**：消息已经发出去了，改回去才是真的对不上，
    #   只能打告警让人知道"这一条没进账本，下次可能超发"。
    _ts = now_cn().strftime('%Y-%m-%d %H:%M:%S')
    _budget["sent"].append({"ts": _ts, "kind": "auto", "src": "auto",
                            "code": str(code), "name": str(name), "title": title})
    _budget["updated_at"] = _ts
    if not notify_budget_save(_budget):
        print("  ⚠️ 账本写盘失败 —— 这条已经发出去了但没记上，下次可能超发")
    return True

# ============ 去重机制 ============

def price_bucket(price, pct=0.005):
    try:
        if price <= 0:
            return 0
        return int(math.log(max(price, 0.001)) / math.log(1 + pct))
    except Exception as e:
        # 坏价格落到 0 号桶会引发去重碰撞（该推的没推 / 重复推），必须留痕
        _log("price_bucket", e)
        return 0

def load_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        # 日志损坏会让去重记录整体归零 → 全天重复推送
        _log("load_log", e)
    return {}

def save_log(log):
    today = now_cn().strftime('%Y-%m-%d')
    keep = {today: log.get(today, [])}
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  日志保存失败: {e}")

# ============ 分时数据（与网页端同逻辑）============

def get_minute_data(code):
    raw = None
    for host in _QQ_HOSTS:
        ok, js, err = http_get_json(f"{host}/appstock/app/minute/query?code={code}")
        if not ok:
            print(f"  分时接口不可用 {host}: {err}")
            continue
        if not isinstance(js, dict) or js.get("code") != 0:
            print(f"  分时接口返回异常 {host}: code={js.get('code') if isinstance(js, dict) else '非JSON'}")
            continue
        node = (js.get("data") or {}).get(code) or {}
        raw = ((node.get("data") or {}) or {}).get("data")
        if raw:
            break
    if not raw:
        return None
    try:
        records = [item.split(" ") for item in raw]
        if not records:
            return None
        ncol = len(records[0])
        if ncol < 3:
            return None
        cols = ['Time', 'Price', 'F3', 'F4'][:ncol]
        df = pd.DataFrame(records, columns=cols)
        for c in cols:
            if c != 'Time':
                df[c] = pd.to_numeric(df[c], errors='coerce')
        if df['Price'].isna().all():
            return None

        last_price = float(df['Price'].dropna().iloc[-1])
        f3 = df['F3'].fillna(0) if 'F3' in df.columns else pd.Series([0.0] * len(df))
        f4 = df['F4'].fillna(0) if 'F4' in df.columns else pd.Series([0.0] * len(df))
        f3_like_price = f3.iloc[-1] > 0 and 0.7 < f3.iloc[-1] / last_price < 1.3
        f4_mono = f4.iloc[-1] > 0 and (f4.diff().dropna() >= -1e-6).all()

        if f3_like_price and f4_mono:
            df['AvgPrice'] = f3
            df['CumVolume'] = f4
        elif f4_mono:
            df['CumVolume'] = f4
            ratio = f3.iloc[-1] / f4.iloc[-1] if f4.iloc[-1] > 0 else 0
            if 0.3 * last_price < ratio < 3 * last_price:
                df['AvgPrice'] = f3 / f4.replace(0, np.nan)
            elif 0.3 * last_price * 100 < ratio < 3 * last_price * 100:
                df['AvgPrice'] = f3 / (f4 * 100)
            else:
                df['AvgPrice'] = np.nan
        else:
            df['CumVolume'] = f4 if f4.iloc[-1] > 0 else f3
            df['AvgPrice'] = np.nan

        df['Volume'] = df['CumVolume'].diff()
        if len(df) > 0:
            df.loc[df.index[0], 'Volume'] = df['CumVolume'].iloc[0]
        df['Volume'] = df['Volume'].fillna(0).clip(lower=0)
        if df['AvgPrice'].isna().any() or (df['AvgPrice'] <= 0).any():
            amt = df['Price'] * df['Volume']
            cum_amt = amt.cumsum()
            df['AvgPrice'] = (cum_amt / df['CumVolume'].replace(0, np.nan)).ffill().fillna(df['Price'])

        ema12 = df['Price'].ewm(span=12, adjust=False).mean()
        ema26 = df['Price'].ewm(span=26, adjust=False).mean()
        df['DIFF'] = ema12 - ema26
        df['DEA'] = df['DIFF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIFF'] - df['DEA'])
        return df
    except Exception as e:
        print(f"  分时数据异常: {e}")
        return None

# ============ 日线数据（用于波段结束预警）============

def _kline_rows_to_df(kline):
    """把「日期,开,收,高,低,量」的行列表规整成日线 DataFrame（腾讯/东财共用）。

    返回 df 或 None；**不抛异常**（列数不对、全脏值都只是 None，由调用方继续试下一个源）。
    不足 60 条也返回 None —— 波段判定需要 60 日窗口，凑不够就当没拿到。
    """
    try:
        df = pd.DataFrame(kline)
        if df.shape[1] < 6:
            return None
        df = df.iloc[:, :6]
        df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        for col in ['Open', 'Close', 'High', 'Low', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['Date', 'Close']).sort_values('Date').reset_index(drop=True)
        return df if len(df) >= 60 else None
    except Exception as e:
        print(f"  日线解析失败: {e}")
        return None


def _get_daily_history(symbol):
    """获取日线（波段结束预警用）。腾讯 → 东财 → 新浪 依次兜底，凑够 60 条才返回。

    ★★ 2026-09-20 补兜底（这是「波段提醒静默停摆」的已知缺口）：
      原先只轮询腾讯 `_QQ_HOSTS`，而腾讯的前复权(qfq)接口会**间歇性 HTTP 200 + 空 body** ——
      一抖就整轮巡检评不出状态、什么都不推，**并且日志里看不出异常**（接口没报错，只是空）。
      网页端早就有兜底、巡检没有，于是出现「网页正常、微信不动」这种最难查的形态。
      现在两边顺序一致：东财(前复权，与腾讯同口径) 第二，新浪(不复权) 最后。
    ⚠️ 用新浪兜底时会打印警告：不复权在除权日会出现价格断层，可能误判「跌破支撑」。
    原先只打单个域名且 prefix 用 startswith 硬判，北交所/沪市转债会拿不到数据。
    """
    code = get_code(symbol)

    # ---- 源 1：腾讯（前复权，主/备域名轮询）----
    for host in _QQ_HOSTS:
        ok, js, err = http_get_json(f"{host}/appstock/app/fqkline/get?param={code},day,,,260,qfq")
        if not ok:
            print(f"  日线接口不可用 {host}: {err}")
            continue
        if not isinstance(js, dict) or js.get("code") != 0:
            continue
        node = (js.get("data") or {}).get(code) or {}
        kline = node.get("qfqday") or node.get("day")
        if not kline:
            print(f"  日线为空 {code}（该代码可能不受腾讯支持）")
            continue
        df = _kline_rows_to_df(kline)
        if df is not None:
            return df

    # ---- 源 2：东方财富（前复权，与腾讯同口径）----
    for host in _EM_KLINE_HOSTS:
        # ★ 不传 beg：带上 `beg=0` 东财会忽略 lmt、把 1999 年至今全部历史都吐回来（实测）
        ok, js, err = http_get_json(
            f"{host}/api/qt/stock/kline/get?secid={em_secid(code)}&klt=101&fqt=1"
            f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56"
            f"&end=20500101&lmt=260")
        if not ok:
            print(f"  东财日线不可用 {host}: {err}")
            continue
        data = (js or {}).get("data") if isinstance(js, dict) else None
        klines = (data or {}).get("klines") if isinstance(data, dict) else None
        if not klines:
            print(f"  东财日线为空 {code}")
            continue
        _rows = []
        for _line in klines:
            _p6 = str(_line).split(",")
            if len(_p6) >= 6:
                _rows.append([_p6[0], _p6[1], _p6[2], _p6[3], _p6[4], _p6[5]])
        df = _kline_rows_to_df(_rows)
        if df is not None:
            print("  ℹ️ 日线来自东财（前复权，与腾讯同口径）")
            return df

    # ---- 源 3：新浪（**不复权**，最后兜底；除权日可能误判，必须留痕）----
    _ok, _js, _err = http_get_json(
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen=260")
    if _ok and isinstance(_js, list) and _js:
        _rows = []
        for _it in _js:
            if isinstance(_it, dict):
                _rows.append([_it.get('day'), _it.get('open'), _it.get('close'),
                              _it.get('high'), _it.get('low'), _it.get('volume')])
        df = _kline_rows_to_df(_rows)
        if df is not None:
            print("  ⚠️ 日线来自新浪（**不复权**）—— 除权日可能出现价格断层，"
                  "若本条预警看起来不合理请以券商行情为准")
            return df
    else:
        print(f"  新浪日线不可用: {_err}")

    print(f"  ⛔ {code} 三个源都拿不到日线（腾讯/东财/新浪）—— 本次无法评估波段状态")
    return None


BAND_RUN_MAX_LOOKBACK = 120   # 一轮「启动」最多往回找多少根 K 线（与主应用同值）

def _band_run_start(df):
    """本轮「波段启动」连续区间从哪根 K 线开始？→ {'days','date','price','idx'}。

    ★ 这是 ai_stock_terminal.py 同名函数的**镜像**，必须逐字同公式（有跨文件一致性测试守着）。
      为什么要两侧都算：「波段启动确认」是一个**可以持续很多天**的状态 —— 判定只看当日是否
      「突破 60 日平台 + 放量」。一只持续创新高的票每天都在满足它，于是标签一直挂着「启动确认」，
      用户看到「已经涨了很多很多了，为什么还说它是启动」。把「这一轮是哪天开始的」算出来，
      界面才能写成「已启动 N 个交易日 · 起点 X · 至今 +Y%」。
      巡检每 5 分钟就会写一次节点，所以这一侧也必须算 —— 否则网页端不刷新时，
      节点里的启动起点会一直停在网页上次刷新的那天。

    返回 days=0 表示**最后一根 K 线不满足启动条件**（例如已经跌破 20 日线），
    这不是"没算出来"，调用方据此显示「—」。
    """
    try:
        close = df['Close']; high = df['High']; vol = df['Volume']
        lookback = 60
        n = len(df)
        if n < lookback + 2:
            return {'days': 0, 'date': '', 'price': 0.0, 'idx': -1}
        plat_high = high.rolling(lookback).max()
        close_high = close.rolling(lookback).max()
        vol5 = vol.rolling(5).mean()
        vol20 = vol.rolling(20).mean()
        with np.errstate(divide='ignore', invalid='ignore'):
            vr = vol5 / vol20
        ok = ((close >= plat_high * 0.995) & (close >= close_high * 0.999)
              & ((vr >= 1.5) | (vol >= vol20 * 1.5)))
        ok = ok.fillna(False).astype(bool)
        ok.iloc[:lookback - 1] = False
        last = n - 1
        if not bool(ok.iloc[last]):
            return {'days': 0, 'date': '', 'price': 0.0, 'idx': -1}
        floor = max(lookback - 1, last - BAND_RUN_MAX_LOOKBACK)
        i = last
        while i > floor and bool(ok.iloc[i - 1]):
            i -= 1
        date = ''
        try:
            if 'Date' in df.columns:
                date = str(df['Date'].iloc[i])[:10]
        except Exception as e:
            _log("_band_run_start/date", e)
        return {'days': int(last - i + 1), 'date': date,
                'price': float(close.iloc[i]), 'idx': int(i)}
    except Exception as e:
        _log("_band_run_start", e)
        return {'days': 0, 'date': '', 'price': 0.0, 'idx': -1}


def _band_metrics(df):
    """计算波段指标（与 ai_stock_terminal.py 的 _calculate_band_metrics 等价）。

    抽出来的原因：原先这套计算只在 check_band_end 里，新增的「波段记忆跟踪」
    也要同一套判定；不共用就会出现「网页说结束、微信没说」这种自相矛盾。
    """
    close = df['Close']; high = df['High']; low = df['Low']; vol = df['Volume']
    current = float(close.iloc[-1])
    if current <= 0:
        return None

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])

    lookback = 60
    platform_high = float(high.tail(lookback).max())
    platform_low = float(low.tail(lookback).min())
    # 突破位 = **突破前**的平台上沿（不含当天）。与 ai_stock_terminal.py 的
    # `_calculate_band_metrics` **同口径**（两边的 docstring 都承诺"等价"，不能漂移）：
    # 含当天的 platform_high 在"今天创新高"时等于现价，做不了突破位。
    breakout_pivot = (float(high.tail(lookback + 1).iloc[:-1].max())
                      if len(df) > lookback else 0.0)
    breakout = current >= platform_high * 0.995 and current >= float(close.tail(lookback).max()) * 0.999

    vol_5 = float(vol.tail(5).mean())
    vol_20 = float(vol.tail(20).mean())
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
    volume_expansion = vol_ratio >= 1.5 or float(vol.iloc[-1]) >= vol_20 * 1.5

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    dea = diff.ewm(span=9, adjust=False).mean()
    macd = 2 * (diff - dea)

    recent = df.tail(60).copy()
    recent['macd'] = macd.tail(60).values
    recent['local_high'] = (recent['High'] > recent['High'].shift(1)) & (recent['High'] > recent['High'].shift(-1))
    highs = recent[recent['local_high']].tail(5)
    top_divergence = False
    top_divergence_gap_pct = 0.0
    if len(highs) >= 2:
        h1, h2 = highs.iloc[-2], highs.iloc[-1]
        if h2['High'] > h1['High'] and h2['macd'] < h1['macd']:
            # 背离幅度：以第一个高点的 MACD 为分母。它小到约等于 0 时分比没有意义
            # → 记 0（= 不给预警），避免出现「分母趋零 ⇒ 幅度无穷大」的假信号。
            _m1 = float(h1['macd'])
            top_divergence_gap_pct = (((_m1 - float(h2['macd'])) / abs(_m1) * 100.0)
                                      if abs(_m1) > 1e-9 else 0.0)
            top_divergence = top_divergence_gap_pct >= BAND_DIVERGENCE_MACD_MIN_PCT

    below_support = current < ma20

    high_250 = float(close.tail(250).max()); low_250 = float(close.tail(250).min())
    position_pct = 50.0 if high_250 <= low_250 else (current - low_250) / (high_250 - low_250) * 100

    # 本轮启动起点（2026-09-22，与主应用同公式）
    _run = _band_run_start(df)

    return {
        'current': current, 'ma20': ma20, 'ma60': ma60,
        'platform_high': platform_high, 'platform_low': platform_low,
        'breakout_pivot': breakout_pivot,
        'breakout': breakout, 'vol_ratio': vol_ratio, 'volume_expansion': volume_expansion,
        'macd': float(macd.iloc[-1]),
        'macd_golden': bool(diff.iloc[-1] > dea.iloc[-1] and diff.iloc[-2] <= dea.iloc[-2]),
        'top_divergence': top_divergence, 'top_divergence_gap_pct': top_divergence_gap_pct,
        'below_support': below_support,
        'position_pct': position_pct, 'lookback': lookback,
        'run_days': _run['days'], 'run_start_date': _run['date'],
        'run_start_price': _run['price'],
        'run_gain_pct': (((current / _run['price']) - 1.0) * 100.0
                         if _run['price'] > 0 else 0.0),
    }


def _band_status(m):
    """与 ai_stock_terminal.py 的 _band_status 判定顺序完全一致。"""
    if m['top_divergence']:
        return '顶背离预警'
    if m['below_support']:
        return '跌破支撑'
    if m['breakout'] and m['volume_expansion']:
        return '波段启动确认'
    if m['current'] > m['ma20']:
        return '波段进行中'
    return '波段未形成'


def _band_alert_decision(old_status, new_status):
    """状态变化要不要打扰用户？返回 'end'（波段结束预警）/ 'start'（波段启动）/ None。

    只对这两类变化推送，其余变化仅记录 —— 否则每次巡检都可能刷屏。
    ⚠️ 与 ai_stock_terminal.py 的 _band_alert_decision 保持同一规则。"""
    if new_status in BAND_ALERT_STATUSES and old_status not in BAND_ALERT_STATUSES:
        return "end"
    if old_status == '波段未形成' and new_status == '波段启动确认':
        return "start"
    return None


def band_snapshot(sym):
    """返回 (状态, 指标dict)；数据不足返回 (None, None)。"""
    df = _get_daily_history(sym)
    if df is None or len(df) < 60:
        return None, None
    m = _band_metrics(df)
    if m is None:
        return None, None
    return _band_status(m), m


def check_band_end(sym, log, today):
    """检查单只股票的日线波段结束信号，同一股票每天只提醒一次。"""
    try:
        status, m = band_snapshot(sym)
        if status is None:
            return False
        if status not in BAND_ALERT_STATUSES:
            return False

        key = f"{sym}_band_end_{today}"
        if key in log.get(today, []):
            return False

        name = get_stock_name(sym)
        reasons = []
        if m['top_divergence']:
            reasons.append("顶背离（股价新高但MACD未新高）")
        if m['below_support']:
            reasons.append(f"跌破20日线支撑（{m['ma20']:.2f}）")

        ok = send_wechat(
            f"【波段结束预警】{name}",
            f"股票：{name} ({sym})\n日期：{today}\n"
            f"依据：{' + '.join(reasons)}\n\n"
            f"该股票波段可能结束，请注意止盈/止损。",
            code=sym, name=name
        )
        if ok:
            log.setdefault(today, []).append(key)
            print(f"  ⚠️ {name}({sym}) 波段结束预警 → 已推送")
            return True
    except Exception as e:
        print(f"  {sym} 波段预警异常: {e}")
    return False


# ============ 波段记忆同步加密（与 ai_stock_terminal.py 同一套规则）============
# 本仓库是 public：band_watch.json 现在是完整记忆（含备注/入选价、股票代码清单），
# 加密是唯一的保密手段。若在两端 Secrets 配了同名的 BAND_KEY，该文件会整段加密后再提交，
# 连代码清单也看不到。
# 为什么不改 Private：public 仓库的 Actions 免费不限量，private 只有 2000 分钟/月，
# 而本项目的巡检 + 保活约需 7800 分钟/月，额度烧穿后 GitHub 会静默停掉定时任务。
# ⚠️ 下面 crypto 三个函数必须与 ai_stock_terminal.py 里的同名函数行为保持一致。

_ENC_FIELD = "enc"
_ENC_VERSION = 1

def band_crypto_key():
    """读加密密钥。巡检侧只认环境变量（由 workflow 从 Secrets 注入 BAND_KEY）。"""
    v = os.environ.get("BAND_KEY", "")
    return str(v).strip() if v and str(v).strip() else ""

def band_crypto_enabled():
    """是否已配置加密密钥。"""
    return bool(band_crypto_key())

def _fernet():
    """构造 Fernet 实例。未配密钥返回 None；配了但不可用则**抛异常**。

    刻意不吞异常：配了 BAND_KEY 却因缺依赖/格式错而悄悄退回明文，
    等于把代码清单原样公开，而这种泄露不会有任何提示。"""
    key = band_crypto_key()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception as e:
        raise RuntimeError(f"已配置 BAND_KEY 但缺少 cryptography 依赖：{e}") from e
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"BAND_KEY 不是合法的 Fernet 密钥（应为 44 字符 base64）：{e}") from e

def band_encrypt_obj(obj):
    """未配密钥 → 原样返回（明文）；配了但加密失败 → 抛异常，由调用方中止写入。"""
    f = _fernet()
    if f is None:
        return obj
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return {"v": _ENC_VERSION, _ENC_FIELD: f.encrypt(raw).decode("ascii")}

def band_decrypt_obj(data):
    """还原摘要对象，返回 (ok, obj 或 None, err)。明文原样返回，兼容历史文件。"""
    if not isinstance(data, dict):
        return False, None, "文件内容不是 JSON 对象"
    token = data.get(_ENC_FIELD)
    if not token:
        return True, data, ""
    try:
        f = _fernet()
    except Exception as e:
        return False, None, str(e)[:160]
    if f is None:
        return False, None, "文件是加密的，但本端没有配置 BAND_KEY"
    try:
        obj = json.loads(f.decrypt(str(token).encode("ascii")).decode("utf-8"))
    except Exception as e:
        return False, None, f"解密失败（两端 BAND_KEY 是否一致？）：{type(e).__name__}"
    if not isinstance(obj, dict):
        return False, None, "解密后的内容不是 JSON 对象"
    return True, obj, ""

# ============ 波段记忆（读网页端同步来的完整镜像 band_watch.json）============

def load_band_memory():
    """读仓库里的波段记忆（由网页端同步过来）。缺失/损坏都退化为空记忆。

    若文件是密文而本端没有 BAND_KEY，会打印醒目横幅并返回空 —— 此时波段监控实际不可用，
    必须让它在 Actions 日志里一眼可见，而不是安静地什么都不推。"""
    try:
        if os.path.exists(BAND_MEMORY_FILE):
            with open(BAND_MEMORY_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            ok, mem, err = band_decrypt_obj(payload)
            if not ok:
                print("=" * 70, file=sys.stderr)
                print(f"[watcher] ⚠️ 波段摘要读取失败：{err}", file=sys.stderr)
                print(f"[watcher]    → 本次无法按「波段记忆」清单监控（文件 {BAND_MEMORY_FILE}）",
                      file=sys.stderr)
                print("[watcher]    → 请确认 GitHub Secrets 有 BAND_KEY，且与网页端 Secrets 一致",
                      file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                _log("load_band_memory:decrypt", ValueError(err))
                return {"version": 1, "stocks": {}}
            if isinstance(mem, dict) and isinstance(mem.get("stocks"), dict):
                return mem
            _log("load_band_memory", ValueError(f"{BAND_MEMORY_FILE} 结构异常，已忽略"))
    except Exception as e:
        _log("load_band_memory", e)
    return {"version": 1, "stocks": {}}


def save_band_memory(mem):
    """把状态变化回写到 band_watch.json（workflow 会检测到 diff 后提交回仓库）。

    ★ 2026-09-20 改，别再改回去：这里原来做「字段裁剪」—— 只写 code/name/status/
      status_ts/last_check/closed/alerts/history 这 8 个白名单字段，理由是
      「这个文件会进 public 仓库」。后果是**每天被巡检覆盖几十次**：网页端刚把带备注的
      完整记忆推上去，这边下一次巡检（5 分钟一次）就把它裁掉，再提交回仓库
      → 用户手写的备注/入选价在仓库里永远留不住，容器一重启就真丢了。

      现在口径与网页端一致（见 app 的 _band_memory_digest）：
      **仓库那份 = 完整记忆，两端结构完全一致**，整节点原样落盘。
      要保密靠 BAND_KEY 加密，不靠删字段。

    配置了 BAND_KEY 时会整段加密后再落盘；没配就是明文（与历史行为一致）。"""
    try:
        mem["updated_at"] = now_cn().strftime('%Y-%m-%d %H:%M:%S')
        clean = {"version": mem.get("version", 1), "updated_at": mem["updated_at"], "stocks": {}}
        for code, node in (mem.get("stocks") or {}).items():
            if not isinstance(node, dict):
                continue
            try:
                # 节点来自 band_decrypt_obj(json.load) 或本进程构建，正常必为 JSON 安全；
                # 这一探只为「万一有怪值」时留下可定位日志，而不是让整次回写莫名失败。
                json.dumps(node, ensure_ascii=False)
                clean["stocks"][code] = dict(node)
            except Exception as e:
                _log("save_band_memory:node", e)
                clean["stocks"][code] = json.loads(
                    json.dumps(node, ensure_ascii=False, default=str))
        out = band_encrypt_obj(clean)
        with open(BAND_MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[watcher] ⚠️ 波段摘要写入失败（本次状态变化不会被提交）：{e}", file=sys.stderr)
        _log("save_band_memory", e)
        return False


def check_band_memory(mem, log, today):
    """按记忆清单检查波段状态变化。返回 (推送条数, 记忆是否被改动)。

    网页端的记忆给出「上次已知状态」，这里负责发现变化并在【关掉网页】时也能推送：
      - 状态恶化到 顶背离预警 / 跌破支撑  → 推「波段结束预警」（最重要）
      - 波段未形成 → 波段启动确认         → 推「波段启动」
      - 其他变化只记录不打扰（否则每天刷屏）
    """
    # ⚠️ 必须取 mem["stocks"] 本身的对象，不能写 `mem.get("stocks") or {}` ——
    # 空字典是 falsy，`or {}` 会返回一个**新**字典，导致下面新建的记录被丢进临时对象、
    # 永远写不回文件（BAND_WATCHLIST 手工名单会因此完全失效）。
    if not isinstance(mem.get("stocks"), dict):
        mem["stocks"] = {}
    stocks = mem["stocks"]
    targets = [c for c, n in stocks.items() if not (n or {}).get("closed")]
    targets += [c for c in BAND_WATCHLIST if c not in stocks]
    if not targets:
        return 0, False

    print(f"波段记忆监控 {len(targets)} 只：{', '.join(targets)}")
    pushed = 0
    changed = False
    for sym in targets:
        try:
            status, m = band_snapshot(sym)
        except Exception as e:
            _log(f"check_band_memory/{sym}", e)
            continue
        if status is None:
            print(f"  ○ {sym} 日线数据不足，本次跳过")
            continue

        node = stocks.get(sym)
        if node is None:
            # BAND_WATCHLIST 里手工加的代码：建一条最小记录
            node = {"code": sym, "name": get_stock_name(sym), "added_at": now_cn().strftime('%Y-%m-%d %H:%M:%S'),
                    "added_price": m['current'], "added_status": status, "added_source": "BAND_WATCHLIST",
                    "snapshot": {}, "note": "", "closed": False, "alerts": {}, "history": []}
            stocks[sym] = node
            changed = True

        old_status = node.get("status")
        node["name"] = node.get("name") or sym
        node["price"] = m['current']
        node["ma20"] = m['ma20']
        node["last_check"] = now_cn().strftime('%Y-%m-%d %H:%M:%S')
        # ★ 本轮启动起点（2026-09-22）：必须写在「状态没变就 continue」**之前** ——
        #   它是滚动回溯出来的，状态不变的日子里它照样会变（这一轮又走了一天）。
        #   用 `is not None` 判存在而不是 `or`：RunDays=0 是**合法值**
        #   （最后一根 K 线已不在启动区），用 `or` 会退回过期旧值，
        #   于是波段早就结束了、界面还写着「已启动 47 个交易日」。
        # ★ 用「键在不在」判，不用 `m.get(...) or 0` —— 后者会把「这次没算出来」
        #   写成「已启动 0 天 / 涨幅 0%」，那是**编数据**。键缺失时保持旧值，
        #   与主应用 `_band_memory_apply` 的 `if _rdays is not None:` 同语义。
        if 'run_days' in m:
            node["run_days"] = int(m.get('run_days') or 0)
            node["run_start_date"] = str(m.get('run_start_date') or '')
            node["run_start_price"] = float(m.get('run_start_price') or 0.0)
            node["run_gain_pct"] = float(m.get('run_gain_pct') or 0.0)

        if status == old_status:
            continue

        node["status"] = status
        node["status_ts"] = now_cn().strftime('%Y-%m-%d %H:%M:%S')
        hist = node.setdefault("history", [])
        hist.append({"ts": node["status_ts"], "status": status, "price": m['current'],
                     "ma20": m['ma20'], "event": "云端巡检"})
        if len(hist) > BAND_MEMORY_HISTORY_MAX:
            node["history"] = hist[-BAND_MEMORY_HISTORY_MAX:]
        changed = True
        print(f"  🔄 {sym} 波段状态 {old_status} → {status}")

        # 决定要不要打扰用户
        to_alert = _band_alert_decision(old_status, status)
        if not to_alert:
            continue

        key = f"{sym}_bandmem_{status}_{today}"
        if key in log.get(today, []):
            continue

        name = node.get("name") or sym
        reasons = []
        if status == '顶背离预警':
            reasons.append("顶背离（股价新高但 MACD 未新高）")
        if status == '跌破支撑':
            reasons.append(f"跌破 20 日线支撑（{m['ma20']:.2f}）")
        title = f"【波段结束预警】{name}" if to_alert == "end" else f"【波段启动】{name}"
        body = (
            f"股票：{name} ({sym})\n"
            f"日期：{today}\n"
            f"状态：{old_status or '（无记录）'} → {status}\n"
            f"现价：{m['current']:.2f}　20日线：{m['ma20']:.2f}\n"
            + (f"依据：{' + '.join(reasons)}\n\n该股票波段可能结束，请注意止盈/止损。"
               if to_alert == "end" else
               "\n该股票出现波段启动信号（来自你的波段记忆清单）。")
        )
        if send_wechat(title, body, code=sym, name=name):
            log.setdefault(today, []).append(key)
            pushed += 1
            print(f"  ⚠️ {name}({sym}) {old_status} → {status} → 已推送微信")
    return pushed, changed


# ============ 主巡检 ============

def _intraday_divergence(df_min):
    """日内 MACD 背离提示（参与置信度判定，也用于主图展示）。

    ⚠️ 本函数在 `ai_stock_terminal.py` 与 `watcher.py` 里**必须逐字同源**。
    """
    info = ""
    try:
        low_idx = df_min['Price'].idxmin()
        if len(df_min.loc[:low_idx]) > 5:
            recent_low = df_min.loc[low_idx, 'Price']; prev_lows = df_min[df_min['Price'] < recent_low * 1.005]
            if len(prev_lows) > 0 and df_min.loc[low_idx, 'MACD'] > df_min.loc[prev_lows.index[0], 'MACD']:
                info += " 底背离"
        high_idx = df_min['Price'].idxmax()
        if len(df_min.loc[:high_idx]) > 5:
            recent_high = df_min.loc[high_idx, 'Price']; prev_highs = df_min[df_min['Price'] > recent_high * 0.995]
            if len(prev_highs) > 0 and df_min.loc[high_idx, 'MACD'] < df_min.loc[prev_highs.index[-1], 'MACD']:
                info += " 顶背离"
    except Exception as e:
        _log("_intraday_divergence", e)
    return info


def check_symbol(sym, market_change, log, today, wl=None):
    """检查单只股票，返回是否触发推送。

    ★ 2026-09-22：日内买卖点先算**置信度**，够不上「最低推送置信度」就只打印、不推微信
      （用户要求「只给我微信推送置信度高的买卖点」）。
    ★ `wl` 由 main() 传下来：门槛就存在白名单文件里，每只票各读一次纯属浪费，
      而且文件读不出来时会逐只刷告警。传 None 时自己读一次（给直接调用的场合）。
    """
    try:
        if wl is None:
            wl, _ = notify_whitelist_load()
        _conf_th = notify_conf_threshold(wl)
        sym_code = get_code(sym)
        df = get_minute_data(sym_code)
        if df is None or df.empty or len(df) < 10:
            return False
        df = df[df['Time'] <= "1500"].copy()
        if len(df) < 10:
            return False
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()

        buy_df = df[(df['Time'] >= "0935") & (df['Time'] <= "1445")].copy()
        sell_df = df[(df['Time'] >= "0930") & (df['Time'] <= "1455")].copy()
        if buy_df.empty or sell_df.empty:
            return False
        buy_df['MACD_UP'] = buy_df['MACD'] > buy_df['MACD'].shift(1)
        sell_df['MACD_DOWN'] = sell_df['MACD'] < sell_df['MACD'].shift(1)

        high = df['Price'].max()
        low = df['Price'].min()
        avg = df['AvgPrice'].mean()
        day_range = max(high - low, 0.001)
        dev = max(0.003, min(((high - low) / avg) * 0.5, 0.015)) if avg > 0 else 0.008
        _div_info = _intraday_divergence(df)

        # 价格位置、止跌/滞涨结构
        buy_df['Price_Position'] = (buy_df['Price'] - low) / day_range
        sell_df['Price_Position'] = (sell_df['Price'] - low) / day_range
        buy_df['STOP_FALL'] = (buy_df['Price'] >= buy_df['Price'].shift(1)) & (buy_df['Price'].shift(1) <= buy_df['Price'].shift(2))
        sell_df['STOP_RISE'] = (sell_df['Price'] <= sell_df['Price'].shift(1)) & (sell_df['Price'].shift(1) >= sell_df['Price'].shift(2))

        # 买点：回踩均价线+MACD向上，或接近日内低点止跌+（MACD向上或缩量）
        buy_cond_a = (buy_df['Price'] < buy_df['AvgPrice'] * (1 - dev * 0.7)) & buy_df['MACD_UP']
        buy_cond_b = (buy_df['Price_Position'] <= 0.30) & buy_df['STOP_FALL'] & (buy_df['MACD_UP'] | (buy_df['Volume'] < buy_df['Vol_MA5'] * 0.85))
        buy_pts = buy_df[buy_cond_a | buy_cond_b]
        # 卖点：冲高乖离均价线+MACD向下，或接近日内高点滞涨+（MACD向下或放量）
        sell_cond_a = (sell_df['Price'] > sell_df['AvgPrice'] * (1 + dev * 0.7)) & sell_df['MACD_DOWN']
        sell_cond_b = (sell_df['Price_Position'] >= 0.70) & sell_df['STOP_RISE'] & (sell_df['MACD_DOWN'] | (sell_df['Volume'] > sell_df['Vol_MA5'] * 1.2))
        sell_pts = sell_df[sell_cond_a | sell_cond_b]

        # 只推送"最近 WINDOW_MIN 分钟内刚形成"的信号，且在本批新信号里取最优价位
        now_min = now_cn().hour * 60 + now_cn().minute

        def minutes_of(t):
            try:
                return int(t[:2]) * 60 + int(t[2:4])
            except Exception:
                return -9999

        def recent_rows(pts):
            """筛出新鲜信号（避免重复推送整天都成立的旧信号）"""
            if pts.empty:
                return pts
            mask = pts['Time'].apply(lambda t: 0 <= now_min - minutes_of(t) <= WINDOW_MIN)
            return pts[mask]

        def key_of(sym, sig, price):
            return f"{sym}_{sig}_{price_bucket(price)}"

        pushed = False
        name = get_stock_name(sym)

        if not buy_pts.empty and market_change >= -1.0:
            recent = recent_rows(buy_pts)
            if not recent.empty:
                row = recent.loc[recent['Price'].idxmin()]
                price = float(row['Price'])
                _conf = intraday_point_confidence('buy', price, float(row['AvgPrice']), dev,
                                                  _div_info, market_change,
                                                  bool(row.get('MACD_UP', False)))
                key = key_of(sym, 'buy', price)
                if not notify_conf_allowed(_conf, _conf_th):
                    # ★ 够不上门槛：**只打印，不推、也不占额度**。
                    #   网页端照旧能看到这个买点 —— 过滤的只是"打扰你"这件事，不是数据。
                    print(f"  ⏸ {name}({sym}) 买点 {price:.3f} 置信度{_conf} "
                          f"< 门槛「{_conf_th}」→ 不推微信")
                elif key not in log.get(today, []):
                    t_str = f"{row['Time'][:2]}:{row['Time'][2:]}"
                    ok = send_wechat(
                        f"【买点·置信度{_conf}】{name}",
                        f"股票：{name} ({sym})\n时间：{t_str}（北京时间）\n"
                        f"价格：{price:.3f}\n置信度：{_conf}\n依据：回踩均价线缩量 + MACD 拐头向上\n"
                        f"偏离均价：{(price / float(row['AvgPrice']) - 1) * 100:.2f}%\n\n"
                        f"仅做参考，请自行判断。",
                        code=sym, name=name
                    )
                    if ok:
                        log.setdefault(today, []).append(key)
                        print(f"  🔴 {name}({sym}) 买点 {price:.3f} @ {t_str} "
                              f"置信度{_conf} → 已推送")
                        pushed = True

        if not sell_pts.empty:
            recent = recent_rows(sell_pts)
            if not recent.empty:
                row = recent.loc[recent['Price'].idxmax()]
                price = float(row['Price'])
                _conf = intraday_point_confidence('sell', price, float(row['AvgPrice']), dev,
                                                  _div_info, market_change,
                                                  bool(row.get('MACD_DOWN', False)))
                key = key_of(sym, 'sell', price)
                if not notify_conf_allowed(_conf, _conf_th):
                    print(f"  ⏸ {name}({sym}) 卖点 {price:.3f} 置信度{_conf} "
                          f"< 门槛「{_conf_th}」→ 不推微信")
                elif key not in log.get(today, []):
                    t_str = f"{row['Time'][:2]}:{row['Time'][2:]}"
                    ok = send_wechat(
                        f"【卖点·置信度{_conf}】{name}",
                        f"股票：{name} ({sym})\n时间：{t_str}（北京时间）\n"
                        f"价格：{price:.3f}\n置信度：{_conf}\n依据：分时卖点触发（冲高乖离或日内高点滞涨）\n"
                        f"偏离均价：{(price / float(row['AvgPrice']) - 1) * 100:+.2f}%\n\n"
                        f"仅做参考，请自行判断。",
                        code=sym, name=name
                    )
                    if ok:
                        log.setdefault(today, []).append(key)
                        print(f"  🟢 {name}({sym}) 卖点 {price:.3f} @ {t_str} "
                              f"置信度{_conf} → 已推送")
                        pushed = True
        return pushed
    except Exception as e:
        print(f"  {sym} 巡检异常: {e}")
        return False


def main():
    print(f"===== 巡检开始 北京时间 {now_cn().strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"🔧 配置检查：SEND_KEY={'已配置' if SEND_KEY else '缺失'} | "
          f"WATCHLIST={len(WATCHLIST)} 只 | BAND_WATCHLIST={len(BAND_WATCHLIST)} 只 | 新鲜窗口={WINDOW_MIN} 分钟")
    # ★ 每次运行都把「谁能自动发 / 还剩几条」打出来：额度被谁吃掉、白名单有没有生效，
    #   事后只能靠这两行日志判。缺了它们，出问题就只剩"猜"。
    _wl0, _wl_ok = notify_whitelist_load()
    _bd0, _bd_ok = notify_budget_state()
    print("🎯 自动推送白名单：" + (
        f"{len(_wl0['items'])} 只（{'、'.join(sorted(notify_whitelist_codes(_wl0)))}）"
        if _wl0['items'] else "空 —— 谁都不自动发（全手动）")
        + ("" if _wl_ok else "　⚠️ 白名单读取失败，已按空名单处理"))
    print(f"⚖️ 最低推送置信度：「{notify_conf_threshold(_wl0)}」"
          "（够不上的日内买卖点只打印、不推微信）")
    print("📊 推送额度：" + notify_budget_line(_bd0)
          + ("" if _bd_ok else "　⚠️ 账本读取失败，本次禁止自动发"))
    if not _wl_ok or not _bd_ok:
        print("   ↑ 这两行出问题时先查 BAND_KEY 是否与网页端/Streamlit Secrets 完全一致")
    print(f"🔒 摘要加密：{'已开启（仓库里是密文）' if band_crypto_enabled() else '未开启（仓库里可读出代码清单）'}")
    if not SEND_KEY:
        print("✗ 未配置 SERVERCHAN_KEY。请到 GitHub 仓库 → Settings → Secrets and variables → "
              "Actions → New repository secret 添加 SERVERCHAN_KEY 后重试。")
        return 0
    mem = load_band_memory()
    mem_codes = [c for c, n in (mem.get("stocks") or {}).items() if not (n or {}).get("closed")]
    if not WATCHLIST and not mem_codes and not BAND_WATCHLIST:
        print("✗ 没有任何监控标的。请在 GitHub Secrets 配置 WATCHLIST（逗号分隔的 6 位代码），"
              "或在网页端「波段记忆」里记入股票并点「同步到云端」。")
        return 0
    if not is_trading_time():
        if not FORCE_RUN:
            print(f"○ 非交易时段，跳过（北京时间 {now_cn().strftime('%H:%M')}）")
            return 0
        print(f"⚠️ FORCE_RUN=1，强制执行巡检（北京时间 {now_cn().strftime('%H:%M')}，非交易时段）")

    log = load_log()
    today = now_cn().strftime('%Y-%m-%d')
    market_change = get_market_change()
    # 北交所标的：腾讯接口不提供分时/日线，这里显式提示并跳过，
    # 避免「配置了却没任何输出」这种最难排查的静默失效。
    bj_syms = [s for s in WATCHLIST if quote_prefix(s) == 'bj']
    if bj_syms:
        print(f"⚠️ 北交所标的 {len(bj_syms)} 只（腾讯不提供行情，本次跳过）：{', '.join(bj_syms)}")
    tradeable = [s for s in WATCHLIST if quote_prefix(s) != 'bj']
    print(f"上证 {market_change:+.2f}% | 日内监控 {len(tradeable)} 只：{', '.join(tradeable) if tradeable else '（无）'}")

    pushed = 0
    for sym in tradeable:
        if check_symbol(sym, market_change, log, today, _wl0):
            pushed += 1

    # 波段结束预警（日线级别，每天同一股票只提醒一次）
    band_alert = 0
    for sym in tradeable:
        if check_band_end(sym, log, today):
            band_alert += 1

    # 波段记忆监控（覆盖网页端记入的清单 + BAND_WATCHLIST）
    mem_pushed, mem_changed = check_band_memory(mem, log, today)
    if mem_changed:
        if save_band_memory(mem):
            print("  💾 波段记忆已回写（workflow 会提交回仓库）")

    save_log(log)
    print(f"===== 巡检结束，共推送 {pushed} 条日内信号 / {band_alert} 条波段结束预警 "
          f"/ {mem_pushed} 条波段记忆变化 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
