#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内买卖点样本：盘后自动采集（intraday_harvest.py）

用途：不依赖 Streamlit 网页，由 GitHub Actions 在**收盘后**（北京 15:40，与全市场
      波段样本采集同一个 cron、独立并行 job）自动采集「日内买卖点样本」，采完发布成
      加密快照供网页端「📈 日内买卖点有效性验证」读取。这就是面板上那个
      「🧲 采集今日分时样本」按钮的云端等价物 —— 有了它，正常情况你什么都不用点。

环境变量（GitHub 仓库 Secrets / Variables）：
    BAND_KEY        配了才允许落盘（fail-closed：没密钥拒绝以明文落进 public 仓库的缓存）
    WATCHLIST       逗号分隔的自选股代码（采集范围来源之一）
    BAND_WATCHLIST  可选的额外手工名单（逗号分隔）
    GITHUB_*        由 Actions 注入，本脚本不读

命令行：
    python intraday_harvest.py                          # 采「最近一个交易日」，落盘 intraday_samples.enc
    python intraday_harvest.py --store plain --file intraday_samples.jsonl.gz
    python intraday_harvest.py --limit 5                # 冒烟：只采前 5 只
    python intraday_harvest.py --date 2026-09-23        # 补采指定交易日
    python intraday_harvest.py --stats                  # 只看报表，不采集

★★ 四条不许破（改这个文件之前先读完）：
  1. **绝不能调用主应用的 `load_watchlist()`**。它在 Actions 里既没有 watchlist.json、
     st.secrets 也是空 stub，于是会走进兜底分支返回**默认的三只标的**
     （`[DEFAULT_STOCK, '515880', '159915']`）—— 一批无关代码被灌进语料库，而且
     只数不为 0、不报错、日志完全正常，属于最难发现的那种错。采集范围**只**认
     环境变量与仓库里的记忆镜像，见 `collect_sources()`。
  2. **采集范围与 watcher.py 巡检逐字一致**（WATCHLIST + band_watch.json 里未结案的 +
     BAND_WATCHLIST）。这一点不是洁癖：这份语料要回答的是「**推给你的**那个买卖点准不准」，
     采样范围一旦宽于或窄于推送范围，统计出来的成绩就不对应你实际看到的东西。
  3. **信号/判定/统计全部走 `sample_harvest.load_app_namespace()` 抽出来的主应用真函数**，
     这里一行算式都不重写。重写一份就一定会漂移（本项目的老毛病）。
  4. **加密与 fail-closed 复用 `sample_harvest` 的 store 层**（`_wrap_plain` 那条链），
     不自己实现一遍 —— 未配密钥时**拒绝落盘**，绝不退回明文。

为什么不需要「补采漏掉的那一天」这种机制：分时接口只给最近一个交易日，所以本语料
**没法历史回填**，只能一天天攒（面板上已如实写明）。反过来，这也带来一个好性质：
节假日跑会拿到节前最后一个交易日的数据，重复采同一批样本 —— 而样本的键由
（代码/日期/方向/分钟/位置）唯一决定，重采结果逐字相同，**天然幂等**，不会污染库。
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import sample_harvest as sh  # noqa: E402

DEFAULT_ENC = os.path.join(HERE, "intraday_samples.enc")           # 加密语料（进缓存/可发布）
DEFAULT_PLAIN = os.path.join(HERE, "intraday_samples.jsonl.gz")    # 本地明文（已 gitignore）

EXIT_OK, EXIT_USAGE, EXIT_NOKEY, EXIT_FAIL = 0, 2, 3, 4


def _split_env(name):
    """把逗号分隔的环境变量切成代码列表（同时容忍中文逗号与空白）。"""
    raw = os.environ.get(name, "") or ""
    return [c.strip() for c in raw.replace("，", ",").split(",") if c.strip()]


