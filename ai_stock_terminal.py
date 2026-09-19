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
# 就把自动刷新间隔放宽到 30 分钟（组件照旧渲染 → 组件自带的 debounce 会 clearInterval 掉那根
# 60 秒的定时器，不会出现"自动刷新被永久关掉"），这一次运行就砍不到了，可以安心把活干完。
_LONG_TASK_KEY = "_long_task_pending"
_LONG_TASK_TTL = 120             # 秒；待办超过这个时长视为作废，避免状态卡死
_AUTOREFRESH_MS = 60000          # 正常：60 秒
_AUTOREFRESH_BUSY_MS = 1800000   # 有待办：30 分钟（等价于暂停，但组件仍在 → 不会冻结页面）

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
# ⚠️ 本文件含隐私（手写备注、入选价），**只留在本地容器，禁止提交**（已加入 .gitignore）。
#    巡检需要的脱敏摘要另存为仓库里的 band_watch.json，见 _band_memory_digest()。
BAND_MEMORY_FILE = os.path.join(BASE_DIR, "band_memory.json")
# 策略复盘的批次档案（哪一批选了哪些票、每笔结案结果与归因）。
# ⚠️ 同 band_memory.json 一样含隐私（批次名单、收益率、胜率），**只留本地，禁止提交**。
#    刻意不进 band_watch.json 的脱敏摘要：收益率曲线属于交易绩效，公开了等于公开持仓表现。
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

def _http_get_json(url, params=None, timeout=(5, 10), retries=2):
    """带 UA / 超时 / 重试的 JSON GET。返回 (ok, data, err)。绝不抛异常。"""
    last_err = "未知错误"
    for i in range(max(1, retries)):
        try:
            r = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=timeout)
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
            r = requests.get(url, headers=_HTTP_HEADERS, timeout=timeout)
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
_QQ_APP_HOSTS = ["https://web.ifzq.gtimg.cn", "https://ifzq.gtimg.cn"]

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

def _fetch_kline_qq(code, limit=640):
    """腾讯日线（前复权），主域名失败自动切备用域名。返回 (df|None, 源名, 错误列表)。"""
    errs = []
    for host in _QQ_APP_HOSTS:
        url = f"{host}/appstock/app/fqkline/get?param={code},day,,,{limit},qfq"
        ok, js, err = _http_get_json(url, timeout=(5, 10), retries=2)
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

