"""
全市场扫描模块 — 遍历全部 A 股，检测符合条件的买入信号。
"""

import time
from typing import Optional
from dataclasses import dataclass

import pandas as pd

from config import WEEKLY_MIN_BARS
from data_fetcher import fetch_all_stock_codes, fetch_weekly_kline
from indicators import calc_all_indicators
from signal_detector import detect_buy_signal, BuySignal, get_signal_summary


@dataclass
class ScanResult:
    """扫描结果。"""
    signals: list[BuySignal]
    total_scanned: int
    total_skipped: int  # 数据不足
    elapsed_seconds: float


def scan_single(code: str, name: str) -> Optional[BuySignal]:
    """扫描单只股票。"""
    df = fetch_weekly_kline(code)
    if df is None or len(df) < WEEKLY_MIN_BARS:
        return None

    df = calc_all_indicators(df)
    return detect_buy_signal(df, code, name)


def scan_market(
    stock_list: Optional[list[tuple[str, str]]] = None,
    delay: float = 0.1,
    verbose: bool = True,
) -> ScanResult:
    """全市场扫描。
    Args:
        stock_list: 指定股票列表 [(code, name), ...]，None 表示全市场
        delay: 请求间隔
        verbose: 是否打印进度
    Returns:
        ScanResult
    """
    if stock_list is None:
        code_df = fetch_all_stock_codes()
        stock_list = list(zip(code_df["code"], code_df["name"]))

    signals: list[BuySignal] = []
    skipped = 0
    total = len(stock_list)
    t0 = time.time()

    for idx, (code, name) in enumerate(stock_list):
        df = fetch_weekly_kline(code)
        if df is None or len(df) < WEEKLY_MIN_BARS:
            skipped += 1
            continue

        df = calc_all_indicators(df)
        sig = detect_buy_signal(df, code, name)
        if sig is not None:
            signals.append(sig)
            if verbose:
                print(f"[{idx + 1}/{total}] {get_signal_summary(sig)}")

        if verbose and (idx + 1) % 100 == 0:
            print(f"  进度: {idx + 1}/{total}, 已找到 {len(signals)} 个信号")

        time.sleep(delay)

    elapsed = time.time() - t0
    if verbose:
        print(f"\n扫描完成: 共 {total} 只, 跳过 {skipped} 只(数据不足), "
              f"找到 {len(signals)} 个信号, 耗时 {elapsed:.1f}s")

    return ScanResult(
        signals=signals,
        total_scanned=total - skipped,
        total_skipped=skipped,
        elapsed_seconds=elapsed,
    )


def signals_to_dataframe(signals: list[BuySignal]) -> pd.DataFrame:
    """将信号列表转为 DataFrame。"""
    if not signals:
        return pd.DataFrame()
    rows = [vars(s) for s in signals]
    return pd.DataFrame(rows)
