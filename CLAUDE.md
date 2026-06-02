# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

基于 **Force 动量指标 + 盘整区间 + MACD 周线二次金叉 + KDJ 金叉确认** 的 A 股选股与回测系统。使用 AKshare 获取数据（腾讯源），Pandas/Numpy 计算指标。

## 核心命令

```bash
# 全市场扫描选股 → 输出 signals.csv
python main.py scan --delay 0.1

# 单只股票快速检测
python main.py check 000001

# 扫描 + 回测 → 输出 trades.csv（默认止损退出模式）
python main.py backtest --delay 0.1

# 分层抽样回测（缓存周线数据，避免重复 API 调用）→ trades_new.csv + backtest_cache.pkl
python run_backtest.py

# 日频回测对比（100只样本，三组并行）→ backtest_cache_daily.pkl
python compare_daily.py

# 止盈能级倍率对比（日线专用，测试不同 M 值）
# 修改 config.py 中 TP_LEVEL_MULTIPLIER 后运行 compare_daily.py

# 白名单管理
python main.py whitelist-update --delay 0.05   # 自动更新白名单（筛选经营现金流>0）
python main.py whitelist-add 000001 --name 平安银行  # 手动添加
python main.py whitelist-remove 000001          # 移除
python main.py whitelist-show                   # 查看概况

# 随机抽取股票验证信号后20周收益
python verify_signals.py
```

## 架构设计

### 数据流

```
# 周线模式（默认）
AKshare(腾讯源 日线) → 日转周线 → 指标计算(MACD/KDJ/Force) → 盘整区间检测 → 信号检测 → 扫描/回测

# 日线模式（fetch_daily_kline 跳过 resample）
AKshare(腾讯源 日线) → 指标计算(MACD/KDJ/Force) → 盘整区间检测 → 信号检测 → 扫描/回测
```

### 选股管道（三层）

```
Force连续增量 → 盘整区间形成 → 区间未突破时 MACD二次金叉 + KDJ金叉(1周期内，最严格) → 买入信号
```

### 退出管道（止损 + 移动止盈 + 时间兜底）

```
开仓 → 止损检查(low[i]≤区间下沿) → 能级突破检测(close[i]≥range_high+N*R) → 移动止盈(close[i]<当前能级价) → 时间兜底(MAX_HOLD_WEEKS)
```

- **R 定义**：R = (区间上限 - 区间下沿) × TP_LEVEL_MULTIPLIER
- **能级体系**：能级 N = 区间上限 + N×R（N=1,2,3,…）
- **跟踪止盈**：收盘价每突破一个能级，止盈价上移至该能级；回落跌破当前止盈价即出场
- **优先级**：止损 > 能级突破检测 > 止盈检查 > 时间兜底
- **TP_LEVEL_MULTIPLIER**：能级间距倍率，周线推荐 1.0，日线推荐 2.0（大于 1 放大间距减少震出）

### 模块职责

- **config.py** — 所有可调参数集中在此，调参无需改其他文件。含 MACD/KDJ/Force/盘整区间/回测/白名单参数
- **data_fetcher.py** — 数据获取层。腾讯源(`stock_zh_a_hist_tx`)，使用 `adjust="qfq"` 前复权数据。`fetch_weekly_kline()` 日线→周线 resample；`fetch_daily_kline()` 直接返回日线（跳过 resample）。代码前缀映射：60/68开头用 `sh`，其余用 `sz`
- **indicators.py** — MACD（EMA递归）、KDJ（标准 SMA 平滑 weight=1/N，滚动窗口RSV，与通达信一致）、Force 动量指标。`_ema()` 已处理前导 NaN
- **signal_detector.py** — 核心选股逻辑：
  - `detect_all_zones()` — 扫描 Force 指标检测盘整区间（连续 N 期 delta>0 触发，区间上下沿由首/末 Force 正负值确定）
  - `detect_all_signals()` — 综合三层管道检测历史所有信号。信号日期取所有条件（MACD+KDJ）同时满足的 bar（`dates[i]`），确保信号日 KDJ 状态与实际一致。同一天去重保留区间范围最大的，止损价取区间后最低价
  - `detect_buy_signal()` — 仅返回当前最新信号
  - `_check_macd_second_cross_at()` — 在指定 bar 检测 MACD 二次金叉（要求两次金叉中间有死叉）或即将二次金叉