def _fetch_kline_sina(code, limit=640):
    """新浪日线备用源（不复权）。腾讯对北交所不提供日线，此处是重要兜底。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={limit}")
    ok, js, err = _http_get_json(url, timeout=(5, 10), retries=2)
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
    """腾讯 → 新浪 → 磁盘缓存。成功返回 (df, 源描述)；全部失败抛 DataFetchError。"""
    attempts = []
    for fn in (_fetch_kline_qq, _fetch_kline_sina):
        try:
            df, src, errs = fn(code)
            attempts.extend(errs)
            if df is not None and not df.empty:
                _cache_kline(code, df)
                LAST_FETCH_DIAG[code] = {"source": src, "ts": now_cn_str(), "attempts": attempts}
                return df, src
        except Exception as e:
            attempts.append((fn.__name__, f"未预期异常 {type(e).__name__}: {str(e)[:100]}"))
    df_cache, ts = _load_cached_kline(code)
    if df_cache is not None:
        attempts.append(("本地缓存", f"已回退到 {ts} 的缓存数据"))
        LAST_FETCH_DIAG[code] = {"source": f"本地缓存({ts})", "ts": ts, "attempts": attempts}
        return df_cache, f"本地缓存({ts})"
    LAST_FETCH_DIAG[code] = {"source": "无", "ts": "", "attempts": attempts}
    raise DataFetchError(code, attempts)

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
    st.checkbox("本页也参与巡检（容易与云端重复推送，建议关闭）", value=False, key="enable_page_monitor")
    if is_trading_time():
        st.success("✅ 当前处于交易时段，监控运行中")
    else:
        st.info("⏸ 非交易时段，暂监控不做推送")
    if st.session_state.get('enable_page_monitor', False):
        st.success(f"✅ 网页巡检已开启，监控 {len(st.session_state.stock_list)} 只自选股")
    else:
        st.info("💡 网页巡检已关闭，推送由云端定时任务负责（无需打开本页）")
    if st.session_state.get('last_monitor_time'):
        st.caption(f"上次巡检: {st.session_state.last_monitor_time}（{st.session_state.get('last_monitor_count', 0)} 只）")
    if st.button("🧪 发送测试推送", use_container_width=True, key="test_notify"):
        if st.session_state.get('send_key'):
            ok = send_wechat_notification(st.session_state.send_key, "【测试】做T助手连通性测试", f"北京时间 {now_cn_str('%Y-%m-%d %H:%M:%S')}\n收到这条说明微信推送链路正常。")
            st.toast("✅ 测试推送已发送" if ok else "❌ 发送失败，检查 SendKey", icon="🔔")
        else:
            st.warning("请先填写 SendKey")
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

# ================= 8. 核心策略判定 =================
def generate_report_and_advice(df_daily, df_minute, deviation, market_change):
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
    intraday_high_predict = 0; intraday_low_predict = 0
    if not df_minute.empty:
        cur_price = df_minute['Price'].iloc[-1]; day_high = df_minute['Price'].max(); day_low = df_minute['Price'].min()
        atr = latest['ATR14'] if not pd.isna(latest['ATR14']) else cur_price * 0.02
        now_time = now_cn().time()
        if now_time < time(9, 30): intraday_high_predict = round(latest['Close'] + atr * 0.5, 3); intraday_low_predict = round(latest['Close'] - atr * 0.5, 3)
        elif now_time > time(15, 0): intraday_high_predict = day_high; intraday_low_predict = day_low
        else:
            _today = now_cn().date()
            current_dt = datetime.combine(_today, now_time); start_am = datetime.combine(_today, time(9, 30)); end_am = datetime.combine(_today, time(11, 30)); start_pm = datetime.combine(_today, time(13, 0))
            passed_minutes = (current_dt - start_am).total_seconds() / 60 if current_dt <= end_am else 120 + (current_dt - start_pm).total_seconds() / 60
            passed_minutes = max(passed_minutes, 1); remaining_minutes = max(240 - passed_minutes, 0)
            if passed_minutes > 10: realized_volatility_per_min = (day_high - day_low) / passed_minutes; remaining_range = realized_volatility_per_min * remaining_minutes
            else: remaining_range = atr * 0.5
            dynamic_offset = min(remaining_range, atr) * 0.6
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
    if intraday_high_predict > 0: predict_text = f"**今日预估波动区间（动态调整）**：\n- 预估最高点：**{intraday_high_predict:.3f}**（基于实时波动率与剩余时间）\n- 预估最低点：**{intraday_low_predict:.3f}**（基于实时波动率与剩余时间）\n- 当前价格：**{df_minute['Price'].iloc[-1]:.3f}**\n\n⚠️ 该预测随盘中行情变化而动态更新，仅供参考，不构成操作依据。"
    else: predict_text = "数据不足，无法预测日内极值。"
    context = f"""【当前盘面实时数据】\n标的: {current_name} ({symbol})\n当前价格: {latest['Close']:.3f}\n日线趋势: {trend}\n是否企稳: {'是' if is_steady else '否'}\n做T方向: {direction}\n关键支撑: {support:.3f}\n关键压力: {resistance:.3f}\n大盘涨跌幅: {market_change:.2f}%\n当前最优买点: {best_buy}\n当前最优卖点: {best_sell}\n日内预估最高: {intraday_high_predict:.3f}\n日内预估最低: {intraday_low_predict:.3f}"""
    return report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest

# ================= 9. 全天候监控所有自选股 =================
def monitor_all_watchlist(send_key, market_change):
    if not send_key or not is_trading_time(): return []
    watchlist = st.session_state.get('stock_list', [])
    if not watchlist: return []
    fired = []
    for sym in watchlist:
        try:
            sym_code = _get_code(sym); df_min = get_minute_data(sym_code)
            if df_min is None or df_min.empty: continue
            dev = dynamic_deviation(df_min)
            sig = compute_intraday_signals(df_min, dev)
            if not sig: continue
            buy_pts = sig['buy']; sell_pts = sig['sell']
            sym_name = get_stock_name(sym)
            if not buy_pts.empty:
                best_row = buy_pts.loc[buy_pts['Price'].idxmin()]; buy_price = float(best_row['Price']); buy_time = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
                if market_change >= -1.0 and should_notify(sym, 'buy', buy_price):
                    ok = send_wechat_notification(send_key, f"【买点提醒】{sym_name}", f"股票：{sym_name} ({sym})\n时间：{buy_time}\n价格：{buy_price:.3f}\n依据：回踩均价线缩量，MACD 拐头向上")
                    if ok: mark_notified(sym, 'buy', buy_price); fired.append(f"🔴 {sym_name} 买点 {buy_price:.3f}")
            if not sell_pts.empty:
                best_row = sell_pts.loc[sell_pts['Price'].idxmax()]; sell_price = float(best_row['Price']); sell_time = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
                if should_notify(sym, 'sell', sell_price):
                    ok = send_wechat_notification(send_key, f"【卖点提醒】{sym_name}", f"股票：{sym_name} ({sym})\n时间：{sell_time}\n价格：{sell_price:.3f}\n依据：冲高乖离均价线放量，MACD 拐头向下")
                    if ok: mark_notified(sym, 'sell', sell_price); fired.append(f"🟢 {sym_name} 卖点 {sell_price:.3f}")
        except Exception as e:
            _log(f"monitor_all_watchlist/{sym}", e)
            continue
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
    """波段分析用日线（前复权，至少 60 条）。复用统一容错层：腾讯多域名 → 新浪兜底。"""
    code = f"{_quote_prefix(symbol)}{str(symbol).strip()}"
    for fn in (_fetch_kline_qq, _fetch_kline_sina):
        try:
            df, _src, _errs = fn(code, limit=300)
            if df is not None and len(df) >= 60:
                return df
        except Exception as e:
            _log("_get_daily_history", e)
            continue
    return None

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
    if len(highs) >= 2:
        h1, h2 = highs.iloc[-2], highs.iloc[-1]
        if h2['High'] > h1['High'] and h2['macd'] < h1['macd']:
            top_divergence = True

    below_support = current < ma20

    high_250 = float(close.tail(250).max()); low_250 = float(close.tail(250).min())
    position_pct = 50.0 if high_250 <= low_250 else (current - low_250) / (high_250 - low_250) * 100

    return {
        'current': current, 'ma20': ma20, 'ma60': ma60,
        'platform_high': platform_high, 'platform_low': platform_low,
        'platform_range_pct': platform_range_pct, 'breakout': breakout,
        'vol_ratio': vol_ratio, 'volume_expansion': volume_expansion,
        'macd': float(macd.iloc[-1]), 'macd_golden': bool(diff.iloc[-1] > dea.iloc[-1] and diff.iloc[-2] <= dea.iloc[-2]),
        'top_divergence': top_divergence, 'below_support': below_support,
        'position_pct': position_pct, 'lookback': lookback,
    }

def _band_status(metrics):
    """根据指标返回波段状态和对应颜色。"""
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
        'VolRatio': m['vol_ratio'], 'Position250': round(m['position_pct'], 1),
        'Reasons': ' | '.join(reasons), 'LastClose': m['current'],
        'Breakout': bool(m['breakout']), 'VolumeExpansion': bool(m['volume_expansion']),
        'TopDivergence': bool(m['top_divergence']), 'BelowSupport': bool(m['below_support']),
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

def screen_band_stocks(max_results=20, max_deep_scan=600, custom_codes=None):
    """波段选股主函数：全市场扫描或基于自定义股票列表，筛选突破平台+放量的波段启动股，并预警结束信号。"""
    progress = st.progress(0, text="正在获取股票列表...")
    # 原始结果（list[dict]）留给「波段记忆」做自动入册；DataFrame 只用于展示
    st.session_state.band_last_raw = []

    candidates = []; all_stocks = []
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
            change_pct = _safe_float(s.get("f3"))
            if abs(change_pct) >= 9.5:
                continue
            candidates.append({'Code': code_, 'Name': name, 'Price': price, 'ChangePct': change_pct,
                               'TotalMv': total_mv, 'MainFlow': _safe_float(s.get("f62")) / 1e8})

    total_cand = len(candidates)
    if not candidates:
        progress.empty()
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
        st.session_state.scan_stats = f"拉取 {len(all_stocks)} 只 → 初筛 {total_cand} 只 → 深度分析 {len(to_scan)} 只（失败 {no_data} 只）→ 无有效结果"
        return pd.DataFrame()

    # 排序：结束预警优先（需要关注），然后按分数降序
    status_order = {'顶背离预警': 0, '跌破支撑': 1, '波段启动确认': 2, '波段进行中': 3, '波段未形成': 4}
    scored_sorted = sorted(scored, key=lambda x: (status_order.get(x['Status'], 5), -x['Score']))
    st.session_state.scan_stats = (f"拉取 {len(all_stocks)} 只 → 初筛 {total_cand} 只 → "
                                   f"深度分析 {len(to_scan)} 只（K线失败 {no_data} 只）→ 有效 {len(scored)} 只")
    df = pd.DataFrame(scored_sorted).head(max_results).reset_index(drop=True)
    df.index = df.index + 1
    st.session_state.band_last_raw = scored_sorted   # 供记忆层自动入册（不受 max_results 截断影响）
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
# ⚠️ 这个常量**只用于「更新已入册的条目」**；新建条目只认 BAND_ENTRY_STATUSES。
#    2026-09-19 之前它被直接当成入册条件，于是全市场扫描时几百只「跌破支撑」
#    被灌进记忆（实测 460 只/458 只是预警）。改回之前先读 band_memory_record 的说明。
BAND_AUTO_MEMO_STATUSES = BAND_ENTRY_STATUSES + BAND_ALERT_STATUSES
# 状态层级：用于判断是「变好」还是「恶化」，从而决定要不要打扰用户
BAND_STATUS_LEVEL = {'波段未形成': 0, '波段进行中': 1, '波段启动确认': 2,
                     '跌破支撑': 3, '顶背离预警': 4}
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
# 提交到仓库的是「脱敏摘要」；完整记忆（含备注/入选价）只留在本地容器。
# ⚠️ 本仓库是 public，所以两个文件职责必须严格区分，不要把 band_memory.json 提交上去。
GITHUB_WATCH_PATH = "band_watch.json"


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
            node = {
                "code": code, "name": r.get('Name') or code,
                "added_at": now_cn_str(), "added_price": float(r.get('Price') or 0.0),
                "added_status": status, "added_source": source,
                "batch_id": batch_id or "",
                "entry_context": _band_entry_context(r, bench_above),
                "snapshot": {
                    "score": r.get('Score', 0), "ma20": r.get('MA20', 0.0),
                    "ma60": r.get('MA60', 0.0),
                    "platform_high": r.get('PlatformHigh', 0.0),
                    "vol_ratio": r.get('VolRatio', 0.0),
                    "position250": r.get('Position250', 0.0),
                    "reasons": r.get('Reasons', ''),
                },
                "note": "", "closed": False, "alerts": {}, "history": [],
            }
            mem['stocks'][code] = node
            added.append(code)
            _band_memory_apply(node, r, event=f"入选记忆（{source}）")
        else:
            if status not in BAND_AUTO_MEMO_STATUSES:
                continue
            # 已记住的：后续扫描发现状态变化（例如 启动确认 → 跌破支撑）也要记下来
            _band_memory_apply(node, r, event=f"扫描刷新（{source}）")
    return added

def band_memory_purge(mem, mode="never_started"):
    """清理记忆，返回 (mem, 删除数量)。

    mode="never_started"：只删「入选时不是波段启动确认」的条目 —— 也就是旧规则下
        由「跌破支撑 / 顶背离预警」误建的那些。**带备注的条目一律保留**，
        因为写了备注说明你是主动关注它的，不能当噪音清掉。
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

