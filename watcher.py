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
import sys
from datetime import datetime, time, timedelta, timezone

import requests
import pandas as pd
import numpy as np

# ✅ 云服务器一律 UTC，所有时间判断统一换算成北京时间
CN_TZ = timezone(timedelta(hours=8))

def now_cn():
    return datetime.now(CN_TZ)

SEND_KEY = os.environ.get("SERVERCHAN_KEY", "").strip()
WATCHLIST = [c.strip() for c in os.environ.get("WATCHLIST", "").replace("，", ",").split(",") if c.strip()]
LOG_FILE = os.environ.get("NOTIFY_LOG", "notify_log.json")
WINDOW_MIN = int(os.environ.get("ALERT_WINDOW_MIN", "10"))
FORCE_RUN = os.environ.get("FORCE_RUN", "0").strip() == "1"

# ============ 基础工具 ============

def is_trading_time():
    t = now_cn().time()
    return (time(9, 25) <= t <= time(11, 32)) or (time(12, 58) <= t <= time(15, 2))

def get_code(symbol):
    return f"sh{symbol}" if symbol.startswith(('5', '6', '9')) else f"sz{symbol}"

def get_stock_name(symbol):
    url = f"https://qt.gtimg.cn/q={get_code(symbol)}"
    try:
        res = requests.get(url, timeout=5)
        res.encoding = 'gbk'
        if "~" in res.text:
            parts = res.text.split("~")
            if len(parts) > 1:
                return parts[1].strip('"')
    except Exception:
        pass
    return symbol

def get_market_change():
    """上证指数涨跌幅(%)"""
    try:
        res = requests.get("https://qt.gtimg.cn/q=sh000001", timeout=5)
        res.encoding = 'gbk'
        if "~" in res.text:
            parts = res.text.split("~")
            if len(parts) > 32:
                return float(parts[32])
    except Exception:
        pass
    return 0.0

def send_wechat(title, content):
    if not SEND_KEY:
        return False
    try:
        res = requests.post(f"https://sctapi.ftqq.com/{SEND_KEY}.send",
                            data={"title": title, "desp": content}, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"  推送异常: {e}")
        return False

# ============ 去重机制 ============

def price_bucket(price, pct=0.005):
    try:
        if price <= 0:
            return 0
        return int(math.log(max(price, 0.001)) / math.log(1 + pct))
    except Exception:
        return 0

def load_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
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
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    try:
        res = requests.get(url, timeout=6).json()
        if res.get("code") != 0:
            return None
        raw = res["data"][code]["data"]["data"]
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

# ============ 主巡检 ============