- **stock_scanner.py** — 遍历股票列表，支持全市场扫描和白名单过滤，支持历史/当前信号模式
- **backtest.py** — 退出模式：止损退出（`_find_stop_loss_exit`）/ 止损+移动止盈（`_find_combined_exit`）/ 固定持仓周数。`USE_TAKE_PROFIT` 控制是否启用能级跟踪止盈。止盈卖出计滑点。`MAX_POSITIONS` 限制同时持仓数。`run_backtest_with_cache()` 复用预加载的周线缓存
- **whitelist_manager.py** — 白名单池管理。基于 AKshare 每股经营现金流数据自动筛选，也支持手动维护 CSV
- **verify_signals.py** — 独立验证脚本，使用 `detect_all_signals()` 统一信号源，统计 N 周后涨跌幅分布

### 关键细节

- `_daily_to_weekly()` 使用 pandas `resample("W")`，open取首、high取max、low取min、close取末、amount求和。resample 默认标签为周日，已通过 `- pd.Timedelta(days=2)` 修正为周五
- 止损价 = `range_low`（区间下沿），跌破区间即离场
- Zone 检测向前扫描 Force<0 确定区间上沿，但信号仅在 zone 封闭后生成，不构成未来函数
- 回测中每笔交易按 `INITIAL_CAPITAL * 0.8 / MAX_POSITIONS` 分配资金，整手买入，计双边手续费和滑点
- 止损卖出不计滑点（预设价格执行），止盈/时间兜底/数据结束卖出计滑点
- 止盈卖出价 = 触发当周收盘价 × (1 - slippage)，止损卖出价 = 预设止损价格（不计滑点）
- `run_backtest.py` 先批量拉取周线数据并缓存为 `backtest_cache.pkl`，回测阶段复用缓存，避免对同一股票重复 API 调用
- 白名单过滤通过 `USE_WHITELIST` 开关控制，默认关闭
- `WEEKLY_MIN_BARS = 120` 确保指标有足够数据预热
- **KDJ 公式**：采用标准 SMA 平滑（权重 = 1/N，与通达信/同花顺一致），K = (1/N)×RSV + ((N-1)/N)×K_prev，D 同理。切勿使用 EMA 平滑（权重 = 2/(N+1)），会导致 KDJ 过度敏感产生虚假金叉信号
- **信号日期一致性**：`signal_date` 统一取 `dates[i]`（所有条件满足的当前 bar），而非 MACD 金叉发生日 `dates[cross_idx]`，避免信号日 KDJ 实际处于死叉的不一致情况

### 频率选择与参数建议

| 参数 | 周线（推荐） | 日线 | 说明 |
|---|---|---|---|
| `KDJ_GOLDEN_CROSS_WINDOW` | 1 | **1** | 实测最优，越宽松信号质量越差 |
| `TP_LEVEL_MULTIPLIER` | 1.0 | **2.0** | 日线 R 天然小，需放大间距防震出 |
| `MIN_BARS` | 120 | 500 | 约 2 年数据 |
| 固定持有 | 12 | 60 | HOLD_WEEKS / HOLD_DAYS |
| 最大持有 | 52 | 260 | MAX_HOLD_WEEKS / MAX_HOLD_DAYS |
| 夏普年化 | 52 | 252 | periods_per_year |

- **周线**（40只样本，KDJ修复后）：C 组（止损+止盈 M=1）总收益 135%、年化 5.69%、胜率 39.7%，止盈锁利占 27.6%，全面优于纯止损
- **日线**（778只样本，2023年起，KDJ修复后）：WINDOW=1 时夏普 1.67、回撤 -5.71%、胜率 41.0% 为最优；C 组（M=2）年化 54.9%、总收益 184%；WINDOW 越大信号越劣质

### 已验证无未来函数

- MACD EMA：左→右递归
- KDJ RSV：`[i-N+1, i]` 滚动窗口
- Force[t]：仅依赖 Low[t-1], High[t], Close[t], Amount[t]
- 信号检测：仅使用 `dif[:i+1]` / `k[:i+1]` 等截至当前 bar 的数据
- 止损退出：逐 bar 检查 low[i] <= stop_loss
- 止盈退出：逐 bar 检查 close[i] >= level_N 突破能级、close[i] < current_tp_stop 触发止盈，仅使用截至当前 bar 的数据