def _norm(codes):
    """去重保序 + 补零到 6 位。**只对纯数字补零**，指数/带前缀的代码原样保留。"""
    out, seen = [], set()
    for c in codes or []:
        s = str(c or "").strip()
        if not s:
            continue
        s6 = s.zfill(6) if s.isdigit() else s
        if s6 not in seen:
            seen.add(s6)
            out.append(s6)
    return out


def collect_sources(override=""):
    """采集范围 = 自选股 ∪ 波段记忆（未结案）∪ 额外手工名单。

    返回 (codes, sources)。`sources` 是 [(来源名, [原始代码…]), …]，只用于日志 ——
    ★ 必须打印出来：「只采到 3 只」时能一眼看出是哪一处来源空了，
      而不是对着一个「共 3 只」发呆。
    """
    if override:
        codes = _norm([c for c in str(override).replace("，", ",").split(",") if c.strip()])
        return codes, [("--codes 指定", codes)]

    sources = []

    # ① 自选股 —— ★ 只认环境变量，绝不能走主应用的 load_watchlist()（见文件头第 1 条）
    wl = _split_env("WATCHLIST")
    sources.append(("WATCHLIST（自选股）", wl))

    # ② 波段记忆 —— 仓库里的 band_watch.json 是**加密的完整镜像**。
    #    复用 watcher.load_band_memory()（单一来源）：它会把「文件在但解不开」打成
    #    醒目横幅写 stderr，而不是安静地返回空 —— 那种情况必须一眼可见，否则表现为
    #    「自动采集突然只采自选股了」，没人会想到是密钥问题。
    mem_codes = []
    try:
        import watcher as _w
        mem = _w.load_band_memory()
        stocks = (mem or {}).get("stocks") if isinstance(mem, dict) else {}
        # ★ 与 watcher.check_band_memory 完全同一个筛选口径（排除已结案/已归档的），
        #   否则时间一长，历史归档会把采集范围越撑越大，而采那些票毫无意义。
        mem_codes = [c for c, n in (stocks or {}).items() if not (n or {}).get("closed")]
    except Exception as e:
        print(f"[intraday] !! 读波段记忆失败，本轮只用其它来源：{type(e).__name__}: {e}",
              file=sys.stderr)
    sources.append(("波段记忆（band_watch.json）", mem_codes))

    # ③ 额外手工名单（可选，不配就是空）
    extra = _split_env("BAND_WATCHLIST")
    sources.append(("BAND_WATCHLIST（额外）", extra))

    codes, seen = [], set()
    for _name, lst in sources:
        for c in _norm(lst):
            if c not in seen:
                seen.add(c)
                codes.append(c)
    return codes, sources


def _print_scope(codes, sources):
    print(f"采集范围：{len(sources)} 处来源 → 去重后共 {len(codes)} 只")
    for name, lst in sources:
        n = len(_norm(lst))
        print(f"  · {name:<28} {n:>3} 只")
    if codes:
        shown = ", ".join(codes[:30]) + (" …" if len(codes) > 30 else "")
        print(f"  实际清单：{shown}")


def do_stats(ns, rows_map, path):
    rows = list(rows_map.values())
    n = len(rows)
    print("=" * 72)
    print(f"日内样本语料库：{n} 条 ｜ 文件 {os.path.basename(path)}")
    print("=" * 72)
    if not n:
        print("（空库。首次运行或还没到收盘时间时会这样，属正常）")
        return EXIT_OK
    rng = ns["intraday_samples_range"](rows)
    st = ns["intraday_sample_lift"](rows)
    print(f"覆盖 {rng['n']} 个交易日（{rng['min']} → {rng['max']}）")
    for kind in ("buy", "sell"):
        sub = [r for r in rows if r.get("kind") == kind
               and ns["_intraday_sample_usable"](r)]
        hit = sum(1 for r in sub if (r.get("outcome") or {}).get("hit"))
        base = [float((r.get("baseline_day") or {}).get("rate")) for r in sub
                if (r.get("baseline_day") or {}).get("n")]
        b = (sum(base) / len(base)) if base else None
        label = ns["INTRADAY_SAMPLE_KIND_LABEL"].get(kind, kind)
        print(f"  {label}：已判定 {len(sub)} 条，达标 {hit} 条"
              f"（{round(hit / len(sub) * 100, 2) if sub else 0}%）"
              f"｜ 同日随机时点基准 {round(b, 2) if b is not None else '—'}%")
    print(f"整体：达标率 {st['hit_rate']}% ｜ 基准率 {st['baseline_rate']}%"
          f" ｜ 超额 {st['lift']}")
    print("  注：单日样本时「样本外」为空，分桶结论一律不下（stable=None），这是刻意的。")
    return EXIT_OK


