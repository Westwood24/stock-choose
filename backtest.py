"""
回测系统模块 — 对选股信号进行历史回测，输出收益统计。
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    INITIAL_CAPITAL,
    COMMISSION_RATE,
    SLIPPAGE,
    HOLD_WEEKS,
    MAX_POSITIONS,
    CAPITAL_DEPLOY_RATIO,
    MAX_SINGLE_STOCK_PCT,
    USE_STOP_LOSS,
    USE_TAKE_PROFIT,
    TP_LEVEL_MULTIPLIER,
    MAX_HOLD_WEEKS,
)
from data_fetcher import fetch_weekly_kline


@dataclass
class Trade:
    """单笔交易记录。"""
    code: str
    name: str
    buy_date: str
    sell_date: str
    buy_price: float
    sell_price: float
    shares: int
    pnl: float          # 盈亏金额
    pnl_pct: float      # 盈亏百分比
    hold_weeks: int
    exit_reason: str = ""  # "stop_loss" | "breakout_failure" | "max_hold" | "end_of_data" | "fixed_hold" | "take_profit"


@dataclass
class BacktestResult:
    """回测结果。"""
    initial_capital: float
    final_capital: float
    total_return: float           # 总收益率
    annual_return: float          # 年化收益率
    max_drawdown: float           # 最大回撤
    sharpe_ratio: float           # 夏普比率
    win_rate: float               # 胜率
    total_trades: int
    equity_curve: pd.DataFrame    # 权益曲线
    trades: list[Trade] = field(default_factory=list)
    summary: str = ""


def _find_stop_loss_exit(df: pd.DataFrame, buy_idx: int, stop_loss: float,
                          max_hold_weeks: int = MAX_HOLD_WEEKS):
    """从买入位置逐根 K 线检查止损退出。
    Returns:
        (sell_idx, sell_price, exit_reason)
    """
    n = len(df)
    low = df["low"].values
    close = df["close"].values

    for i in range(buy_idx + 1, n):
        # 止损触发：最低价触及止损价
        if low[i] <= stop_loss:
            return (i, stop_loss, "stop_loss")

        # 时间兜底：超过最大持仓周数
        if i - buy_idx >= max_hold_weeks:
            sell_price = float(close[i]) * (1 - SLIPPAGE)
            return (i, sell_price, "max_hold")

    # 数据结束兜底
    last_idx = n - 1
    if last_idx > buy_idx:
        sell_price = float(close[last_idx]) * (1 - SLIPPAGE)
        return (last_idx, sell_price, "end_of_data")
    else:
        # 极端情况：买入后无后续数据
        return (buy_idx, float(close[buy_idx]) * (1 - SLIPPAGE), "end_of_data")


def _find_combined_exit(df: pd.DataFrame, buy_idx: int, stop_loss: float,
                         range_high: float, max_hold_weeks: int = MAX_HOLD_WEEKS):
    """止损 + 假突破检测 + 移动止盈 + 时间兜底的组合退出逻辑。

    退出优先级（从高到低）：
        1. 止损 — 最低价触及止损价
        2. 假突破检测 — 收盘价首次突破区间上限后，下一根回落区间内（≤range_high）离场
        3. 能级突破 — 收盘价逐级突破 range_high + N*R，抬升止盈价
        4. 移动止盈 — 收盘价回落跌破当前能级止盈价
        5. 时间兜底 — 超过最大持仓周期

    止盈逻辑（基于能级突破的移动止盈）：
        - R = (range_high - stop_loss) * TP_LEVEL_MULTIPLIER
        - 能级 N = range_high + N*R  (N = 1, 2, 3, ...)
        - 收盘价突破某能级后，该能级价格成为移动止盈价
        - 若收盘价回落跌破当前止盈价，触发止盈出场
        - 突破更高能级时，止盈价也随之抬升（跟踪止盈）

    Returns:
        (sell_idx, sell_price, exit_reason)
    """
    n = len(df)
    low = df["low"].values
    close = df["close"].values

    R = (range_high - stop_loss) * TP_LEVEL_MULTIPLIER
    if R <= 0:
        # R 非正时退化为纯止损模式
        return _find_stop_loss_exit(df, buy_idx, stop_loss, max_hold_weeks)

    current_tp_stop = None   # 当前移动止盈价（已突破的最高能级价格）
    highest_level = 0        # 已突破的最高能级编号
    broke_above_range = False       # 是否已检测到突破区间上限
    breakout_checked = False        # 是否已完成假突破检测

    for i in range(buy_idx + 1, n):
        # --- 1. 止损检查（优先级最高） ---
        if low[i] <= stop_loss:
            return (i, stop_loss, "stop_loss")

        # --- 2. 假突破检测：收盘价突破区间上限后，下一根回落区间内离场 ---
        if not breakout_checked and not broke_above_range and close[i] > range_high:
            broke_above_range = True
        elif broke_above_range and not breakout_checked:
            # 突破后的第一根 bar — 验证是否为假突破
            if close[i] <= range_high:
                # 假突破：回落区间内，离场
                sell_price = float(close[i]) * (1 - SLIPPAGE)
                return (i, sell_price, "breakout_failure")
            else:
                # 突破确认（连续两根站在区间上方），不再检测假突破
                breakout_checked = True
                broke_above_range = False

        # --- 3. 检查是否突破新的能级 ---
        # 从当前最高能级+1 开始逐级检查
        N = highest_level + 1
        while True:
            level_price = range_high + N * R
            if close[i] >= level_price:
                highest_level = N
                current_tp_stop = level_price
                N += 1
            else:
                break

        # --- 4. 移动止盈检查 ---
        if current_tp_stop is not None and close[i] < current_tp_stop:
            # 收盘价回落跌破当前止盈价，触发止盈
            sell_price = float(close[i]) * (1 - SLIPPAGE)
            return (i, sell_price, "take_profit")

        # --- 5. 时间兜底 ---
        if i - buy_idx >= max_hold_weeks:
            sell_price = float(close[i]) * (1 - SLIPPAGE)
            return (i, sell_price, "max_hold")

    # 数据结束兜底
    last_idx = n - 1
    if last_idx > buy_idx:
        sell_price = float(close[last_idx]) * (1 - SLIPPAGE)
        return (last_idx, sell_price, "end_of_data")
    else:
        return (buy_idx, float(close[buy_idx]) * (1 - SLIPPAGE), "end_of_data")


def run_backtest(
    signals: list,
    hold_weeks: Optional[int] = None,
    max_hold_weeks: Optional[int] = None,
    periods_per_year: int = 52,
) -> BacktestResult:
    """运行回测。
    Args:
        signals: BuySignal 列表
        hold_weeks: 固定持仓周数（USE_STOP_LOSS=False 时使用）
        max_hold_weeks: 最大持仓周数（USE_STOP_LOSS=True 时兜底）
        periods_per_year: 年化周期数（周线=52，日线=252）
    Returns:
        BacktestResult
    """
    if hold_weeks is None:
        hold_weeks = HOLD_WEEKS
    if max_hold_weeks is None:
        max_hold_weeks = MAX_HOLD_WEEKS

    if not signals:
        equity = pd.DataFrame({"date": [], "capital": []})
        return BacktestResult(
            initial_capital=INITIAL_CAPITAL,
            final_capital=INITIAL_CAPITAL,
            total_return=0, annual_return=0,
            max_drawdown=0, sharpe_ratio=0,
            win_rate=0, total_trades=0,
            equity_curve=equity, trades=[],
            summary="无交易信号，回测未执行。"
        )

    capital = float(INITIAL_CAPITAL)
    trades: list[Trade] = []
    equity_rows: list[dict] = []

    # 按信号日期排序
    sorted_signals = sorted(signals, key=lambda s: s.date)

    # 按周分组信号（同一周多个信号按 MAX_POSITIONS 限制）
    # 简化处理：逐个信号买入，持有 HOLD_WEEKS 周后卖出
    positions: list[dict] = []  # {code, buy_price, buy_date, shares, sell_week_idx}

    for sig in sorted_signals:
        # 获取该股票的周线数据以确定买卖价格
        df = fetch_weekly_kline(sig.code)
        if df is None or len(df) < 2:
            continue

        # 找到信号日期对应的行
        df["date"] = pd.to_datetime(df["date"])
        buy_mask = df["date"] == pd.to_datetime(sig.date)
        if not buy_mask.any():
            # 取信号日期之后的第一个交易日
            buy_mask = df["date"] >= pd.to_datetime(sig.date)
            if not buy_mask.any():
                continue

        buy_idx = df[buy_mask].index[0]
        buy_price = float(df.loc[buy_idx, "close"]) * (1 + SLIPPAGE)
        buy_date_str = str(df.loc[buy_idx, "date"].date())

        # 止损价必须低于买入价，否则跳过（已跌破支撑位）
        effective_stop = sig.stop_loss
        if effective_stop >= buy_price:
            continue

        if USE_STOP_LOSS and effective_stop > 0:
            # --- 止损退出模式（可选止盈联动） ---
            if USE_TAKE_PROFIT:
                sell_idx, sell_price, exit_reason = _find_combined_exit(
                    df, buy_idx, effective_stop, sig.range_high, max_hold_weeks
                )
            else:
                sell_idx, sell_price, exit_reason = _find_stop_loss_exit(
                    df, buy_idx, effective_stop, max_hold_weeks
                )
        else:
            # --- 固定持仓周数模式 ---
            sell_idx = min(buy_idx + hold_weeks, len(df) - 1)
            sell_price = float(df.loc[sell_idx, "close"]) * (1 - SLIPPAGE)
            exit_reason = "fixed_hold"

        sell_date = str(df.loc[sell_idx, "date"].date())

        # 清理已结束的仓位（卖出日期早于当前信号日期的视为已平仓）
        positions = [p for p in positions if p["sell_date"] >= buy_date_str]
        if len(positions) >= MAX_POSITIONS:
            continue

        # 简化：每笔交易使用总资金的一定比例
        per_trade_capital = min(capital * CAPITAL_DEPLOY_RATIO / MAX_POSITIONS, capital * MAX_SINGLE_STOCK_PCT)
        shares = int(per_trade_capital / buy_price / 100) * 100  # 整手

        if shares <= 0:
            continue

        # 计算费用
        cost = shares * buy_price
        commission_buy = cost * COMMISSION_RATE
        revenue = shares * sell_price
        commission_sell = revenue * COMMISSION_RATE

        pnl = revenue - cost - commission_buy - commission_sell
        pnl_pct = pnl / cost * 100 if cost > 0 else 0

        capital += pnl

        trade = Trade(
            code=sig.code,
            name=sig.name,
            buy_date=buy_date_str,
            sell_date=sell_date,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            hold_weeks=sell_idx - buy_idx,
            exit_reason=exit_reason,
        )
        trades.append(trade)
        positions.append({"code": sig.code, "sell_date": sell_date})

        # 记录权益（简化：每笔交易记录一个点）
        equity_rows.append({
            "date": pd.to_datetime(sig.date),
            "capital": capital,
        })

    # --- 计算统计指标 ---
    total_trades = len(trades)
    if total_trades == 0:
        equity = pd.DataFrame({"date": [], "capital": []})
        return BacktestResult(
            initial_capital=INITIAL_CAPITAL,
            final_capital=INITIAL_CAPITAL,
            total_return=0, annual_return=0,
            max_drawdown=0, sharpe_ratio=0,
            win_rate=0, total_trades=0,
            equity_curve=equity, trades=[],
            summary="无交易信号，回测未执行。"
        )

    final_capital = capital
    total_return = (final_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / total_trades * 100

    # 年化收益率（假设周线，52周/年）
    if equity_rows:
        first_date = equity_rows[0]["date"]
        last_date = equity_rows[-1]["date"]
        years = max((last_date - first_date).days / 365, 0.01)
        annual_return = ((final_capital / INITIAL_CAPITAL) ** (1 / years) - 1) * 100
    else:
        annual_return = 0

    # 最大回撤
    equity_df = pd.DataFrame(equity_rows)
    equity_df = equity_df.sort_values("date").reset_index(drop=True)
    equity_df["peak"] = equity_df["capital"].cummax()
    equity_df["drawdown"] = (equity_df["capital"] - equity_df["peak"]) / equity_df["peak"] * 100
    max_drawdown = float(equity_df["drawdown"].min())

    # 夏普比率（简化：基于每笔交易盈亏）
    if total_trades >= 2:
        returns = np.array([t.pnl_pct for t in trades]) / 100
        sharpe_ratio = float(np.mean(returns) / (np.std(returns, ddof=1) + 1e-10) * np.sqrt(periods_per_year / hold_weeks))
    else:
        sharpe_ratio = 0

    # 退出原因统计
    exit_counts = {}
    for t in trades:
        reason = t.exit_reason or "fixed_hold"
        exit_counts[reason] = exit_counts.get(reason, 0) + 1
    exit_summary = " | ".join(f"{k}:{v}" for k, v in sorted(exit_counts.items()))

    # 生成摘要
    summary = (
        f"======== 回测摘要 ========\n"
        f"初始资金: {INITIAL_CAPITAL:,.0f}\n"
        f"最终资金: {final_capital:,.2f}\n"
        f"总收益率: {total_return:.2f}%\n"
        f"年化收益: {annual_return:.2f}%\n"
        f"最大回撤: {max_drawdown:.2f}%\n"
        f"夏普比率: {sharpe_ratio:.2f}\n"
        f"交易次数: {total_trades}\n"
        f"胜率: {win_rate:.2f}%\n"
        f"退出分布: {exit_summary}\n"
        f"========================="
    )

    return BacktestResult(
        initial_capital=INITIAL_CAPITAL,
        final_capital=final_capital,
        total_return=round(total_return, 2),
        annual_return=round(annual_return, 2),
        max_drawdown=round(max_drawdown, 2),
        sharpe_ratio=round(sharpe_ratio, 2),
        win_rate=round(win_rate, 2),
        total_trades=total_trades,
        equity_curve=equity_df,
        trades=trades,
        summary=summary,
    )


def run_backtest_with_cache(
    signals: list,
    weekly_cache: dict,
    hold_weeks: Optional[int] = None,
    max_hold_weeks: Optional[int] = None,
    periods_per_year: int = 52,
) -> BacktestResult:
    """使用缓存的周线数据运行回测，避免重复 API 调用。
    Args:
        periods_per_year: 年化周期数（周线=52，日线=252）
    """
    if hold_weeks is None:
        hold_weeks = HOLD_WEEKS
    if max_hold_weeks is None:
        max_hold_weeks = MAX_HOLD_WEEKS

    if not signals:
        equity = pd.DataFrame({"date": [], "capital": []})
        return BacktestResult(
            initial_capital=INITIAL_CAPITAL,
            final_capital=INITIAL_CAPITAL,
            total_return=0, annual_return=0,
            max_drawdown=0, sharpe_ratio=0,
            win_rate=0, total_trades=0,
            equity_curve=equity, trades=[],
            summary="无交易信号，回测未执行。"
        )

    capital = float(INITIAL_CAPITAL)
    trades: list[Trade] = []
    equity_rows: list[dict] = []

    sorted_signals = sorted(signals, key=lambda s: s.date)
    positions: list[dict] = []

    for sig in sorted_signals:
        df = weekly_cache.get(sig.code)
        if df is None or len(df) < 2:
            continue

        df["date"] = pd.to_datetime(df["date"])
        buy_mask = df["date"] == pd.to_datetime(sig.date)
        if not buy_mask.any():
            buy_mask = df["date"] >= pd.to_datetime(sig.date)
            if not buy_mask.any():
                continue

        buy_idx = df[buy_mask].index[0]
        buy_price = float(df.loc[buy_idx, "close"]) * (1 + SLIPPAGE)
        buy_date_str = str(df.loc[buy_idx, "date"].date())

        # 止损价必须低于买入价，否则跳过（已跌破支撑位）
        effective_stop = sig.stop_loss
        if effective_stop >= buy_price:
            continue

        if USE_STOP_LOSS and effective_stop > 0:
            # --- 止损退出模式（可选止盈联动） ---
            if USE_TAKE_PROFIT:
                sell_idx, sell_price, exit_reason = _find_combined_exit(
                    df, buy_idx, effective_stop, sig.range_high, max_hold_weeks
                )
            else:
                sell_idx, sell_price, exit_reason = _find_stop_loss_exit(
                    df, buy_idx, effective_stop, max_hold_weeks
                )
        else:
            # --- 固定持仓周数模式 ---
            sell_idx = min(buy_idx + hold_weeks, len(df) - 1)
            sell_price = float(df.loc[sell_idx, "close"]) * (1 - SLIPPAGE)
            exit_reason = "fixed_hold"

        sell_date = str(df.loc[sell_idx, "date"].date())

        # 清理已结束的仓位
        positions = [p for p in positions if p["sell_date"] >= buy_date_str]
        if len(positions) >= MAX_POSITIONS:
            continue

        per_trade_capital = min(capital * CAPITAL_DEPLOY_RATIO / MAX_POSITIONS, capital * MAX_SINGLE_STOCK_PCT)
        shares = int(per_trade_capital / buy_price / 100) * 100

        if shares <= 0:
            continue

        cost = shares * buy_price
        commission_buy = cost * COMMISSION_RATE
        revenue = shares * sell_price
        commission_sell = revenue * COMMISSION_RATE

        pnl = revenue - cost - commission_buy - commission_sell
        pnl_pct = pnl / cost * 100 if cost > 0 else 0

        capital += pnl

        trade = Trade(
            code=sig.code,
            name=sig.name,
            buy_date=buy_date_str,
            sell_date=sell_date,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            hold_weeks=sell_idx - buy_idx,
            exit_reason=exit_reason,
        )
        trades.append(trade)
        positions.append({"code": sig.code, "sell_date": sell_date})

        equity_rows.append({
            "date": pd.to_datetime(sig.date),
            "capital": capital,
        })

    total_trades = len(trades)
    if total_trades == 0:
        equity = pd.DataFrame({"date": [], "capital": []})
        return BacktestResult(
            initial_capital=INITIAL_CAPITAL,
            final_capital=INITIAL_CAPITAL,
            total_return=0, annual_return=0,
            max_drawdown=0, sharpe_ratio=0,
            win_rate=0, total_trades=0,
            equity_curve=equity, trades=[],
            summary="无交易信号，回测未执行。"
        )

    final_capital = capital
    total_return = (final_capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    wins = sum(1 for t in trades if t.pnl > 0)
    win_rate = wins / total_trades * 100

    if equity_rows:
        first_date = equity_rows[0]["date"]
        last_date = equity_rows[-1]["date"]
        years = max((last_date - first_date).days / 365, 0.01)
        annual_return = ((final_capital / INITIAL_CAPITAL) ** (1 / years) - 1) * 100
    else:
        annual_return = 0

    equity_df = pd.DataFrame(equity_rows)
    equity_df = equity_df.sort_values("date").reset_index(drop=True)
    equity_df["peak"] = equity_df["capital"].cummax()
    equity_df["drawdown"] = (equity_df["capital"] - equity_df["peak"]) / equity_df["peak"] * 100
    max_drawdown = float(equity_df["drawdown"].min())

    if total_trades >= 2:
        returns = np.array([t.pnl_pct for t in trades]) / 100
        sharpe_ratio = float(np.mean(returns) / (np.std(returns, ddof=1) + 1e-10) * np.sqrt(periods_per_year / hold_weeks))
    else:
        sharpe_ratio = 0

    exit_counts = {}
    for t in trades:
        reason = t.exit_reason or "fixed_hold"
        exit_counts[reason] = exit_counts.get(reason, 0) + 1
    exit_summary = " | ".join(f"{k}:{v}" for k, v in sorted(exit_counts.items()))

    summary = (
        f"======== 回测摘要 ========\n"
        f"初始资金: {INITIAL_CAPITAL:,.0f}\n"
        f"最终资金: {final_capital:,.2f}\n"
        f"总收益率: {total_return:.2f}%\n"
        f"年化收益: {annual_return:.2f}%\n"
        f"最大回撤: {max_drawdown:.2f}%\n"
        f"夏普比率: {sharpe_ratio:.2f}\n"
        f"交易次数: {total_trades}\n"
        f"胜率: {win_rate:.2f}%\n"
        f"退出分布: {exit_summary}\n"
        f"========================="
    )

    return BacktestResult(
        initial_capital=INITIAL_CAPITAL,
        final_capital=final_capital,
        total_return=round(total_return, 2),
        annual_return=round(annual_return, 2),
        max_drawdown=round(max_drawdown, 2),
        sharpe_ratio=round(sharpe_ratio, 2),
        win_rate=round(win_rate, 2),
        total_trades=total_trades,
        equity_curve=equity_df,
        trades=trades,
        summary=summary,
    )
