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
LOG_FILE = os.environ.get("NOTIFY_LOG", "notify_log.json")
WINDOW_MIN = int(os.environ.get("ALERT_WINDOW_MIN", "10"))
FORCE_RUN = os.environ.get("FORCE_RUN", "0").strip() == "1"

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

def _get_daily_history(symbol):
    """获取日线前复权数据（波段结束预警用）。腾讯主/备域名轮询，返回至少 60 条。

    原先只打单个域名且 prefix 用 startswith 硬判，北交所/沪市转债会拿不到数据。"""
    code = get_code(symbol)
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
        try:
            df = pd.DataFrame(kline)
            if df.shape[1] < 6:
                continue
            df = df.iloc[:, :6]
            df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            for col in ['Open', 'Close', 'High', 'Low', 'Volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.dropna(subset=['Date', 'Close']).sort_values('Date').reset_index(drop=True)
        except Exception as e:
            print(f"  日线解析失败 {code}: {e}")
            continue
        if len(df) >= 60:
            return df
    return None


def check_band_end(sym, log, today):
    """检查单只股票的日线波段结束信号，同一股票每天只提醒一次。"""
    try:
        df = _get_daily_history(sym)
        if df is None or len(df) < 60:
            return False
        close = df['Close']; high = df['High']
        current = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        below_support = current < ma20

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
        if len(highs) >= 2:
            h1, h2 = highs.iloc[-2], highs.iloc[-1]
            if h2['High'] > h1['High'] and h2['macd'] < h1['macd']:
                top_divergence = True

        if not top_divergence and not below_support:
            return False

        key = f"{sym}_band_end_{today}"
        if key in log.get(today, []):
            return False

        name = get_stock_name(sym)
        reasons = []
        if top_divergence:
            reasons.append("顶背离（股价新高但MACD未新高）")
        if below_support:
            reasons.append(f"跌破20日线支撑（{ma20:.2f}）")

        ok = send_wechat(
            f"【波段结束预警】{name}",
            f"股票：{name} ({sym})\n日期：{today}\n"
            f"依据：{' + '.join(reasons)}\n\n"
            f"该股票波段可能结束，请注意止盈/止损。"
        )
        if ok:
            log.setdefault(today, []).append(key)
            print(f"  ⚠️ {name}({sym}) 波段结束预警 → 已推送")
            return True
    except Exception as e:
        print(f"  {sym} 波段预警异常: {e}")
    return False


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
                        f"价格：{price:.3f}\n依据：分时卖点触发（冲高乖离或日内高点滞涨）\n"
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
    # 北交所标的：腾讯接口不提供分时/日线，这里显式提示并跳过，
    # 避免「配置了却没任何输出」这种最难排查的静默失效。
    bj_syms = [s for s in WATCHLIST if quote_prefix(s) == 'bj']
    if bj_syms:
        print(f"⚠️ 北交所标的 {len(bj_syms)} 只（腾讯不提供行情，本次跳过）：{', '.join(bj_syms)}")
    tradeable = [s for s in WATCHLIST if quote_prefix(s) != 'bj']
    print(f"上证 {market_change:+.2f}% | 监控 {len(tradeable)} 只：{', '.join(tradeable) if tradeable else '（无）'}")

    pushed = 0
    for sym in tradeable:
        if check_symbol(sym, market_change, log, today):
            pushed += 1

    # 波段结束预警（日线级别，每天同一股票只提醒一次）
    band_alert = 0
    for sym in tradeable:
        if check_band_end(sym, log, today):
            band_alert += 1

    save_log(log)
    print(f"===== 巡检结束，共推送 {pushed} 条日内信号 / {band_alert} 条波段结束预警 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
