import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import re
import json
import os
import traceback
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

if HAS_AUTOREFRESH:
    st_autorefresh(interval=60000, key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情，全自选股监控）")
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

def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: pass
    return default

def _save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception: pass

def load_watchlist():
    try:
        raw = st.secrets.get("WATCHLIST", "")
        if raw and isinstance(raw, str):
            codes = [c.strip() for c in raw.split(",") if c.strip()]
            if codes: return codes
    except Exception: pass
    return _load_json(WATCHLIST_FILE, [DEFAULT_STOCK, '515880', '159915'])

def save_watchlist(lst):
    try: _save_json(WATCHLIST_FILE, lst)
    except Exception: pass

def load_config():
    cfg = _load_json(CONFIG_FILE, {})
    try:
        ak = st.secrets.get("DEEPSEEK_API_KEY", "")
        if ak and isinstance(ak, str): cfg['api_key'] = ak
    except Exception: pass
    try:
        sk = st.secrets.get("SERVERCHAN_KEY", "")
        if sk and isinstance(sk, str): cfg['send_key'] = sk
    except Exception: pass
    return cfg

def save_config(cfg):
    try: _save_json(CONFIG_FILE, cfg)
    except Exception: pass

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
        import math
        return int(math.log(max(price, 0.001)) / math.log(1 + pct))
    except Exception: return 0

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

# ================= 4. 辅助函数 =================
@st.cache_data(ttl=3600)
def get_stock_name(symbol):
    prefix = "sh" if symbol.startswith(('5', '6', '9')) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{symbol}"
    try:
        res = requests.get(url, timeout=3); res.encoding = 'gbk'
        if "~" in res.text:
            parts = res.text.split("~")
            if len(parts) > 1: return parts[1].strip('"')
    except Exception: pass
    return symbol

@st.cache_data(ttl=60)
def get_market_status():
    try:
        res = requests.get("https://qt.gtimg.cn/q=sh000001", timeout=3); res.encoding = 'gbk'
        if "~" in res.text:
            parts = res.text.split("~")
            if len(parts) > 32: return float(parts[32])
    except Exception: pass
    return 0.0

def is_trading_time():
    # ✅ 必须用北京时间判断（云服务器是 UTC）
    now = now_cn().time()
    return (time(9, 25) <= now <= time(11, 32)) or (time(12, 58) <= now <= time(15, 2))

def send_wechat_notification(send_key, title, content):
    if not send_key: return False
    try:
        res = requests.post(f"https://sctapi.ftqq.com/{send_key}.send", data={"title": title, "desp": content}, timeout=5)
        return res.status_code == 200
    except Exception: return False

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
def _get_code(symbol): return f"sh{symbol}" if symbol.startswith(('5', '6', '9')) else f"sz{symbol}"
code = _get_code(symbol)

@st.cache_data(ttl=60)
def get_daily_data(code):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,640,qfq"
    try:
        res = requests.get(url, timeout=3).json()
        if res.get("code") == 0:
            kline = res["data"][code].get("qfqday") or res["data"][code].get("day")
            df = pd.DataFrame(kline).iloc[:, :6]
            df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
            df['Date'] = pd.to_datetime(df['Date'])
            for col in ['Open', 'Close', 'High', 'Low', 'Volume']: df[col] = pd.to_numeric(df[col], errors='coerce')
            return df.sort_values('Date').reset_index(drop=True)
    except Exception: return None

@st.cache_data(ttl=60)
def get_minute_data(code):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        res = requests.get(url, timeout=3).json()
        if res.get("code") != 0: return None
        raw = res["data"][code]["data"]["data"]
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
    except Exception: return None

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
    if allow_t == "允许" and df_minute is not None and not df_minute.empty:
        df_min = df_minute.copy(); df_min = df_min[df_min['Time'] <= "1500"]; df_min['Vol_MA5'] = df_min['Volume'].rolling(5).mean()
        # 放宽时间窗口：早盘急跌也可捕捉
        df_min_buy = df_min[(df_min['Time'] >= "0935") & (df_min['Time'] <= "1445")].copy(); df_min_sell = df_min[(df_min['Time'] >= "0930") & (df_min['Time'] <= "1455")].copy()

        # 计算当日高低点及价格位置
        day_high = df_min['Price'].max(); day_low = df_min['Price'].min(); day_range = max(day_high - day_low, 0.001)
        df_min_buy['Price_Position'] = (df_min_buy['Price'] - day_low) / day_range
        df_min_sell['Price_Position'] = (df_min_sell['Price'] - day_low) / day_range

        df_min_buy['MACD_UP'] = df_min_buy['MACD'] > df_min_buy['MACD'].shift(1); df_min_sell['MACD_DOWN'] = df_min_sell['MACD'] < df_min_sell['MACD'].shift(1)
        df_min_buy['MACD_GOLDEN'] = (df_min_buy['DIFF'] > df_min_buy['DEA']) & (df_min_buy['DIFF'].shift(1) <= df_min_buy['DEA'].shift(1))
        df_min_sell['MACD_DEAD'] = (df_min_sell['DIFF'] < df_min_sell['DEA']) & (df_min_sell['DIFF'].shift(1) >= df_min_sell['DEA'].shift(1))
        # 止跌/企稳：当前不再创新低；滞涨：当前不再创新高
        df_min_buy['STOP_FALL'] = (df_min_buy['Price'] >= df_min_buy['Price'].shift(1)) & (df_min_buy['Price'].shift(1) <= df_min_buy['Price'].shift(2))
        df_min_sell['STOP_RISE'] = (df_min_sell['Price'] <= df_min_sell['Price'].shift(1)) & (df_min_sell['Price'].shift(1) >= df_min_sell['Price'].shift(2))

        low_idx = df_min['Price'].idxmin()
        if len(df_min.loc[:low_idx]) > 5:
            recent_low = df_min.loc[low_idx, 'Price']; recent_macd = df_min.loc[low_idx, 'MACD']; prev_lows = df_min[df_min['Price'] < recent_low * 1.005]
            if len(prev_lows) > 0:
                prev_macd = df_min.loc[prev_lows.index[0], 'MACD']
                if recent_macd > prev_macd: divergence_info += " 底背离"
        high_idx = df_min['Price'].idxmax()
        if len(df_min.loc[:high_idx]) > 5:
            recent_high = df_min.loc[high_idx, 'Price']; recent_macd = df_min.loc[high_idx, 'MACD']; prev_highs = df_min[df_min['Price'] > recent_high * 0.995]
            if len(prev_highs) > 0:
                prev_macd = df_min.loc[prev_highs.index[-1], 'MACD']
                if recent_macd < prev_macd: divergence_info += " 顶背离"

        # 买点：方案A 回踩均价线+MACD向上；方案B 接近当日低点+止跌+（MACD向上或缩量）
        buy_cond_a = (df_min_buy['Price'] < df_min_buy['AvgPrice'] * (1 - deviation * 0.7)) & df_min_buy['MACD_UP']
        buy_cond_b = (df_min_buy['Price_Position'] <= 0.30) & df_min_buy['STOP_FALL'] & (df_min_buy['MACD_UP'] | (df_min_buy['Volume'] < df_min_buy['Vol_MA5'] * 0.85))
        buy_cond = buy_cond_a | buy_cond_b
        # 卖点：方案A 冲高乖离均价线+MACD向下；方案B 接近当日高点+滞涨+（MACD向下或放量）
        sell_cond_a = (df_min_sell['Price'] > df_min_sell['AvgPrice'] * (1 + deviation * 0.7)) & df_min_sell['MACD_DOWN']
        sell_cond_b = (df_min_sell['Price_Position'] >= 0.70) & df_min_sell['STOP_RISE'] & (df_min_sell['MACD_DOWN'] | (df_min_sell['Volume'] > df_min_sell['Vol_MA5'] * 1.2))
        sell_cond = sell_cond_a | sell_cond_b
        buy_points = df_min_buy[buy_cond]; sell_points = df_min_sell[sell_cond]
        if market_change < -1.0: buy_warning = " ⚠️大盘暴跌，低置信度！"
        if not buy_points.empty:
            best_row = buy_points.loc[buy_points['Price'].idxmin()]; time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['AvgPrice'] - best_row['Price']) / best_row['AvgPrice'] if best_row['AvgPrice'] > 0 else 0
            is_scheme_a = bool((best_row['Price'] < best_row['AvgPrice'] * (1 - deviation * 0.7)) and (best_row['MACD_UP'] if 'MACD_UP' in best_row else False))
            conf = "低" if market_change < -1.0 else ("高" if dev_pct > deviation * 1.5 or "底背离" in divergence_info else ("中" if dev_pct > deviation * 0.8 or is_scheme_a else "低"))
            b_type = "正T低吸" if direction == "正T" else "反T回补"
            reason = "回踩均价线" if is_scheme_a else "接近日内低点止跌"
            best_buy = f"{time_fmt} | {best_row['Price']:.3f} | {b_type} | {reason}{divergence_info} | 置信度{conf}{buy_warning}"
        if not sell_points.empty:
            best_row = sell_points.loc[sell_points['Price'].idxmax()]; time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
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
        if best_buy != "无有效点":
            buy_price = float(best_buy.split('|')[1].strip()); stop_loss = buy_price * 0.995
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
            df_min = df_min[df_min['Time'] <= "1500"].copy()
            if len(df_min) < 10: continue
            df_min['Vol_MA5'] = df_min['Volume'].rolling(5).mean()
            df_min_buy = df_min[(df_min['Time'] >= "0935") & (df_min['Time'] <= "1445")].copy()
            df_min_sell = df_min[(df_min['Time'] >= "0930") & (df_min['Time'] <= "1455")].copy()
            if df_min_buy.empty or df_min_sell.empty: continue
            df_min_buy['MACD_UP'] = df_min_buy['MACD'] > df_min_buy['MACD'].shift(1)
            df_min_sell['MACD_DOWN'] = df_min_sell['MACD'] < df_min_sell['MACD'].shift(1)
            high_price = df_min['Price'].max(); low_price = df_min['Price'].min(); avg_price = df_min['AvgPrice'].mean()
            dev = max(0.003, min(((high_price - low_price) / avg_price) * 0.5, 0.015)) if avg_price > 0 else 0.008
            df_min_buy['Price_Position'] = (df_min_buy['Price'] - low_price) / max(high_price - low_price, 0.001)
            df_min_sell['Price_Position'] = (df_min_sell['Price'] - low_price) / max(high_price - low_price, 0.001)
            df_min_buy['STOP_FALL'] = (df_min_buy['Price'] >= df_min_buy['Price'].shift(1)) & (df_min_buy['Price'].shift(1) <= df_min_buy['Price'].shift(2))
            df_min_sell['STOP_RISE'] = (df_min_sell['Price'] <= df_min_sell['Price'].shift(1)) & (df_min_sell['Price'].shift(1) >= df_min_sell['Price'].shift(2))
            buy_cond_a = (df_min_buy['Price'] < df_min_buy['AvgPrice'] * (1 - dev * 0.7)) & df_min_buy['MACD_UP']
            buy_cond_b = (df_min_buy['Price_Position'] <= 0.30) & df_min_buy['STOP_FALL'] & (df_min_buy['MACD_UP'] | (df_min_buy['Volume'] < df_min_buy['Vol_MA5'] * 0.85))
            buy_cond = buy_cond_a | buy_cond_b
            sell_cond_a = (df_min_sell['Price'] > df_min_sell['AvgPrice'] * (1 + dev * 0.7)) & df_min_sell['MACD_DOWN']
            sell_cond_b = (df_min_sell['Price_Position'] >= 0.70) & df_min_sell['STOP_RISE'] & (df_min_sell['MACD_DOWN'] | (df_min_sell['Volume'] > df_min_sell['Vol_MA5'] * 1.2))
            sell_cond = sell_cond_a | sell_cond_b
            buy_pts = df_min_buy[buy_cond]; sell_pts = df_min_sell[sell_cond]
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
        except Exception: continue
    return fired