_BENCH_CACHE = {}

def _get_index_history(full_symbol, limit=300):
    """指数日线。**必须传完整符号**（如 sh000300）——指数不能走 _quote_prefix，
    因为 000300 会被判成 sz000300（那是个不存在的股票代码）。仍复用统一取数层。"""
    for fn in (_fetch_kline_qq, _fetch_kline_sina):
        try:
            df, _src, _errs = fn(full_symbol, limit=limit)
            if df is not None and len(df) >= 60:
                return df
        except Exception as e:
            _log(f"_get_index_history/{full_symbol}", e)
            continue
    return None

def _bench_history(force=False):
    """基准（沪深300）日线，按自然日缓存，同一天内多处调用只取一次。
    取数失败**不写缓存**，否则一次网络抖动会让当天所有复盘都拿不到基准。"""
    key = now_cn().strftime('%Y-%m-%d')
    if not force and _BENCH_CACHE.get('key') == key and _BENCH_CACHE.get('df') is not None:
        return _BENCH_CACHE['df']
    df = _get_index_history(REVIEW_BENCH_SYMBOL)
    if df is not None:
        _BENCH_CACHE['key'] = key
        _BENCH_CACHE['df'] = df
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

# ============ 波段摘要加密（可选，用于隐藏「关注了哪些股票」）============
# 背景：本仓库是 public。脱敏摘要虽然不含备注/入选价，但仍含股票代码清单。
# 为什么不直接改成 Private：public 仓库的 Actions 完全免费、不限分钟；private 仓库只有
# 2000 分钟/月，而本项目「定时巡检 + 保活」约需 7800 分钟/月，额度烧穿后 GitHub 会
# **静默停掉**定时任务 —— 那样你反而收不到任何提醒。所以正确做法是保持 public，
# 把摘要文件**加密**后再提交：Streamlit Secrets 与 GitHub Actions Secrets 各加一个
# 同名的 BAND_KEY（两边值必须一致）。未配置 BAND_KEY 时保持明文（与历史行为一致），
# 页面会明确提示「摘要未加密」。

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
    """从完整记忆里提取「可公开」的最小摘要，用于提交到仓库。

    ⚠️ 为什么必须脱敏：本项目的 GitHub 仓库是 **public** 的。完整记忆里包含
    用户手写的备注（例如「14.20 买了2成」这一类的持仓信息）和入选价、扫描快照，
    这些东西一旦提交就等于公开自己的持仓思路。所以只同步巡检真正需要的字段：
    代码、名称、当前状态、状态时间、轨迹（时间/状态/当时市价）、去重记录。
    市价是公开行情数据，不涉及隐私；备注与入选价永远留在本地容器。"""
    digest = {"version": BAND_MEMORY_VERSION, "updated_at": now_cn_str(), "stocks": {}}
    for code, node in (mem.get('stocks') or {}).items():
        if not isinstance(node, dict):
            continue
        hist = []
        for h in (node.get('history') or []):
            if not isinstance(h, dict):
                continue
            hist.append({k: h.get(k) for k in ("ts", "status", "price", "ma20", "event")
                         if h.get(k) is not None})
        digest["stocks"][code] = {
            "code": node.get("code", code),
            "name": node.get("name", ""),
            "status": node.get("status", ""),
            "status_ts": node.get("status_ts", ""),
            "last_check": node.get("last_check", ""),
            "closed": bool(node.get("closed")),
            "alerts": dict(node.get("alerts") or {}),
            "history": hist,
        }
    return digest

