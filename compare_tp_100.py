"""
扩大样本对比：100只股票，止盈 ON vs OFF。
采样 → 拉数据 → 检测信号 → 双模式回测 → 对比。
"""
import pandas as pd
import numpy as np
import time
import random
import pickle
import threading
import importlib
import config

random.seed(42)
np.random.seed(42)

TIMEOUT = 30
CACHE_FILE = "backtest_cache_100.pkl"

from data_fetcher import fetch_all_stock_codes, fetch_weekly_kline
from indicators import calc_all_indicators
from signal_detector import detect_all_signals


def fetch_with_timeout(code):
    result = [None]
    def _worker():
        try:
            result[0] = fetch_weekly_kline(code)
        except Exception:
            result[0] = None
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=TIMEOUT)
    if t.is_alive():
        return None
    return result[0]


# ── 获取全市场股票并抽样 ──
print("获取A股列表...")
all_df = fetch_all_stock_codes()
mask = ~all_df["name"].str.contains(r"ST|\*ST|退市", na=False, regex=True)
all_df = all_df[mask]

mainboard = all_df[all_df["code"].str.match(r"^(60|00)\d{4}$")]
gem = all_df[all_df["code"].str.match(r"^30\d{4}$")]
star = all_df[all_df["code"].str.match(r"^688\d{4}$")]

sample_parts = []
for board, n in [(mainboard, 60), (gem, 20), (star, 20)]:
    s = board.sample(n=min(n, len(board)), random_state=42)
    sample_parts.append(s)
sample_df = pd.concat(sample_parts, ignore_index=True)
stock_list = list(zip(sample_df["code"], sample_df["name"]))
print(f"抽样 {len(stock_list)} 只 (主板60+创业板20+科创板20)")

# ── 扫描信号 + 缓存周线数据 ──
all_signals = []
weekly_cache = {}
valid = 0
skipped = 0

print("\n开始扫描信号...")
for idx, (code, name) in enumerate(stock_list):
    try:
        df = fetch_with_timeout(code)
    except Exception:
        skipped += 1
        continue

    if df is None or len(df) < 80:
        skipped += 1
        continue

    df = calc_all_indicators(df)
    weekly_cache[code] = df
    sigs = detect_all_signals(df, code, name)
    all_signals.extend(sigs)
    valid += 1
    tag = f"{len(sigs)}个信号" if sigs else "无信号"
    print(f"[{idx+1:3d}/{len(stock_list)}] {code} {name} — {tag}")
    time.sleep(0.05)

print(f"\n有效股票: {valid}, 跳过: {skipped}, 总信号: {len(all_signals)}")

# 保存缓存
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": weekly_cache}, f)
print(f"缓存已保存至 {CACHE_FILE} ({len(weekly_cache)} 只股票)")

if not all_signals:
    print("无信号，退出。")
    exit()

# ── 双模式回测 ──
import backtest
from backtest import run_backtest_with_cache

print("\n" + "=" * 60)
print("  模式1: 无止盈 (仅止损)")
print("=" * 60)
importlib.reload(config)
config.USE_TAKE_PROFIT = False
importlib.reload(backtest)
bt_no = run_backtest_with_cache(all_signals, weekly_cache)
print(bt_no.summary)

print("\n" + "=" * 60)
print("  模式2: 有止盈 (止损 + 能级跟踪止盈)")
print("=" * 60)
importlib.reload(config)
config.USE_TAKE_PROFIT = True
importlib.reload(backtest)
bt_tp = run_backtest_with_cache(all_signals, weekly_cache)
print(bt_tp.summary)

# ── 对比 ──
print("\n" + "=" * 60)
print("  对比汇总 (100只样本)")
print("=" * 60)

def fmt(v):
    return f"{v:+,.2f}"

def arrow(v):
    return "↑" if v > 0 else ("↓" if v < 0 else "─")

# 判断好坏：收益率/夏普/胜率 ↑好, 回撤 ↓好
def judge(key, diff):
    if key in ("max_drawdown",):
        return "[+]" if diff > 0 else ("[-]" if diff < 0 else "[=]")
    else:
        return "[+]" if diff > 0 else ("[-]" if diff < 0 else "[=]")

rows = [
    ("最终资金", bt_no.final_capital, bt_tp.final_capital),
    ("总收益率(%)", bt_no.total_return, bt_tp.total_return),
    ("年化收益(%)", bt_no.annual_return, bt_tp.annual_return),
    ("最大回撤(%)", bt_no.max_drawdown, bt_tp.max_drawdown),
    ("夏普比率", bt_no.sharpe_ratio, bt_tp.sharpe_ratio),
    ("胜率(%)", bt_no.win_rate, bt_tp.win_rate),
    ("交易次数", bt_no.total_trades, bt_tp.total_trades),
]

print(f"{'指标':<18} {'无止盈':>14} {'有止盈':>14} {'差异':>14}  {'评价'}")
print("-" * 75)
for key, v1, v2 in rows:
    diff = v2 - v1
    print(f"{key:<18} {v1:>14,.2f} {v2:>14,.2f} {fmt(diff):>14}  {judge(key, diff)}")

# 退出原因分布
print(f"\n{'退出原因':<18} {'无止盈':>10} {'有止盈':>10}")
print("-" * 40)
all_reasons = set()
for t in bt_no.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for t in bt_tp.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for r in sorted(all_reasons):
    c1 = sum(1 for t in bt_no.trades if (t.exit_reason or "fixed_hold") == r)
    c2 = sum(1 for t in bt_tp.trades if (t.exit_reason or "fixed_hold") == r)
    print(f"{r:<18} {c1:>10} {c2:>10}")

# 恢复默认
importlib.reload(config)
config.USE_TAKE_PROFIT = True
importlib.reload(backtest)
print(f"\n已恢复 USE_TAKE_PROFIT = {config.USE_TAKE_PROFIT}")