# ================= 10. 波段做T选股助手 =================
# 改动：由"低位启动"改为"突破平台+放量"的波段启动确认，并预警顶背离/跌破支撑等波段结束信号。
# 筛选范围：排除科创/创业板/北交所/ST/退市风险股。
EXCLUDE_PREFIXES = ('688', '300', '301', '8', '4', '92')

_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

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
    # 多 host 容错：当前网络环境下 push2delay 通常更稳定
    hosts = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com",
             "https://7.push2.eastmoney.com", "https://17.push2.eastmoney.com",
             "https://29.push2.eastmoney.com", "https://82.push2.eastmoney.com"]
    for base_url in hosts:
        try:
            res = requests.get(f"{base_url}/api/qt/clist/get", params=params, timeout=8, headers=_REQUEST_HEADERS)
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("data") and data["data"].get("diff"):
                return _diff_to_list(data["data"]["diff"])
        except Exception:
            continue
    return []

def _get_daily_history(symbol):
    """从腾讯获取日线复权数据（波段分析用）。"""
    prefix = "sh" if symbol.startswith(('5', '6', '9')) else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{symbol},day,,,260,qfq"
    try:
        res = requests.get(url, timeout=8, headers=_REQUEST_HEADERS).json()
        if res.get("code") != 0:
            return None
        node = res["data"].get(f"{prefix}{symbol}", {})
        kline = node.get("qfqday") or node.get("day")
        if not kline or len(kline) < 60:
            return None
        df = pd.DataFrame(kline).iloc[:, :6]
        df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
        for col in ['Open', 'Close', 'High', 'Low', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna().sort_values('Date').reset_index(drop=True)
        return df if len(df) >= 60 else None
    except Exception:
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

def _analyze_band(row):
    """单只股票的波段分析，异常只丢该股票。"""
    try:
        df = _get_daily_history(row['Code'])
        if df is None or len(df) < 60:
            return None
        m = _calculate_band_metrics(df)
        if m is None:
            return None

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

        # 只保留有明显波段特征的股票：启动确认、进行中、或出现结束预警
        if score < 20 and not m['breakout'] and not m['top_divergence'] and not m['below_support']:
            return None

        status, color = _band_status(m)
        return {
            'Code': row['Code'], 'Name': row['Name'], 'Price': row['Price'], 'ChangePct': row['ChangePct'],
            'Score': max(0, score), 'Status': status, 'StatusColor': color,
            'MA20': m['ma20'], 'PlatformHigh': m['platform_high'], 'VolRatio': m['vol_ratio'],
            'Position250': round(m['position_pct'], 1), 'Reasons': ' | '.join(reasons)
        }
    except Exception:
        return None

def screen_band_stocks(max_results=20, max_deep_scan=600, custom_codes=None):
    """波段选股主函数：全市场扫描或基于自定义股票列表，筛选突破平台+放量的波段启动股，并预警结束信号。"""
    progress = st.progress(0, text="正在获取股票列表...")

    candidates = []
    if custom_codes:
        # 自定义模式：仅分析用户指定的股票
        seen = set()
        for code_ in custom_codes:
            code_ = str(code_).strip()
            if not code_ or len(code_) != 6 or code_.startswith(EXCLUDE_PREFIXES) or code_ in seen:
                continue
            seen.add(code_)
            name = get_stock_name(code_)
            prefix = "sh" if code_.startswith(('5', '6', '9')) else "sz"
            try:
                res = requests.get(f"https://qt.gtimg.cn/q={prefix}{code_}", timeout=3)
                res.encoding = 'gbk'
                parts = res.text.split('~')
                if len(parts) > 45:
                    price = _safe_float(parts[3])
                    change_pct = _safe_float(parts[32])
                    if price > 0:
                        candidates.append({'Code': code_, 'Name': name, 'Price': price, 'ChangePct': change_pct,
                                           'TotalMv': 0.0, 'MainFlow': 0.0})
            except Exception:
                pass
        progress.progress(12, text=f"已加载自定义列表 {len(candidates)} 只...")
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
            r = future.result()
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
    return df

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
    if st.button(scan_label, type="primary", use_container_width=True, key="scan_stocks"):
        try:
            with st.spinner("正在拉取行情并分析波段状态..."):
                st.session_state.scan_results = screen_band_stocks(custom_codes=custom_codes)
                st.session_state.scan_time = now_cn_str('%Y-%m-%d %H:%M:%S')
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

# ================= 11. 动态股票池系统 =================
@st.cache_data(ttl=180)
def get_market_sentiment():
    sentiment = {'score': 50, 'label': '中性', 'up_count': 0, 'down_count': 0, 'north_flow': 0.0, 'details': []}
    try:
        url = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
        res = requests.get(url, timeout=5); res.encoding = 'gbk'
        for line in res.text.strip().split(';'):
            if '~' not in line: continue
            parts = line.split('~')
            if len(parts) > 32:
                try: sentiment['details'].append(f"{parts[1]}: {float(parts[32]):+.2f}%")
                except (ValueError, IndexError): pass
        try:
            stat_url = "https://push2.eastmoney.com/api/qt/stock/get"
            stat_res = requests.get(stat_url, params={"fltt": "2", "invt": "2", "fields": "f104,f105,f106", "secid": "1.000001"}, timeout=5).json()
            if stat_res.get("data"):
                sentiment['up_count'] = stat_res["data"].get("f104", 0) or 0
                sentiment['down_count'] = stat_res["data"].get("f105", 0) or 0
        except Exception: pass
        market_score = 50.0
        for d in sentiment['details']:
            try: market_score += float(d.split(':')[1].strip().replace('%', '')) * 8
            except Exception: pass
        total = sentiment['up_count'] + sentiment['down_count']
        if total > 0: market_score += (sentiment['up_count'] / total - 0.5) * 30
        sentiment['score'] = max(0, min(100, round(market_score, 1)))
        if sentiment['score'] >= 75: sentiment['label'] = '🔥 极度贪婪'
        elif sentiment['score'] >= 60: sentiment['label'] = '☀️ 偏乐观'
        elif sentiment['score'] >= 40: sentiment['label'] = '☁️ 中性'
        elif sentiment['score'] >= 25: sentiment['label'] = '🌧️ 偏悲观'
        else: sentiment['label'] = '❄️ 极度恐慌'
    except Exception: pass
    return sentiment

@st.cache_data(ttl=600)
def get_industry_prosperity():
    """获取行业板块景气度，带多 host 容错。"""
    industries = {}
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f62", "fs": "m:90+t:2", "fields": "f12,f14,f2,f3,f62"}
    hosts = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com",
             "https://7.push2.eastmoney.com", "https://17.push2.eastmoney.com",
             "https://29.push2.eastmoney.com", "https://82.push2.eastmoney.com"]
    for base_url in hosts:
        try:
            res = requests.get(f"{base_url}/api/qt/clist/get", params=params, timeout=8, headers=_REQUEST_HEADERS)
            if res.status_code != 200:
                continue
            data = res.json()
            if data.get("data") and data["data"].get("diff"):
                for item in _diff_to_list(data["data"]["diff"]):
                    name = item.get("f14", ""); main_flow = item.get("f62", 0) / 1e8; change_pct = item.get("f3", 0)
                    industries[name] = {'score': round(min(100, max(0, 50 + main_flow * 2 + change_pct * 3)), 1), 'change_pct': change_pct, 'main_flow': round(main_flow, 2)}
                break
        except Exception:
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
    except Exception:
        pass
    return hot

