# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基于 **价格行为盘整区间 + MACD 二次金叉 + KDJ 金叉确认 + 周线 MA 趋势过滤** 的 A 股选股与回测系统。

双数据源：AKshare（腾讯源 API）/ 本地 Parquet 数据库（`D:\Trae test\stock data`）。

## 核心命令

```bash
# === 在线模式（AKshare API）===
# 全市场扫描选股 → signals.csv
python main.py scan --delay 0.1

# 单只股票检测
python main.py check 000001

# 扫描 + 回测 → trades.csv
python main.py backtest --delay 0.1

# === 本地模式（Parquet 数据库，推荐）===
# 全量周频回测（默认启用 MA 趋势过滤）→ trades_full.csv
python run_backtest_full.py

# 全量日频回测（M=2, 周线 MA 趋势过滤）→ trades_full_daily.csv
python run_backtest_full_daily.py

# 周频抽样回测（3000只，用于快速验证）→ trades_local.csv
python run_backtest_local.py

# 日频抽样回测（3000只）→ trades_local_daily.csv
python run_backtest_local_daily.py

# === 辅助工具 ===
# 白名单管理
python main.py whitelist-update --delay 0.05
python main.py whitelist-add 000001 --name 平安银行
python main.py whitelist-remove 000001
python main.py whitelist-show
```

## 架构设计

### 选股管道（四层）

```
① MA趋势过滤(周线MA20>MA60) → ② 上升状态触发(连续N期) → ③ 盘整区间形成 → ④ MACD二次金叉 + KDJ金叉(1周期内) → 买入信号
```

**上升状态定义**（三个条件同时满足）：
- `high[i] > high[i-1]` — 最高价高于前一根
- `low[i] > low[i-1]` — 最低价高于前一根
- `amount[i] > MA(volume, N)` — 成交量大于前 N 根均值

**区间定义**：
- 区间下沿 = 第一个上升状态截面的上一截面最低价
- 区间上沿 = 第一个 `high < 前高` 截面的上一截面最高价
- 区间封闭后，所有最高价/最低价未突破区间时寻找 MACD+KDJ 确认

### 退出管道

```
开仓 → 止损检查(low[i] ≤ 止损价) → 能级突破检测(close[i] ≥ range_high + N×R) → 移动止盈(close[i] < 当前能级价) → 时间兜底
```

- **R 定义**：R = (区间上限 - 止损价) × `TP_LEVEL_MULTIPLIER`
- **能级体系**：能级 N = 区间上限 + N×R（N = 1, 2, 3, …）
- **止损价**：区间生成后的最低价（≥ 区间下限）
- **优先级**：止损 > 能级突破检测 > 止盈检查 > 时间兜底

## 关键配置参数

| 参数 | 周线值 | 日线值 | 说明 |
|---|---|---|---|
| `UPTREND_CONSECUTIVE` | 2 | 2 | 连续上升状态期数 |
| `VOLUME_MA_PERIOD` | 20 | 20 | 成交量均线周期 |
| `KDJ_GOLDEN_CROSS_WINDOW` | 1 | 1 | KDJ 金叉有效窗口 |
| `TP_LEVEL_MULTIPLIER` | 1.0 | 2.0 | 能级间距倍率 |
| `MAX_HOLD_WEEKS` / `MAX_HOLD_DAYS` | 52 | 260 | 最大持仓兜底 |
| `USE_MA_TREND_FILTER` | True | True | 周线 MA 趋势过滤 |
| `MA_TREND_FAST` | 20 | 20 | 快线周期 |
| `MA_TREND_SLOW` | 60 | 60 | 慢线周期 |

## 回测结果（全量 5273 只 A 股）

| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 胜率 | 止损率 |
|---|---|---|---|---|---|---|
| **周频 + MA过滤** 🏆 | **+328%** | **+9.9%** | **-39%** | **0.40** | **36.6%** | **59.4%** |
| 周频 无过滤 | +247% | +8.3% | -36% | 0.34 | 33.5% | 59.2% |
| 日频 + MA过滤 (M=2) | +158% | +6.0% | -54% | 0.28 | 29.1% | 70.9% |
| 日频 无过滤 (M=2) | -20% | -1.4% | -73% | 0.02 | 25.9% | 74.1% |

**结论**：周线 MA20>MA60 趋势过滤是唯一通过全量验证的正向优化。过滤掉 33% 空头/震荡市股票后，总收益从 +247% → +328%。

## 模块职责

| 文件 | 职责 |
|---|---|
| `config.py` | 所有可调参数，含 MA 趋势过滤开关 |
| `data_fetcher.py` | AKshare 在线数据（腾讯源 `stock_zh_a_hist_tx`，前复权，日转周） |
| `local_data_fetcher.py` | 本地 Parquet 数据库批量读取 |
| `indicators.py` | MACD（EMA 递归）、KDJ（SMA 平滑）、ATR（EMA） |
| `signal_detector.py` | 价格行为区间 + MACD二次金叉 + KDJ金叉确认 |
| `stock_scanner.py` | 全市场扫描，支持白名单/历史信号 |
| `backtest.py` | 回测引擎：止损/止盈/固定持仓 |
| `whitelist_manager.py` | 白名单管理 |
| `main.py` | CLI 入口 |

## 目录结构

```
stock choose/
├── config.py                    # 策略参数
├── data_fetcher.py              # AKshare 在线数据
├── local_data_fetcher.py        # 本地 Parquet 数据
├── indicators.py                # MACD / KDJ / ATR 指标
├── signal_detector.py           # 选股信号核心
├── stock_scanner.py             # 全市场扫描
├── backtest.py                  # 回测引擎
├── whitelist_manager.py         # 白名单
├── main.py                      # CLI 入口
├── run_backtest_full.py         # 全量周频回测（主要）
├── run_backtest_full_daily.py   # 全量日频回测
├── run_backtest_local.py        # 周频抽样回测
├── run_backtest_local_daily.py  # 日频抽样回测
├── rule.txt                     # 策略规则定义
└── CLAUDE.md                    # 本文档
```

## 关键细节

- **KDJ 公式**：标准 SMA（权重 = 1/N），与通达信一致。切勿用 EMA（权重 = 2/(N+1)）
- **信号日期一致性**：`signal_date` 取 `dates[i]`（所有条件满足的当前 bar）
- **止损卖出不计滑点**，止盈/兜底/数据结束卖出计滑点
- 每笔交易 = `INITIAL_CAPITAL × 0.8 / MAX_POSITIONS`，整手买入
- MA 趋势过滤在前：先筛掉周线空头股票，再做信号检测，减少 35% 噪音信号
- 所有指标/区间/信号/止损/止盈检测均仅使用截至当前 bar 的数据，无未来函数
