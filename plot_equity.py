"""
绘制单位净值图 — 从 trades_full.csv 构建权益曲线
"""
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# 设置中文字体
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False

# 读取交易数据
df = pd.read_csv("trades_full.csv")
df["buy_date"] = pd.to_datetime(df["buy_date"])
df["sell_date"] = pd.to_datetime(df["sell_date"])
df = df.sort_values("buy_date")

# 构建权益曲线
INITIAL = 100_000
capital = INITIAL
equity = [{"date": df["buy_date"].iloc[0] - pd.Timedelta(days=7), "capital": INITIAL}]

for _, row in df.iterrows():
    capital += row["pnl"]
    equity.append({"date": row["sell_date"], "capital": capital})

equity_df = pd.DataFrame(equity)
equity_df["date"] = pd.to_datetime(equity_df["date"])
equity_df = equity_df.sort_values("date").reset_index(drop=True)
equity_df["nav"] = equity_df["capital"] / INITIAL

# 计算回撤
equity_df["peak"] = equity_df["capital"].cummax()
equity_df["drawdown"] = (equity_df["capital"] - equity_df["peak"]) / equity_df["peak"] * 100

# ---- 绘图 ----
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)

# 上：净值曲线
ax1.fill_between(equity_df["date"], 1.0, equity_df["nav"],
                 where=equity_df["nav"] >= 1.0, alpha=0.15, color="#228B22")
ax1.fill_between(equity_df["date"], 1.0, equity_df["nav"],
                 where=equity_df["nav"] < 1.0, alpha=0.15, color="#DC143C")
ax1.plot(equity_df["date"], equity_df["nav"], color="#1a1a2e", linewidth=1.2)
ax1.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.6)
ax1.set_ylabel("单位净值", fontsize=13)
ax1.set_title("A股选股策略 — 单位净值曲线", fontsize=16, fontweight="bold")
ax1.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
ax1.grid(True, alpha=0.3)

# 在图上标注关键数据
final_nav = equity_df["nav"].iloc[-1]
max_nav = equity_df["nav"].max()
max_nav_date = equity_df.loc[equity_df["nav"].idxmax(), "date"]
ax1.annotate(f"最终净值 {final_nav:.2f}\n(+{(final_nav-1)*100:.0f}%)",
             xy=(equity_df["date"].iloc[-1], final_nav),
             xytext=(30, 20), textcoords="offset points",
             fontsize=11, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.4", facecolor="#e8f5e9", edgecolor="#2e7d32", alpha=0.9),
             arrowprops=dict(arrowstyle="->", color="#2e7d32", lw=1.5))

# 下：回撤曲线
ax2.fill_between(equity_df["date"], 0, equity_df["drawdown"], alpha=0.4, color="#DC143C")
ax2.plot(equity_df["date"], equity_df["drawdown"], color="#8B0000", linewidth=0.8)
ax2.set_ylabel("回撤 %", fontsize=12)
ax2.set_xlabel("日期", fontsize=13)
ax2.set_ylim(equity_df["drawdown"].min() * 1.15, 5)
ax2.grid(True, alpha=0.3)
ax2.axhline(y=0, color="gray", linewidth=0.5)

# 标注最大回撤
min_dd = equity_df["drawdown"].min()
min_dd_date = equity_df.loc[equity_df["drawdown"].idxmin(), "date"]
ax2.annotate(f"最大回撤 {min_dd:.1f}%",
             xy=(min_dd_date, min_dd),
             xytext=(0, -25), textcoords="offset points",
             fontsize=10, color="#8B0000", fontweight="bold",
             ha="center",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#ffebee", edgecolor="#c62828", alpha=0.9))

plt.tight_layout()
plt.savefig("equity_curve.png", dpi=150, bbox_inches="tight")
print(f"图表已保存: equity_curve.png")
print(f"初始净值: 1.00")
print(f"最终净值: {final_nav:.2f} (总收益 +{(final_nav-1)*100:.1f}%)")
print(f"最高净值: {max_nav:.2f} (日期: {max_nav_date.strftime('%Y-%m-%d')})")
print(f"最大回撤: {min_dd:.1f}% (日期: {min_dd_date.strftime('%Y-%m-%d')})")

# 补充统计
trades = df
wins = (trades["pnl"] > 0).sum()
print(f"\n交易统计: {len(trades)}笔 | 胜率 {wins/len(trades)*100:.1f}%")
print(f"平均持仓: {trades['hold_weeks'].mean():.1f}周")
print(f"单笔最大盈利: +{trades['pnl'].max():,.0f} | 单笔最大亏损: {trades['pnl'].min():,.0f}")
