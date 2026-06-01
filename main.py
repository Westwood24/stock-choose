"""
主入口 — 选股 + 回测。
"""

import argparse

from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    KDJ_GOLDEN_CROSS_WINDOW, HOLD_WEEKS, MAX_POSITIONS,
    INITIAL_CAPITAL, COMMISSION_RATE, SLIPPAGE,
    FORCE_VOLUME_DIVISOR, FORCE_DELTA_CONSECUTIVE,
    RANGE_BREAK_TOLERANCE, USE_STOP_LOSS, MAX_HOLD_WEEKS,
    USE_WHITELIST, WHITELIST_MIN_OCF,
)
from stock_scanner import scan_market, scan_single, signals_to_dataframe, get_signal_summary
from backtest import run_backtest
from whitelist_manager import (
    load_whitelist, update_whitelist_auto, get_whitelist_codes,
    add_to_whitelist, remove_from_whitelist,
)


def cmd_scan(args):
    """全市场扫描。"""
    print("开始全市场扫描...")
    print(f"参数: MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) | "
          f"KDJ({KDJ_N},{KDJ_K_SMOOTH},{KDJ_D_SMOOTH}) | "
          f"KDJ窗口={KDJ_GOLDEN_CROSS_WINDOW}")
    print(f"      Force除数={FORCE_VOLUME_DIVISOR} | "
          f"区间触发连续={FORCE_DELTA_CONSECUTIVE}期 | "
          f"突破容差={RANGE_BREAK_TOLERANCE}")
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
    exit_mode = "区间止损" if USE_STOP_LOSS else f"固定持仓{HOLD_WEEKS}周"
    print("开始回测...")
    print(f"退出模式: {exit_mode} | 最大持仓{MAX_POSITIONS}只 | "
          f"初始资金{INITIAL_CAPITAL:,} | 手续费{COMMISSION_RATE:.4f} | 滑点{SLIPPAGE:.3f}")
    if USE_STOP_LOSS:
        print(f"最大持仓{MAX_HOLD_WEEKS}周(兜底) | Force除数={FORCE_VOLUME_DIVISOR} | "
              f"区间触发={FORCE_DELTA_CONSECUTIVE}期 | 突破容差={RANGE_BREAK_TOLERANCE}")
    print("-" * 60)

    # 先扫描历史信号
    print("第一步：扫描历史信号...")
    result = scan_market(delay=args.delay, verbose=False, historical=True)
    print(f"扫描到 {len(result.signals)} 个历史信号")

    # 再回测
    print("\n第二步：运行回测...")
    hold_weeks = args.hold_weeks or (MAX_HOLD_WEEKS if USE_STOP_LOSS else HOLD_WEEKS)
    mhw = args.max_hold_weeks or MAX_HOLD_WEEKS
    bt_result = run_backtest(result.signals, hold_weeks=hold_weeks, max_hold_weeks=mhw)
    print(bt_result.summary)

    # 保存详细交易记录
    if bt_result.trades:
        import pandas as pd
        trade_df = pd.DataFrame([vars(t) for t in bt_result.trades])
        trade_df.to_csv("trades.csv", index=False, encoding="utf-8-sig")
        print("\n交易明细已保存至: trades.csv")

    return bt_result


def cmd_whitelist_update(args):
    """自动更新白名单（筛选每股经营现金流 > 0 的股票）。"""
    min_ocf = args.min_ocf or WHITELIST_MIN_OCF
    print(f"开始自动更新白名单...")
    print(f"条件: 每股经营现金流 > {min_ocf}, 请求间隔={args.delay}s")
    print("-" * 60)

    update_whitelist_auto(delay=args.delay)


def cmd_whitelist_add(args):
    """手动添加股票到白名单。"""
    add_to_whitelist(args.code, args.name or "")
    print(f"已添加 {args.code} 到白名单")


def cmd_whitelist_remove(args):
    """从白名单移除股票。"""
    ok = remove_from_whitelist(args.code)
    if ok:
        print(f"已从白名单移除 {args.code}")
    else:
        print(f"{args.code} 不在白名单中")


def cmd_whitelist_show(args):
    """显示白名单概要。"""
    df = load_whitelist()
    if df.empty:
        print("白名单为空。请运行 whitelist-update 或 whitelist-add 添加股票。")
        return

    print(f"白名单共 {len(df)} 只股票")
    print(f"  自动: {(df['source'] == 'auto').sum()} 只")
    print(f"  手动: {(df['source'] == 'manual').sum()} 只")
    if "ocf_per_share" in df.columns:
        auto = df[df["source"] == "auto"]
        if not auto.empty:
            print(f"  平均每股经营现金流: {auto['ocf_per_share'].mean():.2f}")

    print(f"\n前 20 只:")
    for _, row in df.head(20).iterrows():
        print(f"  {row['code']} {row['name']}  "
              f"OCF={row.get('ocf_per_share', '-')}  "
              f"{row.get('source', '-')}  {row.get('updated_at', '-')}")

    print(f"\n配置状态: USE_WHITELIST={'开启' if USE_WHITELIST else '关闭'} "
          f"(修改 config.py 切换)")


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
    p_bt.add_argument("--hold-weeks", type=int, default=None, help="持仓周数（覆盖默认）")
    p_bt.add_argument("--max-hold-weeks", type=int, default=None, help="最大持仓周数（覆盖配置）")

    # whitelist
    p_wl = sub.add_parser("whitelist-update", help="自动更新白名单（筛选经营现金流>0的股票）")
    p_wl.add_argument("--delay", type=float, default=0.05, help="请求间隔秒数")
    p_wl.add_argument("--min-ocf", type=float, default=None, help="最低每股经营现金流")

    p_wla = sub.add_parser("whitelist-add", help="手动添加股票到白名单")
    p_wla.add_argument("code", type=str, help="股票代码")
    p_wla.add_argument("--name", type=str, default="", help="股票名称（可选）")

    p_wlr = sub.add_parser("whitelist-remove", help="从白名单移除股票")
    p_wlr.add_argument("code", type=str, help="股票代码")

    p_wls = sub.add_parser("whitelist-show", help="查看白名单")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "whitelist-update":
        cmd_whitelist_update(args)
    elif args.command == "whitelist-add":
        cmd_whitelist_add(args)
    elif args.command == "whitelist-remove":
        cmd_whitelist_remove(args)
    elif args.command == "whitelist-show":
        cmd_whitelist_show(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
