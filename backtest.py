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


def run_backtest(
    signals: list,
    hold_weeks: Optional[int] = None,
) -> BacktestResult:
    """运行回测。
    Args:
        signals: BuySignal 列表
        hold_weeks: 持仓周数，None 则使用 config 配置
    Returns:
        BacktestResult
    """
    if hold_weeks is None:
        hold_weeks = HOLD_WEEKS

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

        # 卖出日期（HOLD_WEEKS 周后）
        sell_idx = min(buy_idx + hold_weeks, len(df) - 1)
        sell_price = float(df.loc[sell_idx, "close"]) * (1 - SLIPPAGE)
        sell_date = str(df.loc[sell_idx, "date"].date())

        # 计算可买股数（考虑最大持仓限制）
        if len(positions) >= MAX_POSITIONS:
            continue

        position_capital = capital / (MAX_POSITIONS - len(positions)) if MAX_POSITIONS - len(positions) > 0 else capital
        # 简化：每笔交易使用总资金的一定比例
        per_trade_capital = capital * 0.8 / MAX_POSITIONS
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
            buy_date=sig.date,
            sell_date=sell_date,
            buy_price=buy_price,
            sell_price=sell_price,
            shares=shares,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            hold_weeks=sell_idx - buy_idx,
        )
        trades.append(trade)

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
        sharpe_ratio = float(np.mean(returns) / (np.std(returns, ddof=1) + 1e-10) * np.sqrt(52 / hold_weeks))
    else:
        sharpe_ratio = 0

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
