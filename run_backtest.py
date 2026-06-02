"""
回测脚本 — 分层抽样50只，用当前代码跑回测并输出对比。
扫描阶段缓存周线数据，回测阶段复用，避免重复API调用。
"""
import pandas as pd
import numpy as np
import time
import random
import pickle
import threading
from data_fetcher import fetch_all_stock_codes, fetch_weekly_kline
from indicators import calc_all_indicators
from signal_detector import detect_all_signals
from backtest import run_backtest_with_cache

random.seed(42)
np.random.seed(42)

TIMEOUT = 30
CACHE_FILE = "backtest_cache.pkl"


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
        print(f"  ⚠ 超时 {TIMEOUT}s，跳过", end="")
        return None
    return result[0]


# 获取全市场股票
print("获取A股列表...")
all_df = fetch_all_stock_codes()
mask = ~all_df["name"].str.contains(r"ST|\*ST|退市", na=False, regex=True)
all_df = all_df[mask]

# 分层抽样
mainboard = all_df[all_df["code"].str.match(r"^(60|00)\d{4}$")]
gem = all_df[all_df["code"].str.match(r"^30\d{4}$")]
star = all_df[all_df["code"].str.match(r"^688\d{4}$")]

sample_parts = []
for board, n in [(mainboard, 30), (gem, 10), (star, 10)]:
    s = board.sample(n=min(n, len(board)), random_state=42)
    sample_parts.append(s)
sample_df = pd.concat(sample_parts, ignore_index=True)
stock_list = list(zip(sample_df["code"], sample_df["name"]))
print(f"抽样 {len(stock_list)} 只 (主板30+创业板10+科创板10)")

# 扫描信号 + 缓存周线数据
all_signals = []
weekly_cache = {}  # code -> DataFrame (已计算指标)
valid = 0
skipped = 0

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

# 检查同日重复
if all_signals:
    from collections import Counter
    date_counts = Counter(f"{s.code}_{s.date}" for s in all_signals)
    dup = sum(1 for v in date_counts.values() if v > 1)
    print(f"同日重复信号: {dup} 组")

# 保存缓存
with open(CACHE_FILE, "wb") as f:
    pickle.dump({"signals": all_signals, "cache": weekly_cache}, f)
print(f"缓存已保存至 {CACHE_FILE} ({len(weekly_cache)} 只股票)")

# 回测（使用缓存数据）
if all_signals:
    print("\n开始回测...")
    bt = run_backtest_with_cache(all_signals, weekly_cache)
    print(bt.summary)

    if bt.trades:
        trade_df = pd.DataFrame([vars(t) for t in bt.trades])
        trade_df.to_csv("trades_new.csv", index=False, encoding="utf-8-sig")
        print("\n交易明细已保存至 trades_new.csv")

        dup_trades = trade_df.groupby(["code", "buy_date"]).size()
        dup_count = (dup_trades > 1).sum()
        print(f"交易同日重复: {dup_count} 组")
