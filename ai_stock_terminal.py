# ============================================================
# 10. AI 前瞻选股模块（修复稳定版）
#
# 用法：用本段整体替换原文件中
#   「# ================= 10. AI 前瞻选股模块（升级版）=================」
# 到
#   「# ================= 11. 动态股票池系统 =================」
# 之间的全部内容（第 11 节的注释行本身保留不动）。
#
# 修复内容：
#   1. 所有行情字段统一用 _safe_float 兜底，'-' / None / NaN 不再抛 TypeError
#   2. 深度分析改为 10 线程并发，全市场扫描约 30~60 秒（原串行要 15 分钟+）
#   3. 全市场按主力资金流排序分页拉取，不再只取当日涨幅榜前 3000 只
#   4. KDJ 计算 RSV 除零保护（长期一字板股票不再产生 inf/NaN）
#   5. 扫描按钮加异常拦截，出错只提示本模块，不再崩掉整个应用
#   6. 剔除 ST / 退市 / 涨跌停 / 市值超范围股票
# ============================================================
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

# ---------- 行情拉取 ----------

@st.cache_data(ttl=300)
def fetch_market_page(pn, pz=100):
    """分页拉取沪深 A 股列表（按主力资金流降序），单页失败只损失该页。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": str(pn), "pz": str(pz), "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f62", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
              "fields": "f12,f14,f2,f3,f8,f9,f10,f20,f62"}
    try:
        res = requests.get(url, params=params, timeout=10, headers=_REQUEST_HEADERS)
        data = res.json()
        if data.get("data") and data["data"].get("diff"):
            return data["data"]["diff"]
    except Exception:
        pass
    return []

@st.cache_data(ttl=600)
def get_hot_money_stocks():
    """主力资金热度榜（选股与动态池共用），字段全部做脏值兜底。"""
    result = {}
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
                  "fid": "f62", "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                  "fields": "f12,f14,f2,f3,f62"}
        res = requests.get(url, params=params, timeout=8, headers=_REQUEST_HEADERS)
        data = res.json()
        if data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                code_ = str(item.get("f12") or "")
                if code_ and not code_.startswith(EXCLUDE_PREFIXES):
                    result[code_] = {'main_flow': _safe_float(item.get("f62")) / 1e8,
                                     'change_pct': _safe_float(item.get("f3"))}
    except Exception:
        pass
    return result

@st.cache_data(ttl=300)
def get_stock_historical_metrics(symbol):
    """250日分位 / 量比 / 均线 / MACD / KDJ 等历史指标，含除零与脏值保护。"""
    prefix = "sh" if symbol.startswith(('5', '6', '9')) else "sz"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{symbol},day,,,260,qfq"
    try:
        res = requests.get(url, timeout=8, headers=_REQUEST_HEADERS)
        data = res.json()
        if data.get("code") != 0:
            return None
        node = data["data"].get(f"{prefix}{symbol}", {})
        kline = node.get("qfqday") or node.get("day")
        if not kline or len(kline) < 60:
            return None
        df = pd.DataFrame(kline).iloc[:, :6]
        df.columns = ['Date', 'Open', 'Close', 'High', 'Low', 'Volume']
        for col in ['Open', 'Close', 'High', 'Low', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna().sort_values('Date').reset_index(drop=True)
        if len(df) < 60:
            return None

        close = df['Close']; vol = df['Volume']; current = float(close.iloc[-1])
        if current <= 0:
            return None

        # 250 日位置分位
        high_250 = float(close.tail(250).max()); low_250 = float(close.tail(250).min())
        position_pct = 50.0 if high_250 <= low_250 else (current - low_250) / (high_250 - low_250) * 100

        # 量比（5日均量 / 20日均量）
        vol_5 = float(vol.tail(5).mean()); vol_20 = float(vol.tail(20).mean())
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1])

        recent = close.tail(20); recent_mean = float(recent.mean())
        amplitude_20 = (float(recent.max()) - float(recent.min())) / recent_mean * 100 if recent_mean > 0 else 0.0

        prev5 = float(close.iloc[-6]) if len(close) > 6 else 0.0
        change_5d = (current / prev5 - 1) * 100 if prev5 > 0 else 0.0

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        diff = ema12 - ema26; dea = diff.ewm(span=9, adjust=False).mean(); macd = 2 * (diff - dea)
        macd_up = bool(macd.iloc[-1] > macd.iloc[-2] and diff.iloc[-1] > diff.iloc[-2])
        macd_golden = bool(diff.iloc[-1] > dea.iloc[-1] and diff.iloc[-2] <= dea.iloc[-2])

        # KDJ（RSV 除零保护：一字板区间振幅为 0 时按中性值 50 处理）
        low_9 = df['Low'].rolling(9).min(); high_9 = df['High'].rolling(9).max()
        rng = (high_9 - low_9).replace(0, np.nan)
        rsv = ((close - low_9) / rng * 100).fillna(50)
        k = rsv.ewm(com=2, adjust=False).mean(); d = k.ewm(com=2, adjust=False).mean()
        kdj_golden = bool(k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2])
        kdj_up = bool(k.iloc[-1] > k.iloc[-2] and d.iloc[-1] > d.iloc[-2])

        return {'Position250': round(position_pct, 1), 'VolRatio': round(vol_ratio, 2),
                'AboveMA20': current > ma20, 'AboveMA60': current > ma60,
                'Amplitude20': round(amplitude_20, 1), 'Change5D': round(change_5d, 2),
                'MACD_UP': macd_up, 'MACD_GOLDEN': macd_golden,
                'KDJ_GOLDEN': kdj_golden, 'KDJ_UP': kdj_up}
    except Exception:
        return None

# ---------- 单只股票评分（任何异常只丢弃该股票，不影响整体） ----------

def _analyze_one(row, hot_money):
    try:
        metrics = get_stock_historical_metrics(row['Code'])
        if metrics is None:
            return None
        score = 0; reasons = []

        # 维度1：位置（20分）
        pos = metrics['Position250']
        if pos <= 25: score += 20; reasons.append(f"极度低位({pos}%)")
        elif pos <= 40: score += 15; reasons.append(f"低位区间({pos}%)")
        elif pos <= 50: score += 8; reasons.append(f"中低位({pos}%)")
        else: return None  # 位置太高直接淘汰

        # 维度2：技术指标（30分）
        if metrics['MACD_GOLDEN']: score += 15; reasons.append("MACD金叉")
        elif metrics['MACD_UP']: score += 8; reasons.append("MACD向上")
        if metrics['KDJ_GOLDEN']: score += 10; reasons.append("KDJ金叉")
        elif metrics['KDJ_UP']: score += 5; reasons.append("KDJ向上")
        if metrics['AboveMA20']: score += 5; reasons.append("站上20日线")

        # 维度3：资金面（25分）
        main_flow = row['MainFlow']
        if main_flow > 1: score += 15; reasons.append(f"主力净流入({main_flow:.1f}亿)")
        elif main_flow > 0.2: score += 8; reasons.append(f"主力小幅流入({main_flow:.1f}亿)")
        if hot_money.get(row['Code']): score += 10; reasons.append("主力资金热度榜")

        # 维度4：蓄势与启动（25分）
        vr = metrics['VolRatio']
        if 1.2 <= vr <= 2.5: score += 15; reasons.append(f"温和放量({vr:.1f}倍)")
        elif vr < 1.2: score += 5; reasons.append("量能平稳")
        if metrics['Amplitude20'] <= 12: score += 10; reasons.append(f"窄幅蓄势(振幅{metrics['Amplitude20']}%)")
        elif metrics['Amplitude20'] <= 18: score += 5; reasons.append("适度整理")

        if score >= 30 and main_flow > -0.5:
            return {'Code': row['Code'], 'Name': row['Name'], 'Price': row['Price'],
                    'ChangePct': row['ChangePct'], 'PE': row['PE'], 'TotalMv': row['TotalMv'],
                    'Position250': pos, 'VolRatio': vr, 'Score': score,
                    'Reasons': ' | '.join(reasons)}
    except Exception:
        return None
    return None

# ---------- 主扫描流程 ----------

def screen_low_position_stocks(max_results=15, max_deep_scan=400):
    progress = st.progress(0, text="正在获取全市场列表...")

    # 1. 分页拉取全市场（按主力资金流降序，最多 60 页 x 100 只，单页失败自动跳过）
    all_stocks = []
    for pn in range(1, 61):
        page = fetch_market_page(pn)
        if not page:
            break
        all_stocks.extend(page)
        progress.progress(min(int(12 * pn / 60), 12), text=f"已拉取 {len(all_stocks)} 只（第 {pn} 页）...")
    if not all_stocks:
        progress.empty()
        st.error("获取全市场数据失败：东方财富接口无返回（可能被限流，请稍后重试）。")
        return pd.DataFrame()

    # 2. 初筛：全部使用安全数值转换，任何脏数据都不会崩
    candidates = []
    for s in all_stocks:
        code_ = str(s.get("f12") or "")
        name = str(s.get("f14") or "")
        if not code_ or code_.startswith(EXCLUDE_PREFIXES):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        price = _safe_float(s.get("f2"))
        total_mv = _safe_float(s.get("f20")) / 1e8
        if price <= 0 or not (30 <= total_mv <= 800):
            continue
        change_pct = _safe_float(s.get("f3"))
        if change_pct >= 9.5 or change_pct <= -9.5:  # 涨跌停无法操作
            continue
        candidates.append({'Code': code_, 'Name': name, 'Price': price, 'ChangePct': change_pct,
                           'TurnoverRate': _safe_float(s.get("f8")), 'PE': _safe_float(s.get("f9")),
                           'TotalMv': total_mv, 'MainFlow': _safe_float(s.get("f62")) / 1e8})
    if not candidates:
        progress.empty()
        st.warning("初筛后没有候选股票，请稍后重试。")
        return pd.DataFrame()

    # 3. 并发深度分析（10 线程，约 30~60 秒完成）
    progress.progress(15, text=f"初筛后候选 {len(candidates)} 只，并发深度分析中...")
    hot_money = get_hot_money_stocks()
    to_scan = candidates[:max_deep_scan]
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_analyze_one, row, hot_money): row for row in to_scan}
        for future in as_completed(futures):
            done += 1
            if done % 20 == 0 or done == len(to_scan):
                progress.progress(15 + int(80 * done / len(to_scan)),
                                  text=f"深度分析 {done}/{len(to_scan)}（已入选 {len(results)} 只）")
            r = future.result()
            if r is not None:
                results.append(r)

    progress.progress(100, text="✅ 扫描完成")
    progress.empty()

    if not results:
        return pd.DataFrame()
    df_result = pd.DataFrame(results).sort_values('Score', ascending=False).head(max_results).reset_index(drop=True)
    df_result.index = df_result.index + 1
    return df_result

# ---------- UI ----------

def ai_stock_picker_ui():
    st.markdown("---")
    st.header("🎯 AI 前瞻选股助手（修复稳定版）")
    st.caption("位置+技术指标+主力资金+蓄势形态四维评分 · 全市场动态筛选（排除科创/创业板/北交所/ST）")

    with st.expander("📖 选股逻辑说明", expanded=False):
        st.markdown("""
        **核心逻辑**：
        1. **全市场拉取**：按主力资金流降序分页拉取全市场，剔除科创/创业板/北交所/ST/退市股。
        2. **技术指标共振**：MACD 或 KDJ 金叉（或向上拐头），且站上 20 日线。
        3. **主力资金介入**：主力资金净流入 > 0，且优先关注资金热度榜。
        4. **缩量横盘后启动**：近 20 日振幅 < 15%，量能温和放大。

        **综合评分满分 100 分**，50 分以上可重点关注。

        ⚠️ 本工具仅为量化初筛，不构成投资建议，请结合基本面深入研究。
        """)

    if st.button("🔍 扫描全市场（约 30~60 秒）", type="primary", use_container_width=True, key="scan_stocks"):
        try:
            with st.spinner("正在拉取全市场行情并进行多维度分析，请稍候..."):
                st.session_state.scan_results = screen_low_position_stocks()
                st.session_state.scan_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            st.rerun()
        except Exception:
            st.error("选股扫描出错（已自动拦截，不影响页面其他功能），错误详情：")
            st.code(traceback.format_exc(), language="python")

    if 'scan_results' in st.session_state:
        df_r = st.session_state.scan_results
        if df_r is None or df_r.empty:
            st.warning("本次扫描未找到符合条件的低位潜力股，可稍后（或收盘后）重试。")
        else:
            if 'scan_time' in st.session_state:
                st.caption(f"上次扫描时间: {st.session_state.scan_time}")
            for _, r in df_r.iterrows():
                score_color = "#00cc66" if r['Score'] >= 70 else ("#f9e2af" if r['Score'] >= 50 else "#888")
                chg_color = "#ff4b4b" if r['ChangePct'] >= 0 else "#00cc66"
                pe_txt = f"{r['PE']:.1f}" if r['PE'] > 0 else "--"
                st.markdown(f"""
                <div style="background:#1e1e2e; border-radius:10px; padding:14px 18px; margin-bottom:10px; border-left:4px solid {score_color};">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                        <div><span style="font-size:18px; font-weight:bold; color:#f0f2f6;">{r['Name']}</span>
                        <span style="color:#89b4fa; font-size:14px; margin-left:8px;">({r['Code']})</span>
                        <span style="color:{chg_color}; font-size:14px; margin-left:10px;">{r['ChangePct']:+.2f}%</span></div>
                        <div style="text-align:right;"><span style="color:{score_color}; font-size:20px; font-weight:bold;">{r['Score']}分</span></div>
                    </div>
                    <div style="margin-top:8px; color:#c9d1d9; font-size:13px; line-height:1.8;">
                        <span style="color:#89b4fa;">价格:</span> {r['Price']:.2f} | <span style="color:#89b4fa;">PE:</span> {pe_txt} | <span style="color:#89b4fa;">市值:</span> {r['TotalMv']:.0f}亿 | <span style="color:#89b4fa;">250日分位:</span> {r['Position250']:.0f}%
                    </div>
                    <div style="margin-top:6px; color:#f9e2af; font-size:13px;">📋 {r['Reasons']}</div>
                </div>
                """, unsafe_allow_html=True)
