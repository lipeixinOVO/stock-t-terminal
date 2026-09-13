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
    .color-warning { color: #f9e2af; font-weight: bold; }
    .ai-advice-box {
        background-color: #2b2b3b; padding: 15px; border-left: 5px solid #ffaa00;
        border-radius: 8px; color: #f0f2f6; font-size: 16px; margin-bottom: 20px;
    }
    div.stButton > button[kind="primary"] {
        background-color: #1f6feb; color: white; border: none; font-weight: bold;
    }
    div.stButton > button[kind="secondary"] {
        background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🤖 日内做T信号标注助手")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=60000, key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情）")
else:
    st.caption("⚠️ 未安装自动刷新组件，请按 F5 手动刷新网页")

# ================= 2. 本地持久化存储工具 =================
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")

def load_watchlist():
    try:
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return ['515880', '159915']

def save_watchlist(stock_list):
    try:
        with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(stock_list, f, ensure_ascii=False, indent=4)
    except Exception as e:
        pass

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
                st.warning("未发现新的有效股票代码，或格式不正确。")

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
                        if st.session_state.stock_list:
                            st.session_state.current_stock = st.session_state.stock_list[0]
                        else:
                            st.session_state.current_stock = '515880'
                    st.rerun()
    
    st.markdown("---")
    st.header("⚙️ 参数设置")
    auto_dev = st.checkbox("启用动态偏离阈值（自动适配波动）", value=True)
    if not auto_dev:
        manual_dev = st.slider("手动偏离阈值(%)", 0.5, 2.0, 0.8) / 100
    else:
        st.info("已开启动态阈值，根据当日振幅自动计算。")

symbol = st.session_state.current_stock
current_name = get_stock_name(symbol)
st.sidebar.success(f"当前标的: {current_name} ({symbol})")

# ================= 5. 数据获取工具 =================
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
            df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce')
            df['Amount'] = df['Price'] * df['Volume']
            df['AvgPrice'] = df['Amount'].cumsum() / df['Volume'].cumsum()
            
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
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA20_UP'] = df['MA20'] > df['MA20'].shift(1)
    df['MA5_UP'] = df['MA5'] > df['MA5'].shift(1)
    
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
    
    df['Signal'] = 0
    buy_cond = (df['MA20_UP'] == True) & \
               (df['J'] < 15) & \
               (df['Close'] <= df['BOLL_MID'] * 1.02) & \
               (df['Volume'] < df['VOL_MA5'] * 1.5)
    sell_cond = (df['J'] > 105) & \
                (df['Close'] >= df['BOLL_UP'] * 0.98) & \
                (df['Volume'] > df['VOL_MA5'] * 0.8)
    buy_cond = buy_cond & (df['Signal'].shift(1) != 1)
    sell_cond = sell_cond & (df['Signal'].shift(1) != -1)
    df.loc[buy_cond, 'Signal'] = 1
    df.loc[sell_cond, 'Signal'] = -1
    
    return df.bfill().ffill()

# ================= 7. 核心策略判定与AI建议 =================
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
            
    flat_warning = ""
    if narrow_count >= 5:
        flat_warning = " (均线长期粘合，注意久盘必跌风险)"
            
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
                prev_low_idx = prev_lows.index[0]
                prev_macd = df_min.loc[prev_low_idx, 'MACD']
                if recent_macd > prev_macd:
                    divergence_info += " 底背离"
        
        high_idx = df_min['Price'].idxmax()
        if len(df_min.loc[:high_idx]) > 5:
            recent_high = df_min.loc[high_idx, 'Price']
            recent_macd = df_min.loc[high_idx, 'MACD']
            prev_highs = df_min[df_min['Price'] > recent_high * 0.995]
            if len(prev_highs) > 0:
                prev_high_idx = prev_highs.index[-1]
                prev_macd = df_min.loc[prev_high_idx, 'MACD']
                if recent_macd < prev_macd:
                    divergence_info += " 顶背离"
        
        buy_cond = (df_min_buy['Price'] < df_min_buy['AvgPrice'] * (1 - deviation)) & (df_min_buy['Volume'] < df_min_buy['Vol_MA5'] * 0.8)
        sell_cond = (df_min_sell['Price'] > df_min_sell['AvgPrice'] * (1 + deviation)) & (df_min_sell['Volume'] > df_min_sell['Vol_MA5'] * 1.2)
        
        buy_points = df_min_buy[buy_cond]
        sell_points = df_min_sell[sell_cond]
        
        if market_change < -1.0:
            buy_warning = " ⚠️大盘暴跌，强制降为低置信度！"
        
        if not buy_points.empty:
            best_row = buy_points.loc[buy_points['Price'].idxmin()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['AvgPrice'] - best_row['Price']) / best_row['AvgPrice']
            
            if market_change < -1.0:
                conf = "低"
            else:
                conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
                if "底背离" in divergence_info:
                    conf = "高"
                    
            b_type = "正T低吸" if direction == "正T" else "反T回补"
            best_buy = f"{time_fmt} | {best_row['Price']:.3f} | {b_type} | 回踩均价线缩量企稳{divergence_info} | 置信度{conf}{buy_warning}"
            
        if not sell_points.empty:
            best_row = sell_points.loc[sell_points['Price'].idxmax()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['Price'] - best_row['AvgPrice']) / best_row['AvgPrice']
            conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
            if "顶背离" in divergence_info:
                conf = "高"
            s_type = "正T高抛" if direction == "正T" else "反T减仓"
            best_sell = f"{time_fmt} | {best_row['Price']:.3f} | {s_type} | 冲高乖离均价线放量滞涨{divergence_info} | 置信度{conf}"

    today_str = latest['Date'].strftime('%Y-%m-%d')
    time_str = datetime.now().strftime('%H:%M')
    
    market_status = f"上证 {market_change:+.2f}%"
    market_color = "color-green" if market_change >= 0 else "color-red"
    
    report = f"""
    <div class="report-row">
        <span class="color-blue">日期:</span> <span class="color-white">{today_str}</span>
        <span class="color-blue">数据时间:</span> <span class="color-white">{time_str}</span>
        <span class="color-blue">大盘环境:</span> <span class="{market_color}">{market_status}</span>
        <span class="color-blue">当前分时阈值:</span> <span class="color-white">{deviation*100:.2f}%</span>
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
        ai_advice = f"🚨 **【大盘熔断警告】** 当前上证指数跌幅为 {market_change:.2f}%，市场情绪极度恶劣。今日所有买点置信度强制降为「低」，建议暂停一切正T低吸操作，观望为主，保护好本金！"
    elif flat_warning:
        ai_advice = f"⚠️ **【久盘必跌警告】** 日线布林带持续收窄，均线高度粘合超过5天。虽已企稳，但向下破位风险正在积累。如果触发买点，**建议仓位减半**，且严格设好止损，一旦跌破支撑 {support:.3f} 立刻离场！"
    elif allow_t == "不允许":
        ai_advice = f"📉 **当前策略判定：不允许做T。** 日线处于下降趋势且未见企稳特征，此时严禁盲目抄底做正T。若盘中有冲高机会，仅可考虑少量反T减仓，保持观望。"
    elif direction == "反T":
        ai_advice = f"🔄 **当前策略判定：优先反T。** 日线处于下降趋势中的企稳阶段，或震荡区间上沿。建议先抛后买，利用冲高乖离均价线（卖点）进行减仓，待回踩日线支撑（{support:.3f}）附近缩量企稳时再回补。"
    else:
        if best_buy != "无有效点":
            buy_price = float(best_buy.split('|')[1].strip())
            stop_loss = buy_price * 0.995
            ai_advice = f"🔥 **当前策略判定：适合正T低吸。** 日线趋势向上且已企稳。目前分时图已触发最优买点（{best_buy.split('|')[0].strip()}）。<br>📝 **纪律提示**：本次做T建议仓位**不超过底仓的30%**，止损价严格设于 **{stop_loss:.3f}**（买入价下方0.5%）。目标价看向压力位 {resistance:.3f} 附近。"
        else:
            ai_advice = f"⏳ **当前策略判定：等待正T买点。** 日线趋势向上且已企稳，但当前分时价格尚未回踩至均价线缩量企稳，耐心等待最优买点出现，不要盲目追高。"
            
    return report, ai_advice, buy_points, sell_points

# ================= 8. 绘制图表 =================
def plot_daily_chart(df, symbol_name):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K',
                                 increasing_line_color='#ff3333', decreasing_line_color='#00cc66', line=dict(width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ffaa00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['BOLL_UP'], mode='lines', name='BOLL上轨', line=dict(color='#888888', width=1, dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['BOLL_LOW'], mode='lines', name='BOLL下轨', line=dict(color='#888888', width=1, dash='dot')), row=1, col=1)
    
    buy_s = df[df['Signal'] == 1]
    sell_s = df[df['Signal'] == -1]
    if not buy_s.empty:
        fig.add_trace(go.Scatter(x=buy_s['Date'], y=buy_s['Low']*0.98, mode='markers', name='王牌买点', 
                                 marker=dict(symbol='triangle-up', size=16, color='#ff0000', line=dict(width=2, color='white'))), row=1, col=1)
    if not sell_s.empty:
        fig.add_trace(go.Scatter(x=sell_s['Date'], y=sell_s['High']*1.02, mode='markers', name='王牌卖点', 
                                 marker=dict(symbol='triangle-down', size=16, color='#00ff00', line=dict(width=2, color='white'))), row=1, col=1)
    
    colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['MACD'], name='MACD柱', marker_color=colors), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DIFF'], mode='lines', name='DIFF', line=dict(color='#ffffff', width=1.5)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DEA'], mode='lines', name='DEA线', line=dict(color='#ffaa00', width=1.5)), row=2, col=1)
    fig.add_hline(y=0, line_width=1, line_dash="dash", line_color="#888888", row=2, col=1)
    
    fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False, hovermode="x unified", 
                      legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1), margin=dict(t=80, l=10, r=10, b=10))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig

def plot_minute_chart(df, buy_points, sell_points, symbol_name):
    df = df[df['Time'] <= "1500"]
    df = df[~((df['Time'] > "1130") & (df['Time'] < "1300"))].reset_index(drop=True)
    df['Datetime'] = pd.to_datetime("2024-01-01 " + df['Time'].str[:2] + ":" + df['Time'].str[2:])
    
    fig = make_subplots(rows=1, cols=1)
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Price'], mode='lines', name='分时价格', line=dict(color='#00ccff', width=2)))
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['AvgPrice'], mode='lines', name='分时均价', line=dict(color='#ffaa00', width=1.5)))
    
    # 🌟 核心新增：在分时图左上角直接显示实时价格和均价
    if not df.empty:
        latest_price = df['Price'].iloc[-1]
        latest_avg = df['AvgPrice'].iloc[-1]
        fig.add_annotation(
            x=0.02, y=0.98, xref="paper", yref="paper",
            text=f"<b>实时价格:</b> <span style='color:#00ccff'>{latest_price:.3f}</span><br><b>分时均价:</b> <span style='color:#ffaa00'>{latest_avg:.3f}</span>",
            showarrow=False, align="left",
            bgcolor="rgba(30,30,46,0.85)", bordercolor="#444", borderwidth=1, borderpad=6,
            font=dict(color="#f0f2f6", size=14)
        )
    
    if not buy_points.empty:
        buy_points = buy_points[buy_points['Time'] <= "1500"]
        buy_points['Datetime'] = pd.to_datetime("2024-01-01 " + buy_points['Time'].str[:2] + ":" + buy_points['Time'].str[2:])
        fig.add_trace(go.Scatter(
            x=buy_points['Datetime'], y=buy_points['Price']*0.998, mode='markers',
            name='分时买点', marker=dict(symbol='triangle-up', size=16, color='#ff4b4b', line=dict(width=2, color='white'))
        ))
        
    if not sell_points.empty:
        sell_points = sell_points[sell_points['Time'] <= "1500"]
        sell_points['Datetime'] = pd.to_datetime("2024-01-01 " + sell_points['Time'].str[:2] + ":" + sell_points['Time'].str[2:])
        fig.add_trace(go.Scatter(
            x=sell_points['Datetime'], y=sell_points['Price']*1.002, mode='markers',
            name='分时卖点', marker=dict(symbol='triangle-down', size=16, color='#00cc66', line=dict(width=2, color='white'))
        ))
    
    fig.update_layout(template="plotly_dark", height=450, xaxis_rangeslider_visible=False, hovermode="x unified", 
                      legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1), margin=dict(t=80, l=10, r=10, b=10))
    fig.update_xaxes(type='date', tickformat="%H:%M", rangebreaks=[dict(bounds=[11.5, 13], pattern="hour")])
    return fig

# ================= 9. 主程序执行 =================
if __name__ == "__main__":
    df_daily = get_daily_data(code)
    df_minute = get_minute_data(code)
    market_change = get_market_status()
    
    if df_daily is not None:
        df_daily = calculate_daily_indicators(df_daily)
    else:
        st.error("数据不足，无法标注。")
        st.stop()

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

    report, ai_advice, buy_points, sell_points = generate_report_and_advice(df_daily, df_minute, actual_deviation, market_change)
    
    st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="ai-advice-box">🤖 <b>AI 实时建议</b><br>{ai_advice}</div>', unsafe_allow_html=True)

    st.subheader(f"📈 {current_name} ({symbol}) 日线级别走势")
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol), use_container_width=True)
    
    st.subheader(f"⏱️ {current_name} ({symbol}) 分时级别走势 (实时)")
    if df_minute is not None and not df_minute.empty:
        st.plotly_chart(plot_minute_chart(df_minute, buy_points, sell_points, symbol), use_container_width=True)
        st.caption("策略说明：在日线趋势向上或震荡且允许做T的前提下，分时价格缩量回踩均价线时提示买入，分时价格放量冲高乖离均价线时提示卖出，趋势向下时不再生成任何信号。")
    else:
        st.warning("暂无分时数据")
