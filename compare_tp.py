"""
对比止盈 ON/OFF 两种模式回测效果。
复用已有 backtest_cache.pkl，仅切换 USE_TAKE_PROFIT 跑两次回测。
"""
import pickle
import sys
import importlib
import config
from backtest import run_backtest_with_cache

CACHE_FILE = "backtest_cache.pkl"

# 加载缓存
print("加载缓存...")
with open(CACHE_FILE, "rb") as f:
    data = pickle.load(f)
signals = data["signals"]
weekly_cache = data["cache"]
print(f"信号总数: {len(signals)}, 缓存股票数: {len(weekly_cache)}")

# ---- 模式1: 无止盈 (仅止损) ----
print("\n" + "=" * 60)
print("  模式1: 无止盈 (仅止损退出)")
print("=" * 60)
importlib.reload(config)
config.USE_TAKE_PROFIT = False
import backtest
importlib.reload(backtest)
bt1 = run_backtest_with_cache(signals, weekly_cache)
print(bt1.summary)

# ---- 模式2: 有止盈 (止损 + 移动止盈) ----
print("\n" + "=" * 60)
print("  模式2: 有止盈 (止损 + 能级跟踪止盈)")
print("=" * 60)
importlib.reload(config)
config.USE_TAKE_PROFIT = True
importlib.reload(backtest)
bt2 = run_backtest_with_cache(signals, weekly_cache)
print(bt2.summary)

# ---- 对比 ----
print("\n" + "=" * 60)
print("  对比汇总")
print("=" * 60)

def fmt(v):
    return f"{v:+,.2f}"

print(f"{'指标':<20} {'无止盈':>15} {'有止盈':>15} {'差异':>15}")
print("-" * 65)
print(f"{'最终资金':<20} {bt1.final_capital:>15,.2f} {bt2.final_capital:>15,.2f} {fmt(bt2.final_capital - bt1.final_capital):>15}")
print(f"{'总收益率(%)':<20} {bt1.total_return:>15.2f} {bt2.total_return:>15.2f} {fmt(bt2.total_return - bt1.total_return):>15}")
print(f"{'年化收益(%)':<20} {bt1.annual_return:>15.2f} {bt2.annual_return:>15.2f} {fmt(bt2.annual_return - bt1.annual_return):>15}")
print(f"{'最大回撤(%)':<20} {bt1.max_drawdown:>15.2f} {bt2.max_drawdown:>15.2f} {fmt(bt2.max_drawdown - bt1.max_drawdown):>15}")
print(f"{'夏普比率':<20} {bt1.sharpe_ratio:>15.2f} {bt2.sharpe_ratio:>15.2f} {fmt(bt2.sharpe_ratio - bt1.sharpe_ratio):>15}")
print(f"{'胜率(%)':<20} {bt1.win_rate:>15.2f} {bt2.win_rate:>15.2f} {fmt(bt2.win_rate - bt1.win_rate):>15}")
print(f"{'交易次数':<20} {bt1.total_trades:>15} {bt2.total_trades:>15} {bt2.total_trades - bt1.total_trades:>15}")

# 退出原因对比
print(f"\n{'退出原因':<20} {'无止盈':>15} {'有止盈':>15}")
print("-" * 50)
all_reasons = set()
for t in bt1.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for t in bt2.trades:
    all_reasons.add(t.exit_reason or "fixed_hold")
for r in sorted(all_reasons):
    c1 = sum(1 for t in bt1.trades if (t.exit_reason or "fixed_hold") == r)
    c2 = sum(1 for t in bt2.trades if (t.exit_reason or "fixed_hold") == r)
    print(f"{r:<20} {c1:>15} {c2:>15}")