def band_memory_merge_digest(local, digest):
    """把仓库里的摘要合并回本地完整记忆。

    只吸收「状态 / 状态时间 / 轨迹 / 归档」这些巡检能产出的信息；
    **备注、入选价、扫描快照一律保留本地值**，绝不被云端覆盖。
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
            # 云端有、本地没有（一般是容器重启后本地丢过）：按摘要重建一条最小记录
            node = {
                "code": code, "name": r_node.get("name") or code,
                "added_at": r_node.get("status_ts") or "",
                "added_price": None, "added_status": r_node.get("status") or "",
                "added_source": "云端巡检", "snapshot": {}, "note": "",
                "closed": bool(r_node.get("closed")),
                "status": r_node.get("status") or "", "status_ts": r_node.get("status_ts") or "",
                "price": None, "history": [],
            }
            for h in (r_node.get("history") or []):
                if isinstance(h, dict):
                    node["history"].append(dict(h))
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
    """把「脱敏摘要」提交到仓库。返回 (ok, msg)。

    merge_remote=True 时先拉取云端摘要并合并（原地更新 mem），这样巡检脚本写入的
    状态变化不会被网页端覆盖。冲突（409/422）时重取 sha 再试一次。

    ⚠️ 提交的是 `_band_memory_digest(mem)`，**不含备注/入选价**（仓库是 public 的）。
    若配置了 BAND_KEY，还会再用 Fernet 整段加密后才提交，连代码清单也一并隐藏。"""
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
SAMPLE_VOL_UNIT = {"qq": 100.0, "sina": 1.0}
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
            'VolRatio': m['vol_ratio'], 'Position250': round(m['position_pct'], 1),
            'Reasons': ' | '.join(reasons), 'LastClose': m['current'],
            'Breakout': bool(m['breakout']), 'VolumeExpansion': bool(m['volume_expansion']),
            'TopDivergence': bool(m['top_divergence']), 'BelowSupport': bool(m['below_support']),
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
    否则成交额会差 100 倍（腾讯按手、新浪按股）。"""
    for fn, unit in ((_fetch_kline_qq, SAMPLE_VOL_UNIT['qq']),
                     (_fetch_kline_sina, SAMPLE_VOL_UNIT['sina'])):
        try:
            df, _src, _errs = fn(_get_code(code), limit=limit)
            if df is not None and len(df) >= SAMPLE_MIN_BARS:
                return df, unit
        except Exception as e:
            _log(f"_sample_fetch_kline/{code}", e)
            continue
    return None, 1.0