def main():
    t0 = time.time()
    ap = argparse.ArgumentParser(
        description="日内买卖点样本：盘后采集（Actions 用；与网页端那个按钮同一套函数）")
    ap.add_argument("--store", choices=("plain", "enc"), default="enc",
                    help="落盘形态：enc=加密（默认，Actions 用）／plain=明文（本机排查用）")
    ap.add_argument("--file", default="", help="语料库路径（默认按 --store 推导）")
    ap.add_argument("--limit", type=int, default=0, help="只采前 N 只（冒烟测试用）")
    ap.add_argument("--date", default="", metavar="YYYY-MM-DD",
                    help="覆盖交易日（默认问接口要「最近一个交易日」，正常不用给）")
    ap.add_argument("--codes", default="", help="覆盖采集范围（调试用，逗号分隔）")
    ap.add_argument("--index", default="sh000001", help="大盘分时用的指数（默认上证）")
    ap.add_argument("--stats", action="store_true", help="只打印报表，不采集")
    args = ap.parse_args()

    enc = (args.store == "enc")
    path = args.file or (DEFAULT_ENC if enc else DEFAULT_PLAIN)

    print("=" * 72)
    print(f"日内买卖点样本 ｜ 落盘 {os.path.basename(path)}（{'加密' if enc else '明文'}）")
    print("=" * 72)

    print("[1] 抽取主应用的真函数（信号/判定/统计全部走线上那套，这里不重写算式）")
    ns, missing = sh.load_app_namespace()
    if missing:
        print(f"!! 以下函数没抽到（主应用改名了？）：{missing}")
        return EXIT_FAIL
    if not callable(ns.get("intraday_samples_harvest")):
        print("!! 没抽到 intraday_samples_harvest —— 这个版本的采集器不可用")
        return EXIT_FAIL
    # ★ 元组赋值常量（DEVIATION_MIN/MAX）是否真的抽到了 —— 漏了不会报错，
    #   只会让 dynamic_deviation 静默回退 0.008，于是采到的买卖点和页面上不是同一批。
    #   这里**显式校验**，因为这条静默失效恰好会毁掉这份语料的全部意义。
    print(f"  关键常量校验：DEVIATION_MIN={ns.get('DEVIATION_MIN')} "
          f"DEVIATION_MAX={ns.get('DEVIATION_MAX')} "
          f"观察窗口={ns.get('INTRADAY_SAMPLE_WINDOW')} 分钟")
    if ns.get("DEVIATION_MAX") is None or ns.get("DEVIATION_MIN") is None:
        print("!! 动态偏离阈值常量没抽到（加载器漏了元组赋值？）—— 拒绝在错误阈值下采集")
        return EXIT_FAIL

    if args.stats:
        rows_map = sh.load_store(ns, path, enc=enc, store=sh.INTRADAY_STORE)
        return do_stats(ns, rows_map, path)

    # fail-closed：要求加密却没有密钥时**在采集之前**就退出，别白跑一遍再去写盘时才发现
    if enc and not ns["band_crypto_enabled"]():
        print("!! 要求加密落盘，但本端没有 BAND_KEY —— 拒绝以明文落进 public 仓库的缓存。")
        print("   配置路径：本仓库 Settings → Secrets and variables → Actions → BAND_KEY，")
        print("   值与 Streamlit Secrets 里的 BAND_KEY 完全一致。")
        return EXIT_NOKEY

    print("[2] 解析采集范围（与 watcher 巡检同一口径）")
    codes, sources = collect_sources(args.codes)
    _print_scope(codes, sources)
    if not codes:
        # ★ 首次配置（WATCHLIST 还没设、记忆也是空的）会走到这里，属正常状态，
        #   所以**不能报错退出** —— 退出码非 0 会让 Actions 步骤变红，
        #   久了就没人信这个红灯了（同 sample_harvest 缺 BAND_KEY 的处理）。
        print("::warning::采集范围为空 —— 既没有自选股也没有波段记忆。"
              "正常情况请检查仓库 Secrets 里的 WATCHLIST。")
        return EXIT_OK

    if args.limit and args.limit > 0:
        print(f"  ⚠️ 冒烟测试：只采前 {args.limit} 只，这批**不代表完整范围**")

    print("[3] 采集（按当天分时构造样本，信号只用「当时能看到的数据」判）")

    def _cb(done, total, code):
        print(f"  [{done}/{total}] {code}")

    try:
        rows, rep = ns["intraday_samples_harvest"](
            codes, date=(args.date or None), limit=(args.limit or 0),
            progress_cb=_cb, market_index=args.index)
    except Exception as e:
        ns["_log"]("intraday_harvest", e)
        print(f"!! 采集失败：{type(e).__name__}: {e}")
        return EXIT_FAIL

    print(f"  交易日 {rep.get('date')}"
          + ("（**日期是猜的**：没问到交易日）" if rep.get("date_inferred") else "")
          + f" ｜ 取到分时 {rep.get('ok')}/{rep.get('codes')} 只"
          + f" ｜ 无信号 {rep.get('no_signal')} 只 ｜ 取不到分时 {rep.get('no_data')} 只")
    print(f"  大盘逐分钟涨跌：{'已取到' if rep.get('index_ok') else '**没取到，退回了实时快照**'}"
          f" ｜ 跳过窗口未走完的点 {rep.get('skipped_tail')} 个")
    if rep.get("fail_examples"):
        print(f"  取不到分时的示例：{rep['fail_examples']}")
    if rep.get("baseline"):
        b = rep["baseline"]
        print(f"  当日随机时点基准率：买点 {b.get('buy', {}).get('rate')}% "
              f"｜ 卖点 {b.get('sell', {}).get('rate')}%")
    if not rows:
        print("::warning::本次没有产出任何样本 —— 非交易时段、或当天分时确实判不出买卖点时如此。")
        print("  （观察窗口没走完的点会被跳过，所以盘中跑只会采到一部分；盘后跑才全）")
        return EXIT_OK

    print(f"[4] 并入语料库（去重键带方向与分钟，同一天多个点不会互相覆盖）")
    try:
        rows_map = sh.load_store(ns, path, enc=enc, store=sh.INTRADAY_STORE)
        before = len(rows_map)
        merged, added, updated, trimmed = ns["intraday_samples_merge"](rows_map, rows)
        n = sh.save_store(ns, merged, path, enc=enc, store=sh.INTRADAY_STORE)
    except sh.StoreError as e:
        print(f"!! 落盘被拒绝：{e}")
        return EXIT_NOKEY
    except Exception as e:
        ns["_log"]("intraday_harvest/save", e)
        print(f"!! 落盘失败：{type(e).__name__}: {e}")
        return EXIT_FAIL
    print(f"  库内 {before} → {n} 条（本次采 {len(rows)} 条，新增 {added}，覆盖 {updated}，"
          f"超上限裁剪 {trimmed}）")
    if added == 0 and before:
        print("  （本次没有新增 —— 重复采集是幂等的：同一天同一批点重采结果逐字相同）")
    do_stats(ns, merged, path)
    print(f"\n用时 {time.time() - t0:.1f} 秒")
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
