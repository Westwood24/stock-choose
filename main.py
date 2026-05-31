"""
主入口 — 选股 + 回测。
"""

import argparse

from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    KDJ_GOLDEN_CROSS_WINDOW, HOLD_WEEKS, MAX_POSITIONS,
    INITIAL_CAPITAL, COMMISSION_RATE, SLIPPAGE,
)
from stock_scanner import scan_market, scan_single, signals_to_dataframe, get_signal_summary
from backtest import run_backtest


def cmd_scan(args):
    """全市场扫描。"""
    print("开始全市场扫描...")
    print(f"参数: MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) | "
          f"KDJ({KDJ_N},{KDJ_K_SMOOTH},{KDJ_D_SMOOTH}) | "
          f"KDJ窗口={KDJ_GOLDEN_CROSS_WINDOW}")
    print("-" * 60)

    result = scan_market(delay=args.delay)
    df = signals_to_dataframe(result.signals)

    if not df.empty:
        output = args.output or "signals.csv"
        df.to_csv(output, index=False, encoding="utf-8-sig")
        print(f"信号已保存至: {output}")

    return result


def cmd_check(args):
    """单只股票检测。"""
    code = args.code
    sig = scan_single(code, "查询")
    if sig:
        print(get_signal_summary(sig))
    else:
        print(f"{code}: 未发现买入信号")


def cmd_backtest(args):
    """回测模式。"""
    print("开始回测...")
    print(f"参数: 持仓{HOLD_WEEKS}周, 最大持仓{MAX_POSITIONS}只, "
          f"初始资金{INITIAL_CAPITAL:,}, 手续费{COMMISSION_RATE:.4f}, 滑点{SLIPPAGE:.3f}")
    print("-" * 60)

    # 先扫描
    print("第一步：扫描信号...")
    result = scan_market(delay=args.delay, verbose=False)
    print(f"扫描到 {len(result.signals)} 个信号")

    # 再回测
    print("\n第二步：运行回测...")
    bt_result = run_backtest(result.signals, hold_weeks=args.hold_weeks)
    print(bt_result.summary)

    # 保存详细交易记录
    if bt_result.trades:
        import pandas as pd
        trade_df = pd.DataFrame([vars(t) for t in bt_result.trades])
        trade_df.to_csv("trades.csv", index=False, encoding="utf-8-sig")
        print("\n交易明细已保存至: trades.csv")

    return bt_result


def main():
    parser = argparse.ArgumentParser(description="MACD周线二次金叉选股系统")
    sub = parser.add_subparsers(dest="command")

    # scan
    p_scan = sub.add_parser("scan", help="全市场扫描")
    p_scan.add_argument("--delay", type=float, default=0.1, help="请求间隔秒数")
    p_scan.add_argument("--output", type=str, default="signals.csv", help="输出CSV路径")

    # check
    p_check = sub.add_parser("check", help="单只股票检测")
    p_check.add_argument("code", type=str, help="股票代码，如 000001")

    # backtest
    p_bt = sub.add_parser("backtest", help="扫描 + 回测")
    p_bt.add_argument("--delay", type=float, default=0.1, help="请求间隔秒数")
    p_bt.add_argument("--hold-weeks", type=int, default=None, help="持仓周数")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