def band_samples_harvest(codes, source='backfill', lookback_days=0, fetch=None,
                         bench_df=None, names=None, market_meta=None, progress=None,
                         max_codes=0):
    """采样主引擎：对一批代码逐日重放（或只看最新一天），产出样本行。

    lookback_days=0 → 只评估最后一根K线（前瞻采集用）
    lookback_days=N → 回看最近 N 个交易日（历史回填用）

    返回 (rows, report)。report 记下成功/失败/跳过的只数，失败原因逐代码留痕 ——
    取数失败必须可见，否则"样本怎么这么少"会变成一个查不出来的谜。
    """
    fetch = fetch or _sample_fetch_kline
    names = names or {}
    rows = []
    report = {"codes": 0, "fetched": 0, "failed": 0, "stale": 0,
              "no_bench": bench_df is None,
              "evaluated": 0, "kept": 0, "fail_examples": []}
    cl = list(codes)
    if max_codes and max_codes > 0:
        cl = cl[:max_codes]
    for k, code in enumerate(cl):
        code = str(code).zfill(6)
        report["codes"] += 1
        if progress and (k % 20 == 0):
            try:
                progress(k, len(cl), code)
            except Exception as e:
                _log("band_samples_harvest/progress", e)
        try:
            df, unit = fetch(code, 700)
        except Exception as e:
            df, unit = None, 1.0
            _log(f"band_samples_harvest/fetch/{code}", e)
        if df is None or len(df) < SAMPLE_MIN_BARS + 1:
            report["failed"] += 1
            if len(report["fail_examples"]) < 8:
                report["fail_examples"].append(code)
            continue
        # ★ 陈旧序列剔除：取数只保证"最近 700 根"，退市/长期停牌的票会整段落在很多年前，
        #   长度校验拦不住它们。判据用最后一根K线距今天的自然日数，宁可少收也不收僵尸样本。
        try:
            _last = pd.Timestamp(df['Date'].iloc[-1])
            if not pd.isna(_last):
                if (pd.Timestamp(now_cn().date()) - _last.normalize()).days > SAMPLE_MAX_STALE_DAYS:
                    report["stale"] += 1
                    continue
        except Exception as e:
            _log(f"band_samples_harvest/stale/{code}", e)
        report["fetched"] += 1
        mask = _sample_prefilter(df)
        n = len(df)
        if lookback_days and lookback_days > 0:
            start = max(SAMPLE_MIN_BARS, n - int(lookback_days))
        else:
            start = n - 1
        prev_tier = None
        for i in range(start, n):
            must = bool(mask[i]) if i < len(mask) else False
            if lookback_days and lookback_days > 0 and not must:
                # 非「必算」日（趋势/基准对照）按 1/N 确定性抽样，其余直接跳过省下 3ms/天
                if not _sample_hash_hit(code, df['Date'].iloc[i], SAMPLE_CTRL_EVERY):
                    continue
            report["evaluated"] += 1
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
            rows.append(row)
            report["kept"] += 1
    if progress:
        try:
            progress(len(cl), len(cl), '')
        except Exception as e:
            _log("band_samples_harvest/progress_done", e)
    return rows, report


