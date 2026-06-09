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
# 绘制单位净值曲线
python plot_equity.py

# 白名单管理
python main.py whitelist-update --delay 0.05
python main.py whitelist-add 000001 --name 平安银行
python main.py whitelist-remove 000001
python main.py whitelist-show
```

## 架构设计

### 选股管道（四层）

```
① MA趋势过滤(周线MA20>MA60) → ② 上升状态触发(连续N期) → ③ 盘整区间形成 → ④ MACD二次金叉 + KDJ金叉(N周期内) → 买入信号
```

**上升状态定义**（三个条件同时满足）：
- `high[i] > high[i-1]` — 最高价高于前一根
- `low[i] > low[i-1]` — 最低价高于前一根
- `amount[i] > MA(volume, N)` — 成交量大于前 N 根均值

**区间定义**：
- 区间下沿 = 第一个上升状态截面的上一截面最低价
- 区间上沿 = 第一个 `high < 前高` 截面的上一截面最高价
- 区间封闭后，所有最高价/最低价未突破区间时寻找 MACD+KDJ 确认

**信号类型**：
- `second_golden_cross` — MACD 已形成二次金叉
- `approaching` — MACD 即将形成二次金叉（DIF 向 DEA 收敛但未交叉）

### 退出管道（六级优先级）

```
开仓 → ① 止损 → ② approaching验证(3周期内MACD必须金叉) → ③ 假突破检测 → ④ 能级突破 → ⑤ 移动止盈 → ⑥ 时间兜底
```

- **R 定义**：R = (区间上限 - 止损价) × `TP_LEVEL_MULTIPLIER`
- **能级体系**：能级 N = 区间上限 + N×R（N = 1, 2, 3, …）
- **止损价**：区间生成后的最低价（≥ 区间下限）
- **approaching 验证**（v2.6）：`approaching` 信号开仓后，3 周期内检测 DIF 是否突破 DEA 形成金叉。若未金叉 → `approaching_failed` 离场（均亏 -6.24%，远优于止损 -10.85%）
- **假突破检测**（v2.2）：收盘价首次突破区间上限后，下一根 K 线回落区间内（≤range_high）→ 离场。突破确认后不再检测
- **优先级**：止损 > approaching验证 > 假突破检测 > 能级突破 > 移动止盈 > 时间兜底

## 关键配置参数

| 参数 | 周线值 | 说明 |
|---|---|---|
| `MACD_FAST / SLOW / SIGNAL` | 12 / 26 / 9 | MACD 标准参数 |
| `KDJ_N / K_SMOOTH / D_SMOOTH` | 9 / 3 / 3 | KDJ 标准参数 |
| `KDJ_GOLDEN_CROSS_WINDOW` | **3** | KDJ 金叉有效窗口（1~5扫描，3最优） |
| `UPTREND_CONSECUTIVE` | 2 | 连续上升状态期数 |
| `VOLUME_MA_PERIOD` | 20 | 成交量均线周期 |
| `TP_LEVEL_MULTIPLIER` | 1.0 | 能级间距倍率（日线用 2.0） |
| `MAX_HOLD_WEEKS` | 52 | 最大持仓兜底 |
| `MAX_POSITIONS` | **11** | 最大同时持仓数（4~20扫描，11最优） |
| `CAPITAL_DEPLOY_RATIO` | **1.0** | 总仓位部署比例（满仓最优） |
| `MAX_SINGLE_STOCK_PCT` | **0.09** | 单票最大仓位占比（9%最优） |
| `USE_MA_TREND_FILTER` | True | 周线 MA 趋势过滤 |
| `MA_TREND_FAST` | 20 | 快线周期 |
| `MA_TREND_SLOW` | 60 | 慢线周期 |

## 回测结果（全量 5273 只 A 股，周频，MA 过滤）

| 指标 | v2.6 最终 🏆 | v2.1 基准 |
|---|---|---|
| **总收益** | **+892.7%** | +328% |
| **年化收益** | **+15.9%** | +9.9% |
| **最大回撤** | **-37.0%** | -39% |
| **夏普比率** | **0.31** | 0.40 |
| **胜率** | **34.2%** | 36.6% |
| **交易数** | **609** | — |

### 退出原因分布（v2.6 最终版）

| 退出类型 | 笔数 | 均盈亏 | 胜率 | 说明 |
|---|---|---|---|---|
| `take_profit` | 115 | +45.4% | 99.1% | 能级移动止盈 |
| `breakout_failure` | 72 | +8.1% | 86.1% | 假突破离场 |
| `max_hold` | 25 | +26.4% | 68.0% | 时间兜底 |
| `end_of_data` | 11 | +27.3% | 72.7% | 数据结束 |
| `approaching_failed` | 55 | -6.2% | 12.7% | 预测金叉未兑现 |
| `stop_loss` | 331 | -10.9% | 0% | 止损 |

### 优化历程

| 版本 | 改动 | 总收益 | 年化 | 回撤 |
|---|---|---|---|---|
| v2.1 | 基准（W=1, POS=5, 80%仓位） | +328% | +9.9% | -39% |
| v2.2 | +假突破离场 | +391% | +10.8% | -37% |
| v2.3 | KDJ窗口 1→3 | +742% | +14.7% | -46% |
| v2.5 | 满仓100%+单票9%+POS=11 | +849% | +15.6% | -44% |
| **v2.6** | **+approaching 3周期MACD验证** | **+893%** | **+15.9%** | **-37%** |

## 模块职责

| 文件 | 职责 |
|---|---|
| `config.py` | 所有可调参数 |
| `data_fetcher.py` | AKshare 在线数据（腾讯源 `stock_zh_a_hist_tx`，前复权，日转周） |
| `local_data_fetcher.py` | 本地 Parquet 数据库批量读取 |
| `indicators.py` | MACD（EMA 递归）、KDJ（SMA 平滑）、ATR（EMA） |
| `signal_detector.py` | 价格行为区间 + MACD二次金叉/即将金叉 + KDJ金叉确认 |
| `stock_scanner.py` | 全市场扫描，支持白名单/历史信号 |
| `backtest.py` | 回测引擎：止损 / approaching验证 / 假突破检测 / 能级止盈 / 时间兜底 |
| `whitelist_manager.py` | 白名单管理 |
| `main.py` | CLI 入口 |
| `plot_equity.py` | 单位净值曲线绘图工具 |

## 目录结构

```
stock choose/
├── config.py                    # 策略参数
├── data_fetcher.py              # AKshare 在线数据
├── local_data_fetcher.py        # 本地 Parquet 数据
├── indicators.py                # MACD / KDJ / ATR 指标
├── signal_detector.py           # 选股信号核心
├── stock_scanner.py             # 全市场扫描
├── backtest.py                  # 回测引擎（含六级退出管道）
├── whitelist_manager.py         # 白名单管理
├── main.py                      # CLI 入口
├── plot_equity.py               # 净值曲线绘图
├── run_backtest_full.py         # 全量周频回测（主要）
├── run_backtest_full_daily.py   # 全量日频回测
├── run_backtest_local.py        # 周频抽样回测
├── run_backtest_local_daily.py  # 日频抽样回测
├── rule.txt                     # 策略规则定义
├── requirements.txt             # Python 依赖
└── CLAUDE.md                    # 本文档
```

## 关键细节

- **KDJ 公式**：标准 SMA（权重 = 1/N），与通达信一致。切勿用 EMA（权重 = 2/(N+1)）
- **信号日期一致性**：`signal_date` 取 `dates[i]`（所有条件满足的当前 bar）
- **止损卖出不计滑点**，止盈/假突破/approaching验证/兜底/数据结束卖出计滑点
- **仓位公式**：`per_trade_capital = min(capital × CAPITAL_DEPLOY_RATIO / MAX_POSITIONS, capital × MAX_SINGLE_STOCK_PCT)`，整手买入
- **MA 趋势过滤在前**：先筛掉周线空头股票，再做信号检测，过滤约 33% 空头/震荡市股票
- **所有指标/区间/信号/止损/止盈检测均仅使用截至当前 bar 的数据**，无未来函数
- **假突破检测**（rule.txt 第21行）：收盘价突破区间上限后，下一根 K 线收盘价回落区间内 → 离场
- **approaching 验证**：仅对 `approaching` 类型信号生效。开仓后 3 周期内 DIF 必须上穿 DEA（金叉），否则 `approaching_failed` 退出。`second_golden_cross` 信号不受此验证影响