def check_symbol(sym, market_change, log, today):
    """检查单只股票，返回是否触发推送"""
    try:
        sym_code = get_code(sym)
        df = get_minute_data(sym_code)
        if df is None or df.empty or len(df) < 10:
            return False
        df = df[df['Time'] <= "1500"].copy()
        if len(df) < 10:
            return False
        df['Vol_MA5'] = df['Volume'].rolling(5).mean()

        buy_df = df[(df['Time'] >= "0945") & (df['Time'] <= "1445")].copy()
        sell_df = df[(df['Time'] >= "0930") & (df['Time'] <= "1455")].copy()
        if buy_df.empty or sell_df.empty:
            return False
        buy_df['MACD_UP'] = buy_df['MACD'] > buy_df['MACD'].shift(1)
        sell_df['MACD_DOWN'] = sell_df['MACD'] < sell_df['MACD'].shift(1)

        high = df['Price'].max()
        low = df['Price'].min()
        avg = df['AvgPrice'].mean()
        dev = max(0.003, min(((high - low) / avg) * 0.4, 0.015)) if avg > 0 else 0.008

        buy_pts = buy_df[(buy_df['Price'] < buy_df['AvgPrice'] * (1 - dev)) &
                         (buy_df['Volume'] < buy_df['Vol_MA5'] * 0.7) &
                         (buy_df['MACD_UP'] == True)]
        sell_pts = sell_df[(sell_df['Price'] > sell_df['AvgPrice'] * (1 + dev)) &
                           (sell_df['Volume'] > sell_df['Vol_MA5'] * 1.5) &
                           (sell_df['MACD_DOWN'] == True)]

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
                key = key_of(sym, 'buy', price)
                if key not in log.get(today, []):
                    t_str = f"{row['Time'][:2]}:{row['Time'][2:]}"
                    ok = send_wechat(
                        f"【买点】{name}",
                        f"股票：{name} ({sym})\n时间：{t_str}（北京时间）\n"
                        f"价格：{price:.3f}\n依据：回踩均价线缩量 + MACD 拐头向上\n"
                        f"偏离均价：{(price / float(row['AvgPrice']) - 1) * 100:.2f}%\n\n"
                        f"仅做参考，请自行判断。"
                    )
                    if ok:
                        log.setdefault(today, []).append(key)
                        print(f"  🔴 {name}({sym}) 买点 {price:.3f} @ {t_str} → 已推送")
                        pushed = True

        if not sell_pts.empty:
            recent = recent_rows(sell_pts)
            if not recent.empty:
                row = recent.loc[recent['Price'].idxmax()]
                price = float(row['Price'])
                key = key_of(sym, 'sell', price)
                if key not in log.get(today, []):
                    t_str = f"{row['Time'][:2]}:{row['Time'][2:]}"
                    ok = send_wechat(
                        f"【卖点】{name}",
                        f"股票：{name} ({sym})\n时间：{t_str}（北京时间）\n"
                        f"价格：{price:.3f}\n依据：冲高乖离均价线放量 + MACD 拐头向下\n"
                        f"偏离均价：{(price / float(row['AvgPrice']) - 1) * 100:+.2f}%\n\n"
                        f"仅做参考，请自行判断。"
                    )
                    if ok:
                        log.setdefault(today, []).append(key)
                        print(f"  🟢 {name}({sym}) 卖点 {price:.3f} @ {t_str} → 已推送")
                        pushed = True
        return pushed
    except Exception as e:
        print(f"  {sym} 巡检异常: {e}")
        return False


def main():
    print(f"===== 巡检开始 北京时间 {now_cn().strftime('%Y-%m-%d %H:%M:%S')} =====")
    print(f"🔧 配置检查：SEND_KEY={'已配置' if SEND_KEY else '缺失'} | "
          f"WATCHLIST={len(WATCHLIST)} 只 | 新鲜窗口={WINDOW_MIN} 分钟")
    if not SEND_KEY:
        print("✗ 未配置 SERVERCHAN_KEY。请到 GitHub 仓库 → Settings → Secrets and variables → "
              "Actions → New repository secret 添加 SERVERCHAN_KEY 后重试。")
        return 0
    if not WATCHLIST:
        print("✗ 未配置 WATCHLIST。请到 GitHub 仓库 → Settings → Secrets and variables → "
              "Actions → New repository secret 添加 WATCHLIST（逗号分隔的 6 位代码）。")
        return 0
    if not is_trading_time():
        if not FORCE_RUN:
            print(f"○ 非交易时段，跳过（北京时间 {now_cn().strftime('%H:%M')}）")
            return 0
        print(f"⚠️ FORCE_RUN=1，强制执行巡检（北京时间 {now_cn().strftime('%H:%M')}，非交易时段）")

    log = load_log()
    today = now_cn().strftime('%Y-%m-%d')
    market_change = get_market_change()
    print(f"上证 {market_change:+.2f}% | 监控 {len(WATCHLIST)} 只：{', '.join(WATCHLIST)}")

    pushed = 0
    for sym in WATCHLIST:
        if check_symbol(sym, market_change, log, today):
            pushed += 1
    save_log(log)
    print(f"===== 巡检结束，共推送 {pushed} 条 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