# ---------------- 存储：本地 JSONL.gz（原始行是唯一事实来源，聚合一律从它现算）----------------

def load_band_samples(path=None, cap=None):
    """读样本库，返回 {key: row}。文件不存在返回空 dict（首次运行是正常情况，不是错误）。"""
    p = path or SAMPLE_FILE
    out = {}
    if not os.path.exists(p):
        return out
    try:
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
    except Exception as e:
        _log("load_band_samples", e)
        return {}
    return out


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
    rows = list(rows_map.values())
    if not rows:
        st.info("样本库还是空的。两种攒法：\n\n"
                "1. **点下面的「采集今日全市场样本」** —— 按今天的真实判断收一批前瞻样本"
                "（这是唯一能当成绩用的那类）；\n"
                "2. **在本机跑一次历史回填** —— 立刻拿到数千条重放样本，"
                "脚本见 `_debug_probe/backfill_samples.py`。")
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
    """「采集今日全市场样本」这一个动作的 UI + 落盘。空库与非空库共用，避免两处逻辑漂移。"""
    _c1, _c2 = st.columns([1, 2])
    with _c1:
        _do = long_button("📥 采集今日全市场样本", key="sample_forward_btn",
                          use_container_width=True)
    with _c2:
        st.caption("采集会扫描全市场（含创业板/科创板/北交所），只记信号与对照，不发推送、"
                   "不写进你的记忆名单。样本只存在本地容器，不会上传仓库。")
    if not _do:
        return
    _sample_progress_widget.bar = None
    try:
        with st.spinner("正在扫描全市场并采集样本（约 1～3 分钟）..."):
            rows_new, rep = band_samples_harvest_forward(progress_cb=_sample_progress_widget)
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
    跑全市场要几十分钟，跑 5 只一分钟就够。正式采集必须留空（limit=0）。

    ★ 两条走过弯路的地方：
      ① 北交所不在东财股票列表分页里，必须单独按 `m:0+t:81+s:2048` 补一次，
         否则「不限板块」是假的 —— 北交所的波动结构恰恰是最不该被预先排除的。
      ② 基准（沪深300）必须一起传进采样引擎。不传的话 band_outcome_compute 拿不到
         bench_df → excess_pct 恒为 None → 分桶报表里所有「相对基准」全是空的，
         这批样本就永远回答不了「它到底有没有产生超额」——采集全白做。
    """
    report = {"codes": 0, "fetched": 0, "failed": 0, "kept": 0, "fail_examples": []}
    meta = {}
    names = {}
    try:
        raw = []
        for pn in range(1, 41):
            page = fetch_market_page(pn)
            if not page:
                break
            raw.extend(page)
        if not raw:
            report["error"] = "全市场接口暂不可用（非交易时段/网络限制）"
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
    st.caption("🔒 备注与入选价**只存在本地容器**；同步到仓库的只是「代码 + 状态」摘要"
               "（你的仓库是公开的，所以隐私字段一律不上传）。")

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
    c2.metric("结束预警", f"{stats['alert']} 只", help="顶背离预警 / 跌破支撑，需要处理")
    c3.metric("波段启动", f"{stats['entry']} 只")
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
    with st.expander(f"🧹 清理记忆（{_junk} 只可清理）", expanded=False):
        st.markdown(f"""
        2026-09-19 之前的版本允许用「跌破支撑 / 顶背离预警」**新建**记忆条目，
        全市场扫描一次就会把几百只跌破 20 日线的股票灌进来。现在规则已改为
        **只有「波段启动确认」能入册**，这里用来清理已经堆下来的存量。

        - 可清理：**{_junk}** 只（入选时不是「波段启动确认」，且你没写过备注）
        - 会保留：**{_noted}** 只（你写过备注，说明是主动关注的）
        - 正常入册的（启动确认）一律不动
        """)
        _pend = st.session_state.get('bandmem_purge_pending')
        if _pend == 'junk':
            st.warning(f"确认删除这 {_junk} 只？不可恢复，建议先点上方「📥 下载备份」留底。")
        elif _pend == 'all':
            st.error("确认**清空全部记忆**？所有备注与轨迹都会一起删除，不可恢复。")
        if _pend:
            _k1, _k2 = st.columns(2)
            if _k1.button("✅ 确认", key="bandmem_purge_ok", use_container_width=True):
                _mode = 'all' if _pend == 'all' else 'never_started'
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
            _b1, _b2 = st.columns(2)
            if _b1.button(f"🧹 清理这 {_junk} 只", key="bandmem_purge_junk",
                          use_container_width=True, disabled=(_junk == 0)):
                st.session_state['bandmem_purge_pending'] = 'junk'
                st.rerun()
            if _b2.button("🗑️ 清空全部记忆", key="bandmem_purge_all", use_container_width=True):
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
    order = sorted(mem['stocks'].values(),
                   key=lambda n: (bool(n.get('closed')),
                                  -BAND_STATUS_LEVEL.get(n.get('status'), 0),
                                  str(n.get('added_at', ''))))
    for node in order:
        code = node.get('code') or '?'
        cur = node.get('status') or '未知'
        col, icon = _band_status_badge(cur)
        add_col, add_icon = _band_status_badge(node.get('added_status'))
        changed = bool(node.get('added_status')) and node.get('added_status') != cur
        title = (f"{icon} {node.get('name')}（{code}）　{cur}"
                 + ("　⚠️ 状态已变化" if changed else "")
                 + ("　🗄️ 已归档" if node.get('closed') else ""))
        with st.expander(title, expanded=(cur in BAND_ALERT_STATUSES and not node.get('closed'))):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("当前状态", cur)
            m2.metric("入选时", f"{add_icon} {node.get('added_status') or '—'}")
            m3.metric("现价", _fmt_price(node.get('price')))
            m4.metric("入选价", _fmt_price(node.get('added_price')))
            st.caption(
                f"入选时间：{node.get('added_at') or '—'}　|　来源：{node.get('added_source') or '—'}"
                f"　|　最近检查：{node.get('last_check') or '未检查'}"
                f"　|　20日线：{_fmt_price(node.get('ma20'))}")
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
            if n3.button("🗑️ 从记忆中删除", use_container_width=True, key=f"bandmem_del_{code}"):
                mem['stocks'].pop(code, None)
                save_band_memory(mem)
                band_memory_push_github(mem)
                st.session_state.band_memory_sync_msg = f"已删除 {node.get('name')}（{code}）"
                st.rerun()

    with st.expander("☁️ 云端推送（微信）怎么配", expanded=False):
        st.markdown("""
        记忆里「代码 + 当前状态」的摘要会被提交到仓库，GitHub Actions 上的巡检脚本
        每 5 分钟读一次，发现状态**变差**（→ 顶背离预警 / 跌破支撑）就推送微信，
        你关掉本页也能收到。

        **隐私说明（重要）**：本仓库是 **public**，所以提交上去的只有
        代码、名称、当前状态、状态时间和轨迹，**不含**你写的备注、入选价和扫描快照。

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


