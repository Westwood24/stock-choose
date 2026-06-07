r"""
全量周频回测 — 使用本地 Parquet 数据库，全部 A 股（过滤 ST/退市）。
默认启用周线 MA 趋势过滤（config.USE_MA_TREND_FILTER）。
"""
import time, pickle
from collections import Counter
import pandas as pd, numpy as np

from local_data_fetcher import get_all_stock_codes, fetch_all_weekly_from_local
from indicators import calc_all_indicators
from signal_detector import detect_all_signals
from backtest import run_backtest_with_cache
from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    KDJ_GOLDEN_CROSS_WINDOW,
    UPTREND_CONSECUTIVE, VOLUME_MA_PERIOD,
    RANGE_BREAK_TOLERANCE,
    USE_STOP_LOSS, USE_TAKE_PROFIT, TP_LEVEL_MULTIPLIER,
    MAX_HOLD_WEEKS, INITIAL_CAPITAL, MAX_POSITIONS,
    USE_MA_TREND_FILTER, MA_TREND_FAST, MA_TREND_SLOW,
)

CACHE_FILE = "backtest_cache_full.pkl"
TRADE_FILE = "trades_full.csv"

# ============================================================
# 获取全部股票
# ============================================================
print("=" * 60)
print("从本地数据库获取全部 A 股列表...")
all_codes = get_all_stock_codes()
print(f"总计 {len(all_codes)} 只股票")

filtered = [(c, n) for c, n in all_codes
            if "ST" not in n and "*ST" not in n and "退市" not in n]
print(f"过滤 ST/退市后: {len(filtered)} 只")
stock_list = filtered

# ============================================================
# 参数打印
# ============================================================
print()
print("=" * 60)
print("策略参数:")
print(f"  MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) | "
      f"KDJ({KDJ_N},{KDJ_K_SMOOTH},{KDJ_D_SMOOTH}) | "
      f"KDJ窗口={KDJ_GOLDEN_CROSS_WINDOW}")
print(f"  区间触发={UPTREND_CONSECUTIVE}期上升 | 成交量MA={VOLUME_MA_PERIOD} | "
      f"突破容差={RANGE_BREAK_TOLERANCE}")
print(f"  止损={'启用' if USE_STOP_LOSS else '关闭'} | "
      f"止盈={'启用' if USE_TAKE_PROFIT else '关闭'} | "
      f"能级倍率={TP_LEVEL_MULTIPLIER} | 最大持仓={MAX_HOLD_WEEKS}周")
print(f"  初始资金={INITIAL_CAPITAL:,} | 最大仓位={MAX_POSITIONS}")
ma_status = f"启用 (MA{MA_TREND_FAST}>MA{MA_TREND_SLOW})" if USE_MA_TREND_FILTER else "关闭"
print(f"  MA趋势过滤={ma_status}")
print(f"  全量股票: {len(stock_list)} 只")
print("=" * 60)

# ============================================================
# 批量拉取周线数据
# ============================================================
print()
print("第一步：从本地数据库批量读取日线并转换周线...")
t0 = time.time()
codes_only = [c for c, _ in stock_list]
all_weekly = fetch_all_weekly_from_local(codes_only, verbose=True)
elapsed = time.time() - t0
print(f"数据读取耗时: {elapsed:.1f}s")

# ============================================================
# MA 趋势过滤
# ============================================================
if USE_MA_TREND_FILTER:
    print()
    print(f"第二步：周线 MA{MA_TREND_FAST}>MA{MA_TREND_SLOW} 趋势过滤...")
    approved = {}
    bull = bear = no_data = 0
    for code, name in stock_list:
        df = all_weekly.get(code)
        if df is None or len(df) < MA_TREND_SLOW:
            no_data += 1
            continue
        close_w = df["close"].values
        ma_fast = np.mean(close_w[-MA_TREND_FAST:])
        ma_slow = np.mean(close_w[-MA_TREND_SLOW:])
        if ma_fast > ma_slow:
            approved[code] = df
            bull += 1
        else:
            bear += 1
    all_weekly = approved
    print(f"  多头: {bull}, 空头: {bear}, 数据不足: {no_data}")
    print(f"  过滤后: {len(all_weekly)} 只 (淘汰 {bear} 只)")
else:
    print()
    print("第二步：MA 趋势过滤已关闭，使用全部股票")

# ============================================================
# 信号检测
# ============================================================
print()
print(f"第{'三' if USE_MA_TREND_FILTER else '二'}步：计算指标 & 检测信号...")
all_signals = []
valid = skipped = 0
t1 = time.time()

for idx, (code, name) in enumerate(stock_list):
    df = all_weekly.get(code)
    if df is None:
        skipped += 1
        continue
    try:
        df = calc_all_indicators(df)
        all_weekly[code] = df
        sigs = detect_all_signals(df, code, name)
        all_signals.extend(sigs)
        valid += 1
    except Exception:
        skipped += 1
        continue

    if (idx + 1) % 400 == 0:
        pct = (idx + 1) / len(stock_list) * 100
        tag = f"{len(sigs)}个信号" if sigs else "无信号"
        print(f"  [{idx+1:4d}/{len(stock_list)}] {code} {name} — {tag} | {pct:.1f}%")

elapsed2 = time.time() - t1
print(f"\n信号检测耗时: {elapsed2:.1f}s")
print(f"有效股票: {valid}, 跳过: {skipped}, 总信号: {len(all_signals)}")

if all_signals:
    sig_years = Counter(s.date[:4] for s in all_signals)
    print("信号年份分布:")
    for y in sorted(sig_years):
        print(f"  {y}: {sig_years[y]} 个")

# ============================================================
# 保存缓存
# ============================================================
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": all_weekly}, f)
print(f"\n缓存已保存至 {CACHE_FILE}")

# ============================================================
# 回测
# ============================================================
if all_signals:
    print()
    print(f"第{'四' if USE_MA_TREND_FILTER else '三'}步：运行回测...")
    print("-" * 60)
    bt = run_backtest_with_cache(all_signals, all_weekly)
    print(bt.summary)

    if bt.trades:
        trade_df = pd.DataFrame([vars(t) for t in bt.trades])
        trade_df.to_csv(TRADE_FILE, index=False, encoding="utf-8-sig")
        print(f"\n交易明细已保存至 {TRADE_FILE} ({len(bt.trades)} 笔)")

        print()
        print("退出原因分布:")
        for reason, group in trade_df.groupby("exit_reason"):
            wins = (group["pnl"] > 0).sum()
            print(f"  {reason}: {len(group)}笔, "
                  f"平均盈亏={group['pnl_pct'].mean():.2f}%, "
                  f"胜率={wins/len(group)*100:.1f}%")

        trade_df["year"] = pd.to_datetime(trade_df["buy_date"]).dt.year
        print()
        print("按年份分布:")
        for year, group in trade_df.groupby("year"):
            wins = (group["pnl"] > 0).sum()
            print(f"  {year}: {len(group)}笔, 胜率={wins/len(group)*100:.1f}%, "
                  f"总盈亏={group['pnl'].sum():,.0f}")
else:
    print("\n无信号，无法回测。")

print(f"\n总耗时: {time.time()-t0:.1f}s ({(time.time()-t0)/60:.1f}分钟)")
