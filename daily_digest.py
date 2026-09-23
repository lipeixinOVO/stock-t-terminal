# -*- coding: utf-8 -*-
"""每日 18:00 全量日报（无头，由 GitHub Actions 调用，经 Server酱 推微信）。

为什么单独一个脚本，而不是塞进 watcher.py：
  watcher.py 每 5 分钟跑一次，职责是「盘中命中就推」——它要的是**及时**；
  日报要的是**日终汇总**，而且要看语料库、算「逻辑有效性」报表，那些函数都在
  ai_stock_terminal.py 里。所以这里复用 sample_harvest.py 那套 AST 加载器把主应用当库用，
  **绝不重写第二份口径** —— 口径漂移过一次（分时买卖点曾主图 0.4 / 巡检 0.5）就再也对不上账。

环境变量：
  SERVERCHAN_KEY    必填；缺了只打印不推送，返回 0（不把定时任务判红）
  BAND_KEY          选填；解开加密语料 / 加密摘要用
  BAND_SAMPLES_ENC  选填，加密语料路径（默认 band_samples.enc）
  BAND_WATCH        选填，波段摘要路径（默认 band_watch.json）
  NOTIFY_LOG        选填，去重记录路径（默认 notify_log.json）
  DIGEST_STATE      选填，日报自己的状态文件（默认 digest_state.json）
  DIGEST_DRY        选填，=1 时只打印不推送（本地调试用）

★ 数据缺失时的原则：**如实说缺什么，绝不编**。
  语料读不到、摘要解不开、推送日志没拿到 —— 都进「数据与异常」一节，并计入退出码 0 的正常返回
  （定时任务的失败信号只留给「脚本本身崩了」这一种情况，否则红灯久了就没人信）。
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sample_harvest as sh        # noqa: E402  （复用它的 AST 加载器与语料读写）

SEND_KEY = os.environ.get("SERVERCHAN_KEY", "").strip()
BAND_KEY = os.environ.get("BAND_KEY", "").strip()
ENC_PATH = os.environ.get("BAND_SAMPLES_ENC", os.path.join(HERE, "band_samples.enc"))
WATCH_PATH = os.environ.get("BAND_WATCH", os.path.join(HERE, "band_watch.json"))
LOG_PATH = os.environ.get("NOTIFY_LOG", os.path.join(HERE, "notify_log.json"))
# ★ 手动推送账本（2026-09-22 新增）。自动推送已关闭，今天推了几条
#   只能从这份账本看 —— 巡检的 notify_log 现在恒为空。
BUDGET_PATH = os.environ.get("NOTIFY_BUDGET", os.path.join(HERE, "notify_budget.json"))
# ★ 自动推送白名单（2026-09-22 新增）：只有名单里的票出现信号才自动发微信
#   （日内买卖点 + 该票的波段启动/结束点）。日报要把名单只数写出来 ——
#   否则「今天为什么一条都没自动发」在日报里根本无从判断。
WHITELIST_PATH = os.environ.get("NOTIFY_WHITELIST",
                                os.path.join(HERE, "notify_whitelist.json"))
STATE_PATH = os.environ.get("DIGEST_STATE", os.path.join(HERE, "digest_state.json"))
# ★ 日报正文的落盘位置（2026-09-22 新增）。为什么要落盘：
#   用户原话「万一说没有微信通知的机会了的话，怎么办？最好在网页中有地方可以呈现，
#   让我无论是在微信上还是在网页上都能看到」。微信额度只有 5 条，日报要预留的那 1 条
#   也可能因为别的原因没发出去；所以正文同时存一份到仓库，网页端解密后原样显示。
#   ⚠️ 仓库是 **public** —— 必须经 band_encrypt_obj 加密后写，且**没配 BAND_KEY 时拒绝落盘**
#     （见 save_last）。宁可网页端看不到，也不能把股票代码明文提交上去。
LAST_PATH = os.environ.get("DIGEST_LAST", os.path.join(HERE, "digest_last.json"))
SAVE_LAST = os.environ.get("DIGEST_SAVE", "1").strip() != "0"
DRY = os.environ.get("DIGEST_DRY", "").strip() == "1"


def _log(where, err):
    print(f"[digest][{where}] {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)


def _cn_now():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8)))


def send_wechat(title, content):
    """Server酱推送。与 watcher.py 同一个接口与同一条 key。"""
    if not SEND_KEY:
        return False
    try:
        import requests
        res = requests.post(f"https://sctapi.ftqq.com/{SEND_KEY}.send",
                            data={"title": title, "desp": content}, timeout=15)
        print(f"  推送 HTTP {res.status_code}: {res.text[:200]}")
        return res.status_code == 200
    except Exception as e:
        _log("send_wechat", e)
        return False


def load_state():
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception as e:
        # 状态文件坏掉只会让「今日变化」变成「无对比对象」，不该让整个日报失败
        _log("load_state", e)
    return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log("save_state", e)
        return False


def load_corpus(ns):
    """读云端/本地加密语料 → (rows, err)。没有文件＝还没采过（正常），解不开＝要报出来。"""
    if not os.path.exists(ENC_PATH):
        return [], f"没有 {os.path.basename(ENC_PATH)}（本次运行没承接上语料缓存）"
    if not BAND_KEY:
        return [], "没有 BAND_KEY，读不了加密语料"
    try:
        rows = sh.load_store(ns, ENC_PATH, enc=True)
        return list(rows.values()), ""
    except Exception as e:
        _log("load_corpus", e)
        return [], f"语料读取/解密失败：{e}"


def load_watch(ns):
    """读波段摘要（可能是密文）→ (digest, err)。"""
    if not os.path.exists(WATCH_PATH):
        return None, f"没有 {os.path.basename(WATCH_PATH)}（网页端还没同步过）"
    try:
        with open(WATCH_PATH, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None, "波段摘要不是 JSON 对象"
        ok, digest, err = ns["band_decrypt_obj"](payload)
        if not ok:
            return None, f"波段摘要解不开：{err}"
        if not isinstance((digest or {}).get("stocks"), dict):
            return None, "波段摘要结构异常（没有 stocks）"
        return digest, ""
    except Exception as e:
        _log("load_watch", e)
        return None, f"波段摘要读取失败：{e}"


def load_notify_log():
    """读今天的推送去重记录 → (keys, err)。结构：{"YYYY-MM-DD": [key, ...]}"""
    if not os.path.exists(LOG_PATH):
        return [], f"没有 {os.path.basename(LOG_PATH)}（本次运行没承接上巡检缓存）"
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        today = _cn_now().strftime("%Y-%m-%d")
        keys = (d or {}).get(today) or []
        return [str(k) for k in keys], ""
    except Exception as e:
        _log("load_notify_log", e)
        return [], f"推送日志读取失败：{e}"


def load_notify_budget(ns):
    """读推送账本（可能是密文）→ (budget 或 None, err)。

    账本不存在不算错：今天还没手动推过就没有这个文件，
    审计口径在 sec_notify 里如实写出来就行，不要把它当异常告警。
    ★ 但“文件在却解不开”必须报错 —— 那意味着账本可能正在丢失，
      而不是“今天没推”。
    """
    if not os.path.exists(BUDGET_PATH):
        return None, ""
    try:
        with open(BUDGET_PATH, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None, "推送账本不是 JSON 对象"
        ok, budget, err = ns["band_decrypt_obj"](payload)
        if not ok:
            return None, f"推送账本解不开：{err}"
        return budget, ""
    except Exception as e:
        _log("load_notify_budget", e)
        return None, f"推送账本读取失败：{e}"


def load_notify_whitelist(ns):
    """读自动推送白名单（可能是密文）→ (whitelist 或 None, err)。

    不存在**不算错**（名单为空＝谁都不自动发，这是默认状态，与上一版行为一致）；
    但"文件在却解不开"必须报出来 —— 那会让云端**静默地一条都不自动发**，
    表现成「我明明勾了却不生效」，比直接报错难查得多。
    """
    if not os.path.exists(WHITELIST_PATH):
        return None, ""
    try:
        with open(WHITELIST_PATH, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None, "自动推送白名单不是 JSON 对象"
        ok, wl, err = ns["band_decrypt_obj"](payload)
        if not ok:
            return None, f"自动推送白名单解不开：{err}"
        return wl, ""
    except Exception as e:
        _log("load_notify_whitelist", e)
        return None, f"自动推送白名单读取失败：{e}"


def today_budget(budget):
    """只看**今天**那份账：跨天的 sent 不算今天的推送。"""
    if not isinstance(budget, dict):
        return None
    if str(budget.get("date") or "") != _cn_now().strftime("%Y-%m-%d"):
        return None
    return budget


def code_of_notify_key(key):
    """去重 key 的第一段就是股票代码（见 watcher.py 的 key 构造）。"""
    return str(key).split("|")[0].strip()


def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v:+.2f}%"


# ============================ 各段落 ============================

def sec_header():
    today = _cn_now().strftime("%Y-%m-%d %H:%M")
    return [f"**{today}（北京时间）**", ""]


def sec_corpus(ns, rows, state):
    """一、语料库现状 + 今日采集"""
    lines = ["## 一、样本语料", ""]
    if not rows:
        lines.append("- 读不到语料（见最后一节），跳过采集统计")
        return lines
    summ = ns["band_sample_summary"](rows)
    rng = ns["band_samples_range"](rows)
    fwd = summ.get("forward", {})
    bf = summ.get("backfill", {})
    lines.append(f"- 前瞻样本 **{fwd.get('total', 0)} 条**"
                 f"（已结案 {fwd.get('closed', 0)}，可交易 {fwd.get('tradable', 0)}）"
                 f"　← 这一栏才是能当成绩用的")
    lines.append(f"- 回填样本 {bf.get('total', 0)} 条（已结案 {bf.get('closed', 0)}）"
                 f"　← 自带前视偏差，只提假设")
    lines.append(f"- 覆盖 {rng['codes']} 只 / 区间 {rng['min_date']} ～ {rng['max_date']}")

    # 今日采到的（= 前瞻样本里日期最新的那一天）
    fwd_rows = [r for r in rows if r.get("source") == "forward" and r.get("date")]
    if fwd_rows:
        latest = max(str(r["date"]) for r in fwd_rows)
        todays = [r for r in fwd_rows if str(r["date"]) == latest]
        tiers = {}
        for r in todays:
            tiers[r.get("tier") or "unknown"] = tiers.get(r.get("tier") or "unknown", 0) + 1
        tdesc = "、".join(f"{ns['SAMPLE_TIER_LABEL'].get(k, k)} {v}"
                        for k, v in sorted(tiers.items(), key=lambda kv: -kv[1]))
        lines.append(f"- 最近一批（{latest}）**{len(todays)} 条**：{tdesc}")
        # ★ 数量对不上会被当成 bug：停牌股没有新K线，它的「最后一根」还停在停牌前那天，
        #   所以按最新日期一数就少几条。口径本身就是「按该股最后一根K线日期记」，是对的。
        stale_n = len(fwd_rows) - len(todays)
        if stale_n > 0:
            lines.append(f"　（另有 {stale_n} 条停牌股，最新K线还停在停牌前那天，故不计入本批）")
    prev_n = state.get("forward_n")
    if isinstance(prev_n, int):
        delta = fwd.get("total", 0) - prev_n
        lines.append(f"- 相对上次日报：{'新增 ' + str(delta) + ' 条' if delta >= 0 else '减少 ' + str(-delta) + ' 条'}"
                     f"（上次 {prev_n} 条）")
    return lines


def sec_lift(ns, rows):
    """二、逻辑有效性 —— 每个入场条件到底有没有贡献超额"""
    lines = ["## 二、逻辑有效性（前瞻样本，可比成绩）", ""]
    fwd_n = sum(1 for r in rows if r.get("source") == "forward")
    if not fwd_n:
        lines.append("- 还没有前瞻样本，无法评估逻辑（回填样本只用来提假设，不在这里下结论）")
        return lines
    try:
        lift = ns["band_sample_lift"](rows, source="forward", tradable_only=True)
    except Exception as e:
        _log("sec_lift", e)
        lines.append(f"- 报表计算失败：{e}")
        return lines
    base = lift.get("baseline") or {}
    pend_n = sum(1 for r in rows
                 if r.get("source") == "forward"
                 and not (r.get("outcome") or {}).get("closed"))
    if not base.get("n"):
        # ★ 前瞻样本「入场即最后一根K线」→ 采下来当天必然**全部**是 pending。
        #   这段文字会连续生效很多天（第一批要几个交易日后才结案），不解释清楚
        #   用户打开日报只会看到一片空，会以为日报坏了 —— 那比数字难看更糟。
        lines.append(f"- 已采到前瞻样本 **{fwd_n} 条**，其中 **{pend_n} 条还在等结案**。")
        lines.append(f"- 前瞻样本必须等入场之后走完行情才结算（止盈 +{ns['REVIEW_TP_PCT']:g}%、"
                     f"跌破 20 日线、或最多持有 {ns['REVIEW_MAX_HOLD_DAYS']} 个交易日），"
                     f"所以**刚采下来这批暂时没有成绩，这里空着是正常的、不是出错**。")
        lines.append(f"- 第一批结案后会自动出现在这里，最晚不超过 {ns['REVIEW_MAX_HOLD_DAYS']} 个交易日。")
        return lines
    lines.append(f"- 基准率：n={base.get('n', 0)}　胜率 {base.get('win_rate')}%　"
                 f"平均超额 {fmt_pct(base.get('avg_excess'))}　平均收益 {fmt_pct(base.get('avg_ret'))}")
    lines.append(f"- 样本外门槛：只有样本内外同号（stable）的桶才算站得住；样本量下限 {ns['REVIEW_MIN_SAMPLE']}")
    lines.append("")
    shown = 0
    cur_dim = None
    for r in sorted(lift.get("rows") or [], key=lambda x: (x.get("dim") or "", -x.get("n", 0))):
        if r.get("n", 0) < ns["REVIEW_MIN_SAMPLE"]:
            continue
        if r.get("dim") != cur_dim:
            cur_dim = r.get("dim")
            lines.append(f"**{cur_dim}**")
        flag = "✅" if r.get("stable") else "⚠️"
        lines.append(f"- {flag} {r['bucket']}：n={r['n']}　胜率 {r['win_rate']}%"
                     f"　超额 {fmt_pct(r['avg_excess'])}　相对基准 {fmt_pct(r['lift'])}"
                     f"　样本外 {fmt_pct(r.get('oos_excess'))}(n={r.get('oos_n', 0)})")
        shown += 1
    if not shown:
        lines.append("- 各分桶样本量都还不够，暂不给结论（继续攒）")
    return lines


def sec_memory(ns, watch, state):
    """三、波段记忆现状 + 今日变化"""
    lines = ["## 三、波段记忆", ""]
    if not watch:
        lines.append("- 读不到云端摘要（见最后一节）")
        return lines
    stocks = watch.get("stocks") or {}
    alert_names = ns.get("BAND_ALERT_STATUSES") or ()
    entry_names = ns.get("BAND_ENTRY_STATUSES") or ()
    if not alert_names or not entry_names:
        # 抽不到状态集合必须说出来：否则下面会静默数成 0，看着像「今天一个预警都没有」
        _log("load_app_namespace", RuntimeError("BAND_ALERT/ENTRY_STATUSES 未抽到"))
    active = {c: n for c, n in stocks.items() if not (n or {}).get("closed")}
    alert = [c for c, n in active.items()
             if (n or {}).get("status") in alert_names]
    entry = [c for c, n in active.items()
             if (n or {}).get("status") in entry_names]
    lines.append(f"- 在跟踪 **{len(active)} 只**：启动 {len(entry)} ／ 结束预警 {len(alert)}"
                 f" ／ 累计记录 {len(stocks)}")
    if alert:
        names = "、".join(f"{(stocks[c] or {}).get('name') or c}（{c}）" for c in sorted(alert)[:8])
        lines.append(f"- ⚠️ 需要处理（结束预警）：{names}")
    prev = (state.get("statuses") or {})
    if prev:
        changed = []
        for c, n in active.items():
            old = prev.get(c)
            new = (n or {}).get("status")
            if old and new and old != new:
                changed.append(f"{(n or {}).get('name') or c}（{c}）{old} → **{new}**")
        added = [f"{(stocks[c] or {}).get('name') or c}（{c}）" for c in active if c not in prev]
        removed = [c for c in prev if c not in active]
        if added:
            lines.append(f"- 今日新增入册：{'、'.join(added[:10])}")
        if changed:
            lines.append(f"- 今日状态变化：{'；'.join(changed[:10])}")
        if removed:
            lines.append(f"- 已归档/移除：{'、'.join(removed[:10])}")
        if not (added or changed or removed):
            lines.append("- 今日状态无变化")
    else:
        lines.append("- （首次运行，无对比基准，明天开始会给出「今日变化」）")
    return lines


def sec_notify(keys, err, budget=None, budget_err="", whitelist=None, wl_err=""):
    """四、今日推送统计。

    ★ 2026-09-22 起以**账本**为主口径：自动推送只在**白名单**命中时才发生
      （日内买卖点 + 该票的波段启动/结束点），其余全靠手动点 ——
      所以账本是唯一能反映"今天究竟发了几条"的地方（巡检的 notify_log 恒为空）。
    ★ 白名单只数必须写出来：不然「今天为什么一条都没自动发」无从判断。
    """
    lines = ["## 四、今日盘中推送", ""]
    if budget_err:
        lines.append(f"- ⚠️ {budget_err}")
    if wl_err:
        lines.append(f"- ⚠️ {wl_err}")
    _wl_items = [it for it in ((whitelist or {}).get("items") or []) if isinstance(it, dict)]
    lines.append(
        f"- 自动推送白名单：**{len(_wl_items)} 只**"
        + (f"（{'、'.join(str(it.get('code')) for it in _wl_items[:12])}）"
           if _wl_items else "（空 —— 谁都不自动发）"))
    # ★ 2026-09-22：门槛（最低推送置信度）也要写出来 —— 它就是「今天为什么没推」的答案。
    #   缺字段时**不在这里补默认值**：默认值只在应用与巡检里有且只有一处，
    #   日报再写死一份就成了第二个口径（口径漂移的代价见过一次了）。
    _mc = str((whitelist or {}).get("min_conf") or "").strip()
    lines.append("- 最低推送置信度："
                 + (f"**{_mc}**" if _mc else "未设置（按代码默认）")
                 + " —— 够不上的日内买卖点不发微信")
    tb = today_budget(budget)
    if tb is None:
        lines.append("- 今日推送：还没有账本记录（没推过，或账本日期还是旧的）")
    else:
        sent = tb.get("sent") or []
        lines.append(f"- 今日推送 **{len(sent)} / {tb.get('limit')}** 条"
                     f"（其中 {tb.get('reserved')} 条预留本日报；"
                     f"白名单自动 + 手动共用）")
        for it in sent[:10]:
            _src = str(it.get("src") or "")
            _tag = "（自动）" if _src == "auto" else ("（手动）" if _src == "manual" else "")
            lines.append(f"  - {it.get('ts', '')}　{it.get('title', '')}{_tag}")
        if not sent:
            lines.append("  - （今天没有推送）")
    lines.append("- 推送方式：白名单里的票出现信号会**自动发**；"
                 "其余只登记候选，由网页端「📤 今日推送」手动点")
    if err:
        lines.append(f"- {err}")
    elif keys:
        lines.append(f"- 巡检去重记录里有 {len(keys)} 条（历史遗留，不代表今天发过）")
    return lines


def sec_health(ns, notes, t_start, bench_ok, bench_cost):
    """五、数据与异常"""
    lines = ["## 五、数据与异常", ""]
    lines.append(f"- 沪深300 基准日线：{'取到' if bench_ok else '**没取到**'}"
                 f"（{bench_cost:.1f}s）"
                 + ("" if bench_ok else "　→ 这批样本的超额收益会是空的"))
    if notes:
        for n in notes:
            lines.append(f"- ⚠️ {n}")
    else:
        lines.append("- 语料 / 波段摘要 / 推送日志均正常，无异常")
    lines.append(f"\n_日报生成耗时 {time.time() - t_start:.1f}s。"
                 f"完整报表可在网页端「🧪 逻辑有效性验证」里看，采集由 15:40 的盘后任务自动完成。_")
    # ★ 告诉用户"微信没收到时去哪看"（2026-09-22）：正文会加密同步到仓库，
    #   网页端「🧠 波段记忆」页的「📰 18:00 日报」里能读到**同一条**。
    lines.append("_这条日报的正文已同步到网页端「🧠 波段记忆」→「📰 18:00 日报」；"
                 "没收到微信（例如当天额度用完）时去那里看得到一模一样的全文。_")
    return lines


def build_digest(ns, rows, watch, keys, log_err, budget, budget_err,
                 notes, state, bench_ok, bench_cost, t_start, wl=None, wl_err=""):
    """把五节拼成一份日报正文并返回。

    ★ 抽出来的唯一理由（2026-09-22）：**正文只允许在这一处生成**。
      网页端要能回看"和微信里一模一样的那条"，靠的就是读这里落盘的那一份；
      如果让网页另写一套拼装，两边早晚漂移 —— 这个项目已经因为口径漂移吃过一次亏
      （分时买卖点主图 0.4 / 巡检 0.5），所以宁可多传几个参数也不复制逻辑。
    """
    body = []
    body += sec_header()
    body += sec_corpus(ns, rows, state)
    body.append("")
    body += sec_lift(ns, rows)
    body.append("")
    body += sec_memory(ns, watch, state)
    body.append("")
    body += sec_notify(keys, log_err, budget, budget_err, wl, wl_err)
    body.append("")
    body += sec_health(ns, notes, t_start, bench_ok, bench_cost)
    return "\n".join(body)


def save_last(ns, title, text):
    """把日报正文**加密**落盘，供网页端回看 → (ok, msg)。

    ★ fail-closed（与 band_watch.json 同一条红线，不许改）：
      - 没配 BAND_KEY → **拒绝写**。日报正文里有股票代码与名称，
        明文提交等于把清单直接公开在 public 仓库里。宁可网页端看不到，也不明文落盘。
      - 加密本身失败 → 抛出来、返回失败，绝不退化成明文写盘。
    """
    if not SAVE_LAST:
        return False, "DIGEST_SAVE=0，本次跳过落盘"
    try:
        if not ns["band_crypto_enabled"]():
            return False, ("没配 BAND_KEY —— 日报正文含股票代码，拒绝以明文写进 public 仓库"
                           "（配好 BAND_KEY 后网页端才能回看）")
    except Exception as e:
        _log("save_last/crypto_check", e)
        return False, f"加密可用性检查失败：{e}"
    try:
        payload = ns["band_encrypt_obj"]({
            "date": _cn_now().strftime("%Y-%m-%d"),
            "title": title,
            "text": text,
            "saved_at": _cn_now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        with open(LAST_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True, f"已加密写入 {os.path.basename(LAST_PATH)}"
    except Exception as e:
        _log("save_last", e)
        return False, f"日报落盘失败：{e}"


# ============================ 主流程 ============================

def main():
    ap = argparse.ArgumentParser(description="每日 18:00 全量日报（无头，可定时）")
    ap.add_argument("--no-push", action="store_true", help="只打印不推送（等同 DIGEST_DRY=1）")
    args = ap.parse_args()
    t_start = time.time()

    ns, missing = sh.load_app_namespace()
    if missing:
        print(f"!! 主应用符号抽取失败，缺：{missing}")
        return 2

    notes = []
    rows, corpus_err = load_corpus(ns)
    if corpus_err:
        notes.append(f"语料不可用：{corpus_err}")
    watch, watch_err = load_watch(ns)
    if watch_err:
        notes.append(f"波段摘要不可用：{watch_err}")
    keys, log_err = load_notify_log()
    if log_err:
        notes.append(f"推送日志不可用：{log_err}")
    budget, budget_err = load_notify_budget(ns)
    if budget_err:
        notes.append(f"推送账本不可用：{budget_err}")
    whitelist, wl_err = load_notify_whitelist(ns)
    if wl_err:
        notes.append(f"自动推送白名单不可用：{wl_err}")

    # 数据源健康：真的去取一次基准，取不到就说取不到（样本的超额收益依赖它）
    bench_ok, bench_cost = False, 0.0
    t_b = time.time()
    try:
        bench = ns["_bench_history"]()
        bench_ok = bench is not None and len(bench) > 0
    except Exception as e:
        _log("bench", e)
        notes.append(f"基准取数异常：{e}")
    bench_cost = time.time() - t_b
    if not bench_ok:
        notes.append("基准（沪深300）本次没取到 —— 若采集当时也取不到，该批样本的超额收益为空")

    state = load_state()
    text = build_digest(ns, rows, watch, keys, log_err, budget, budget_err,
                        notes, state, bench_ok, bench_cost, t_start, whitelist, wl_err)

    print("=" * 72)
    print(text)
    print("=" * 72)

    title = f"做T助手日报 {_cn_now().strftime('%m-%d')}"
    if DRY or args.no_push:
        print("（DRY：跳过推送）")
    elif not SEND_KEY:
        print("!! 未配置 SERVERCHAN_KEY，跳过推送（网页端可在侧边栏看到配置说明）")
    else:
        print("推送结果：", "成功" if send_wechat(title, text) else "失败")

    # 落一份加密正文供网页端回看（2026-09-22）。落盘失败**不让日报判红**：
    # 定时任务的失败信号只留给"脚本本身崩了"，否则红灯久了就没人信（见文件头）。
    _saved, _saved_msg = save_last(ns, title, text)
    print(f"网页端回看：{'✅' if _saved else '⚠️'} {_saved_msg}")

    # ---- 落状态，供明天算「今日变化」----
    new_state = dict(state)
    new_state["date"] = _cn_now().strftime("%Y-%m-%d")
    if rows:
        summ = ns["band_sample_summary"](rows)
        new_state["forward_n"] = (summ.get("forward") or {}).get("total", 0)
        new_state["backfill_n"] = (summ.get("backfill") or {}).get("total", 0)
    if watch:
        new_state["statuses"] = {c: (n or {}).get("status") or ""
                                 for c, n in (watch.get("stocks") or {}).items()
                                 if not (n or {}).get("closed")}
    save_state(new_state)
    print(f"状态已更新：{STATE_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
    except Exception as e:                     # 崩了才是真失败，要红
        import traceback
        traceback.print_exc()
        print(f"!! 日报生成失败：{e}")
        sys.exit(1)