def ai_band_picker_ui():
    st.markdown("---")
    st.header("🌊 波段做T选股助手")
    st.caption("筛选突破整理平台+放量的波段启动股，并预警顶背离/跌破支撑等波段结束信号（排除科创/创业板/北交所/ST）")

    with st.expander("📖 选股逻辑说明", expanded=False):
        st.markdown("""
        **波段开始条件**：
        1. 股价突破近 60 日整理平台（收盘价接近或创 60 日新高）。
        2. 成交量明显放大（5 日均量 ≥ 20 日均量 1.5 倍，或当日量 ≥ 20 日均量 1.5 倍）。
        3. 站上 20 日线，20 日线在 60 日线上方更佳。
        4. MACD 金叉或红柱，资金流入加分。

        **波段结束提醒**：
        - 顶背离：股价创新高，但 MACD 未创新高。
        - 跌破支撑：收盘价跌破 20 日线。

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

    scan_label = "🔍 扫描波段启动股"
    if scan_scope == "仅手动自选":
        scan_label = "🔍 扫描手动自选"
    elif scan_scope == "指定板块":
        scan_label = "🔍 扫描指定板块"
    if long_button(scan_label, key="scan_stocks", type="primary", use_container_width=True):
        try:
            with st.spinner("正在拉取行情并分析波段状态..."):
                st.session_state.scan_results = screen_band_stocks(custom_codes=custom_codes)
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
            for _, r in df_r.iterrows():
                chg_color = "#ff4b4b" if r['ChangePct'] >= 0 else "#00cc66"
                st.markdown(f"""
                <div style="background:#1e1e2e; border-radius:10px; padding:14px 18px; margin-bottom:10px; border-left:4px solid {r['StatusColor']};">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                        <div><span style="font-size:18px; font-weight:bold; color:#f0f2f6;">{r['Name']}</span>
                        <span style="color:#89b4fa; font-size:14px; margin-left:8px;">({r['Code']})</span>
                        <span style="color:{chg_color}; font-size:14px; margin-left:10px;">{r['ChangePct']:+.2f}%</span></div>
                        <div style="text-align:right;"><span style="color:{r['StatusColor']}; font-size:16px; font-weight:bold;">{r['Status']}</span></div>
                    </div>
                    <div style="margin-top:8px; color:#c9d1d9; font-size:13px; line-height:1.8;">
                        <span style="color:#89b4fa;">价格:</span> {r['Price']:.2f} | <span style="color:#89b4fa;">20日线:</span> {r['MA20']:.2f} | <span style="color:#89b4fa;">平台上沿:</span> {r['PlatformHigh']:.2f} | <span style="color:#89b4fa;">量比:</span> {r['VolRatio']:.1f} | <span style="color:#89b4fa;">250日分位:</span> {r['Position250']:.0f}%
                    </div>
                    <div style="margin-top:6px; color:#f9e2af; font-size:13px;">📋 {r['Reasons']}</div>
                </div>
                """, unsafe_allow_html=True)

    # 波段记忆面板（记住选过的票 + 跟踪状态变化 + 云端推送）
    try:
        band_memory_ui()
    except Exception as e:
        _log("band_memory_ui", e)
        st.warning("波段记忆面板渲染出错（已拦截，不影响上方选股功能）")
        with st.expander("查看错误详情"):
            st.code(traceback.format_exc(), language="python")

    # 策略复盘面板（批次 / 结案 / 归因 —— 只统计，不改选股参数）
    try:
        band_review_ui()
    except Exception as e:
        _log("band_review_ui", e)
        st.warning("策略复盘面板渲染出错（已拦截，不影响其他功能）")
        with st.expander("查看错误详情"):
            st.code(traceback.format_exc(), language="python")

    # 逻辑有效性验证面板（机器自采样本 —— 与上面的记忆名单完全分开）
    try:
        band_sample_ui()
    except Exception as e:
        _log("band_sample_ui", e)
        st.warning("逻辑验证面板渲染出错（已拦截，不影响其他功能）")
        with st.expander("查看错误详情"):
            st.code(traceback.format_exc(), language="python")

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
            st.warning(f"⚠️ 腾讯主源不可用，本次日线来自 **{_daily_src}**。价格口径可能与实时行情略有差异，"
                       f"回踩/压力位判断请以券商行情为准。")
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
    report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest = generate_report_and_advice(df_daily, df_minute, actual_deviation, market_change)
    if is_trading_time() and st.session_state.get('send_key') and st.session_state.get('enable_page_monitor', False):
        fired = monitor_all_watchlist(st.session_state.send_key, market_change)
        st.session_state.last_monitor_time = now_cn_str('%H:%M:%S')
        st.session_state.last_monitor_count = len(st.session_state.get('stock_list', []))
        for f in fired: st.toast(f, icon="🔔")
    elif st.session_state.get('send_key') and not is_trading_time():
        st.session_state.last_monitor_count = 0
    st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ai-advice-box">🤖 <b>AI 实时建议</b><br>{ai_advice}</div>', unsafe_allow_html=True)
    col_g, col_p = st.columns(2)
    with col_g: st.markdown(f'<div class="guide-box">🎯 <b>今日做T指引</b><br>{t_guide}</div>', unsafe_allow_html=True)
    with col_p: st.markdown(f'<div class="predict-box">📊 <b>日内极值预测</b><br>{predict_text}</div>', unsafe_allow_html=True)
    col_title1, col_btn1 = st.columns([9, 1])
    with col_title1: st.subheader(f"📈 {current_name} ({symbol}) 日线级别走势")
    with col_btn1:
        if st.button("🔄 复位", use_container_width=True, key="reset_daily_chart"): st.session_state.chart_reset_key += 1; st.rerun()
    ma_html = f"""<div class="ma-bar"><span style="color:#ffffff">M5: {latest['MA5']:.3f}</span><span style="color:#ffff00">M10: {latest['MA10']:.3f}</span><span style="color:#ff00ff">M20: {latest['MA20']:.3f}</span><span style="color:#00ff00">M30: {latest['MA30']:.3f}</span><span style="color:#00ccff">年线: {latest['MA250']:.3f}</span></div>"""
    st.markdown(ma_html, unsafe_allow_html=True)
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol, latest, st.session_state.chart_reset_key), use_container_width=True, config=PLOTLY_CONFIG_DAILY)
    st.caption("💡 **放大**：在图上按住左键拖出矩形框，松开即放大该区域（主图与成交量**同步缩放**，在哪个子图上拖都可以）；"
               "右上角工具栏有缩放 / 平移 / 自动缩放 / 重置按钮；双击图表或点「🔄 复位」回到初始视图。")
    col_title2, col_btn2 = st.columns([9, 1])
    with col_title2: st.subheader(f"⏱️ {current_name} ({symbol}) 分时级别走势（同花顺风格）")
    with col_btn2:
        if st.button("🔄 复位", use_container_width=True, key="reset_minute_chart"): st.session_state.chart_reset_key += 1; st.rerun()
    if df_minute is not None and not df_minute.empty:
        st.plotly_chart(plot_minute_chart_ths(df_minute, buy_points, sell_points, symbol, prev_close, st.session_state.chart_reset_key), use_container_width=True, config=PLOTLY_CONFIG_CLEAN)
        st.caption("操作说明：分时图只显示 09:30-15:00 交易时段，锁定缩放。")
    else: st.warning("暂无分时数据")
    ai_band_picker_ui()
    dynamic_pool_ui()
    with st.container():
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
