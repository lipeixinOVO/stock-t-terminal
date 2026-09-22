import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import re
import base64
import json
import math
import os
import traceback
import sys
import gzip
import hashlib
import time as _time_module
import threading
from datetime import datetime, time, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ✅ 时区修复：Streamlit Cloud 服务器是 UTC 时区，A股相关判断必须统一用北京时间
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    """返回北京时间 datetime（部署在海外服务器上也能正确判断交易时段）"""
    return datetime.now(CN_TZ)

def now_cn_str(fmt='%Y-%m-%d %H:%M:%S'):
    return now_cn().strftime(fmt)

def _log(where, err):
    """统一的可观测性出口：把原本被 except 静默吞掉的异常写到 stderr。

    历史教训：动态池里 get_stock_historical_metrics 未定义，NameError 被
    `except Exception: return code_, None, None` 吞掉，导致评分静默失效数月无人发现。
    凡是要吞异常，必须先经过这里留痕。"""
    try:
        print(f"[stock-t][{where}] {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)
    except Exception:
        pass

# ✅ 打开页面后默认显示的标的：中国巨石 (600176)
DEFAULT_STOCK = '600176'

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

# ================= 1. 页面配置 =================
st.set_page_config(page_title="日内做T信号标注助手", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    h1, h2, h3 { color: #f0f2f6; font-family: 'Microsoft YaHei'; }
    .report-box { background-color: #1e1e2e; padding: 12px 18px; border-radius: 8px; font-family: 'Consolas', monospace; font-size: 15px; line-height: 1.6; margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; }
    .report-row { display: flex; gap: 20px; flex-wrap: wrap; }
    .color-red { color: #ff4b4b; font-weight: bold; }
    .color-green { color: #00cc66; font-weight: bold; }
    .color-blue { color: #89b4fa; font-weight: bold; }
    .color-white { color: #f0f2f6; }
    .color-yellow { color: #f9e2af; font-weight: bold; }
    .ai-advice-box { background-color: #2b2b3b; padding: 15px; border-left: 5px solid #ffaa00; border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px; }
    .guide-box { background-color: #1a2b1a; padding: 15px; border-left: 5px solid #00cc66; border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px; }
    .predict-box { background-color: #2b1a1a; padding: 15px; border-left: 5px solid #ff4b4b; border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px; }
    /* 今日盘面动态分析（2026-09-20 新增）：蓝边，和绿边「做T指引」、红边「极值预测」区分开 */
    .struct-box { background-color: #16212e; padding: 15px; border-left: 5px solid #4aa3ff; border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px; }
    .struct-tag { display: inline-block; margin-left: 8px; padding: 1px 9px; border-radius: 10px; background-color: #24384f; color: #9fd0ff; font-size: 13px; }
    .struct-line { margin-top: 9px; line-height: 1.75; }
    .struct-scen { margin-top: 9px; line-height: 1.75; color: #dfe6ef; }
    .struct-warn { margin-top: 9px; line-height: 1.75; color: #ffd166; }
    /* ★ 2026-09-21：「当前正在走哪一条情景」的标注（用户要求「动态标注是哪种状态」）。
       颜色沿用全站的 A 股口径：涨=红、跌=绿（见 .color-red / .color-green 与 K 线 increasing/decreasing）。*/
    .struct-now { margin-top: 9px; line-height: 1.75; background-color: #1d3145; border-left: 3px solid #4aa3ff; border-radius: 5px; padding: 6px 10px; color: #dcebff; }
    .struct-tag-now-up { background-color: #4a1d1d !important; color: #ffb3b3 !important; font-weight: bold; }
    .struct-tag-now-flat { background-color: #4a3f1d !important; color: #ffd166 !important; font-weight: bold; }
    .struct-tag-now-down { background-color: #1f4d33 !important; color: #8ff0b5 !important; font-weight: bold; }
    .scen-now { background-color: #2f4a66; color: #ffffff; padding: 0 7px; border-radius: 9px; font-weight: bold; }
    div.stButton > button[kind="primary"] { background-color: #1f6feb; color: white; border: none; font-weight: bold; }
    div.stButton > button[kind="secondary"] { background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
    @media (min-width: 992px) {
        div[data-testid="stAppViewBlockContainer"] > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"]:last-child { position: fixed !important; top: 70px !important; right: 20px !important; width: 400px !important; max-height: 88vh !important; background-color: #1e1e2e !important; border: 1px solid #30363d !important; border-radius: 12px !important; padding: 12px !important; z-index: 9999 !important; box-shadow: 0 8px 24px rgba(0,0,0,0.6) !important; overflow-y: auto !important; }
        .main .block-container { padding-right: 440px !important; }
    }
    @media (max-width: 992px) {
        div[data-testid="stAppViewBlockContainer"] > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"]:last-child { position: static !important; width: 100% !important; max-height: none !important; margin-top: 20px !important; }
        .main .block-container { padding-right: 1rem !important; }
    }
    .ma-bar { background-color: #161b22; padding: 8px 15px; border-radius: 6px; font-family: 'Consolas', monospace; font-size: 14px; display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 5px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🤖 日内做T信号标注助手")

# ============ 长任务 × 自动刷新 的互斥（★ 别动这里，这是"跑一会就得再点一次"的修复）============
# 根因（已实测确认）：本页顶部有一个全局 st_autorefresh(60 秒)。长任务（采集/扫描/复盘）
# 要跑 1~3 分钟，而**按钮点击后的那次运行会把这个 60 秒计时器装上**；到第 60 秒计时器触发
# rerun，Streamlit 会在**下一个 st.* 调用**处中断正在跑的那次运行（采集的进度回调每 20 只
# 就调一次 st.progress，几乎立刻被打断）。中断后按钮的"按下"状态已经消失——它只在被点击的
# 那一次运行里为 True——于是活停在一半、结果也没落盘，用户只能再点一次。
# 因为是**全局**计时器，所以几乎所有的长任务都是这个毛病，不是单个按钮写错了。
# 修法：把"点击"和"执行"拆成两次运行。点击那次只登记待办 + 立刻 rerun；下一次运行发现有待办
# 就把自动刷新间隔放宽到 60 分钟（组件照旧渲染 → 组件自带的 debounce 会 clearInterval 掉那根
# 60 秒的定时器，不会出现"自动刷新被永久关掉"），这一次运行就砍不到了，可以安心把活干完。
# ★ 这个窗口必须**大于最长任务的耗时**，否则等于换了个时间点再砍一次：全市场采集改造前
#   要 100 分钟以上，原来的 30 分钟窗口根本不够（实测就发生了）。现已把取数改成
#   「连接复用 + 8 线程并发」（实测两项合计约 47 倍），全市场降到十几分钟以内，
#   窗口同时放宽到 60 分钟，两头都留了余量。改这个值之前先确认最长任务要跑多久。
_LONG_TASK_KEY = "_long_task_pending"
_LONG_TASK_TTL = 120             # 秒；待办超过这个时长视为作废，避免状态卡死
_AUTOREFRESH_MS = 60000          # 正常：60 秒
_AUTOREFRESH_BUSY_MS = 3600000   # 有待办：60 分钟（等价于暂停，但组件仍在 → 不会冻结页面）

def _long_task_pending():
    """当前是否有一个"即将执行"的长任务。顺带清理过期待办。

    任何一次用户交互都会走到这里，所以即使状态因为异常而卡住，也会自愈回 60 秒刷新。"""
    p = st.session_state.get(_LONG_TASK_KEY)
    if not p:
        return False
    try:
        if _time_module.time() - float(p.get("ts", 0)) > _LONG_TASK_TTL:
            st.session_state[_LONG_TASK_KEY] = None
            return False
    except Exception as e:
        _log("_long_task_pending/stale", e)
        st.session_state[_LONG_TASK_KEY] = None
        return False
    return True

def long_button(label, key, container=None, **kw):
    """长任务专用按钮：**点一次就够**。用法与 st.button 完全一致，只是把触发拆成两次运行。

    第 1 次（被点击的那次运行）：只登记待办 + st.rerun()，立刻返回，界面马上有反应；
    第 2 次：顶部已把自动刷新放宽到 30 分钟 → 本次运行不会被打断 → 安心把活干完。
    调用方原有的 `if <按钮>:` 与末尾的 st.rerun() 都不用改。

    ⚠️ 凡是"会跑几十秒以上"的动作都该用它；秒级动作继续用 st.button 就行（多一次 rerun 没必要）。"""
    c = container if container is not None else st
    if c.button(label, key=key, **kw):
        st.session_state[_LONG_TASK_KEY] = {"key": key, "ts": _time_module.time()}
        st.rerun()
    p = st.session_state.get(_LONG_TASK_KEY)
    if p and p.get("key") == key:
        st.session_state[_LONG_TASK_KEY] = None   # 先清掉：干完活后调用方的 rerun 会恢复 60 秒刷新
        return True
    return False

if HAS_AUTOREFRESH:
    _busy = _long_task_pending()
    st_autorefresh(interval=(_AUTOREFRESH_BUSY_MS if _busy else _AUTOREFRESH_MS),
                   key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情，全自选股监控）"
               + ("　⏳ 长任务进行中，定时刷新已临时让路（60 秒的刷新会打断长任务）"
                  if _busy else ""))
else:
    st.caption("⚠️ 未安装自动刷新组件，请按 F5 手动刷新网页")

# ================= 2. 本地持久化存储 + Secrets 支持 =================
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(BASE_DIR): raise ValueError("invalid dir")
except Exception: BASE_DIR = os.getcwd()

WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DYNAMIC_POOL_FILE = os.path.join(BASE_DIR, "dynamic_pool.json")
NOTIFY_LOG_FILE = os.path.join(BASE_DIR, "notify_log.json")
BAND_MANUAL_FILE = os.path.join(BASE_DIR, "band_manual_list.json")
# 波段记忆：把扫描出的「值得跟踪」的股票持久化，并记录状态变化轨迹。
# 本文件是本地权威副本（含手写备注、入选价），不单独提交（已加入 .gitignore）。
# ★ 仓库里的 band_watch.json 是它的**完整镜像**（不再做字段裁剪），见 _band_memory_digest()。
BAND_MEMORY_FILE = os.path.join(BASE_DIR, "band_memory.json")
# 策略复盘的批次档案（哪一批选了哪些票、每笔结案结果与归因）。
# 本地文件，不单独提交；目前**也**没有并进 band_watch.json —— 理由不是保密，而是：
#   ① 结案结果是按日线回溯重算出来的，点一下「🔄 重算」就能复现，不存在"丢了就找不回"；
#   ② 多一条同步链路就多一处冲突要处理。
# 真要它也跨容器存活，把 `batches` 一起并进 _band_memory_digest() 即可。
BAND_BATCHES_FILE = os.path.join(BASE_DIR, "band_batches.json")

def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e:
        # 静默返回 default 会造成"配置/自选股莫名恢复出厂"这类无从排查的现象
        _log(f"_load_json({os.path.basename(path)})", e)
    return default

def _save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        _log("_save_json", e)   # 磁盘只读 / 写满时不再完全无声

def load_watchlist():
    try:
        raw = st.secrets.get("WATCHLIST", "")
        if raw and isinstance(raw, str):
            codes = [c.strip() for c in raw.split(",") if c.strip()]
            if codes: return codes
    except Exception: pass          # 本地无 secrets.toml 属预期情况，不刷日志
    return _load_json(WATCHLIST_FILE, [DEFAULT_STOCK, '515880', '159915'])

def save_watchlist(lst):
    _save_json(WATCHLIST_FILE, lst)   # _save_json 内部已捕获并留痕，无需再包一层

def load_config():
    cfg = _load_json(CONFIG_FILE, {})
    try:
        ak = st.secrets.get("DEEPSEEK_API_KEY", "")
        if ak and isinstance(ak, str): cfg['api_key'] = ak
    except Exception: pass          # 同上：本地无 secrets.toml 属预期情况
    try:
        sk = st.secrets.get("SERVERCHAN_KEY", "")
        if sk and isinstance(sk, str): cfg['send_key'] = sk
    except Exception: pass
    return cfg

def save_config(cfg):
    _save_json(CONFIG_FILE, cfg)      # _save_json 内部已捕获并留痕

def _secrets_has_key(name):
    try:
        v = st.secrets.get(name, "")
        return bool(v and isinstance(v, str))
    except Exception: return False

def load_dynamic_pool(): return _load_json(DYNAMIC_POOL_FILE, {})
def save_dynamic_pool(pool_dict): _save_json(DYNAMIC_POOL_FILE, pool_dict)

def load_band_manual(): return _load_json(BAND_MANUAL_FILE, [])
def save_band_manual(lst): _save_json(BAND_MANUAL_FILE, lst)

# 预定义常见板块（用代表性成分股列表，避免实时板块接口不稳定）
BAND_BOARD_MAP = {
    "半导体": ["600584", "002049", "603501", "600745", "002371", "603986", "600460", "002156"],
    "人工智能": ["000938", "002230", "600728", "000977", "002236", "600570", "603019", "600100"],
    "新能源": ["002594", "600438", "601012", "603659", "300014", "002074", "002812", "600884"],
    "银行": ["600000", "601398", "601288", "601939", "600036", "601166", "601988", "601328"],
    "白酒": ["600519", "000858", "002304", "600809", "000568", "600702", "600779", "603369"],
    "医药": ["600276", "000538", "600436", "600196", "002001", "600079", "603259", "000963"],
    "券商": ["600030", "601688", "600837", "601211", "600999", "000776", "601377", "002500"],
    "军工": ["600893", "600372", "002179", "600760", "000768", "600391", "002025", "600482"],
    "消费电子": ["002475", "601138", "002600", "000100", "002241", "603501", "688036", "300433"],
    "汽车": ["601127", "000625", "601633", "600660", "002048", "600104", "000951", "600066"],
    "光伏": ["600438", "601012", "603806", "002129", "600732", "002459", "601865", "688599"],
    "地产": ["000002", "600048", "600606", "001979", "600383", "600340", "000069", "600657"],
    "电力": ["600900", "600011", "600886", "601985", "600795", "600027", "601991", "000027"],
    "有色": ["601899", "603993", "600362", "002460", "000630", "600497", "600111", "601600"],
    "通信": ["600941", "600498", "000063", "600487", "601728", "600522", "002281", "300308"],
}

# ================= 3. 通知去重机制 =================
def _load_notify_log(): return _load_json(NOTIFY_LOG_FILE, {})
def _save_notify_log(log): _save_json(NOTIFY_LOG_FILE, log)
def _prune_notify_log(log):
    today = now_cn().strftime('%Y-%m-%d')
    return {today: log.get(today, [])}

def _price_bucket(price, pct=0.005):
    try:
        if price <= 0: return 0
        return int(math.log(max(price, 0.001)) / math.log(1 + pct))
    except Exception as e:
        # 坏价格落到 0 号桶会引发去重碰撞（该推的没推 / 重复推），必须留痕
        _log("_price_bucket", e)
        return 0

def make_notify_key(symbol, signal_type, price): return f"{symbol}_{signal_type}_{_price_bucket(price)}"

def should_notify(symbol, signal_type, price):
    log = _prune_notify_log(_load_notify_log())
    return make_notify_key(symbol, signal_type, price) not in log.get(now_cn().strftime('%Y-%m-%d'), [])

def mark_notified(symbol, signal_type, price):
    log = _prune_notify_log(_load_notify_log())
    key = make_notify_key(symbol, signal_type, price)
    today = now_cn().strftime('%Y-%m-%d')
    if today not in log: log[today] = []
    if key not in log[today]: log[today].append(key)
    _save_notify_log(log)

# ================= 3.1 ★ 微信推送「每日限额」（2026-09-22 新增）=================
# 用户原话：「因为我每天微信通知只有五条的限制，留下一条给六点的通知，
#   剩下的全部交给我来手动选择」。
# 所以：**所有自动推送一律关掉** —— 网页端 monitor_all_watchlist 不再发，
# 云端巡检 watcher 也不再发（见 watcher.send_wechat），只留 18:00 日报那一条。
# 剩 4 条由用户在「📤 今日推送」面板里点哪条发哪条。
#
# 账本 `notify_budget.json` 提交进仓库（2026-09-22 用户同意）：网页端容器一重启本地
# 文件就没了，计数归零会让用户点到第 6 条被 Server酱 直接拒。
# 与 band_watch.json 同一套保密规则：配了 BAND_KEY 就整段加密 ——
# 否则「今天推了哪几只票」会以明文出现在 public 仓库里。
NOTIFY_BUDGET_FILE = os.path.join(BASE_DIR, "notify_budget.json")
GITHUB_BUDGET_PATH = "notify_budget.json"
NOTIFY_LIMIT_DEFAULT = 5        # Server酱 免费版每天 5 条（硬限制在服务端，这边只是刹车）
NOTIFY_RESERVE_DEFAULT = 1      # 其中 1 条预留给 18:00 日报
NOTIFY_LIMIT_MAX = 20           # 可调，但别声称能超过服务端配额
# 候选池：只有这三种状态值得打扰用户
NOTIFY_CAND_STATUSES = ('跌破支撑', '顶背离预警', '波段启动确认')


def _clamp_int(v, lo, hi, default):
    """收敛进 [lo, hi]。坏值落到 default（**不是** lo）。"""
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
    ★ reserved 必须 ≤ limit：否则手动额度算成负数，界面会出现"还能推 -1 条"。
    ★ sent 只保留结构正确的条目：坏条目会让计数与实际推送数不符（额度算错）。
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


def notify_budget_manual_left(b):
    """手动还能推几条 = 上限 − 预留（日报）− 已用。"""
    nb = notify_budget_normalize(b)
    return max(0, nb["limit"] - nb["reserved"] - len(nb["sent"]))


def notify_budget_line(b):
    """界面用的一行话。数字只从账本算一次，不在别处另算。"""
    nb = notify_budget_normalize(b)
    left = notify_budget_manual_left(nb)
    txt = (f"今日已用 **{len(nb['sent'])} / {nb['limit']}** 条"
           f"（{nb['reserved']} 条预留 18:00 日报）→ 手动还能推 **{left}** 条")
    return txt + ("　⚠️ 手动额度已用完，今天不再推送" if left == 0 else "")


def notify_budget_load_local():
    return notify_budget_normalize(_load_json(NOTIFY_BUDGET_FILE, {}))


def notify_budget_save_local(b):
    _save_json(NOTIFY_BUDGET_FILE, b)


def notify_budget_set(b):
    """更新会话缓存 + 本地文件（不提交远端，提交由调用方决定）。"""
    nb = notify_budget_normalize(b)
    st.session_state['notify_budget'] = nb
    notify_budget_save_local(nb)
    return nb


def notify_budget_get():
    """取当前账本。**默认不打网络** —— 本地文件（云端就是仓库检出那份）已经最新：
    只有本应用会写账本，而每次写入都同时落本地 + 提交远端，所以本地 ≥ 远端提交版。
    要拉远端就调 notify_budget_pull()（设置区那个「从云端刷新」按钮）。
    """
    cached = st.session_state.get('notify_budget')
    if isinstance(cached, dict):
        return cached
    nb = notify_budget_load_local()
    st.session_state['notify_budget'] = nb
    return nb


def notify_budget_pull():
    """从仓库读账本，返回 (ok, msg, budget 或 None)。

    远端不存在（404）→ 当空账本（首次启用时的正常情况，不是错误）。
    ★ 远端存在但**读不出来**（没密钥 / 密钥不对）时返回 ok=False、budget=None ——
      绝不能当空账本！那等于把当日已用条数清零，用户会超发。
    """
    if not _github_token():
        return False, "未配置 GitHub Token", None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_BUDGET_PATH}"
    try:
        r = requests.get(url, headers=_github_headers(_github_token()),
                         params={"ref": "main"}, timeout=(5, 15))
    except Exception as e:
        _log("notify_budget_pull", e)
        return False, f"拉取异常：{type(e).__name__}: {str(e)[:100]}", None
    if r.status_code == 404:
        return True, "仓库里还没有通知账本（首次推送时创建）", None
    if r.status_code != 200:
        return False, f"拉取失败 HTTP {r.status_code}", None
    try:
        raw = base64.b64decode((r.json() or {}).get("content") or "").decode("utf-8")
        data = json.loads(raw)
    except Exception as e:
        _log("notify_budget_pull:decode", e)
        return False, f"账本解码失败：{str(e)[:100]}", None
    ok, obj, err = band_decrypt_obj(data)
    if not ok or obj is None:
        return False, f"账本读取失败：{err}", None
    return True, "", notify_budget_normalize(obj)


def notify_budget_push(budget):
    """把账本提交到仓库，返回 (ok, msg)。加密与冲突重试规则同 band_watch.json。"""
    token = _github_token()
    if not token:
        return False, "未配置 GitHub Token（账本无法同步，云端重启后今日计数会丢）"
    try:
        payload_obj = band_encrypt_obj(budget)
    except Exception as e:
        _log("notify_budget_push:encrypt", e)
        return False, f"加密失败，已中止同步（不会以明文提交）：{str(e)[:120]}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_BUDGET_PATH}"
    content = base64.b64encode(json.dumps(payload_obj, ensure_ascii=False,
                                         indent=2).encode("utf-8")).decode("ascii")
    n = len(budget.get("sent") or [])
    for attempt in (1, 2):
        try:
            sha = None
            r = requests.get(url, headers=_github_headers(token),
                             params={"ref": "main"}, timeout=(5, 15))
            if r.status_code == 200:
                sha = (r.json() or {}).get("sha")
            body = {"message": f"更新微信推送账本（今日 {n} 条）",
                    "content": content, "branch": "main"}
            if sha:
                body["sha"] = sha
            r = requests.put(url, headers=_github_headers(token), json=body, timeout=(5, 25))
            if r.status_code in (200, 201):
                return True, "已同步"
            if r.status_code in (409, 422) and attempt == 1:
                continue        # sha 过期（别处刚提交过），重取后再试一次
            return False, f"同步失败 HTTP {r.status_code}: {r.text[:150]}"
        except Exception as e:
            _log("notify_budget_push", e)
            if attempt == 2:
                return False, f"同步异常：{type(e).__name__}: {str(e)[:100]}"
    return False, "同步失败（已重试）"


def notify_send_key():
    """当前可用的 Server酱 SendKey：Secrets 优先，其次侧边栏/本地配置。"""
    try:
        k = st.secrets.get("SERVERCHAN_KEY", "")
        if k and isinstance(k, str):
            return k.strip()
    except Exception:
        pass        # 本地无 secrets.toml 属预期情况
    try:
        return str(st.session_state.get('send_key') or (load_config().get('send_key') or '')).strip()
    except Exception as e:
        _log("notify_send_key", e)
        return ""


def notify_send_guarded(title, content, kind, code="", name=""):
    """★ 微信推送的**唯一出口**：先校验额度 → 再发送 → 成功才记账。

    顺序是刻意的，别改成"先发后记"：
      · 先发后记 → 发送失败也扣了额度，用户白白少一条；
      · 只校验不记账 → 连点两下就超发（每次点击都只是一次 rerun）。
    寄文失败不回滚计数：消息已经发出去了，这时把账本改回去才是真的对不上。
    返回 (ok, msg)，ok=False 时 msg 是给用户看的原因。
    """
    b = notify_budget_get()
    if notify_budget_manual_left(b) <= 0:
        nb = notify_budget_normalize(b)
        return False, (f"今日手动额度已用完（上限 {nb['limit']} 条，其中 {nb['reserved']} 条"
                       f"留给 18:00 日报，已推 {len(nb['sent'])} 条）。"
                       "要再多发，去左侧「📱 微信提醒」把上限调高。")
    key = notify_send_key()
    if not key:
        return False, ("未配置 Server酱 SendKey —— 左侧「📱 微信提醒」里填，"
                       "或放到 Streamlit Secrets 的 SERVERCHAN_KEY")
    if not send_wechat_notification(key, title, content):
        return False, ("发送失败：Server酱 没返回 200。可能是 SendKey 失效，"
                       "也可能今天的 5 条在别处已经用掉了。")
    nb = notify_budget_normalize(b)
    nb["sent"].append({"ts": now_cn_str('%Y-%m-%d %H:%M:%S'), "kind": str(kind),
                       "code": str(code), "name": str(name), "title": title})
    nb["updated_at"] = now_cn_str('%Y-%m-%d %H:%M:%S')
    nb = notify_budget_set(nb)
    _ok, _m = notify_budget_push(nb)
    tail = "" if _ok else f"（账本同步失败：{_m}）"
    return True, f"✅ 已推送「{title}」；今日已用 {len(nb['sent'])}/{nb['limit']} 条{tail}"


def notify_candidates(mem):
    """把记忆里「值得提醒」的票整理成候选清单（预警在前、启动在后）。

    ★ 为什么从记忆里取、而不是等巡检上报：巡检现在只更新状态、不推送，
      而状态本来就落在 band_watch.json 里 —— 网页端同步后就有，不必另造上报通道。
    排序直接用 BAND_STATUS_LEVEL（预警等级高于启动），不另立一套顺序。
    """
    out = []
    _stocks = mem.get('stocks') if isinstance(mem, dict) else None
    if not isinstance(_stocks, dict):
        _stocks = {}
    for code, node in _stocks.items():
        if not isinstance(node, dict):
            continue
        status = node.get('status')
        if status not in NOTIFY_CAND_STATUSES or node.get('closed'):
            continue
        name = node.get('name') or str(code)
        price = _band_num(node.get('price'))
        pivot = band_breakout_pivot(node)
        defense = band_defense_price(node)
        ts = node.get('status_ts') or ''
        is_end = status in ('跌破支撑', '顶背离预警')
        # 本轮启动标注（2026-09-22）：推送候选上也写清「已启动 N 日 · 至今 +X%」，
        # 免得在微信里看到「波段启动」四个字又误以为它今天才启动。
        _run_txt = band_run_text(node)
        out.append({
            "code": str(code), "name": name, "status": status,
            "kind": "band", "price": price, "pivot": pivot, "defense": defense, "ts": ts,
            "run": _run_txt, "score": float(node.get('score') or 0.0),
            "title": (f"【波段结束预警】{name}" if is_end
                      else f"【波段启动】{name}"),
            "body": (f"股票：{name}（{code}）\n状态：{status}\n"
                     f"现价：{_fmt_price(price)}　突破位：{_fmt_price(pivot)}"
                     f"　防守（20 日线）：{_fmt_price(defense)}\n"
                     + (f"{_run_txt}\n" if _run_txt else "")
                     + f"状态时间：{ts}\n\n仅做参考，请自行判断。"),
        })
    # 同一档状态内**按前景分降序**（2026-09-22 用户要求：越靠前越有前景）；
    # 状态层级仍是第一关键字 —— 预警必须排在最前，那是"该动手"的信号，
    # 与"哪只更有前景"是两件事。
    out.sort(key=lambda c: (-BAND_STATUS_LEVEL.get(c['status'], 0),
                            -c.get('score', 0.0), c['code']))
    return out


def _notify_row(c, exhausted, already, gkey, idx):
    """一行候选 + 一个推送按钮。`already`=今天已推过 → 不给按钮（免得重复占额度）。

    ★ 按钮 key 必须带 gkey + idx：同一只票可能同时出现在两组里，
      光用 code 会 StreamlitDuplicateElementKey 把整页打崩。
    """
    _col, _act = st.columns([3.4, 1.0])
    with _col:
        _bits = []
        if c.get('price'):
            _bits.append(f"现价 {_fmt_price(c['price'])}")
        if c.get('pivot'):
            _bits.append(f"突破位 {_fmt_price(c['pivot'])}")
        if c.get('defense'):
            _bits.append(f"防守 {_fmt_price(c['defense'])}")
        _meta = "　".join(_bits)
        st.markdown(
            f"**{c.get('name')}（{c.get('code')}）** "
            f"<span style='color:#89b4fa;'>{c.get('status') or c.get('kind')}</span>"
            + (f"　<span style='color:#9aa0a6;font-size:12px;'>{_meta}</span>" if _meta else ""),
            unsafe_allow_html=True)
        if c.get('ts'):
            st.caption(f"状态时间 {c['ts']}")
    with _act:
        if already:
            st.caption("✅ 今天已推")
        elif st.button("📤 推送", key=f"notify_push_{gkey}_{idx}",
                       use_container_width=True, disabled=exhausted,
                       help=("今日额度已用完" if exhausted
                             else "立刻发一条微信（占今日 1 条额度）")):
            _ok, _m = notify_send_guarded(c.get('title') or "", c.get('body') or "",
                                          kind=c.get('kind') or 'band',
                                          code=c.get('code'), name=c.get('name'))
            if _ok:
                st.session_state['notify_flash'] = _m
                st.rerun()
            else:
                st.error(_m)


def notify_center_ui(mem):
    """📤 今日推送：额度 + 候选 + 手动推送（2026-09-22 按用户要求新建）。

    用户原话：「因为我每天微信通知只有五条的限制，留下一条给六点的通知，
    剩下的全部交给我来手动选择」。
    """
    st.markdown("---")
    st.subheader("📤 今日推送（手动）")
    _b = notify_budget_get()
    st.caption("额度：" + notify_budget_line(_b))
    st.caption("🚫 自动推送已全部关闭 —— 云端巡检和本页都不再自己发微信，"
               "下面每个候选都由你点才发。"
               "18:00 日报照旧（它占预留的那一条）。")
    _left = notify_budget_manual_left(_b)
    _sent_codes = {str(it.get('code')) for it in notify_budget_normalize(_b)['sent']}
    _cands = notify_candidates(mem)
    _intra = list(st.session_state.get('manual_push_candidates') or [])
    if _left <= 0:
        st.warning("今日手动额度已用完 —— 想再推只能把上限调高，或等明天。")
    if not _cands and not _intra:
        st.caption("（现在没有可推的候选：记忆里没有处于预警/启动状态的票，"
                   "本页巡检也没发现新的日内信号。）")
        return
    if _intra:
        st.markdown(f"**日内信号（本页巡检发现，{len(_intra)} 条）**")
        for _i, _c in enumerate(_intra):
            _notify_row(_c, _left <= 0, str(_c.get('code')) in _sent_codes, "intra", _i)
    if _cands:
        st.markdown(f"**波段候选（来自「🧠 波段记忆」，{len(_cands)} 条）**")
        for _i, _c in enumerate(_cands):
            _notify_row(_c, _left <= 0, _c['code'] in _sent_codes, "band", _i)

# ---------- 📰 18:00 日报上网页（2026-09-22）----------

def digest_last_load_local():
    """读本地那份日报正文（可能是密文）→ (data 或 None, err)。"""
    if not os.path.exists(DIGEST_LAST_FILE):
        return None, (f"本地还没有 {os.path.basename(DIGEST_LAST_FILE)}"
                      "（云端日报任务跑过一次之后才会有）")
    try:
        with open(DIGEST_LAST_FILE, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None, "日报文件不是 JSON 对象"
        ok, data, err = band_decrypt_obj(payload)
        if not ok:
            # ★ 与 band_watch.json 同一条红线：解不开就**明确报错**，
            #   绝不静默当成"今天没有日报"（那会让人以为日报没生成）。
            return None, f"日报解不开：{err}"
        if not isinstance(data, dict) or not str(data.get("text") or "").strip():
            return None, "日报内容为空或结构异常"
        return data, ""
    except Exception as e:
        _log("digest_last_load_local", e)
        return None, f"日报读取失败：{e}"


def digest_last_fetch_remote():
    """从仓库拉最新那份日报 → (ok, msg, data 或 None)。"""
    if not _github_token():
        return False, "未配置 GitHub Token（读云端日报要用它，和记忆同步是同一个）", None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DIGEST_PATH}"
    try:
        r = requests.get(url, headers=_github_headers(_github_token()),
                         params={"ref": "main"}, timeout=(5, 15))
        if r.status_code == 404:
            return False, f"仓库里还没有 {GITHUB_DIGEST_PATH}（云端日报任务跑过之后才会创建）", None
        if r.status_code != 200:
            return False, f"拉取失败 HTTP {r.status_code}: {r.text[:120]}", None
        raw = base64.b64decode(r.json().get("content") or "").decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False, "云端日报结构异常，已忽略", None
        ok, data, err = band_decrypt_obj(payload)
        if not ok:
            return False, f"云端日报解不开：{err}", None
        if not isinstance(data, dict) or not str(data.get("text") or "").strip():
            return False, "云端日报内容为空", None
        return True, f"已拉取 {data.get('date') or '?'} 的日报", data
    except Exception as e:
        _log("digest_last_fetch_remote", e)
        return False, f"拉取异常：{type(e).__name__}: {str(e)[:100]}", None


def _digest_last_resolve():
    """这次显示哪一份 → (data 或 None, err, 来源文案)。

    优先用「已经是今天的」本地副本 —— Streamlit Cloud 每次提交都会重新部署，
    所以仓库里那份通常就在本地磁盘上，读它零成本也零延迟。
    只有「不是今天的 / 本地压根没有」才去云端拉一次，而且**限制频率**
    （DIGEST_LAST_TTL），否则每点一次按钮都会打一次 GitHub API。
    """
    data, err = digest_last_load_local()
    _today = now_cn_str('%Y-%m-%d')
    if data and str(data.get('date') or '') == _today:
        return data, "", "本地仓库副本"
    _last = 0.0
    try:
        _last = float(st.session_state.get('digest_last_try_at') or 0)
    except Exception as e:
        _log("_digest_last_resolve/ts", e)
    # ★ 必须用 _time_module —— 本模块顶部有 `from datetime import ... time ...`，那个 `time` 是**类**、
    #   不是模块，所以 `time.time()` 会 AttributeError（2026-09-22 实测踩到，页面把它吞成了「跟踪清单渲染出错」）。
    #   那个 time 是**类**，不是模块 —— 写 time.time() 会 AttributeError（已实测踩到）。
    if _time_module.time() - _last < DIGEST_LAST_TTL:
        return data, err, "本地仓库副本"
    st.session_state['digest_last_try_at'] = _time_module.time()
    ok, msg, rdata = digest_last_fetch_remote()
    if ok and rdata:
        return rdata, "", "云端最新"
    # 拉不到就退回本地那份 —— 但把原因如实写出来，不做静默降级
    return data, ((err + "；") if err else "") + (msg or ""), "本地仓库副本"


def digest_last_ui():
    """📰 18:00 日报（网页端回看）—— 2026-09-22 按用户要求新增。

    用户原话：「每天六点的总结 万一说没有微信通知的机会了的话 怎么办？
    最好在网页中有地方可以呈现，让我无论是在微信上还是在网页上都能看到」。

    微信每天只有 5 条额度、日报要预留的那 1 条也可能因为别的原因发不出去。
    所以云端日报任务把**同一条正文**加密写进仓库，这里解密后**原样渲染**。
    ★ 刻意不在这里重算一份：重算就会有两个版本（网页版 / 微信版），
      数字一旦不一致，用户根本无从判断哪个对 —— 那比看不到更糟。
    """
    st.markdown("---")
    st.subheader("📰 18:00 日报（网页回看）")
    st.caption("与微信里收到的那条**逐字一致** —— 云端日报任务生成后加密存进仓库，"
               "这里解密显示，不是重新算的摘要。没收到微信（例如当天额度用完）时就看这里。")

    if st.button("🔄 从云端拉取最新日报", key="digest_last_pull"):
        _okp, _msgp, _datap = digest_last_fetch_remote()
        st.session_state['digest_last_pull_msg'] = ("✅ " if _okp else "❌ ") + _msgp
        if _okp:
            st.session_state['digest_last_try_at'] = _time_module.time()
        st.rerun()

    _pull_msg = st.session_state.pop('digest_last_pull_msg', None)
    if _pull_msg:
        if _pull_msg.startswith("✅"):
            st.success(_pull_msg)
        else:
            st.warning(_pull_msg)

    _data, _err, _src = _digest_last_resolve()
    if not _data:
        st.warning("现在拿不到日报正文：" + (_err or "原因未知"))
        st.caption("常见原因：① 云端日报还没跑过（每天 18:00 一次）；"
                   "② **没配 BAND_KEY** —— 日报正文含股票代码，脚本会拒绝以明文写进 public 仓库，"
                   "于是根本没有落盘（这是刻意的，不是故障）；"
                   "③ 没配 GITHUB_TOKEN，读不到云端那份。")
        return

    _d = str(_data.get('date') or '?')
    _stale = (_d != now_cn_str('%Y-%m-%d'))
    st.caption(f"📅 日报日期 **{_d}**　·　生成于 {_data.get('saved_at') or '?'}"
               f"　·　来源：{_src}"
               + ("　·　⚠️ **不是今天的**（今天那份可能还没生成，或云端任务失败了）" if _stale else ""))
    if _err:
        st.caption(f"⚠️ 顺带说明：{_err}")
    _title = str(_data.get('title') or f"做T助手日报 {_d}")
    with st.expander(f"📄 {_title}（点开/收起）", expanded=True):
        st.markdown(_data.get('text') or '')


# ================= 3.5 网络请求层 + 代码前缀 + 日线缓存 =================
# 背景（本次修复）：原先 get_daily_data 用 timeout=3、无 headers、无重试、无备用源，
# 且 _get_code 前缀规则漏判北交所（43/83/87/88/92 开头）与沪市转债（110/111/113）。
# 北交所代码被拼成 sz 前缀后，腾讯返回 code=0 但 qfqday 为空数组，
# pd.DataFrame([]) 抛异常 → 被 except 静默吞掉 → 页面只显示"数据不足，无法标注。"，
# 用户拿不到任何可排查的信息。以下做统一加固：多 host/多源轮询 + 重试 + 超时放宽
# + 磁盘缓存兜底 + 失败原因可诊断。

_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://gu.qq.com/",
    "Accept": "*/*",
}

class DataFetchError(Exception):
    """数据获取失败。携带逐次尝试的原因链，供页面诊断展示，不再静默返回 None。"""
    def __init__(self, symbol, attempts):
        self.symbol = symbol
        self.attempts = list(attempts)
        brief = "；".join(f"{s}={e}" for s, e in self.attempts) if self.attempts else "无可用数据源"
        super().__init__(f"{symbol} 日线获取失败：{brief}")

# ★ 连接复用：原来每次取数都是裸 `requests.get`，即**每个请求都重新做一次 TCP+TLS 握手**。
#   全市场采集要发几千次请求，握手开销直接翻倍（实测本机每只 800ms，其中很大一块是握手）。
#   改成「每线程一个 Session」：线程内复用连接，握手摊薄到每线程一次。
#   为什么必须**线程独立**：requests.Session 官方明确不是线程安全的，共用会串数据。
#   为什么用 `getattr` + try 兜底：拿不到 Session 时退回 requests 模块本身（它同样有 .get），
#   保证「Session 出问题」永远不会退化成「取不到数据」——这条比提速重要得多。
_TLS_LOCAL = threading.local()

def _http_session():
    """取本线程的 requests.Session（复用连接）。任何异常都回退到 requests 模块。"""
    try:
        s = getattr(_TLS_LOCAL, "sess", None)
        if s is None:
            s = requests.Session()
            _TLS_LOCAL.sess = s
        return s
    except Exception as e:
        _log("_http_session", e)
        return requests

def _http_get_json(url, params=None, timeout=(5, 10), retries=2):
    """带 UA / 超时 / 重试的 JSON GET。返回 (ok, data, err)。绝不抛异常。"""
    last_err = "未知错误"
    for i in range(max(1, retries)):
        try:
            r = _http_session().get(url, params=params, headers=_HTTP_HEADERS, timeout=timeout)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            return True, r.json(), ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            if i < max(1, retries) - 1:
                _time_module.sleep(0.4 * (i + 1))
    return False, None, last_err

def _http_get_text(url, encoding='gbk', timeout=(5, 10), retries=2):
    """带 UA / 超时 / 重试的文本 GET（腾讯 qt.gtimg.cn 返回 GBK）。返回 (ok, text, err)。"""
    last_err = "未知错误"
    for i in range(max(1, retries)):
        try:
            r = _http_session().get(url, headers=_HTTP_HEADERS, timeout=timeout)
            r.encoding = encoding
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            return True, r.text, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            if i < max(1, retries) - 1:
                _time_module.sleep(0.4 * (i + 1))
    return False, "", last_err

def _quote_prefix(symbol):
    """判定行情代码前缀 sh / sz / bj（修正原规则对北交所、沪市转债的漏判）。"""
    s = str(symbol).strip()
    if not re.fullmatch(r'\d{6}', s):
        return "sz"
    if s.startswith(('43', '83', '87', '88', '92')):
        return "bj"                      # 北交所（含 920 新代码段）
    if s.startswith(('110', '111', '113', '118', '119')):
        return "sh"                      # 沪市可转债 / 可交换债（原规则误判为 sz）
    if s.startswith('12'):
        return "sz"                      # 深市可转债
    if s.startswith(('5', '6', '9')):
        return "sh"                      # 沪市股票 / 基金 / B股
    return "sz"

# 腾讯日线/分时多域名容错
# ★ 2026-09-21：末尾补上腾讯 K 线的**镜像域**。为什么需要它 ——
#   本机（以及部分受代理影响的网络）`web.ifzq.gtimg.cn` / `ifzq.gtimg.cn` 会返 501，
#   一失败整条链就退到**新浪不复权**源；除权日会读出假的「跌破 20 日线」。
#   镜像域 `proxy.finance.qq.com/ifzqgtimg` 是同一套 API、同样前复权，只是换个 host，
#   作为**第三顺位兜底**：主域正常时永远不会被用到，线上行为不变。
_QQ_APP_HOSTS = ["https://web.ifzq.gtimg.cn", "https://ifzq.gtimg.cn",
                 "https://proxy.finance.qq.com/ifzqgtimg"]

# 东方财富日K的多域名池 —— **备用日线源**。
# ★ 为什么选它当第二源：它同样提供**前复权**日线（fqt=1），与腾讯同口径、可直接互换；
#   而新浪是**不复权**，除权/分红当天会出现价格断层 → 假的「跌破 20 日线」，
#   所以新浪只能压到最后的在线兜底位置，不能顶上来。
_EM_KLINE_HOSTS = ["https://push2his.eastmoney.com", "https://push2.eastmoney.com"]

# ★★ 源健康熔断：某个源连续失败若干次后，**本进程内**一段时间不再尝试它。
#   为什么必须有：全市场扫描要为几百只票逐个取数。若此时某个源是死的，不做熔断就等于
#   「每只票都为它付一次超时 + 重试」→ 扫描时间成倍膨胀，还会把剩下那个好源也拖进限流。
#   （实测踩过的形态：主源抖动时整轮扫描慢到被页面 60 秒自动刷新掐断，结果全丢。）
#   为什么打包成一个字典而不是几个全局变量：AST 加载器只抽取列进抽取集合的名字，
#   **名字越少越不容易漏**；漏一个 → NameError 被兜底吞掉 → 测试照样绿、功能静默失效。
FEED_HEALTH = {}                 # {源名: {"fails": 连续失败次数, "dead_until": 解禁时间戳}}
FEED_DEAD_AFTER = 3              # 连续失败几次算「暂时不可用」
FEED_DEAD_SECONDS = 600          # 熔断时长：10 分钟，足够跨过一次接口抖动
# 「这个代码它本来就没有数据」类错误 —— **不能**计入源健康，
# 否则连扫 3 只北交所股票（腾讯不提供北交所日线）就会把好端端的腾讯源熔断掉。
_FEED_CODE_LEVEL = ("空日线", "不受支持", "无数据", "有效数据不足", "列数不足")


def _feed_alive(name):
    """该源现在是否值得尝试（熔断器）。熔断到期会自动解禁并重新计数。"""
    try:
        st = FEED_HEALTH.get(name) or {}
        until = float(st.get("dead_until") or 0)
        if until and _time_module.time() < until:
            return False
        if until:
            FEED_HEALTH.pop(name, None)      # 熔断到期 → 清空重来，给它一次机会
        return True
    except Exception as e:
        _log("_feed_alive", e)
        return True                          # 熔断器自己出错时，宁可照常尝试


def _feed_note(name, errs, ok):
    """记一次源尝试结果。错误全为「代码级」时不惩罚源（见 _FEED_CODE_LEVEL）。"""
    try:
        if ok:
            FEED_HEALTH.pop(name, None)
            return
        msgs = [str(m) for _n, m in (errs or [])]
        if msgs and all(any(h in m for h in _FEED_CODE_LEVEL) for m in msgs):
            return
        st = FEED_HEALTH.setdefault(name, {})
        st["fails"] = int(st.get("fails") or 0) + 1
        if st["fails"] >= FEED_DEAD_AFTER:
            st["dead_until"] = _time_module.time() + FEED_DEAD_SECONDS
    except Exception as e:
        _log("_feed_note", e)


def feed_health():
    """给 UI / 诊断用：各源现状（连续失败数、是否熔断、还有几秒解禁）。"""
    out = {}
    now = _time_module.time()
    try:
        for name, st in (FEED_HEALTH or {}).items():
            until = float((st or {}).get("dead_until") or 0)
            out[name] = {"fails": int((st or {}).get("fails") or 0),
                         "dead": bool(until and now < until),
                         "resume_in": max(0, int(until - now)) if until else 0}
    except Exception as e:
        _log("feed_health", e)
    return out


def _em_secid(code):
    """东财 secid：沪市 `1.`，深市 / 北交所 `0.`。

    ★ 刻意**不复用** `_quote_prefix` —— 那个产出的是腾讯要的 sh/sz/bj，
      东财只认 0/1 两个市场号；混用会让沪市票去查深市，永远返回空。
    ★ 也接受**完整符号**（`sh000300` 这种）：指数必须这么传，因为纯数字 000300
      走前缀规则会被判成深市（那是个不存在的股票代码）。
    """
    s = str(code).strip().lower()
    if s.startswith("sh"):
        return "1." + s[2:]
    if s.startswith(("sz", "bj")):
        return "0." + s[2:]
    if s.startswith(("5", "6", "9", "110", "111", "113", "118", "119")):
        return "1." + s
    return "0." + s


def _fetch_kline_em(code, limit=640, timeout=(5, 10), retries=2):
    """东方财富日线（**前复权**）—— 腾讯整体不可用时的第二源。

    接口返回的 klines 每条形如 "日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手率"，
    列序与 `_normalize_kline_rows` 要求的一致，取前 6 项即可。
    返回 (df|None, 源名, 错误列表)，与 `_fetch_kline_qq` 同接口，可直接互换。
    """
    errs = []
    secid = _em_secid(code)
    for host in _EM_KLINE_HOSTS:
        short = host.split("//")[1]
        ok, js, err = _http_get_json(
            f"{host}/api/qt/stock/kline/get",
            # ★ 参数陷阱（2026-09-20 实测）：只要带上 `beg=0`，东财就会**忽略 lmt** ——
            #   明明是 lmt=60，却把 1999 年至今全部 6526 条一起吐回来。
            #   正确写法是**不传 beg**、只给 `end` + `lmt`，这才是「最近 N 条」。
            #   传错不会报错，只会让波段选股每只票白拉几十倍数据、整轮扫描被拖慢。
            params={"secid": secid, "klt": "101", "fqt": "1",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56",
                    "end": "20500101", "lmt": str(limit)},
            timeout=timeout, retries=retries)
        if not ok:
            errs.append((f"东财({short})", err))
            continue
        data = (js or {}).get("data") if isinstance(js, dict) else None
        klines = (data or {}).get("klines") if isinstance(data, dict) else None
        if not klines:
            errs.append((f"东财({short})", "返回空日线（该代码不受支持或无数据）"))
            continue
        rows = []
        for line in klines:
            p6 = str(line).split(",")
            if len(p6) < 6:
                continue
            rows.append([p6[0], p6[1], p6[2], p6[3], p6[4], p6[5]])
        df = _normalize_kline_rows(rows)
        if df is None:
            errs.append((f"东财({short})", f"有效数据不足（原始 {len(rows)} 条）"))
            continue
        if limit and len(df) > int(limit):
            # 兜底：接口若哪天又改了 lmt 语义，至少不会把超量数据灌进上层
            df = df.tail(int(limit)).reset_index(drop=True)
        return df, f"东财前复权·{short}", errs
    return None, "", errs

# 日线磁盘缓存：所有在线源都失败时兜底，避免接口抖动直接让页面瘫痪。
# 说明：Streamlit Cloud 容器文件系统在应用重启后会清空，但对分钟级的接口抖动足够用；
# 多会话共享同一文件，写入采用"临时文件 + 原子替换"避免读到半截 JSON。
KLINE_CACHE_FILE = os.path.join(BASE_DIR, "kline_cache.json")
KLINE_CACHE_MAX_CODES = 20
KLINE_CACHE_ROWS = 320

def _cache_kline(code, df):
    """把成功取到的日线写入磁盘缓存（跨 Streamlit 会话共享，用于接口抖动兜底）。"""
    try:
        store = _load_json(KLINE_CACHE_FILE, {})
        if not isinstance(store, dict):
            store = {}
        tail = df[['Date', 'Open', 'Close', 'High', 'Low', 'Volume']].tail(KLINE_CACHE_ROWS).copy()
        tail['Date'] = tail['Date'].astype(str)
        store.pop(code, None)                       # 先删再插，刷新 LRU 顺序
        store[code] = {"ts": now_cn_str(), "rows": tail.values.tolist()}
        while len(store) > KLINE_CACHE_MAX_CODES:
            store.pop(next(iter(store)), None)
        tmp = KLINE_CACHE_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp, KLINE_CACHE_FILE)
    except Exception as e:
        # 缓存写失败本身不致命，但会导致"接口抖动时无法兜底"，必须留痕
        _log("_cache_kline", e)

def _load_cached_kline(code):
    """读磁盘缓存，返回 (df 或 None, 缓存时间字符串)。"""
    try:
        store = _load_json(KLINE_CACHE_FILE, {})
        node = store.get(code) if isinstance(store, dict) else None
        if not node or not node.get("rows"):
            return None, ""
        df = pd.DataFrame(node["rows"], columns=['Date', 'Open', 'Close', 'High', 'Low', 'Volume'])
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        for c in ['Open', 'Close', 'High', 'Low', 'Volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['Date', 'Close']).sort_values('Date').reset_index(drop=True)
        return (df if len(df) else None), str(node.get("ts", ""))
    except Exception as e:
        _log("_load_cached_kline", e)
        return None, ""

def _normalize_kline_rows(rows):
    """把 [date, open, close, high, low, volume] 行数组规范成 DataFrame。
    关键：空数组返回 None 而不是抛异常（这是本次"数据不足"的直接肇因）。"""
    if not rows:
        return None
    try:
        df = pd.DataFrame(list(rows))
        if df.shape[1] < 6:
            # 列数不足说明接口返回体变了（例：风控页/空壳 JSON），必须留痕而非静默"数据不足"
            _log("_normalize_kline_rows", ValueError(f"列数不足: {df.shape[1]} < 6, 首行={str(rows[0])[:120]}"))
            return None
        df = df.iloc[:, :6]
        df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        for c in ['Open', 'Close', 'High', 'Low', 'Volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['Date', 'Close']).sort_values('Date').reset_index(drop=True)
        return df if len(df) >= 30 else None
    except Exception as e:
        _log("_normalize_kline_rows", e)
        return None

def _fetch_kline_qq(code, limit=640, timeout=(5, 10), retries=2):
    """腾讯日线（前复权），主域名失败自动切备用域名。返回 (df|None, 源名, 错误列表)。

    timeout / retries 可传参：给「数据源体检」用更短的值，免得一次体检要等好几分钟。"""
    errs = []
    for host in _QQ_APP_HOSTS:
        url = f"{host}/appstock/app/fqkline/get?param={code},day,,,{limit},qfq"
        ok, js, err = _http_get_json(url, timeout=timeout, retries=retries)
        if not ok:
            errs.append((f"腾讯({host.split('//')[1]})", err))
            continue
        if not isinstance(js, dict) or js.get("code") != 0:
            errs.append((f"腾讯({host.split('//')[1]})", f"接口 code={js.get('code') if isinstance(js, dict) else '非JSON'}"))
            continue
        node = (js.get("data") or {}).get(code) or {}
        df = _normalize_kline_rows(node.get("qfqday") or node.get("day"))
        if df is None:
            errs.append((f"腾讯({host.split('//')[1]})", "返回空日线（该代码不受支持或无数据）"))
            continue
        return df, f"腾讯·{host.split('//')[1]}", errs
    return None, "", errs

def _fetch_kline_sina(code, limit=640, timeout=(5, 10), retries=2):
    """新浪日线兜底源（**不复权**）。腾讯对北交所不提供日线，此处是重要兜底。

    ⚠️ 不复权的代价：除权/分红当天价格会断层，可能出现假的「跌破 20 日线」。
    所以它只排在**前复权源都拿不到**时才用（顺序见 `_try_fetch_kline`），别往前提。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={limit}")
    ok, js, err = _http_get_json(url, timeout=timeout, retries=retries)
    if not ok:
        return None, "", [("新浪", err)]
    if not isinstance(js, list) or not js:
        return None, "", [("新浪", "返回空日线")]
    rows = []
    for it in js:
        if not isinstance(it, dict):
            continue
        rows.append([it.get('day'), it.get('open'), it.get('close'),
                     it.get('high'), it.get('low'), it.get('volume')])
    df = _normalize_kline_rows(rows)
    if df is None:
        return None, "", [("新浪", f"有效数据不足（原始 {len(rows)} 条）")]
    return df, "新浪(不复权)", []

# 最近一次取数诊断结果：{code: {"source":..., "ts":..., "attempts":[(源, 原因), ...]}}
LAST_FETCH_DIAG = {}

def _try_fetch_kline(code):
    """腾讯(前复权) → 东财(前复权) → 新浪(不复权) → 磁盘缓存。成功返回 (df, 源描述)。

    ★ 顺序有两个理由，别随手改：
      ① **前复权源优先**：新浪不复权，除权日会出现价格断层 → 假的跌破 20 日线；
         东财与腾讯同为前复权、可直接互换，所以东财排第二、新浪压最后。
      ② 每个源都过一次熔断器（`_feed_alive`）：某个源死掉时，全市场扫描不该为它反复付超时 ——
         这一条直接决定「主源抖动时还能不能把整轮扫完」。
    全部在线源都失败才回退磁盘缓存；连缓存都没有才抛 DataFetchError。"""
    attempts = []
    for name, fn in (("腾讯", _fetch_kline_qq), ("东财", _fetch_kline_em),
                     ("新浪", _fetch_kline_sina)):
        if not _feed_alive(name):
            attempts.append((name, "已熔断：连续失败过多，本次跳过（约 10 分钟后自动重试）"))
            continue
        try:
            df, src, errs = fn(code)
            attempts.extend(errs)
            if df is not None and not df.empty:
                _feed_note(name, errs, True)
                _cache_kline(code, df)
                LAST_FETCH_DIAG[code] = {"source": src, "ts": now_cn_str(), "attempts": attempts}
                return df, src
            _feed_note(name, errs, False)
        except Exception as e:
            attempts.append((fn.__name__, f"未预期异常 {type(e).__name__}: {str(e)[:100]}"))
            _feed_note(name, [("异常", str(e))], False)
    df_cache, ts = _load_cached_kline(code)
    if df_cache is not None:
        attempts.append(("本地缓存", f"已回退到 {ts} 的缓存数据"))
        LAST_FETCH_DIAG[code] = {"source": f"本地缓存({ts})", "ts": ts, "attempts": attempts}
        return df_cache, f"本地缓存({ts})"
    LAST_FETCH_DIAG[code] = {"source": "无", "ts": "", "attempts": attempts}
    raise DataFetchError(code, attempts)

def feed_probe(code, limit=80):
    """逐个源**实测**一遍（无视熔断器），返回可直接显示的行列表。

    给「主源抖动」现场判断用：所以每个源只试 1 次、超时也用得短 ——
    健康时一两秒就出结果，全挂时最多等半分钟。**不要**在这里把重试和备用域名都跑一遍，
    否则一次体检要等好几分钟（那就没人愿意点了）。
    """
    lines = []
    for name, fn in (("腾讯", _fetch_kline_qq), ("东财", _fetch_kline_em),
                     ("新浪", _fetch_kline_sina)):
        t0 = _time_module.time()
        try:
            df, src, errs = fn(code, limit=limit, timeout=(4, 6), retries=1)
            dt = _time_module.time() - t0
            if df is not None and len(df) >= 1:
                lines.append(f"- ✅ **{name}**：拿到 {len(df)} 条，最新 "
                             f"{str(df['Date'].iloc[-1])[:10]}（{dt:.1f}s，源={src}）")
            else:
                why = "；".join(f"{a}→{b}" for a, b in (errs or [])[:3]) or "无返回"
                lines.append(f"- ❌ **{name}**：失败（{dt:.1f}s）{why}")
        except Exception as e:
            lines.append(f"- ❌ **{name}**：异常 {type(e).__name__}: {str(e)[:80]}")
    _hl = feed_health()
    if _hl:
        lines.append("")
        lines.append("本轮熔断状态：" + "；".join(
            f"{n} 连败 {d['fails']}" + (f"（{d['resume_in']}s 后重试）" if d['dead'] else "")
            for n, d in sorted(_hl.items())))
    return lines


def _render_feed_diag(code, diag):
    """在页面上摊开「数据源现状 + 一键体检」。

    抽成函数是为了在「日线取回来了但是备用源」与「日线彻底取不到」两个分支里复用。
    ★ 刻意**不自己开 expander** —— 调用方可能已经在 expander 里了，嵌套会直接报错。
    """
    _last = st.session_state.get("feed_probe_lines")
    if _last:
        st.markdown("**🔌 数据源实测结果**（上次点击「逐个源实测」时）")
        for _l in _last:
            st.markdown(_l)
    _fh = feed_health()
    if _fh:
        st.markdown("**本轮取数中出现的源异常**（连续失败会自动熔断，免得拖垮整轮扫描）")
        for _n, _d in sorted(_fh.items()):
            _badge = (f"⛔ 已熔断，约 {_d['resume_in']} 秒后自动重试" if _d["dead"] else "✅ 正常")
            st.markdown(f"- {_n}：连续失败 {_d['fails']} 次　{_badge}")
    else:
        st.markdown("三个源都还没出现过连续失败。")
    _at = ((diag or {}).get("attempts") or [])
    if _at:
        st.markdown("**本次逐次尝试记录**")
        for _i, _a in enumerate(_at, 1):
            _nm, _rs = (_a if isinstance(_a, (tuple, list)) and len(_a) == 2
                        else ("尝试", str(_a)))
            st.markdown(f"    {_i}. **{_nm}** → {_rs}")
    st.caption("兜底顺序：腾讯(前复权) → 东财(前复权) → 新浪(不复权) → 本地缓存。"
               "三个源各实测一次，最长约 1 分钟。")
    # ★ 用 long_button 而不是 st.button：三个源都卡死时最坏约 50 秒，
    #   已进入顶部全局 60 秒自动刷新的射程 —— 被打断后按钮"按下"状态消失、
    #   结果也没落盘，用户只能再点一次（见 test_long_task.py）。
    if long_button("🔌 逐个源实测一遍", key="probe_feeds_now"):
        try:
            with st.spinner("正在逐个源实测..."):
                st.session_state.feed_probe_lines = feed_probe(code)
        except Exception as _pe:
            _log("probe_feeds_now", _pe)
            st.session_state.feed_probe_lines = [f"- 体检失败：{_pe}"]
        st.rerun()


# ================= 4. 辅助函数 =================
@st.cache_data(ttl=3600)
def get_stock_name(symbol):
    prefix = _quote_prefix(symbol)
    ok, text, _err = _http_get_text(f"https://qt.gtimg.cn/q={prefix}{symbol}", encoding='gbk', timeout=(5, 10), retries=2)
    if ok and "~" in text:
        parts = text.split("~")
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip('"')
    return symbol

@st.cache_data(ttl=60)
def get_market_status():
    ok, text, _err = _http_get_text("https://qt.gtimg.cn/q=sh000001", encoding='gbk', timeout=(5, 10), retries=2)
    if ok and "~" in text:
        parts = text.split("~")
        if len(parts) > 32:
            try: return float(parts[32])
            except (TypeError, ValueError) as e:
                # 静默变 0.00% 会让"大盘暴跌时停止推送买点"的保护逻辑失效
                _log("get_market_status:float(parts[32])", e)
    return 0.0

def is_trading_time():
    # ✅ 必须用北京时间判断（云服务器是 UTC）
    now = now_cn().time()
    return (time(9, 25) <= now <= time(11, 32)) or (time(12, 58) <= now <= time(15, 2))

def send_wechat_notification(send_key, title, content):
    if not send_key: return False
    try:
        res = requests.post(f"https://sctapi.ftqq.com/{send_key}.send", data={"title": title, "desp": content}, timeout=(5, 10))
        return res.status_code == 200
    except Exception as e:
        _log("send_wechat_notification", e)
        return False

# ================= 5. 侧边栏 =================
with st.sidebar:
    st.header("📈 自选股管理")
    if 'stock_list' not in st.session_state: st.session_state.stock_list = load_watchlist()
    if 'current_stock' not in st.session_state: st.session_state.current_stock = DEFAULT_STOCK

    with st.form("batch_add_form", clear_on_submit=True):
        new_stocks = st.text_input("批量添加股票代码", placeholder="例如: 512480, 159915 000001", label_visibility="collapsed")
        submit_add = st.form_submit_button("➕ 批量添加", use_container_width=True)
        if submit_add and new_stocks.strip():
            codes = re.findall(r'\d{6}', new_stocks)
            added = False
            for code_ in codes:
                if code_ not in st.session_state.stock_list:
                    st.session_state.stock_list.append(code_)
                    added = True
            if added:
                save_watchlist(st.session_state.stock_list); st.rerun()
            else: st.warning("未发现新的有效股票代码。")
    st.markdown("---")
    
    if not st.session_state.stock_list:
        st.info("暂无自选股，请添加"); st.session_state.current_stock = DEFAULT_STOCK
    else:
        for stock in st.session_state.stock_list:
            col_stock, col_del = st.columns([4, 1])
            with col_stock:
                stock_name = get_stock_name(stock)
                is_selected = (stock == st.session_state.current_stock)
                if st.button(f"{stock_name} ({stock})", key=f"select_{stock}", use_container_width=True, type="primary" if is_selected else "secondary"):
                    st.session_state.current_stock = stock; st.rerun()
            with col_del:
                if st.button("❌", key=f"del_{stock}"):
                    st.session_state.stock_list.remove(stock); save_watchlist(st.session_state.stock_list)
                    if st.session_state.current_stock == stock:
                        st.session_state.current_stock = st.session_state.stock_list[0] if st.session_state.stock_list else DEFAULT_STOCK
                    st.rerun()
    st.markdown("---")
    st.header("⚙️ 参数设置")
    auto_dev = st.checkbox("启用动态偏离阈值", value=True)
    if not auto_dev: manual_dev = st.slider("手动偏离阈值(%)", 0.5, 2.0, 0.8) / 100
    st.markdown("---")
    st.header("🤖 AI 配置")
    config = load_config()
    if 'api_key' not in st.session_state: st.session_state.api_key = config.get('api_key', '')
    def on_api_key_change(): save_config({**load_config(), 'api_key': st.session_state.api_key})
    st.text_input("DeepSeek API Key", type="password", key="api_key", on_change=on_api_key_change, help="保存后无需再配置")
    if _secrets_has_key("DEEPSEEK_API_KEY"): st.success("✅ 已从 Streamlit Secrets 读取 API Key")
    elif st.session_state.api_key: st.info("💾 API Key 来自本地文件（云端重启后会丢）")
    st.markdown("---")
    st.header("📱 微信提醒（可选）")
    if 'send_key' not in st.session_state: st.session_state.send_key = config.get('send_key', '')
    def on_send_key_change(): save_config({**load_config(), 'send_key': st.session_state.send_key})
    st.text_input("Server酱 SendKey", type="password", key="send_key", on_change=on_send_key_change, help="去 sct.ftqq.com 免费注册获取")
    if _secrets_has_key("SERVERCHAN_KEY"): st.success("✅ 已从 Streamlit Secrets 读取 SendKey")
    elif st.session_state.send_key: st.info("💾 SendKey 来自本地文件（云端重启后会丢）")
    # ---- ★ 每日限额（2026-09-22 用户要求：5 条里留 1 条给 18:00 日报，其余手动选）----
    _nb = notify_budget_get()
    st.caption("📊 " + notify_budget_line(_nb))
    with st.form("notify_budget_form"):
        _nb1, _nb2 = st.columns(2)
        _nb_lim = _nb1.number_input("每日上限", min_value=1,
                                    max_value=NOTIFY_LIMIT_MAX, value=int(_nb['limit']),
                                    step=1, key="nb_limit",
                                    help="Server酱 免费版每天只有 5 条，这里只是防超发的刹车")
        _nb_res = _nb2.number_input("留给 18:00 日报", min_value=0,
                                    max_value=NOTIFY_LIMIT_MAX, value=int(_nb['reserved']),
                                    step=1, key="nb_reserved",
                                    help="固定预留给日报的条数，不参与手动额度")
        _nb_c1, _nb_c2 = st.columns(2)
        _nb_save = _nb_c1.form_submit_button("💾 保存限额", use_container_width=True)
        _nb_sync = _nb_c2.form_submit_button("🔄 从云端刷新", use_container_width=True)
    if _nb_save:
        _nb_new = notify_budget_set({**_nb, "limit": _nb_lim, "reserved": _nb_res})
        _ok, _m = notify_budget_push(_nb_new)
        if _ok:
            st.session_state['notify_flash'] = (f"已保存：每日 {_nb_new['limit']} 条，"
                                              f"预留 {_nb_new['reserved']} 条给日报")
            st.rerun()
        else:
            st.warning(f"已保存到本地，但账本提交失败：{_m}")
    if _nb_sync:
        _ok, _m, _remote = notify_budget_pull()
        if _ok and _remote is not None:
            notify_budget_set(_remote)
            st.session_state['notify_flash'] = (f"已按云端账本刷新："
                                              f"今日已用 {len(_remote['sent'])} 条")
            st.rerun()
        else:
            st.warning(f"刷新失败：{_m}" if not _ok else f"{_m}（按本地计）")
    st.caption("🚫 自动推送已全部关闭 —— 巡检和本页都不再自己发微信。"
               "候选在「🌊 选股与跟踪」页的「📤 今日推送」里，"
               "由你点哪条发哪条；18:00 日报照旧（占上面预留的那条）。")
    _nf = st.session_state.pop('notify_flash', None)
    if _nf: st.success(_nf)

    st.markdown("---")
    st.subheader("🧠 波段记忆同步")
    if 'github_token' not in st.session_state: st.session_state.github_token = config.get('github_token', '')
    def on_gh_token_change(): save_config({**load_config(), 'github_token': st.session_state.github_token})
    st.text_input("GitHub Token", type="password", key="github_token", on_change=on_gh_token_change,
                  help="用于把「波段记忆」同步到仓库，让云端巡检能按记忆里的清单推送微信。"
                       "建议放到 Streamlit Secrets 的 GITHUB_TOKEN，更安全。")
    if _secrets_has_key("GITHUB_TOKEN"):
        st.success("✅ 已从 Streamlit Secrets 读取 GitHub Token")
    elif st.session_state.github_token:
        st.info("💾 Token 来自本地文件（云端重启后会丢，建议改用 Streamlit Secrets）")
    else:
        st.caption("未配置 → 记忆只存在本容器，关页面后云端不会推送")

    st.markdown("---")
    st.header("🔔 推送自检")
    st.caption(f"🕐 北京时间 {now_cn().strftime('%Y-%m-%d %H:%M:%S')}")
    st.caption("☁️ 关页面也能推送：已由 GitHub Actions 每 5 分钟云端巡检，与本页是否打开无关")
    st.checkbox("本页顺便帮我找日内信号（只登记候选，不自动推送）", value=False,
                key="enable_page_monitor",
                help="开启后每次刷新页面都会算一遍自选股的日内买卖点，"
                     "把结果登记成候选等你挑；**不会**自动发微信。")
    if is_trading_time():
        st.success("✅ 当前处于交易时段")
    else:
        st.info("⏸ 非交易时段")
    if st.session_state.get('enable_page_monitor', False):
        st.success(f"✅ 本页巡检已开启（{len(st.session_state.stock_list)} 只自选股）"
                   "—— 只登记候选，推送还是你来点")
    else:
        st.info("💡 本页巡检已关闭（关着也行：推送入口在「📤 今日推送」）")
    if st.session_state.get('last_monitor_time'):
        st.caption(f"上次巡检: {st.session_state.last_monitor_time}（{st.session_state.get('last_monitor_count', 0)} 只）")
    if st.button("🧪 发送测试推送（占用今日 1 条额度）", use_container_width=True, key="test_notify"):
        if not notify_send_key():
            st.warning("请先填写 SendKey")
        else:
            _ok, _m = notify_send_guarded("【测试】做T助手连通性测试",
                                          f"北京时间 {now_cn_str('%Y-%m-%d %H:%M:%S')}\n"
                                          "收到这条说明微信推送链路正常。",
                                          kind='test')
            if _ok:
                st.session_state['notify_flash'] = _m
                st.rerun()
            else:
                st.warning(_m)
    st.markdown("---")
    if _secrets_has_key("WATCHLIST"): st.caption("📌 自选股来自 Streamlit Secrets")

symbol = st.session_state.current_stock
current_name = get_stock_name(symbol)
st.sidebar.success(f"当前标的: {current_name} ({symbol})")

# ================= 6. 数据获取 =================
def _get_code(symbol): return f"{_quote_prefix(symbol)}{str(symbol).strip()}"
code = _get_code(symbol)

@st.cache_data(ttl=60, show_spinner=False)
def get_daily_data(code):
    """主图日线：腾讯(自动切域名) → 新浪(不复权) → 磁盘缓存 三级容错。
    全部失败时抛 DataFetchError，由主程序展示逐次失败原因。
    注意：st.cache_data 不会缓存抛异常的结果，所以网络恢复后无需等 60 秒 TTL。"""
    df, _src = _try_fetch_kline(code)
    return df

def _fetch_minute_raw(code):
    """拉取腾讯分时原始数据（多域名轮询）。返回 (raw 列表 | None, 错误列表)。"""
    errs = []
    for host in _QQ_APP_HOSTS:
        url = f"{host}/appstock/app/minute/query?code={code}"
        ok, js, err = _http_get_json(url, timeout=(5, 10), retries=2)
        tag = f"腾讯分时({host.split('//')[1]})"
        if not ok:
            errs.append((tag, err)); continue
        if not isinstance(js, dict) or js.get("code") != 0:
            errs.append((tag, f"接口 code={js.get('code') if isinstance(js, dict) else '非JSON'}")); continue
        node = (js.get("data") or {}).get(code) or {}
        raw = ((node.get("data") or {}) or {}).get("data")
        if not raw:
            errs.append((tag, "返回空分时")); continue
        return raw, errs
    return None, errs

@st.cache_data(ttl=60, show_spinner=False)
def get_minute_data(code):
    raw, _errs = _fetch_minute_raw(code)
    if not raw: return None
    try:
        records = [item.split(" ") for item in raw]
        if not records: return None
        ncol = len(records[0])
        if ncol < 3: return None
        cols = ['Time', 'Price', 'F3', 'F4'][:ncol]
        df = pd.DataFrame(records, columns=cols)
        for c in cols:
            if c != 'Time': df[c] = pd.to_numeric(df[c], errors='coerce')
        if df['Price'].isna().all(): return None

        last_price = float(df['Price'].dropna().iloc[-1])
        f3 = df['F3'].fillna(0) if 'F3' in df.columns else pd.Series([0.0] * len(df))
        f4 = df['F4'].fillna(0) if 'F4' in df.columns else pd.Series([0.0] * len(df))
        f3_like_price = f3.iloc[-1] > 0 and 0.7 < f3.iloc[-1] / last_price < 1.3
        f4_mono = f4.iloc[-1] > 0 and (f4.diff().dropna() >= -1e-6).all()

        if f3_like_price and f4_mono:
            df['AvgPrice']  = f3; df['CumVolume'] = f4
        elif f4_mono:
            df['CumVolume'] = f4
            ratio = f3.iloc[-1] / f4.iloc[-1] if f4.iloc[-1] > 0 else 0
            if 0.3 * last_price < ratio < 3 * last_price: df['AvgPrice'] = f3 / f4.replace(0, np.nan)
            elif 0.3 * last_price * 100 < ratio < 3 * last_price * 100: df['AvgPrice'] = f3 / (f4 * 100)
            else: df['AvgPrice'] = np.nan
        else:
            df['CumVolume'] = f4 if f4.iloc[-1] > 0 else f3; df['AvgPrice']  = np.nan

        df['Volume'] = df['CumVolume'].diff()
        if len(df) > 0: df.loc[df.index[0], 'Volume'] = df['CumVolume'].iloc[0]
        df['Volume'] = df['Volume'].fillna(0).clip(lower=0)
        if df['AvgPrice'].isna().any() or (df['AvgPrice'] <= 0).any():
            amt = df['Price'] * df['Volume']; cum_amt = amt.cumsum()
            df['AvgPrice'] = (cum_amt / df['CumVolume'].replace(0, np.nan)).ffill().fillna(df['Price'])

        df['Price_Change'] = df['Price'].diff()
        ema12 = df['Price'].ewm(span=12, adjust=False).mean()
        ema26 = df['Price'].ewm(span=26, adjust=False).mean()
        df['DIFF'] = ema12 - ema26; df['DEA'] = df['DIFF'].ewm(span=9, adjust=False).mean(); df['MACD'] = 2 * (df['DIFF'] - df['DEA'])
        return df
    except Exception as e:
        _log("get_minute_data", e)
        return None

# ================= 7. 指标计算（日线）=================
def calculate_daily_indicators(df):
    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean(); df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean(); df['MA30'] = df['Close'].rolling(30).mean(); df['MA250'] = df['Close'].rolling(250).mean()
    df['MA20_UP'] = df['MA20'] > df['MA20'].shift(1)
    ema12 = df['Close'].ewm(span=12, adjust=False).mean(); ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIFF'] = ema12 - ema26; df['DEA'] = df['DIFF'].ewm(span=9, adjust=False).mean(); df['MACD'] = 2 * (df['DIFF'] - df['DEA'])
    low_9 = df['Low'].rolling(9).min(); high_9 = df['High'].rolling(9).max()
    rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
    df['K'] = rsv.ewm(com=2, adjust=False).mean(); df['D'] = df['K'].ewm(com=2, adjust=False).mean(); df['J'] = 3 * df['K'] - 2 * df['D']
    df['BOLL_MID'] = df['Close'].rolling(20).mean(); std = df['Close'].rolling(20).std()
    df['BOLL_UP'] = df['BOLL_MID'] + 2 * std; df['BOLL_LOW'] = df['BOLL_MID'] - 2 * std
    df['BOLL_WIDTH'] = (df['BOLL_UP'] - df['BOLL_LOW']) / df['BOLL_MID']
    df['VOL_MA5'] = df['Volume'].rolling(5).mean(); df['VOL_MA10'] = df['Volume'].rolling(10).mean()
    df['TR'] = np.maximum(df['High'] - df['Low'], np.maximum(abs(df['High'] - df['Close'].shift(1)), abs(df['Low'] - df['Close'].shift(1))))
    df['ATR14'] = df['TR'].rolling(14).mean()
    df['Signal'] = 0
    buy_cond = (df['MA20_UP'] == True) & (df['J'] < 10) & (df['Close'] <= df['BOLL_MID']) & (df['Volume'] < df['VOL_MA5'] * 1.2)
    sell_cond = (df['J'] > 110) & (df['Close'] >= df['BOLL_UP'] * 0.98) & (df['Volume'] > df['VOL_MA5'] * 1.0) & (df['Close'] < df['Open'])
    buy_cond = buy_cond & (df['Signal'].shift(1) != 1); sell_cond = sell_cond & (df['Signal'].shift(1) != -1)
    df.loc[buy_cond, 'Signal'] = 1; df.loc[sell_cond, 'Signal'] = -1
    return df.bfill().ffill()

# ================= 7.5 分时买卖点判定（主图与后台监控共用）=================
# 历史教训：这段逻辑原先在 generate_report_and_advice 和 monitor_all_watchlist 里
# 各写了一份，于是「动态偏离阈值系数 0.4→0.5」只改到监控那处，主图仍按 0.4 算，
# 导致页面上显示的买卖点和微信推送的买卖点对不上。现统一到本函数，只此一份。
INTRADAY_BUY_WINDOW = ("0935", "1445")    # 买点时间窗口（放宽后早盘急跌也能捕捉）
INTRADAY_SELL_WINDOW = ("0930", "1455")   # 卖点时间窗口
DEVIATION_COEF = 0.5                      # 动态偏离阈值系数（主图与监控必须用同一个）
DEVIATION_MIN, DEVIATION_MAX = 0.003, 0.015

def dynamic_deviation(df_minute):
    """按当日振幅自适应计算偏离阈值。主图与后台监控共用同一个函数。

    历史问题：该系数曾在两处分别写死（主图 0.4、监控 0.5），改一处漏一处，
    导致页面显示的买卖点与微信推送的买卖点不一致。"""
    try:
        if df_minute is None or df_minute.empty:
            return 0.008
        high_price = float(df_minute['Price'].max()); low_price = float(df_minute['Price'].min())
        avg_price = float(df_minute['AvgPrice'].mean())
        if avg_price > 0:
            return max(DEVIATION_MIN,
                       min(((high_price - low_price) / avg_price) * DEVIATION_COEF, DEVIATION_MAX))
    except Exception as e:
        _log("dynamic_deviation", e)
    return 0.008

def compute_intraday_signals(df_minute, deviation):
    """统一的分时买卖点判定。

    参数 deviation 为动态偏离阈值（如 0.008 表示 0.8%）。

    返回 dict（数据不足时返回 None）：
        buy / sell   : 命中的买点 / 卖点 DataFrame，含 AvgPrice、MACD_UP/DOWN、STOP_FALL/RISE 等列
        df_min       : 截断到 15:00 且已加 Vol_MA5 的分时数据
        day_high/low : 当日分时最高 / 最低价
    """
    if df_minute is None or df_minute.empty:
        return None
    try:
        df_min = df_minute[df_minute['Time'] <= "1500"].copy()
        if len(df_min) < 10:
            return None
        df_min['Vol_MA5'] = df_min['Volume'].rolling(5).mean()
        day_high = float(df_min['Price'].max()); day_low = float(df_min['Price'].min())
        day_range = max(day_high - day_low, 0.001)

        buy = df_min[(df_min['Time'] >= INTRADAY_BUY_WINDOW[0]) & (df_min['Time'] <= INTRADAY_BUY_WINDOW[1])].copy()
        sell = df_min[(df_min['Time'] >= INTRADAY_SELL_WINDOW[0]) & (df_min['Time'] <= INTRADAY_SELL_WINDOW[1])].copy()
        if buy.empty or sell.empty:
            return None

        buy['Price_Position'] = (buy['Price'] - day_low) / day_range
        sell['Price_Position'] = (sell['Price'] - day_low) / day_range
        buy['MACD_UP'] = buy['MACD'] > buy['MACD'].shift(1)
        sell['MACD_DOWN'] = sell['MACD'] < sell['MACD'].shift(1)
        buy['MACD_GOLDEN'] = (buy['DIFF'] > buy['DEA']) & (buy['DIFF'].shift(1) <= buy['DEA'].shift(1))
        sell['MACD_DEAD'] = (sell['DIFF'] < sell['DEA']) & (sell['DIFF'].shift(1) >= sell['DEA'].shift(1))
        # 止跌/企稳：当前不再创新低；滞涨：当前不再创新高
        buy['STOP_FALL'] = (buy['Price'] >= buy['Price'].shift(1)) & (buy['Price'].shift(1) <= buy['Price'].shift(2))
        sell['STOP_RISE'] = (sell['Price'] <= sell['Price'].shift(1)) & (sell['Price'].shift(1) >= sell['Price'].shift(2))

        # 买点：方案A 回踩均价线+MACD向上；方案B 接近当日低点+止跌+（MACD向上或缩量）
        buy_cond = ((buy['Price'] < buy['AvgPrice'] * (1 - deviation * 0.7)) & buy['MACD_UP']) | \
                   ((buy['Price_Position'] <= 0.30) & buy['STOP_FALL'] &
                    (buy['MACD_UP'] | (buy['Volume'] < buy['Vol_MA5'] * 0.85)))
        # 卖点：方案A 冲高乖离均价线+MACD向下；方案B 接近当日高点+滞涨+（MACD向下或放量）
        sell_cond = ((sell['Price'] > sell['AvgPrice'] * (1 + deviation * 0.7)) & sell['MACD_DOWN']) | \
                    ((sell['Price_Position'] >= 0.70) & sell['STOP_RISE'] &
                     (sell['MACD_DOWN'] | (sell['Volume'] > sell['Vol_MA5'] * 1.2)))

        return {'buy': buy[buy_cond], 'sell': sell[sell_cond], 'df_min': df_min,
                'day_high': day_high, 'day_low': day_low,
                'cur_price': float(df_min['Price'].iloc[-1])}
    except Exception as e:
        _log("compute_intraday_signals", e)
        return None

def _intraday_divergence(df_min):
    """日内 MACD 背离提示（仅用于主图展示，不参与买卖点判定）。"""
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


# ================= 7.5 今日盘面动态分析 =================
# 为什么要单独算这个（2026-09-20）：原来的「日内极值预测」只给出「以现价为中心、±0.6×ATR」
# 的对称区间，它回答不了盘中最要紧的那个问题 ——「今天到底在走什么走势、现在算高位还是低位」；
# 上下快的时候更是越看越糊。这一层专门讲**结构**：
#   形态（单边 / 冲高回落 / 探底回升 / 横盘…）· 现价在当日区间的百分位 ·
#   波动速度（是不是刚刚在加速）· 三种情景 + 触发价 · 与做T指引是否打架。
# ★ 纯函数：不碰网络、不碰 Streamlit；now_time 由调用方传入，便于做确定性测试。
INTRADAY_STRUCT_MIN_BARS = 10      # 少于这么多根分钟线，结构判定没有意义
INTRADAY_SPEED_WINDOW = 30         # 「最近 N 分钟」窗口：判断波动是不是骤然加速
INTRADAY_SPEED_FAST = 2.5          # 近窗口每分钟振幅 ÷ 当日至今平均每分钟振幅 ≥ 此值 → 上下太快
INTRADAY_SPEED_WARM = 1.6          # ≥ 此值 → 波动加快
INTRADAY_SPEED_CALM = 0.6          # ≤ 此值 → 波动收敛
INTRADAY_RANGE_ATR_BIG = 1.3       # 当日振幅 ÷ ATR14 ≥ 此值 → 全天大波动（相对该股常态）
INTRADAY_RANGE_ATR_CALM = 0.8      # ≤ 此值 → 相对自身常态偏收敛
INTRADAY_NARROW_PCT = 0.5          # 当日振幅（%）不足此值 → 窄幅盘整
INTRADAY_FLAT_PCT = 0.15           # 现价与开盘相差（%）不足此值 → 视为「走平」
INTRADAY_HIGH_POS = 0.75           # 现价在当日区间的位置 ≥ 此值 → 日内高位
INTRADAY_LOW_POS = 0.25            # ≤ 此值 → 日内低位
INTRADAY_STILL_RATIO = 0.40        # 近窗口振幅 ÷ 当日振幅 ≤ 此值 → 「最近没在动」（横盘类）
INTRADAY_EDGE_RATIO = 0.40         # 极值出现在前 40% 时段 → 算「单边」而不是「反转」
# ---- 「当前在走哪一条情景」的判定阈值（2026-09-21 新增，见 _intraday_pick_scenario）----
INTRADAY_AVG_NEUTRAL = 0.05        # 现价与均价线的偏离（%）不足此值 → 视为「贴着均价线」
INTRADAY_STRONG_POS = 0.66         # 站上均价线 + 日内位置 ≥ 此值 → 判为「① 偏强」
INTRADAY_WEAK_POS = 0.34           # 跌破均价线 + 日内位置 ≤ 此值 → 判为「③ 偏弱」
INTRADAY_NEAR_EDGE = 0.08          # 距日内高点/低点 ≤ 当日振幅的此比例 → 视为「正贴着临界价」
INTRADAY_EDGE_RECENT = 0.75        # 极值出现在时间轴后 25% 内 → 视为「刚刚才创新高/新低」
INTRADAY_SCEN_LABEL = {1: "① 偏强", 2: "② 中性", 3: "③ 偏弱"}
INTRADAY_SCEN_KIND = {1: "up", 2: "flat", 3: "down"}
# ★ 横盘类形态：价格在一个箱体里来回，**位置本身不构成方向选择** ——
#   摆到中上位不等于「偏强」，要真贴上/突破上沿才算。见 _intraday_pick_scenario 的 boxed 参数。
#   为什么必须这么分（2026-09-21 实测）：不区分时会出现「形态=横盘整理 / 当前=① 偏强」
#   同框自相矛盾 —— 用户一眼就会截图来问。
INTRADAY_BOXED_SHAPES = ("横盘整理", "窄幅盘整", "高位横盘", "低位横盘")

INTRADAY_SHAPE_READ = {
    "单边上行": "开盘后逐波抬高、日内低点出现在前段 —— 典型**单边上行**，盘中的回落多是洗盘；不破均价线方向仍偏多。",
    "震荡上行": "重心缓慢上移、幅度不大 —— **震荡上行**，偏多但不强，别在日内高位追。",
    "探底回升": "盘中砸出低点后又拉回来 —— **探底回升（V 型）**，低位有承接；但要站稳均价线才算真确认。",
    "冲高回落": "高点已经过去、现价从高点回落明显 —— **冲高回落（倒 V）**，上方抛压重，反抽到均价线附近容易再被打下来。",
    "单边下行": "开盘后逐波走低、日内高点出现在前段 —— **单边下行**，反抽到均价线附近都是减仓机会，别急着抄底。",
    "震荡下行": "重心缓慢下移 —— **震荡下行**，偏弱，反弹力度通常有限。",
    "高位横盘": "价格贴在日内高位窄幅波动 —— **高位横盘**：能放量突破日内高点就是蓄势，突不上去要防冲高做头。",
    "低位横盘": "价格贴在日内低位窄幅波动 —— **低位横盘**：缩量会磨人，再放量跌破日内低点要防加速下跌。",
    "横盘整理": "最近一段时间价格在一个小区间里反复 —— **横盘整理**，方向未定，等它自己选方向。",
    "窄幅盘整": "全天振幅极小 —— **极度缩量盘整**，做T空间几乎没有，硬做只会被手续费磨损。",
}


def _intraday_stage_text(now_time):
    """把当前时刻映射成盘中阶段。"""
    if now_time < time(9, 30): return "开盘前"
    if now_time <= time(10, 0): return "开盘半小时"
    if now_time <= time(11, 30): return "上午盘中"
    if now_time < time(13, 0): return "午间休市"
    if now_time <= time(14, 30): return "下午盘中"
    if now_time <= time(15, 0): return "尾盘"
    return "已收盘"


def _intraday_pick_scenario(pos, avg_dev, cur, avg_line, day_high, day_low, rng,
                            hi_touch, lo_touch, boxed=False):
    """判断**此刻真正在走**哪一条情景（纯函数）。

    hi_touch / lo_touch：**最后一次**触及今日高/低点的时间位置（0=开盘、1=最后一根分钟线），
      由调用方算好传进来（本函数没有分时序列）。必须用「最后一次」而不是「极值第一次出现在哪」——
      高位横盘会**反复**碰到同一个高点，`idxmax()` 给的是第一次出现的位置，会把「一直贴着上沿」
      误判成「极值早就过去了」（实测：现价距日高只有 0.02，却提示「要等涨到日高才算偏强」）。

    改这个函数之前先读完（2026-09-21 用户要求：「这三种状态能不能每天动态得标注一下是哪种状态」）：
    原来那三条情景是**静态预案**，每次看都要自己拿现价去对，页面从不告诉你现在算哪一条。
    这里把它定下来，判定顺序是 **先看是不是刚贴上临界价，再看位置 + 均价线**：

    ① 贴着今日高点、且高点就出在最近的分钟里、且站在均价线上方 → 「① 偏强」（情景刚开始）
    ② 贴着今日低点、且低点就出在最近的分钟里、且跌破均价线     → 「③ 偏弱」
    ③ 否则：站上均价线 + 位置 ≥ 0.66 → 偏强；跌破均价线 + 位置 ≤ 0.34 → 偏弱；
       两头都不满足 → 中性。

    ★ 两条刻意的设计（别"顺手优化"掉）：
      1. **突破/破位也要求偏离均价线 ≥ INTRADAY_AVG_NEUTRAL**。否则一个极窄的横盘日
         （振幅 0.3%、现价恰好就是当日最高）会被标成「刚突破」—— 位置是 100%，
         但那是噪音，不是突破。窄幅日就该老实显示「中性」。
      2. **位置偏上但已跌破均价线 → 中性（不是偏强）**，反过来同理，并在 reason 里说明
         「有冲高回落/探底回升迹象」。位置和均价线打架时按中性处理最诚实 ——
         说成偏强会让用户在冲高回落的下跌段去追多。
      3. **中性分支里「贴着均价线」的判定要排在「位置偏上/偏下」之前**，且措辞必须自洽：
         偏离 +0.02% 却写"已跌破均价线"会立刻毁掉用户对整栏的信任（这个错犯过一次）。

    ★ boxed=True 表示形态是**横盘类**（横盘整理 / 窄幅盘整 / 高位横盘 / 低位横盘）：
      箱体里位置会自己来回摆，所以**「位置中上」不单独算偏强**（反之亦然），只有真贴上
      上/下沿（break）才算方向开始选；否则一律中性，并在 reason 里说清「要等贴到哪条线」。
      不这么分就会出现「形态=横盘整理 / 当前=① 偏强」同框自相矛盾。

    返回 {"no": 1|2|3, "label": "① 偏强"/…, "kind": "up"/"flat"/"down",
          "mode": "break"|"pos", "reason": "一句话依据"}。
    mode="break" 表示正贴着边界、情景刚开始（最该盯）；"pos" 表示按位置判定。
    """
    _has_avg = avg_line > 0
    _ma = f"均价线 {avg_line:.3f}"

    def _mk(no, mode, reason):
        return {"no": no, "label": INTRADAY_SCEN_LABEL[no], "kind": INTRADAY_SCEN_KIND[no],
                "mode": mode, "reason": reason}

    if not _has_avg:
        # 数据源没给均价线 → 只能按位置判，且**明说**这是降级口径（不许假装和完整口径一样）
        _tail = "；本次取不到均价线，只按位置判"
        if pos >= INTRADAY_STRONG_POS:
            return _mk(1, "pos", f"现价处于日内 {pos * 100:.0f}% 分位（中上位）" + _tail)
        if pos <= INTRADAY_WEAK_POS:
            return _mk(3, "pos", f"现价处于日内 {pos * 100:.0f}% 分位（中下位）" + _tail)
        return _mk(2, "pos", f"现价处于日内 {pos * 100:.0f}% 分位（中部）" + _tail)

    _near = rng * INTRADAY_NEAR_EDGE
    if (avg_dev >= INTRADAY_AVG_NEUTRAL and (day_high - cur) <= _near
            and hi_touch >= INTRADAY_EDGE_RECENT):
        return _mk(1, "break",
                   f"现价 {cur:.3f} 正贴着今日高点 {day_high:.3f}（高点就出在最近几分钟），"
                   f"且站上{_ma}（{avg_dev:+.2f}%）")
    if (avg_dev <= -INTRADAY_AVG_NEUTRAL and (cur - day_low) <= _near
            and lo_touch >= INTRADAY_EDGE_RECENT):
        return _mk(3, "break",
                   f"现价 {cur:.3f} 正贴着今日低点 {day_low:.3f}（低点就出在最近几分钟），"
                   f"且跌破{_ma}（{avg_dev:+.2f}%）")
    # ★ boxed（横盘类）：位置摆动不算方向 → 先跳过两条「按位置」的判定，落到中性分支去。
    if (not boxed) and avg_dev > INTRADAY_AVG_NEUTRAL and pos >= INTRADAY_STRONG_POS:
        return _mk(1, "pos",
                   f"站上{_ma}（{avg_dev:+.2f}%），且处于日内 {pos * 100:.0f}% 分位（中上位）")
    if (not boxed) and avg_dev < -INTRADAY_AVG_NEUTRAL and pos <= INTRADAY_WEAK_POS:
        return _mk(3, "pos",
                   f"跌破{_ma}（{avg_dev:+.2f}%），且处于日内 {pos * 100:.0f}% 分位（中下位）")
    # ---- ② 中性：位置或均价线至少有一头不支持单边 ----
    # ⚠️ 分支顺序有讲究（2026-09-21 实测踩过）：**「贴着均价线」必须放在最前面**。
    #    先按 pos 判会让「位置 100% 分位、但偏离均价线只有 +0.02%」落进"位置偏上"那条，
    #    于是输出「位置偏上但**已跌破**均价线（+0.02%）」—— 正数却写"跌破"，自相矛盾。
    #    贴线时 pos 再极端也**没有拉开差距**，所以走贴线分支、并在括号里点明"不算突破/破位"。
    if boxed and abs(avg_dev) > INTRADAY_AVG_NEUTRAL and (
            pos >= INTRADAY_STRONG_POS or pos <= INTRADAY_WEAK_POS):
        # 箱体里位置摆到中上/中下位 —— 这**不是**方向选择，说清要等哪条线被贴到。
        if pos >= INTRADAY_STRONG_POS:
            return _mk(2, "pos",
                       f"形态仍是横盘箱体，位置摆到日内 {pos * 100:.0f}% 分位不构成方向选择"
                       f" —— 要等真贴上/站上今日高点 **{day_high:.3f}** 才算偏强")
        return _mk(2, "pos",
                   f"形态仍是横盘箱体，位置摆到日内 {pos * 100:.0f}% 分位不构成方向选择"
                   f" —— 要等真跌到/跌破今日低点 **{day_low:.3f}** 才算偏弱")
    if abs(avg_dev) <= INTRADAY_AVG_NEUTRAL:
        _gap = ""
        if pos >= INTRADAY_STRONG_POS:
            _gap = f"（位置虽在 {pos * 100:.0f}% 分位，但没拉开差距，不算突破）"
        elif pos <= INTRADAY_WEAK_POS:
            _gap = f"（位置虽在 {pos * 100:.0f}% 分位，但没拉开差距，不算破位）"
        _why = f"贴着{_ma}（{avg_dev:+.2f}%），方向未定" + _gap
    elif pos >= INTRADAY_STRONG_POS:
        # 走到这里 avg_dev 必然 < -NEUTRAL（位置偏上却已跌破均价线）→ 冲高回落
        _why = (f"位置偏上（{pos * 100:.0f}%）但已跌破{_ma}（{avg_dev:+.2f}%）"
                " —— 有冲高回落的迹象，先按中性看")
    elif pos <= INTRADAY_WEAK_POS:
        # 同理，走到这里 avg_dev 必然 > +NEUTRAL（位置偏下却已站上均价线）→ 探底回升
        _why = (f"位置偏下（{pos * 100:.0f}%）但已站上{_ma}（{avg_dev:+.2f}%）"
                " —— 有探底回升的迹象，先按中性看")
    else:
        _why = f"处于日内 {pos * 100:.0f}% 分位（中部），偏离{_ma} 只有 {avg_dev:+.2f}%"
    return _mk(2, "pos", _why)


def analyze_intraday_structure(df_minute, prev_close, atr, now_time=None, direction=None):
    """今日盘面动态分析（纯函数）。

    参数：
        df_minute  : 分时数据（需含 Time / Price，AvgPrice 可选）
        prev_close : 昨收价，作为涨跌幅基准（<=0 或 None 时退回用开盘价）
        atr        : 日线 ATR14，用来衡量「这只票平时一天波动多大」
        now_time   : datetime.time；不传则取当前北京时间
        direction  : 做T指引方向（"正T"/"反T"/"不做"），用于判断「指引」与「盘中位置」是否打架

    返回 dict。ok=False 表示数据不足 —— 调用方直接跳过渲染即可，本函数不会抛异常。
    """
    out = {"ok": False, "shape": "", "shape_read": "", "stage": "", "pos": 0.0, "pos_pct": 0.0,
           "chg": 0.0, "avg_dev": 0.0, "speed": 0.0, "speed_label": "", "win": 0,
           "range_atr": 0.0, "range_label": "", "range_pct": 0.0,
           "read": "", "scenarios": "", "conflict": "",
           "active_no": 0, "active_label": "", "active_kind": "flat", "active_mode": "",
           "active_line": "",
           "open_price": 0.0, "day_high": 0.0, "day_low": 0.0,
           "avg_line": 0.0, "mid_line": 0.0, "up_trigger": 0.0, "down_trigger": 0.0}
    try:
        if df_minute is None or getattr(df_minute, "empty", True):
            return out
        dfm = df_minute[df_minute["Time"] <= "1500"]
        if len(dfm) < INTRADAY_STRUCT_MIN_BARS:
            return out
        px = pd.to_numeric(dfm["Price"], errors="coerce").dropna().reset_index(drop=True)
        if len(px) < INTRADAY_STRUCT_MIN_BARS:
            return out
        n = len(px); span = max(n - 1, 1)
        open_price = float(px.iloc[0]); cur = float(px.iloc[-1])
        day_high = float(px.max()); day_low = float(px.min())
        rng = day_high - day_low
        _pc = 0.0
        try:
            _pc = float(prev_close) if prev_close is not None else 0.0
        except (TypeError, ValueError):
            _pc = 0.0
        base = _pc if _pc > 0 else open_price
        if base <= 0:
            return out
        avg_line = 0.0
        if "AvgPrice" in dfm.columns:
            _av = pd.to_numeric(dfm["AvgPrice"], errors="coerce").dropna()
            if len(_av): avg_line = float(_av.iloc[-1])
        want_time = now_time if now_time is not None else now_cn().time()
        stage = _intraday_stage_text(want_time)

        pos = (cur - day_low) / rng if rng > 1e-9 else 0.5
        chg = (cur - base) / base * 100
        avg_dev = (cur - avg_line) / avg_line * 100 if avg_line > 0 else 0.0
        range_pct = rng / base * 100
        high_ratio = int(px.idxmax()) / span
        low_ratio = int(px.idxmin()) / span
        # ★ 2026-09-21：再算一份「**最后一次**触及极值」的位置，专供情景判定用。
        #   上面两个 high_ratio / low_ratio 是**形态**用的（问的是「极值出现在前段还是后段」），
        #   语义不能混用：高位横盘会反复碰到同一个高点，拿「第一次出现的位置」会得出
        #   「一直贴着上沿」=「极值早过去了」这种自相矛盾的结论。
        _tol = max(rng * 1e-3, 1e-9)
        _hi_hits = px.index[px >= day_high - _tol].tolist()
        _lo_hits = px.index[px <= day_low + _tol].tolist()
        hi_touch = (max(_hi_hits) if _hi_hits else int(px.idxmax())) / span
        lo_touch = (max(_lo_hits) if _lo_hits else int(px.idxmin())) / span

        # 近窗口振幅：既用于「形态」（最近是横着还是动着），也用于「速度」的分子
        win = min(INTRADAY_SPEED_WINDOW, max(span, 1))
        _tail = px.tail(win + 1)
        recent_rng = float(_tail.max()) - float(_tail.min())
        recent_ratio = recent_rng / rng if rng > 1e-9 else 0.0

        # ---- 形态：先看「最近是否还在动」，再按方向 + 极值出现的时间位置区分单边 / 反转 ----
        if range_pct < INTRADAY_NARROW_PCT:
            shape = "窄幅盘整"
        elif recent_ratio <= INTRADAY_STILL_RATIO or abs(cur - open_price) / open_price * 100 <= INTRADAY_FLAT_PCT:
            shape = ("高位横盘" if pos >= INTRADAY_HIGH_POS
                     else "低位横盘" if pos <= INTRADAY_LOW_POS else "横盘整理")
        elif cur > open_price:
            if pos >= INTRADAY_HIGH_POS:
                shape = "单边上行" if low_ratio <= INTRADAY_EDGE_RATIO else "探底回升"
            elif pos >= 0.45:
                shape = "震荡上行"
            else:
                shape = "冲高回落"
        else:
            if pos <= INTRADAY_LOW_POS:
                shape = "单边下行" if high_ratio <= INTRADAY_EDGE_RATIO else "冲高回落"
            elif pos <= 0.55:
                shape = "震荡下行"
            else:
                shape = "探底回升"

        # ---- 速度：近窗口每分钟振幅 ÷ 当日至今平均每分钟振幅。
        #      自归一化（不用 ATR 当标尺）—— 早期用 ATR/240 当基准会把「开盘本来就活跃」
        #      误判成「上下太快」，实测偏差过大，弃用。
        per_min_all = rng / span
        per_min_recent = recent_rng / win
        if span < INTRADAY_SPEED_WINDOW:
            speed = 0.0; speed_label = "样本不足"
        elif per_min_all <= 1e-9:
            speed = 0.0; speed_label = "几乎不动"
        else:
            speed = per_min_recent / per_min_all
            if speed >= INTRADAY_SPEED_FAST: speed_label = "上下太快"
            elif speed >= INTRADAY_SPEED_WARM: speed_label = "波动加快"
            elif speed <= INTRADAY_SPEED_CALM: speed_label = "波动收敛"
            else: speed_label = "波动平稳"

        _atr = 0.0
        try:
            _atr = float(atr)
        except (TypeError, ValueError):
            _atr = 0.0
        if pd.isna(_atr) or _atr <= 0: _atr = 0.0
        range_atr = rng / _atr if _atr > 0 else 0.0
        if range_atr == 0.0: range_label = "无参照"
        elif range_atr >= INTRADAY_RANGE_ATR_BIG: range_label = "全天大波动"
        elif range_atr <= INTRADAY_RANGE_ATR_CALM: range_label = "波动收敛"
        else: range_label = "波动正常"

        # ---- 一句话结论 ----
        pos_label = "日内高位" if pos >= INTRADAY_HIGH_POS else ("日内低位" if pos <= INTRADAY_LOW_POS else "日内中位")
        avg_label = "在均价线上方" if avg_dev > 0.05 else ("在均价线下方" if avg_dev < -0.05 else "贴着均价线")
        shape_read = INTRADAY_SHAPE_READ.get(shape, "盘面结构不典型。")
        read = f"[{stage}] 今日涨跌 {chg:+.2f}%。{shape_read}"
        read += f" 现价 {cur:.3f} 处于{pos_label}（当日区间 {pos * 100:.0f}% 分位），{avg_label} {avg_dev:+.2f}%。"
        read += f" 当日振幅 {range_pct:.2f}%（约为该股日均波动的 {range_atr:.2f} 倍）。"
        if speed > 0:
            read += f" 近 {win} 分钟波动是今日平均的 {speed:.2f} 倍（{speed_label}）。"
        else:
            read += f" 开盘不足 {INTRADAY_SPEED_WINDOW} 分钟，还测不出波动是否在加速。"
        if speed_label == "上下太快":
            read += (" —— 现在是**直上直下**的段落，看不懂方向很正常：别在这个节奏里追涨杀跌，"
                     "等一根缩量、振幅收窄的小 K 线（波动收敛）再动手。")

        # ---- 三种情景 + 触发价 + ★「当前正在走哪一条」（2026-09-21 新增）----
        # 起因：用户看着这三条问「能不能每天动态地标注一下是哪种状态」——
        # 静态预案每次都要自己拿现价去对。现在按「位置 + 均价线 + 极值是不是刚创出来」
        # 定出此刻那一条，并在**标签行**与**情景行**上同时标出来。
        mid = avg_line if avg_line > 0 else (day_high + day_low) / 2
        _pick = _intraday_pick_scenario(pos, avg_dev, cur, avg_line, day_high, day_low,
                                        rng, hi_touch, lo_touch,
                                        boxed=shape in INTRADAY_BOXED_SHAPES)
        _active_no = _pick["no"]
        # 数据截止到哪一根分钟线 —— 让「动态」可核对（不然无法判断看到的是不是最新读数）
        _last_t = str(dfm["Time"].iloc[-1]).strip().zfill(4)
        _cut = (f"{_last_t[:2]}:{_last_t[2:4]}" if _last_t.isdigit()
                else want_time.strftime("%H:%M"))
        _scen_lines = {
            1: (f"**① 偏强 —— 看延续上行**：放量站上 **{day_high:.3f}**（今日高点）且不破，"
                f"前高之上还有空间；回踩不破均价线 {mid:.3f} 可跟。"),
            2: (f"**② 中性 —— 看区间反复**：在 **{day_low:.3f} ~ {day_high:.3f}** 之间围绕均价线 "
                f"{mid:.3f} 来回震荡 —— 那就只做两头（贴上下沿反向操作），不追中间。"),
            3: (f"**③ 偏弱 —— 看继续下探**：跌破 **{day_low:.3f}**（今日低点）且反抽无力，"
                f"下一档参考 **{day_low - rng * 0.5:.3f}**（今日低点再下移半个当日振幅）。"),
        }
        # ⚠️ [[...]] 只由带 scen_mark 开关的那次调用转换；普通文本一律不传该开关。
        scenarios = "\n".join(
            (f"[[▶ 当前]] {_scen_lines[k]}" if k == _active_no else _scen_lines[k])
            for k in (1, 2, 3))
        active_line = (
            f"▶ **此刻走 {_pick['label']}**（截至 {_cut} 的分时）—— {_pick['reason']}。"
            + ("这是刚贴上临界价、情景**刚开始**的时刻，最该盯的就是它。"
               if _pick["mode"] == "break"
               else "位置没变就按这一条执行；破了边界会自动切到另一条。"))

        # ---- 指引与盘中位置是否打架（最容易亏钱的地方，必须明说）----
        conflict = ""
        if direction == "正T" and pos >= INTRADAY_HIGH_POS:
            conflict = (f"⚠️ **指引与盘中位置打架**：日线指引偏「正T（先买后卖）」，但现价已在日内区间 "
                        f"**{pos * 100:.0f}%** 的高位、距今日高点只剩 {day_high - cur:.3f} —— "
                        f"此刻低吸等于买在日内天花板下沿。建议等回踩均价线 **{mid:.3f}** 附近再看，别在这个位置追。")
        elif direction == "反T" and pos <= INTRADAY_LOW_POS:
            conflict = (f"⚠️ **指引与盘中位置打架**：日线指引偏「反T（先卖后买）」，但现价已在日内区间 "
                        f"**{pos * 100:.0f}%** 的低位、距今日低点只剩 {cur - day_low:.3f} —— "
                        f"此刻高抛等于砍在日内地板上。建议等反抽均价线 **{mid:.3f}** 附近再减。")
        elif direction == "正T" and pos <= INTRADAY_LOW_POS and avg_dev < 0:
            conflict = (f"✅ **指引与盘中位置一致**：偏「正T低吸」，而现价正好在日内低位（{pos * 100:.0f}%）"
                        f"且位于均价线下方 —— 位置是对的，但仍要等**缩量止跌**（不再创新低）再出手。")
        elif direction == "反T" and pos >= INTRADAY_HIGH_POS and avg_dev > 0:
            conflict = (f"✅ **指引与盘中位置一致**：偏「反T高抛」，而现价正好在日内高位（{pos * 100:.0f}%）"
                        f"且冲在均价线上方 —— 位置是对的，冲高滞涨（不再创新高）即可减仓。")

        out.update({
            "ok": True, "shape": shape, "shape_read": shape_read, "stage": stage,
            "pos": round(pos, 4), "pos_pct": round(pos * 100, 1),
            "chg": round(chg, 2), "avg_dev": round(avg_dev, 2),
            "speed": round(speed, 2), "speed_label": speed_label, "win": win,
            "range_atr": round(range_atr, 2), "range_label": range_label,
            "range_pct": round(range_pct, 2),
            "read": read, "scenarios": scenarios, "conflict": conflict,
            "active_no": _active_no, "active_label": _pick["label"],
            "active_kind": _pick["kind"], "active_mode": _pick["mode"],
            "active_line": active_line,
            "open_price": round(open_price, 3), "day_high": round(day_high, 3),
            "day_low": round(day_low, 3), "avg_line": round(avg_line, 3),
            "mid_line": round(mid, 3), "up_trigger": round(day_high, 3),
            "down_trigger": round(day_low, 3),
        })
        return out
    except Exception as e:
        _log("analyze_intraday_structure", e)
        return out


def _intraday_rich_text(text, scen_mark=False):
    """把「今日看盘」四个盒子里的文案渲染成 HTML：自己转义、自己换行。

    用于 struct-box / ai-advice-box / guide-box / predict-box —— 它们的文案都是塞在
    `<div ...>` 里的 raw HTML。Streamlit 前端走的是 `allowDangerousHtml` 直通 HTML，
    **raw HTML 块内部的 markdown 不会被解析**（已在前端包 StreamlitMarkdown 里确认）：
    原样传 `**今日不做T**` 会显示成星号，而不是加粗。所以这里自己转，不赌渲染器行为。

    ★ scen_mark=True（2026-09-21 新增）：额外把 `[[x]]` 转成「当前情景」标注 chip。
      只有「三种情景」那一栏传这个开关 —— 别的盒子（尤其 AI 输出）一律不传，
      免得文本里恰好出现 `[[...]]` 被误当成标注。
    """
    s = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    if scen_mark:
        s = re.sub(r"\[\[(.+?)\]\]", r'<span class="scen-now">\1</span>', s)
    return s.replace("\n", "<br>")


# ================= 8. 核心策略判定 =================
def generate_report_and_advice(df_daily, df_minute, deviation, market_change, prev_close=None):
    latest = df_daily.iloc[-1]; prev = df_daily.iloc[-2]
    trend = "震荡"
    if latest['MA20_UP'] and latest['Close'] > latest['MA20']: trend = "上升"
    elif not latest['MA20_UP'] and latest['Close'] < latest['MA20']: trend = "下降"
    is_steady = False
    price_stop = latest['Close'] > prev['Close'] or (latest['Close'] - latest['Low']) > (latest['High'] - latest['Close'])
    vol_stop = latest['Volume'] < latest['VOL_MA5'] * 1.5
    support_hold = latest['Close'] > latest['BOLL_LOW'] * 0.98
    narrow_vol = latest['BOLL_WIDTH'] < 0.15
    if price_stop and vol_stop and support_hold: is_steady = True
    pattern = "无明显形态"
    if is_steady:
        if latest['BOLL_WIDTH'] < 0.08 and abs(latest['MA5'] - latest['MA20']) < 0.01: pattern = "均线粘合走平"
        elif latest['Close'] > latest['MA20'] and prev['Close'] < prev['MA20']: pattern = "W底/N字结构"
        elif narrow_vol: pattern = "缩量横盘"
    narrow_count = 0
    for i in range(1, 8):
        if df_daily['BOLL_WIDTH'].iloc[-i] < 0.08: narrow_count += 1
    flat_warning = " (均盘必跌风险)" if narrow_count >= 5 else ""
    allow_t = "允许"; direction = "正T"
    if trend == "下降" and not is_steady: allow_t = "不允许"; direction = "不做"
    elif trend == "下降" and is_steady: allow_t = "允许"; direction = "反T"
    elif trend == "震荡": direction = "正T" if latest['Close'] < latest['BOLL_MID'] else "反T"
    support = round(min(latest['MA20'], latest['BOLL_LOW']), 3); resistance = round(max(latest['Close'] * 1.02, latest['BOLL_UP']), 3)
    best_buy = "无有效点"; best_sell = "无有效点"; buy_points = pd.DataFrame(); sell_points = pd.DataFrame(); buy_warning = ""; divergence_info = ""
    best_buy_price = 0.0; best_sell_price = 0.0   # 同时保留数值，避免下游再去 split 自己拼的字符串
    # ★ 2026-09-20 拆开两个量：区间上沿/下沿并不是「预测」—— 它是「已实现极值」与
    #   「剩余时段外推」取并集，上午冲过一次高之后，上沿会长时间停在那根已发生的高点上。
    #   所以额外把「剩余时段预估振幅」单独留出来（intraday_pred_offset），文案里如实写明。
    intraday_high_predict = 0; intraday_low_predict = 0
    intraday_pred_offset = 0.0; intraday_pred_mode = ""
    cur_price = 0.0; day_high = 0.0; day_low = 0.0
    if not df_minute.empty:
        cur_price = float(df_minute['Price'].iloc[-1]); day_high = float(df_minute['Price'].max()); day_low = float(df_minute['Price'].min())
        atr = latest['ATR14'] if not pd.isna(latest['ATR14']) else cur_price * 0.02
        now_time = now_cn().time()
        if now_time < time(9, 30):
            intraday_high_predict = round(latest['Close'] + atr * 0.5, 3); intraday_low_predict = round(latest['Close'] - atr * 0.5, 3)
            intraday_pred_offset = atr * 0.5; intraday_pred_mode = "盘前"
        elif now_time > time(15, 0):
            intraday_high_predict = day_high; intraday_low_predict = day_low
            intraday_pred_offset = 0.0; intraday_pred_mode = "收盘结算"
        else:
            _today = now_cn().date()
            current_dt = datetime.combine(_today, now_time); start_am = datetime.combine(_today, time(9, 30)); end_am = datetime.combine(_today, time(11, 30)); start_pm = datetime.combine(_today, time(13, 0))
            passed_minutes = (current_dt - start_am).total_seconds() / 60 if current_dt <= end_am else 120 + (current_dt - start_pm).total_seconds() / 60
            passed_minutes = max(passed_minutes, 1); remaining_minutes = max(240 - passed_minutes, 0)
            if passed_minutes > 10: realized_volatility_per_min = (day_high - day_low) / passed_minutes; remaining_range = realized_volatility_per_min * remaining_minutes
            else: remaining_range = atr * 0.5
            dynamic_offset = min(remaining_range, atr) * 0.6
            intraday_pred_offset = dynamic_offset; intraday_pred_mode = "盘中"
            intraday_high_predict = round(max(day_high, cur_price + dynamic_offset), 3); intraday_low_predict = round(min(day_low, cur_price - dynamic_offset), 3)
    sig = compute_intraday_signals(df_minute, deviation) if allow_t == "允许" else None
    if sig:
        df_min = sig['df_min']; day_high = sig['day_high']; day_low = sig['day_low']
        buy_points = sig['buy']; sell_points = sig['sell']
        divergence_info = _intraday_divergence(df_min)
        if market_change < -1.0: buy_warning = " ⚠️大盘暴跌，低置信度！"
        if not buy_points.empty:
            best_row = buy_points.loc[buy_points['Price'].idxmin()]; time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            best_buy_price = float(best_row['Price'])
            dev_pct = (best_row['AvgPrice'] - best_row['Price']) / best_row['AvgPrice'] if best_row['AvgPrice'] > 0 else 0
            is_scheme_a = bool((best_row['Price'] < best_row['AvgPrice'] * (1 - deviation * 0.7)) and (best_row['MACD_UP'] if 'MACD_UP' in best_row else False))
            conf = "低" if market_change < -1.0 else ("高" if dev_pct > deviation * 1.5 or "底背离" in divergence_info else ("中" if dev_pct > deviation * 0.8 or is_scheme_a else "低"))
            b_type = "正T低吸" if direction == "正T" else "反T回补"
            reason = "回踩均价线" if is_scheme_a else "接近日内低点止跌"
            best_buy = f"{time_fmt} | {best_row['Price']:.3f} | {b_type} | {reason}{divergence_info} | 置信度{conf}{buy_warning}"
        if not sell_points.empty:
            best_row = sell_points.loc[sell_points['Price'].idxmax()]; time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            best_sell_price = float(best_row['Price'])
            dev_pct = (best_row['Price'] - best_row['AvgPrice']) / best_row['AvgPrice'] if best_row['AvgPrice'] > 0 else 0
            is_scheme_a = bool((best_row['Price'] > best_row['AvgPrice'] * (1 + deviation * 0.7)) and (best_row['MACD_DOWN'] if 'MACD_DOWN' in best_row else False))
            conf = "高" if dev_pct > deviation * 1.5 or "顶背离" in divergence_info else ("中" if dev_pct > deviation * 0.8 or is_scheme_a else "低")
            s_type = "正T高抛" if direction == "正T" else "反T减仓"
            reason = "冲高乖离均价线" if is_scheme_a else "接近日内高点滞涨"
            best_sell = f"{time_fmt} | {best_row['Price']:.3f} | {s_type} | {reason}{divergence_info} | 置信度{conf}"
    today_str = latest['Date'].strftime('%Y-%m-%d'); time_str = now_cn().strftime('%H:%M'); market_status = f"上证 {market_change:+.2f}%"
    market_color = "color-green" if market_change >= 0 else "color-red"
    report = f"""
    <div class="report-row"><span class="color-blue">日期:</span> <span class="color-white">{today_str}</span><span class="color-blue">数据时间:</span> <span class="color-white">{time_str}</span><span class="color-blue">大盘:</span> <span class="{market_color}">{market_status}</span><span class="color-blue">阈值:</span> <span class="color-white">{deviation*100:.2f}%</span></div>
    <div class="report-row"><span class="color-blue">日线趋势:</span> <span class="color-white">{trend}</span><span class="color-blue">是否企稳:</span> <span class="color-white">{'是' if is_steady else '否'}</span><span class="color-blue">企稳形态:</span> <span class="color-white">{pattern}{flat_warning}</span></div>
    <div class="report-row"><span class="color-blue">做T方向:</span> <span class="color-white">{direction}</span><span class="color-blue">关键支撑:</span> <span class="color-white">{support:.3f}</span><span class="color-blue">关键压力:</span> <span class="color-white">{resistance:.3f}</span></div>
    <div class="report-row"><span class="color-red">最优买点:</span> <span class="color-red">{best_buy}</span></div>
    <div class="report-row"><span class="color-green">最优卖点:</span> <span class="color-green">{best_sell}</span></div>
    <div class="report-row"><span class="color-blue">失效条件:</span> <span class="color-white">跌破支撑 {support:.3f} 或 日线趋势转下降</span></div>
    """
    ai_advice = ""
    if market_change < -1.5: ai_advice = f"🚨 **大盘熔断警告**：上证跌幅 {market_change:.2f}%，暂停一切正T低吸，观望为主。"
    elif flat_warning: ai_advice = f"⚠️ **久盘必跌警告**：均线粘合超过5天，若触发买点请**仓位减半**，跌破 {support:.3f} 立刻离场！"
    elif allow_t == "不允许": ai_advice = f"📉 **不允许做T**：日线下降趋势且未企稳，严禁抄底做正T。仅可少量反T减仓。"
    elif direction == "反T": ai_advice = f"🔄 **优先反T**：日线下降趋稳或震荡上沿。先抛后买，冲高乖离均价线减仓，回踩 {support:.3f} 附近回补。"
    else:
        if best_buy_price > 0:
            stop_loss = best_buy_price * 0.995
            ai_advice = f"🔥 **适合正T低吸**：已触发买点（{best_buy.split('|')[0].strip()}）。仓位≤底仓30%，止损 {stop_loss:.3f}，目标 {resistance:.3f}。"
        else: ai_advice = f"⏳ **等待正T买点**：日线向上且企稳，但分时价格尚未回踩均价线企稳，耐心等待。"
    if allow_t == "不允许": t_guide = f"**今日不做T** —— 日线处于下降趋势且未企稳，风险大于收益。\n\n操作建议：\n1. 空仓观望或仅持底仓不动。\n2. 若盘中有冲高至压力位 {resistance:.3f} 附近，可少量反T减仓。\n3. 等待日线企稳信号（缩量止跌+支撑不破）再考虑重新入场。"
    elif direction == "反T": t_guide = f"**今日优先做反T（先卖后买）** —— 日线下降趋稳或震荡区间上沿。\n\n操作步骤：\n1. **高抛**：当分时价格冲高至均价线以上 {deviation*100:.2f}% 且放量滞涨时，减仓 30%。\n2. **低吸回补**：待价格回落至日线支撑 {support:.3f} 附近缩量企稳时，用同等仓位买回。\n3. **止损**：若回补后跌破 {support:.3f}，立刻止损。\n4. **仓位**：单次不超过底仓 30%。"
    else: t_guide = f"**今日优先做正T（先买后卖）** —— 日线趋势向上且已企稳。\n\n操作步骤：\n1. **低吸**：当分时价格回踩均价线以下 {deviation*100:.2f}% 且缩量企稳时，买入 30% 仓位。\n2. **高抛**：待价格冲高至压力位 {resistance:.3f} 附近且放量滞涨时，卖出回补的仓位。\n3. **止损**：若买入后跌破买入价 0.5%，立刻止损。\n4. **仓位**：单次不超过底仓 30%，单日最多操作 2-3 次。"
    if intraday_high_predict > 0:
        if intraday_pred_offset <= 0 and intraday_pred_mode == "收盘结算":
            predict_text = (f"**今日波动区间（收盘结算）**：\n"
                            f"- 今日最高：**{intraday_high_predict:.3f}**\n"
                            f"- 今日最低：**{intraday_low_predict:.3f}**\n\n"
                            f"⚠️ 已收盘 —— 这里显示的是今日**实际**极值，不是预测。")
        elif intraday_pred_mode == "盘前":
            predict_text = (f"**今日预估波动区间（盘前）**：\n"
                            f"- 预估最高：**{intraday_high_predict:.3f}**\n"
                            f"- 预估最低：**{intraday_low_predict:.3f}**\n"
                            f"- 参考价（昨收）：**{latest['Close']:.3f}**\n\n"
                            f"⚠️ 开盘前只能按昨收 ± 半个 ATR 估，开盘后会自动转成按实时波动率算。")
        else:
            predict_text = (f"**今日波动区间（盘中动态）**：\n"
                            f"- 今日**已**出现：最高 **{day_high:.3f}** / 最低 **{day_low:.3f}**\n"
                            f"- 剩余时段预估振幅：**±{intraday_pred_offset:.3f}**（现价 {cur_price:.3f}）\n"
                            f"- 全天预估区间：**{intraday_low_predict:.3f} ~ {intraday_high_predict:.3f}**\n\n"
                            f"⚠️ 全天区间 = 「今日已实现的极值」与「剩余时段按实时波动率外推」取并集。"
                            f"所以只要上午冲过一次高，上沿就会长时间停在那个**已经发生过的**最高价上 —— "
                            f"它回答的是「今天已经走过哪儿」，不是「接下来要去哪儿」。"
                            f"要看接下来怎么走，读下方的「今日盘面动态分析」。")
    else: predict_text = "数据不足，无法预测日内极值。"
    # ★ 盘面结构：与「极值预测」互补 —— 预测给区间，结构给「现在处在什么走势的哪一段」。
    intraday_struct = analyze_intraday_structure(df_minute, prev_close, latest['ATR14'],
                                                 now_cn().time(), direction)
    context = f"""【当前盘面实时数据】\n标的: {current_name} ({symbol})\n当前价格: {latest['Close']:.3f}\n日线趋势: {trend}\n是否企稳: {'是' if is_steady else '否'}\n做T方向: {direction}\n关键支撑: {support:.3f}\n关键压力: {resistance:.3f}\n大盘涨跌幅: {market_change:.2f}%\n当前最优买点: {best_buy}\n当前最优卖点: {best_sell}\n日内预估最高: {intraday_high_predict:.3f}\n日内预估最低: {intraday_low_predict:.3f}"""
    if intraday_struct.get("ok"):
        _spd = (f"{intraday_struct['speed']:.2f} 倍" if intraday_struct['speed'] > 0
                else "开盘不足 30 分钟，测不出加速度")
        context += (f"\n\n【今日盘面结构】\n"
                    f"形态: {intraday_struct['shape']}（盘中阶段: {intraday_struct['stage']}）\n"
                    f"日内位置: {intraday_struct['pos_pct']:.0f}%（0=今日最低，100=今日最高）\n"
                    f"现价相对昨收: {intraday_struct['chg']:+.2f}%\n"
                    f"相对均价线: {intraday_struct['avg_dev']:+.2f}%\n"
                    f"当日振幅: {intraday_struct['range_pct']:.2f}%（约为该股日均波动的 {intraday_struct['range_atr']:.2f} 倍）\n"
                    f"近30分钟波动速度: {intraday_struct['speed_label']}（{_spd}）\n"
                    f"结构判读: {intraday_struct['read']}")
        if intraday_struct.get("conflict"):
            context += f"\n指引冲突提示: {intraday_struct['conflict']}"
    return report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest, intraday_struct

# ================= 9. 全天候监控所有自选股 =================
def monitor_all_watchlist(send_key, market_change):
    """扫一遍自选股的分时信号，返回本次新出现的信号文案（供 toast）。

    ★ 2026-09-22 按用户要求改：**不再自动发微信**，只把候选记进
      `st.session_state['manual_push_candidates']`，由用户在「📤 今日推送」里点。
      原因：Server酱 免费版每天 5 条，1 条留给 18:00 日报，
      用户要自己决定剩下 4 条推什么（原话「剩下的全部交给我来手动选择」）。
    ★ 参数 `send_key` 保留原签名（调用方与历史前测都按这个签名调）：
      没配 key 时直接返回 —— 反正推不出去，连候选都不必算。
    ★ should_notify/mark_notified 继续用，语义从「该不该推」变成「今天这个信号登记过没」：
      不记的话，每 5 分钟一次 rerun 都会把同一条候选重复塞进列表。
    """
    if not send_key or not is_trading_time():
        return []
    watchlist = st.session_state.get('stock_list', [])
    if not watchlist:
        return []
    cands = list(st.session_state.get('manual_push_candidates') or [])
    _known = {(c.get('code'), c.get('kind')) for c in cands}
    fired = []
    for sym in watchlist:
        try:
            sym_code = _get_code(sym); df_min = get_minute_data(sym_code)
            if df_min is None or df_min.empty: continue
            dev = dynamic_deviation(df_min)
            sig = compute_intraday_signals(df_min, dev)
            if not sig: continue
            sym_name = get_stock_name(sym)
            for _sig, _pts, _pick in (('buy', sig['buy'], 'min'), ('sell', sig['sell'], 'max')):
                if _pts is None or _pts.empty: continue
                _row = (_pts.loc[_pts['Price'].idxmin()] if _pick == 'min'
                        else _pts.loc[_pts['Price'].idxmax()])
                _price = float(_row['Price'])
                if _sig == 'buy' and market_change < -1.0: continue   # 大盘暴跌不报买点（原逻辑保留）
                if not should_notify(sym, _sig, _price): continue
                mark_notified(sym, _sig, _price)
                if (sym, _sig) in _known: continue
                _known.add((sym, _sig))
                _t = f"{_row['Time'][:2]}:{_row['Time'][2:]}"
                _is_buy = (_sig == 'buy')
                cands.append({
                    'code': str(sym), 'name': sym_name, 'kind': _sig,
                    'status': '买点' if _is_buy else '卖点',
                    'price': _price, 'ts': now_cn_str('%Y-%m-%d %H:%M:%S'),
                    'title': (f"【买点提醒】{sym_name}" if _is_buy
                              else f"【卖点提醒】{sym_name}"),
                    'body': (f"股票：{sym_name} ({sym})\n时间：{_t}（北京时间）\n"
                             f"价格：{_price:.3f}\n依据："
                             + ('回踩均价线缩量，MACD 拐头向上' if _is_buy
                                else '冲高乖离均价线放量，MACD 拐头向下')
                             + "\n\n仅做参考，请自行判断。"),
                })
                _emoji = '🔴' if _is_buy else '🟢'
                _label = '买点' if _is_buy else '卖点'
                fired.append(f"{_emoji} {sym_name} {_label} {_price:.3f}")
        except Exception as e:
            _log(f"monitor_all_watchlist/{sym}", e)
            continue
    if cands:
        st.session_state['manual_push_candidates'] = cands[-30:]   # 别无限长
    return fired

# ================= 10. 波段做T选股助手 =================
# 改动：由"低位启动"改为"突破平台+放量"的波段启动确认，并预警顶背离/跌破支撑等波段结束信号。
# 筛选范围：排除科创/创业板/北交所/ST/退市风险股。
EXCLUDE_PREFIXES = ('688', '300', '301', '8', '4', '92')

_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# 东财多 host 轮询池（当前网络环境下 push2delay 通常最稳，其余为兜底）
# 三处东财调用原先各写了一份同样的列表，已统一到这里，新增调用点请直接复用。
_EM_HOSTS = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com",
             "https://7.push2.eastmoney.com", "https://17.push2.eastmoney.com",
             "https://29.push2.eastmoney.com", "https://82.push2.eastmoney.com"]

def _safe_float(v, default=0.0):
    """把东方财富接口返回的 '-' / None / NaN / inf 等脏值安全转成 float，绝不抛异常。"""
    try:
        if v is None or v == '' or v == '-':
            return default
        f = float(v)
        if f != f or f == float('inf') or f == float('-inf'):
            return default
        return f
    except (TypeError, ValueError):
        return default

def _safe_float_or_none(v):
    """东财脏值（'-' / None / NaN / inf）→ float 或 None。

    与 _safe_float 的区别：无有效数据时返回 None 而非 0，用于「无数据」和「值为 0」
    需要区分的场景（如 PE、PB 这类本来就不该为 0 的指标）。"""
    try:
        if v is None or v == '' or v == '-':
            return None
        f = float(v)
        if f != f or f == float('inf') or f == float('-inf'):
            return None
        return f
    except (TypeError, ValueError):
        return None

def _is_st_or_risk(name):
    """剔除 ST、*ST、退市等风险股。"""
    n = (name or '').upper()
    return 'ST' in n or '*ST' in n or '退' in (name or '')

def _diff_to_list(diff):
    """东财接口有时返回列表、有时返回字典，统一转成列表。"""
    if isinstance(diff, dict):
        try: return list(diff.values())
        except Exception: return []
    if isinstance(diff, list): return diff
    return []

@st.cache_data(ttl=300)
def fetch_market_page(pn, pz=100):
    """分页拉取沪深 A 股列表（按主力资金流降序），自动切换可用 host，单页失败只损失该页。"""
    params = {"pn": str(pn), "pz": str(pz), "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f62", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
              "fields": "f12,f14,f2,f3,f8,f9,f10,f20,f62"}
    for base_url in _EM_HOSTS:
        try:
            res = requests.get(f"{base_url}/api/qt/clist/get", params=params, timeout=8, headers=_REQUEST_HEADERS)
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("data") and data["data"].get("diff"):
                return _diff_to_list(data["data"]["diff"])
        except Exception as e:
            _log(f"fetch_market_page@{base_url}", e)
            continue
    return []

def _get_daily_history(symbol):
    """波段分析用日线（至少 60 条）。腾讯(前复权) → 东财(前复权) → 新浪(不复权) 依次兜底。

    ★ 这是**波段选股**的取数热路径：一次扫描要对几百只票各取一次（16 线程并发）。
      所以这里必须挂熔断器 —— 某个源死掉时，不做熔断就等于「这几百只票每只都为它付一次
      超时 + 重试」，整轮扫描会被拖到被页面 60 秒自动刷新掐断（实测形态：结果全丢）。
    ★ 顺序与 `_try_fetch_kline` 一致：前复权源优先，新浪（不复权）压最后。
    """
    code = f"{_quote_prefix(symbol)}{str(symbol).strip()}"
    for name, fn in (("腾讯", _fetch_kline_qq), ("东财", _fetch_kline_em),
                     ("新浪", _fetch_kline_sina)):
        if not _feed_alive(name):
            continue
        try:
            df, _src, errs = fn(code, limit=300)
            if df is not None and len(df) >= 60:
                _feed_note(name, errs, True)
                return df
            _feed_note(name, errs, False)
        except Exception as e:
            _log("_get_daily_history", e)
            _feed_note(name, [("异常", str(e))], False)
            continue
    return None

BAND_RUN_MAX_LOOKBACK = 120   # 一轮「启动」最多往回找多少根 K 线，防止极端行情下算到一年前
BAND_RUN_CROWD_FREE_PCT = 15.0  # 离启动点涨幅在多少以内不扣分（超过部分才按拥挤度扣）

def _band_run_start(df):
    """本轮「波段启动」连续区间是从哪根 K 线开始的？→ {'days','date','price','idx'}。

    ★ 为什么需要它（2026-09-22 用户反馈原话）：
      「你看之前挑选出来的像是百亚股份这种，看起来已经涨了很多很多了的，
        为什么还要说它是启动呢？所以说能不能在股票启动确认的位置标注出来，
        让我明确地知道它已经启动确认了」。

      根因：「波段启动确认」是一个**可以持续很多天**的状态 —— 判定只看**当日**是否
      「突破 60 日平台 + 放量」（见 `_band_status`）。一只 8 月启动、之后一路创新高放量的票，
      **每天都在满足**这个条件，于是标签一直挂着「启动确认」不放。
      标签本身不带时间，看上去就成了「它今天才启动」—— 而实际它已经走了两个月。
      所以这里把「这一轮是从哪天开始的」单独算出来，交给展示层写成
      「已启动 N 个交易日 · 起点 2026-07-15 · 至今 +38.2%」。

    ★ 算法：把「启动确认」的两个条件**按日向量化重算**一遍，再从最后一根往回走，
      走到第一个不满足的 K 线为止，它的下一根就是本轮起点。
      **阈值必须与 `_calculate_band_metrics` 里那一份逐字相同**（0.995 / 0.999 / 1.5），
      否则会出现「卡片说今天启动、涨幅却按另一天算」这种自相矛盾。
      ⚠️ 改动这里必须同时改 watcher.py 的 `_band_run_start`（有跨文件一致性测试守着）。

    返回 days=0 表示**最后一根 K 线不满足启动条件**（例如已经跌破 20 日线）——
    这不是"没算出来"，调用方据此显示「—」，绝不编一个数出来。
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
        # 与 _calculate_band_metrics 同一组阈值：突破平台 + 量能放大
        ok = ((close >= plat_high * 0.995) & (close >= close_high * 0.999)
              & ((vr >= 1.5) | (vol >= vol20 * 1.5)))
        ok = ok.fillna(False).astype(bool)
        # rolling 窗口没填满的那些日子不作判定（fillna 只挡 NaN，不挡"窗口只有 3 根"）
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
        # 算不出来不能拖垮选股/刷新：如实记日志，返回"没算出来"，由展示层降级成「—」
        _log("_band_run_start", e)
        return {'days': 0, 'date': '', 'price': 0.0, 'idx': -1}


def _calculate_band_metrics(df):
    """计算波段相关指标：平台区间、突破、均线、MACD、顶背离、跌破支撑。"""
    close = df['Close']; high = df['High']; low = df['Low']; vol = df['Volume']
    current = float(close.iloc[-1])
    if current <= 0:
        return None

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1])

    lookback = 60
    platform_high = float(high.tail(lookback).max())
    platform_low = float(low.tail(lookback).min())
    platform_range_pct = (platform_high - platform_low) / platform_low * 100 if platform_low > 0 else 100

    # ★ 突破位 = **突破前**的平台上沿（不含当天）。为什么必须把当天排除掉：
    #   `platform_high` 是**含当天**的 60 日最高价 ⇒ 任何「今天创新高」的票它都等于现价，
    #   拿它当「突破位」或「目标」必然贴脸 —— 这正是 2026-09-21 用户反馈
    #   「这个目标为什么这么接近启动价格…是不是有点开玩笑了」的根因。
    #   窗口不足一个完整周期时记 0，由展示层显示「—」，绝不瞎凑一个数。
    breakout_pivot = (float(high.tail(lookback + 1).iloc[:-1].max())
                      if len(df) > lookback else 0.0)

    # 突破：收盘价接近或创 60 日新高
    breakout = current >= platform_high * 0.995 and current >= float(close.tail(lookback).max()) * 0.999

    vol_5 = float(vol.tail(5).mean())
    vol_20 = float(vol.tail(20).mean())
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0
    volume_expansion = vol_ratio >= 1.5 or float(vol.iloc[-1]) >= vol_20 * 1.5

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    dea = diff.ewm(span=9, adjust=False).mean()
    macd = 2 * (diff - dea)

    # 顶背离：近 60 日内至少两个显著高点，后一个高点价格更高但 MACD 更低
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

    # ★ 本轮启动起点（2026-09-22 新增）：标签「波段启动确认」可以持续很多天，
    #   没有它就说不清「它到底是哪天启动的、到现在涨了多少」。见 _band_run_start 的说明。
    _run = _band_run_start(df)

    return {
        'current': current, 'ma20': ma20, 'ma60': ma60,
        'platform_high': platform_high, 'platform_low': platform_low,
        'breakout_pivot': breakout_pivot,
        'platform_range_pct': platform_range_pct, 'breakout': breakout,
        'vol_ratio': vol_ratio, 'volume_expansion': volume_expansion,
        'macd': float(macd.iloc[-1]), 'macd_golden': bool(diff.iloc[-1] > dea.iloc[-1] and diff.iloc[-2] <= dea.iloc[-2]),
        'top_divergence': top_divergence, 'top_divergence_gap_pct': top_divergence_gap_pct,
        'below_support': below_support,
        'position_pct': position_pct, 'lookback': lookback,
        'run_days': _run['days'], 'run_start_date': _run['date'],
        'run_start_price': _run['price'],
        # 起点价缺失（days=0）时给 0，绝不用现价顶替 —— 否则"至今 +0.0%"看着像刚启动
        'run_gain_pct': (((current / _run['price']) - 1.0) * 100.0
                         if _run['price'] > 0 else 0.0),
    }

def _band_status(metrics):
    """根据指标返回波段状态和对应颜色。

    ★ 这是**短路判断**，先命中先返回，即优先级：
      顶背离预警 > 跌破支撑 > 波段启动确认（突破+放量）> 波段进行中 > 波段未形成。
      所以标签是「**最该处理的那一条**」，不是「全部结论」：
      一只票可以同时满足「突破+放量」与「顶背离」，标签只会显示顶背离。

    ★ 而且别把「顶背离」理解成「它没在启动」：顶背离的定义本来就要求
      **后一个高点比前一个更高**（价格创新高、MACD 反而更低），
      跟「突破创新高」是同一个方向上的描述 —— 两者同时成立不矛盾，
      那正是**动量衰竭型突破**的典型形态。看到它该做的是「别追高/准备止盈」，
      而不是「它还没启动」。
    """
    if metrics['top_divergence']:
        return '顶背离预警', '#ff4b4b'
    if metrics['below_support']:
        return '跌破支撑', '#ff3333'
    if metrics['breakout'] and metrics['volume_expansion']:
        return '波段启动确认', '#00cc66'
    if metrics['current'] > metrics['ma20']:
        return '波段进行中', '#f9e2af'
    return '波段未形成', '#888'

def _band_score(m):
    """波段打分与理由。单一来源：选股排序和记忆跟踪都走这里，避免两套打分漂移。"""
    score = 0; reasons = []
    if m['breakout']:
        score += 35; reasons.append(f"突破{m['lookback']}日平台")
    elif m['current'] >= m['platform_high'] * 0.97:
        score += 15; reasons.append("接近平台突破")

    if m['volume_expansion']:
        score += 25; reasons.append(f"放量({m['vol_ratio']:.1f}倍)")
    elif m['vol_ratio'] >= 1.2:
        score += 10; reasons.append(f"量能放大({m['vol_ratio']:.1f}倍)")

    if m['current'] > m['ma20'] > m['ma60']:
        score += 15; reasons.append("均线多头排列")
    elif m['current'] > m['ma20']:
        score += 8; reasons.append("站上20日线")

    if m['macd_golden']:
        score += 15; reasons.append("MACD金叉")
    elif m['macd'] > 0:
        score += 5; reasons.append("MACD红柱")

    if m['position_pct'] <= 50:
        score += 10; reasons.append(f"低位({m['position_pct']:.0f}%)")
    elif m['position_pct'] <= 75:
        score += 5; reasons.append(f"中位({m['position_pct']:.0f}%)")

    # ★ 2026-09-22 新增「拥挤度减分」：用户要求「顺序排列要按它们的前景来排列，越靠前的越有前景」。
    #   启动确认是个**可持续多日**的状态，标签本身不含时间信息；离启动起点越远，
    #   可用的向上空间越小、回撤风险越高 —— 这正是「前景」该扣分的地方。
    #   起步线 15%：涨到 15% 以内不扣（正常突破后的第一波），之后每多涨 1% 扣 0.8 分，最多扣 30 分。
    #   ★ 这**只影响排序与展示**，不会把票踢出结果：启动确认的底分
    #     （突破 35 + 放量 25 + 均线 15 + MACD 15 ≈ 90）远高于选股门槛 20，
    #     扣满 30 分仍有 60 分以上。有单测盯着"扣分后仍在门槛之上"。
    #   ★ 用 .get 取值：历史/外部构造的 metrics（含 watcher 那份、老测试夹具）没有这个字段，
    #     下标硬取会直接 KeyError —— 展示与打分对缺字段一律降级。
    _run_gain = m.get('run_gain_pct')
    if isinstance(_run_gain, (int, float)) and _run_gain >= BAND_RUN_CROWD_FREE_PCT:
        _crowd = min(30.0, (_run_gain - BAND_RUN_CROWD_FREE_PCT) * 0.8)
        score -= _crowd
        reasons.append(f"离启动点+{_run_gain:.0f}%（扣{_crowd:.0f}分）")

    if m['top_divergence']:
        score -= 40; reasons.append("⚠️顶背离")
    if m['below_support']:
        score -= 40; reasons.append("⚠️跌破20日线")
    return score, reasons

def _band_evaluate(code, name='', price=0.0, change_pct=0.0):
    """核心波段评估：**不做分数过滤**，只要数据够就给结论。

    选股（_analyze_band）和「波段记忆」的状态刷新都复用它。
    差别只在：选股要过滤掉没有波段特征的票，记忆跟踪则必须拿到真实状态。
    返回 dict 或 None（数据不足）。"""
    df = _get_daily_history(code)
    if df is None or len(df) < 60:
        return None
    m = _calculate_band_metrics(df)
    if m is None:
        return None
    score, reasons = _band_score(m)
    status, color = _band_status(m)
    return {
        'Code': str(code), 'Name': name or str(code),
        # 没有实时行情时（记忆刷新场景）退化为最新收盘价，避免显示成 0
        'Price': float(price) if float(price or 0) > 0 else m['current'],
        'ChangePct': float(change_pct or 0.0),
        'Score': max(0, score), 'Status': status, 'StatusColor': color,
        'MA20': m['ma20'], 'MA60': m['ma60'],
        'PlatformHigh': m['platform_high'], 'PlatformLow': m['platform_low'],
        'BreakoutPivot': m['breakout_pivot'],
        'VolRatio': m['vol_ratio'], 'Position250': round(m['position_pct'], 1),
        'Reasons': ' | '.join(reasons), 'LastClose': m['current'],
        'Breakout': bool(m['breakout']), 'VolumeExpansion': bool(m['volume_expansion']),
        'TopDivergence': bool(m['top_divergence']), 'BelowSupport': bool(m['below_support']),
        # 本轮启动起点（2026-09-22）：卡片/记忆/推送候选都靠它写「已启动 N 日 · 至今 +X%」
        'RunDays': m['run_days'], 'RunStartDate': m['run_start_date'],
        'RunStartPrice': m['run_start_price'], 'RunGainPct': m['run_gain_pct'],
    }

def _analyze_band(row):
    """单只股票的波段分析（带筛选，供选股用）。异常只丢该股票。"""
    try:
        r = _band_evaluate(row.get('Code'), row.get('Name', ''),
                           row.get('Price', 0.0), row.get('ChangePct', 0.0))
        if r is None:
            return None
        # 只保留有明显波段特征的股票：启动确认、进行中、或出现结束预警
        if r['Score'] < 20 and not r['Breakout'] and not r['TopDivergence'] and not r['BelowSupport']:
            return None
        return r
    except Exception as e:
        _log(f"_analyze_band/{row.get('Code', '?')}", e)
        return None

def screen_band_stocks(max_results=20, max_deep_scan=600, custom_codes=None, price_cap=0.0):
    """波段选股主函数：全市场扫描或基于自定义股票列表，筛选突破平台+放量的波段启动股，并预警结束信号。

    price_cap：单股价格上限（元）。>0 时把买不起的票在**初筛阶段**就剔掉 ——
      不光为了结果干净，更是省时间：深度分析要逐只拉 300 根日线，
      过滤放在前面，那部分请求就完全不用发。
    """
    progress = st.progress(0, text="正在获取股票列表...")
    # 原始结果（list[dict]）留给「波段记忆」做自动入册；DataFrame 只用于展示
    st.session_state.band_last_raw = []

    candidates = []; all_stocks = []
    capped = 0                      # 被价格上限过滤掉的数量（必须写进漏斗，否则像 bug）
    if custom_codes:
        # 自定义模式：仅分析用户指定的股票
        seen = set()
        for code_ in custom_codes:
            code_ = str(code_).strip()
            if not code_ or len(code_) != 6 or code_.startswith(EXCLUDE_PREFIXES) or code_ in seen:
                continue
            seen.add(code_)
            name = get_stock_name(code_)
            try:
                # 复用统一请求层：带 UA / 超时 (5,10) / 重试；前缀交给 _quote_prefix，不再手写判断
                ok, text, err = _http_get_text(f"https://qt.gtimg.cn/q={_get_code(code_)}",
                                               encoding='gbk', timeout=(5, 10), retries=2)
                parts = text.split('~') if ok else []
                if len(parts) > 45:
                    price = _safe_float(parts[3]); change_pct = _safe_float(parts[32])
                    if price > 0:
                        if price_cap and price > price_cap:
                            capped += 1
                        else:
                            candidates.append({'Code': code_, 'Name': name, 'Price': price, 'ChangePct': change_pct,
                                               'TotalMv': 0.0, 'MainFlow': 0.0})
                else:
                    _log("screen_band_stocks/quote", f"{code_} 行情不可用（{err or '返回字段不足'}）")
            except Exception as e:
                _log("screen_band_stocks/quote", e)
        progress.progress(12, text=f"已加载自定义列表 {len(candidates)} 只...")
        all_stocks = custom_codes or []
    else:
        # 全市场扫描
        all_stocks = []
        for pn in range(1, 41):
            page = fetch_market_page(pn)
            if not page:
                break
            all_stocks.extend(page)
            progress.progress(min(int(12 * pn // 40), 12), text=f"已拉取 {len(all_stocks)} 只（第 {pn} 页）...")
        if not all_stocks:
            progress.empty()
            st.session_state.scan_stats = "❌ 全市场接口暂不可用（非交易时间/网络限制）"
            st.error("获取全市场数据失败。可切换到上方「仅手动自选」或「指定板块」模式重试，交易时段全市场接口通常更稳定。")
            return pd.DataFrame()
        for s in all_stocks:
            code_ = str(s.get("f12") or "")
            name = str(s.get("f14") or "")
            if not code_ or code_.startswith(EXCLUDE_PREFIXES) or _is_st_or_risk(name):
                continue
            price = _safe_float(s.get("f2"))
            total_mv = _safe_float(s.get("f20")) / 1e8
            if price <= 0 or total_mv <= 10:
                continue
            if price_cap and price > price_cap:
                capped += 1
                continue
            change_pct = _safe_float(s.get("f3"))
            if abs(change_pct) >= 9.5:
                continue
            candidates.append({'Code': code_, 'Name': name, 'Price': price, 'ChangePct': change_pct,
                               'TotalMv': total_mv, 'MainFlow': _safe_float(s.get("f62")) / 1e8})

    # 价格上限的过滤结果必须写进漏斗 —— 否则用户看到"明明有票却扫不到"会以为是 bug
    _cap_txt = (f"（价格上限 ≤{price_cap:.2f} 元过滤掉 {capped} 只）"
                if (price_cap and capped) else "")
    total_cand = len(candidates)
    if not candidates:
        progress.empty()
        if capped:
            # ★ 全被价格上限吃掉时必须说清楚，绝不能显示成"未获取到候选股票"
            st.session_state.scan_stats = f"价格上限 ≤{price_cap:.2f} 元过滤掉了全部 {capped} 只候选"
            st.warning(f"价格上限（≤ {price_cap:.2f} 元）把 {capped} 只候选全过滤掉了 —— "
                       "把上限调高一些再扫。")
        else:
            st.session_state.scan_stats = "未获取到候选股票，请检查输入或稍后重试"
            st.warning("未获取到候选股票，请检查输入或稍后重试。")
        return pd.DataFrame()

    progress.progress(15, text=f"初筛后候选 {total_cand} 只，并发深度分析中...")

    if total_cand <= max_deep_scan:
        to_scan = candidates
    else:
        step = total_cand / max_deep_scan
        picks = sorted(set(int(i * step) for i in range(max_deep_scan)))
        to_scan = [candidates[i] for i in picks]
        hot_part = candidates[:100]
        seen = set(x['Code'] for x in to_scan)
        to_scan.extend([c for c in hot_part if c['Code'] not in seen])

    scored = []
    no_data = 0
    done = 0
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(_analyze_band, row): row for row in to_scan}
        for future in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == len(to_scan):
                progress.progress(15 + int(80 * done / len(to_scan)),
                                  text=f"深度分析 {done}/{len(to_scan)}（有效 {len(scored)} 只）")
            try:
                r = future.result()
            except Exception as e:
                _log("screen_band_stocks/future", e)
                no_data += 1
                continue
            if r is None:
                no_data += 1
            else:
                scored.append(r)

    progress.progress(100)
    progress.empty()

    if not scored:
        st.session_state.scan_stats = (f"拉取 {len(all_stocks)} 只 → 初筛 {total_cand} 只{_cap_txt} → "
                                       f"深度分析 {len(to_scan)} 只（失败 {no_data} 只）→ 无有效结果")
        return pd.DataFrame()

    # ★★ 2026-09-21 修 bug（用户反馈「我点了这个扫描波段启动股，但是没有选出来任何一个」）：
    #   旧实现把所有结果按「结束预警优先」排序后，一句 `.head(max_results)` 截断 ——
    #   全市场里「跌破支撑 / 顶背离」动辄几百只，20 个展示位会被结束信号**全部吃满**，
    #   「波段启动确认」被排挤到 df 之外 → 主区永远是「刚启动（0 只）」。
    #   可这一页存在的唯一意义就是回答「今天有哪些票刚启动」。
    #   所以改成：**信号不设名额，噪音才设名额** ——
    #     启动确认：全部保留（按分数降序），只给一个防极端行情的大上限；
    #     其余状态：按「结束预警优先」排序后截断到 max_results。
    # ★ 同时按用户 2026-09-21 的要求，**结束信号（顶背离 / 跌破支撑）不再出现在选股页**
    #   （原话「把这些所谓的顶背离的都删了，我不在乎他们是不是顶背离，因为我都没有选过他们」）。
    #   它只对「🧠 波段记忆」里在跟踪的票有意义 —— 那里有红框与微信推送。
    # ★ 这里只是**不展示**，不是丢弃：`band_last_raw`（记忆层自动入册用）仍是全集，
    #   scan_stats 与页面上都会写明「N 只结束信号已移出本页」，不做静默隐藏。
    status_order = {'顶背离预警': 0, '跌破支撑': 1, '波段启动确认': 2, '波段进行中': 3, '波段未形成': 4}
    entries = sorted([r for r in scored if r['Status'] in BAND_ENTRY_STATUSES],
                     key=lambda x: -x['Score'])
    others = sorted([r for r in scored if r['Status'] not in BAND_ENTRY_STATUSES],
                    key=lambda x: (status_order.get(x['Status'], 5), -x['Score']))
    alerts = [r for r in others if r['Status'] in BAND_ALERT_STATUSES]
    rest = [r for r in others if r['Status'] not in BAND_ALERT_STATUSES]
    _ENTRY_CAP = 100          # 启动确认不设「名额」，但极端行情下别渲染上千张卡片
    _cap_note = f"，本页展示前 {_ENTRY_CAP}" if len(entries) > _ENTRY_CAP else ""
    scored_sorted = entries + others          # 全集：记忆层自动入册用，不受展示截断影响
    st.session_state.scan_alerts_hidden = len(alerts)     # 选股页据此写「已移出本页 N 只」
    st.session_state.scan_stats = (f"拉取 {len(all_stocks)} 只 → 初筛 {total_cand} 只{_cap_txt} → "
                                   f"深度分析 {len(to_scan)} 只（K线失败 {no_data} 只）→ 有效 {len(scored)} 只"
                                   f"（启动确认 {len(entries)} 只{_cap_note}；"
                                   f"结束信号 {len(alerts)} 只已移出本页）")
    df = pd.DataFrame(entries[:_ENTRY_CAP] + rest[:max_results]).reset_index(drop=True)
    df.index = df.index + 1
    st.session_state.band_last_raw = scored_sorted
    return df

# ============ 波段记忆（Band Memory）============
# 解决的问题：波段扫描结果原本只活在 st.session_state 里，刷新即丢，
# 而且 Streamlit Cloud 容器重启后文件也会清空 —— 「过几天就不知道是哪个了」。
# 方案：扫描时自动把值得跟踪的股票记入 band_memory.json，记录状态变化轨迹，
#       并同步到 GitHub 仓库，让云端巡检脚本（watcher.py）能在你关掉页面时也推送微信。
#
# ⚠️ 下列状态常量必须与 watcher.py 中的同名常量保持一致。
BAND_MEMORY_VERSION = 1
BAND_ALERT_STATUSES = ('顶背离预警', '跌破支撑')        # 波段结束预警：需要止盈/止损
BAND_ENTRY_STATUSES = ('波段启动确认',)                 # 波段启动：值得关注/参与
# ⚠️ 名字里的 AUTO_MEMO 很容易读成「自动记入的条件」—— **它不是**。
#    它的真实含义是「这些状态的变化值得更新已有条目 / 推送提醒」，而且**只对已入册的股票生效**；
#    新建条目只认 BAND_ENTRY_STATUSES（波段启动确认）。
#    2026-09-19 之前它被直接当成入册条件，于是全市场扫描时几百只「跌破支撑」
#    被灌进记忆（实测 460 只/458 只是预警）。改回之前先读 band_memory_record 的说明。
BAND_AUTO_MEMO_STATUSES = BAND_ENTRY_STATUSES + BAND_ALERT_STATUSES
# 状态层级：**危险 / 恶化程度**，只用来判断「是变好了还是变差了、要不要打扰用户」。
# ⚠️ 数值高 ≠ 信号好：顶背离预警=4 排最高，意思是「最该处理」，不是「最值得买」；
#    启动确认=2 比它低，纯粹因为「启动」不是需要你立刻动手的事。
BAND_STATUS_LEVEL = {'波段未形成': 0, '波段进行中': 1, '波段启动确认': 2,
                     '跌破支撑': 3, '顶背离预警': 4}
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
#   ⚠️ 必须与 watcher.py 中的同名常量保持一致；不一致就会出现
#      「网页标了顶背离、微信不推」这种最难查的不对称。
BAND_DIVERGENCE_MACD_MIN_PCT = 30.0
BAND_MEMORY_HISTORY_MAX = 40    # 每只股票最多保留的轨迹条数，防止文件无限膨胀

# ============ 策略复盘规则（2026-09-19 新增）============
# 目的：让「选了 → 跟踪 → 结案 → 归因」变成可统计的闭环，而不是凭印象复盘。
#
# ★ 三条不可动摇的原则（改这里之前务必读完）：
#  1. **判定规则必须冻结**：一本账只有一套规则，规则一改就必须升 REVIEW_RULE_VERSION，
#     且老批次永远按它入册时的版本判定。否则等于用今天的尺子去量昨天的成绩，历史不可比。
#  2. **绝不用 LLM 判成败**：成败必须由下面这些确定性阈值算出来，可复现、可审计。
#     让模型「读一读这笔为什么失败」得到的是叙事，不是可验证的结论，而且无法证伪。
#  3. **结案判定只用日线回溯，不看巡检频率**：同一笔在任何时候、任何机器上重算，
#     结果必须完全一致。所以结案点由日线序列里的第一个触发日决定，与是否开着网页无关。
REVIEW_RULE_VERSION = "r1"       # 判定规则版本号；改阈值/改结案条件必须同步升版
REVIEW_BENCH_SYMBOL = "sh000300"  # 基准：沪深300（统一走数据层的腾讯源，已验证可用）
REVIEW_TP_PCT = 15.0              # 止盈结案线：浮盈达到 +15% 即结案
REVIEW_MAX_HOLD_DAYS = 40         # 时间结案：入场后 40 个交易日仍无触发则按当日收盘结案
REVIEW_MFE_GOOD = 8.0             # 「给过机会」判据：最大浮盈曾 >= 8%
REVIEW_MIN_SAMPLE = 20            # 分桶样本量下限；低于此只显示「样本不足」，不许下结论
CLOSE_REASON_LABEL = {
    'take_profit': '止盈结案', 'stop': '跌破20日线结案',
    'timeout': '时间结案', 'pending': '跟踪中',
}
# 四分类：只有 lose_logic（入场逻辑本身错）才是「该改选股逻辑」的信号；
# lose_exec 是卖点问题，改选股逻辑只会把对的信号改坏。
VERDICT_LABEL = {
    'win': '成功（绝对盈利且跑赢基准）',
    'lose_rel': '跑输（赚了但没跑赢基准，属搭便车）',
    'lose_exec': '逻辑对、执行错（曾有大浮盈但没守住）',
    'lose_logic': '入场逻辑错（从未给过机会）',
}

GITHUB_REPO = "lipeixinOVO/stock-t-terminal"
# 提交到仓库的是 band_watch.json —— 它是本地完整记忆的**完整镜像**（2026-09-20 起不再裁剪字段）；
# 本地那份 band_memory.json 本身仍旧不提交（避免同一份数据两条提交链路互相踩）。
# ⚠️ 本仓库是 public：想保密就配 BAND_KEY（提交上去的是密文），**不要靠删字段**。
GITHUB_WATCH_PATH = "band_watch.json"
# ★ 18:00 日报正文在仓库里的路径（2026-09-22 新增）。
#   正文含股票代码与名称 ⇒ 进 public 仓库前必须由 band_encrypt_obj 加密；
#   云端（daily_digest.py）没配 BAND_KEY 时会**拒绝落盘**，所以这里读不到就是"没配密钥"，
#   而不是"日报坏了" —— 展示层要如实区分这两种情况。
GITHUB_DIGEST_PATH = "digest_last.json"
DIGEST_LAST_FILE = os.path.join(BASE_DIR, "digest_last.json")
DIGEST_LAST_TTL = 600      # 自动去云端拉日报的最小间隔（秒）


def _band_memory_empty():
    return {"version": BAND_MEMORY_VERSION, "updated_at": now_cn_str(), "stocks": {}}

def load_band_memory():
    """读记忆文件；结构异常时重置而不是抛异常。"""
    mem = _load_json(BAND_MEMORY_FILE, _band_memory_empty())
    if not isinstance(mem, dict) or not isinstance(mem.get("stocks"), dict):
        _log("load_band_memory", ValueError("band_memory.json 结构异常，已重置"))
        return _band_memory_empty()
    mem.setdefault("version", BAND_MEMORY_VERSION)
    mem.setdefault("stocks", {})
    return mem

def save_band_memory(mem):
    mem["updated_at"] = now_cn_str()
    mem["version"] = BAND_MEMORY_VERSION
    _save_json(BAND_MEMORY_FILE, mem)

def _band_memory_trim(node):
    """轨迹裁剪：只留最近的若干条，长期使用不会把文件撑大。"""
    h = node.get("history")
    if isinstance(h, list) and len(h) > BAND_MEMORY_HISTORY_MAX:
        node["history"] = h[-BAND_MEMORY_HISTORY_MAX:]

def _band_memory_apply(node, r, event):
    """把一次评估结果写进记忆节点。状态变化时追加轨迹。返回是否发生状态变化。"""
    new_status = r.get('Status')
    if not new_status:
        return False
    node['name'] = r.get('Name') or node.get('name') or node.get('code')
    node['price'] = float(r.get('Price') or node.get('price') or 0.0)
    node['score'] = r.get('Score', node.get('score', 0))
    node['ma20'] = r.get('MA20', node.get('ma20', 0.0))
    node['reasons'] = r.get('Reasons', node.get('reasons', ''))
    node['last_check'] = now_cn_str()
    # ★ 动态目标价的来源：**当前**平台高点（`_band_evaluate` 每天重算 ⇒ 平台抬高时目标自动上移）。
    #   必须写在下面「状态没变就直接 return」的**之前** —— 否则状态长期不变的票，
    #   目标价会永远停在入选那一天，"动态"就是句空话。踩过一次这种顺序坑（日报条数统计）。
    node['platform_high'] = float(r.get('PlatformHigh') or node.get('platform_high') or 0.0)
    # ★ 突破位同理（也必须写在"状态没变就直接 return"之前）：它是**滚动窗口**算出来的，
    #   窗口前移它就会变；停在上一次刷新的值会让"回踩位"越来越失真。
    node['breakout_pivot'] = float(r.get('BreakoutPivot')
                                   or node.get('breakout_pivot') or 0.0)
    # ★ 本轮启动起点（2026-09-22）：与 breakout_pivot 同一个理由 —— 它是**滚动回溯**出来的，
    #   停在上一次刷新的值会让「已启动 N 日」越算越错（尤其这一轮中途断过几天、
    #   或者早就跌破 20 日线时，旧值会让界面继续显示一个不存在的"启动中"）。
    #   所以也必须写在「状态没变就直接 return」**之前**。
    #   ★ 用 `is not None` 判存在、而不是 `or`：RunDays=0 是**合法值**
    #     （意思是"最后一根 K 线已经不在启动区"），用 `or` 会退回过期旧值，
    #     于是已经结束的波段会一直显示「已启动 47 个交易日」。
    _rdays = r.get('RunDays')
    if _rdays is not None:
        node['run_days'] = int(_rdays)
        node['run_start_date'] = str(r.get('RunStartDate') or '')
        node['run_start_price'] = float(r.get('RunStartPrice') or 0.0)
        node['run_gain_pct'] = float(r.get('RunGainPct') or 0.0)
    old_status = node.get('status')
    if new_status == old_status:
        return False
    node['status'] = new_status
    node['status_ts'] = now_cn_str()
    node.setdefault('history', []).append({
        "ts": node['status_ts'], "status": new_status,
        "price": node.get('price', 0.0), "score": node.get('score', 0),
        "ma20": node.get('ma20', 0.0), "reasons": node.get('reasons', ''),
        "event": event,
    })
    _band_memory_trim(node)
    return True

def _band_alert_decision(old_status, new_status):
    """状态变化要不要打扰用户？返回 'end'（波段结束预警）/ 'start'（波段启动）/ None。

    只对这两类变化高亮/推送，其余仅记录，否则天天刷屏。
    ⚠️ 必须与 watcher.py 的同名函数保持同一规则（有跨文件一致性测试守着）。"""
    if new_status in BAND_ALERT_STATUSES and old_status not in BAND_ALERT_STATUSES:
        return "end"
    if old_status == '波段未形成' and new_status == '波段启动确认':
        return "start"
    return None


def _band_memory_new_node(r, source, batch_id=None, bench_above=None):
    """按**唯一一份字段表**新建一条记忆条目，返回 node。

    ★ 抽出来的唯一理由：自动入册与「手动加入」必须生成**完全同构**的条目。
      两处各抄一份字面量的话，早晚会漂移（少一个 snapshot 字段、漏掉 entry_context），
      而这种漂移在页面上看不出来 —— 只会在几周后复盘时发现某几只票的归因是空的。
    """
    code = str(r.get('Code') or '').strip()
    return {
        "code": code, "name": r.get('Name') or code,
        "added_at": now_cn_str(), "added_price": float(r.get('Price') or 0.0),
        "added_status": r.get('Status'), "added_source": source,
        "batch_id": batch_id or "",
        "entry_context": _band_entry_context(r, bench_above),
        "snapshot": {
            "score": r.get('Score', 0), "ma20": r.get('MA20', 0.0),
            "ma60": r.get('MA60', 0.0),
            "platform_high": r.get('PlatformHigh', 0.0),
            "breakout_pivot": r.get('BreakoutPivot', 0.0),
            "vol_ratio": r.get('VolRatio', 0.0),
            "position250": r.get('Position250', 0.0),
            "reasons": r.get('Reasons', ''),
        },
        "note": "", "closed": False, "alerts": {},
        # platform_high：**含当天的 60 日最高价**（每次刷新重算，这里先落一个入册时的值）。
        #   ⚠️ 别再把它叫「目标价」：创新高的票它必然≈现价，当不了目标（见上面对话框的说明）。
        # alert_ack：「已看过该预警」的标记，值 = 当时的 status_ts。
        #   ★ 2026-09-20 起它会随**完整镜像**一起同步（以前做字段裁剪时才不同步），
        #   所以容器重启后**不会**再把同一条预警重复展开。
        "platform_high": float(r.get('PlatformHigh') or 0.0),
        # breakout_pivot：突破位（突破前的 60 日平台上沿，不含当天）。
        #   取不到就是 0 → 展示层显示「—」；旧节点没有这个字段，刷新一次会补上。
        "breakout_pivot": float(r.get('BreakoutPivot') or 0.0),
        # run_*：入册那一刻，这一轮「启动」是从哪天开始的（2026-09-22）。
        #   卡片/记忆清单/推送候选据此写「已启动 N 个交易日 · 起点 X · 至今 +Y%」——
        #   因为「波段启动确认」是个可以持续很多天的状态，标签本身不带时间，
        #   用户会误以为"它今天才启动"（原话：「已经涨了很多很多了，为什么还说它是启动」）。
        #   取不到就是 0/''，展示层降级成「—」，绝不编。
        "run_start_date": str(r.get('RunStartDate') or ''),
        "run_start_price": float(r.get('RunStartPrice') or 0.0),
        "run_days": int(r.get('RunDays') or 0),
        "run_gain_pct": float(r.get('RunGainPct') or 0.0),
        "alert_ack": "",
        "history": [],
    }


def band_memory_record(mem, rows, source, batch_id=None, bench_above=None):
    """把本次扫描结果中「值得跟踪」的股票写入记忆。返回新增代码列表。

    ★ 入册规则（2026-09-19 修正）：
      - **新建**条目只认「波段启动确认」；
      - 「顶背离预警 / 跌破支撑」只用于**更新已在记忆里**的股票，绝不新建条目。

    为什么必须这样分：结束预警的意思是「你在盯的那只波段要结束了」，前提是它曾经启动过。
    全市场扫描时处于「跌破 20 日线」的股票动辄几百只（实测 600 只深度分析里 458 只），
    旧规则把它们全部入册 —— 记忆瞬间堆到 460 只、其中 458 只是预警，完全没法用。

    batch_id / bench_above 供「策略复盘」使用：批次用于把同一期选的票归到一组，
    bench_above 记录入场时的大盘环境（归因要用）。两者都不参与入册判定。
    """
    added = []
    for r in (rows or []):
        code = str(r.get('Code') or '').strip()
        status = r.get('Status')
        if not code:
            continue
        node = mem['stocks'].get(code)
        if node is None:
            if status not in BAND_ENTRY_STATUSES:
                continue                  # ← 关键：预警状态不得新建条目
            node = _band_memory_new_node(r, source, batch_id, bench_above)
            mem['stocks'][code] = node
            added.append(code)
            _band_memory_apply(node, r, event=f"入选记忆（{source}）")
        else:
            if status not in BAND_AUTO_MEMO_STATUSES:
                continue
            # 已记住的：后续扫描发现状态变化（例如 启动确认 → 跌破支撑）也要记下来
            _band_memory_apply(node, r, event=f"扫描刷新（{source}）")
    return added

def band_memory_add_manual(mem, r, source="手动加入"):
    """手动把一只票放进「波段记忆」全程监控。返回 (是否新建条目, 代码)。

    ★ 与自动入册**只差一条：不看状态**。
      自动入册只认「波段启动确认」，是因为全市场扫描会把几百只预警票一次灌进记忆；
      但这里是用户**自己按下的按钮** —— 他已经决定要盯这只（很可能已经买了），
      规则不该替他否决。
    ★ 已在册时不覆盖 `added_price`（`_band_memory_apply` 不碰该字段）：
      入场价必须留在入选那一天，否则「相对入选价涨了多少」这个判断就废了。
    """
    code = str(r.get('Code') or '').strip()
    if not code:
        return False, ""
    stocks = mem.setdefault('stocks', {})
    node = stocks.get(code)
    if node is None:
        node = _band_memory_new_node(r, source)
        stocks[code] = node
        _band_memory_apply(node, r, event=f"手动加入记忆（{source}）")
        return True, code
    _band_memory_apply(node, r, event=f"手动刷新（{source}）")
    return False, code

def band_memory_purge(mem, mode="never_started"):
    """清理记忆，返回 (mem, 删除数量)。

    mode="never_started"：只删「入选时不是波段启动确认」的条目 —— 也就是旧规则下
        由「跌破支撑 / 顶背离预警」误建的那些。**带备注的条目一律保留**，
        因为写了备注说明你是主动关注它的，不能当噪音清掉。
    mode="alert"：只删「入选时就是结束预警（顶背离 / 跌破支撑）」的条目，
        **连带备注一起删**。这是用户 2026-09-21 明确点名的口径 ——
        原话「把这些所谓的顶背离的都删了，我不在乎他们是不是顶背离，因为我都没有选过他们」。
        与 never_started 的差别：那些备注多半不是在说这只票（是旧规则灌进来时顺手带的），
        所以这里不拿备注当免删金牌；但**启动确认入册的、以及手动记入的（added_status 为空）
        一律不动**，避免误伤用户真正手动加进来的票。
    mode="all"：全清（调用方须自行做二次确认）。
    """
    stocks = mem.get('stocks')
    if not isinstance(stocks, dict):
        return mem, 0
    removed = 0
    for code in list(stocks.keys()):
        node = stocks.get(code)
        if not isinstance(node, dict):
            stocks.pop(code, None)
            removed += 1
            continue
        if mode == "all":
            stocks.pop(code, None)
            removed += 1
            continue
        if mode == "alert":
            # 只认「入选时就是预警状态」这一条 —— added_status 为空的（手动记入）不碰
            if node.get('added_status') in BAND_ALERT_STATUSES:
                stocks.pop(code, None)
                removed += 1
            continue
        if node.get('added_status') in BAND_ENTRY_STATUSES:
            continue                       # 正常入册（启动确认）的保留
        if str(node.get('note') or '').strip():
            continue                       # 你写过备注的保留
        stocks.pop(code, None)
        removed += 1
    return mem, removed

def band_memory_purge_stats(mem):
    """清理前的预估：返回 (将被删除的条数, 带备注会被保留的条数)。"""
    junk = noted = 0
    for node in (mem.get('stocks') or {}).values():
        if not isinstance(node, dict):
            junk += 1
            continue
        if node.get('added_status') in BAND_ENTRY_STATUSES:
            continue
        if str(node.get('note') or '').strip():
            noted += 1
        else:
            junk += 1
    return junk, noted

def band_memory_alert_stats(mem):
    """统计「入选时就是结束预警（顶背离 / 跌破支撑）」的条目数。

    单独一个函数而不是往 `band_memory_purge_stats` 里塞第三个返回值 ——
    那个函数的 (junk, noted) 二元组已被多处解包，改签名会连带改一片调用点。
    """
    n = 0
    for node in (mem.get('stocks') or {}).values():
        if isinstance(node, dict) and node.get('added_status') in BAND_ALERT_STATUSES:
            n += 1
    return n

# ============ 策略复盘：批次 / 结案 / 归因（P0，只统计不改参数）============
# 读这一节之前先看上面 REVIEW_* 常量的三条原则。
#
# 这一阶段的产出是「可归因的样本库 + 诚实的统计看板」，**不含任何自动调参**。
# 为什么不直接上自动优化：每周 10 只，一年约 500 笔，但真正走到结案的只有一部分，
# 而要判断「量比门槛 1.2 还是 1.5 更好」同一分桶至少需要几十笔样本 —— 头两三个月
# 任何参数调整都只是噪音拟合。先攒样本，攒够了再谈优化。

def _batches_empty():
    return {"version": 1, "updated_at": now_cn_str(), "batches": {}}

def load_band_batches():
    """读批次档案；结构异常时重置而不是抛异常（与 load_band_memory 同策略）。"""
    b = _load_json(BAND_BATCHES_FILE, _batches_empty())
    if not isinstance(b, dict) or not isinstance(b.get("batches"), dict):
        _log("load_band_batches", ValueError("band_batches.json 结构异常，已重置"))
        return _batches_empty()
    b.setdefault("version", 1)
    b.setdefault("batches", {})
    return b

def save_band_batches(batches):
    batches["updated_at"] = now_cn_str()
    batches["version"] = 1
    _save_json(BAND_BATCHES_FILE, batches)

def band_batch_create(batches, codes, source, note='', rule_version=REVIEW_RULE_VERSION):
    """把「这一次选出来的这批票」记成一个批次。返回 batch_id。

    批次的意义：复盘时必须能回答「这一批（同一时刻、同一套规则下选出来的）成绩如何」。
    逐只散着记，事后就分不清哪些是同一期选的，也就无法归因。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    today = now_cn().strftime('%Y-%m-%d')
    n = 1
    while f"{today}-{n:02d}" in (batches.get('batches') or {}):
        n += 1
    batch_id = f"{today}-{n:02d}"
    batches.setdefault('batches', {})[batch_id] = {
        "batch_id": batch_id, "created_at": now_cn_str(), "source": source or "",
        "note": note or "", "rule_version": rule_version,
        "codes": codes, "count": len(codes),
    }
    return batch_id

def _get_index_history(full_symbol, limit=300):
    """指数日线。**必须传完整符号**（如 sh000300）——指数不能走 _quote_prefix，
    因为 000300 会被判成 sz000300（那是个不存在的股票代码）。仍复用统一取数层。

    ★ 加东财作为第二源：腾讯 qfq 接口一抖，当天所有复盘的基准都会取不到 →
      超额收益全变 None（复盘直接废掉）。这里**不挂熔断器** —— 它按自然日只取一次
      （见 `_bench_history`），不值得为它多引一份共享状态。
    """
    for fn in (_fetch_kline_qq, _fetch_kline_em, _fetch_kline_sina):
        try:
            df, _src, _errs = fn(full_symbol, limit=limit)
            if df is not None and len(df) >= 60:
                return df
        except Exception as e:
            _log(f"_get_index_history/{full_symbol}", e)
            continue
    return None

def _clear_bench_cache():
    """清掉基准缓存。测试里的 st 替身把 cache_data 做成了透传装饰器（没有 .clear），
    所以这里要容忍 AttributeError。"""
    try:
        _bench_history_fetch.clear()
    except AttributeError as e:
        _log("_clear_bench_cache", e)


def _bench_history(force=False):
    """基准（沪深300）日线。**跨重跑**按自然日缓存：同一天里多处调用 / 反复交互都只取一次。
    取数失败**不写缓存**（抛异常绕过缓存），下一次调用会重试。

    ★ 这里踩过一个大坑：原来用模块级 dict `_BENCH_CACHE` 记缓存 —— 但 Streamlit 每次交互
      都会**重新 exec 整个脚本**，模块级变量跟着被重置，那份"按自然日缓存"其实只在
      一个重跑周期内有效。结果就是用户**每点一次按钮都真去拉一次沪深300日线**，
      实测 0.76s/次，占掉一次重跑的四分之一（2026-09-20 用 cProfile 量出来的）。
      **凡是"想跨越重跑活下来"的状态，都必须交给 st.cache_data / st.session_state。**
    """
    try:
        if force:
            _clear_bench_cache()
        return _bench_history_fetch(now_cn().strftime('%Y-%m-%d'))
    except Exception as e:
        _log("_bench_history", e)
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def _bench_history_fetch(day_key):
    """真正去取数的那一层。`day_key` 只为让缓存按自然日失效，不参与取数。

    ★ 取名**不要加下划线前缀**：Streamlit 会把下划线开头的参数**排除出缓存键**，
      那样跨天也不会重新取，一直拿昨天的基准。
    ★ 取数失败必须 **raise**：st.cache_data 不缓存抛异常的调用，但**会**缓存 None ——
      直接返回 None 会让一次网络抖动污染当天所有复盘。
    """
    df = _get_index_history(REVIEW_BENCH_SYMBOL)
    if df is None or len(df) < 60:
        raise RuntimeError("基准（沪深300）日线取数失败")
    return df

def _bench_above_ma20(bench_df):
    """入场时的大盘环境：沪深300 收盘是否在 20 日线上。取不到返回 None（不猜）。"""
    try:
        if bench_df is None or len(bench_df) < 25:
            return None
        close = bench_df['Close'].astype(float)
        ma20 = float(close.rolling(20).mean().iloc[-1])
        cur = float(close.iloc[-1])
        if ma20 != ma20 or ma20 <= 0 or cur <= 0:
            return None
        return bool(cur > ma20)
    except Exception as e:
        _log("_bench_above_ma20", e)
        return None

def _band_entry_context(r, bench_above=None):
    """入场那一刻的可量化条件。**这是整个归因的地基**：
    没有入场快照，事后只能说「它跌了」；有了它才能问「在什么条件下这套逻辑失效」。"""
    try:
        px = float(r.get('Price') or 0.0)
        ma20 = float(r.get('MA20') or 0.0)
        ma60 = float(r.get('MA60') or 0.0)
    except Exception as e:
        _log("_band_entry_context", e)
        px = ma20 = ma60 = 0.0
    return {
        "entry_score": float(r.get('Score') or 0.0),
        "position250": float(r.get('Position250') or 0.0),
        "vol_ratio": float(r.get('VolRatio') or 0.0),
        "breakout": bool(r.get('Breakout')),
        "above_ma60": (px > ma60) if (px > 0 and ma60 > 0) else None,
        "ma_bull": (px > ma20 > ma60) if (px > 0 and ma20 > 0 and ma60 > 0) else None,
        "ma20": ma20, "ma60": ma60,
        "bench_above_ma20": bench_above,
        "rule_version": REVIEW_RULE_VERSION,
    }

def band_outcome_compute(entry_date, entry_price, df, bench_df=None):
    """用日线回溯确定结案点，并算出复盘指标。**纯函数，不碰网络，可复现**。

    结案条件（按日线逐日检查，取第一个触发日）：
      ① 浮盈 >= REVIEW_TP_PCT        → 'take_profit'
      ② 收盘跌破 MA20                 → 'stop'
      ③ 持有满 REVIEW_MAX_HOLD_DAYS   → 'timeout'
    入场日算第 0 天、不参与判定（当天买当天卖不算一笔波段）。

    ★ 为什么不按巡检快照判：快照取决于你有没有开着网页、网络通不通。
      用日线回溯则任何时间、任何机器重算都得到同一个结果 —— 这是统计可信的前提。

    返回 dict；数据不足返回 None。
    """
    try:
        if df is None or len(df) < 25 or not entry_date:
            return None
        d0 = str(entry_date)[:10]
        dates = df['Date'].astype(str).str.slice(0, 10).tolist()
        closes = [float(x) for x in df['Close'].tolist()]
        highs = [float(x) for x in df['High'].tolist()]
        lows = [float(x) for x in df['Low'].tolist()]
        ma20_full = [float(x) for x in df['Close'].astype(float).rolling(20).mean().tolist()]
        n = len(dates)

        # 入场日 = 最后一根「日期 <= 入场日」的K线。周末/节假日入场时它会落到前一个交易日，
        # 而入选价本来就是那个收盘价（手动记入在非交易日拿到的就是最近收盘）。
        i0 = None
        for i in range(n):
            if dates[i] <= d0:
                i0 = i
        if i0 is None or i0 >= n - 1:
            return None                    # 入场之后还没有新的交易日

        px = float(entry_price or 0.0)
        if px <= 0:
            px = closes[i0]
        if px <= 0:
            return None

        # ★ MFE / MAE 口径（别乱改，页面指标与归因都依赖它）：
        #   MFE = 最大浮盈，初值 0 → 全程没涨过就是 0%（不用负数），用于判「给没给过机会」。
        #   MAE = 最大浮亏，初值 0 → 全程没跌破入场价就是 0%，有下探才是负数。
        #   两者都只统计入场日之后的K线（入场日算第 0 天，不参与）。
        #   合起来才能把「入场逻辑错」和「卖点执行错」分开：
        #   MFE 够大却亏 → 逻辑没错，是卖的问题；MFE 一直贴地 → 才是选股逻辑的问题。
        mfe = 0.0
        mae = 0.0
        close_reason = None
        close_i = None
        for j in range(i0 + 1, n):
            if highs[j] / px - 1 > mfe:
                mfe = highs[j] / px - 1
            if lows[j] / px - 1 < mae:
                mae = lows[j] / px - 1
            if closes[j] / px - 1 >= REVIEW_TP_PCT / 100.0:
                close_reason, close_i = 'take_profit', j
                break
            m = ma20_full[j]
            if m == m and m > 0 and closes[j] < m:
                close_reason, close_i = 'stop', j
                break
            if (j - i0) >= REVIEW_MAX_HOLD_DAYS:
                close_reason, close_i = 'timeout', j
                break

        closed = close_i is not None
        if not closed:
            close_i = n - 1                # 跟踪中：先按最新一根给浮动指标
        close_price = closes[close_i]
        ret = close_price / px - 1.0
        days_held = close_i - i0

        # 基准：与个股使用同一对日期（入场日、结案日各取「最后一根 <= 该日」的基准K线）
        bench_ret = None
        try:
            if bench_df is not None and len(bench_df) >= 20:
                bd = bench_df['Date'].astype(str).str.slice(0, 10).tolist()
                bc = [float(x) for x in bench_df['Close'].tolist()]
                bi0 = bi1 = None
                for i in range(len(bd)):
                    if bd[i] <= dates[i0]:
                        bi0 = i
                    if bd[i] <= dates[close_i]:
                        bi1 = i
                if bi0 is not None and bi1 is not None and bi1 > bi0 and bc[bi0] > 0:
                    bench_ret = bc[bi1] / bc[bi0] - 1.0
        except Exception as e:
            _log("band_outcome_compute/bench", e)

        excess = (ret - bench_ret) if bench_ret is not None else None

        if not closed:
            verdict = 'pending'
        elif excess is not None:
            if ret > 0 and excess > 0:
                verdict = 'win'
            elif ret > 0:
                verdict = 'lose_rel'                       # 赚了但跑输 → 搭便车
            elif mfe * 100 >= REVIEW_MFE_GOOD:
                verdict = 'lose_exec'                      # 给过机会没走 → 卖点问题
            else:
                verdict = 'lose_logic'                     # 从未给过机会 → 入场逻辑问题
        else:
            # 基准拿不到时只能按绝对收益粗判，并显式标记（缺少「搭便车」这一档）
            verdict = 'win' if ret > 0 else (
                'lose_exec' if mfe * 100 >= REVIEW_MFE_GOOD else 'lose_logic')

        return {
            "closed": bool(closed),
            "close_reason": close_reason or 'pending',
            "close_at": dates[close_i] if closed else "",
            "close_price": round(close_price, 3),
            "days_held": int(days_held),
            "ret_pct": round(ret * 100, 2),
            "bench_ret_pct": round(bench_ret * 100, 2) if bench_ret is not None else None,
            "excess_pct": round(excess * 100, 2) if excess is not None else None,
            "mfe_pct": round(mfe * 100, 2),
            "mae_pct": round(mae * 100, 2),
            "verdict": verdict,
            "rule_version": REVIEW_RULE_VERSION,
            "computed_at": now_cn_str(),
        }
    except Exception as e:
        _log("band_outcome_compute", e)
        return None

def band_review_refresh(mem, max_items=80, force=False):
    """给记忆里的股票补算结案结果（并写回 node['outcome']）。返回 (mem, 更新只数)。

    只重算「跟踪中」的条目；已结案的默认跳过（结果不会再变），除非 force=True。
    上限 max_items 防止记忆里有几百只时把页面卡住。
    """
    stocks = (mem or {}).get('stocks') or {}
    if not isinstance(stocks, dict):
        return mem, 0
    bench_df = _bench_history()
    updated = 0
    for code, node in stocks.items():
        if updated >= max_items:
            break
        if not isinstance(node, dict):
            continue
        if node.get('closed'):
            continue
        prev = node.get('outcome') or {}
        if prev.get('closed') and not force:
            continue
        try:
            df = _get_daily_history(code)
            out = band_outcome_compute(node.get('added_at'), node.get('added_price'), df, bench_df)
            if out is None:
                continue
            node['outcome'] = out
            updated += 1
        except Exception as e:
            _log(f"band_review_refresh/{code}", e)
            continue
    if updated:
        mem['review_updated_at'] = now_cn_str()
    return mem, updated

def _bucket_defs():
    """归因维度：全部取自「入场时就已知」的条件，绝不用事后才知道的信息分桶。"""
    return [
        ("大盘环境", lambda c: {True: "沪深300在20日线上", False: "沪深300在20日线下"}
            .get(c.get('bench_above_ma20'))),
        ("位置分位", lambda c: (None if c.get('position250') is None else
                              ("0-50%" if c['position250'] < 50 else
                               ("50-75%" if c['position250'] < 75 else "75-100%")))),
        ("放量倍数", lambda c: (None if c.get('vol_ratio') is None else
                              ("<1.2" if c['vol_ratio'] < 1.2 else
                               ("1.2-1.5" if c['vol_ratio'] < 1.5 else ">=1.5")))),
        ("平台突破", lambda c: {True: "真突破60日平台", False: "未突破"}.get(c.get('breakout'))),
        ("入场评分", lambda c: (None if c.get('entry_score') is None else
                              ("<30" if c['entry_score'] < 30 else
                               ("30-50" if c['entry_score'] < 50 else ">=50")))),
    ]

def band_attribution_report(mem):
    """按入场条件分桶统计。只统计**已结案**的笔。返回 list[dict]。

    每行带 enough 标记：样本量 < REVIEW_MIN_SAMPLE 时页面必须显示「样本不足」，
    不允许据此下结论 —— 否则就是在噪音里挑好看的那一桶。"""
    rows = []
    closed_nodes = [n for n in ((mem or {}).get('stocks') or {}).values()
                    if isinstance(n, dict) and (n.get('outcome') or {}).get('closed')]
    for dim, fn in _bucket_defs():
        groups = {}
        for node in closed_nodes:
            ctx = node.get('entry_context') or {}
            name = fn(ctx)
            if not name:
                continue
            groups.setdefault(name, []).append(node)
        for name in sorted(groups.keys()):
            items = groups[name]
            outs = [n['outcome'] for n in items]
            rets = [o.get('ret_pct') or 0.0 for o in outs]
            wins = sum(1 for o in outs if o.get('verdict') == 'win')
            exs = [o['excess_pct'] for o in outs if o.get('excess_pct') is not None]
            rows.append({
                "dim": dim, "bucket": name, "n": len(items),
                "enough": len(items) >= REVIEW_MIN_SAMPLE,
                "win_rate": round(wins / len(items) * 100, 1) if items else 0.0,
                "avg_ret": round(sum(rets) / len(rets), 2) if rets else 0.0,
                "avg_excess": round(sum(exs) / len(exs), 2) if exs else None,
                "avg_mfe": round(sum(o.get('mfe_pct') or 0.0 for o in outs) / len(outs), 2),
                "avg_mae": round(sum(o.get('mae_pct') or 0.0 for o in outs) / len(outs), 2),
            })
    return rows

def band_review_stats(mem):
    """复盘总览。tracked 含未结案的，closed 才是可用于统计的样本。

    ★ tracked 要排除「用户已归档、但波段从未算出结案结果」的条目：
      这类条目已被用户手工移出观察（`closed=True` 且 outcome 为空），
      `band_review_refresh` 会跳过它们、永远不会补上 outcome，
      若还计进 tracked 就会让「跟踪中」永久虚高几只，看板数字从一开始就是假的。
    """
    stocks = (mem or {}).get('stocks') or {}
    tracked = [n for n in stocks.values()
               if isinstance(n, dict)
               and not (n.get('closed') and not (n.get('outcome') or {}).get('closed'))]
    closed = [n for n in tracked if (n.get('outcome') or {}).get('closed')]
    outs = [n['outcome'] for n in closed]
    verdict_counts = {}
    for o in outs:
        v = o.get('verdict') or 'unknown'
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
    exs = [o['excess_pct'] for o in outs if o.get('excess_pct') is not None]
    return {
        "tracked": len(tracked), "closed": len(closed), "open": len(tracked) - len(closed),
        "win": verdict_counts.get('win', 0),
        "win_rate": round(verdict_counts.get('win', 0) / len(outs) * 100, 1) if outs else 0.0,
        "avg_ret": round(sum(o.get('ret_pct') or 0.0 for o in outs) / len(outs), 2) if outs else 0.0,
        "avg_excess": round(sum(exs) / len(exs), 2) if exs else None,
        "avg_mfe": round(sum(o.get('mfe_pct') or 0.0 for o in outs) / len(outs), 2) if outs else 0.0,
        "avg_mae": round(sum(o.get('mae_pct') or 0.0 for o in outs) / len(outs), 2) if outs else 0.0,
        "verdict_counts": verdict_counts,
        "bench_ready": _bench_history() is not None,
        "rule_version": REVIEW_RULE_VERSION,
        "min_sample": REVIEW_MIN_SAMPLE,
    }

def band_memory_refresh(mem, codes=None):
    """重新拉取记忆内股票的当前波段状态（并发）。返回 (mem, changes)。

    changes 只包含**状态发生变化**的条目，供页面高亮提醒。"""
    targets = [c for c in (codes or list(mem['stocks'].keys()))
               if c in mem['stocks'] and not mem['stocks'][c].get('closed')]
    changes = []
    if not targets:
        return mem, changes
    def _one(code):
        try:
            node = mem['stocks'].get(code) or {}
            return code, _band_evaluate(code, node.get('name') or code)
        except Exception as e:
            _log(f"band_memory_refresh/{code}", e)
            return code, None
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_one, c) for c in targets]
        for fut in as_completed(futures):
            try:
                code, r = fut.result()
            except Exception as e:
                _log("band_memory_refresh/future", e)
                continue
            node = mem['stocks'].get(code)
            if r is None or node is None:
                if node is not None:
                    node['last_check'] = now_cn_str()
                continue
            old_status = node.get('status')
            if _band_memory_apply(node, r, event="状态刷新"):
                changes.append({
                    "code": code, "name": node.get('name'),
                    "from": old_status, "to": r.get('Status'),
                    "price": r.get('Price', 0.0), "reasons": r.get('Reasons', ''),
                    "alert": _band_alert_decision(old_status, r.get('Status')),
                    "worsened": BAND_STATUS_LEVEL.get(r.get('Status'), 0)
                                > BAND_STATUS_LEVEL.get(old_status, 0),
                })
    save_band_memory(mem)
    return mem, changes

def band_memory_stats(mem):
    stocks = (mem or {}).get('stocks', {}) or {}
    active = [n for n in stocks.values() if not n.get('closed')]
    return {
        "total": len(stocks),
        "active": len(active),
        "alert": sum(1 for n in active if n.get('status') in BAND_ALERT_STATUSES),
        "entry": sum(1 for n in active if n.get('status') in BAND_ENTRY_STATUSES),
    }

def _github_token():
    """GitHub Token 的三级读取：环境变量 → 本地 config.json → Streamlit Secrets。"""
    for env_key in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(env_key)
        if v and v.strip():
            return v.strip()
    v = (load_config() or {}).get("github_token")
    if v and str(v).strip():
        return str(v).strip()
    try:
        v = st.secrets.get("GITHUB_TOKEN", "")
        if v and str(v).strip():
            return str(v).strip()
    except Exception:
        pass
    return ""

def _github_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "stock-t-terminal"}

# ============ 波段记忆同步加密（可选，用于隐藏「关注了哪些股票」）============
# 背景：本仓库是 public。提交上去的 band_watch.json 现在是**完整记忆**（含备注/入选价），
# 所以「加密」不再只是可选项，而是**唯一的保密手段** —— 字段裁剪那套已经废掉了
# （见 _band_memory_digest 的说明：删字段挡不住想读的人，却会把功能砍掉一半）。
# 为什么不直接改成 Private：public 仓库的 Actions 完全免费、不限分钟；private 仓库只有
# 2000 分钟/月，而本项目「定时巡检 + 保活」约需 7800 分钟/月，额度烧穿后 GitHub 会
# **静默停掉**定时任务 —— 那样你反而收不到任何提醒。所以正确做法是保持 public，
# 把文件**加密**后再提交：Streamlit Secrets 与 GitHub Actions Secrets 各加一个
# 同名的 BAND_KEY（两边值必须一致）。未配置 BAND_KEY 时保持明文（与历史行为一致），
# 页面会明确提示「云端摘要未加密」。

_ENC_FIELD = "enc"
_ENC_VERSION = 1

def band_crypto_key():
    """读加密密钥：环境变量 → 本地 config.json → Streamlit Secrets。空串表示未启用。"""
    v = os.environ.get("BAND_KEY", "")
    if v and str(v).strip():
        return str(v).strip()
    try:
        v = (load_config() or {}).get("band_key")
    except Exception as e:
        _log("band_crypto_key:config", e)
        v = ""
    if v and str(v).strip():
        return str(v).strip()
    try:
        v = st.secrets.get("BAND_KEY", "")
        if v and str(v).strip():
            return str(v).strip()
    except Exception:
        pass      # 本地无 secrets.toml 属预期情况，刷日志反而是噪声
    return ""

def band_crypto_enabled():
    """是否已配置加密密钥（页面据此提示「加密 / 未加密」）。"""
    return bool(band_crypto_key())

def _fernet():
    """构造 Fernet 实例。未配密钥返回 None；配了但不可用则**抛异常**。

    刻意不在这里吞掉异常：如果配了 BAND_KEY 却因为缺依赖 / 密钥格式错而悄悄退回明文，
    就等于把「关注了哪些股票」原样公开出去，而这种泄露不会有任何提示。"""
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
    """把摘要对象包成可公开的文件结构。

    未配密钥 → 原样返回（明文，兼容历史行为）；
    配了密钥但加密失败 → 抛异常，由调用方中止提交。"""
    f = _fernet()
    if f is None:
        return obj
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return {"v": _ENC_VERSION, _ENC_FIELD: f.encrypt(raw).decode("ascii")}

def band_decrypt_obj(data):
    """还原摘要对象，返回 (ok, obj 或 None, err)。

    明文（没有 enc 字段）原样返回，兼容历史文件与未启用加密的场景；
    是密文但本端没密钥 / 解不开 → 明确报错，由调用方展示，绝不当作空数据静默处理。"""
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

def generate_band_key():
    """生成一个新的 Fernet 密钥，供用户填入两边 Secrets。"""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("ascii")

def _band_memory_digest(mem):
    """把本地完整记忆导出成「提交到仓库」的那一份 —— **整节点原样导出，不做任何字段裁剪**。

    ★ 2026-09-20 改，别再改回去：这里原来做「脱敏」—— 只挑 code/name/status/status_ts/
      last_check/closed/alerts/history 这 8 个白名单字段，把 note（手写备注）、
      added_price（入选价）、platform_high（平台高点）、snapshot、added_at/added_source
      全部丢掉，理由是"仓库是 public"。

      代价是**真的丢数据**，而且丢的都是本地没有第二份的东西：
        · 手写备注只在本地容器里 → 容器一重启就**永久消失**（云端那份里根本没这个字段）；
        · platform_high 不在导出里 → 恢复后只能显示「暂缺：还没刷新过，拿不到平台高点」；
        · added_price 不在导出里 → 启动价只能靠入册轨迹推算，界面上得标「（推算）」。
      而且白名单本身就是一类静默故障源：漏一个字段不会报错，只是悄悄少个值
      —— platform_high 就是这么漏的，靠人工比对才发现。

      现在的口径（用户明确表示**保密性不重要、只看功能**）：
        **仓库那份 = 完整记忆，两端结构完全一致。**
      想保密就交给 Fernet 加密（配了 BAND_KEY，提交上去的就是密文），**而不是靠删字段**：
      删字段挡不住真想看的人（代码清单照样是公开的），却会把功能砍掉一半。"""
    out = {"version": BAND_MEMORY_VERSION, "updated_at": now_cn_str(), "stocks": {}}
    for code, node in (mem.get('stocks') or {}).items():
        if not isinstance(node, dict):
            continue
        try:
            # 本地记忆本来就靠 _save_json(json.dump) 落盘，所以节点几乎必然已是 JSON 安全的；
            # 这一探只为「万一有怪值」时留下可定位的日志，而不是让整次同步莫名失败。
            json.dumps(node, ensure_ascii=False)
            out["stocks"][code] = dict(node)
        except Exception as e:
            _log("_band_memory_digest:node", e)
            out["stocks"][code] = json.loads(json.dumps(node, ensure_ascii=False, default=str))
    return out

def band_memory_merge_digest(local, digest):
    """把仓库里那一份（完整镜像）合并回本地完整记忆。

    冲突口径（★ 别改，这是两端不互相踩的前提）：
      · 状态/状态时间 → 取 `status_ts` 较新的一方（巡检可能比网页端新）；
      · 轨迹、各告警的最近推送时间 → 并集去重；
      · `closed` → **本地说了算**（用户手动归档的意图不能被云端覆盖）；
      · 其余字段（备注/入选价/平台高点/快照/复盘结果…）→ **本地非空以本地为准**，
        本地为空才用云端的值补上（容器重启后本地只剩个壳，能补就补，省得界面显示「暂缺」）。
    """
    out = _band_memory_empty()
    local_stocks = dict((local or {}).get('stocks') or {})
    remote_stocks = (digest or {}).get('stocks') or {}
    out['stocks'] = local_stocks

    for code, r_node in remote_stocks.items():
        if not isinstance(r_node, dict):
            continue
        l_node = local_stocks.get(code)
        if l_node is None:
            # 云端有、本地没有（一般是容器重启后本地丢过）：**整节点照抄云端**。
            # ★ 以前这里只搬写死的白名单字段、note/added_price 一律置空 → 手写备注只要本地
            #   丢了就永久找不回来（而云端那份里明明是有的）；而且清单是硬编码的，
            #   漏一个字段不报错、只是悄悄少个值。所以这里改成 dict(r_node) 整体照抄，
            #   下面几个 setdefault 只负责把**缺的结构性字段**补成合法默认值。
            node = dict(r_node)
            node["code"] = r_node.get("code") or code
            node["name"] = r_node.get("name") or code
            # added_at：新格式里云端直接有；没有时退回 status_ts（旧版本留下的副本没这个字段）
            node["added_at"] = r_node.get("added_at") or r_node.get("status_ts") or ""
            node.setdefault("added_price", None)
            node["added_status"] = (r_node.get("added_status")
                                    or r_node.get("status") or "")
            node["added_source"] = r_node.get("added_source") or "云端巡检"
            node.setdefault("snapshot", {})
            node.setdefault("note", "")
            node["closed"] = bool(r_node.get("closed"))
            node.setdefault("status", "")
            node.setdefault("status_ts", "")
            node.setdefault("last_check", "")
            node.setdefault("price", None)
            node["history"] = [dict(h) for h in (r_node.get("history") or [])
                               if isinstance(h, dict)]
            out['stocks'][code] = node
            continue

        # 两边都有：状态取 status_ts 较新的一方
        if str(r_node.get("status_ts") or "") > str(l_node.get("status_ts") or ""):
            l_node["status"] = r_node.get("status") or l_node.get("status")
            l_node["status_ts"] = r_node.get("status_ts") or l_node.get("status_ts")
        if r_node.get("last_check"):
            l_node["last_check"] = r_node["last_check"]
        if not l_node.get("name"):
            l_node["name"] = r_node.get("name") or code
        # 轨迹并集（按 时间+状态 去重；云端条目缺价格时保留本地已有的价格）
        seen = {(str(h.get("ts", "")), str(h.get("status", "")))
                for h in (l_node.get("history") or []) if isinstance(h, dict)}
        for h in (r_node.get("history") or []):
            if not isinstance(h, dict):
                continue
            key = (str(h.get("ts", "")), str(h.get("status", "")))
            if key in seen:
                continue
            seen.add(key)
            l_node.setdefault("history", []).append(dict(h))
        l_node["history"] = sorted((l_node.get("history") or []),
                                   key=lambda x: str(x.get("ts", "")))[-BAND_MEMORY_HISTORY_MAX:]
        # 各告警的最近推送时间去重合并，避免两端各推一次
        alerts = dict(l_node.get("alerts") or {})
        for k, v in (r_node.get("alerts") or {}).items():
            if k not in alerts or str(v) > str(alerts[k]):
                alerts[k] = v
        l_node["alerts"] = alerts
        # ★ 吸收「本地空着、云端有值」的字段：容器重启后本地可能只剩个壳，而这些值云端
        #   那份里现在也有（导出不再裁剪），能补就补，省得界面显示「暂缺」。
        #   只在本地确实为空时才补 —— 本地非空永远以本地为准（本地是权威副本）。
        #   ⚠️ 写成通用循环、不列字段名：写死清单就是静默故障源（漏一个不报错，只是悄悄
        #   少个值 —— platform_high 当年就是这么漏的）。新增节点字段会自动覆盖。
        #   排除集 = 上面已经按各自规则处理过、或必须由本地说了算的字段：
        #     code/name 身份、status/status_ts/last_check 走时间戳仲裁、
        #     history/alerts 走并集、closed 由用户归档意图决定。
        _keep_local = {"code", "name", "status", "status_ts", "last_check",
                       "history", "alerts", "closed"}
        for _f in (set(r_node) - _keep_local):
            if not l_node.get(_f) and r_node.get(_f) is not None:
                _v = r_node[_f]
                l_node[_f] = dict(_v) if isinstance(_v, dict) else _v
        # closed 以本地为准（用户手动归档的意图不能被云端覆盖）
    return out

def _fetch_remote_memory():
    """拉取仓库里的「波段摘要」。返回 (ok, msg, digest 或 None, 远端是否加密)。

    第 4 项 encrypted 用于**防止把加密数据降级成明文**：若远端已是密文而本端没有密钥，
    调用方必须拒绝提交 —— 否则一次推送就会让之前的加密前功尽弃。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_WATCH_PATH}"
    try:
        r = requests.get(url, headers=_github_headers(_github_token()),
                         params={"ref": "main"}, timeout=(5, 15))
        if r.status_code == 404:
            return False, f"仓库里还没有 {GITHUB_WATCH_PATH}（首次同步时创建）", None, False
        if r.status_code != 200:
            return False, f"拉取失败 HTTP {r.status_code}: {r.text[:120]}", None, False
        raw = base64.b64decode(r.json().get("content") or "").decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False, "仓库里的波段摘要结构异常，已忽略", None, False
        encrypted = bool(payload.get(_ENC_FIELD))
        ok, digest, err = band_decrypt_obj(payload)
        if not ok:
            return False, err, None, encrypted
        if not isinstance(digest.get("stocks"), dict):
            return False, "仓库里的波段摘要结构异常，已忽略", None, encrypted
        return True, f"{len(digest.get('stocks', {}))} 只", digest, encrypted
    except Exception as e:
        _log("_fetch_remote_memory", e)
        return False, f"拉取异常：{type(e).__name__}: {str(e)[:100]}", None, False

def band_memory_pull_github():
    """从仓库拉取波段摘要并合并进本地记忆。返回 (ok, msg, merged_local_mem 或 None)。"""
    if not _github_token():
        return False, "未配置 GitHub Token", None
    ok, msg, digest, _enc = _fetch_remote_memory()
    if not ok or digest is None:
        return ok, msg, None
    return True, f"已拉取云端摘要（{msg}）", band_memory_merge_digest(load_band_memory(), digest)


def _remote_memory_state():
    """远端 band_watch.json 是否已存在：'missing' / 'present' / 'unknown'（取不到，无法判断）。

    为什么要单独探一下：`_fetch_remote_memory()` 把「文件不存在」与「文件在但读不出来
    （网络挂了 / 密钥不对）」**都**归成 digest=None，可这两种情况的处置必须相反 ——
    前者要自动创建，后者**绝不能**自动覆盖（否则会把别人密钥加密的数据冲掉）。"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_WATCH_PATH}"
    try:
        r = requests.get(url, headers=_github_headers(_github_token()),
                         params={"ref": "main"}, timeout=(5, 15))
    except Exception as e:
        _log("_remote_memory_state", e)
        return "unknown"
    if r.status_code == 404:
        return "missing"
    return "present" if r.status_code == 200 else "unknown"


def band_memory_autosync(mem):
    """静默把本地记忆「保底」同步到仓库 —— 免得每次都得手动点「同步到云端」。

    只在**确实有事可做**时才提交，绝不刷提交：
      ① 远端还没有 band_watch.json（首次点火，让云端巡检知道要盯哪些票）；或
      ② 远端已存在且能正常读出，但缺少本地已记住的某些股票代码。

    ★ 刻意**不**拿 status_ts / last_check 当「有变化？」的判据：云端巡检每 5 分钟就会
      改写这些字段，用它判断会让网页端与巡检**互相刷提交**。本仓库是 public，
      提交历史会被刷爆。状态新鲜度归云端巡检管，这边只负责「名单别丢」。
    ★ 远端存在但读不出来时一律不动，绝不自动覆盖。

    异常全部吞掉并记日志，绝不影响页面渲染。返回 (是否推送成功, 提示语)。"""
    try:
        if not _github_token():
            return False, ""
        codes = set((mem.get('stocks') or {}).keys())
        if not codes:
            return False, ""            # 本地空 → 不推，免得把云端清单清空
        state = _remote_memory_state()
        if state == "unknown":
            return False, ""            # 判断不了就别动，下次再说
        if state == "present":
            ok, _msg, digest, _enc = _fetch_remote_memory()
            if not ok or digest is None:
                return False, ""        # 读不出来 → 保守不动，避免覆盖
            if not (codes - set((digest.get('stocks') or {}).keys())):
                return False, ""        # 远端已覆盖本地全部条目 → 无事可做
        ok, m = band_memory_push_github(mem)
        return ok, (f"已自动同步到云端（{len(codes)} 只）" if ok else f"自动同步失败：{m}")
    except Exception as e:
        _log("band_memory_autosync", e)
        return False, ""


def band_memory_push_github(mem, merge_remote=True):
    """把记忆提交到仓库（`band_watch.json`）。返回 (ok, msg)。

    merge_remote=True 时先拉取云端那份并合并（原地更新 mem），这样巡检脚本写入的
    状态变化不会被网页端覆盖。冲突（409/422）时重取 sha 再试一次。

    提交的是 `_band_memory_digest(mem)` —— **完整记忆、不做字段裁剪**（2026-09-20 起）。
    若配置了 BAND_KEY，还会再用 Fernet 整段加密后才提交（此时连代码清单都是密文）。"""
    token = _github_token()
    if not token:
        return False, "未配置 GitHub Token（在 Streamlit Secrets 加 GITHUB_TOKEN 即可自动同步）"
    encrypted_remote = False
    if merge_remote:
        ok, _msg, digest, encrypted_remote = _fetch_remote_memory()
        if ok and digest is not None:
            merged = band_memory_merge_digest(mem, digest)
            mem.clear(); mem.update(merged)      # 原地更新，保持调用方引用有效
            _save_json(BAND_MEMORY_FILE, mem)
    if encrypted_remote and not band_crypto_enabled():
        return False, ("仓库里的摘要是加密的，但本端没配 BAND_KEY，已中止同步"
                       "（否则会把密文覆盖成明文，等于加密白做）")
    digest_obj = _band_memory_digest(mem)
    # ★ 条数必须在**加密前**统计：密文对象只有 {v, enc} 两个键，加密后再取 'stocks'
    #   恒为 0，提交信息会永远显示「更新波段记忆（0 只）」，看着像清单是空的（实测踩过）。
    n = len(digest_obj.get('stocks', {}))
    try:
        payload_obj = band_encrypt_obj(digest_obj)
    except Exception as e:
        _log("band_memory_push_github:encrypt", e)
        return False, f"加密失败，已中止同步（不会以明文提交）：{str(e)[:120]}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_WATCH_PATH}"
    content = base64.b64encode(json.dumps(payload_obj, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    for attempt in (1, 2):
        try:
            sha = None
            r = requests.get(url, headers=_github_headers(token),
                             params={"ref": "main"}, timeout=(5, 15))
            if r.status_code == 200:
                sha = r.json().get("sha")
            body = {"message": f"更新波段记忆（{n} 只）",
                    "content": content, "branch": "main"}
            if sha:
                body["sha"] = sha
            r = requests.put(url, headers=_github_headers(token), json=body, timeout=(5, 25))
            if r.status_code in (200, 201):
                return True, f"已同步到仓库（{n} 只），云端巡检将按此清单监控"
            if r.status_code in (409, 422) and attempt == 1:
                continue    # sha 过期（云端巡检刚提交过），重取后再试一次
            return False, f"同步失败 HTTP {r.status_code}: {r.text[:150]}"
        except Exception as e:
            _log("band_memory_push_github", e)
            if attempt == 2:
                return False, f"同步异常：{type(e).__name__}: {str(e)[:100]}"
    return False, "同步失败"


def band_memory_merge(local, remote):
    """合并本地与云端的记忆：并集；同一只股票取 status_ts 较新者为主体，
    再补上对方的轨迹条数，备注/归档状态以本地（用户手动操作）为准。"""
    out = _band_memory_empty()
    local_stocks = (local or {}).get('stocks', {}) or {}
    remote_stocks = (remote or {}).get('stocks', {}) or {}
    for code in set(local_stocks) | set(remote_stocks):
        l_node, r_node = local_stocks.get(code), remote_stocks.get(code)
        if l_node and not r_node:
            out['stocks'][code] = l_node; continue
        if r_node and not l_node:
            out['stocks'][code] = r_node; continue
        # 两边都有：以 status_ts 较新的为主体
        base = l_node if str(l_node.get('status_ts', '')) >= str(r_node.get('status_ts', '')) else r_node
        other = r_node if base is l_node else l_node
        merged = dict(base)
        # 轨迹取并集（按时间+状态去重后排序）
        seen, hist = set(), []
        for item in list(l_node.get('history') or []) + list(r_node.get('history') or []):
            if not isinstance(item, dict):
                continue
            k = (str(item.get('ts', '')), str(item.get('status', '')))
            if k in seen:
                continue
            seen.add(k); hist.append(item)
        hist.sort(key=lambda x: str(x.get('ts', '')))
        merged['history'] = hist[-BAND_MEMORY_HISTORY_MAX:]
        # 用户手动维护的字段以本地为准
        merged['note'] = l_node.get('note') or r_node.get('note') or ""
        merged['closed'] = bool(l_node.get('closed'))
        merged['added_at'] = l_node.get('added_at') or r_node.get('added_at')
        merged['added_price'] = l_node.get('added_price', r_node.get('added_price', 0.0))
        merged['added_status'] = l_node.get('added_status') or r_node.get('added_status')
        merged['added_source'] = l_node.get('added_source') or r_node.get('added_source')
        merged['snapshot'] = l_node.get('snapshot') or r_node.get('snapshot') or {}
        # 各告警的最近推送时间去重合并，避免两端各推一次
        alerts = dict(r_node.get('alerts') or {})
        for k, v in (l_node.get('alerts') or {}).items():
            if k not in alerts or str(v) > str(alerts[k]):
                alerts[k] = v
        merged['alerts'] = alerts
        merged['name'] = l_node.get('name') or r_node.get('name') or other.get('name')
        out['stocks'][code] = merged
    return out


def _fmt_price(v, digits=2):
    """价格格式化：缺失/为 0 时显示「—」而不是误导性的 0.00。

    云端摘要里可能没有价格（例如只从仓库读到了状态），这种情况下 0.00 会让人以为
    股价归零。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:.{digits}f}" if f > 0 else "—"


# ============ 波段参考位（2026-09-21 重写；旧口径见 git 历史） ============
# 旧版是「启动价 → 目标价 + 阶段进度」，进度 = (现价−启动价)÷(目标价−启动价)。
# 为什么整块废掉：那个"目标价"取的是 **含当天的** 60 日最高价 ⇒ 任何"今天创新高"的票
# 它都≈现价（突破判定还允许比 60 日高点低 0.5% 也算突破），于是清单上全是
# 「启动 9.94 → 目标 9.98」这种贴脸的伪进度。用户 2026-09-21 反馈
# 「这个目标为什么这么接近启动价格…是不是有点开玩笑了」。
#
# 现在改成三个**各自说清是什么**的位（见下面三个取值函数）：
#   · 入选价  = 入册那天的现价（自动入册的票必然≈现价，这是事实，不藏）
#   · 突破位  = **突破前**的 60 日平台上沿（不含当天）= 回踩到这儿才算不破位
#   · 防守位  = 20 日线（与「跌破支撑」判定用的是同一个数）
#
# ★★ 措辞红线（别删）：展示的全是「按固定规则算出来的参考位」，**不是预测**。
#    尤其**不许再出现「目标价」这个词** —— 创新高的票上方没有历史阻力，
#    编一个"目标"出来就是骗用户去挂单。


def _band_num(v):
    """把 None / '' / NaN 一律收敛成 0.0，省掉各处 try。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f else 0.0        # NaN 不等于自身


def band_start_price(node):
    """取该股的启动价（= 入选价 added_price），取不到时用轨迹兜底。

    ★ 为什么保留兜底（别删）：2026-09-20 起 `band_watch.json` 已是完整镜像、
      `added_price` 会一起同步，所以正常情况下走不到兜底分支了。
      但兜底仍有价值：① 升级前留下的旧云端副本没有这个字段；
      ② 节点不完整（手工编辑/半途写入）时不能把「阶段进度」直接判死 —— 显示不出来用户
      根本没法用。`history` 里入册那一条的 `price` 就是启动价，是可靠的近似来源
      （轨迹上限 40 条，正常远够不到）。
    """
    p = _band_num(node.get("added_price"))
    if p > 0:
        return p
    for h in (node.get("history") or []):
        if isinstance(h, dict):
            q = _band_num(h.get("price"))
            if q > 0:
                return q                # 轨迹按追加顺序，第一条即入册那次
    return 0.0


def band_breakout_pivot(node):
    """突破位：**突破前**的平台上沿（入册/刷新时由 `_calculate_band_metrics` 落盘）。

    ★ 取不到就返回 0（展示层显示「—」），**绝不许拿 platform_high 兜底** ——
      那正是这次要修的病：含当天的 60 日高点在"今天创新高"时完全等于现价，
      拿它当突破位/目标必然是「启动 9.94 → 目标 9.98」这种贴脸数字。
    """
    return _band_num(node.get("breakout_pivot"))


def band_defense_price(node):
    """防守位：20 日线。与 `_band_status` 的「跌破支撑 = 现价 < MA20」是同一把尺。

    ★ 为什么复用节点里的 `ma20` 而不是现算一遍：状态判定用的 MA20 和卡片上显示的 MA20
      必须是同一个数，否则会出现「状态说没跌破、卡片说已经到防守位」的自相矛盾。
    """
    return _band_num(node.get("ma20"))


# ---------- 本轮启动信息的展示（2026-09-22）----------
BAND_RUN_FAR_PCT = 25.0   # 离启动点涨幅达到它 → 标「追高区」，卡片换警示色

def _band_run_pick(src, *keys):
    """按候选键名依次取值；取到空/None 就当没有。字段缺失绝不抛异常。"""
    for k in keys:
        try:
            if k in src:
                v = src.get(k)
                if v is not None and v != '':
                    return v
        except Exception:
            continue
    return None


def band_run_info(src):
    """取本轮启动信息 → {'days','date','price','gain'}（缺什么给 0 / ''）。

    两种容器的字段名不同：**评估结果行**是 `RunDays/RunStartDate/RunGainPct`（大写驼峰），
    **记忆节点**是 `run_days/run_start_date/run_gain_pct`（小写下划线）。
    这里统一读一次 —— 三个展示点（选股页卡片 / 记忆清单 / 今日推送候选）
    各写一套 if 的话，早晚会有一处漏改，然后同一只票在两页显示不同的天数。
    ★ 老记忆节点、老扫描结果、手写测试夹具都没有这些字段，所以一律 `.get` + 降级，绝不下标硬取。
    """
    try:
        days = int(float(_band_run_pick(src, 'RunDays', 'run_days') or 0))
    except Exception:
        days = 0
    try:
        price = float(_band_run_pick(src, 'RunStartPrice', 'run_start_price') or 0.0)
    except Exception:
        price = 0.0
    try:
        gain = float(_band_run_pick(src, 'RunGainPct', 'run_gain_pct') or 0.0)
    except Exception:
        gain = 0.0
    try:
        date = str(_band_run_pick(src, 'RunStartDate', 'run_start_date') or '')[:10]
    except Exception:
        date = ''
    return {'days': days, 'date': date, 'price': price, 'gain': gain}


def band_run_text(src):
    """本轮启动的一句话标注；**算不出来就返回空串**（调用方决定显示「—」还是整段省略）。

    为什么必须写清楚（而不是只给个天数）：用户看到的困惑是
    「已经涨了很多很多了，为什么还说它是启动」—— 因为这是一个**可以持续很多天**的状态。
    所以这句话的作用就是把「启动」翻译成「**哪天**启动的、**到现在**涨了多少」。
    """
    info = band_run_info(src)
    if info['days'] <= 0:
        return ''
    if info['days'] == 1:
        return '本轮启动：今天刚确认'
    txt = (f"本轮启动：已 {info['days']} 个交易日 · 起点 {info['date'] or '—'}"
           f" · 至今 {info['gain']:+.1f}%")
    if info['gain'] >= BAND_RUN_FAR_PCT:
        txt += f"　⚠️ 已离启动点较远（≥{BAND_RUN_FAR_PCT:.0f}%，追涨空间小）"
    return txt


def band_alert_need_expand(node):
    """这一条预警要不要**自动展开**（「只展开新出现的预警」）。

    规则：预警类 + 未归档 + 用户还没点过「我已看过」（alert_ack ≠ status_ts）。
    用户点过之后 ack == status_ts ⇒ 折叠；日后状态再变 ⇒ status_ts 跟着变 ⇒ 又展开一次。
    新预警本来就该被看到，这个"再展开"是刻意的，不是 bug。

    ★ 为什么抽成独立函数（2026-09-20）：这条判定原来内联在渲染里，
      而 **AppTest 的 expander 不暴露 `expanded` 状态、连折叠的内容也会进元素树**，
      所以 UI 层根本断言不了"折叠没折叠"。抽出来后可以直接测，
      UI 侧只留一条"渲染用的是这个函数"的接线守卫。
    """
    if not isinstance(node, dict):
        return False
    if (node.get('status') or '') not in BAND_ALERT_STATUSES:
        return False
    if node.get('closed'):
        return False
    return str(node.get('alert_ack') or '') != str(node.get('status_ts') or '')


def band_levels_text(node):
    """清单标题行用的紧凑串：「入选价 · 突破位 · 防守位」（不展开也能看到）。

    缺哪一段就**不拼那一段**（不塞「—」占位）—— 标题行已经很长，
    缺什么在展开区里说明原因。三个都没数时返回空串。
    """
    parts = []
    start = band_start_price(node)
    if start > 0:
        parts.append(f"入选 {_fmt_price(start)}")
    pivot = band_breakout_pivot(node)
    if pivot > 0:
        parts.append(f"突破位 {_fmt_price(pivot)}")
    defense = band_defense_price(node)
    if defense > 0:
        parts.append(f"防守 {_fmt_price(defense)}")
    return " · ".join(parts)


def _band_bulk_manage_ui(mem, nodes):
    """记忆清单的批量管理：勾选 → 归档 / 删除（一行常驻，不再折叠）。

    ★ 为什么改成现在这个形状（2026-09-21，用户第二轮抱怨「这个页面我想删除怎么这么难」）：
      上一版把「多选 + 确认框 + 删除」整块塞进一个**默认折叠**的 expander，用户得走
      「先展开 → 在下拉里找票 → 滚下去勾一个确认框 → 再回来点删除」四步，而且确认框
      和删除按钮都在折叠体内、中间还隔着别的控件 —— 这就是「难」的来源。
      现在：**整块常驻显示、不再折叠**；删除只多一步**紧挨着**的确认
      （点「🗑️ 删除选中」→ 同一位置立刻出现「✅ 确认删除」），不再要求先去勾一个复选框。
      同时卡片里的「🗑️ 从记忆中删除」已上提到卡片顶部（见下方 band_memory_ui），
      单只删除 = 展开卡片 + 点一下，不用再往下滚。
    ★ 一次确认是**安全下限**，不是仪式：删除不可撤销（本地记忆文件直接重写，并覆盖云端那份）。
      归档是可逆的，直接执行、不设门槛。
    ★ Streamlit 陷阱：**不能在同一个 run 里改已实例化 widget 的 session_state**
      （会抛 StreamlitAPIException）。所以凡是「要改 bandmem_bulk_pick」的动作一律走
      `on_click` 回调 —— 回调在下一次 run 开头执行，那时旧 widget 已经销毁，改 state 才合法。
      反过来，「删除待确认」只用 bandmem_bulk_del_pending 这个**非 widget** 的 state，
      普通按钮里直接赋值就是安全的（别把它改成 checkbox）。
    """
    nodes = [n for n in (nodes or []) if isinstance(n, dict)]
    if not nodes:
        return

    meta = []
    for n in nodes:
        c = str(n.get('code') or '?')
        _alert = (n.get('status') in BAND_ALERT_STATUSES) and not n.get('closed')
        meta.append({
            "code": c,
            "alert": _alert,
            "closed": bool(n.get('closed')),
            "label": (("⚠️ " if _alert else "")
                      + ("🗄️ " if n.get('closed') else "")
                      + f"{n.get('name') or c}（{c}）　{n.get('status') or '未知'}"),
        })
    by_label = {m["label"]: m["code"] for m in meta}

    def _set_pick(labels):
        st.session_state.bandmem_bulk_pick = list(labels)

    def _bulk_close(mem_obj, codes, value):
        for c in codes:
            n = mem_obj['stocks'].get(c)
            if isinstance(n, dict):
                n['closed'] = value
        save_band_memory(mem_obj)
        band_memory_push_github(mem_obj)
        st.session_state.band_memory_sync_msg = (
            f"已{'归档' if value else '取消归档'} {len(codes)} 只")
        st.rerun()

    def _bulk_delete(mem_obj, codes):
        names = []
        for c in codes:
            n = mem_obj['stocks'].pop(c, None)
            if isinstance(n, dict):
                names.append(n.get('name') or c)
        save_band_memory(mem_obj)
        band_memory_push_github(mem_obj)
        st.session_state.band_memory_sync_msg = (
            f"已批量删除 {len(codes)} 只：{'、'.join(names[:5])}"
            + ("…" if len(names) > 5 else ""))
        st.session_state.bandmem_bulk_pick = []      # 回调里改 state 是安全的
        st.rerun()

    def _confirm_bulk_delete(mem_obj, codes):
        # 回调跑在新一次 run 的**开头**：那时 bandmem_bulk_pick 尚未实例化，清空才合法。
        st.session_state['bandmem_bulk_del_pending'] = None
        _bulk_delete(mem_obj, codes)

    st.caption(f"🧺 批量处理（共 {len(nodes)} 只）：在下面挑票 → 点「归档」或「删除」。"
               "单只也可以展开卡片，直接在卡片**顶部**点「🗑️ 从记忆中删除」。")
    picked = st.multiselect("选中要处理的股票（可多选、可搜索）",
                            options=list(by_label.keys()),
                            key="bandmem_bulk_pick",
                            placeholder="这里选，或点下面「⚠️ 只选预警的」")
    codes = [by_label[x] for x in picked if x in by_label]

    _pend = st.session_state.get('bandmem_bulk_del_pending') or []
    if _pend:
        # ★ 就地确认：块紧贴在多选框下面，确认 / 取消就在原地出现，不用去别处找复选框。
        _pend_names = []
        for _c in _pend:
            _n = mem['stocks'].get(_c) if isinstance(mem, dict) else None
            _pend_names.append((_n or {}).get('name') or _c)
        st.warning(f"确认删除这 {len(_pend)} 只？**不可撤销**"
                   f"（{'、'.join(_pend_names[:6])}"
                   + ("…" if len(_pend_names) > 6 else "") + "）。"
                   "建议先点上方「📥 下载备份」留底。")
        _k1, _k2 = st.columns(2)
        _k1.button(f"✅ 确认删除（{len(_pend)}）", use_container_width=True,
                   key="bandmem_bulk_del_ok",
                   on_click=_confirm_bulk_delete, args=(mem, list(_pend)))
        if _k2.button("✖️ 取消", use_container_width=True,
                      key="bandmem_bulk_del_cancel"):
            st.session_state['bandmem_bulk_del_pending'] = None
            st.rerun()
        return

    _has_pick = bool(codes)
    _a1, _a2, _a3, _a4 = st.columns(4)
    _a1.button("⚠️ 只选预警的", use_container_width=True, key="bandmem_bulk_q_alert",
               on_click=_set_pick,
               args=([m["label"] for m in meta if m["alert"]],))
    _a2.button(f"🗄️ 归档选中（{len(codes)}）", use_container_width=True,
               key="bandmem_bulk_close", disabled=not _has_pick,
               on_click=_bulk_close, args=(mem, codes, True))
    _a3.button(f"♻️ 取消归档（{len(codes)}）", use_container_width=True,
               key="bandmem_bulk_unclose", disabled=not _has_pick,
               on_click=_bulk_close, args=(mem, codes, False))
    # ⚠️ 这里刻意用**普通按钮**而不是 on_click：bandmem_bulk_del_pending 不是 widget 的
    #    key，同一 run 里直接赋值合法，也就省掉一次多余的 rerun 回调。
    if _a4.button(f"🗑️ 删除选中（{len(codes)}）", use_container_width=True,
                  key="bandmem_bulk_del", disabled=not _has_pick):
        st.session_state['bandmem_bulk_del_pending'] = list(codes)
        st.rerun()
    if not _has_pick:
        st.caption("⬆️ 先在框里选票（或点「⚠️ 只选预警的」），归档 / 删除按钮才会亮。")


def _band_status_badge(status):
    """状态 → (颜色, 图标)，与 _band_status 的取色保持一致。"""
    return {
        '顶背离预警': ('#ff4b4b', '🔴'),
        '跌破支撑': ('#ff3333', '🔻'),
        '波段启动确认': ('#00cc66', '🚀'),
        '波段进行中': ('#f9e2af', '📈'),
        '波段未形成': ('#888', '⬜'),
    }.get(status, ('#888', '❔'))

# ================================================================
# 策略样本语料库（2026-09-19 新增）—— 让机器自己攒样本，不再依赖手工名单
# ================================================================
# 为什么要它：只记录「用户选中的票」，永远无法证明这套逻辑有效 —— 没有分母，
# 也没有对照组。要知道「突破＋放量」到底有没有用，必须同时看到：
#   ① 满足信号的日子后来怎么样了（signal）
#   ② 只满足一半条件的日子怎么样了（near_break / near_vol）—— 这是"到底哪个条件在起作用"的关键对照
#   ③ 完全不满足条件的普通日子怎么样了（trend_ctrl / base_ctrl）—— 这是基准率，没有它就不知道信号有没有超额
#
# ★ 三条不可动摇的原则（改这里之前务必读完）：
#  1. **样本与用户的名单完全分开**。用户名单是 band_memory（要给人看的），
#     样本语料是 band_samples（只给机器统计用）。两边的口径、数量、入册规则互不影响。
#  2. **全市场全收，但每条都打「可交易」标记**。不设板块与数量门槛（否则等于预先筛掉了
#     可能有效的那部分逻辑），但成交额/价格/ST 必须记下来，报表默认只看可交易样本 ——
#     否则"选出来的票都涨了"很可能只是不可交易小票的流动性幻觉。
#  3. **回填样本与前瞻样本永不混在一张表里统计**。回填（历史重放）自带前视与生存者偏差
#     （退市股缺失、前复权数据被后续分红改写），只能用来提假设；前瞻样本才是成绩。
SAMPLE_FILE = os.path.join(BASE_DIR, "band_samples.jsonl.gz")   # ★ 仅本地，已 gitignore
SAMPLE_CTRL_EVERY = 100        # 非「必算」日按 1/N 确定性抽样（趋势/基准对照）
SAMPLE_MAX_ROWS = 200000       # 行数上限，超出按日期从旧到新裁剪（聚合统计不受影响）
SAMPLE_MIN_BARS = 60           # 真实指标函数需要的历史根数
SAMPLE_MIN_AMOUNT_YI = 1.0     # 「可交易」口径：近20日日均成交额 >= 1 亿
SAMPLE_MIN_PRICE = 2.0         # 「可交易」口径：股价 >= 2 元（规避面值退市股）
SAMPLE_EVENT_DEDUP = True      # 连续「必算」日只保留事件首日，避免把一次机会重复计成 N 笔
# ★ 陈旧序列门槛：最后一根K线距今超过这么多自然日 → 视为停牌/退市，整只不进样本。
#   为什么必须有：取数是「最近 700 根」，若一只票 2002 年就退市了（如 000003 PT金田A），
#   它的 700 根全在 2002 年之前，长度校验照样通过，回看 450 天就落进 1998~2002 ——
#   实测 632 条样本里有 157 条是这种 20 多年前的"僵尸样本"，会把时间切分彻底带偏。
SAMPLE_MAX_STALE_DAYS = 30
# 成交量单位：腾讯日线按「手」返回（×100 股），新浪按「股」。实测两者算出的成交额一致，
# 但单位不同 —— 直接用 raw Volume 算成交额会差 100 倍，这里显式声明，不做猜测。
# 成交量单位：腾讯、东财都按「手」，新浪按「股」。选错单位成交额会差 100 倍。
SAMPLE_VOL_UNIT = {"qq": 100.0, "em": 100.0, "sina": 1.0}
# ★ 采样取数的提速。**这里曾经是"裸 requests.get + 串行"**：全市场 3750 只 × 每只约 1.7 秒
#   （云端在美国、接口在国内，单次往返就要几百毫秒）≈ **100 分钟**，
#   而页面顶部的自动刷新「让路」窗口只有 30 分钟 → 跑到一半必被掐断、结果全丢。
#
#   提速分两块，**实测的主因是连接复用，不是并发**（见 _debug_probe/bench_harvest.py，60 只真票）：
#     ① 连接复用（`_http_session`，每线程一个 Session）：3251ms/只 → 200ms/只，**16.2×**
#        根因是裸 requests.get 每次请求都重做一次 TCP+TLS 握手，本机实测握手就占约 1.6 秒。
#     ② 8 线程并发：200ms/只 → 69ms/只，**2.9×**
#     合计相对改造前 **47×**，产出内容与顺序完全一致（bench 里有断言）。
#   ⚠️ 线程数不是越大越好：实测 16 线程(90ms)、24 线程(117ms) 反而比 8 线程(69ms) 慢，
#      说明单条网络路径的带宽/队列是瓶颈。改这个值之前先跑 bench 确认，别凭直觉调大。
#   为什么敢并发：采样专用取数 `_sample_fetch_kline` **直接调取数函数**，
#   既不写磁盘缓存（`_cache_kline`）也不碰 `LAST_FETCH_DIAG`，全程无共享可写状态。
#   设成 1 就退化为原来的串行行为（排查限流问题时用）。
SAMPLE_FETCH_WORKERS = 8
SAMPLE_SOURCES = ("backfill", "forward")
SAMPLE_SOURCE_LABEL = {
    "forward": "前瞻采集（真实判断，可作为成绩）",
    "backfill": "历史回填（重放，仅供提假设）",
}
SAMPLE_TIER_LABEL = {
    "signal": "入场信号（突破＋放量）",
    "near_break": "突破了但没放量",
    "near_vol": "放量了但没突破",
    "trend_ctrl": "趋势对照（在20日线上）",
    "base_ctrl": "基准对照（20日线下）",
}

# ★ 云端语料的回读通道（2026-09-19 改造）。
#   为什么需要它：语料原来只存在**本地文件**里（SAMPLE_FILE 是 gitignore 的），
#   而每天自动采集跑在 GitHub Actions —— 采完的产物躺在 Actions 缓存里，
#   网页端根本看不见。于是云端页面上「逻辑有效性验证」**永远是空的**，
#   你只能在本机手点采集；就算在云上点了，Streamlit Cloud 没有持久化磁盘，
#   容器一重启采到的样本就没了。这就是「每天都要点一下」的根因。
#   现在 Actions 每天把**只含前瞻样本**的加密快照发布成这个滚动 release 资源，
#   网页端打开页面自动拉取 + 解密 + 与本地合并（见 load_band_samples_cloud）。
CORPUS_RELEASE_TAG = "data-latest"
CORPUS_ASSET_NAME = "band_samples_forward.enc"
CORPUS_URL = (f"https://github.com/{GITHUB_REPO}/releases/download/"
              f"{CORPUS_RELEASE_TAG}/{CORPUS_ASSET_NAME}")
CORPUS_FETCH_TIMEOUT = (4, 12)   # (连接, 读取) 秒；页面渲染路径上不能久等


def _sample_key(code, date, source):
    return f"{str(code).zfill(6)}|{str(date)[:10]}|{source}"


def _sample_hash_hit(code, date, every):
    """确定性抽样：同一 (代码, 日期, every) 永远得到同一结果。

    ★ 绝不能用 random —— 否则每次重跑抽到的日子都不一样，样本库无法复现，
      而且同一个机会可能被反复计入。用 md5 取模，稳定且与顺序无关。
    """
    if every <= 1:
        return True
    h = hashlib.md5(f"{code}|{str(date)[:10]}|{every}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % every == 0


def _sample_tier(m):
    """按真实指标给这一天分层。**只认 _calculate_band_metrics 的输出**，
    预筛函数不参与分层判定（它只负责"不漏"，见 _sample_prefilter）。"""
    try:
        bo = bool(m.get('breakout'))
        ve = bool(m.get('volume_expansion'))
        if bo and ve:
            return 'signal'
        if bo:
            return 'near_break'
        if ve:
            return 'near_vol'
        if float(m.get('current') or 0) > float(m.get('ma20') or 0):
            return 'trend_ctrl'
        return 'base_ctrl'
    except Exception as e:
        _log("_sample_tier", e)
        return None


def _sample_prefilter(df, min_bars=SAMPLE_MIN_BARS):
    """向量化预筛：标出「必须用真实指标函数全量评估」的交易日。

    ★ 必须是 signal/near_break/near_vol 三个分层的**超集**。
      因为逐日重放的真实函数调用约 3ms/天（实测），全量跑 4000 只 × 450 天 ≈ 40 分钟；
      预筛把这一层压到毫秒级，只对可能成为样本的日子付真实计算的钱。
      一旦预筛漏掉真实信号，样本库就会系统性缺失 —— 比慢严重得多。
      所以这里用**与 _calculate_band_metrics 完全相同的公式**算 breakout / volume_expansion
      （逐字对齐，不是近似）。
      测试里有一条不变式断言守住它：凡真实分层不为对照的日子，必须在预筛集合内。

    ★ 为什么**只**放 breakout / volume_expansion，不放「已在20日线上」：
      「站上20日线」是 _sample_tier 里的 trend_ctrl 分层，它只是**对照**，按 1/N 抽样即可。
      若把它也划进必算集合，会连带毁掉事件去重 ——
      一只股票连涨 100 天，中间出现的那次真突破会和前面 99 个"站上20日线"的日子
      被并成同一个事件，真正的信号样本就被丢掉了。这个坑已经踩过一次。
    """
    try:
        close = df['Close'].astype(float)
        high = df['High'].astype(float)
        vol = df['Volume'].astype(float)
        n = len(df)
        idx = np.arange(n)
        hi60 = high.rolling(60).max()
        c60 = close.rolling(60).max()
        v5 = vol.rolling(5).mean()
        v20 = vol.rolling(20).mean()
        # 与 _calculate_band_metrics 逐字一致的 breakout / volume_expansion
        breakout_v = (close >= hi60 * 0.995) & (close >= c60 * 0.999)
        vol_exp_v = ((v20 > 0) & (v5 / v20 >= 1.5)) | ((v20 > 0) & (vol >= v20 * 1.5))
        mask = (breakout_v | vol_exp_v) & (idx >= min_bars)
        mask = mask.fillna(False)
        return mask.values.astype(bool)
    except Exception as e:
        _log("_sample_prefilter", e)
        return np.zeros(len(df), dtype=bool) if len(df) else np.zeros(0, dtype=bool)


def _sample_tradable(df, i, vol_unit, name=''):
    """「可交易」标记。返回 (tradable|None, amt_yi|None, price, st_proxy)。

    ★ 历史回填拿不到当时的市值与 ST 名称（接口只给当下值），所以：
      - 成交额用日线自算（近20日均值），单位靠显式声明的 vol_unit 折算；
      - 市值一律记 None，**不拿今天的市值冒充历史**；
      - ST 只能用当前名称做代理，字段名直接叫 st_proxy，不装成历史事实。
      推不出就返回 None（未知），不用 False 冒充"不可交易"。
    """
    try:
        price = float(df['Close'].iloc[i])
    except Exception as e:
        _log("_sample_tradable/price", e)
        return None, None, 0.0, None
    st_proxy = _is_st_or_risk(name) if name else None
    amt_yi = None
    try:
        lo = max(0, i - 19)
        cl = df['Close'].astype(float).iloc[lo:i + 1]
        vo = df['Volume'].astype(float).iloc[lo:i + 1]
        amt = float((cl * vo).mean()) * float(vol_unit or 1.0)
        if amt > 0:
            amt_yi = round(amt / 1e8, 4)
    except Exception as e:
        _log("_sample_tradable/amount", e)
    if amt_yi is None or st_proxy is None:
        return None, amt_yi, price, st_proxy
    ok = (amt_yi >= SAMPLE_MIN_AMOUNT_YI) and (price >= SAMPLE_MIN_PRICE) and not st_proxy
    return bool(ok), amt_yi, price, st_proxy


def band_evaluate_asof(code, name, df, i, bench_df=None):
    """把 band_evaluate 的结果在**历史第 i 根K线**上重演。

    ★ 关键设计：切 `df.iloc[i-299 : i+1]`，而不是 `df.iloc[:i+1]`。
      因为线上 _get_daily_history 只取 300 根日线，MACD 用的是 ewm（递归、对起点敏感）。
      切最近 300 根 = 当时线上真正看到的那 300 根，所以这不是近似，是对线上行为的**精确复现**。
      切片再长/再短都会与线上不一致，别"顺手优化"。
    """
    try:
        if df is None or i is None or i < 0 or i >= len(df):
            return None
        lo = max(0, i - 299)
        sub = df.iloc[lo:i + 1]
        m = _calculate_band_metrics(sub)
        if m is None:
            return None
        score, reasons = _band_score(m)
        status, color = _band_status(m)
        return {
            'Code': str(code), 'Name': name or str(code),
            'Price': m['current'], 'ChangePct': _sample_change_pct(df, i),
            'Score': max(0, score), 'Status': status, 'StatusColor': color,
            'MA20': m['ma20'], 'MA60': m['ma60'],
            'PlatformHigh': m['platform_high'], 'PlatformLow': m['platform_low'],
            'BreakoutPivot': m['breakout_pivot'],
            'VolRatio': m['vol_ratio'], 'Position250': round(m['position_pct'], 1),
            'Reasons': ' | '.join(reasons), 'LastClose': m['current'],
            'Breakout': bool(m['breakout']), 'VolumeExpansion': bool(m['volume_expansion']),
            'TopDivergence': bool(m['top_divergence']), 'BelowSupport': bool(m['below_support']),
            # 与 _band_evaluate 同构（回测/归因直接复用同一批字段）
            'RunDays': m['run_days'], 'RunStartDate': m['run_start_date'],
            'RunStartPrice': m['run_start_price'], 'RunGainPct': m['run_gain_pct'],
        }
    except Exception as e:
        _log(f"band_evaluate_asof/{code}", e)
        return None


def _sample_change_pct(df, i):
    """当日涨跌幅（用前一根收盘算）。没有前一根就给 0，不猜。"""
    try:
        if i <= 0:
            return 0.0
        prev = float(df['Close'].iloc[i - 1])
        cur = float(df['Close'].iloc[i])
        if prev <= 0:
            return 0.0
        return round((cur / prev - 1.0) * 100, 2)
    except Exception as e:
        _log("_sample_change_pct", e)
        return 0.0


def _sample_bench_asof(bench_df, date_s):
    """把基准（沪深300）日线切到「入场日当天」为止；切不出来返回 None。

    ★ 为什么必须是 None 而不是退回整段 bench_df：退回整段 = 拿今天的大盘状态
      描述历史每一天，这是典型的前视泄露，会让「大盘环境」这个维度完全不可用。
      拿不准就**放弃这一列特征**（_bench_above_ma20(None) → None → 该桶不计入），
      样本少一列不会撒谎，多一列假信息才会。
    """
    try:
        if bench_df is None or not len(bench_df):
            return None
        d = str(date_s)[:10]
        sub = bench_df[bench_df['Date'].astype(str).str.slice(0, 10) <= d]
        if len(sub) >= 25:      # 与 _bench_above_ma20 的下限一致
            return sub
    except Exception as e:
        _log("_sample_bench_asof", e)
    return None


def band_sample_build(code, name, df, i, bench_df, source, vol_unit=1.0, market_meta=None):
    """构造一条样本：(入场时可见的特征) + (按 r1 规则算出的结果标签)。

    ★ 特征一律取自入场当日及之前，绝不使用事后信息 —— 这是整个语料库的地基。
      特征字典的键名与 _band_entry_context 保持一致，所以可以直接复用 _bucket_defs()
      的分桶定义，不用维护第二套维度口径。
    """
    try:
        r = band_evaluate_asof(code, name, df, i, bench_df)
        if r is None:
            return None
        tier = _sample_tier({
            'breakout': r['Breakout'], 'volume_expansion': r['VolumeExpansion'],
            'current': r['Price'], 'ma20': r['MA20'],
        })
        if tier is None:
            return None
        date = df['Date'].iloc[i]
        date_s = pd.Timestamp(date).strftime('%Y-%m-%d')
        meta = (market_meta or {}).get(str(code).zfill(6)) or {}
        if meta:
            # 前瞻：用接口给的当日真实市值/成交额
            amt_yi = meta.get('amt_yi')
            mv_yi = meta.get('mv_yi')
            price = float(meta.get('price') or r['Price'])
            st_proxy = _is_st_or_risk(meta.get('name') or name)
            tradable = None
            if amt_yi is not None:
                tradable = bool(amt_yi >= SAMPLE_MIN_AMOUNT_YI and price >= SAMPLE_MIN_PRICE
                                and not st_proxy)
        else:
            tradable, amt_yi, price, st_proxy = _sample_tradable(df, i, vol_unit, name)
            mv_yi = None
        # ★ 大盘环境必须切到「入场日当天」为止。直接吃整段 bench_df 会把**今天**的大盘
        #   状态套到历史每一天上（前视泄露），「大盘在20日线上」这个桶就彻底废了。
        #   切不动就返回 None（宁可这一列缺失，也不许用未来信息），由 _bench_above_ma20 判空。
        ctx = _band_entry_context({
            'Price': r['Price'], 'MA20': r['MA20'], 'MA60': r['MA60'],
            'Score': r['Score'], 'Position250': r['Position250'],
            'VolRatio': r['VolRatio'], 'Breakout': r['Breakout'],
        }, bench_above=_bench_above_ma20(_sample_bench_asof(bench_df, date_s)))
        ctx['change_pct'] = r['ChangePct']
        ctx['volume_expansion'] = r['VolumeExpansion']
        ctx['platform_high'] = r['PlatformHigh']
        ctx['dist_to_platform_pct'] = (round((r['PlatformHigh'] / r['Price'] - 1) * 100, 2)
                                       if r['Price'] > 0 else None)
        outcome = band_outcome_compute(date_s, float(r['Price']), df, bench_df)
        if outcome is None:
            # ★ 前瞻采集的正常情况：入场点就是最后一根K线，还没有"之后"可以判结案。
            #   这时**必须**以 pending 落库，绝不能不收 ——
            #   否则「每日前瞻采集」会永远产出 0 条、静默失效（这个坑被测试抓到过）。
            #   结果留给 band_samples_refresh_pending 在之后的每日采集里补算。
            if i >= len(df) - 1:
                outcome = _sample_pending_outcome()
            else:
                return None
        return {
            "code": str(code).zfill(6), "name": name or str(code), "date": date_s,
            "source": source, "tier": tier, "status": r['Status'],
            "rule_version": REVIEW_RULE_VERSION,
            "tradable": tradable, "amt_yi": amt_yi, "mv_yi": mv_yi,
            "price": round(float(price or r['Price']), 3), "st_proxy": st_proxy,
            "ctx": ctx, "outcome": outcome,
        }
    except Exception as e:
        _log(f"band_sample_build/{code}", e)
        return None


def _sample_fetch_kline(code, limit=700):
    """采样专用取数：返回 (df, vol_unit)。**必须同时带回单位**，
    否则成交额会差 100 倍（腾讯、东财按手，新浪按股）。

    ★ 服务顺序与主链一致（腾讯 → 东财 → 新浪），但**刻意不碰 `_feed_alive` / `_feed_note`**：
      这个函数跑在采集线程池里，而熔断器是**共享可写状态**；`_sample_harvest_one`
      必须保持「无共享可写状态」才能在多线程下安全并发（有 AST 断言守着这条）。
      采集侧本来就有自己的失败计数与并发限流，不差这一个熔断。
    """
    for name, fn, unit in (("腾讯", _fetch_kline_qq, SAMPLE_VOL_UNIT['qq']),
                           ("东财", _fetch_kline_em, SAMPLE_VOL_UNIT['em']),
                           ("新浪", _fetch_kline_sina, SAMPLE_VOL_UNIT['sina'])):
        try:
            df, _src, _errs = fn(_get_code(code), limit=limit)
            if df is not None and len(df) >= SAMPLE_MIN_BARS:
                return df, unit
        except Exception as e:
            _log(f"_sample_fetch_kline/{name}/{code}", e)
            continue
    return None, 1.0


def _sample_harvest_one(code, source, lookback_days, fetch, names, bench_df,
                        market_meta):
    """处理**单只**股票的全部采样工作，返回 (rows, stats)。

    ★ 刻意做成「无共享可写状态」的纯函数，这样才能安全地并发跑：
      只读 fetch / names / bench_df / market_meta，自己攒自己的 rows 与计数。
      （采样取数 `_sample_fetch_kline` 本来就不写磁盘缓存、不碰 LAST_FETCH_DIAG，
        所以并发不会互相踩。）
    """
    code = str(code).zfill(6)
    st = {"fetched": 0, "failed": 0, "stale": 0, "evaluated": 0, "kept": 0, "fail": None}
    try:
        df, unit = fetch(code, 700)
    except Exception as e:
        df, unit = None, 1.0
        _log(f"band_samples_harvest/fetch/{code}", e)
    if df is None or len(df) < SAMPLE_MIN_BARS + 1:
        st["failed"] = 1
        st["fail"] = code
        return [], st
    # ★ 陈旧序列剔除：取数只保证"最近 700 根"，退市/长期停牌的票会整段落在很多年前，
    #   长度校验拦不住它们。判据用最后一根K线距今天的自然日数，宁可少收也不收僵尸样本。
    try:
        _last = pd.Timestamp(df['Date'].iloc[-1])
        if not pd.isna(_last):
            if (pd.Timestamp(now_cn().date()) - _last.normalize()).days > SAMPLE_MAX_STALE_DAYS:
                st["stale"] = 1
                return [], st
    except Exception as e:
        _log(f"band_samples_harvest/stale/{code}", e)
    st["fetched"] = 1
    mask = _sample_prefilter(df)
    n = len(df)
    if lookback_days and lookback_days > 0:
        start = max(SAMPLE_MIN_BARS, n - int(lookback_days))
    else:
        start = n - 1
    prev_tier = None
    out = []
    for i in range(start, n):
        must = bool(mask[i]) if i < len(mask) else False
        if lookback_days and lookback_days > 0 and not must:
            # 非「必算」日（趋势/基准对照）按 1/N 确定性抽样，其余直接跳过省下 3ms/天
            if not _sample_hash_hit(code, df['Date'].iloc[i], SAMPLE_CTRL_EVERY):
                continue
        st["evaluated"] += 1
        row = band_sample_build(code, names.get(code) or '', df, i, bench_df,
                                source, vol_unit=unit, market_meta=market_meta)
        if row is None:
            continue
        # ★ 事件去重：只按**分层变化**去重，不按"是否必算日"去重。
        #   同一分层连续出现（比如连涨 50 天都是 trend_ctrl）只留第一天，
        #   因为那是同一个机会；但分层一变（trend_ctrl → signal）必须留下，
        #   那正是我们要找的信号。—— 按"必算日"去重会把行情中间的突破吞掉。
        tier = row.get('tier')
        if SAMPLE_EVENT_DEDUP and lookback_days and lookback_days > 0 and tier == prev_tier:
            continue
        prev_tier = tier
        out.append(row)
        st["kept"] += 1
    return out, st


def band_samples_harvest(codes, source='backfill', lookback_days=0, fetch=None,
                         bench_df=None, names=None, market_meta=None, progress=None,
                         max_codes=0):
    """采样主引擎：对一批代码逐日重放（或只看最新一天），产出样本行。

    lookback_days=0 → 只评估最后一根K线（前瞻采集用）
    lookback_days=N → 回看最近 N 个交易日（历史回填用）

    返回 (rows, report)。report 记下成功/失败/跳过的只数，失败原因逐代码留痕 ——
    取数失败必须可见，否则"样本怎么这么少"会变成一个查不出来的谜。

    ★ 并发：每只股票的工作交给 `_sample_harvest_one`，用 `SAMPLE_FETCH_WORKERS` 个线程
      同时跑（每只至少一次 HTTP 往返，改造前串行跑全市场要 100 分钟以上，页面早被自动刷新
      掐断了）。**产出顺序仍按输入顺序汇总**，所以结果与串行版逐字节一致 —— 语料可复现、
      测试可断言（test_memory §[16]、bench_harvest 都有对照断言）。
    """
    fetch = fetch or _sample_fetch_kline
    names = names or {}
    report = {"codes": 0, "fetched": 0, "failed": 0, "stale": 0,
              "no_bench": bench_df is None,
              "evaluated": 0, "kept": 0, "fail_examples": []}
    cl = list(codes)
    if max_codes and max_codes > 0:
        cl = cl[:max_codes]
    total = len(cl)
    workers = max(1, int(SAMPLE_FETCH_WORKERS or 1))
    report["workers"] = workers

    results = {}

    def _safe_one(code):
        """包一层：单只票的意外异常不许带走整批扫描（3750 只里崩一只不该全丢）。"""
        try:
            return _sample_harvest_one(code, source, lookback_days, fetch, names,
                                       bench_df, market_meta)
        except Exception as e:
            _log(f"band_samples_harvest/worker/{code}", e)
            return [], {"fetched": 0, "failed": 1, "stale": 0, "evaluated": 0,
                        "kept": 0, "fail": str(code).zfill(6)}

    if workers > 1 and total > 1:
        _fut2idx = {}
        with ThreadPoolExecutor(max_workers=workers) as _ex:
            for _k, _c in enumerate(cl):
                _fut2idx[_ex.submit(_safe_one, _c)] = _k
            _done = 0
            for _fut in as_completed(_fut2idx):
                _k = _fut2idx[_fut]
                _done += 1
                try:
                    results[_k] = _fut.result()
                except Exception as e:                 # 理论上到不了这里，留个兜底
                    _log(f"band_samples_harvest/future/{_k}", e)
                    results[_k] = ([], {"fetched": 0, "failed": 1, "stale": 0,
                                        "evaluated": 0, "kept": 0,
                                        "fail": str(cl[_k]).zfill(6)})
                if progress and _done % 20 == 0:
                    try:
                        progress(_done, total, str(cl[_k]).zfill(6))
                    except Exception as e:
                        _log("band_samples_harvest/progress", e)
    else:
        for _k, _c in enumerate(cl):
            if progress and (_k % 20 == 0):
                try:
                    progress(_k, total, str(_c).zfill(6))
                except Exception as e:
                    _log("band_samples_harvest/progress", e)
            results[_k] = _safe_one(_c)

    # ★ 按输入顺序汇总（并发不改变产出顺序）
    rows = []
    for _k in range(total):
        _r, _st = results.get(
            _k, ([], {"fetched": 0, "failed": 1, "stale": 0, "evaluated": 0,
                      "kept": 0, "fail": str(cl[_k]).zfill(6) if _k < total else ""}))
        rows.extend(_r)
        report["codes"] += 1
        for _key in ("fetched", "failed", "stale", "evaluated", "kept"):
            report[_key] += _st.get(_key, 0)
        if _st.get("fail") and len(report["fail_examples"]) < 8:
            report["fail_examples"].append(_st["fail"])
    if progress:
        try:
            progress(total, total, '')
        except Exception as e:
            _log("band_samples_harvest/progress_done", e)
    return rows, report


# ---------------- 存储：本地 JSONL.gz（原始行是唯一事实来源，聚合一律从它现算）----------------

def load_band_samples(path=None, cap=None):
    """读样本库，返回 {key: row}。文件不存在返回空 dict（首次运行是正常情况，不是错误）。

    ★ 解析结果按 (路径, cap, 文件 mtime+size) 缓存：这份语料是数 MB 的 gzip JSONL，
      解压+解析实测约 0.57s，而 Streamlit **每次交互都会重跑整个脚本** ——
      不缓存的话用户每点一个按钮都要白付一次（实测占一次重跑的 ~18%）。
      用 mtime+size 当缓存键：采集进程写完文件后自动失效，不需要谁记得手动清缓存。
    """
    p = path or SAMPLE_FILE
    if not os.path.exists(p):
        return {}
    try:
        _st_ = os.stat(p)
        _stamp = "%d:%d" % (_st_.st_mtime_ns, _st_.st_size)
    except OSError as e:
        _log("load_band_samples/stat", e)
        _stamp = "unknown"
    try:
        return _load_band_samples_parsed(p, cap, _stamp)
    except Exception as e:
        _log("load_band_samples", e)
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def _load_band_samples_parsed(p, cap, stamp):
    """解压 + 解析 JSONL.gz。`stamp` 不参与解析，只是缓存键的一部分。

    ★ 参数别加下划线前缀：Streamlit 会把下划线开头的参数排除出缓存键，
      那样文件更新了也永远读到旧结果。
    ★ 让异常**抛出去**（外层 load_band_samples 兜住）：st.cache_data 不缓存抛异常的调用，
      所以损坏的文件不会被缓存成「空库」而长期显现为「没有样本」。
    """
    out = {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception as e:
                _log("load_band_samples/line", e)
                continue
            if not isinstance(row, dict) or not row.get("code") or not row.get("date"):
                continue
            out[_sample_key(row["code"], row["date"], row.get("source"))] = row
            if cap and len(out) >= cap:
                break
    return out


def _unwrap_corpus_text(data):
    """把加密语料文件的内容解回 JSONL 文本。

    ★ 格式与 `sample_harvest.py::_wrap_plain` **严格一致，两边必须同步改**：
        文件 = JSON 文本 {"v":1,"enc":"<fernet token>"}
        解开 token → {"v":1,"kind":"band_samples","gz_b64":"<base64(gzip(JSONL))>"}
      解不开一律抛异常，交给调用方决定怎么降级 —— **绝不把「解不开」当成「库里是空的」**，
      否则密钥配错会静默表现为「没有样本」，最难排查。
    """
    env = json.loads(data)
    if not isinstance(env, dict):
        raise ValueError("语料密文外层不是 JSON 对象")
    obj = env
    if env.get(_ENC_FIELD):
        ok, obj, err = band_decrypt_obj(env)
        if not ok:
            raise ValueError(f"语料解密失败：{err}")
    b64 = (obj or {}).get("gz_b64")
    if not b64:
        raise ValueError("解密成功但内容里没有 gz_b64 字段（文件类型不对？）")
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


def parse_band_samples_text(text, rows=None):
    """把语料的 JSONL 文本解析成行列表。坏行跳过并留痕（与 load_band_samples 同一套判据）。"""
    out = rows if rows is not None else []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception as e:
            _log("parse_band_samples_text/line", e)
            continue
        if isinstance(row, dict) and row.get("code") and row.get("date"):
            out.append(row)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_cloud_corpus(url=CORPUS_URL):
    """拉云端语料快照 → (rows, err)。**永不抛异常**：失败返回 ([], 原因字符串)。

    ★ ttl 取 1 小时：语料是**每天盘后更新一次**，没必要每次 rerun 都去下几 MB。
      实测一次全市场前瞻 ≈ 3711 条（一只股票一条横截面），快照上限 6 万条约十几 MB 级。
    """
    try:
        r = _http_session().get(url, timeout=CORPUS_FETCH_TIMEOUT,
                                headers=_REQUEST_HEADERS, allow_redirects=True)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        return parse_band_samples_text(_unwrap_corpus_text(r.text)), ""
    except Exception as e:
        _log("_fetch_cloud_corpus", e)
        return [], f"{type(e).__name__}: {str(e)[:120]}"


def load_band_samples_cloud():
    """读云端前瞻语料 → (rows, 说明)。任何失败都只降级为「读不到」，不影响本地那份。"""
    if not band_crypto_enabled():
        return [], "本端没有 BAND_KEY，读不了云端语料（只有本机那份可用）"
    rows, err = _fetch_cloud_corpus()
    if err:
        return [], f"云端语料拉取失败：{err}"
    if not rows:
        return [], "云端语料还没有前瞻样本（首次发布后就会出现）"
    return rows, f"云端前瞻语料 {len(rows)} 条"


def save_band_samples(rows, path=None):
    """写样本库。rows 可以是 dict 或 list。返回实际写入行数。

    ★ 用 JSONL + gzip：样本到几十万行时普通 json 一次性反序列化会让页面卡住，
      JSONL 可以按行流式读，gzip 把体积压到约 1/5。
    """
    p = path or SAMPLE_FILE
    items = list(rows.values()) if isinstance(rows, dict) else list(rows)
    try:
        tmp = p + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            for row in items:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        os.replace(tmp, p)
        return len(items)
    except Exception as e:
        _log("save_band_samples", e)
        return 0


def band_samples_merge(old, new):
    """把新采的样本并入已有库。返回 (merged, added, updated, trimmed)。

    ★ 去重键 = 代码 + 日期 + 来源。同一机会重复采集不会重复计数。
      ★ 但「跟踪中 → 已结案」必须允许更新：早期采的样本当时候波段还没结束，
        结果字段是 pending；后来重跑时应该用新的结案结果覆盖它，
        否则样本库里会永久留着一批永远算不出结果的 pending 行。
    """
    merged = dict(old)
    added = updated = 0
    for row in new:
        k = _sample_key(row.get("code"), row.get("date"), row.get("source"))
        prev = merged.get(k)
        if prev is None:
            merged[k] = row
            added += 1
            continue
        p_closed = bool((prev.get("outcome") or {}).get("closed"))
        n_closed = bool((row.get("outcome") or {}).get("closed"))
        if n_closed and not p_closed:
            merged[k] = row
            updated += 1
    trimmed = 0
    if len(merged) > SAMPLE_MAX_ROWS:
        # 按日期从旧到新裁剪；聚合统计是从行现算的，所以裁剪会让最早那段退出统计窗口，
        # 这是有意的取舍（本地文件不能无限长大），面板上会显示实际覆盖的日期区间。
        ordered = sorted(merged.items(), key=lambda kv: str(kv[1].get("date") or ""))
        over = len(merged) - SAMPLE_MAX_ROWS
        for k, _v in ordered[:over]:
            merged.pop(k, None)
            trimmed += 1
    return merged, added, updated, trimmed


def band_samples_range(rows):
    """样本覆盖的日期区间与基本信息。"""
    ds = [str(r.get("date") or "") for r in rows]
    ds = [d for d in ds if d]
    return {
        "n": len(rows),
        "min_date": min(ds) if ds else "",
        "max_date": max(ds) if ds else "",
        "codes": len({r.get("code") for r in rows if r.get("code")}),
    }


def band_sample_summary(rows):
    """按来源 × 分层汇总。**来源绝不合并展示** —— 回填与前瞻的可信度完全不同。"""
    out = {}
    for r in rows:
        src = r.get("source") or "unknown"
        tier = r.get("tier") or "unknown"
        d = out.setdefault(src, {"total": 0, "tiers": {}, "tradable": 0, "closed": 0})
        d["total"] += 1
        d["tiers"][tier] = d["tiers"].get(tier, 0) + 1
        if r.get("tradable"):
            d["tradable"] += 1
        if (r.get("outcome") or {}).get("closed"):
            d["closed"] += 1
    return out


def _sample_usable(row):
    """能否进统计：必须已结案。跟踪中的样本只攒着，不参与任何结论。"""
    return bool((row.get("outcome") or {}).get("closed"))


def band_sample_lift(rows, source=None, tradable_only=True, split_ratio=0.7):
    """核心报表：每个入场条件分桶的表现，以及**与该来源基准率的差**。

    ★ 为什么必须有基准率：只说「突破＋放量 的胜率 55%」是没有意义的 ——
      牛市里随便买都有 55%。只有和同期全样本基准率对比出的**差额**才是逻辑的贡献。

    ★ split_ratio：按日期排序把样本切成前 70%（样本内）与后 30%（样本外）。
      参数/规律只有在样本外仍保持同号才算站得住。这是防止"在噪音里挑好看的那一桶"
      唯一的自动化防线 —— 没有它，样本越多越容易自欺。

    返回 {"baseline": {...}, "rows": [...]}。
    """
    use = [r for r in rows if isinstance(r, dict) and _sample_usable(r)
           and (source is None or r.get("source") == source)
           and (not tradable_only or r.get("tradable"))]
    baseline = {"n": len(use)}
    if not use:
        baseline.update({"avg_excess": None, "win_rate": None, "avg_ret": None})
        return {"baseline": baseline, "rows": []}
    ex = [r['outcome']['excess_pct'] for r in use
          if r['outcome'].get('excess_pct') is not None]
    baseline["avg_excess"] = round(sum(ex) / len(ex), 2) if ex else None
    baseline["win_rate"] = round(sum(1 for r in use
                                     if r['outcome'].get('verdict') == 'win') / len(use) * 100, 1)
    rets = [r['outcome'].get('ret_pct') or 0.0 for r in use]
    baseline["avg_ret"] = round(sum(rets) / len(rets), 2)
    # 时间切分：按日期升序取前 split 比例为样本内，其余为样本外
    ordered = sorted(use, key=lambda r: str(r.get("date") or ""))
    cut = int(len(ordered) * split_ratio)
    is_set = {_sample_key(r.get("code"), r.get("date"), r.get("source")) for r in ordered[:cut]}
    outs = []
    for dim, fn in _bucket_defs():
        groups = {}
        for r in use:
            name = fn(r.get('ctx') or {})
            if name:
                groups.setdefault(name, []).append(r)
        for name in sorted(groups.keys()):
            items = groups[name]
            o = [r['outcome'] for r in items]
            e = [x['excess_pct'] for x in o if x.get('excess_pct') is not None]
            rets_b = [x.get('ret_pct') or 0.0 for x in o]
            is_items = [r for r in items
                        if _sample_key(r.get("code"), r.get("date"), r.get("source")) in is_set]
            is_ex = [r['outcome']['excess_pct'] for r in is_items
                     if r['outcome'].get('excess_pct') is not None]
            oos_items = [r for r in items
                         if _sample_key(r.get("code"), r.get("date"), r.get("source")) not in is_set]
            oos_ex = [r['outcome']['excess_pct'] for r in oos_items
                      if r['outcome'].get('excess_pct') is not None]
            avg_ex = round(sum(e) / len(e), 2) if e else None
            oos_avg = round(sum(oos_ex) / len(oos_ex), 2) if oos_ex else None
            ins_avg = round(sum(is_ex) / len(is_ex), 2) if is_ex else None
            enough = len(items) >= REVIEW_MIN_SAMPLE
            stable = None
            if enough and oos_avg is not None and ins_avg is not None and baseline["avg_excess"] is not None:
                d_in = ins_avg - baseline["avg_excess"]
                d_oos = oos_avg - baseline["avg_excess"]
                stable = bool((d_in > 0) == (d_oos > 0))
            outs.append({
                "dim": dim, "bucket": name, "n": len(items), "enough": enough,
                "win_rate": round(sum(1 for x in o if x.get('verdict') == 'win') / len(items) * 100, 1),
                "avg_ret": round(sum(rets_b) / len(rets_b), 2),
                "avg_excess": avg_ex,
                "lift": round(avg_ex - baseline["avg_excess"], 2)
                        if (avg_ex is not None and baseline["avg_excess"] is not None) else None,
                "in_sample_excess": ins_avg, "oos_excess": oos_avg,
                "oos_n": len(oos_items), "stable": stable,
            })
    return {"baseline": baseline, "rows": outs}


def band_sample_ui():
    """🧪 逻辑有效性验证：机器自采样本的诚实报表。

    ★ 本面板只做统计，**不改任何选股参数**，也不产出给你的推荐名单。
      它的唯一用途是回答「哪个入场条件真的在贡献超额收益」。
    """
    st.markdown("---")
    st.header("🧪 逻辑有效性验证")
    st.caption("样本由机器在全市场自动采集，与「🧠 波段记忆」里给你的名单**完全分开** —— "
               "这一块是给逻辑自己用的：靠它才能回答「突破＋放量到底有没有用」。")
    st.caption(f"判定规则 **{REVIEW_RULE_VERSION}**（与复盘面板同一套）｜"
               f"基准 沪深300｜样本量下限 {REVIEW_MIN_SAMPLE} 笔｜"
               "回填样本与前瞻样本**分开统计，绝不混算**")

    rows_map = load_band_samples()
    _local_n = len(rows_map)
    # ★ 云端那份（盘后 Actions 自动采的前瞻语料）合并进来：这一步是「不用手点」的关键。
    #   复用 band_samples_merge —— 去重键与「结案覆盖 pending」的规则完全同一套，
    #   不另写一份合并逻辑。云端读不到时只降级，本地那份照旧可用。
    _cloud_rows, _cloud_msg = load_band_samples_cloud()
    if _cloud_rows:
        rows_map, _c_added, _c_updated, _c_trimmed = band_samples_merge(rows_map, _cloud_rows)
    rows = list(rows_map.values())
    st.caption(f"📥 语料来源：本地明文 {_local_n} 条 ｜ {_cloud_msg}"
               "　·　云端那份由**盘后自动采集**每天更新，不需要你手点。")
    if not rows:
        st.info("还没读到样本。三种来源：\n\n"
                "0. **盘后自动采集已上线** —— 周一至周五 15:40（北京时间）由 GitHub Actions "
                "自动扫全市场，采到的前瞻样本每天发布一次，本页打开时会自动拉取，"
                "**正常情况你什么都不用点**；\n"
                "1. **展开下面的「🔧 手动采集」**，点「采集今日全市场样本」—— "
                "立刻按今天的真实判断收一批（这是唯一能当成绩用的那类）；"
                "全市场约 5～15 分钟，只想先试流程就把「试跑：只采前 N 只」填 50；\n"
                "2. **在本机跑一次历史回填** —— 立刻拿到数千条重放样本"
                "（自带前视偏差，只能提假设），脚本见 `_debug_probe/backfill_samples.py`。")
        _sample_forward_action({})
        return

    rng = band_samples_range(rows)
    summ = band_sample_summary(rows)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("样本总数", f"{rng['n']} 条")
    m2.metric("覆盖股票", f"{rng['codes']} 只")
    m3.metric("前瞻样本", f"{summ.get('forward', {}).get('total', 0)} 条")
    m4.metric("回填样本", f"{summ.get('backfill', {}).get('total', 0)} 条")
    m5.metric("已结案", f"{sum(s.get('closed', 0) for s in summ.values())} 条")
    st.caption(f"样本覆盖区间：{rng['min_date']} ～ {rng['max_date']}"
               f"（超过 {SAMPLE_MAX_ROWS} 条时从最早开始裁剪，所以区间起点会随时间前移）")
    # ★ 下面两行是「数字看着不对」的防呆说明（用户 2026-09-20 就为此专门截图问过）。
    #   共同点：都是**只有一天横截面**时的正常表现，但看起来像 bug，所以只在命中时才显示，
    #   不命中就不占版面。别删 —— 删了下次还会被问一遍。
    if rng['n'] and rng['codes'] and rng['n'] == rng['codes']:
        st.caption("ℹ️ 「覆盖股票」此刻与「样本总数」相等，**这是正常的**：一只股票一天只记一条，"
                   "库里只有一天的横截面时，条数必然等于只数。攒几天后总数会成倍增长、"
                   "只数基本不动，两个数自然就分开了。")
    if not summ.get('backfill', {}).get('total'):
        st.caption("ℹ️ 「回填样本」为 0：历史回填库只在**本机**跑、只存在本机，"
                   "云端同步过来的这份刻意**只含前瞻样本** —— 回填自带前视偏差（退市股缺失、"
                   "前复权被后续分红改写），只能用来提假设，传上来的意义不大还占空间。")

    with st.expander(f"📊 来源 × 分层分布", expanded=False):
        _t = []
        for src in sorted(summ.keys()):
            d = summ[src]
            for tier in sorted(d["tiers"].keys()):
                _t.append({"来源": SAMPLE_SOURCE_LABEL.get(src, src),
                           "分层": SAMPLE_TIER_LABEL.get(tier, tier),
                           "条数": d["tiers"][tier]})
        if _t:
            st.dataframe(pd.DataFrame(_t), use_container_width=True, hide_index=True)
        st.caption("「入场信号」是完整的突破＋放量；两个 near_ 分层是只满足一半条件的对照 —— "
                   "**它们才是回答「到底哪个条件在起作用」的关键**；两个 ctrl 是基准率对照。")

    _src_pick = st.radio("看哪一类样本", ["forward", "backfill"], horizontal=True,
                         format_func=lambda k: SAMPLE_SOURCE_LABEL.get(k, k),
                         key="sample_src_pick")
    _tradable_only = st.checkbox("只看可交易样本（推荐）", value=True, key="sample_tradable_only")
    if _src_pick == "backfill":
        st.warning("这是**历史回填**样本：自带前视偏差与生存者偏差"
                   "（退市股缺失、前复权数据被后续分红改写、当时的市值/ST状态已不可知）。"
                   "**只能用来提出假设，不能当成绩看。**")
    lift = band_sample_lift(rows, source=_src_pick, tradable_only=_tradable_only)
    base = lift.get("baseline") or {}
    if not base.get("n"):
        st.info("这个口径下还没有已结案的样本。回填刚跑完时大部分样本已结案；"
                "前瞻样本要等波段走完（最长 40 个交易日）才有结果。")
    else:
        b1, b2, b3 = st.columns(3)
        b1.metric("基准样本量", f"{base['n']} 笔")
        b2.metric("基准胜率", f"{base.get('win_rate')}%")
        b3.metric("基准平均超额",
                  "—" if base.get('avg_excess') is None else f"{base['avg_excess']:+.2f}%")
        st.caption("以上是**该来源全样本**的基准率。下面每个分桶都要跟它比 —— "
                   "只看分桶胜率的绝对值会被牛市骗（谁都在赚）。")
        if lift["rows"]:
            _df = pd.DataFrame(lift["rows"]).rename(columns={
                "dim": "维度", "bucket": "分桶", "n": "笔数", "enough": "样本够",
                "win_rate": "胜率%", "avg_ret": "平均收益%", "avg_excess": "平均超额%",
                "lift": "相对基准", "in_sample_excess": "样本内超额%",
                "oos_excess": "样本外超额%", "oos_n": "样本外笔数", "stable": "稳健",
            })
            _df["样本够"] = _df["样本够"].map({True: "✅", False: "样本不足"})
            _df["稳健"] = _df["稳健"].map({True: "✅ 同号", False: "❌ 反号", None: "—"})
            st.dataframe(_df[["维度", "分桶", "笔数", "样本够", "胜率%", "平均超额%",
                              "相对基准", "样本内超额%", "样本外超额%", "样本外笔数", "稳健"]],
                         use_container_width=True, hide_index=True)
            st.caption("**「稳健」一列是整个面板最有价值的地方**：样本内为正、样本外却反号，"
                       "说明那点优势只是噪音。样本不足或反号的桶，一律不许拿来改逻辑。")
        else:
            st.caption("暂无可分桶的样本（入场上下文缺失或都未结案）。")

    _sample_forward_action(rows_map)

    _rep = st.session_state.get("sample_last_report")
    if _rep:
        with st.expander("上次采集明细", expanded=False):
            st.json({k: v for k, v in _rep.items() if k != 'fail_examples'})
            if _rep.get("fail_examples"):
                st.caption("取数失败的代码（最多列 8 个）：" + ", ".join(_rep["fail_examples"]))


def _sample_forward_action(rows_map):
    """「采集今日全市场样本」这一个动作的 UI + 落盘。空库与非空库共用，避免两处逻辑漂移。

    ★ 2026-09-21（用户原话：「你都是全自动了，为什么还有这个按钮？」）：
      盘后 Actions 自动采集 + 本页打开自动拉取早已上线，手动采集只是**兜底 / 调试**的口子，
      但它原来是个正面朝用户的大按钮、右边还挂着一整块「正常情况下你不需要点这个按钮」的说明
      —— 看起来就像「每天都要点一下」的常规步骤，这正是被质疑的原因。
      现在整块收进**默认折叠**的 expander，标签上直接写「平时不用点」，要兜底时才展开。
      功能一字未删；按钮 key（sample_forward_btn / sample_forward_limit）保持不变，
      回归脚本仍按老 key 定位。
    """
    with st.expander("🔧 手动采集（兜底 / 调试用，平时不用点）", expanded=False):
        st.caption("正常情况**不用点**：周一至周五 15:40（北京时间）GitHub Actions 自动扫全市场，"
                   "采到的前瞻样本每天发布一次，本页打开时自动拉取"
                   "（看上面「语料来源」那行就知道有没有拉到）。"
                   "手动采集只在两种情况下有意义：① 想立刻用**今天**的判断收一批；"
                   "② 本地调试流程。"
                   "⚠️ 部署在 Streamlit Cloud 上时，手动采到的样本只存在容器里，"
                   "**容器一重启就没了、也不会传到云端**，所以别把它当常规手段。")
        _c1, _c2 = st.columns([1, 1.1])
        with _c1:
            _do = long_button("📥 采集今日全市场样本", key="sample_forward_btn",
                              use_container_width=True)
        with _c2:
            # ★ 这里原来写的是「约 1～3 分钟」，**是错的**：全市场 3750 只 × 每只一次 HTTP
            #   往返（云端在美国、接口在国内），改造前实测跑了 100 分钟以上，比「让路」窗口还长，
            #   必被自动刷新掐断。现已改成「线程本地连接复用 + 8 线程并发」（实测约 47×；
            #   其中连接复用 16.2×、并发 2.9×），耗时降到十几分钟以内。
            #   故意给一个**区间**而不给单点：云端耗时随网络波动很大，本机测不出云端绝对值，
            #   所以在文案里引导用户用「试跑」自己量，而不是给一个看起来很准的假数字。
            _limit_in = st.number_input("试跑：只采前 N 只", min_value=0, max_value=6000,
                                        value=0, step=100, key="sample_forward_limit",
                                        help="0 = 全市场（默认）。想先确认流程通不通，填 50。")
        st.caption("扫描范围为全市场（含创业板/科创板/北交所），只记信号与对照，"
                   "不发推送、不写进记忆名单；全市场约 5～15 分钟（视网络而定，想先量准就填「试跑」），"
                   "期间请保持本页打开（自动刷新已让路）。")
    if not _do:
        return
    _limit = int(_limit_in or 0)
    _sample_progress_widget.bar = None
    try:
        with st.spinner("正在扫描全市场并采集样本（约 5～15 分钟，已开连接复用 + 8 线程）..."
                        if not _limit else
                        f"正在试跑采集（前 {_limit} 只，约 1 分钟）..."):
            rows_new, rep = band_samples_harvest_forward(
                progress_cb=_sample_progress_widget, limit=_limit)
    except Exception as e:
        _log("band_sample_ui/forward", e)
        st.error(f"采集出错（已拦截）：{e}")
        return
    st.session_state.sample_last_report = rep
    # ★ 先把上次留下的「跟踪中」样本重算一遍：前瞻样本采下来时波段还没走完，
    #   不补算就永远是 pending，前瞻样本库只进不出、永远无法统计。
    merged, added, updated, trimmed = band_samples_merge(rows_map, rows_new)
    try:
        _bench = _bench_history()
        merged, refreshed = band_samples_refresh_pending(merged, bench_df=_bench)
    except Exception as e:
        _log("band_sample_ui/refresh_pending", e)
        refreshed = 0
    if not rows_new and not refreshed:
        st.warning(f"本次没采到新样本（扫描 {rep.get('codes')} 只，取数失败 {rep.get('failed')} 只）。"
                   f"{rep.get('error') or '非交易时段或全市场接口不稳时会这样。'}")
        return
    save_band_samples(merged)
    st.success(f"采集完成：本次 {len(rows_new)} 条，新增 {added} 条，"
               f"更新结案 {updated + refreshed} 条"
               + (f"，裁剪 {trimmed} 条" if trimmed else "") + "。")
    if rep.get("no_bench"):
        st.warning("⚠️ 本次没取到基准（沪深300）日线：这批样本的**超额收益会是空的**，"
                   "只能看绝对收益。等网络恢复后重跑同一天即可补上（同键会覆盖更新）。")
    st.rerun()


def _sample_progress_widget(done, total, code):
    """采集进度条。用函数属性持有 Streamlit 进度对象，避免每次回调都新建组件。"""
    try:
        if getattr(_sample_progress_widget, "bar", None) is None:
            _sample_progress_widget.bar = st.progress(0.0, text="正在扫描全市场...")
        if total and done <= total:
            _sample_progress_widget.bar.progress(min(done / total, 1.0),
                                                text=f"已扫描 {done}/{total}（{code}）")
    except Exception as e:
        _log("_sample_progress_widget", e)


def _sample_meta_add(meta, names, s):
    """把东财股票列表的一行收进 (meta, names)。

    ★ 成交额 / 市值取不到时记 **None（未知）**，绝不写 0：
      下游 `band_sample_build` 是 `if amt_yi is not None` 才判「可交易」，
      写 0 会被判成「不可交易」，等于把一只正常股票悄悄排除在统计之外。
    """
    code = str(s.get("f12") or "").zfill(6)
    if len(code) != 6 or not code.isdigit():
        return
    price = _safe_float(s.get("f2"))
    if price <= 0:
        return
    amt = _safe_float_or_none(s.get("f62"))
    mv = _safe_float_or_none(s.get("f20"))
    meta[code] = {
        "name": str(s.get("f14") or ""), "price": price,
        "mv_yi": round(mv / 1e8, 2) if (mv and mv > 0) else None,
        "amt_yi": round(amt / 1e8, 4) if (amt and amt > 0) else None,
    }
    names[code] = meta[code]["name"]


def band_samples_harvest_forward(progress_cb=None, bench_df=None, limit=0):
    """前瞻采集：扫描全市场，对**今天**这一根K线取样本。

    返回 (rows, report)。扫描口径与选股一致（同一套 _EM_HOSTS 分页），
    但**不套用 EXCLUDE_PREFIXES** —— 采样要覆盖所有板块，这是与选股的关键差别。

    limit>0 时只取清单里的前 limit 只（报告里的 universe 仍报真实总数）。
    这是给**冒烟测试**用的：云端 CI 想验证「密钥配好了、加密落盘通了」时，
    跑全市场要十几分钟，跑 5 只一分钟就够。正式采集必须留空（limit=0）。

    ★ 两条走过弯路的地方：
      ① 北交所不在东财股票列表分页里，必须单独按 `m:0+t:81+s:2048` 补一次，
         否则「不限板块」是假的 —— 北交所的波动结构恰恰是最不该被预先排除的。
      ② 基准（沪深300）必须一起传进采样引擎。不传的话 band_outcome_compute 拿不到
         bench_df → excess_pct 恒为 None → 分桶报表里所有「相对基准」全是空的，
         这批样本就永远回答不了「它到底有没有产生超额」——采集全白做。
      ③ 清单分页**不能遇空页就 break**（2026-09-22 踩到）：东财分页接口会「半死」——
         前几页正常、之后每页都空。旧逻辑一 break 就只拿了几页就去采样，产出的是
         「按主力净额排序的前 N 只」这种**有偏子集**，退出码却是 0、日志也不报错。
         现在改成：空页重试 + 只在**连续**空页时才收尾 + 清单总量低于下限就显式报错退出。
    """
    report = {"codes": 0, "fetched": 0, "failed": 0, "kept": 0, "fail_examples": []}
    meta = {}
    names = {}
    # ★ 清单分页的自我保护参数，刻意写成**函数内局部量**：
    #   sample_harvest.py 是按白名单（WANT_CONSTS）用 AST 抽模块级常量的，
    #   新增的模块级常量不会被注入；函数体里的局部量则随函数一起被抽走。
    _pn_max = 60          # 页上限：60 页 × 100 只 = 6000，覆盖沪深 A 股并留余量
    _page_retry = 2       # 单页返回空时的重试次数
    _retry_wait = 1.5     # 重试间隔（秒）
    _empty_stop = 2       # 连续空页达到这个数，才认定「真的到底了」
    _universe_min = 3000  # 清单低于此数 → 判定残缺，放弃本次采集（宁可不采，不采有偏样本）
    try:
        raw = []
        empty_run = 0
        pages_ok = 0
        for pn in range(1, _pn_max + 1):
            page = []
            for _attempt in range(_page_retry + 1):
                page = fetch_market_page(pn)
                if page:
                    break
                if _attempt < _page_retry:
                    time.sleep(_retry_wait)
            if not page:
                empty_run += 1
                _log("band_samples_harvest_forward/page_empty",
                     RuntimeError(f"第 {pn} 页返回空（已重试 {_page_retry} 次）；"
                                  f"连续空页 {empty_run}"))
                if empty_run >= _empty_stop:
                    break
                continue        # ★ 关键：单页失败只丢该页，绝不终止整个清单
            empty_run = 0
            pages_ok += 1
            raw.extend(page)
        if not raw:
            report["error"] = "全市场接口暂不可用（非交易时段/网络限制）"
            return [], report
        uniq_codes = {str(s.get("f12") or "").zfill(6) for s in raw}
        # ★ 清单完整性校验：接口「半死」时的典型形态是「前几页正常、之后每页都空」。
        #   旧逻辑遇空页即 break，于是安静地只拿了几页清单就去采样 —— 产出的其实是
        #   「按主力净额排序的前 N 只」这种有偏子集，而且退出码仍是 0，
        #   比彻底采不到更危险（脏样本进库后无法区分）。
        #   这里宁可显式失败，也不产出冒充「全市场」的有偏样本。
        if len(uniq_codes) < _universe_min:
            report["error"] = (f"全市场清单残缺：仅取到 {len(uniq_codes)} 只 / {pages_ok} 页，"
                               f"低于下限 {_universe_min} —— 放弃本次采集以免污染语料库")
            report["universe"] = len(uniq_codes)
            report["pages_ok"] = pages_ok
            return [], report
        for s in raw:
            _sample_meta_add(meta, names, s)
        # 北交所补采（失败不阻断主流程，但要在日志里留痕）
        try:
            r = requests.get(f"{_EM_HOSTS[0]}/api/qt/clist/get",
                             params={"pn": "1", "pz": "1000", "po": "1", "np": "1",
                                     "fltt": "2", "invt": "2", "fid": "f62",
                                     "fs": "m:0+t:81+s:2048",
                                     "fields": "f12,f14,f2,f20,f62"},
                             timeout=10, headers=_REQUEST_HEADERS)
            for s in _diff_to_list((r.json().get("data") or {}).get("diff")):
                _sample_meta_add(meta, names, s)
        except Exception as e:
            _log("band_samples_harvest_forward/bj", e)
    except Exception as e:
        _log("band_samples_harvest_forward/universe", e)
        report["error"] = f"获取全市场清单失败：{e}"
        return [], report
    if not meta:
        report["error"] = "全市场清单为空"
        return [], report
    if bench_df is None:                      # 允许调用方传入（无头采集脚本会复用缓存）
        bench_df = _bench_history()
    rows, rep = band_samples_harvest(
        list(meta.keys()), source='forward', lookback_days=0, names=names,
        market_meta=meta, bench_df=bench_df, progress=(progress_cb or None),
        max_codes=max(0, int(limit or 0)))
    rep["universe"] = len(meta)
    rep["pages_ok"] = pages_ok                # 清单取到几页 —— 用于事后判断有没有被截断
    rep["limit"] = max(0, int(limit or 0))     # 报告里显式留痕：这次是不是冒烟测试
    rep["no_bench"] = bench_df is None        # 必须显式告知：没有基准就没有超额
    return rows, rep


def _sample_pending_outcome():
    """前瞻样本「还没到能判结案的时候」的占位结果。

    字段与 band_outcome_compute 的输出保持同构，这样报表代码不用分支判断；
    但它 `closed=False`，所以永远进不了统计（_sample_usable 会挡住）。
    """
    return {
        "closed": False, "close_reason": "pending", "close_at": "", "close_price": None,
        "days_held": 0, "ret_pct": None, "bench_ret_pct": None, "excess_pct": None,
        "mfe_pct": None, "mae_pct": None, "verdict": "pending",
        "rule_version": REVIEW_RULE_VERSION, "computed_at": now_cn_str(),
    }


def band_samples_pending_keys(rows):
    """列出所有「还没结案」的样本（这些需要每天重算一次结果）。"""
    out = []
    for k, r in (rows.items() if isinstance(rows, dict) else enumerate(rows)):
        if not isinstance(r, dict):
            continue
        if not (r.get("outcome") or {}).get("closed"):
            out.append(r)
    return out


def band_samples_refresh_pending(rows_map, fetch=None, bench_df=None, max_items=400):
    """把跟踪中的样本重算一遍，能结案的补上结果。返回 (rows_map, 更新数)。

    ★ 为什么必须有它：前瞻样本采下来时波段还没走完，结果必然是 pending。
      没有这一步，前瞻样本库里会永久堆着一批永远算不出结果的占位行 ——
      「每日前瞻」就变成了只进不出、永远无法统计的死数据。
    只重算未结案的；已结案的绝不复算（历史结果不可被改写）。
    """
    fetch = fetch or _sample_fetch_kline
    pend = band_samples_pending_keys(rows_map)[:max_items]
    if not pend:
        return rows_map, 0
    by_code = {}
    for r in pend:
        by_code.setdefault(str(r.get("code")).zfill(6), []).append(r)
    updated = 0
    for code, items in by_code.items():
        try:
            df, _unit = fetch(code, 700)
        except Exception as e:
            _log(f"band_samples_refresh_pending/fetch/{code}", e)
            continue
        if df is None or len(df) < SAMPLE_MIN_BARS:
            continue
        dates = [str(x)[:10] for x in df['Date'].astype(str).tolist()]
        for r in items:
            d = str(r.get("date") or "")[:10]
            i0 = None
            for j in range(len(dates)):
                if dates[j] <= d:
                    i0 = j
            if i0 is None or i0 >= len(df) - 1:
                continue
            try:
                out = band_outcome_compute(d, r.get("price"), df, bench_df)
            except Exception as e:
                _log(f"band_samples_refresh_pending/{code}", e)
                continue
            if out is None or not out.get("closed"):
                continue
            r["outcome"] = out
            updated += 1
    return rows_map, updated


def band_review_ui():

    """📊 策略复盘：把「选了 → 结案 → 归因」变成可统计的看板。

    ★ 本面板**只做统计，不改动任何选股参数**。理由见 REVIEW_* 常量的注释：
      每周 10 只的样本量，头两三个月根本不足以区分参数优劣，此时调参就是噪音拟合。
      所以这一阶段的正确产出是「可归因的样本库 + 诚实的分桶报表」，不是自动优化。

    ★ 重算走显式按钮，**渲染时不发网络请求**：一是页面不会无故变慢，
      二是同一个按钮任何时候点都得到同一结果（因为结案完全由日线回溯决定）。
    """
    st.markdown("---")
    st.header("📊 策略复盘")
    st.caption(f"判定规则 **{REVIEW_RULE_VERSION}**（规则一改必须升版；老批次永远按入册时的版本判定）"
               f"｜基准 沪深300｜止盈 +{REVIEW_TP_PCT:.0f}%｜止损 跌破20日线｜"
               f"时间结案 {REVIEW_MAX_HOLD_DAYS} 个交易日")
    st.caption("⚠️ 本面板只统计、**不改选股参数**。样本量够之前任何调参都是噪音拟合 —— "
               f"分桶样本少于 {REVIEW_MIN_SAMPLE} 笔只标「样本不足」，不下结论。")

    mem = load_band_memory()
    batches = load_band_batches()
    if not mem.get('stocks'):
        st.info("记忆里还没有股票。先在上方选股，或用「🧠 波段记忆 → 手动记入」把想跟踪的代码加进来。")
        return

    _c1, _c2 = st.columns([1, 3])
    with _c1:
        _do = long_button("🔄 重算结案与归因", key="band_review_refresh",
                          use_container_width=True)
    with _c2:
        st.caption("重算只处理「跟踪中」的条目（已结案的不会再变）；结案点由日线回溯决定，"
                   "与是否开着网页无关，因此结果可复现。")
    if _do:
        with st.spinner("正在回溯日线、计算结案点与归因指标..."):
            mem, _n = band_review_refresh(mem)
            save_band_memory(mem)
        st.success(f"已更新 {_n} 只的结案/归因。")
        st.rerun()

    st_ = band_review_stats(mem)

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("跟踪中", f"{st_['open']} 只")
    m2.metric("已结案", f"{st_['closed']} 只")
    m3.metric("胜率", f"{st_['win_rate']}%")
    m4.metric("平均超额", ("—" if st_['avg_excess'] is None else f"{st_['avg_excess']:+.2f}%"))
    m5.metric("平均最大浮盈", f"{st_['avg_mfe']:+.2f}%")
    m6.metric("平均最大浮亏", f"{st_['avg_mae']:+.2f}%")
    if not st_['bench_ready']:
        st.warning("基准（沪深300）日线本次没取到，超额收益暂时算不出来 —— "
                   "缺少超额的胜率会虚高（牛市里谁都在赚），别据此下判断。")

    if st_['closed'] == 0:
        st.info("还没有已结案的样本。点上方「🔄 重算结案与归因」先算一遍："
                "只要某笔已触发止盈/跌破20日线/满40个交易日，就会被判为结案并计入统计。")
        return

    # ---- 败因四分类（这是本面板最有价值的一块）----
    st.markdown("#### 🔍 败因四分类")
    st.markdown("**只有「入场逻辑错」才该去改选股逻辑；「逻辑对、执行错」是卖点问题，"
                "此时改选股逻辑只会把对的信号改坏。**")
    _vc = st_.get('verdict_counts') or {}
    _vc_rows = []
    for _k in ('win', 'lose_rel', 'lose_exec', 'lose_logic'):
        _n = _vc.get(_k, 0)
        if _n:
            _vc_rows.append(f"| {VERDICT_LABEL.get(_k, _k)} | {_n} | {_n / st_['closed'] * 100:.1f}% |")
    if _vc_rows:
        st.markdown("| 分类 | 笔数 | 占比 |\n|---|---:|---:|\n" + "\n".join(_vc_rows))

    # ---- 分桶归因 ----
    st.markdown("#### 🧮 分桶归因")
    st.caption("全部按「入场时就已知」的条件分桶，绝不使用事后才知道的信息。"
               "找的是「哪种入场条件下这套逻辑失效」，用于后续加过滤条件（做减法），"
               "而不是放大看起来有效的信号。")
    _rows = band_attribution_report(mem)
    if not _rows:
        st.caption("暂无可用分桶（可能是入场上下文缺失，或基准数据不足）。")
    else:
        _df = pd.DataFrame(_rows)
        _df = _df.rename(columns={
            "dim": "维度", "bucket": "分桶", "n": "笔数", "win_rate": "胜率%",
            "avg_ret": "平均收益%", "avg_excess": "平均超额%",
            "avg_mfe": "平均最大浮盈%", "avg_mae": "平均最大浮亏%", "enough": "样本够",
        })
        _df["样本够"] = _df["样本够"].map({True: "✅", False: "样本不足"})
        st.dataframe(_df[["维度", "分桶", "笔数", "样本够", "胜率%",
                          "平均收益%", "平均超额%", "平均最大浮盈%", "平均最大浮亏%"]],
                     use_container_width=True, hide_index=True)
        if not any(r['enough'] for r in _rows):
            st.caption(f"当前所有分桶的样本量都不到 {REVIEW_MIN_SAMPLE} 笔 —— "
                       "上面的胜率差异**还只是噪音**，不要据此调整任何参数。")

    # ---- 批次档案 ----
    _allb = batches.get('batches') or {}
    with st.expander(f"📦 批次档案（{len(_allb)} 批）", expanded=False):
        if not _allb:
            st.caption("还没有批次。下次在「🧠 波段记忆 → 手动记入」批量记入时会自动记成一批 —— "
                       "批次是复盘的前提：逐只散着记，事后分不清哪些是同一期选的，也就无法归因。")
        else:
            for _bid in sorted(_allb.keys(), reverse=True):
                _b = _allb[_bid] or {}
                _codes = [str(x) for x in (_b.get('codes') or [])]
                _done = 0
                for _cc in _codes:
                    _nd = mem['stocks'].get(_cc)
                    if isinstance(_nd, dict) and ((_nd.get('outcome') or {}).get('closed')):
                        _done += 1
                st.markdown(f"**{_bid}**　{_b.get('source', '')}｜{len(_codes)} 只｜已结案 {_done} 只"
                            f"　`{' '.join(_codes)}`")

def band_memory_ui():
    """波段记忆面板：记住选过的票、跟踪状态变化、并同步到云端巡检。"""
    st.markdown("---")
    st.header("🧠 波段记忆")
    st.caption("扫描到**波段启动确认**才会自动记在这里（结束预警只更新已在册的股票，"
               "不会新建条目）；记录入选时间、入选价和之后每一次状态变化。")
    st.caption("☁️ 同步到仓库的是**完整记忆**（含备注、入选价、平台高点），不是裁剪版 —— "
               "这样容器重启后手写备注也能找回来。配了 BAND_KEY 时提交上去的是**密文**；"
               "没配就是明文（你的仓库是公开的，请自行取舍）。")

    # ---- 首次进入本会话时静默拉取云端记忆，避免本地文件被容器重启清空后一无所知 ----
    if not st.session_state.get('band_memory_pulled_once'):
        st.session_state.band_memory_pulled_once = True
        ok_pull, msg_pull, merged = band_memory_pull_github()
        if ok_pull and merged is not None:
            before = band_memory_stats(load_band_memory())['total']
            save_band_memory(merged)
            after = band_memory_stats(merged)['total']
            if after != before:
                st.session_state.band_memory_sync_msg = f"{msg_pull}；已并入本地（{before} → {after} 只）"

    mem = load_band_memory()

    # ---- 静默「保底」自动保存到云端 ----
    # 远端还没有 band_watch.json、或远端缺本地某些票时，自动补推一次；其余情况一律不提交
    # （见 band_memory_autosync 的注释：绝不能让网页端与云端巡检互相刷提交）。
    # 每个会话只跑一次；之后任何改动（记入/备注/归档/删除）本来就会各自即时推送。
    if _github_token() and not st.session_state.get('band_memory_autosync_done'):
        st.session_state.band_memory_autosync_done = True
        _auto_ok, _auto_msg = band_memory_autosync(mem)
        if _auto_msg:
            st.session_state.band_memory_sync_msg = _auto_msg

    stats = band_memory_stats(mem)

    if not _github_token():
        st.info("ℹ️ 还没配置 GitHub Token —— 记忆只保存在本页容器里（重启会丢），"
                "云端也不会推送微信。配置方法见下方「☁️ 云端推送」说明。")

    if band_crypto_enabled():
        st.caption("🔒 云端摘要已加密：仓库里的 band_watch.json 是密文，看不到你关注了哪些股票。")
    else:
        st.caption("🔓 云端摘要未加密：仓库里的 band_watch.json 能直接读出股票代码清单，"
                   "想隐藏它见下方「🔒 隐藏股票代码清单」。")

    # ---- 概览 ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("记住的股票", f"{stats['active']} 只", help="不含已标记结束的")
    c2.metric("结束预警", f"{stats['alert']} 只",
              help="顶背离预警 / 跌破支撑 —— 这两个都是**结束**信号，需要处理。"
                   "它们排在最前只因为「最该处理」，不是「更好」。")
    c3.metric("波段启动", f"{stats['entry']} 只",
              help="只统计当前状态正好是「波段启动确认」的票。"
                   "★ 状态判定是短路判断（顶背离 > 跌破支撑 > 启动确认），只显示优先命中的那一条；"
                   "而顶背离本身要求「价格创新高」，所以一只票可以同时满足启动条件与顶背离 —— "
                   "标着「结束预警」的票不代表它没在启动。")
    c4.metric("累计记录", f"{stats['total']} 只", help="含已归档")

    # ---- 本次新增提示 ----
    _newly = st.session_state.pop('band_memory_new', None)
    if _newly:
        st.success(f"✅ 本次扫描新记入 {len(_newly)} 只：{', '.join(_newly)}")

    # ---- 状态变化提醒（这是「提醒我」的核心）----
    changes = st.session_state.get('band_memory_changes') or []
    if changes:
        ended = [c for c in changes if c.get('alert') == 'end']
        started = [c for c in changes if c.get('alert') == 'start']
        if ended:
            st.error("⚠️ 以下股票**波段结束信号**出现，建议优先处理：\n\n" + "\n".join(
                f"- **{c['name']}（{c['code']}）**：{c['from']} → **{c['to']}**"
                f"　现价 {c['price']:.2f}" + (f"　📋 {c['reasons']}" if c.get('reasons') else "")
                for c in ended))
        if started:
            st.success("🚀 以下股票出现**波段启动**：\n\n" + "\n".join(
                f"- **{c['name']}（{c['code']}）**：{c['from']} → **{c['to']}**"
                f"　现价 {c['price']:.2f}" for c in started))
        others = [c for c in changes if not c.get('alert')]
        if others:
            st.info("ℹ️ 另有状态变化（已记录，未推送）：\n\n" + "\n".join(
                f"- **{c['name']}（{c['code']}）**：{c['from']} → **{c['to']}**"
                f"　现价 {c['price']:.2f}" for c in others))

    # ★ 今日推送（手动）：额度 + 候选。放在清单**之前** ——
    #   用户打开这一页通常就是想看"今天要推什么"，不该让他先滚完长名单。
    notify_center_ui(mem)
    # 日报回看紧跟今日推送：两块说的都是「通知」，微信没收到时来这一页找（2026-09-22）
    digest_last_ui()

    if not mem['stocks']:
        st.caption("（还没有记录。点上方按钮扫描一次，或在这里手动记入。）")

    # ---- 操作区 ----
    b1, b2, b3, b4 = st.columns(4)
    if long_button("🔄 刷新全部状态", key="bandmem_refresh", container=b1,
                   use_container_width=True,
                   help="重新拉取记忆里所有股票的最新波段状态，并标出变化"):
        if not mem['stocks']:
            st.warning("记忆里还没有股票。")
        else:
            with st.spinner(f"正在重新分析 {stats['active']} 只股票..."):
                _mem, _changes = band_memory_refresh(mem)
                _pushed_msg = ""
                if _github_token():
                    _ok_p, _msg_p = band_memory_push_github(_mem)
                    _pushed_msg = f"；{_msg_p}"
            st.session_state.band_memory_changes = _changes
            st.session_state.band_memory_sync_msg = f"已刷新，{len(_changes)} 只状态发生变化{_pushed_msg}"
            st.rerun()
    if long_button("☁️ 同步到云端", key="bandmem_push", container=b2,
                   use_container_width=True,
                   help="把记忆提交到 GitHub 仓库，云端巡检脚本据此推送微信"):
        with st.spinner("正在同步..."):
            _ok, _msg = band_memory_push_github(mem)
        st.session_state.band_memory_sync_msg = _msg
        st.rerun()
    if long_button("⬇️ 从云端拉取", key="bandmem_pull", container=b3,
                   use_container_width=True,
                   help="把云端记录（含巡检脚本发现的状态变化）合并到本地"):
        with st.spinner("正在拉取..."):
            _ok, _msg, _merged = band_memory_pull_github()
        if _ok and _merged is not None:
            save_band_memory(_merged)
        st.session_state.band_memory_sync_msg = _msg
        st.rerun()
    b4.download_button("📥 下载备份", use_container_width=True,
                       data=json.dumps(mem, ensure_ascii=False, indent=2),
                       file_name=f"band_memory_{now_cn().strftime('%Y%m%d_%H%M')}.json",
                       mime="application/json", key="bandmem_download")

    if st.session_state.get('band_memory_sync_msg'):
        st.caption(f"☁️ {st.session_state.band_memory_sync_msg}")

    # ---- 清理（2026-09-19 新增）----
    # 修复「全市场扫描把几百只预警灌进记忆」之后，必须给用户一个清掉存量的口子，
    # 否则 460 条只能一只一只点删除。
    _junk, _noted = band_memory_purge_stats(mem)
    _n_alert = band_memory_alert_stats(mem)
    with st.expander(f"🧹 清理记忆（{_junk} 只噪声 / {_n_alert} 只结束信号）", expanded=False):
        st.markdown(f"""
        2026-09-19 之前的版本允许用「跌破支撑 / 顶背离预警」**新建**记忆条目，
        全市场扫描一次就会把几百只跌破 20 日线的股票灌进来。现在规则已改为
        **只有「波段启动确认」能入册**，这里用来清理已经堆下来的存量。

        - 噪声可清理：**{_junk}** 只（入选时不是「波段启动确认」，且你没写过备注）
        - 会保留：**{_noted}** 只（你写过备注，说明是主动关注的）
        - **结束信号条目：{_n_alert}** 只（入选时就是顶背离 / 跌破支撑 —— 旧规则自动灌进来的，
          你从没主动选过它们。这一档**连带备注一起删**。）
        - 正常入册的（启动确认）一律不动
        """)
        _pend = st.session_state.get('bandmem_purge_pending')
        if _pend == 'junk':
            st.warning(f"确认删除这 {_junk} 只？不可恢复，建议先点上方「📥 下载备份」留底。")
        elif _pend == 'alert':
            st.warning(f"确认删除这 **{_n_alert} 只结束信号条目**（顶背离 / 跌破支撑）？"
                       "**带备注的也会一起删**，不可恢复 —— 建议先点上方「📥 下载备份」留底。")
        elif _pend == 'all':
            st.error("确认**清空全部记忆**？所有备注与轨迹都会一起删除，不可恢复。")
        if _pend:
            _k1, _k2 = st.columns(2)
            if _k1.button("✅ 确认", key="bandmem_purge_ok", use_container_width=True):
                _mode = ('all' if _pend == 'all' else
                         'alert' if _pend == 'alert' else 'never_started')
                _mem2, _n = band_memory_purge(mem, _mode)
                save_band_memory(_mem2)
                _pmsg = ""
                if _github_token():
                    # merge_remote=False：不能用远端摘要反向合并，否则刚清掉的条目会被拉回来
                    _, _pmsg = band_memory_push_github(_mem2, merge_remote=False)
                st.session_state['bandmem_purge_pending'] = None
                st.session_state.band_memory_sync_msg = (
                    f"已清理 {_n} 条{('；' + _pmsg) if _pmsg else ''}")
                st.rerun()
            if _k2.button("✖️ 取消", key="bandmem_purge_cancel", use_container_width=True):
                st.session_state['bandmem_purge_pending'] = None
                st.rerun()
        else:
            _b1, _b2, _b3 = st.columns(3)
            if _b1.button(f"🧹 清理这 {_junk} 只", key="bandmem_purge_junk",
                          use_container_width=True, disabled=(_junk == 0),
                          help="删掉「入选时不是波段启动确认、且你没写过备注」的条目"):
                st.session_state['bandmem_purge_pending'] = 'junk'
                st.rerun()
            if _b2.button(f"⚠️ 清结束信号（{_n_alert}）", key="bandmem_purge_alert",
                          use_container_width=True, disabled=(_n_alert == 0),
                          help="删掉全部「入选时就是顶背离 / 跌破支撑」的条目 —— 带备注的也会删。"
                               "这些是旧规则自动灌进来的，你从没主动选过它们。"):
                st.session_state['bandmem_purge_pending'] = 'alert'
                st.rerun()
            if _b3.button("🗑️ 清空全部记忆", key="bandmem_purge_all", use_container_width=True):
                st.session_state['bandmem_purge_pending'] = 'all'
                st.rerun()

    with st.expander("📤 从备份恢复 / 手动记入代码", expanded=False):
        up = st.file_uploader("上传之前下载的 band_memory.json（会与当前记录合并）",
                              type=["json"], key="bandmem_upload")
        if up is not None:
            try:
                remote = json.loads(up.getvalue().decode("utf-8", errors="replace"))
                if isinstance(remote, dict) and isinstance(remote.get("stocks"), dict):
                    save_band_memory(band_memory_merge(mem, remote))
                    st.success(f"已合并导入（{len(remote.get('stocks', {}))} 只）")
                    st.rerun()
                else:
                    st.error("文件结构不对，应该是由本页「下载备份」导出的 JSON。")
            except Exception as e:
                _log("band_memory_ui/upload", e)
                st.error(f"解析失败：{e}")
        st.caption("—— 手动记入（自动入册只收「波段启动确认」，其他状态想跟踪就手动加）——")
        with st.form("bandmem_manual_form", clear_on_submit=True):
            manual = st.text_input("股票代码（多个用空格/逗号分隔）", placeholder="例如: 600176,000001")
            if st.form_submit_button("➕ 记入并分析", use_container_width=True) and manual.strip():
                codes = [c.strip() for c in re.split(r'[,，\s]+', manual) if c.strip()]
                codes = [c for c in codes if len(c) == 6 and c.isdigit() and not c.startswith(EXCLUDE_PREFIXES)]
                if not codes:
                    st.warning("没有有效的 6 位代码（科创/创业板/北交所会被排除）。")
                else:
                    rows = []
                    for code in codes:
                        try:
                            r = _band_evaluate(code, get_stock_name(code))
                            if r:
                                rows.append(r)
                            else:
                                st.warning(f"{code} 日线数据不足，未能记入。")
                        except Exception as e:
                            _log(f"band_memory_ui/manual/{code}", e)
                            st.warning(f"{code} 分析失败：{e}")
                    if rows:
                        # 入场时的大盘环境：归因要用，必须在「入场那一刻」记下来，事后补不了
                        _bench = _bench_history()
                        _above = _bench_above_ma20(_bench)
                        added = band_memory_record(mem, rows, source="手动记入",
                                                   bench_above=_above)
                        # 手动记入的票不分状态一律收下（用户明确想跟踪）
                        for r in rows:
                            code = str(r['Code'])
                            if code not in mem['stocks']:
                                mem['stocks'][code] = {
                                    "code": code, "name": r.get('Name') or code,
                                    "added_at": now_cn_str(), "added_price": r.get('Price', 0.0),
                                    "added_status": r.get('Status'), "added_source": "手动记入",
                                    "batch_id": "",
                                    "entry_context": _band_entry_context(r, _above),
                                    "snapshot": {}, "note": "", "closed": False,
                                    "alerts": {}, "history": [],
                                }
                                _band_memory_apply(mem['stocks'][code], r, event="手动记入")
                                added.append(code)
                        # 复盘用：这一次新增的票记成一个批次（没有新增就不建，避免重复批次）
                        if added:
                            _bs = load_band_batches()
                            _bid = band_batch_create(_bs, added, source="手动记入",
                                                     note="面板手动批量记入")
                            save_band_batches(_bs)
                            for _c in added:
                                _n = mem['stocks'].get(_c)
                                if isinstance(_n, dict):
                                    _n['batch_id'] = _bid
                        save_band_memory(mem)
                        st.session_state.band_memory_sync_msg = f"已记入 {len(added)} 只"
                        band_memory_push_github(mem)
                        st.rerun()

    if not mem['stocks']:
        return

    # ---- 记忆清单 ----
    st.markdown("#### 📋 记忆清单")
    # ★ 2026-09-22 换序（用户要求）：「顺序排列要按它们的前景来排列，越靠前的越有前景」。
    #   旧口径是「状态层级 → 入册时间」，于是刚启动、上方空间大的票会被压在若干只预警票下面。
    #   新口径：归档的沉底，其余**一律按前景分降序**（同分再比入册时间，保证顺序稳定可复现）。
    #   ★ 预警票不会因为排在后面就漏看 —— 「只展开新出现的预警」（band_alert_need_expand）
    #     与红框仍然生效。**提醒靠的是标记，不是位置。**
    order = sorted(mem['stocks'].values(),
                   key=lambda n: (bool(n.get('closed')),
                                  -float(n.get('score') or 0),
                                  str(n.get('added_at', ''))))
    # 批量多选处理（归档 / 删除）—— 不用再一只只展开去找删除按钮
    _band_bulk_manage_ui(mem, order)

    for node in order:
        code = node.get('code') or '?'
        cur = node.get('status') or '未知'
        col, icon = _band_status_badge(cur)
        add_col, add_icon = _band_status_badge(node.get('added_status'))
        changed = bool(node.get('added_status')) and node.get('added_status') != cur
        stage_txt = band_levels_text(node)
        # 本轮启动标注（2026-09-22）：让「启动确认」这个能持续很多天的标签带上时间 ——
        # 一眼看出它**早就启动**了、还是今天刚启动（见 band_run_text 的说明）。
        _run_txt = band_run_text(node)
        title = (f"{icon} {node.get('name')}（{code}）　{cur}"
                 + ("　⚠️ 状态已变化" if changed else "")
                 + ("　🗄️ 已归档" if node.get('closed') else "")
                 + (f"　·　{stage_txt}" if stage_txt else "")
                 + (f"　·　{_run_txt}" if _run_txt else ""))
        # ★ 「只展开新出现的预警」—— 判定抽在 band_alert_need_expand 里（可单测），
        #   这里只负责接线。别把条件再内联回来：AppTest 断言不了展开状态。
        need_show = band_alert_need_expand(node)
        with st.expander(title, expanded=need_show):
            # ★ 2026-09-21 重写：旧版是「启动价 → 目标价 + 阶段进度」。用户反馈
            #   「目标为什么这么接近启动价格…启动价格都是现价」，根因见 band_breakout_pivot。
            #   现在三个位各自说清是什么，而且**不再出现「目标价」**——
            #   创新高的票上方没有历史阻力，编一个目标出来就是让人去挂单。
            _start = band_start_price(node)
            _pivot = band_breakout_pivot(node)
            _defense = band_defense_price(node)
            _cur = _band_num(node.get('price'))
            # 入选价是不是"推算"来的：容器重启后本地不存 added_price，只能从入册轨迹近似。
            # ★ 只有**真的推出了值**才敢叫「推算」：added_price 和轨迹都没有时（例如复盘用的
            #   历史样本、或数据被清过），入选价是 0、界面显示「—」，这时再标「推算」就是骗人。
            _start_guess = (_band_num(node.get('added_price')) <= 0 and _start > 0)
            _start_help = ("容器重启后本地不保存入选价，这里用**入册那条轨迹的价格**近似；"
                           "误差通常极小，但它不是原始记录。" if _start_guess else
                           "入册那天的现价。**自动入册的票它必然≈现价** —— 因为「波段启动确认」"
                           "就是在当天创新高时命中的，这不是记录错误，是入选机制决定的。")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("当前状态", cur)
            m2.metric("现价", _fmt_price(_cur))
            m3.metric("入选价" + ("（推算）" if _start_guess else ""),
                      _fmt_price(_start), help=_start_help,
                      delta=(f"{(_cur / _start - 1) * 100:+.1f}% 自入选"
                             if (_start > 0 and _cur > 0) else None),
                      delta_color="inverse")
            m4.metric("突破位", _fmt_price(_pivot),
                      delta=(f"现价 {(_cur / _pivot - 1) * 100:+.1f}%"
                             if (_pivot > 0 and _cur > 0) else None),
                      delta_color="off",
                      help="**突破前**的 60 日平台上沿（**不含当天**）。现价在它上方是正常突破；"
                           "回踩到它附近且不破，才算这次突破有效。")
            m5.metric("防守位（20 日线）", _fmt_price(_defense),
                      delta=(f"现价 {(_cur / _defense - 1) * 100:+.1f}%"
                             if (_defense > 0 and _cur > 0) else None),
                      delta_color="off",
                      help="与状态判定的「跌破支撑」用的是同一个数：现价跌到它下方就转「跌破支撑」。")
            _miss = []
            if _start <= 0:
                _miss.append("入选价没有记录（容器重启后本地信息会丢，"
                             "点上面的「🔄 刷新全部状态」会按轨迹补算）")
            if _pivot <= 0:
                _miss.append("突破位还没算过（点「🔄 刷新全部状态」会补上）")
            if _defense <= 0:
                _miss.append("防守位暂缺（还没刷新过，拿不到 20 日线）")
            if _miss:
                st.caption("暂缺：" + "；".join(_miss) + "。")
            _ph = _band_num(node.get('platform_high'))
            if _ph > 0:
                st.caption(f"当前平台高点（近 60 日最高价，**含当天**）{_fmt_price(_ph)}"
                           "　—— 创新高的票它会≈现价，这是入选机制决定的，不是数据错了。")
            st.caption("三个位都是按固定规则算出来的**参考位，不是预测**。")
            st.caption(
                f"入选时间：{node.get('added_at') or '—'}"
                f"　|　入选时：{add_icon} {node.get('added_status') or '—'}"
                f"　|　来源：{node.get('added_source') or '—'}"
                f"　|　最近检查：{node.get('last_check') or '未检查'}"
                f"　|　20日线：{_fmt_price(node.get('ma20'))}")

            # ★ 2026-09-21：操作行（备注 / 归档 / 删除）**上提到卡片顶部**。
            #   原来它在卡片最底部，上面还压着最多 10 行「状态轨迹」—— 想删一只票要先展开、
            #   再往下滚一屏才找得到删除按钮，用户反馈「这个页面我想删除怎么这么难」即源于此。
            note_key = f"bandmem_note_{code}"
            note = st.text_input("备注（买入价 / 仓位 / 想法，本地与云端都会保存）",
                                 value=node.get('note') or '', key=note_key)
            n1, n2, n3 = st.columns([1, 1, 2])
            if n1.button("💾 保存备注", use_container_width=True, key=f"bandmem_savenote_{code}"):
                node['note'] = note
                save_band_memory(mem)
                band_memory_push_github(mem)
                st.session_state.band_memory_sync_msg = f"{node.get('name')} 备注已保存"
                st.rerun()
            if n2.button("🗄️ 归档" if not node.get('closed') else "♻️ 取消归档",
                         use_container_width=True, key=f"bandmem_close_{code}",
                         help="归档后不再参与状态刷新和云端推送，但记录保留"):
                node['closed'] = not node.get('closed')
                save_band_memory(mem)
                band_memory_push_github(mem)
                st.rerun()
            if n3.button("🗑️ 从记忆中删除", use_container_width=True, key=f"bandmem_del_{code}",
                         help="从本地和云端清单里一起移除，之后不再监控这只票"):
                # ★★ 这里必须是 merge_remote=False（同「🧹 清理记忆」那处）。
                #   默认 True 会先拉云端那份再合并，而 band_memory_merge_digest 对
                #   「云端有、本地没有」的条目是**整节点照抄** → 刚删掉的票立刻被复活，
                #   还顺手 _save_json 写回本地 → 用户看到的就是「点了删除没反应」（2026-09-20 实测）。
                _del_name = node.get('name') or code
                mem['stocks'].pop(code, None)
                save_band_memory(mem)
                _del_ok, _del_msg = band_memory_push_github(mem, merge_remote=False)
                if _del_ok:
                    st.session_state.band_memory_sync_msg = f"已删除 {_del_name}（{code}）"
                else:
                    # ★ 同步失败就别报「已删除」：云端那份还在，下次合并会把它拉回来。
                    st.session_state.band_memory_sync_msg = (
                        f"⚠️ 已在本地删除 {_del_name}（{code}），但云端同步失败（{_del_msg}）——"
                        "下次同步时它可能被云端那份合并回来，请检查 GITHUB_TOKEN")
                st.rerun()

            if need_show:
                if st.button("👁️ 我已看过这条预警（以后不再自动展开）",
                             key=f"bandmem_ack_{code}"):
                    # ★ 2026-09-20 起 alert_ack 也随完整镜像同步，容器重启后不会再重复展开。
                    #   这里仍然**不主动推送**：点「我已看过」不是状态变化，
                    #   没必要为它单独提交一次。
                    node['alert_ack'] = str(node.get('status_ts') or '')
                    save_band_memory(mem)
                    st.session_state.band_memory_sync_msg = (
                        f"{node.get('name')}：已标记看过，之后折叠；状态再变化会重新展开提醒")
                    st.rerun()
            if node.get('reasons'):
                st.caption(f"📋 最近依据：{node['reasons']}")

            # 轨迹：一眼看清「什么时候变成什么的」
            hist = node.get('history') or []
            if hist:
                st.markdown("**状态轨迹**（新→旧）")
                for h in reversed(hist[-10:]):
                    _hc, _hi = _band_status_badge(h.get('status'))
                    st.markdown(
                        f"- `{h.get('ts', '')}`　{_hi} **{h.get('status')}**"
                        f"　价格 {_fmt_price(h.get('price'))}"
                        f"　<span style='color:{_hc};font-size:12px;'>{h.get('event', '')}</span>",
                        unsafe_allow_html=True)

    with st.expander("☁️ 云端推送（微信）怎么配", expanded=False):
        st.markdown("""
        记忆里「代码 + 当前状态」的摘要会被提交到仓库，GitHub Actions 上的巡检脚本
        每 5 分钟读一次，发现状态**变差**（→ 顶背离预警 / 跌破支撑）就推送微信，
        你关掉本页也能收到。

        **同步说明**：提交到仓库的是**完整记忆** —— 代码、名称、当前状态、轨迹，
        以及你写的备注、入选价、平台高点，**不再做字段裁剪**（这样容器重启后备注不会丢）。
        配了 `BAND_KEY` 时提交的是**密文**，仓库公开也读不出内容；
        没配 `BAND_KEY` 提交的就是明文，请自行取舍。

        ⚠️ **不要直接把仓库改成 Private**。本项目的定时巡检（每 5 分钟一次）
        加保活（每 10 分钟一次），合计约 **7800 分钟/月**，而 private 仓库的免费额度
        只有 **2000 分钟/月**；额度烧穿后 GitHub 会**静默停掉定时任务**，
        你就再也收不到微信提醒了 —— 这比「代码清单公开」更危险。
        public 仓库的 Actions 完全免费且不计时，保持 public 反而更可靠。

        **需要你做一次配置**（只需一次）：

        1. 打开 GitHub → 右上头像 → Settings → Developer settings →
           Personal access tokens → **Fine-grained tokens** → Generate new token。
        2. Repository access 选 **Only select repositories** → 选 `stock-t-terminal`。
        3. Permissions → Repository permissions → **Contents** 设为 **Read and write**。
        4. 生成后复制 token（形如 `github_pat_...`）。
        5. 打开 Streamlit Cloud → 你的 app → Settings → Secrets，加上一行：

        ```toml
        GITHUB_TOKEN = "github_pat_xxxxx"
        ```

        6. 回到本页，点一次「☁️ 同步到云端」，看到「已同步到仓库」即成功。

        没配也能用：记忆照常记录、页面照常提醒，只是关掉页面后收不到微信。
        也可以改用 GitHub Secrets 里的 `BAND_WATCHLIST`（逗号分隔代码）作为替代方案，
        那样连代码列表都不需要提交。
        """)

        st.markdown("---")
        st.markdown("#### 🔒 隐藏股票代码清单（可选，推荐）")
        if band_crypto_enabled():
            st.success("当前状态：**已开启加密** —— 仓库里的 band_watch.json 是密文。")
        else:
            st.warning("当前状态：**未加密** —— 仓库里的 band_watch.json 能直接读出你关注了哪些股票。")

        st.markdown("""
        上面只解决了「备注和买入价」不外泄，但 `band_watch.json` 里仍有**股票代码清单**。
        想连这份清单也藏起来，就在两端各加一个**完全相同**的密钥 `BAND_KEY`：

        1. 点下面的按钮生成一个密钥。
        2. **Streamlit Cloud** → 你的 app → Settings → Secrets，加一行 `BAND_KEY = "密钥"`。
        3. **GitHub 仓库** → Settings → Secrets and variables → Actions →
           New repository secret，名字填 `BAND_KEY`，值填**同一串**。
        4. 保存后回到本页，点一次「☁️ 同步到云端」，仓库里的文件就变成密文了。

        ⚠️ 两边必须是**同一串**密钥。少配一边或填错，摘要就解不开 ——
        网页端会明确提示「解密失败」，巡检侧会在 Actions 日志里打印醒目告警
        （这时波段监控实际不工作，不会静默假装正常）。
        另外，开启加密之后**不要把某一端的 BAND_KEY 删掉**：同步逻辑会拒绝用明文去覆盖密文，
        宁可报错也不会泄露。
        """)

        if st.button("🔑 生成一个新的加密密钥", key="band_key_gen"):
            try:
                st.session_state["band_key_new"] = generate_band_key()
            except Exception as e:
                st.error(f"生成失败（缺少 cryptography 依赖？）：{e}")
        if st.session_state.get("band_key_new"):
            st.code(st.session_state["band_key_new"], language="text")
            st.caption("把上面这串原样复制到两处 Secrets（Streamlit 与 GitHub Actions），一字都不能差。")


def _add_band_to_memory(r, source="手动加入"):
    """把一张选股卡片写进「波段记忆」，并**立刻**同步到云端。返回给用户看的提示语。

    ★ 为什么点一下就要 push，不能只写本地：用户按这个按钮的全部意义是
      「让云端巡检开始盯它、出现结束信号推我微信」。只写本地的话，要等下一次
      别的原因触发同步才生效 —— 对"及时提醒"来说那就是失效。
    """
    try:
        mem = load_band_memory()
        is_new, code = band_memory_add_manual(mem, r, source=source)
        if not code:
            return "❌ 代码为空，未加入记忆。"
        save_band_memory(mem)
        _ok, _m = band_memory_push_github(mem)
        _name = r.get('Name') or code
        _head = (f"✅ 已加入记忆并开始监控：**{_name}（{code}）**" if is_new
                 else f"✅ 已在跟踪清单里，已刷新状态：**{_name}（{code}）**")
        return _head + ("　云端巡检已接手，出现顶背离 / 跌破支撑会推微信。"
                        if _ok else f"　（本地已记住；云端同步未完成：{_m}）")
    except Exception as e:
        _log("_add_band_to_memory", e)
        return f"❌ 加入记忆失败：{type(e).__name__}: {str(e)[:120]}"


def _scan_drop_row(code):
    """把一只票从**本次扫描结果**里去掉（只动 session_state，不碰记忆与文件）。

    ★ 这是「加一个可以删除的功能」里**无副作用**的一半：重扫还会出现，
      所以不需要二次确认。真正不可逆的是 `_scan_delete_from_memory`。
    """
    df = st.session_state.get('scan_results')
    if df is None or getattr(df, 'empty', True):
        return 0
    keep = df[df['Code'].astype(str) != str(code)]
    n = int(len(df) - len(keep))
    if n:
        keep = keep.reset_index(drop=True)
        keep.index = keep.index + 1
        st.session_state.scan_results = keep
    return n


def _scan_delete_from_memory(code):
    """把一只票从「波段记忆」（本地文件 + 云端）里删掉，返回给用户看的提示语。

    ★★ 推送必须带 merge_remote=False：默认 True 会先拉云端那份再合并，而合并对
      「云端有、本地没有」是**整节点照抄** → 刚删掉的票立刻被复活、还顺手写回本地
      → 用户看到的就是「点了删除没反应」（2026-09-20 实测）。同一条纪律在
      「🧠 波段记忆」的单只删除与「🧹 清理记忆」里都已存在，这里不能漏。
    """
    code = str(code or '').strip()
    if not code:
        return "❌ 代码为空，未删除。"
    try:
        mem = load_band_memory()
        node = (mem.get('stocks') or {}).pop(code, None)
        if not isinstance(node, dict):
            return f"⚠️ {code} 不在跟踪清单里（可能已被删除）。"
        _name = node.get('name') or code
        save_band_memory(mem)
        _ok, _msg = band_memory_push_github(mem, merge_remote=False)
        if _ok:
            return f"🗑️ 已从记忆中删除 **{_name}（{code}）**，云端巡检不再盯它。"
        # 同步失败就别报「已删除」：云端那份还在，下次合并会把它拉回来。
        return (f"⚠️ 已在本地删除 {_name}（{code}），但云端同步失败（{_msg}）——"
                "下次同步时它可能被云端那份合并回来，请检查 GITHUB_TOKEN。")
    except Exception as e:
        _log("_scan_delete_from_memory", e)
        return f"❌ 删除失败：{type(e).__name__}: {str(e)[:120]}"


def _render_band_card(r, tracked=None):
    """渲染一张波段选股结果卡片。

    ★ 已在「跟踪清单」（波段记忆）里的票只打标记，**不再重复当成一条新发现** ——
      原先扫描结果里出现一次、记忆清单里又出现一次，就是「两块内容看着重复」的来源。
    ★ 2026-09-21 加删除入口（用户原话「加一个可以删除的功能，因为这些我都没办法自己删除」）：
      - 已跟踪 → 「🗑️ 从记忆删除」：不可逆（本地 + 云端一起删），点击后**就地**出现一次确认；
      - 未跟踪 → 「🗑️ 移除本行」：只从本次扫描结果里去掉，重扫会回来，故不设确认。
    ★ 2026-09-21（晚）按用户要求与「🧠 波段记忆」**同口径**：价格行改成
      现价（= 加入记忆后的入选价）/ 突破位 / 防守位（20 日线）三段，
      并**去掉「平台上沿」** —— 它（含当天）与突破位（不含当天）几乎同值，
      两个几乎一样的数字并排，正是用户反馈「目标价贴着启动价」的误导来源。
      数据没丢：记忆节点里仍有 `platform_high`，这里只是不再展示它。
    """
    tracked = tracked or set()
    chg_color = "#ff4b4b" if r['ChangePct'] >= 0 else "#00cc66"
    _tag = ("　<span style='color:#89b4fa;font-size:12px;'>✅ 已在跟踪清单</span>"
            if str(r['Code']) in tracked else "")
    # ★ 突破位可能缺失或为 0（旧扫描结果 / 手写夹具）→ 一律降级成「—」。
    #   **绝不让展示串 KeyError 把整页打崩** —— 2026-09-21 实测：卡片里直接下标取这个字段，
    #   缺它的调用路径（选股页 UI 用例）整页白屏报错（A2「没有 st.error」当场变红）。
    #   展示层对缺字段的正确姿态是"少显示一段"，不是"炸掉"。
    #   ⚠️ 上面这句注释刻意不写出那个下标写法，否则它自己就会让源码守卫变红
    #      （"被守的字符串同时出现在注释里"是这套测试明确的坑，见 test_picker_ui A6c）。
    _pivot_txt = _fmt_price(_band_num(r.get('BreakoutPivot')))
    # ★ 防守位与「🧠 波段记忆」同口径：就是 20 日线（同一列 MA20），缺值降级成「—」。
    #   和突破位一样，**绝不在展示串里直接下标取字段** —— 缺字段的调用路径不能打崩整页。
    _def_txt = _fmt_price(_band_num(r.get('MA20')))
    # ★ 本轮启动标注 + 前景分（2026-09-22）。用户在选股页看到「已涨很多还写启动确认」，
    #   就是因为这个标签不带时间 —— 判定只看**当日**是否突破+放量，一只持续创新高的票
    #   每天都在满足它。这里把「哪天启动的、至今涨了多少」印在卡片上。
    #   ★ 全部走 .get + 降级：老扫描结果与手写夹具都没有这几个字段，
    #     下标硬取会让"缺字段的那条调用路径"整页白屏（2026-09-21 实测踩过）。
    _run_info = band_run_info(r)
    _run_txt = band_run_text(r)
    _run_line = (_run_txt if _run_txt
                 else "本轮启动：—（最后一根 K 线不在启动区，或历史不足 62 根）")
    _run_color = ("#f9e2af" if (_run_info['days'] > 0
                                and _run_info['gain'] >= BAND_RUN_FAR_PCT) else "#a6e3a1")
    _score_v = r.get('Score')
    try:
        _score_line = (f"前景分 <b>{float(_score_v):.0f}</b>"
                       if _score_v is not None else "前景分 —")
    except (TypeError, ValueError):
        _score_line = "前景分 —"
    st.markdown(f"""
                <div style="background:#1e1e2e; border-radius:10px; padding:14px 18px; margin-bottom:10px; border-left:4px solid {r['StatusColor']};">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                        <div><span style="font-size:18px; font-weight:bold; color:#f0f2f6;">{r['Name']}</span>
                        <span style="color:#89b4fa; font-size:14px; margin-left:8px;">({r['Code']})</span>
                        <span style="color:{chg_color}; font-size:14px; margin-left:10px;">{r['ChangePct']:+.2f}%</span>{_tag}</div>
                        <div style="text-align:right;"><span style="color:{r['StatusColor']}; font-size:16px; font-weight:bold;">{r['Status']}</span>
                        <div style="color:#c9d1d9; font-size:12px; margin-top:2px;">{_score_line}</div></div>
                    </div>
                    <div style="margin-top:8px; color:#c9d1d9; font-size:13px; line-height:1.8;">
                        <span style="color:#89b4fa;">现价:</span> {r['Price']:.2f}（加入记忆后即「入选价」） | <span style="color:#89b4fa;">突破位:</span> {_pivot_txt} | <span style="color:#89b4fa;">防守位:</span> {_def_txt}（20 日线） | <span style="color:#89b4fa;">量比:</span> {r['VolRatio']:.1f} | <span style="color:#89b4fa;">250日分位:</span> {r['Position250']:.0f}%
                    </div>
                    <div style="margin-top:6px; color:{_run_color}; font-size:13px;">🚀 {_run_line}</div>
                    <div style="margin-top:6px; color:#f9e2af; font-size:13px;">📋 {r['Reasons']}</div>
                </div>
                """, unsafe_allow_html=True)
    # ---- 操作行：一键进「波段记忆」 / 删除 ----
    # ★ 按钮只能放在卡片**下方**：Streamlit 的控件没法塞进上面那段 raw HTML 里。
    _code = str(r['Code'])
    _lot = float(r['Price'] or 0) * 100
    _c_act, _c_del, _c_hint = st.columns([1.15, 1.15, 3.0])
    with _c_act:
        if _code in tracked:
            st.caption("✅ 已在跟踪清单")
        elif st.button("➕ 加入记忆", key=f"memo_add_{_code}", use_container_width=True,
                       help="放进「🧠 波段记忆」全程监控：云端巡检每 5 分钟看一次，"
                            "出现顶背离 / 跌破支撑会推微信提醒。已买入的票点一下就行。"):
            st.session_state.band_memory_manual_msg = _add_band_to_memory(r)
            st.rerun()
    with _c_del:
        # ★ 就地二次确认：`scan_del_pending` 是**非 widget** 的 state，
        #   普通按钮里直接赋值合法（同「🧠 波段记忆」批量删除的写法）。
        _del_pend = st.session_state.get('scan_del_pending')
        if _code in tracked:
            if _del_pend == _code:
                _d1, _d2 = st.columns(2)
                if _d1.button("✅ 确认", key=f"scan_delok_{_code}", use_container_width=True,
                              help="确认从本地与云端清单一起移除，不可撤销"):
                    st.session_state['scan_del_pending'] = None
                    st.session_state.band_memory_manual_msg = _scan_delete_from_memory(_code)
                    st.rerun()
                if _d2.button("✖️ 取消", key=f"scan_delno_{_code}", use_container_width=True):
                    st.session_state['scan_del_pending'] = None
                    st.rerun()
            elif st.button("🗑️ 从记忆删除", key=f"scan_memdel_{_code}", use_container_width=True,
                           help="从「🧠 波段记忆」的本地清单与云端一起移除，之后不再监控这只票。"
                                "不可撤销，点一下后会再确认一次。"):
                st.session_state['scan_del_pending'] = _code
                st.rerun()
        elif st.button("🗑️ 移除本行", key=f"scan_drop_{_code}", use_container_width=True,
                       help="只把这一行从**本次扫描结果**里去掉；下次重新扫描它还会出现。"):
            _scan_drop_row(_code)
            st.rerun()
    with _c_hint:
        if _code in tracked:
            st.caption(f"1 手（100 股）≈ ¥{_lot:,.0f}　·　可在「🧠 波段记忆」页签里备注 / 删除。")
        else:
            # 买不买得起一眼看得到 —— 用户反馈过"有的票 1 手都买不起"
            st.caption(f"1 手（100 股）≈ ¥{_lot:,.0f}")


def ai_band_picker_ui():
    st.markdown("---")
    st.header("🌊 波段做T选股")
    st.caption("**只挑「刚启动」的票**：突破 60 日整理平台 + 放量站上 20 日线"
               "（排除科创/创业板/北交所/ST）。看中了就点卡片下方的「➕ 加入记忆」，"
               "之后由「🧠 波段记忆」全程盯着，出现顶背离 / 跌破支撑会推你微信。"
               "**结束信号（顶背离 / 跌破支撑）不会列在这一页** —— 它只对跟踪清单里的票有意义；"
               "不想看的票可以直接点卡片下方「🗑️ 移除本行」删掉。")

    with st.expander("📖 选股逻辑说明", expanded=False):
        st.markdown("""
        **波段开始条件**：
        1. 股价突破近 60 日整理平台（收盘价接近或创 60 日新高）。
        2. 成交量明显放大（5 日均量 ≥ 20 日均量 1.5 倍，或当日量 ≥ 20 日均量 1.5 倍）。
        3. 站上 20 日线，20 日线在 60 日线上方更佳。
        4. MACD 金叉或红柱，资金流入加分。

        **波段结束提醒**（只在「🧠 波段记忆」里生效 —— 选股页不再列这类票）：
        - 顶背离：股价创新高，但 MACD 未创新高（且背离幅度 ≥30%，见下）。
        - 跌破支撑：收盘价跌破 20 日线。

        **顶背离为什么要求 ≥30% 的 MACD 落差**：回测 27,352 次预警事件后发现，
        只看「MACD 有没有变低」会把大量擦边情况标成预警（实测有只高 0.27% 的新高），
        而按 MACD 的落差收紧是唯一能让预警变准的方向；顺带一提，
        **按「新高幅度」收紧是反的**（新高幅度越大，趋势反而越不容易坏）。
        另外要清楚：顶背离不是「要跌」的信号，而是「趋势更容易坏」的信号 ——
        背离组与「创新高但无背离」组之后 10 天平均收益几乎一样，
        但背离组 10 天内跌破 20 日线的比例是 68%，无背离组只有 37%。
        所以它的用法是「别追高 / 准备止盈」，不是清仓。

        ⚠️ 本工具仅为量化初筛，不构成投资建议。
        """)

    # 扫描范围与手动股票管理
    if 'band_manual_codes' not in st.session_state:
        st.session_state.band_manual_codes = load_band_manual()

    scan_scope = st.radio("扫描范围", ["全市场扫描", "仅手动自选", "指定板块"], horizontal=True, key="band_scan_scope")

    custom_codes = None
    if scan_scope == "仅手动自选":
        with st.form("band_manual_form", clear_on_submit=True):
            manual_input = st.text_input("手动添加股票代码（多个用空格/逗号分隔）", placeholder="例如: 600176,000001,512480")
            submitted = st.form_submit_button("➕ 添加并保存", use_container_width=True)
            if submitted and manual_input.strip():
                new_codes = [c.strip() for c in re.split(r'[,，\s]+', manual_input) if c.strip()]
                valid_codes = [c for c in new_codes if len(c) == 6 and c.isdigit() and not c.startswith(EXCLUDE_PREFIXES)]
                added = []
                for c in valid_codes:
                    if c not in st.session_state.band_manual_codes:
                        st.session_state.band_manual_codes.append(c); added.append(c)
                if added:
                    save_band_manual(st.session_state.band_manual_codes)
                    st.success(f"已添加 {len(added)} 只：{', '.join(added)}")
                else:
                    st.warning("没有新的有效代码（请检查是否为6位、非科创/创业板/北交所）")
        if st.session_state.band_manual_codes:
            st.caption(f"当前手动列表（{len(st.session_state.band_manual_codes)} 只）：{', '.join(st.session_state.band_manual_codes)}")
            col_clear, col_remove = st.columns([1, 1])
            with col_clear:
                if st.button("🧹 清空手动列表", use_container_width=True, key="clear_band_manual"):
                    st.session_state.band_manual_codes = []; save_band_manual([]); st.rerun()
            with col_remove:
                if st.button("❌ 删除最后一只", use_container_width=True, key="pop_band_manual"):
                    if st.session_state.band_manual_codes:
                        st.session_state.band_manual_codes.pop(); save_band_manual(st.session_state.band_manual_codes); st.rerun()
        custom_codes = st.session_state.band_manual_codes
    elif scan_scope == "指定板块":
        board_name = st.selectbox("选择板块", list(BAND_BOARD_MAP.keys()), key="band_board_select")
        board_codes = BAND_BOARD_MAP.get(board_name, [])
        if board_codes:
            st.caption(f"已加载「{board_name}」板块 {len(board_codes)} 只成分股（预定义列表）")
            custom_codes = board_codes
        else:
            st.warning(f"未能加载「{board_name}」板块成分股，将回退到全市场扫描")

    # ---- 价格上限（买得起才看）----
    # 为什么放在选股页而不是设置页：它是**每次选股都要调的参数**，不是全局设置。
    # 为什么值得持久化：存在本地 config.json（已 gitignore），设一次就记住。
    if 'band_price_cap' not in st.session_state:
        try:
            st.session_state.band_price_cap = float(load_config().get('band_price_cap') or 0.0)
        except Exception as _e:
            _log("ai_band_picker_ui/price_cap_load", _e)
            st.session_state.band_price_cap = 0.0

    def _save_price_cap():
        try:
            save_config({**load_config(), 'band_price_cap': float(st.session_state.band_price_cap)})
        except Exception as _e:
            _log("ai_band_picker_ui/price_cap_save", _e)

    _c_cap, _c_cap_hint = st.columns([1, 2.6])
    with _c_cap:
        _price_cap = st.number_input("价格上限（元/股，0 = 不限）", min_value=0.0, max_value=9999.0,
                                     step=1.0, key="band_price_cap", on_change=_save_price_cap,
                                     help="只扫不超过这个价格的票。A 股 1 手 = 100 股，"
                                          "上限 20 元 ⇒ 1 手最多约 ¥2000。")
    with _c_cap_hint:
        if _price_cap:
            st.caption(f"已开启：只看 ≤ {_price_cap:.2f} 元的票（1 手约 ≤ ¥{_price_cap * 100:,.0f}）。"
                       "过滤掉多少只会写进下面的「扫描漏斗」。")
        else:
            st.caption("未设上限。设一个能过滤掉买不起的票（例如 20 元 ⇒ 1 手最多约 ¥2000）。")

    scan_label = "🔍 扫描波段启动股"
    if scan_scope == "仅手动自选":
        scan_label = "🔍 扫描手动自选"
    elif scan_scope == "指定板块":
        scan_label = "🔍 扫描指定板块"
    if long_button(scan_label, key="scan_stocks", type="primary", use_container_width=True):
        try:
            with st.spinner("正在拉取行情并分析波段状态..."):
                st.session_state.scan_results = screen_band_stocks(custom_codes=custom_codes,
                                                                   price_cap=float(_price_cap))
                st.session_state.scan_time = now_cn_str('%Y-%m-%d %H:%M:%S')
                # 自动入册：本次扫描中「波段启动确认」的股票写进记忆（预警状态只更新已有条目，
                # 不新建 —— 否则全市场扫描会把几百只跌破 20 日线的股票一次性灌进来），
                # 这样你关掉页面之后再回来，仍然知道当初是哪些票、后来变成了什么状态。
                _mem = load_band_memory()
                _added = band_memory_record(_mem, st.session_state.get('band_last_raw'), scan_scope)
                if _added:
                    save_band_memory(_mem)
                    st.session_state.band_memory_new = _added
                    _ok, _msg = band_memory_push_github(_mem)      # 没配 Token 会返回提示，不算失败
                    st.session_state.band_memory_sync_msg = _msg
            st.rerun()
        except Exception:
            st.error("选股扫描出错（已自动拦截，不影响页面其他功能）：")
            st.code(traceback.format_exc(), language="python")

    _manual_msg = st.session_state.get('band_memory_manual_msg')
    if _manual_msg:
        st.success(_manual_msg)
        st.session_state.band_memory_manual_msg = None      # 只提示一次

    if 'scan_results' in st.session_state:
        df_r = st.session_state.scan_results
        if df_r is None or df_r.empty:
            if 'scan_stats' in st.session_state:
                st.caption(f"📊 扫描漏斗: {st.session_state.scan_stats}")
            if scan_scope == "全市场扫描" and st.session_state.get('scan_stats', '').startswith("❌ 全市场接口"):
                st.info("💡 提示：全市场接口受网络/时段影响较大，可尝试「仅手动自选」输入几只股票，或选择「指定板块」进行扫描。")
            else:
                st.warning("本次扫描未找到符合条件的波段股，可稍后（或收盘后）重试。")
        else:
            if 'scan_time' in st.session_state:
                st.caption(f"上次扫描时间: {st.session_state.scan_time}")
            if 'scan_stats' in st.session_state:
                st.caption(f"📊 扫描漏斗: {st.session_state.scan_stats}")
            # ★ 2026-09-21 按用户要求重排：**选股页只回答「今天有哪些票刚启动」**。
            #   用户原话：「我只要刚启动的，然后如果我选中的话就加入记忆清单，
            #   然后在记忆清单里全程监视，及时提醒我结束。」
            #   所以其余状态全部收进折叠区，理由：
            #   - 「结束信号」只对**已在跟踪清单里的票**有意义 —— 你不持有的票，
            #     它结束不结束跟你无关；而你持有的票，结束提醒由「🧠 波段记忆」的红框
            #     和微信推送负责，不需要在选股页再看一遍。
            #   - **折叠而不是删除**：任何一行都不静默丢弃（万一是自己持有的票，仍点得开）。
            #   ★ 主区状态直接用 `BAND_ENTRY_STATUSES`，不另立一个常量 ——
            #     「刚启动」和「能自动入册」本来就是同一件事，两处各写一份就会漂移。
            try:
                _tracked = set((load_band_memory().get('stocks') or {}).keys())
            except Exception as e:
                _log("ai_band_picker_ui/tracked", e)
                _tracked = set()
            _seen = set()
            _primary = df_r[df_r['Status'].isin(BAND_ENTRY_STATUSES)]
            st.markdown(f"#### 🚀 刚启动（波段启动确认）（{len(_primary)} 只）")
            if _primary.empty:
                st.caption("本次没有刚启动的票。折叠区里可能还有正在走的波段。")
            else:
                st.caption("**这一组才是买入信号**（突破 60 日平台 + 放量）。"
                           "它们已自动进入「🧠 波段记忆」；若你已买入某只，"
                           "点它下方的「➕ 加入记忆」即可开始全程监控"
                           "（会立刻同步到云端，出现顶背离 / 跌破支撑推微信）。")
                # ★ 2026-09-21（晚）三个位的读法：和「🧠 波段记忆」同一套措辞，
                #   尤其**不许出现「目标价」**（创新高的票上方没有历史阻力）。
                st.caption("价格行读法：**现价**（加入记忆时它就记作「入选价」）· "
                           "**突破位**（突破前的 60 日平台上沿、**不含当天** —— "
                           "这条才是要站上去的线）· "
                           "**防守位**（20 日线，收盘跌破即波段结束）。"
                           "三个位都是按固定规则算出来的参考位，不是预测。")
                _seen.update(_primary['Code'].astype(str).tolist())
                for _, r in _primary.iterrows():
                    _render_band_card(r, _tracked)

            # ★ 2026-09-21 按用户要求：**结束信号（顶背离 / 跌破支撑）不再出现在选股页**。
            #   原话「把这些所谓的顶背离的都删了，我不在乎他们是不是顶背离，因为我都没有选过他们」。
            #   它们只对「🧠 波段记忆」里在跟踪的票有意义 —— 那里的红框与微信推送会负责提醒。
            #   ★ 但绝不静默：这里明写「已移出本页 N 只」，数字来自 screen_band_stocks 写的
            #     `scan_alerts_hidden`（df 里已经没有它们了，只能从那里取）。
            _n_hidden = int(st.session_state.get('scan_alerts_hidden') or 0)
            if _n_hidden:
                st.caption(f"⚠️ 已按你的要求把 **{_n_hidden} 只结束信号**（顶背离 / 跌破支撑）"
                           "移出本页 —— 它们只对「🧠 波段记忆」里在跟踪的票有意义，"
                           "那里的红框和微信推送会负责提醒。")
            # ---- 其余状态：默认折叠，不占版面 ----
            _others = df_r[~df_r['Code'].astype(str).isin(_seen)]
            if not _others.empty:
                with st.expander(f"📂 其他状态（{len(_others)} 只：进行中 / 未形成）"
                                 "—— 默认折叠，不影响选股", expanded=False):
                    for _gtitle, _gsts in (
                            ("🔄 波段进行中", ('波段进行中',)),
                            ("… 波段未形成", ('波段未形成',)),
                    ):
                        _sub = _others[_others['Status'].isin(_gsts)]
                        if _sub.empty:
                            continue
                        _seen.update(_sub['Code'].astype(str).tolist())
                        st.markdown(f"**{_gtitle}（{len(_sub)} 只）**")
                        for _, r in _sub.iterrows():
                            _render_band_card(r, _tracked)
                    # 兜底：万一以后加了新状态、或状态文案改了，剩下的一律照常显示，绝不静默丢弃
                    _rest = _others[~_others['Code'].astype(str).isin(_seen)]
                    if not _rest.empty:
                        st.markdown(f"**其他状态（{len(_rest)} 只）**")
                        for _, r in _rest.iterrows():
                            _render_band_card(r, _tracked)

    # ★ 2026-09-19 重排：这里原先紧接着渲染「波段记忆 / 策略复盘 / 逻辑有效性验证」
    #   三个面板，导致选股页越拖越长；而记忆清单又会把刚扫出来的启动股再列一遍，
    #   看上去就像两块内容重复。现在选股只负责回答「今天扫到了什么」，
    #   跟踪/复盘/验证各自回到自己的页签（见主流程的 st.tabs）。
    try:
        _m_quick = load_band_memory()
        _s_quick = band_memory_stats(_m_quick)
        st.caption(f"📋 跟踪清单就在下方「🧠 波段记忆」（在跟踪 {_s_quick['active']} 只："
                   f"启动 {_s_quick['entry']} ／ 结束预警 {_s_quick['alert']}）；"
                   f"策略复盘与逻辑有效性验证在「📊 复盘与验证」页签。")
    except Exception as e:
        _log("ai_band_picker_ui/quick_stats", e)

# ================= 11. 动态股票池系统 =================
@st.cache_data(ttl=180)
def get_market_sentiment():
    sentiment = {'score': 50, 'label': '中性', 'up_count': 0, 'down_count': 0, 'north_flow': 0.0, 'details': []}
    try:
        ok, text, err = _http_get_text("https://qt.gtimg.cn/q=sh000001,sz399001,sz399006",
                                       encoding='gbk', timeout=(5, 10), retries=2)
        if not ok:
            _log("get_market_sentiment/index", err)
        for line in (text or "").strip().split(';'):
            if '~' not in line: continue
            parts = line.split('~')
            if len(parts) > 32:
                try: sentiment['details'].append(f"{parts[1]}: {float(parts[32]):+.2f}%")
                except (ValueError, IndexError): pass
        # 涨跌家数：东财多 host 轮询（原先只打 push2 单 host，被拦时这一项会一直为 0）
        for base_url in _EM_HOSTS:
            ok2, stat_res, _err2 = _http_get_json(
                f"{base_url}/api/qt/stock/get",
                params={"fltt": "2", "invt": "2", "fields": "f104,f105,f106", "secid": "1.000001"},
                timeout=(5, 8), retries=1)
            if ok2 and isinstance(stat_res, dict) and stat_res.get("data"):
                sentiment['up_count'] = int(_safe_float(stat_res["data"].get("f104")))
                sentiment['down_count'] = int(_safe_float(stat_res["data"].get("f105")))
                break
        market_score = 50.0
        for d in sentiment['details']:
            try: market_score += float(d.split(':')[1].strip().replace('%', '')) * 8
            except (ValueError, IndexError): pass
        total = sentiment['up_count'] + sentiment['down_count']
        if total > 0: market_score += (sentiment['up_count'] / total - 0.5) * 30
        sentiment['score'] = max(0, min(100, round(market_score, 1)))
        if sentiment['score'] >= 75: sentiment['label'] = '🔥 极度贪婪'
        elif sentiment['score'] >= 60: sentiment['label'] = '☀️ 偏乐观'
        elif sentiment['score'] >= 40: sentiment['label'] = '☁️ 中性'
        elif sentiment['score'] >= 25: sentiment['label'] = '🌧️ 偏悲观'
        else: sentiment['label'] = '❄️ 极度恐慌'
    except Exception as e:
        _log("get_market_sentiment", e)
    return sentiment

@st.cache_data(ttl=600)
def get_industry_prosperity():
    """获取行业板块景气度，带多 host 容错。"""
    industries = {}
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90+t:2", "fields": "f12,f14,f2,f3,f62"}
    for base_url in _EM_HOSTS:
        try:
            res = requests.get(f"{base_url}/api/qt/clist/get", params=params, timeout=8, headers=_REQUEST_HEADERS)
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("data") and data["data"].get("diff"):
                for item in _diff_to_list(data["data"]["diff"]):
                    name = str(item.get("f14") or "")
                    main_flow = _safe_float(item.get("f62")) / 1e8
                    change_pct = _safe_float(item.get("f3"))
                    industries[name] = {'score': round(min(100, max(0, 50 + main_flow * 2 + change_pct * 3)), 1),
                                        'change_pct': change_pct, 'main_flow': round(main_flow, 2)}
                break
        except Exception as e:
            _log("get_industry_prosperity", e)
            continue
    return industries

@st.cache_data(ttl=300)
def get_hot_money_stocks(pages=3):
    """获取主力资金热度榜（按主力净流入降序），返回 {code: {'main_flow': 亿元}}。
    复用 fetch_market_page 的多 host 容错能力，避免单点接口失败。"""
    hot = {}
    try:
        for pn in range(1, pages + 1):
            page = fetch_market_page(pn, pz=100)
            if not page:
                break
            for item in page:
                code_ = str(item.get("f12") or "")
                mf = _safe_float(item.get("f62")) / 1e8
                if code_ and mf > 0:
                    hot[code_] = {'main_flow': mf}
    except Exception as e:
        _log("get_hot_money_stocks", e)
    return hot

@st.cache_data(ttl=900)
def get_stock_full_data(symbol):
    """获取个股完整数据，带多 host 容错。"""
    try:
        # 前缀统一由 _quote_prefix 判定：东财 secid 沪市用 1.，深市与北交所都用 0.
        secid = f"{'1' if _quote_prefix(symbol) == 'sh' else '0'}.{symbol}"
        params = {"fltt": "2", "invt": "2", "fields": "f43,f57,f58,f9,f23,f37,f45,f46,f48,f50,f62,f116,f117,f127,f168", "secid": secid}
        for base_url in _EM_HOSTS:
            try:
                ok, js, err = _http_get_json(f"{base_url}/api/qt/stock/get", params=params,
                                             timeout=(5, 8), retries=1)
                if not ok:
                    continue
                d = (js.get("data") if isinstance(js, dict) else None) or {}
                if not d:
                    continue
                return {'pe': _safe_float_or_none(d.get('f9')), 'pb': _safe_float_or_none(d.get('f23')),
                        'roe': _safe_float_or_none(d.get('f37')), 'profit': _safe_float_or_none(d.get('f45')),
                        'industry': d.get('f127') or "", 'main_flow': _safe_float_or_none(d.get('f62')),
                        'total_mv': _safe_float_or_none(d.get('f116')), 'circ_mv': _safe_float_or_none(d.get('f117')),
                        'turnover': _safe_float_or_none(d.get('f168')), 'vol_ratio': _safe_float_or_none(d.get('f50'))}
            except Exception as e:
                _log("get_stock_full_data", e)
                continue
        return None
    except Exception as e:
        _log("get_stock_full_data/outer", e)
        return None

@st.cache_data(ttl=1800)
def get_stock_historical_metrics(symbol):
    """动态池评分用的历史位置指标（250 日分位、是否站上 20/60 日线）。

    ⚠️ 原先这个函数被 refresh_dynamic_pool 调用但**从未定义** —— NameError 被
    fetch_one 的 `except Exception: return code_, None, None` 吞掉，于是 full_data 和 hist
    双双为 None，动态池综合评分只能拿到情绪分，实际长期处于失效状态。此处补全。
    """
    try:
        df = _get_daily_history(symbol)
        if df is None or len(df) < 60:
            return None
        close = df['Close']
        current = float(close.iloc[-1])
        if current <= 0:
            return None
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1])
        high_250 = float(close.tail(250).max()); low_250 = float(close.tail(250).min())
        position = 50.0 if high_250 <= low_250 else (current - low_250) / (high_250 - low_250) * 100
        return {'Position250': round(position, 1), 'AboveMA20': bool(current > ma20),
                'AboveMA60': bool(current > ma60), 'MA20': ma20, 'MA60': ma60, 'Current': current}
    except Exception as e:
        _log("get_stock_historical_metrics", e)
        return None

def calculate_dynamic_score(symbol, full_data, industry_data, sentiment, hot_money, hist_metrics):
    score = 0; reasons = []; tags = []
    if full_data:
        pe = full_data.get('pe'); pb = full_data.get('pb'); roe = full_data.get('roe'); profit = full_data.get('profit')
        if pe is not None and 0 < pe <= 15: score += 10; reasons.append(f"低PE({pe:.1f})")
        elif pe is not None and 0 < pe <= 25: score += 6; reasons.append(f"PE合理({pe:.1f})")
        if pb is not None and 0 < pb <= 1.5: score += 8; reasons.append(f"低PB({pb:.2f})")
        elif pb is not None and 0 < pb <= 3: score += 4; reasons.append(f"PB适中({pb:.2f})")
        if roe is not None and roe > 15: score += 7; reasons.append(f"高ROE({roe:.1f}%)"); tags.append("高ROE")
        elif roe is not None and roe > 8: score += 4; reasons.append(f"ROE良好({roe:.1f}%)")
        if profit is not None and profit > 1e8: score += 5; reasons.append(f"盈利强({profit/1e8:.1f}亿)"); tags.append("盈利强")
        elif profit is not None and profit > 0: score += 2; reasons.append("盈利为正")
    if industry_data:
        ind_score = industry_data.get('score', 50); ind_flow = industry_data.get('main_flow', 0)
        score += int(ind_score * 0.20)
        if ind_score >= 70: reasons.append(f"行业景气({ind_score:.0f}分)"); tags.append("行业景气")
        if ind_flow > 5: score += 5; reasons.append(f"行业资金+{ind_flow:.1f}亿")
        elif ind_flow > 0: score += 2
    elif full_data and full_data.get('industry'): reasons.append(f"行业:{full_data['industry']}")
    if sentiment:
        s_score = sentiment.get('score', 50); s_label = sentiment.get('label', '中性')
        if 40 <= s_score <= 70: score += 12; reasons.append(f"情绪适配({s_label})")
        elif s_score > 70: score += 6; reasons.append(f"市场过热({s_label})")
        else: score += 8; reasons.append(f"市场低迷({s_label})"); tags.append("逆势")
    if hot_money:
        mf = hot_money.get('main_flow', 0)
        if mf > 5: score += 12; reasons.append(f"主力+{mf:.1f}亿"); tags.append("主力加仓")
        elif mf > 1: score += 8; reasons.append(f"主力+{mf:.1f}亿")
        elif mf > 0: score += 4; reasons.append(f"主力小幅流入")
    if full_data:
        turnover = full_data.get('turnover'); vol_ratio = full_data.get('vol_ratio')
        if turnover is not None and 2 <= turnover <= 10: score += 4; reasons.append(f"换手{turnover:.1f}%")
        if vol_ratio is not None and 1.2 <= vol_ratio <= 3: score += 4; reasons.append(f"量比{vol_ratio:.1f}")
    if hist_metrics:
        pos = hist_metrics.get('Position250', 50)
        if pos <= 20: score += 6; reasons.append(f"250日分位{pos:.0f}%"); tags.append("低位")
        elif pos <= 40: score += 3; reasons.append(f"分位{pos:.0f}%")
        if hist_metrics.get('AboveMA20'): score += 2
        if hist_metrics.get('AboveMA60'): score += 2
    else: score += 3
    return {'score': min(100, score), 'reasons': ' | '.join(reasons[:8]), 'tags': tags}

def refresh_dynamic_pool(max_candidates=30):
    pool = load_dynamic_pool(); now_str = now_cn_str('%Y-%m-%d %H:%M')
    progress = st.progress(0, text="正在初始化动态池引擎...")
    progress.progress(5, text="正在评估市场情绪..."); sentiment = get_market_sentiment()
    progress.progress(12, text="正在分析行业景气度..."); industry_all = get_industry_prosperity()
    progress.progress(20, text="正在获取主力资金热度榜..."); hot_money = get_hot_money_stocks()
    
    # ✅ 修复：候选来源多元化（自选股 + 资金热度榜前50 + 全市场均匀抽样），不再只剩自选股
    def _collect_candidates(limit):
        out, seen = [], set()
        def add(c):
            if c and c not in seen and not c.startswith(EXCLUDE_PREFIXES):
                seen.add(c); out.append(c)
        for s in st.session_state.stock_list: add(s)          # 自选股优先
        for c in list(hot_money.keys())[:50]: add(c)          # 资金热度榜
        try:
            market_codes = []
            for pn in range(1, 6):                            # 全市场抽样池
                pg = fetch_market_page(pn)
                if not pg: break
                market_codes.extend([str(x.get("f12") or "") for x in pg])
            market_codes = [c for c in market_codes if c and not c.startswith(EXCLUDE_PREFIXES)]
            need = limit - len(out)
            if need > 0 and market_codes:
                if len(market_codes) > need:
                    step = len(market_codes) / need
                    for i in range(need):
                        add(market_codes[min(int(i * step), len(market_codes) - 1)])
                else:
                    for c in market_codes: add(c)
        except Exception as e:
            _log("refresh_dynamic_pool/_collect_candidates", e)
        return out[:limit]

    candidates = _collect_candidates(max_candidates)
    progress.progress(25, text=f"候选池 {len(candidates)} 只，并发分析中...")
    
    def fetch_one(code_):
        try:
            return code_, get_stock_full_data(code_), get_stock_historical_metrics(code_)
        except Exception as e:
            _log("refresh_dynamic_pool/fetch_one", e)
            return code_, None, None
    results = {}; completed = 0; total = len(candidates)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, c): c for c in candidates}
        for future in as_completed(futures):
            try:
                code_, full_data, hist = future.result()
            except Exception as e:
                _log("refresh_dynamic_pool/future", e)
                continue
            results[code_] = (full_data, hist)
            completed += 1; progress.progress(25 + int(65 * completed / total), text=f"分析中 {completed}/{total}...")
    progress.progress(92, text="正在计算综合评分...")
    for code_ in candidates:
        full_data, hist = results.get(code_, (None, None))
        industry_data = None
        if full_data and full_data.get('industry'):
            ind_name = full_data['industry']
            if ind_name in industry_all: industry_data = industry_all[ind_name]
            else:
                for k, v in industry_all.items():
                    if k and (k in ind_name or ind_name in k): industry_data = v; break
        hm = hot_money.get(code_)
        result = calculate_dynamic_score(code_, full_data, industry_data, sentiment, hm, hist)
        if code_ not in pool:
            pool[code_] = {'score': result['score'], 'reasons': result['reasons'], 'tags': result['tags'], 'last_update': now_str, 'first_seen': now_str}
        else:
            old_score = pool[code_].get('score', 0); new_score = int(old_score * 0.4 + result['score'] * 0.6)
            pool[code_].update({'score': new_score, 'reasons': result['reasons'], 'tags': result['tags'], 'last_update': now_str})
    pool = {k: v for k, v in pool.items() if v.get('score', 0) >= 40 or k in st.session_state.stock_list}
    progress.progress(100, text="✅ 完成"); progress.empty(); save_dynamic_pool(pool); return pool

def dynamic_pool_ui():
    st.markdown("---"); st.header("🌊 动态股票池")
    col_sent, col_pool_info = st.columns([1, 1])
    with col_sent:
        sentiment = get_market_sentiment(); s_score = sentiment['score']
        s_color = "#00cc66" if s_score >= 60 else ("#f9e2af" if s_score >= 40 else "#ff4b4b")
        st.markdown(f"""<div style="background:#1e1e2e; border-radius:10px; padding:15px; border-left:4px solid {s_color};"><div style="color:#89b4fa; font-size:14px;">📊 市场情绪温度</div><div style="font-size:32px; font-weight:bold; color:{s_color}; margin:8px 0;">{s_score:.0f}<span style="font-size:16px; color:#888;">/100</span></div><div style="color:#f9e2af; font-size:16px;">{sentiment['label']}</div><div style="color:#c9d1d9; font-size:12px; margin-top:8px;">{' '.join(sentiment.get('details', [])[:3])}</div></div>""", unsafe_allow_html=True)
    with col_pool_info:
        pool = load_dynamic_pool(); high_score = sum(1 for v in pool.values() if v.get('score', 0) >= 65)
        last_upd = max([v.get('last_update', '') for v in pool.values()]) if pool else '从未'
        st.markdown(f"""<div style="background:#1e1e2e; border-radius:10px; padding:15px; border-left:4px solid #89b4fa;"><div style="color:#89b4fa; font-size:14px;">📦 动态池状态</div><div style="font-size:28px; font-weight:bold; color:#f0f2f6; margin:8px 0;">{len(pool)}<span style="font-size:16px; color:#888;"> 只</span></div><div style="color:#00cc66; font-size:14px;">高分股(≥65): {high_score} 只</div><div style="color:#c9d1d9; font-size:12px; margin-top:8px;">上次更新: {last_upd}</div></div>""", unsafe_allow_html=True)
    col_refresh, col_clean = st.columns([1, 1])
    with col_refresh:
        if long_button("🔄 刷新动态池", key="refresh_pool", type="primary",
                       use_container_width=True):
            with st.spinner("并发拉取中，约需 30 秒..."): st.session_state.dynamic_pool = refresh_dynamic_pool()
            st.success("✅ 动态池刷新完成！"); st.rerun()
    with col_clean:
        if st.button("🧹 清理低分股", use_container_width=True, key="clean_pool"):
            pool = load_dynamic_pool(); cleaned = {k: v for k, v in pool.items() if v.get('score', 0) >= 50}
            save_dynamic_pool(cleaned); st.success(f"已清理 {len(pool) - len(cleaned)} 只低分股"); st.rerun()
    pool = load_dynamic_pool()
    if pool:
        sorted_pool = sorted(pool.items(), key=lambda x: x[1].get('score', 0), reverse=True)
        st.markdown("### 📋 池内股票（按综合评分排序）")
        for code_, info in sorted_pool[:20]:
            name = get_stock_name(code_); score = info.get('score', 0)
            tags = info.get('tags', []); reasons = info.get('reasons', '')
            score_color = "#00cc66" if score >= 65 else ("#f9e2af" if score >= 50 else "#888")
            tag_html = ' '.join([f'<span style="background:#2b2b3b; padding:2px 8px; border-radius:10px; font-size:12px; color:#89b4fa;">{t}</span>' for t in tags])
            st.markdown(f"""<div style="background:#1e1e2e; border-radius:8px; padding:12px 16px; margin-bottom:8px; border-left:3px solid {score_color};"><div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;"><div><span style="font-size:16px; font-weight:bold; color:#f0f2f6;">{name}</span><span style="color:#89b4fa; font-size:13px; margin-left:6px;">({code_})</span><span style="margin-left:10px;">{tag_html}</span></div><span style="color:{score_color}; font-size:18px; font-weight:bold;">{score}分</span></div><div style="color:#c9d1d9; font-size:12px; margin-top:6px;">{reasons}</div></div>""", unsafe_allow_html=True)
    else: st.info("动态池为空，点击上方「刷新动态池」开始构建。")

# ================= 12. 图表绘制 =================
PLOTLY_CONFIG_CLEAN = {'displayModeBar': False, 'scrollZoom': False, 'staticPlot': False, 'doubleClick': 'reset'}
# 日K主图专用：把模式栏给回来（缩放 / 框选缩放 / 平移 / 自动缩放 / 重置）。
# 全站原本都是 displayModeBar=False，用户在日K图上就只剩「拖框」和「双击重置」两种手段；
# 一旦拖框因坐标轴被锁而失效，表现就完全是「缩放坏了」。分时图仍用 CLEAN
# （它所有轴都 fixedrange=True，本来就不需要交互，多一根工具栏只是噪声）。
PLOTLY_CONFIG_DAILY = dict(PLOTLY_CONFIG_CLEAN, displayModeBar=True)

def plot_daily_chart(df, symbol_name, latest, uirevision_key=0):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.72, 0.28])
    fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K', increasing_line_color='#ff3333', decreasing_line_color='#00cc66', line=dict(width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA5'], mode='lines', name='MA5', line=dict(color='#ffffff', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA10'], mode='lines', name='MA10', line=dict(color='#ffff00', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ff00ff', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA30'], mode='lines', name='MA30', line=dict(color='#00ff00', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], mode='lines', name='年线', line=dict(color='#00ccff', width=1.2, dash='dash')), row=1, col=1)
    buy_s = df[df['Signal'] == 1]; sell_s = df[df['Signal'] == -1]
    if not buy_s.empty: fig.add_trace(go.Scatter(x=buy_s['Date'], y=buy_s['Low'] * 0.97, mode='markers', name='买点', marker=dict(symbol='triangle-up', size=18, color='#ff3333', line=dict(width=2, color='#ffffff')), hovertemplate='买点<br>日期:%{x}<br>价格:%{customdata:.3f}<extra></extra>', customdata=buy_s['Close']), row=1, col=1)
    if not sell_s.empty: fig.add_trace(go.Scatter(x=sell_s['Date'], y=sell_s['High'] * 1.03, mode='markers', name='卖点', marker=dict(symbol='triangle-down', size=18, color='#00cc66', line=dict(width=2, color='#ffffff')), hovertemplate='卖点<br>日期:%{x}<br>价格:%{customdata:.3f}<extra></extra>', customdata=sell_s['Close']), row=1, col=1)
    vol_colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量', marker_color=vol_colors, showlegend=False), row=2, col=1)
    if 'VOL_MA5' in df.columns: fig.add_trace(go.Scatter(x=df['Date'], y=df['VOL_MA5'], mode='lines', name='VOL_MA5', line=dict(color='#ffffff', width=1.5), showlegend=False), row=2, col=1)
    if 'VOL_MA10' in df.columns: fig.add_trace(go.Scatter(x=df['Date'], y=df['VOL_MA10'], mode='lines', name='VOL_MA10', line=dict(color='#ffaa00', width=1.5), showlegend=False), row=2, col=1)
    cur_vol = latest['Volume']; vol_ma5 = latest['VOL_MA5'] if not pd.isna(latest.get('VOL_MA5', np.nan)) else 0; vol_ma10 = latest['VOL_MA10'] if not pd.isna(latest.get('VOL_MA10', np.nan)) else 0
    fig.add_annotation(xref="paper", yref="paper", x=0.005, y=0.275, text=f"<b>成交量</b>  {cur_vol/1e6:.2f}M   MA5:{vol_ma5/1e6:.2f}M   MA10:{vol_ma10/1e6:.2f}M", showarrow=False, xanchor='left', yanchor='top', font=dict(color='#89b4fa', size=11, family='Consolas'), bgcolor='rgba(0,0,0,0)', bordercolor='rgba(0,0,0,0)')
    fig.update_layout(template="plotly_dark", height=650, xaxis_rangeslider_visible=False, hovermode="x unified", dragmode='zoom', legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10), bgcolor='rgba(0,0,0,0)', bordercolor='rgba(0,0,0,0)'), margin=dict(t=50, l=10, r=10, b=10), uirevision=uirevision_key)
    # ---- 缩放联动（这块踩过坑，动手前务必读完）----
    # make_subplots(shared_xaxes=True) 的真实行为：让**上方**子图的 x 去 matches **最下方**子图的 x。
    # 实测 fig.layout.xaxis.matches == 'x2' —— 也就是主图 x 是"跟随者"，成交量副图的 x2 才是"驱动者"。
    # ⚠️ 曾经的写法是给副图又加 `matches='x'` 且 `fixedrange=True`，等于干了两件坏事：
    #    ① 制造 x ↔ x2 的循环匹配；② 把"驱动者"锁死 —— 跟随者自然跟着一起锁死。
    #    症状就是：主图**横向完全拖不动、只剩纵向能缩放**，而且"只要去动成交量图的缩放设置，
    #    主图就坏掉"（因为被锁的正是驱动轴）。所以这里**绝不能**再给副图 x 加 fixedrange。
    # 现在的口径：两根 x 靠 shared_xaxes 天然联动，在任意一个子图上拖框，两个面板同时缩放；
    #    副图纵向锁死（成交量刻度不需要拖），主图纵向自由。
    # rangebreaks 也必须**两根 x 都设**：rangebreaks 不会被 matches 复制，只设一根会让两个面板的
    #    日期刻度错位（少了"驱动轴不带周末压缩"这个半坏状态）。
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], fixedrange=False)
    fig.update_yaxes(fixedrange=False, row=1, col=1)
    fig.update_yaxes(fixedrange=True, row=2, col=1)
    return fig

def plot_minute_chart_ths(df, buy_points, sell_points, symbol_name, prev_close, uirevision_key=0):
    df = df[(df['Time'] >= "0930") & (df['Time'] <= "1500")]; df = df[~((df['Time'] > "1130") & (df['Time'] < "1300"))].reset_index(drop=True)
    if df.empty: return go.Figure()
    df['Datetime'] = pd.to_datetime("2024-01-01 " + df['Time'].str[:2] + ":" + df['Time'].str[2:])
    latest_price = df['Price'].iloc[-1]; latest_avg = df['AvgPrice'].iloc[-1]
    actual_max = df['Price'].max(); actual_min = df['Price'].min()
    actual_range_pct = (actual_max - actual_min) / prev_close if prev_close > 0 else 0.02
    base_pct = max(0.03, min(actual_range_pct * 0.8 + 0.01, 0.08))
    y_max = max(prev_close * (1 + base_pct), actual_max * 1.003, latest_price * 1.003); y_min = min(prev_close * (1 - base_pct), actual_min * 0.997, latest_price * 0.997)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Price'], mode='lines', name='分时价格', line=dict(color='#00ccff', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['AvgPrice'], mode='lines', name='分时均价', line=dict(color='#ffaa00', width=1.5)), row=1, col=1)
    fig.add_hline(y=prev_close, line_dash="dash", line_color="#888888", line_width=1, row=1, col=1, annotation_text=f"昨收 {prev_close:.3f}", annotation_position="right", annotation_font=dict(color="#f0f2f6", size=12))
    color_price = "#ff3333" if latest_price >= prev_close else "#00cc66"
    fig.add_hline(y=latest_price, line_dash="dot", line_color=color_price, line_width=1.5, row=1, col=1, annotation_text=f"{latest_price:.3f}", annotation_position="left", annotation_font=dict(color=color_price, size=12))
    if not buy_points.empty:
        buy_points = buy_points[buy_points['Time'] <= "1500"]
        if not buy_points.empty:
            buy_points = buy_points.copy(); buy_points['Datetime'] = pd.to_datetime("2024-01-01 " + buy_points['Time'].str[:2] + ":" + buy_points['Time'].str[2:])
            fig.add_trace(go.Scatter(x=buy_points['Datetime'], y=buy_points['Price']*0.997, mode='markers', name='分时买点', marker=dict(symbol='triangle-up', size=18, color='#ff3333', line=dict(width=2, color='#ffffff'))), row=1, col=1)
    if not sell_points.empty:
        sell_points = sell_points[sell_points['Time'] <= "1500"]
        if not sell_points.empty:
            sell_points = sell_points.copy(); sell_points['Datetime'] = pd.to_datetime("2024-01-01 " + sell_points['Time'].str[:2] + ":" + sell_points['Time'].str[2:])
            fig.add_trace(go.Scatter(x=sell_points['Datetime'], y=sell_points['Price']*1.003, mode='markers', name='分时卖点', marker=dict(symbol='triangle-down', size=18, color='#00cc66', line=dict(width=2, color='#ffffff'))), row=1, col=1)
    fig.add_annotation(x=0.99, y=0.98, xref="paper", yref="paper", text=f"<b>价格:</b> <span style='color:{color_price}'>{latest_price:.3f}</span><br><b>均价:</b> <span style='color:#ffaa00'>{latest_avg:.3f}</span>", showarrow=False, align="right", bgcolor='rgba(0,0,0,0)', bordercolor='rgba(0,0,0,0)', borderpad=6, font=dict(color="#f0f2f6", size=14))
    vol_colors = ['#ff3333' if change >= 0 else '#00cc66' for change in df['Price_Change']]
    fig.add_trace(go.Bar(x=df['Datetime'], y=df['Volume'], name='分时成交量', marker_color=vol_colors, width=1000*60*0.8), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Volume'].rolling(5).mean(), mode='lines', name='均量', line=dict(color='#ffaa00', width=1.5)), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, hovermode="x unified", dragmode=False, legend=dict(orientation="h", yanchor="top", y=1.0, xanchor="left", x=0, bgcolor='rgba(0,0,0,0)', bordercolor='rgba(0,0,0,0)'), margin=dict(t=40, l=10, r=10, b=10), uirevision=uirevision_key)
    fig.update_xaxes(type='date', tickformat="%H:%M", range=["2024-01-01 09:30:00", "2024-01-01 15:00:00"], rangebreaks=[dict(bounds=[11.5, 13], pattern="hour")], fixedrange=True)
    fig.update_yaxes(range=[y_min, y_max], fixedrange=True, row=1, col=1); fig.update_yaxes(fixedrange=True, row=2, col=1)
    return fig

# ================= 13. 主程序执行 =================
try:
    if 'chart_reset_key' not in st.session_state: st.session_state.chart_reset_key = 0
    if 'notified_keys' not in st.session_state: st.session_state.notified_keys = set()
    df_daily = None; df_minute = None; _daily_err = None; _daily_src = ""
    try:
        df_daily = get_daily_data(code)
    except DataFetchError as _e:
        _daily_err = _e
    except Exception as _e:
        _daily_err = DataFetchError(code, [("未知异常", f"{type(_e).__name__}: {str(_e)[:150]}")])
    df_minute = get_minute_data(code); market_change = get_market_status()
    _diag = LAST_FETCH_DIAG.get(code, {}); _daily_src = _diag.get("source", "")
    if df_daily is not None and not df_daily.empty:
        df_daily = calculate_daily_indicators(df_daily)
        if _daily_src and not _daily_src.startswith("腾讯"):
            _degraded = _daily_src.startswith(("本地缓存", "新浪"))
            st.warning(f"⚠️ 腾讯主源不可用，本次日线来自 **{_daily_src}**。"
                       + ("**这回退到了本地缓存 / 不复权源**，价格口径可能与实时行情有差异，"
                          "回踩/压力位判断请以券商行情为准。" if _degraded else
                          "东财与腾讯同为前复权，口径一致，可以直接用。"))
            with st.expander("🔌 数据源现状 / 一键体检", expanded=False):
                _render_feed_diag(code, _diag)
    else:
        st.error(f"❌ 无法获取 {current_name}（{symbol}）的日线数据，主图已暂停渲染。")
        with st.expander("🔍 展开查看失败原因（排查用，可直接截图反馈）", expanded=True):
            st.markdown(f"- 请求代码：`{code}`（前缀由 `_quote_prefix` 自动判定）")
            st.markdown(f"- 北京时间：{now_cn_str()}")
            st.markdown("- 逐次尝试结果：")
            _attempts = (_daily_err.attempts if _daily_err is not None else _diag.get("attempts", []))
            if _attempts:
                for _i, _a in enumerate(_attempts, 1):
                    _nm, _rs = (_a if isinstance(_a, (tuple, list)) and len(_a) == 2 else ("尝试", str(_a)))
                    st.markdown(f"    {_i}. **{_nm}** → {_rs}")
            else:
                st.markdown("    （无记录）")
            st.caption("常见原因：① 网络或代理拦截了行情接口；② 代码前缀不被数据源支持"
                       "（如北交所 43/83/87/88/92 开头，腾讯不提供日线）；③ 该代码已退市或长期停牌。")
            st.markdown("---")
            _render_feed_diag(code, _diag)
        if st.button("🔄 清除数据缓存并重试", key="retry_daily_fetch"):
            get_daily_data.clear(); get_minute_data.clear(); st.rerun()
        st.markdown("---")
        st.info("📌 下面两个模块不依赖当前标的的日线，仍可正常使用。也可在左侧自选股中切换到其他标的。")
        ai_band_picker_ui()
        dynamic_pool_ui()
        st.stop()
    today_norm  = pd.Timestamp(now_cn().date()); last_k_norm = df_daily['Date'].iloc[-1].normalize()
    if len(df_daily) >= 2 and last_k_norm == today_norm: prev_close = df_daily['Close'].iloc[-2]
    else: prev_close = df_daily['Close'].iloc[-1]
    actual_deviation = dynamic_deviation(df_minute) if auto_dev else manual_dev
    report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest, intraday_struct = generate_report_and_advice(df_daily, df_minute, actual_deviation, market_change, prev_close)
    if is_trading_time() and st.session_state.get('send_key') and st.session_state.get('enable_page_monitor', False):
        fired = monitor_all_watchlist(st.session_state.send_key, market_change)
        st.session_state.last_monitor_time = now_cn_str('%H:%M:%S')
        st.session_state.last_monitor_count = len(st.session_state.get('stock_list', []))
        for f in fired: st.toast(f, icon="🔔")
    elif st.session_state.get('send_key') and not is_trading_time():
        st.session_state.last_monitor_count = 0
    # ================= ★ 页面重排（2026-09-19）=================
    # 原来是一条长滚动：日内信号 → 日线图 → 分时图 → 选股 → 记忆 → 复盘 → 验证 → 动态池 → AI，
    # 十块内容一路排下去。问题有三个：
    #   ① 越往下越长，盘中要看的日内信息被埋在中间；
    #   ② 「选股 / 波段记忆 / 策略复盘 / 逻辑验证」四块挨在一起，视觉上像在重复说一件事；
    #   ③ 找不到东西 —— 想复盘要滚很久。
    # 现在按「你正在干什么」分成四段，每件事只出现在一个地方：
    #   今日看盘（盘中最常用，最干净）｜选股与跟踪｜复盘与验证｜AI 与设置
    # ★ 用 st.tabs 不会增加任何计算量：Streamlit 只是把元素分到不同容器里，
    #   所有分栏内容都会照常执行（和重排前一样），只是显示时按需切换。
    _tab_today, _tab_pick, _tab_review, _tab_ai = st.tabs(
        ["🎯 今日看盘", "🌊 选股与跟踪", "📊 复盘与验证", "💬 AI 与设置"])

    with _tab_today:
        st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)
        # ★ 三个盒子里的文案都过 _intraday_rich_text：它们是塞进 <div> 的 raw HTML，
        #   Streamlit 前端用 allowDangerousHtml 直通 HTML，**块内的 markdown 不会被解析**，
        #   `**加粗**` 会原样显示成星号（已在 StreamlitMarkdown 前端包里确认）。
        #   所以自己把 ** 转 <b>、换行转 <br>，不依赖渲染器。
        st.markdown(f'<div class="ai-advice-box">🤖 <b>AI 实时建议</b><br>{_intraday_rich_text(ai_advice)}</div>', unsafe_allow_html=True)
        # ================= ★ 今日盘面动态分析（2026-09-20 新增）=================
        # 起因：用户反馈「盘面上下太快的时候根本看不懂今天到底可能是什么走势」。
        # 原来的两栏（做T指引 / 极值预测）各答一个侧面，谁也不回答「今天在走什么结构」；
        # 而极值预测的上下沿会被**已实现**的极值钉住，越到下午越像在回显事实。
        # 这一栏专门讲结构：形态 + 日内位置 + 波动速度 + 三种情景 + 触发价，
        # 并且当「指引方向」与「盘中位置」互相打架时**明说出来**（那是最容易亏钱的位置）。
        if intraday_struct.get("ok"):
            _st_sub = _intraday_rich_text(intraday_struct["read"])
            # ★ scen_mark=True：只有「三种情景」这一栏把 [[...]] 转成「当前」标注 chip，
            #   别的盒子（含 AI 输出）不传这个开关，免得文本里恰好出现 [[...]] 被误转。
            _st_scen = _intraday_rich_text(intraday_struct["scenarios"], scen_mark=True)
            _st_now = _intraday_rich_text(intraday_struct.get("active_line") or "")
            _now_kind = intraday_struct.get("active_kind") or "flat"
            _st_conf = _intraday_rich_text(intraday_struct["conflict"]) if intraday_struct.get("conflict") else ""
            _st_html = (
                f'<div class="struct-box">🧭 <b>今日盘面动态分析</b>'
                f'<span class="struct-tag struct-tag-now-{_now_kind}">当前 {intraday_struct.get("active_label") or "—"}</span>'
                f'<span class="struct-tag">{intraday_struct["shape"]}</span>'
                f'<span class="struct-tag">{intraday_struct["stage"]}</span>'
                f'<span class="struct-tag">昨收以来 {intraday_struct["chg"]:+.2f}%</span>'
                f'<span class="struct-tag">日内位置 {intraday_struct["pos_pct"]:.0f}%</span>'
                f'<span class="struct-tag">振幅 {intraday_struct["range_pct"]:.2f}%（{intraday_struct["range_label"]}）</span>'
                f'<span class="struct-tag">{intraday_struct["speed_label"]}</span>'
                f'<div class="struct-line">{_st_sub}</div>'
                + (f'<div class="struct-now">{_st_now}</div>' if _st_now else "")
                + f'<div class="struct-scen">{_st_scen}</div>'
                + (f'<div class="struct-warn">{_st_conf}</div>' if _st_conf else "")
                + '</div>'
            )
            st.markdown(_st_html, unsafe_allow_html=True)
        else:
            st.caption("🧭 今日盘面动态分析：分时数据不足（至少需要 10 根分钟线），暂不判定结构。")
        col_g, col_p = st.columns(2)
        with col_g: st.markdown(f'<div class="guide-box">🎯 <b>今日做T指引</b><br>{_intraday_rich_text(t_guide)}</div>', unsafe_allow_html=True)
        with col_p: st.markdown(f'<div class="predict-box">📊 <b>日内波动区间</b><br>{_intraday_rich_text(predict_text)}</div>', unsafe_allow_html=True)
        # ★ 分时图放最前：盘中真正盯着看的是它；日线图 120 根是「确认大势」用的，
        #   收进展开项，少占一屏。
        col_title2, col_btn2 = st.columns([9, 1])
        with col_title2: st.subheader(f"⏱️ {current_name} ({symbol}) 分时级别走势（同花顺风格）")
        with col_btn2:
            if st.button("🔄 复位", use_container_width=True, key="reset_minute_chart"): st.session_state.chart_reset_key += 1; st.rerun()
        if df_minute is not None and not df_minute.empty:
            st.plotly_chart(plot_minute_chart_ths(df_minute, buy_points, sell_points, symbol, prev_close, st.session_state.chart_reset_key), use_container_width=True, config=PLOTLY_CONFIG_CLEAN)
            st.caption("操作说明：分时图只显示 09:30-15:00 交易时段，锁定缩放。")
        else:
            st.warning("暂无分时数据")
        with st.expander(f"📈 {current_name}（{symbol}）日线级别走势（120 根）", expanded=False):
            col_title1, col_btn1 = st.columns([9, 1])
            with col_title1: st.caption("点开即用；不放首页是因为盘中主要看分时。")
            with col_btn1:
                if st.button("🔄 复位", use_container_width=True, key="reset_daily_chart"): st.session_state.chart_reset_key += 1; st.rerun()
            ma_html = f"""<div class="ma-bar"><span style="color:#ffffff">M5: {latest['MA5']:.3f}</span><span style="color:#ffff00">M10: {latest['MA10']:.3f}</span><span style="color:#ff00ff">M20: {latest['MA20']:.3f}</span><span style="color:#00ff00">M30: {latest['MA30']:.3f}</span><span style="color:#00ccff">年线: {latest['MA250']:.3f}</span></div>"""
            st.markdown(ma_html, unsafe_allow_html=True)
            st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol, latest, st.session_state.chart_reset_key), use_container_width=True, config=PLOTLY_CONFIG_DAILY)
            st.caption("💡 **放大**：在图上按住左键拖出矩形框，松开即放大该区域（主图与成交量**同步缩放**，在哪个子图上拖都可以）；"
                       "右上角工具栏有缩放 / 平移 / 自动缩放 / 重置按钮；双击图表或点「🔄 复位」回到初始视图。")

    with _tab_pick:
        # 扫描（今天扫到什么，按状态分组）→ 跟踪清单（我记住的票，唯一一处）→ 动态池
        ai_band_picker_ui()
        try:
            band_memory_ui()
        except Exception as e:
            _log("band_memory_ui", e)
            st.warning("跟踪清单渲染出错（已拦截，不影响上方选股功能）")
            with st.expander("查看错误详情"):
                st.code(traceback.format_exc(), language="python")
        try:
            dynamic_pool_ui()
        except Exception as e:
            _log("dynamic_pool_ui", e)
            st.warning("动态股票池渲染出错（已拦截，不影响其他功能）")
            with st.expander("查看错误详情"):
                st.code(traceback.format_exc(), language="python")

    with _tab_review:
        # 事后统计都归这里：批次复盘 + 归因、以及机器自采样本的逻辑有效性验证
        try:
            band_review_ui()
        except Exception as e:
            _log("band_review_ui", e)
            st.warning("策略复盘面板渲染出错（已拦截，不影响其他功能）")
            with st.expander("查看错误详情"):
                st.code(traceback.format_exc(), language="python")
        try:
            band_sample_ui()
        except Exception as e:
            _log("band_sample_ui", e)
            st.warning("逻辑验证面板渲染出错（已拦截，不影响其他功能）")
            with st.expander("查看错误详情"):
                st.code(traceback.format_exc(), language="python")

    with _tab_ai:
        st.caption("自选股、参数、AI Key、微信提醒、波段记忆同步都在**左侧边栏**；"
                   "这里只放日常问盘面的对话框。")
        st.subheader("💬 DeepSeek AI")
        if 'messages' not in st.session_state: st.session_state.messages = []
        if 'history_questions' not in st.session_state: st.session_state.history_questions = []
        col_hist, col_chat = st.columns([1, 2])
        with col_hist:
            st.markdown("**📜 历史提问**")
            if not st.session_state.history_questions: st.caption("暂无历史")
            else:
                for i, q in enumerate(reversed(st.session_state.history_questions[-15:])):
                    if st.button(f"• {q[:12]}...", key=f"hist_{i}", use_container_width=True): st.session_state.messages = [{"role": "user", "content": q}]; st.rerun()
        with col_chat:
            if not st.session_state.api_key: st.warning("⚠️ 请先在左侧侧边栏配置 DeepSeek API Key")
            else:
                chat_container = st.container(height=420, border=True)
                with chat_container:
                    for message in st.session_state.messages:
                        with st.chat_message(message["role"]): st.markdown(message["content"])
                if prompt := st.chat_input("在此提问..."):
                    with st.chat_message("user"): st.markdown(prompt)
                    st.session_state.messages.append({"role": "user", "content": prompt}); st.session_state.history_questions.append(prompt)
                    system_prompt = f"""你是一个专业的A股做T交易助手。请根据以下实时盘面数据，用简洁专业的语言回答用户的问题。注意：不要盲目看多或看空，要结合支撑压力、量能和趋势给出客观判断。{context}"""
                    messages_to_send = [{"role": "system", "content": system_prompt}] + st.session_state.messages
                    with st.chat_message("assistant"):
                        message_placeholder = st.empty(); full_response = ""
                        try:
                            headers = {"Authorization": f"Bearer {st.session_state.api_key}", "Content-Type": "application/json"}
                            data = {"model": "deepseek-chat", "messages": messages_to_send, "stream": False}
                            response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=data, timeout=30)
                            if response.status_code == 200: full_response = response.json()['choices'][0]['message']['content']
                            else: full_response = f"API请求失败（{response.status_code}）。请检查API Key或余额。"
                        except Exception as e: full_response = f"网络异常：{str(e)}"
                        message_placeholder.markdown(full_response)
                    st.session_state.messages.append({"role": "assistant", "content": full_response}); st.rerun()
except Exception as _top_err:
    _log("main", _top_err)
    st.error("❌ 主程序运行出错，请把下面的错误信息截图反馈：")
    st.code(traceback.format_exc(), language="python")
