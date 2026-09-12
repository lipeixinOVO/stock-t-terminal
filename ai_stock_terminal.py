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
st.set_page_config(page_title="个人AI量化终端 - 王牌做T版", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    h1, h2, h3 { color: #f0f2f6; font-family: 'Microsoft YaHei'; }
    div[data-testid="stMetricValue"] { color: #ffaa00; font-size: 22px; }
    div[data-testid="stMetricLabel"] { color: #a0aec0; }
    .stAlert { border-radius: 8px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🤖 个人AI量化终端 - 王牌做T版")

if HAS_AUTOREFRESH:
    st_autorefresh(interval=60000, key="auto_refresh")
    st.caption("✅ 自动刷新已开启（每60秒更新一次行情）")
else:
    st.caption("⚠️ 未安装自动刷新组件，请按 F5 手动刷新网页")

# ================= 2. 侧边栏参数 =================
with st.sidebar:
    st.header("⚙️ 参数设置")
    symbol = st.text_input("股票/ETF代码", value="515880", help="默认: 515880 (通信ETF)")
    
    st.markdown("---")
    st.markdown("**王牌做T参数 (已放宽，可自行微调)**")
    j_buy_threshold = st.slider("日线KDJ超卖买入(J值)", 0, 30, 15) 
    j_sell_threshold = st.slider("日线KDJ超买卖出(J值)", 90, 130, 105)
    
    st.markdown("**分时极值参数**")
    min_deviation = st.slider("分时偏离均价阈值(%)", 0.5, 2.0, 1.0) / 100
    
    st.info("💡 系统每60秒自动刷新一次行情数据")

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

@st.cache_data(ttl=60)
def get_market_turnover():
    url = "https://qt.gtimg.cn/q=s_sh000001,s_sz399001"
    try:
        res = requests.get(url, timeout=3)
        res.encoding = 'gbk'
        lines = res.text.strip().split(';')
        total_amount = 0
        for line in lines:
            if line:
                parts = line.split('~')
                if len(parts) > 8:
                    total_amount += float(parts[8])
        return total_amount / 10000
    except Exception:
        return 0

def predict_turnover(current_amount):
    now = datetime.now().time()
    if now < time(9, 30) or now > time(15, 0) or (time(11, 30) < now < time(13, 0)):
        return current_amount
    start_am = datetime.combine(datetime.today(), time(9, 30))
    end_am = datetime.combine(datetime.today(), time(11, 30))
    start_pm = datetime.combine(datetime.today(), time(13, 0))
    now_dt = datetime.now()
    total_minutes = 240
    if now_dt <= end_am:
        passed_minutes = (now_dt - start_am).total_seconds() / 60
    else:
        passed_minutes = 120 + (now_dt - start_pm).total_seconds() / 60
    if passed_minutes <= 10:
        return current_amount * (total_minutes / 10)
    return current_amount * (total_minutes / passed_minutes)

# ================= 4. 指标计算与王牌信号生成 =================
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

def generate_daily_signals(df, j_buy, j_sell):
    df = df.copy()
    df['Signal'] = 0
    
    buy_cond = (df['MA20_UP'] == True) & \
               (df['J'] < j_buy) & \
               (df['Close'] <= df['BOLL_MID'] * 1.02) & \
               (df['Volume'] < df['VOL_MA5'] * 1.5)
               
    sell_cond = (df['J'] > j_sell) & \
                (df['Close'] >= df['BOLL_UP'] * 0.98) & \
                (df['Volume'] > df['VOL_MA5'] * 0.8)
                
    buy_cond = buy_cond & (df['Signal'].shift(1) != 1)
    sell_cond = sell_cond & (df['Signal'].shift(1) != -1)
    
    df.loc[buy_cond, 'Signal'] = 1
    df.loc[sell_cond, 'Signal'] = -1
    return df

# ================= 5. 绘制超大双图 =================
def plot_daily_chart(df, symbol_name):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    
    fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='日K',
                                 increasing_line_color='#ff3333', decreasing_line_color='#00cc66', line=dict(width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA5'], mode='lines', name='MA5', line=dict(color='#ffffff', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ffaa00', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['BOLL_UP'], mode='lines', name='BOLL上轨', line=dict(color='#888888', width=1, dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['BOLL_LOW'], mode='lines', name='BOLL下轨', line=dict(color='#888888', width=1, dash='dot')), row=1, col=1)
    
    buy_s = df[df['Signal'] == 1]
    sell_s = df[df['Signal'] == -1]
    fig.add_trace(go.Scatter(x=buy_s['Date'], y=buy_s['Low']*0.98, mode='markers', name='王牌买点', marker=dict(symbol='triangle-up', size=16, color='#ff0000', line=dict(width=2, color='white'))), row=1, col=1)
    fig.add_trace(go.Scatter(x=sell_s['Date'], y=sell_s['High']*1.02, mode='markers', name='王牌卖点', marker=dict(symbol='triangle-down', size=16, color='#00ff00', line=dict(width=2, color='white'))), row=1, col=1)
    
    colors = ['#ff3333' if c >= o else '#00cc66' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df['Date'], y=df['MACD'], name='MACD柱', marker_color=colors), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DIFF'], mode='lines', name='DIFF', line=dict(color='#ffffff', width=1.5)), row=2, col=1)
    fig.add_trace(go.Scatter(x=df['Date'], y=df['DEA'], mode='lines', name='DEA线', line=dict(color='#ffaa00', width=1.5)), row=2, col=1)
    
    fig.add_hline(y=0, line_width=1, line_dash="dash", line_color="#888888", row=2, col=1)
    
    fig.update_layout(
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False, hovermode="x unified", 
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
        margin=dict(t=80, l=10, r=10, b=10)
    )
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig

def plot_minute_chart(df, symbol_name, deviation):
    df = df[df['Time'] <= "1500"]
    df = df[~((df['Time'] > "1130") & (df['Time'] < "1300"))].reset_index(drop=True)
    
    fig = make_subplots(rows=1, cols=1)
    
    df['Datetime'] = pd.to_datetime("2024-01-01 " + df['Time'].str[:2] + ":" + df['Time'].str[2:])
    
    df['Min_Signal'] = 0
    df.loc[df['Price'] < df['AvgPrice'] * (1 - deviation), 'Min_Signal'] = 1
    df.loc[df['Price'] > df['AvgPrice'] * (1 + deviation), 'Min_Signal'] = -1
    
    buy_min = df[df['Min_Signal'] == 1].nsmallest(2, 'Price')
    sell_min = df[df['Min_Signal'] == -1].nlargest(2, 'Price')
    
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['Price'], mode='lines', name='分时价格', line=dict(color='#00ccff', width=2)))
    fig.add_trace(go.Scatter(x=df['Datetime'], y=df['AvgPrice'], mode='lines', name='分时均价', line=dict(color='#ffaa00', width=1.5)))
    
    if not buy_min.empty:
        fig.add_trace(go.Scatter(
            x=buy_min['Datetime'], 
            y=buy_min['Price']*0.998, 
            mode='markers+text', 
            name='日内绝佳买点', 
            marker=dict(symbol='triangle-up', size=22, color='#ff0000', line=dict(width=3, color='white')),
            text=['买入' for _ in range(len(buy_min))], 
            textposition='bottom center', 
            textfont=dict(color='#ff0000', size=14, family='Arial Black')
        ))
        for idx, row in buy_min.iterrows():
            fig.add_annotation(
                x=row['Datetime'], y=row['Price'],
                text=f"<b>绝佳买点</b><br>{row['Price']:.3f}",
                showarrow=True, arrowhead=2, arrowsize=2, arrowwidth=2, arrowcolor='#ff0000',
                ax=0, ay=-45, 
                bgcolor="rgba(255, 0, 0, 0.8)", bordercolor="#ffffff", borderwidth=2, borderpad=5,
                font=dict(color="#ffffff", size=13)
            )

    if not sell_min.empty:
        fig.add_trace(go.Scatter(
            x=sell_min['Datetime'], 
            y=sell_min['Price']*1.002, 
            mode='markers+text', 
            name='日内绝佳卖点', 
            marker=dict(symbol='triangle-down', size=22, color='#00ff00', line=dict(width=3, color='white')),
            text=['卖出' for _ in range(len(sell_min))], 
            textposition='top center', 
            textfont=dict(color='#00ff00', size=14, family='Arial Black')
        ))
        for idx, row in sell_min.iterrows():
            fig.add_annotation(
                x=row['Datetime'], y=row['Price'],
                text=f"<b>绝佳卖点</b><br>{row['Price']:.3f}",
                showarrow=True, arrowhead=2, arrowsize=2, arrowwidth=2, arrowcolor='#00ff00',
                ax=0, ay=45, 
                bgcolor="rgba(0, 255, 0, 0.8)", bordercolor="#ffffff", borderwidth=2, borderpad=5,
                font=dict(color="#000000", size=13)
            )
    
    fig.update_layout(
        template="plotly_dark", height=500, xaxis_rangeslider_visible=False, hovermode="x unified", 
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
        margin=dict(t=80, l=10, r=10, b=10)
    )
    
    fig.update_xaxes(
        type='date',
        tickformat="%H:%M",
        rangebreaks=[dict(bounds=[11.5, 13], pattern="hour")]
    )
    
    return fig

# ================= 6. 主程序执行 =================
if __name__ == "__main__":
    df_daily = get_daily_data(code)
    df_minute = get_minute_data(code)
    turnover = get_market_turnover()
    predict_turnover = predict_turnover(turnover)

    if df_daily is not None:
        df_daily = calculate_daily_indicators(df_daily)
        df_daily = generate_daily_signals(df_daily, j_buy_threshold, j_sell_threshold)
    else:
        st.error("无法获取日线数据，请检查网络或代码。")
        st.stop()

    # 💡 修复1：处理非交易时间显示0亿的问题
    if predict_turnover > 0:
        display_turnover = f"{predict_turnover:.0f} 亿"
    else:
        display_turnover = "休市/暂无数据"

    # 💡 修复2：计算市场量能状态（用于底部建议，不再塞入指标卡片）
    if predict_turnover > 10000:
        vol_status = "🔥 **大盘状态：放量市场** (做T空间打开，可积极参与)"
    elif predict_turnover > 8000:
        vol_status = "⚖️ **大盘状态：温和放量** (正常做T，不追高)"
    else:
        vol_status = "❄️ **大盘状态：缩量市场** (做T空间有限，逢低吸纳不追涨)"

    latest = df_daily.iloc[-1]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("最新价", f"{latest['Close']:.3f}", f"{latest['Close']-latest['Open']:.3f}")
    col2.metric("日线J值", f"{latest['J']:.2f}", "超买" if latest['J']>100 else "超卖" if latest['J']<0 else "正常")
    col3.metric("日线MACD", "多头" if latest['DIFF']>latest['DEA'] else "空头")
    # 将第三个参数留空（""），避免出现截断和多余的箭头
    col4.metric("大盘总成交额(预测)", display_turnover, "")

    # 💡 修复3：将大盘量能完整建议放在全宽容器中显示，保证100%不被截断
    st.info(vol_status)

    st.subheader("📈 日线级别做T (大趋势)")
    st.plotly_chart(plot_daily_chart(df_daily.tail(120), symbol), use_container_width=True)
    
    st.subheader("⏱️ 分时级别做T (实时) - 仅标注日内最优点位")
    if df_minute is not None and not df_minute.empty:
        st.plotly_chart(plot_minute_chart(df_minute, symbol, min_deviation), use_container_width=True)
    else:
        st.warning("暂无分时数据（非交易时间或接口受限）")

    st.markdown("---")
    now = datetime.now().time()
    if time(14, 30) <= now <= time(15, 0):
        st.warning(f"⏰ **尾盘决策 (14:30 - 15:00)** | 大盘量能：{vol_status}")
        if latest['Signal'] == 1:
            st.success("🔥 **王牌买点触发：日线超卖且回调至中轨。如果大盘量能允许，可考虑尾盘低吸，博弈次日反弹！**")
        elif latest['Signal'] == -1:
            st.error("⚠️ **王牌卖点触发：日线超买且触及上轨。建议尾盘高抛锁定利润，规避隔夜风险！**")
        else:
            st.info("⚖️ **当前无王牌信号。建议保持观望，或利用分时图显示的日内极值点进行极小仓位的辅助做T。**")
    elif now > time(15, 0):
        st.success("🌙 **盘后复盘**")
        st.write(f"- 大盘成交额：**{turnover:.0f} 亿元**（预测全天：{predict_turnover:.0f} 亿元），状态：{vol_status}")
        st.write(f"- 标的今日收盘：**{latest['Close']:.3f}**，MACD：**{'多头' if latest['DIFF']>latest['DEA'] else '空头'}**，J值：**{latest['J']:.2f}**")
        st.info("📝 **复盘总结**：王牌策略要求“趋势向上+超卖+回调中轨”才买入，“超买+冲高上轨”才卖出。注意把握做T节奏。")
    else:
        st.info("⏳ 当前非尾盘时间（14:30后）及盘后时间，尾盘决策与复盘功能将在对应时间段自动激活。")
