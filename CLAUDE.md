# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基于 **价格行为盘整区间 + MACD 二次金叉 + KDJ 金叉确认** 的 A 股选股与回测系统。

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

# 分层抽样回测（50只，缓存周线）→ trades_new.csv + backtest_cache.pkl
python run_backtest.py

# === 本地模式（Parquet 数据库，推荐）===
# 周频全量回测（5273只）→ trades_full.csv + backtest_cache_full.pkl
python run_backtest_full.py

# 日频全量回测（M=2）→ trades_full_daily.csv
python run_backtest_full_daily.py

# 日频优化版（U=3, V=50, M=1）→ trades_daily_opt.csv
python run_backtest_daily_opt.py

# 分层抽样回测（3000只，本地）→ trades_local.csv
python run_backtest_local.py

# 日频抽样回测（3000只，本地）→ trades_local_daily.csv
python run_backtest_local_daily.py

# === 辅助工具 ===
# 白名单管理
python main.py whitelist-update --delay 0.05
python main.py whitelist-add 000001 --name 平安银行
python main.py whitelist-remove 000001
python main.py whitelist-show

# 随机抽取股票验证信号后20周收益
python verify_signals.py

# 日频对比（多组参数并行）
python compare_daily.py
python compare_daily_1000.py       # 1000只样本
python compare_tp.py               # 止盈倍率对比
python compare_result.py           # 结果汇总对比
```

## 架构设计

### 数据流

```
# 周线模式（默认）
源数据(日线) → 日转周线 → 指标计算(MACD/KDJ) → 盘整区间检测(价格行为) → 信号检测 → 扫描/回测

