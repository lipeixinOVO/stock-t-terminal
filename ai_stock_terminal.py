import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
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
    /* 紧凑型报告盒子 */
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
    </style>
    """, unsafe_allow_html=True)

st.title("🤖 日内做T信号标注助手")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=60000, key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情）")
else:
    st.caption("⚠️ 未安装自动刷新组件，请按 F5 手动刷新网页")

# ================= 2. 侧边栏参数 =================
with st.sidebar:
    st.header("⚙️ 参数设置")
    symbol = st.text_input("股票/ETF代码", value="515880", help="默认: 515880 (通信ETF)")
    st.markdown("**分时极值参数**")
    min_deviation = st.slider("分时偏离均价阈值(%)", 0.5, 2.0, 0.8) / 100

# ================= 3. 数据获取工具 =================
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
            return df
    except Exception:
        return None

# ================= 4. 指标计算 =================
def calculate_daily_indicators(df):
    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
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
    
    df['VOL_MA5'] = df['Volume'].rolling(5).mean()
    return df.bfill().ffill()

def generate_daily_signals(df):
    df = df.copy()
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
    return df

# ================= 5. 生成极简策略报告 =================
def generate_report(df_daily, df_minute, deviation):
    latest = df_daily.iloc[-1]
    trend = "震荡"
    if latest['MA20_UP'] and latest['Close'] > latest['MA20']:
        trend = "上升"
    elif not latest['MA20_UP'] and latest['Close'] < latest['MA20']:
        trend = "下降"
        
    allow_t = "允许" if trend != "下降" else "不允许"
    direction = "正T" if trend == "上升" or (trend == "震荡" and latest['Close'] < latest['BOLL_MID']) else ("反T" if trend == "震荡" else "不做")
    
    support = round(min(latest['MA20'], latest['BOLL_LOW']), 3)
    resistance = round(max(latest['Close'] * 1.02, latest['BOLL_UP']), 3)
    
    best_buy = None
    best_sell = None
    buy_points = pd.DataFrame()
    sell_points = pd.DataFrame()
    
    if allow_t == "允许" and df_minute is not None and not df_minute.empty:
        df_min = df_minute.copy()
        df_min = df_min[df_min['Time'] <= "1500"]
        df_min['Vol_MA5'] = df_min['Volume'].rolling(5).mean()
        
        buy_cond = (df_min['Price'] < df_min['AvgPrice'] * (1 - deviation)) & (df_min['Volume'] < df_min['Vol_MA5'] * 0.8)
        sell_cond = (df_min['Price'] > df_min['AvgPrice'] * (1 + deviation)) & (df_min['Volume'] > df_min['Vol_MA5'] * 1.2)
        
        buy_points = df_min[buy_cond]
        sell_points = df_min[sell_cond]
        
        if not buy_points.empty:
            best_row = buy_points.loc[buy_points['Price'].idxmin()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['AvgPrice'] - best_row['Price']) / best_row['AvgPrice']
            conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
            b_type = "正T低吸" if trend == "上升" or (trend == "震荡" and latest['Close'] < latest['BOLL_MID']) else "反T回补"
            best_buy = f"{time_fmt} | {best_row['Price']:.3f} | {b_type} | 回踩均价线缩量企稳 | 置信度{conf}"
            
        if not sell_points.empty:
            best_row = sell_points.loc[sell_points['Price'].idxmax()]
            time_fmt = f"{best_row['Time'][:2]}:{best_row['Time'][2:]}"
            dev_pct = (best_row['Price'] - best_row['AvgPrice']) / best_row['AvgPrice']
            conf = "高" if dev_pct > deviation * 2 else ("中" if dev_pct > deviation * 1.2 else "低")
            s_type = "正T高抛" if trend == "上升" or (trend == "震荡" and latest['Close'] < latest['BOLL_MID']) else "反T减仓"
            best_sell = f"{time_fmt} | {best_row['Price']:.3f} | {s_type} | 冲高乖离均价线放量滞涨 | 置信度{conf}"

    if best_buy is None: best_buy = "无有效点"
    if best_sell is None: best_sell = "无有效点"

    today_str = latest['Date'].strftime('%Y-%m-%d')
    time_str = datetime.now().strftime('%H:%M')
    
    # 紧凑型 HTML 报告（移除风险提示，买点红字，卖点绿字）
    report = f"""
    <div class="report-row">
        <span class="color-blue">日期:</span> <span class="color-white">{today_str}</span>
        <span class="color-blue">数据时间:</span> <span class="color-white">{time_str}</span>
        <span class="color-blue">日线趋势:</span> <span class="color-white">{trend}</span>
        <span class="color-blue">做T方向:</span> <span class="color-white">{direction}</span>
    </div>
    <div class="report-row">
        <span class="color-blue">关键支撑:</span> <span class="color-white">{support:.3f}</span>
        <span class="color-blue">关键压力:</span> <span class="color-white">{resistance:.3f}</span>
        <span class="color-blue">失效条件:</span> <span class="color-white">跌破支撑 {support:.3f} 或 日线趋势转下降</span>
    </div>
    <div style="margin-top: 5px;">
        <span class="color-red">最优买点:</span> <span class="color-red">{best_buy}</span>
    </div>
    <div>
        <span class="color-green">最优卖点:</span> <span class="color-green">{best_sell}</span>
    </div>
    """
    return report, buy_points, sell_points

# ================= 6. 绘制图表 =================
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
    
    # 分时买点标记
    if not buy_points.empty:
        buy_points = buy_points[buy_points['Time'] <= "1500"]
        buy_points['Datetime'] = pd.to_datetime("2024-01-01 " + buy_points['Time'].str[:2] + ":" + buy_points['Time'].str[2:])
        fig.add_trace(go.Scatter(
            x=buy_points['Datetime'], y=buy_points['Price']*0.998, mode='markers',
            name='分时买点', marker=dict(symbol='triangle-up', size=16, color='#ff4b4b', line=dict(width=2, color='white'))
        ))
        
    # 分时卖点标记
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

# ================= 7. 主程序执行 =================
if __name__ == "__main__":
    df_daily = get_daily_data(code)
    df_minute = get_minute_data(code)
    
    if df_daily is not None:
        df_daily = calculate_daily_indicators(df_daily)
        df_daily = generate_daily_signals(df_daily)
    else:
        st.error("数据不足，无法标注。")
        st.stop()

    report, buy_points, sell_points = generate_report(df_daily, df_minute, min_deviation)
    
    # 显示紧凑型报告
    st.markdown(f'<div class="report-box">{report}</div>', unsafe_allow_html=True)

    st.subheader("📈 日线级别走势")
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol), use_container_width=True)
    
    st.subheader("⏱️ 分时级别走势 (实时)")
    if df_minute is not None and not df_minute.empty:
        st.plotly_chart(plot_minute_chart(df_minute, buy_points, sell_points, symbol), use_container_width=True)
        
        # 显示一句话策略总结
        st.caption("策略说明：在日线趋势向上或震荡且允许做T的前提下，分时价格缩量回踩均价线时提示买入，分时价格放量冲高乖离均价线时提示卖出，趋势向下时不再生成任何信号。")
    else:
        st.warning("暂无分时数据")
