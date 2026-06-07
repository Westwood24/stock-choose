r"""
全量日频回测脚本 — 使用本地 Parquet 数据库，全部 A 股（过滤 ST/退市）。
日线不转换周线，M=2（TP_LEVEL_MULTIPLIER=2.0）。
"""
import sys
import time
import random
import pickle
from collections import Counter

import pandas as pd
import numpy as np

from local_data_fetcher import (
    get_all_stock_codes,
    fetch_all_daily_from_local,
)
from indicators import calc_all_indicators
from signal_detector import detect_all_signals
from backtest import run_backtest_with_cache
from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    KDJ_GOLDEN_CROSS_WINDOW,
    UPTREND_CONSECUTIVE, VOLUME_MA_PERIOD,
    RANGE_BREAK_TOLERANCE,
    INITIAL_CAPITAL, MAX_POSITIONS, COMMISSION_RATE, SLIPPAGE,
    USE_MA_TREND_FILTER, MA_TREND_FAST, MA_TREND_SLOW,
)

random.seed(42)
np.random.seed(42)

# ── 日频参数 ──
TP_LEVEL_MULTIPLIER = 2.0    # M=2，日线使用更大倍率减少震出
MAX_HOLD_DAYS = 260           # 最大持仓天数（≈52周）
TRADING_DAYS_PER_YEAR = 252   # 年化交易日数

# 覆盖止盈倍率为 M=2
import config
import backtest
config.TP_LEVEL_MULTIPLIER = TP_LEVEL_MULTIPLIER
config.USE_STOP_LOSS = True
config.USE_TAKE_PROFIT = True

CACHE_FILE = "backtest_cache_full_daily.pkl"
TRADE_FILE = "trades_full_daily.csv"

# ============================================================
# 获取全部股票列表
# ============================================================
print("=" * 60)
print("从本地数据库获取全部 A 股列表...")
all_codes = get_all_stock_codes()
print(f"总计 {len(all_codes)} 只股票")

filtered = [
    (c, n) for c, n in all_codes
    if "ST" not in n and "*ST" not in n and "退市" not in n
]
print(f"过滤 ST/退市后: {len(filtered)} 只")

stock_list = filtered

mainboard = [(c, n) for c, n in stock_list if c.startswith(("60", "00"))]
gem = [(c, n) for c, n in stock_list if c.startswith("30")]
star = [(c, n) for c, n in stock_list if c.startswith("688")]
print(f"  主板: {len(mainboard)}, 创业板: {len(gem)}, 科创板: {len(star)}")

# ============================================================
# 参数打印
# ============================================================
print()
print("=" * 60)
print("策略参数（全量日频回测，M=2）:")
print(f"  MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) | "
      f"KDJ({KDJ_N},{KDJ_K_SMOOTH},{KDJ_D_SMOOTH}) | "
      f"KDJ窗口={KDJ_GOLDEN_CROSS_WINDOW}")
print(f"  区间触发连续={UPTREND_CONSECUTIVE}期上升状态 | "
      f"成交量MA={VOLUME_MA_PERIOD} | "
      f"突破容差={RANGE_BREAK_TOLERANCE}")
print(f"  止损=启用 | 止盈=启用 | 能级倍率 M={TP_LEVEL_MULTIPLIER} | "
      f"最大持仓={MAX_HOLD_DAYS}天兜底")
ma_status = f"启用 (MA{MA_TREND_FAST}>MA{MA_TREND_SLOW})" if USE_MA_TREND_FILTER else "关闭"
print(f"  初始资金={INITIAL_CAPITAL:,} | 最大仓位={MAX_POSITIONS}")
print(f"  MA趋势过滤={ma_status}")
print(f"  全量股票: {len(stock_list)} 只 | 年化交易日={TRADING_DAYS_PER_YEAR}")
print("=" * 60)

# ============================================================
# 批量拉取日线数据
# ============================================================
print()
print("第一步：从本地数据库批量读取日线数据（不转换周线）...")
t0 = time.time()

codes_only = [c for c, _ in stock_list]
daily_cache = fetch_all_daily_from_local(codes_only, verbose=True)

elapsed = time.time() - t0
print(f"数据读取耗时: {elapsed:.1f}s")