# 日线模式（跳过 resample）
源数据(日线) → 指标计算(MACD/KDJ) → 盘整区间检测(价格行为) → 信号检测 → 扫描/回测
```

### 选股管道（三层）

```
① 上升状态触发(连续N期) → ② 盘整区间形成 → ③ 区间内 MACD二次金叉 + KDJ金叉(1周期内) → 买入信号
```

**上升状态定义**（三个条件同时满足）：
- `high[i] > high[i-1]` — 最高价高于前一根
- `low[i] > low[i-1]` — 最低价高于前一根
- `amount[i] > MA(volume, N)` — 成交量大于前 N 根均值

**区间定义**：
- 区间下沿 = 第一个上升状态截面的上一截面最低价
- 区间上沿 = 第一个 `high < 前高` 截面的上一截面最高价
- 区间封闭后，所有最高价/最低价未突破区间时寻找 MACD+KDJ 确认

### 退出管道（止损 > 止盈 > 时间兜底）

```
开仓 → 止损检查(low[i] ≤ 止损价) → 能级突破检测(close[i] ≥ range_high + N×R) → 移动止盈(close[i] < 当前能级价) → 时间兜底
```

- **R 定义**：R = (区间上限 - 止损价) × `TP_LEVEL_MULTIPLIER`
- **能级体系**：能级 N = 区间上限 + N×R（N = 1, 2, 3, …）
- **跟踪止盈**：每突破一个能级，止盈价上移至该能级；回落跌破当前止盈价即出场
- **止损价**：区间生成后的最低价（≥ 区间下限），跌破即离场

### 模块职责

| 文件 | 职责 |
|---|---|
| `config.py` | 所有可调参数，调参无需改其他文件 |
| `data_fetcher.py` | AKshare 在线数据（腾讯源 `stock_zh_a_hist_tx`，`adjust="qfq"` 前复权，日转周 resample） |
| `local_data_fetcher.py` | 本地 Parquet 数据库（`D:\Trae test\stock data\data\daily_qfq`），批量读取/日转周/日线直出 |
| `indicators.py` | MACD（EMA 递归）、KDJ（标准 SMA weight=1/N，与通达信一致） |
| `signal_detector.py` | 核心选股：`detect_all_zones()` 价格行为区间 + `detect_all_signals()` MACD+KDJ 综合判定 |
| `stock_scanner.py` | 遍历股票列表，支持全市场/白名单/历史信号模式 |
| `backtest.py` | 回测引擎：止损退出 / 止损+移动止盈 / 固定持仓。`run_backtest_with_cache()` 复用缓存 |
| `whitelist_manager.py` | 白名单管理，基于每股经营现金流自动筛选 |
| `verify_signals.py` | 独立验证：统计信号后 N 周涨跌幅分布 |
| `compare_daily*.py` | 日频多组参数对比回测 |
| `compare_tp*.py` | 止盈倍率对比回测 |

### 关键配置参数

| 参数 | 周线值 | 日线值 | 说明 |
|---|---|---|---|
| `UPTREND_CONSECUTIVE` | 2 | 2 | 连续上升状态期数（触发区间） |
| `VOLUME_MA_PERIOD` | 20 | 20 | 成交量均线周期 |
| `KDJ_GOLDEN_CROSS_WINDOW` | 1 | 1 | KDJ 金叉有效窗口，越宽松信号越劣质 |
| `TP_LEVEL_MULTIPLIER` | 1.0 | 2.0 | 能级间距倍率，日线需放大防震出 |
| `MAX_HOLD_WEEKS` / `MAX_HOLD_DAYS` | 52 | 260 | 最大持仓兜底 |
| `RANGE_BREAK_TOLERANCE` | 0.0 | 0.0 | 区间突破容差（严格不突破） |

### 关键细节

- `_daily_to_weekly()`：pandas `resample("W")`，标签从周日修正为周五（`- pd.Timedelta(days=2)`）
- **止损卖出不计滑点**（预设价格执行），止盈/兜底/数据结束卖出计滑点
- 每笔交易 = `INITIAL_CAPITAL × 0.8 / MAX_POSITIONS`，整手买入，双边手续费
- `run_backtest_*.py` 先批量缓存数据为 `.pkl`，回测阶段复用，避免重复读取
- 白名单通过 `USE_WHITELIST` 开关控制，默认关闭
- **KDJ 公式**：标准 SMA（权重 = 1/N），与通达信一致。切勿用 EMA（权重 = 2/(N+1)），会过度敏感
- **信号日期一致性**：`signal_date` 取 `dates[i]`（所有条件满足的当前 bar），非 MACD 金叉日
- **无未来函数**：所有指标/区间/信号/止损/止盈检测均仅使用截至当前 bar 的数据

## 回测结果（全量 5273 只 A 股）

| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 胜率 | 止损率 |
|---|---|---|---|---|---|---|
| **周频 MACD+KDJ** ✅ | **+247%** | **+8.3%** | -36% | 0.34 | 33.5% | 59% |
| 日频 MACD+KDJ (M=2) | -20% | -1.4% | -73% | 0.02 | 25.9% | 74% |
| 日频 MACD+KDJ 优化 | +18% | +1.0% | -52% | 0.12 | 27.4% | 72% |
| 周频 纯突破 ❌ | -48% | -4.1% | -81% | -0.04 | 44.4% | 47% |

**结论**：周频 + MACD+KDJ 确认是最优策略。去掉 MACD/KDJ 改为"收盘价突破区间上限买入"后效果急剧恶化（-48%），证明 MACD+KDJ 双确认对过滤虚假突破至关重要。

## 目录结构

```
stock choose/
├── config.py              # 参数配置
├── data_fetcher.py        # AKshare 在线数据
├── local_data_fetcher.py  # 本地 Parquet 数据
├── indicators.py          # MACD / KDJ 指标
├── signal_detector.py     # 选股信号（区间 + MACD + KDJ）
├── stock_scanner.py       # 全市场扫描
├── backtest.py            # 回测引擎
├── whitelist_manager.py   # 白名单
├── main.py                # CLI 入口
├── run_backtest.py        # 在线抽样回测
├── run_backtest_full.py   # 全量周频回测（本地）
├── run_backtest_full_daily.py  # 全量日频回测（本地）
├── run_backtest_local.py  # 抽样周频回测（本地）
├── run_backtest_local_daily.py # 抽样日频回测（本地）
├── run_backtest_daily_opt.py   # 日频优化回测
├── run_backtest_breakout.py    # 纯突破策略实验（已弃用）
├── verify_signals.py      # 信号验证
├── compare_daily.py       # 日频多组对比
├── compare_tp.py          # 止盈倍率对比
├── rule.txt               # 策略规则定义
└── CLAUDE.md              # 本文档
```
