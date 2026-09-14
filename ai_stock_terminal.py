import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import re
import json
import os
from datetime import datetime, time

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
    .report-box { 
        background-color: #1e1e2e; padding: 12px 18px; border-radius: 8px; 
        font-family: 'Consolas', monospace; font-size: 15px; line-height: 1.6; 
        margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px;
    }
    .report-row { display: flex; gap: 20px; flex-wrap: wrap; }
    .color-red { color: #ff4b4b; font-weight: bold; }
    .color-green { color: #00cc66; font-weight: bold; }
    .color-blue { color: #89b4fa; font-weight: bold; }
    .color-white { color: #f0f2f6; }
    .color-yellow { color: #f9e2af; font-weight: bold; }
    .ai-advice-box {
        background-color: #2b2b3b; padding: 15px; border-left: 5px solid #ffaa00;
        border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px;
    }
    .guide-box {
        background-color: #1a2b1a; padding: 15px; border-left: 5px solid #00cc66;
        border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px;
    }
    .predict-box {
        background-color: #2b1a1a; padding: 15px; border-left: 5px solid #ff4b4b;
        border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px;
    }
    div.stButton > button[kind="primary"] {
        background-color: #1f6feb; color: white; border: none; font-weight: bold;
    }
    div.stButton > button[kind="secondary"] {
        background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d;
    }
    
    /* 右侧悬浮 AI 聊天固定窗 */
    @media (min-width: 992px) {
        div[data-testid="stAppViewBlockContainer"] > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"]:last-child {
            position: fixed !important;
            top: 70px !important;
            right: 20px !important;
            width: 400px !important;
            max-height: 88vh !important;
            background-color: #1e1e2e !important;
            border: 1px solid #30363d !important;
            border-radius: 12px !important;
            padding: 12px !important;
            z-index: 9999 !important;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6) !important;
            overflow-y: auto !important;
        }
        .main .block-container {
            padding-right: 440px !important;
        }
    }
    @media (max-width: 992px) {
        div[data-testid="stAppViewBlockContainer"] > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"]:last-child {
            position: static !important;
            width: 100% !important;
            max-height: none !important;
            margin-top: 20px !important;
        }
        .main .block-container {
            padding-right: 1rem !important;
        }
    }
    /* 顶部均线数值栏 */
    .ma-bar { 
        background-color: #161b22; padding: 8px 15px; border-radius: 6px; 
        font-family: 'Consolas', monospace; font-size: 14px; 
        display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 5px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🤖 日内做T信号标注助手")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=60000, key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情）")
else:
    st.caption("⚠️ 未安装自动刷新组件，请按 F5 手动刷新网页")

# ================= 2. 本地持久化存储 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default

def _save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass

def load_watchlist(): return _load_json(WATCHLIST_FILE, ['515880', '159915'])
def save_watchlist(lst): _save_json(WATCHLIST_FILE, lst)
def load_config(): return _load_json(CONFIG_FILE, {})
def save_config(cfg): _save_json(CONFIG_FILE, cfg)

# ================= 3. 辅助函数 =================
@st.cache_data(ttl=3600)
def get_stock_name(symbol):
    prefix = "sh" if symbol.startswith(('5', '6', '9')) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{symbol}"
    try:
        res = requests.get(url, timeout=3)
        res.encoding = 'gbk'
        text = res.text
        if "~" in text:
            parts = text.split("~")
            if len(parts) > 1:
                return parts[1].strip('"')
    except Exception:
        pass
    return symbol

@st.cache_data(ttl=60)
def get_market_status():
    url = "https://qt.gtimg.cn/q=sh000001"
    try:
        res = requests.get(url, timeout=3)
        res.encoding = 'gbk'
        text = res.text
        if "~" in text:
            parts = text.split("~")
            if len(parts) > 32:
                return float(parts[32])
    except Exception:
        pass
    return 0.0

def is_trading_time():
    now = datetime.now().time()
    return (time(9, 30) <= now <= time(11, 30)) or (time(13, 0) <= now <= time(15, 0))

def send_wechat_notification(send_key, title, content):
    if not send_key:
        return False
    try:
        url = f"https://sctapi.ftqq.com/{send_key}.send"
        data = {"title": title, "desp": content}
        res = requests.post(url, data=data, timeout=5)
        return res.status_code == 200
    except Exception:
        return False

# ================= 4. 侧边栏 =================
with st.sidebar:
    st.header("📈 自选股管理")
    
    if 'stock_list' not in st.session_state:
        st.session_state.stock_list = load_watchlist()
    if 'current_stock' not in st.session_state:
        st.session_state.current_stock = st.session_state.stock_list[0] if st.session_state.stock_list else '515880'

    with st.form("batch_add_form", clear_on_submit=True):
        new_stocks = st.text_input("批量添加股票代码", placeholder="例如: 512480, 159915 000001", label_visibility="collapsed")
        submit_add = st.form_submit_button("➕ 批量添加", use_container_width=True)
        if submit_add and new_stocks.strip():
            codes = re.findall(r'\d{6}', new_stocks)
            added = False
            for code in codes:
                if code not in st.session_state.stock_list:
                    st.session_state.stock_list.append(code)
                    added = True
            if added:
                save_watchlist(st.session_state.stock_list)
                st.rerun()
            else:
                st.warning("未发现新的有效股票代码。")

    st.markdown("---")
    
    if not st.session_state.stock_list:
        st.info("暂无自选股，请添加")
        st.session_state.current_stock = '515880'
    else:
        for stock in st.session_state.stock_list:
            col_stock, col_del = st.columns([4, 1])
            with col_stock:
                stock_name = get_stock_name(stock)
                display_text = f"{stock_name} ({stock})"
                is_selected = (stock == st.session_state.current_stock)
                if st.button(display_text, key=f"select_{stock}", use_container_width=True, 
                             type="primary" if is_selected else "secondary"):
                    st.session_state.current_stock = stock
                    st.rerun()
            with col_del:
                if st.button("❌", key=f"del_{stock}"):
                    st.session_state.stock_list.remove(stock)
                    save_watchlist(st.session_state.stock_list)
                    if st.session_state.current_stock == stock:
                        st.session_state.current_stock = st.session_state.stock_list[0] if st.session_state.stock_list else '515880'
                    st.rerun()
    
    st.markdown("---")
    st.header("⚙️ 参数设置")
    auto_dev = st.checkbox("启用动态偏离阈值", value=True)
    if not auto_dev:
        manual_dev = st.slider("手动偏离阈值(%)", 0.5, 2.0, 0.8) / 100
    
    st.markdown("---")
    st.header("🤖 AI 配置")
    config = load_config()
    if 'api_key' not in st.session_state:
        st.session_state.api_key = config.get('api_key', '')
    def on_api_key_change():
        save_config({**load_config(), 'api_key': st.session_state.api_key})
    st.text_input("DeepSeek API Key", type="password", key="api_key", 
                  on_change=on_api_key_change, help="保存后无需再配置")
    if st.session_state.api_key:
        st.success("API Key 已保存")
    
    st.markdown("---")
    st.header("📱 微信提醒（可选）")
    if 'send_key' not in st.session_state:
        st.session_state.send_key = config.get('send_key', '')
    def on_send_key_change():
        save_config({**load_config(), 'send_key': st.session_state.send_key})
    st.text_input("Server酱 SendKey", type="password", key="send_key", 
                  on_change=on_send_key_change, help="去 sct.ftqq.com 免费注册获取")
    if st.session_state.send_key:
        st.success("微信提醒已开启")

symbol = st.session_state.current_stock
current_name = get_stock_name(symbol)
st.sidebar.success(f"当前标的: {current_name} ({symbol})")

# ================= 5. 数据获取 =================
code = f"sh{symbol}" if symbol.startswith(('5', '6', '9')) else f"sz{symbol}"

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
            for col in ['Open', 'Close', 'High', 'Low', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df.sort_values('Date').reset_index(drop=True)
    except Exception:
        return None

@st.cache_data(ttl=60)
def get_minute_data(code):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        res = requests.get(url, timeout=3).json()
        if res.get("code") == 0:
            data = res["data"][code]["data"]["data"]
            records = [item.split(" ") for item in data]
            df = pd.DataFrame(records, columns=['Time', 'Price', 'AvgPrice', 'Volume'])
            df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
            # 🌟 注：这里的 Volume 是腾讯接口返回的每一分钟成交量（增量），并非总成交量
            df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
            df['Amount'] = df['Price'] * df['Volume']
            df['AvgPrice'] = df['Amount'].cumsum() / df['Volume'].cumsum()
            df['Price_Change'] = df['Price'].diff()
            ema12 = df['Price'].ewm(span=12, adjust=False).mean()
            ema26 = df['Price'].ewm(span=26, adjust=False).mean()
            df['DIFF'] = ema12 - ema26
            df['DEA'] = df['DIFF'].ewm(span=9, adjust=False).mean()
            df['MACD'] = 2 * (df['DIFF'] - df['DEA'])
            return df
    except Exception:
        return None

# ================= 6. 指标计算 =================
def calculate_daily_indicators(df):
    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA30'] = df['Close'].rolling(30).mean()
    df['MA250'] = df['Close'].rolling(250).mean()
    
    df['MA20_UP'] = df['MA20'] > df['MA20'].shift(1)
    
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIFF'] = ema12 - ema26
    df['DEA'] = df['DIFF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = 2 * (df['DIFF'] - df['DEA'])
    
    low_9 = df['Low'].rolling(9).min()
    high_9 = df['High'].rolling(9).max()
    rsv = (df['Close'] - low_9) / (high_9 - low_9) * 100
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    
    df['BOLL_MID'] = df['Close'].rolling(20).mean()
    std = df['Close'].rolling(20).std()
    df['BOLL_UP'] = df['BOLL_MID'] + 2 * std
    df['BOLL_LOW'] = df['BOLL_MID'] - 2 * std
    df['BOLL_WIDTH'] = (df['BOLL_UP'] - df['BOLL_LOW']) / df['BOLL_MID']
    
    df['VOL_MA5'] = df['Volume'].rolling(5).mean()
    df['TR'] = np.maximum(df['High'] - df['Low'], 
                          np.maximum(abs(df['High'] - df['Close'].shift(1)), 
                                     abs(df['Low'] - df['Close'].shift(1))))
    df['ATR14'] = df['TR'].rolling(14).mean()
    
    df['Signal'] = 0
    buy_cond = (df['MA20_UP'] == True) & (df['J'] < 15) & (df['Close'] <= df['BOLL_MID'] * 1.02) & (df['Volume'] < df['VOL_MA5'] * 1.5)
    sell_cond = (df['J'] > 105) & (df['Close'] >= df['BOLL_UP'] * 0.98) & (df['Volume'] > df['VOL_MA5'] * 0.8)
    buy_cond = buy_cond & (df['Signal'].shift(1) != 1)
    sell_cond = sell_cond & (df['Signal'].shift(1) != -1)
    df.loc[buy_cond, 'Signal'] = 1
    df.loc[sell_cond, 'Signal'] = -1
    return df.bfill().ffill()

# ================= 7. 核心策略判定 =================
def generate_report_and_advice(df_daily, df_minute, deviation, market_change):
    latest = df_daily.iloc[-1]
    prev = df_daily.iloc[-2]
    
    trend = "震荡"
    if latest['MA20_UP'] and latest['Close'] > latest['MA20']:
        trend = "上升"
    elif not latest['MA20_UP'] and latest['Close'] < latest['MA20']:
        trend = "下降"
        
    is_steady = False
    price_stop = latest['Close'] > prev['Close'] or (latest['Close'] - latest['Low']) > (latest['High'] - latest['Close'])
    vol_stop = latest['Volume'] < latest['VOL_MA5'] * 1.5
    support_hold = latest['Close'] > latest['BOLL_LOW'] * 0.98
    narrow_vol = latest['BOLL_WIDTH'] < 0.15 
    if price_stop and vol_stop and support_hold:
        is_steady = True
        
    pattern = "无明显形态"
    if is_steady:
        if latest['BOLL_WIDTH'] < 0.08 and abs(latest['MA5'] - latest['MA20']) < 0.01:
            pattern = "均线粘合走平"
        elif latest['Close'] > latest['MA20'] and prev['Close'] < prev['MA20']:
            pattern = "W底/N字结构"
        elif narrow_vol:
            pattern = "缩量横盘"
            
    narrow_count = 0
    for i in range(1, 8):
        if df_daily['BOLL_WIDTH'].iloc[-i] < 0.08:
            narrow_count += 1
    flat_warning = " (均盘必跌风险)" if narrow_count >= 5 else ""
            
    allow_t = "允许"
    direction = "正T"
    if trend == "下降" and not is_steady:
        allow_t = "不允许"
        direction = "不做"
    elif trend == "下降" and is_steady:
        allow_t = "允许"
        direction = "反T"
    elif trend == "震荡":
        direction = "正T" if latest['Close'] < latest['BOLL_MID'] else "反T"
        
    support = round(min(latest['MA20'], latest['BOLL_LOW']), 3)
    resistance = round(max(latest['Close'] * 1.02, latest['BOLL_UP']), 3)
    
    best_buy = "无有效点"
    best_sell = "无有效点"
    buy_points = pd.DataFrame()
    sell_points = pd.DataFrame()
    buy_warning = ""
    divergence_info = ""
    
    # 动态极值预测（基于时间衰减 + 实时波动率）
    intraday_high_predict = 0
    intraday_low_predict = 0
    if not df_minute.empty:
        cur_price = df_minute['Price'].iloc[-1]
        day_high = df_minute['Price'].max()
        day_low = df_minute['Price'].min()
        atr = latest['ATR14'] if not pd.isna(latest['ATR14']) else cur_price * 0.02
        
        now_time = datetime.now().time()
        if now_time < time(9, 30):
            intraday_high_predict = round(latest['Close'] + atr * 0.5, 3)
            intraday_low_predict = round(latest['Close'] - atr * 0.5, 3)
        elif now_time > time(15, 0):
            intraday_high_predict = day_high
            intraday_low_predict = day_low
        else:
            current_dt = datetime.combine(datetime.today(), now_time)
            start_am = datetime.combine(datetime.today(), time(9, 30))
            end_am = datetime.combine(datetime.today(), time(11, 30))
            start_pm = datetime.combine(datetime.today(), time(13, 0))
            end_pm = datetime.combine(datetime.today(), time(15, 0))
            
            if current_dt <= end_am:
                passed_minutes = (current_dt - start_am).total_seconds() / 60
            else:
                passed_minutes = 120 + (current_dt - start_pm).total_seconds() / 60
                
            passed_minutes = max(passed_minutes, 1)
            remaining_minutes = max(240 - passed_minutes, 0)
            
            if passed_minutes > 10:
                realized_volatility_per_min = (day_high - day_low) / passed_minutes
                remaining_range = realized_volatility_per_min * remaining_minutes
            else:
                remaining_range = atr * 0.5
                
            dynamic_offset = min(remaining_range, atr) * 0.6
            intraday_high_predict = round(max(day_high, cur_price + dynamic_offset), 3)
            intraday_low_predict = round(min(day_low, cur_price - dynamic_offset), 3)
    
    if allow_t == "允许" and df_minute is not None and not df_minute.empty:
        df_min = df_minute.copy()
        df_min = df_min[df_min['Time'] <= "1500"]
        df_min['Vol_MA5'] = df_min['Volume'].rolling(5).mean()
        df_min_buy = df_min[(df_min['Time'] >= "0945") & (df_min['Time'] <= "1445")]
        df_min_sell = df_min[(df_min['Time'] >= "0930") & (df_min['Time'] <= "1455")]
        
        low_idx = df_min['Price'].idxmin()
        if len(df_min.loc[:low_idx]) > 5:
            recent_low = df_min.loc[low_idx, 'Price']
            recent_macd = df_min.loc[low_idx, 'MACD']
            prev_lows = df_min[df_min['Price'] < recent_low * 1.005]
            if len(prev_lows) > 0:
                prev_macd = df_min.loc[prev_lows.index[0], 'MACD']
                if recent_macd > prev_macd:
                    divergence_info += " 底背离"
        
        high_idx = df_min['Price'].idxmax()
        if len(df_min.loc[:high_idx]) > 5:
            recent_high = df_min.loc[high_idx, 'Price']
            recent_macd = df_min.loc[high_idx, 'MACD']
            prev_highs = df_min[df_min['Price'] > recent_high * 0.995]
            if len(prev_highs) > 0:
                prev_macd = df_min.loc[prev_highs.index[-1], 'MACD']
                if recent_macd < prev_macd:
                    divergence_info += " 顶背离"
        
        buy_cond = (df_min_buy['Price'] < df_min_buy['AvgPrice'] * (1 - deviation)) & (df_min_buy['Volume'] < df_min_buy['Vol_MA5'] * 0.8)
        sell_cond = (df_min_sell['Price'] > df_min_sell['AvgPrice'] * (1 + deviation)) & (df_min_sell['Volume'] > df_min_sell['Vol_MA5'] * 1.2)
        buy_points = df_min_buy[buy_cond]
        sell_points = df_min_sell[sell_cond]
        
        if market_change < -1.0:
            buy_warning = " ⚠️大盘暴跌，低置信度！"
        
        if not buy_points.empty:
            best_row = buy_points.loc[buy_points['Price'].idxmin()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['AvgPrice'] - best_row['Price']) / best_row['AvgPrice']
            if market_change < -1.0:
                conf = "低"
            else:
                conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
                if "底背离" in divergence_info: conf = "高"
            b_type = "正T低吸" if direction == "正T" else "反T回补"
            best_buy = f"{time_fmt} | {best_row['Price']:.3f} | {b_type} | 回踩均价线缩量{divergence_info} | 置信度{conf}{buy_warning}"
            
        if not sell_points.empty:
            best_row = sell_points.loc[sell_points['Price'].idxmax()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['Price'] - best_row['AvgPrice']) / best_row['AvgPrice']
            conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
            if "顶背离" in divergence_info: conf = "高"
            s_type = "正T高抛" if direction == "正T" else "反T减仓"
            best_sell = f"{time_fmt} | {best_row['Price']:.3f} | {s_type} | 冲高乖离均价线放量{divergence_info} | 置信度{conf}"

    today_str = latest['Date'].strftime('%Y-%m-%d')
    time_str = datetime.now().strftime('%H:%M')
    market_status = f"上证 {market_change:+.2f}%"
    market_color = "color-green" if market_change >= 0 else "color-red"
    
    report = f"""
    <div class="report-row">
        <span class="color-blue">日期:</span> <span class="color-white">{today_str}</span>
        <span class="color-blue">数据时间:</span> <span class="color-white">{time_str}</span>
        <span class="color-blue">大盘:</span> <span class="{market_color}">{market_status}</span>
        <span class="color-blue">阈值:</span> <span class="color-white">{deviation*100:.2f}%</span>
    </div>
    <div class="report-row">
        <span class="color-blue">日线趋势:</span> <span class="color-white">{trend}</span>
        <span class="color-blue">是否企稳:</span> <span class="color-white">{'是' if is_steady else '否'}</span>
        <span class="color-blue">企稳形态:</span> <span class="color-white">{pattern}{flat_warning}</span>
    </div>
    <div class="report-row">
        <span class="color-blue">做T方向:</span> <span class="color-white">{direction}</span>
        <span class="color-blue">关键支撑:</span> <span class="color-white">{support:.3f}</span>
        <span class="color-blue">关键压力:</span> <span class="color-white">{resistance:.3f}</span>
    </div>
    <div class="report-row">
        <span class="color-red">最优买点:</span> <span class="color-red">{best_buy}</span>
    </div>
    <div class="report-row">
        <span class="color-green">最优卖点:</span> <span class="color-green">{best_sell}</span>
    </div>
    <div class="report-row">
        <span class="color-blue">失效条件:</span> <span class="color-white">跌破支撑 {support:.3f} 或 日线趋势转下降</span>
    </div>
    """
    
    ai_advice = ""
    if market_change < -1.5:
        ai_advice = f"🚨 **大盘熔断警告**：上证跌幅 {market_change:.2f}%，暂停一切正T低吸，观望为主。"
    elif flat_warning:
        ai_advice = f"⚠️ **久盘必跌警告**：均线粘合超过5天，若触发买点请**仓位减半**，跌破 {support:.3f} 立刻离场！"
    elif allow_t == "不允许":
        ai_advice = f"📉 **不允许做T**：日线下降趋势且未企稳，严禁抄底做正T。仅可少量反T减仓。"
    elif direction == "反T":
        ai_advice = f"🔄 **优先反T**：日线下降趋稳或震荡上沿。先抛后买，冲高乖离均价线减仓，回踩 {support:.3f} 附近回补。"
    else:
        if best_buy != "无有效点":
            buy_price = float(best_buy.split('|')[1].strip())
            stop_loss = buy_price * 0.995
            ai_advice = f"🔥 **适合正T低吸**：已触发买点（{best_buy.split('|')[0].strip()}）。仓位≤底仓30%，止损 {stop_loss:.3f}，目标 {resistance:.3f}。"
        else:
            ai_advice = f"⏳ **等待正T买点**：日线向上且企稳，但分时价格尚未回踩均价线企稳，耐心等待。"
    
    if allow_t == "不允许":
        t_guide = f"**今日不做T** —— 日线处于下降趋势且未企稳，风险大于收益。\n\n操作建议：\n1. 空仓观望或仅持底仓不动。\n2. 若盘中有冲高至压力位 {resistance:.3f} 附近，可少量反T减仓。\n3. 等待日线企稳信号（缩量止跌+支撑不破）再考虑重新入场。"
    elif direction == "反T":
        t_guide = f"**今日优先做反T（先卖后买）** —— 日线下降趋稳或震荡区间上沿。\n\n操作步骤：\n1. **高抛**：当分时价格冲高至均价线以上 {deviation*100:.2f}% 且放量滞涨时，减仓 30%。\n2. **低吸回补**：待价格回落至日线支撑 {support:.3f} 附近缩量企稳时，用同等仓位买回。\n3. **止损**：若回补后跌破 {support:.3f}，立刻止损。\n4. **仓位**：单次不超过底仓 30%。"
    else:
        t_guide = f"**今日优先做正T（先买后卖）** —— 日线趋势向上且已企稳。\n\n操作步骤：\n1. **低吸**：当分时价格回踩均价线以下 {deviation*100:.2f}% 且缩量企稳时，买入 30% 仓位。\n2. **高抛**：待价格冲高至压力位 {resistance:.3f} 附近且放量滞涨时，卖出回补的仓位。\n3. **止损**：若买入后跌破买入价 0.5%，立刻止损。\n4. **仓位**：单次不超过底仓 30%，单日最多操作 2-3 次。"
    
    if intraday_high_predict > 0:
        predict_text = f"**今日预估波动区间（动态调整）**：\n- 预估最高点：**{intraday_high_predict:.3f}**（基于实时波动率与剩余时间）\n- 预估最低点：**{intraday_low_predict:.3f}**（基于实时波动率与剩余时间）\n- 当前价格：**{df_minute['Price'].iloc[-1]:.3f}**\n\n⚠️ 该预测随盘中行情变化而动态更新，仅供参考，不构成操作依据。"
    else:
        predict_text = "数据不足，无法预测日内极值。"
    
    context = f"""
    【当前盘面实时数据】
    标的: {current_name} ({symbol})
    当前价格: {latest['Close']:.3f}
    日线趋势: {trend}
    是否企稳: {'是' if is_steady else '否'}
    做T方向: {direction}
    关键支撑: {support:.3f}
    关键压力: {resistance:.3f}
    大盘涨跌幅: {market_change:.2f}%
    当前最优买点: {best_buy}
    当前最优卖点: {best_sell}
    日内预估最高: {intraday_high_predict:.3f}
    日内预估最低: {intraday_low_predict:.3f}
    """
    
    return report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest

# ================= 8. 图表绘制（同花顺风格） =================
PLOTLY_CONFIG_CLEAN = {
    'displayModeBar': False,
    'scrollZoom': False,
    'staticPlot': False,
    'doubleClick': 'reset',
}

def plot_daily_chart(df, symbol_name, latest, uirevision_key=0):
    """日线图：三面板（K线、成交量、MACD），主图框选放大，副图自动跟随"""
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.6, 0.2, 0.2])
    
    fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K',
                                 increasing_line_color='#ff3333', decreasing_line_color='#00cc66', line=dict(width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA5'], mode='lines', name='MA5', line=dict(color='#ffffff', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA10'], mode='lines', name='MA10', line=dict(color='#ffff00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ff00ff', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA30'], mode='lines', name='MA30', line=dict(color='#00ff00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA250'], mode='lines', name='年线', line=dict(color='#00ccff', width=1.5, dash='dash')), row=1, col=1)
    
    buy_s = df[df['Signal'] == 1]
    sell_s = df[df['Signal'] == -1]
    if not buy_s.empty:
        fig.add_trace(go.Scatter(x=buy_s['Date'], y=buy_s['Low']*0.98, mode='markers', name='买点', 
                                 marker=dict(symbol='triangle-up', size=16, color='#ff0000', line=dict(width=2, color='white'))), row=1, col=1)
    if not sell_s.empty:
        fig.add_trace(go.Scatter(x=sell_s['Date'], y=sell_s['High']*1.02, mode='markers', name='卖点', 
                                 marker=dict(symbol='triangle-down', size=16, color='#00ff00', line=dict(width=2, color='white'))), row=1, col=1)
    
    fig.add_hline(y=latest['Close'], line_dash="dot", line_color="#888888", line_width=1.5, row=1, col=1,
                  annotation_text=f"{latest['Close']:.3f}", annotation_position="left", 
                  annotation_font=dict(color="#f0f2f6", size=12))
    
    vol_colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='成交量', marker_color=vol_colors), row=2, col=1)
    
    colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['MACD'], name='MACD', marker_color=colors), row=3, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DIFF'], mode='lines', name='DIFF', line=dict(color='#ffffff', width=1.5)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DEA'], mode='lines', name='DEA', line=dict(color='#ffaa00', width=1.5)), row=3, col=1)
    fig.add_hline(y=0, line_width=1, line_dash="dash", line_color="#888888", row=3, col=1)
    
    fig.update_layout(
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False, 
        hovermode="x unified", dragmode='zoom',
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), 
        margin=dict(t=50, l=10, r=10, b=10),
        uirevision=uirevision_key
    )
    
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], fixedrange=False, row=1, col=1)
    fig.update_yaxes(fixedrange=False, row=1, col=1)
    fig.update_xaxes(fixedrange=False, row=2, col=1)
    fig.update_yaxes(fixedrange=False, row=2, col=1)
    fig.update_xaxes(fixedrange=False, row=3, col=1)
    fig.update_yaxes(fixedrange=False, row=3, col=1)
    
    return fig

