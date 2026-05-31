# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基于周线 MACD 二次金叉 + KDJ 金叉确认的 A 股选股与回测系统。使用 AKshare 获取数据（腾讯源），Pandas/Numpy 计算指标。

## 核心命令

```bash
# 全市场扫描选股 → 输出 signals.csv
python main.py scan --delay 0.1

# 单只股票快速检测
python main.py check 000001

# 扫描 + 回测 → 输出 trades.csv
python main.py backtest --hold-weeks 12

# 随机抽取 N 只股票验证信号有效性（20周后涨跌幅）
python verify_signals.py
```

## 架构设计

### 数据流

```
AKshare(腾讯源 日线) → 日转周线 → 指标计算(MACD/KDJ) → 信号检测 → 扫描/回测
```

### 模块职责

- **config.py** — 所有可调参数集中在此，调参无需改其他文件。`MACD_FAST/SLOW/SIGNAL`、`KDJ_N`、信号窗口、回测资金/仓位参数
- **data_fetcher.py** — 数据获取层。东方财富源(`stock_zh_a_hist`)不稳定，已改用腾讯源(`stock_zh_a_hist_tx`)，日线通过 `_daily_to_weekly()` resample 为周线。代码前缀映射：60/68开头用 `sh` 前缀，其余用 `sz`
- **indicators.py** — MACD 用 EMA 递归计算，KDJ 用 EMA 平滑。`_ema()` 已处理前导 NaN（跳过 NaN 段找首个有效窗口做初始 SMA）
- **signal_detector.py** — 核心选股逻辑。检测 MACD 二次金叉（两次 DIF 上穿 DEA 且中间有死叉）或即将二次金叉（DIF 在 DEA 下方但差距缩小且趋势向上），同时要求 KDJ 在 `KDJ_GOLDEN_CROSS_WINDOW` 个周期内出现金叉
- **stock_scanner.py** — 遍历股票列表，逐个获取数据、计算指标、检测信号
- **backtest.py** — 按信号日期开仓，持有 `HOLD_WEEKS` 周后平仓。计算总收益、年化收益、最大回撤、夏普比率、胜率
- **verify_signals.py** — 独立的验证脚本，扫描历史所有信号并统计 N 周后涨跌幅分布

### 关键细节

- `_daily_to_weekly()` 使用 pandas `resample("W")`，open取首、high取max、low取min、close取末、amount求和
- 信号检测中的 MACD 二次金叉在 `find_historical_signals`（verify 脚本）中使用全量金叉/死叉/KDJ金叉集合做 O(n) 检测，比逐窗口调用 `detect_buy_signal` 快很多
- 回测中每笔交易按 `INITIAL_CAPITAL * 0.8 / MAX_POSITIONS` 分配资金，整手买入，计双边手续费和滑点
