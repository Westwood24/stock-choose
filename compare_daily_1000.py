"""
1000只股票日线回测 (2023年起) — 三组对比。
"""
import pandas as pd
import numpy as np
import time
import random
import pickle
import threading
import importlib

random.seed(42)
np.random.seed(42)

TIMEOUT = 30
CACHE_FILE = "backtest_cache_daily_1000.pkl"
SINCE_DATE = "20230101"       # 信号自此日期起算
FETCH_START = "20200101"      # 数据起点（给指标预热）

import config
from data_fetcher import fetch_all_stock_codes, fetch_daily_kline
from indicators import calc_all_indicators
from signal_detector import detect_all_signals


def fetch_with_timeout(code):
    result = [None]
    def _worker():
        try:
            result[0] = fetch_daily_kline(code, start_date=FETCH_START)
        except Exception:
            result[0] = None
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=TIMEOUT)
    if t.is_alive():
        return None
    return result[0]


# ── 抽样 1000 只 ──
print("获取A股列表...")
all_df = fetch_all_stock_codes()
mask = ~all_df["name"].str.contains(r"ST|\*ST|退市", na=False, regex=True)
all_df = all_df[mask]

mainboard = all_df[all_df["code"].str.match(r"^(60|00)\d{4}$")]
gem = all_df[all_df["code"].str.match(r"^30\d{4}$")]
star = all_df[all_df["code"].str.match(r"^688\d{4}$")]

sample_parts = []
for board, n in [(mainboard, 600), (gem, 200), (star, 200)]:
    s = board.sample(n=min(n, len(board)), random_state=42)
    sample_parts.append(s)
sample_df = pd.concat(sample_parts, ignore_index=True)
stock_list = list(zip(sample_df["code"], sample_df["name"]))
print(f"抽样 {len(stock_list)} 只 (主板600+创业板200+科创板200)")

# ── 扫描 ──
all_signals_raw = []
daily_cache = {}
valid = 0
skipped = 0

print("\n开始扫描日线信号...")
t0 = time.time()
for idx, (code, name) in enumerate(stock_list):
    try:
        df = fetch_with_timeout(code)
    except Exception:
        skipped += 1
        continue

    if df is None or len(df) < 300:  # 放宽min_bars，2020至今约1300根日线
        skipped += 1
        continue

    df = calc_all_indicators(df)
    daily_cache[code] = df
    sigs = detect_all_signals(df, code, name)
    all_signals_raw.extend(sigs)
    valid += 1

    elapsed = time.time() - t0
    eta = elapsed / (idx + 1) * (len(stock_list) - idx - 1) if idx > 0 else 0
    tag = f"{len(sigs)}个信号" if sigs else "无"
    print(f"[{idx+1:4d}/{len(stock_list)}] {code} {name} — {tag} | ETA {eta:.0f}s")
    time.sleep(0.03)

print(f"\n有效股票: {valid}, 跳过: {skipped}, 原始信号: {len(all_signals_raw)}")

# ── 过滤 2023 年后的信号 ──
all_signals = [s for s in all_signals_raw if s.date >= SINCE_DATE]
print(f"2023年起信号: {len(all_signals)} (过滤掉 {len(all_signals_raw) - len(all_signals)})")

# 保存缓存
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": daily_cache}, f)
print(f"缓存已保存至 {CACHE_FILE} ({len(daily_cache)} 只)")

if not all_signals:
    print("无信号，退出。")
    exit()

# ── 三组回测 ──
import backtest
from backtest import run_backtest_with_cache
from config import HOLD_DAYS, MAX_HOLD_DAYS, TRADING_DAYS_PER_YEAR

def run_group(label, stop_loss, take_profit, tp_mult, hold_bars=None, max_hold_bars=None):
    print("\n" + "=" * 60)
    print(f"  {label} (M={tp_mult})")
    print("=" * 60)
    importlib.reload(config)
    config.USE_STOP_LOSS = stop_loss
    config.USE_TAKE_PROFIT = take_profit
    config.TP_LEVEL_MULTIPLIER = float(tp_mult)
    importlib.reload(backtest)
    bt = run_backtest_with_cache(
        all_signals, daily_cache,
        hold_weeks=hold_bars,
        max_hold_weeks=max_hold_bars,
        periods_per_year=TRADING_DAYS_PER_YEAR,
    )
    print(bt.summary)
    return bt

bt_a = run_group("A组: 固定持仓60天", False, False, 0, hold_bars=HOLD_DAYS)
bt_b = run_group("B组: 仅止损", True, False, 0, max_hold_bars=MAX_HOLD_DAYS)
bt_c = run_group("C组: 止损+止盈", True, True, 2.0, max_hold_bars=MAX_HOLD_DAYS)

# ── 对比 ──
print("\n" + "=" * 60)
print("  1000只日线回测 (2023年起)")
print("=" * 60)

rows = [
    ("最终资金", bt_a.final_capital, bt_b.final_capital, bt_c.final_capital),
    ("总收益率(%)", bt_a.total_return, bt_b.total_return, bt_c.total_return),
    ("年化收益(%)", bt_a.annual_return, bt_b.annual_return, bt_c.annual_return),
    ("最大回撤(%)", bt_a.max_drawdown, bt_b.max_drawdown, bt_c.max_drawdown),
    ("夏普比率", bt_a.sharpe_ratio, bt_b.sharpe_ratio, bt_c.sharpe_ratio),
    ("胜率(%)", bt_a.win_rate, bt_b.win_rate, bt_c.win_rate),
    ("交易次数", bt_a.total_trades, bt_b.total_trades, bt_c.total_trades),
]

print(f"{'指标':<20} {'A-固定60天':>14} {'B-仅止损':>14} {'C-止损+止盈(M=2)':>18}")
print("-" * 70)
for key, v1, v2, v3 in rows:
    print(f"{key:<20} {v1:>14,.2f} {v2:>14,.2f} {v3:>18,.2f}")

print(f"\n{'退出原因':<20} {'A-固定60天':>10} {'B-仅止损':>10} {'C-止损+止盈':>12}")
print("-" * 56)
all_reasons = set()
for t in bt_a.trades: all_reasons.add(t.exit_reason or 'fixed_hold')
for t in bt_b.trades: all_reasons.add(t.exit_reason or 'fixed_hold')
for t in bt_c.trades: all_reasons.add(t.exit_reason or 'fixed_hold')
for r in sorted(all_reasons):
    c1 = sum(1 for t in bt_a.trades if (t.exit_reason or 'fixed_hold') == r)
    c2 = sum(1 for t in bt_b.trades if (t.exit_reason or 'fixed_hold') == r)
    c3 = sum(1 for t in bt_c.trades if (t.exit_reason or 'fixed_hold') == r)
    print(f"{r:<20} {c1:>10} {c2:>10} {c3:>12}")

# 恢复
importlib.reload(config)
config.USE_TAKE_PROFIT = True
config.TP_LEVEL_MULTIPLIER = 2.0
importlib.reload(backtest)
print("\n已恢复: USE_TAKE_PROFIT=True, TP_LEVEL_MULTIPLIER=2.0")