def plot_minute_chart_ths(df, buy_points, sell_points, symbol_name, prev_close, uirevision_key=0):
    """分时图：双面板（价格线、成交量），完全禁止缩放，当前价格用虚线对齐左侧"""
    df = df[df['Time'] <= "1500"]
    df = df[~((df['Time'] > "1130") & (df['Time'] < "1300"))].reset_index(drop=True)
    if df.empty:
        return go.Figure()
    df['Datetime'] = pd.to_datetime("2024-01-01 " + df['Time'].str[:2] + ":" + df['Time'].str[2:])
    
    latest_price = df['Price'].iloc[-1]
    latest_avg = df['AvgPrice'].iloc[-1]
    
    # 固定Y轴范围，并确保当前价格在可视范围内
    y_max = prev_close * 1.03
    y_min = prev_close * 0.97
    actual_max = df['Price'].max()
    actual_min = df['Price'].min()
    y_max = max(y_max, actual_max * 1.005, latest_price * 1.005)
    y_min = min(y_min, actual_min * 0.995, latest_price * 0.995)
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    
    # 分时价格线
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Price'], mode='lines', name='分时价格', 
                             line=dict(color='#00ccff', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['AvgPrice'], mode='lines', name='分时均价', 
                             line=dict(color='#ffaa00', width=1.5)), row=1, col=1)
    
    # 昨收虚线
    fig.add_hline(y=prev_close, line_dash="dash", line_color="#888888", line_width=1, row=1, col=1,
                  annotation_text=f"昨收 {prev_close:.3f}", annotation_position="right",
                  annotation_font=dict(color="#f0f2f6", size=12))
    
    # 🌟 核心修复1：当前价格虚线对齐左侧价格轴
    color_price = "#ff3333" if latest_price >= prev_close else "#00cc66"
    fig.add_hline(y=latest_price, line_dash="dot", line_color=color_price, line_width=1.5, row=1, col=1,
                  annotation_text=f"{latest_price:.3f}", annotation_position="left", 
                  annotation_font=dict(color=color_price, size=12))
    
    # 买卖点
    if not buy_points.empty:
        buy_points = buy_points[buy_points['Time'] <= "1500"]
        if not buy_points.empty:
            buy_points = buy_points.copy()
            buy_points['Datetime'] = pd.to_datetime("2024-01-01 " + buy_points['Time'].str[:2] + ":" + buy_points['Time'].str[2:])
            fig.add_trace(go.Scatter(
                x=buy_points['Datetime'], y=buy_points['Price']*0.998, mode='markers',
                name='分时买点', marker=dict(symbol='triangle-up', size=16, color='#ff4b4b', line=dict(width=2, color='white'))
            ), row=1, col=1)
        
    if not sell_points.empty:
        sell_points = sell_points[sell_points['Time'] <= "1500"]
        if not sell_points.empty:
            sell_points = sell_points.copy()
            sell_points['Datetime'] = pd.to_datetime("2024-01-01 " + sell_points['Time'].str[:2] + ":" + sell_points['Time'].str[2:])
            fig.add_trace(go.Scatter(
                x=sell_points['Datetime'], y=sell_points['Price']*1.002, mode='markers',
                name='分时卖点', marker=dict(symbol='triangle-down', size=16, color='#00cc66', line=dict(width=2, color='white'))
            ), row=1, col=1)
    
    # 右上角保留文本（作为补充）
    fig.add_annotation(
        x=0.99, y=0.98, xref="paper", yref="paper",
        text=f"<b>价格:</b> <span style='color:{color_price}'>{latest_price:.3f}</span><br><b>均价:</b> <span style='color:#ffaa00'>{latest_avg:.3f}</span>",
        showarrow=False, align="right",
        bgcolor="rgba(30,30,46,0.85)", bordercolor="#444", borderwidth=1, borderpad=6,
        font=dict(color="#f0f2f6", size=14)
    )
    
    # 🌟 核心修复2：分时成交量（每分钟增量） + 5分钟均量线
    vol_colors = ['#ff3333' if change >= 0 else '#00cc66' for change in df['Price_Change']]
    fig.add_trace(go.Bar(x=df['Datetime'], y=df['Volume'], name='分时成交量', marker_color=vol_colors, width=1000*60*0.8), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Volume'].rolling(5).mean(), mode='lines', name='均量', line=dict(color='#ffaa00', width=1.5)), row=2, col=1)
    
    fig.update_layout(
        template="plotly_dark", height=500, 
        xaxis_rangeslider_visible=False, hovermode="x unified",
        dragmode=False,
        legend=dict(orientation="h", yanchor="top", y=1.0, xanchor="left", x=0),
        margin=dict(t=40, l=10, r=10, b=10),
        uirevision=uirevision_key
    )
    
    fig.update_xaxes(
        type='date', 
        tickformat="%H:%M", 
        range=["2024-01-01 09:30:00", "2024-01-01 15:00:00"],
        rangebreaks=[dict(bounds=[11.5, 13], pattern="hour")],
        fixedrange=True
    )
    fig.update_yaxes(range=[y_min, y_max], fixedrange=True, row=1, col=1)
    fig.update_yaxes(fixedrange=True, row=2, col=1)
    
    return fig

# ================= 9. 主程序执行 =================
if __name__ == "__main__":
    if 'chart_reset_key' not in st.session_state:
        st.session_state.chart_reset_key = 0
    if 'notified_keys' not in st.session_state:
        st.session_state.notified_keys = set()
    
    df_daily = get_daily_data(code)
    df_minute = get_minute_data(code)
    market_change = get_market_status()
    
    if df_daily is not None:
        df_daily = calculate_daily_indicators(df_daily)
    else:
        st.error("数据不足，无法标注。")
        st.stop()
    
    prev_close = df_daily['Close'].iloc[-2] if len(df_daily) > 1 else df_daily['Close'].iloc[-1]

    if auto_dev:
        if df_minute is not None and not df_minute.empty:
            high_price = df_minute['Price'].max()
            low_price = df_minute['Price'].min()
            avg_price = df_minute['AvgPrice'].mean()
            if avg_price > 0:
                amplitude = (high_price - low_price) / avg_price
                actual_deviation = max(0.003, min(amplitude * 0.4, 0.015))
            else:
                actual_deviation = 0.008
        else:
            actual_deviation = 0.008
    else:
        actual_deviation = manual_dev

    report, ai_advice, t_guide, predict_text, buy_points, sell_points, context, best_buy, best_sell, latest = generate_report_and_advice(
        df_daily, df_minute, actual_deviation, market_change
    )
    
    if is_trading_time():
        if best_buy != "无有效点":
            buy_key = f"{symbol}_buy_{best_buy.split('|')[0].strip()}"
            if buy_key not in st.session_state.notified_keys:
                st.session_state.notified_keys.add(buy_key)
                st.toast(f"🔴 {current_name} 出现买点！{best_buy.split('|')[1].strip()}", icon="🔔")
                if st.session_state.get('send_key'):
                    send_wechat_notification(st.session_state.send_key, 
                                            f"【买点提醒】{current_name}", 
                                            f"股票：{current_name} ({symbol})\n时间：{best_buy.split('|')[0].strip()}\n价格：{best_buy.split('|')[1].strip()}\n依据：{best_buy.split('|')[3].strip()}")
        
        if best_sell != "无有效点":
            sell_key = f"{symbol}_sell_{best_sell.split('|')[0].strip()}"
            if sell_key not in st.session_state.notified_keys:
                st.session_state.notified_keys.add(sell_key)
                st.toast(f"🟢 {current_name} 出现卖点！{best_sell.split('|')[1].strip()}", icon="🔔")
                if st.session_state.get('send_key'):
                    send_wechat_notification(st.session_state.send_key, 
                                            f"【卖点提醒】{current_name}", 
                                            f"股票：{current_name} ({symbol})\n时间：{best_sell.split('|')[0].strip()}\n价格：{best_sell.split('|')[1].strip()}\n依据：{best_sell.split('|')[3].strip()}")

    st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ai-advice-box">🤖 <b>AI 实时建议</b><br>{ai_advice}</div>', unsafe_allow_html=True)
    
    col_g, col_p = st.columns(2)
    with col_g:
        st.markdown(f'<div class="guide-box">🎯 <b>今日做T指引</b><br>{t_guide}</div>', unsafe_allow_html=True)
    with col_p:
        st.markdown(f'<div class="predict-box">📊 <b>日内极值预测</b><br>{predict_text}</div>', unsafe_allow_html=True)

    col_title1, col_btn1 = st.columns([9, 1])
    with col_title1:
        st.subheader(f"📈 {current_name} ({symbol}) 日线级别走势")
    with col_btn1:
        if st.button("🔄 复位", use_container_width=True, key="reset_daily_chart"):
            st.session_state.chart_reset_key += 1
            st.rerun()
    
    ma_html = f"""
    <div class="ma-bar">
        <span style="color:#ffffff">M5: {latest['MA5']:.3f}</span>
        <span style="color:#ffff00">M10: {latest['MA10']:.3f}</span>
        <span style="color:#ff00ff">M20: {latest['MA20']:.3f}</span>
        <span style="color:#00ff00">M30: {latest['MA30']:.3f}</span>
        <span style="color:#00ccff">年线: {latest['MA250']:.3f}</span>
    </div>
    """
    st.markdown(ma_html, unsafe_allow_html=True)
    
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol, latest, st.session_state.chart_reset_key), 
                    use_container_width=True, config=PLOTLY_CONFIG_CLEAN)
    
    col_title2, col_btn2 = st.columns([9, 1])
    with col_title2:
        st.subheader(f"⏱️ {current_name} ({symbol}) 分时级别走势（同花顺风格）")
    with col_btn2:
        if st.button("🔄 复位", use_container_width=True, key="reset_minute_chart"):
            st.session_state.chart_reset_key += 1
            st.rerun()
    
    if df_minute is not None and not df_minute.empty:
        st.plotly_chart(plot_minute_chart_ths(df_minute, buy_points, sell_points, symbol, prev_close, st.session_state.chart_reset_key), 
                        use_container_width=True, config=PLOTLY_CONFIG_CLEAN)
        st.caption("操作说明：日线图主图支持框选放大（成交量/MACD副图自动跟随）；分时图已锁定缩放，只能拖动。双击图表或点击复位按钮恢复初始视图。")
    else:
        st.warning("暂无分时数据")

    with st.container():
        st.subheader("💬 DeepSeek AI")
        
        if 'messages' not in st.session_state:
            st.session_state.messages = []
        if 'history_questions' not in st.session_state:
            st.session_state.history_questions = []
        
        col_hist, col_chat = st.columns([1, 2])
        
        with col_hist:
            st.markdown("**📜 历史提问**")
            if not st.session_state.history_questions:
                st.caption("暂无历史")
            else:
                for i, q in enumerate(reversed(st.session_state.history_questions[-15:])):
                    if st.button(f"• {q[:12]}...", key=f"hist_{i}", use_container_width=True):
                        st.session_state.messages = [{"role": "user", "content": q}]
                        st.rerun()
        
        with col_chat:
            if not st.session_state.api_key:
                st.warning("⚠️ 请先在左侧侧边栏配置 DeepSeek API Key")
            else:
                chat_container = st.container(height=420, border=True)
                with chat_container:
                    for message in st.session_state.messages:
                        with st.chat_message(message["role"]):
                            st.markdown(message["content"])
                
                if prompt := st.chat_input("在此提问..."):
                    with st.chat_message("user"):
                        st.markdown(prompt)
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    st.session_state.history_questions.append(prompt)
                    
                    system_prompt = f"""你是一个专业的A股做T交易助手。请根据以下实时盘面数据，用简洁专业的语言回答用户的问题。
                    注意：不要盲目看多或看空，要结合支撑压力、量能和趋势给出客观判断。
                    {context}
                    """
                    messages_to_send = [{"role": "system", "content": system_prompt}] + st.session_state.messages
                    
                    with st.chat_message("assistant"):
                        message_placeholder = st.empty()
                        full_response = ""
                        try:
                            headers = {
                                "Authorization": f"Bearer {st.session_state.api_key}",
                                "Content-Type": "application/json"
                            }
                            data = {
                                "model": "deepseek-chat",
                                "messages": messages_to_send,
                                "stream": False
                            }
                            response = requests.post("https://api.deepseek.com/chat/completions", 
                                                     headers=headers, json=data, timeout=30)
                            if response.status_code == 200:
                                result = response.json()
                                full_response = result['choices'][0]['message']['content']
                            else:
                                full_response = f"API请求失败（{response.status_code}）。请检查API Key或余额。"
                        except Exception as e:
                            full_response = f"网络异常：{str(e)}"
                        message_placeholder.markdown(full_response)
                    st.session_state.messages.append({"role": "assistant", "content": full_response})
                    st.rerun()
