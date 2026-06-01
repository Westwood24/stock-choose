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
AKshare(腾讯源 日线) → 日转周线 → 指标计算(MACD/KDJ/Force) → 盘整区间检测 → 信号检测 → 扫描/回测
```

### 选股管道（三层）

```
Force连续增量 → 盘整区间形成 → 区间未突破时 MACD二次金叉 + KDJ金叉(4周期内) → 买入信号
```

### 模块职责

- **config.py** — 所有可调参数集中在此，调参无需改其他文件。含 MACD/KDJ/Force/盘整区间/回测/白名单参数
- **data_fetcher.py** — 数据获取层。腾讯源(`stock_zh_a_hist_tx`)，日线通过 `_daily_to_weekly()` resample 为周线。代码前缀映射：60/68开头用 `sh`，其余用 `sz`
- **indicators.py** — MACD（EMA递归）、KDJ（EMA平滑，滚动窗口RSV）、Force 动量指标。`_ema()` 已处理前导 NaN
- **signal_detector.py** — 核心选股逻辑：
  - `detect_all_zones()` — 扫描 Force 指标检测盘整区间（连续 N 期 delta>0 触发，区间上下沿由首/末 Force 正负值确定）
  - `detect_all_signals()` — 综合三层管道检测历史所有信号，同一天去重保留区间范围最大的，止损价取区间后最低价
  - `detect_buy_signal()` — 仅返回当前最新信号
  - `_check_macd_second_cross_at()` — 在指定 bar 检测 MACD 二次金叉（要求两次金叉中间有死叉）或即将二次金叉
- **stock_scanner.py** — 遍历股票列表，支持全市场扫描和白名单过滤，支持历史/当前信号模式
- **backtest.py** — 两种退出模式：止损退出（`_find_stop_loss_exit`）和固定持仓周数。`MAX_POSITIONS` 限制同时持仓数
- **whitelist_manager.py** — 白名单池管理。基于 AKshare 每股经营现金流数据自动筛选，也支持手动维护 CSV
- **verify_signals.py** — 独立验证脚本，使用 `detect_all_signals()` 统一信号源，统计 N 周后涨跌幅分布

### 关键细节

- `_daily_to_weekly()` 使用 pandas `resample("W")`，open取首、high取max、low取min、close取末、amount求和
- 止损价 = `min(low[zone_end+1 : signal_bar+1])`，即区间形成后到信号触发前的最低价（>=range_low），非未来函数
- Zone 检测向前扫描 Force<0 确定区间上沿，但信号仅在 zone 封闭后生成，不构成未来函数
- 回测中每笔交易按 `INITIAL_CAPITAL * 0.8 / MAX_POSITIONS` 分配资金，整手买入，计双边手续费和滑点
- 止损卖出不计滑点（预设价格执行），时间兜底/数据结束卖出计滑点
- 白名单过滤通过 `USE_WHITELIST` 开关控制，默认关闭
- `WEEKLY_MIN_BARS = 120` 确保指标有足够数据预热

### 已验证无未来函数

- MACD EMA：左→右递归
- KDJ RSV：`[i-N+1, i]` 滚动窗口
- Force[t]：仅依赖 Low[t-1], High[t], Close[t], Amount[t]
- 信号检测：仅使用 `dif[:i+1]` / `k[:i+1]` 等截至当前 bar 的数据
- 止损退出：逐 bar 检查 low[i] <= stop_loss
