r"""
日频回测脚本 — 使用本地 Parquet 数据库，日线不转换周线。
M=2（TP_LEVEL_MULTIPLIER=2.0），3000 只样本。
"""
import pandas as pd
import numpy as np
import time
import random
import pickle
from collections import Counter

random.seed(42)
np.random.seed(42)

CACHE_FILE = "backtest_cache_local_daily_3000.pkl"

from local_data_fetcher import (
    get_all_stock_codes,
    fetch_all_daily_from_local,
)
from indicators import calc_all_indicators
from signal_detector import detect_all_signals
from backtest import run_backtest_with_cache

# ── 日频参数 ──
TP_LEVEL_MULTIPLIER = 2.0    # M=2，日线使用更大倍率减少震出
MAX_HOLD_DAYS = 260           # 最大持仓天数（≈52周）
TRADING_DAYS_PER_YEAR = 252   # 年化交易日数
DAILY_MIN_BARS = 500          # 日线最少数据量

from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    KDJ_GOLDEN_CROSS_WINDOW,
    UPTREND_CONSECUTIVE, VOLUME_MA_PERIOD,
    RANGE_BREAK_TOLERANCE,
    INITIAL_CAPITAL, MAX_POSITIONS, COMMISSION_RATE, SLIPPAGE,
)

# 覆盖止盈倍率为 M=2
import config
import backtest
config.TP_LEVEL_MULTIPLIER = TP_LEVEL_MULTIPLIER
# 强制重载 backtest 以使用新 TP_LEVEL_MULTIPLIER
import importlib
importlib.reload(backtest)

# ============================================================
# 获取股票列表
# ============================================================
print("=" * 60)
print("从本地数据库获取 A 股列表...")
all_codes = get_all_stock_codes()
print(f"总计 {len(all_codes)} 只股票")

filtered = [
    (c, n) for c, n in all_codes
    if "ST" not in n and "*ST" not in n and "退市" not in n
]
print(f"过滤 ST/退市后: {len(filtered)} 只")

mainboard = [(c, n) for c, n in filtered if c.startswith(("60", "00"))]
gem = [(c, n) for c, n in filtered if c.startswith("30")]
star = [(c, n) for c, n in filtered if c.startswith("688")]

print(f"  主板: {len(mainboard)}, 创业板: {len(gem)}, 科创板: {len(star)}")

TOTAL_SAMPLE = 3000
total_available = len(mainboard) + len(gem) + len(star)
n_main = int(TOTAL_SAMPLE * len(mainboard) / total_available)
n_gem = int(TOTAL_SAMPLE * len(gem) / total_available)
n_star = TOTAL_SAMPLE - n_main - n_gem

sample_parts = []
for board, n_target, label in [(mainboard, n_main, "主板"), (gem, n_gem, "创业板"), (star, n_star, "科创板")]:
    n = min(n_target, len(board))
    s = random.sample(board, n)
    sample_parts.append(s)
    print(f"  抽取{label}: {n} 只")

stock_list = [item for part in sample_parts for item in part]
print(f"总抽样: {len(stock_list)} 只")

# ============================================================
# 参数打印
# ============================================================
print()
print("=" * 60)
print("策略参数（日频，M=2）:")
print(f"  MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) | "
      f"KDJ({KDJ_N},{KDJ_K_SMOOTH},{KDJ_D_SMOOTH}) | "
      f"KDJ窗口={KDJ_GOLDEN_CROSS_WINDOW}")
print(f"  区间触发连续={UPTREND_CONSECUTIVE}期上升状态 | "
      f"成交量MA={VOLUME_MA_PERIOD} | "
      f"突破容差={RANGE_BREAK_TOLERANCE}")
print(f"  止损=启用 | 止盈=启用 | 能级倍率 M={TP_LEVEL_MULTIPLIER} | "
      f"最大持仓={MAX_HOLD_DAYS}天兜底")
print(f"  初始资金={INITIAL_CAPITAL:,} | 最大仓位={MAX_POSITIONS}")
print(f"  日线最少数据={DAILY_MIN_BARS} | 年化交易日={TRADING_DAYS_PER_YEAR}")
print("=" * 60)

# ============================================================
# 批量拉取日线数据
# ============================================================
print()
print("第一步：从本地数据库批量读取日线数据...")
t0 = time.time()

codes_only = [c for c, _ in stock_list]
daily_cache = fetch_all_daily_from_local(codes_only, verbose=True)

elapsed = time.time() - t0
print(f"数据读取耗时: {elapsed:.1f}s")

# ============================================================
# 信号检测
# ============================================================
print()
print("第二步：计算指标 & 检测信号（日线）...")
all_signals = []
valid = 0
skipped = 0

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
    if (idx + 1) % 100 == 0 or idx == 0:
        print(f"  [{idx+1:4d}/{len(stock_list)}] {code} {name} — {tag}")

print(f"\n有效股票: {valid}, 跳过: {skipped}, 总信号: {len(all_signals)}")

if all_signals:
    date_counts = Counter(f"{s.code}_{s.date}" for s in all_signals)
    dup = sum(1 for v in date_counts.values() if v > 1)
    print(f"同日重复信号: {dup} 组")

# ============================================================
# 保存缓存
# ============================================================
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": daily_cache}, f)
print(f"缓存已保存至 {CACHE_FILE} ({len(daily_cache)} 只股票)")

# ============================================================
# 回测
# ============================================================
if all_signals:
    print()
    print("第三步：运行日频回测 (M=2)...")
    print("-" * 60)

    bt = run_backtest_with_cache(
        all_signals, daily_cache,
        max_hold_weeks=MAX_HOLD_DAYS,
        periods_per_year=TRADING_DAYS_PER_YEAR,
    )
    print(bt.summary)

    if bt.trades:
        trade_df = pd.DataFrame([vars(t) for t in bt.trades])
        trade_df.to_csv("trades_local_daily.csv", index=False, encoding="utf-8-sig")
        print(f"\n交易明细已保存至 trades_local_daily.csv ({len(bt.trades)} 笔)")

        print()
        print("退出原因分布:")
        for reason, group in trade_df.groupby("exit_reason"):
            print(f"  {reason}: {len(group)}笔, "
                  f"平均盈亏={group['pnl_pct'].mean():.2f}%, "
                  f"胜率={(group['pnl'] > 0).sum() / len(group) * 100:.1f}%")

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

# 恢复默认
config.TP_LEVEL_MULTIPLIER = 1.0
importlib.reload(backtest)
print(f"\n已恢复 TP_LEVEL_MULTIPLIER = {config.TP_LEVEL_MULTIPLIER}")