# ============================================================
# MA 趋势过滤
# ============================================================
if USE_MA_TREND_FILTER:
    print()
    print(f"第二步：周线 MA{MA_TREND_FAST}>MA{MA_TREND_SLOW} 趋势过滤...")
    from local_data_fetcher import fetch_all_weekly_from_local
    weekly_data = fetch_all_weekly_from_local(codes_only, verbose=False)
    approved = {}
    bull = bear = no_data = 0
    for code, _ in stock_list:
        wdf = weekly_data.get(code)
        if wdf is None or len(wdf) < MA_TREND_SLOW:
            no_data += 1
            continue
        ma_fast = np.mean(wdf["close"].values[-MA_TREND_FAST:])
        ma_slow = np.mean(wdf["close"].values[-MA_TREND_SLOW:])
        if ma_fast > ma_slow:
            approved[code] = daily_cache.get(code)
            bull += 1
        else:
            bear += 1
    daily_cache = {k: v for k, v in approved.items() if v is not None}
    print(f"  多头: {bull}, 空头: {bear}, 数据不足: {no_data}")
    print(f"  过滤后: {len(daily_cache)} 只 (淘汰 {bear} 只)")
    step_label = "三"
else:
    step_label = "二"

# ============================================================
# 信号检测
# ============================================================
print()
print(f"第{step_label}步：计算指标 & 检测信号（日线）...")
all_signals = []
valid = 0
skipped = 0

t1 = time.time()
for idx, (code, name) in enumerate(stock_list):
    df = daily_cache.get(code)
    if df is None:
        skipped += 1
        continue

    try:
        df = calc_all_indicators(df)
        daily_cache[code] = df
        sigs = detect_all_signals(df, code, name)
        all_signals.extend(sigs)
        valid += 1
    except Exception as e:
        skipped += 1
        if idx < 10:
            print(f"  [!] {code} {name}: {e}")
        continue

    tag = f"{len(sigs)}个信号" if sigs else "无信号"
    if (idx + 1) % 200 == 0:
        pct = (idx + 1) / len(stock_list) * 100
        elapsed_i = time.time() - t1
        eta = elapsed_i / (idx + 1) * (len(stock_list) - idx - 1)
        print(f"  [{idx+1:4d}/{len(stock_list)}] {code} {name} — {tag} | "
              f"{pct:.1f}% | 已用{elapsed_i:.0f}s | 预计剩余{eta:.0f}s")

elapsed2 = time.time() - t1
print(f"\n信号检测耗时: {elapsed2:.1f}s")
print(f"有效股票: {valid}, 跳过: {skipped}, 总信号: {len(all_signals)}")

if all_signals:
    date_counts = Counter(f"{s.code}_{s.date}" for s in all_signals)
    dup = sum(1 for v in date_counts.values() if v > 1)
    print(f"同日重复信号: {dup} 组")

    sig_years = Counter(s.date[:4] for s in all_signals)
    print("信号年份分布:")
    for y in sorted(sig_years):
        print(f"  {y}: {sig_years[y]} 个")

# ============================================================
# 保存缓存
# ============================================================
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": daily_cache}, f)
print(f"\n缓存已保存至 {CACHE_FILE} ({len(daily_cache)} 只股票)")

# ============================================================
# 回测
# ============================================================
if all_signals:
    print()
    print("第三步：运行全量日频回测 (M=2)...")
    print("-" * 60)

    bt = run_backtest_with_cache(
        all_signals, daily_cache,
        max_hold_weeks=MAX_HOLD_DAYS,
        periods_per_year=TRADING_DAYS_PER_YEAR,
    )
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
            avg_return = group["pnl_pct"].mean()
            print(f"  {year}: {len(group)}笔, 胜率={wins/len(group)*100:.1f}%, "
                  f"总盈亏={group['pnl'].sum():,.0f}, 平均盈亏={avg_return:.2f}%")
else:
    print("\n无信号，无法回测。")

total_elapsed = time.time() - t0
print(f"\n全量日频回测总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}分钟)")

# 恢复默认
config.TP_LEVEL_MULTIPLIER = 1.0
print(f"已恢复 TP_LEVEL_MULTIPLIER = {config.TP_LEVEL_MULTIPLIER}")