@st.cache_data(ttl=900)
def get_stock_full_data(symbol):
    """获取个股完整数据，带多 host 容错。"""
    try:
        prefix = "sh" if symbol.startswith(('5', '6', '9')) else "sz"
        secid = f"{'1' if prefix == 'sh' else '0'}.{symbol}"
        params = {"fltt": "2", "invt": "2", "fields": "f43,f57,f58,f9,f23,f37,f45,f46,f48,f50,f62,f116,f117,f127,f168", "secid": secid}
        hosts = ["https://push2delay.eastmoney.com", "https://push2.eastmoney.com",
                 "https://7.push2.eastmoney.com", "https://17.push2.eastmoney.com",
                 "https://29.push2.eastmoney.com", "https://82.push2.eastmoney.com"]
        for base_url in hosts:
            try:
                res = requests.get(f"{base_url}/api/qt/stock/get", params=params, timeout=6, headers=_REQUEST_HEADERS)
                if res.status_code != 200:
                    continue
                d = res.json().get("data") or {}
                if not d:
                    continue
                def sf(v): return float(v) if v not in (None, "-", "") else None
                return {'pe': sf(d.get('f9')), 'pb': sf(d.get('f23')), 'roe': sf(d.get('f37')), 'profit': sf(d.get('f45')), 'industry': d.get('f127') or "", 'main_flow': sf(d.get('f62')), 'total_mv': sf(d.get('f116')), 'circ_mv': sf(d.get('f117')), 'turnover': sf(d.get('f168')), 'vol_ratio': sf(d.get('f50'))}
            except Exception:
                continue
        return None
    except Exception: return None

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
        except Exception: pass
        return out[:limit]

    candidates = _collect_candidates(max_candidates)
    progress.progress(25, text=f"候选池 {len(candidates)} 只，并发分析中...")
    
    def fetch_one(code_):
        try: return code_, get_stock_full_data(code_), get_stock_historical_metrics(code_)
        except Exception: return code_, None, None
    results = {}; completed = 0; total = len(candidates)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, c): c for c in candidates}
        for future in as_completed(futures):
            code_, full_data, hist = future.result(); results[code_] = (full_data, hist)
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
        if st.button("🔄 刷新动态池", type="primary", use_container_width=True, key="refresh_pool"):
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
    # 主图可自由缩放；成交量副图 X/Y 轴均锁定 → 完全跟随主图联动，不能独立缩放平移
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], fixedrange=False, row=1, col=1)
    fig.update_xaxes(matches='x', row=2, col=1)
    fig.update_xaxes(fixedrange=True, row=2, col=1)
    fig.update_yaxes(fixedrange=False, row=1, col=1); fig.update_yaxes(fixedrange=True, row=2, col=1)
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
    df_daily = get_daily_data(code); df_minute = get_minute_data(code); market_change = get_market_status()
    if df_daily is not None: df_daily = calculate_daily_indicators(df_daily)
    else: st.error("数据不足，无法标注。"); st.stop()
    today_norm  = pd.Timestamp.now().normalize(); last_k_norm = df_daily['Date'].iloc[-1].normalize()
    if len(df_daily) >= 2 and last_k_norm == today_norm: prev_close = df_daily['Close'].iloc[-2]
    else: prev_close = df_daily['Close'].iloc[-1]
    if auto_dev:
        if df_minute is not None and not df_minute.empty:
            high_price = df_minute['Price'].max(); low_price = df_minute['Price'].min(); avg_price = df_minute['AvgPrice'].mean()
            if avg_price > 0: actual_deviation = max(0.003, min(((high_price - low_price) / avg_price) * 0.4, 0.015))
            else: actual_deviation = 0.008
        else: actual_deviation = 0.008
    else: actual_deviation = manual_dev
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
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol, latest, st.session_state.chart_reset_key), use_container_width=True, config=PLOTLY_CONFIG_CLEAN)
    st.caption("💡 **框选放大**：在**主图**上按住鼠标左键拖出一个矩形框，松开即放大该区域；双击图表或点「🔄 复位」恢复初始视图。成交量副图已锁定。")
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
    st.error("❌ 主程序运行出错，请把下面的错误信息截图反馈：")
    st.code(traceback.format_exc(), language="python")
