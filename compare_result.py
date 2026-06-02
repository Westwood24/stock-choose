"""
三组对比：固定持仓20周 vs 仅止损 vs 止损+止盈。
"""
import pickle
import importlib
import config

CACHE_FILE = "backtest_cache_100.pkl"

print("加载缓存...")
with open(CACHE_FILE, "rb") as f:
    data = pickle.load(f)
signals = data["signals"]
weekly_cache = data["cache"]
print(f"信号总数: {len(signals)}, 缓存股票数: {len(weekly_cache)}")

import backtest
from backtest import run_backtest_with_cache


def run_group(label, stop_loss, take_profit, hold_weeks=None):
    print("\n" + "=" * 60)
    print(f"  {label}")
    print("=" * 60)
    importlib.reload(config)
    config.USE_STOP_LOSS = stop_loss
    config.USE_TAKE_PROFIT = take_profit
    importlib.reload(backtest)
    bt = run_backtest_with_cache(signals, weekly_cache, hold_weeks=hold_weeks)
    print(bt.summary)
    return bt


# ── 三组回测 ──
bt_a = run_group("A组: 固定持仓20周 (无止损无止盈)", stop_loss=False, take_profit=False, hold_weeks=20)
bt_b = run_group("B组: 仅止损 (无止盈)", stop_loss=True, take_profit=False)
bt_c = run_group("C组: 止损 + 跟踪止盈", stop_loss=True, take_profit=True)

# ── 三组对比 ──
print("\n" + "=" * 60)
print("  三组对比汇总 (100只样本)")
print("=" * 60)

def fmt(v):
    return f"{v:+,.2f}"

rows = [
    ("最终资金", bt_a.final_capital, bt_b.final_capital, bt_c.final_capital),
    ("总收益率(%)", bt_a.total_return, bt_b.total_return, bt_c.total_return),
    ("年化收益(%)", bt_a.annual_return, bt_b.annual_return, bt_c.annual_return),
    ("最大回撤(%)", bt_a.max_drawdown, bt_b.max_drawdown, bt_c.max_drawdown),
    ("夏普比率", bt_a.sharpe_ratio, bt_b.sharpe_ratio, bt_c.sharpe_ratio),
    ("胜率(%)", bt_a.win_rate, bt_b.win_rate, bt_c.win_rate),
    ("交易次数", bt_a.total_trades, bt_b.total_trades, bt_c.total_trades),
]

print(f"{'指标':<18} {'A-固定20周':>14} {'B-仅止损':>14} {'C-止损+止盈':>14}")
print("-" * 64)
for key, v1, v2, v3 in rows:
    print(f"{key:<18} {v1:>14,.2f} {v2:>14,.2f} {v3:>14,.2f}")

# 退出原因分布
print(f"\n{'退出原因':<18} {'A-固定20周':>10} {'B-仅止损':>10} {'C-止损+止盈':>10}")
print("-" * 52)
all_reasons = set()
for t in bt_a.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for t in bt_b.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for t in bt_c.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for r in sorted(all_reasons):
    c1 = sum(1 for t in bt_a.trades if (t.exit_reason or "fixed_hold") == r)
    c2 = sum(1 for t in bt_b.trades if (t.exit_reason or "fixed_hold") == r)
    c3 = sum(1 for t in bt_c.trades if (t.exit_reason or "fixed_hold") == r)
    print(f"{r:<18} {c1:>10} {c2:>10} {c3:>10}")

# 恢复默认
importlib.reload(config)
config.USE_TAKE_PROFIT = True
importlib.reload(backtest)
print(f"\n已恢复 USE_TAKE_PROFIT = {config.USE_TAKE_PROFIT}")
