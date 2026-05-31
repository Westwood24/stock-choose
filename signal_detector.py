"""
选股信号检测模块 — 检测 MACD 周线二次金叉 + KDJ 金叉确认。
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    KDJ_GOLDEN_CROSS_WINDOW,
    MACD_SECOND_CROSS_LOOKBACK,
    MACD_APPROACHING_THRESHOLD,
)


@dataclass
class BuySignal:
    """买入信号数据类。"""
    code: str
    name: str
    date: str
    signal_type: str  # "second_golden_cross" | "approaching"
    dif: float
    dea: float
    k: float
    d: float
    kdj_cross_bars_ago: int  # KDJ 金叉距今多少根 K 线


def detect_buy_signal(df: pd.DataFrame, code: str, name: str) -> Optional[BuySignal]:
    """在单只股票的周线数据上检测买入信号。
    Returns:
        BuySignal 或 None
    """
    n = len(df)
    if n < MACD_SECOND_CROSS_LOOKBACK:
        return None

    # 提取最新数据，只看尾部
    lookback = min(n, MACD_SECOND_CROSS_LOOKBACK)
    recent = df.iloc[-lookback:].reset_index(drop=True)

    dif = recent["dif"].values
    dea = recent["dea"].values
    k = recent["k"].values
    d_vals = recent["d"].values

    # --- 1. 检测 MACD 二次金叉 ---
    macd_result = _check_macd_second_cross(dif, dea)
    if macd_result is None:
        return None

    signal_type, cross_idx = macd_result

    # --- 2. 检测 KDJ 金叉是否在 N 个周期内 ---
    kdj_bars_ago = _find_kdj_golden_cross(k, d_vals, within=KDJ_GOLDEN_CROSS_WINDOW)
    if kdj_bars_ago is None:
        return None

    # --- 3. 构造信号 ---
    latest = df.iloc[-1]
    signal_date = str(latest["date"].date()) if signal_type == "approaching" else str(recent.iloc[cross_idx]["date"].date())

    return BuySignal(
        code=code,
        name=name,
        date=signal_date,
        signal_type=signal_type,
        dif=round(float(latest["dif"]), 4),
        dea=round(float(latest["dea"]), 4),
        k=round(float(latest["k"]), 2),
        d=round(float(latest["d"]), 2),
        kdj_cross_bars_ago=kdj_bars_ago,
    )


def _check_macd_second_cross(dif: np.ndarray, dea: np.ndarray):
    """检测 MACD 二次金叉或即将二次金叉。
    Returns:
        (signal_type, cross_idx) 或 None
    """
    n = len(dif)

    # 找所有金叉位置
    golden_crosses = []
    for i in range(1, n):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            golden_crosses.append(i)

    if len(golden_crosses) < 2:
        # 只有一次金叉 → 检查是否"即将二次金叉"
        return _check_approaching_second_cross(dif, dea, golden_crosses)

    # 最近一次金叉
    last_gc = golden_crosses[-1]

    # 验证最近一次金叉和上一次之间是否有死叉
    prev_gc = golden_crosses[-2]
    has_dead_cross = False
    for i in range(prev_gc + 1, last_gc):
        if dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            has_dead_cross = True
            break

    if has_dead_cross:
        return ("second_golden_cross", last_gc)

    # 没有死叉也当作二次金叉（连续金叉也接受）
    return ("second_golden_cross", last_gc)


def _check_approaching_second_cross(dif: np.ndarray, dea: np.ndarray,
                                     golden_crosses: list):
    """检测是否即将形成二次金叉。
    条件：
    1. 之前有过一次金叉
    2. 当前 DIF 在 DEA 下方
    3. DIF 上升趋势（最近 3 周期斜率 > 0）
    4. DIF 与 DEA 的差距在缩小
    """
    n = len(dif)

    if not golden_crosses:
        return None  # 从未金叉过，不是"二次"

    # 当前 DIF 必须在 DEA 下方
    if dif[-1] >= dea[-1]:
        return None

    # DIF 近 3 周期趋势向上
    if len(dif) < 4:
        return None
    dif_recent = dif[-4:]
    if dif_recent[-1] - dif_recent[-2] < 0:
        return None
    # 二次确认：斜率整体向上
    if dif_recent[-1] - dif_recent[0] <= 0:
        return None

    # DIF 与 DEA 差距在缩小
    gap_now = abs(dif[-1] - dea[-1])
    gap_before = abs(dif[-4] - dea[-4])
    if gap_now / max(gap_before, 1e-10) > (1 - MACD_APPROACHING_THRESHOLD):
        return None

    return ("approaching", -1)


def _find_kdj_golden_cross(k: np.ndarray, d: np.ndarray,
                           within: int = 4):
    """在最近 N 个周期内寻找 KDJ 金叉。
    Returns:
        bars_ago（距今K线数）或 None
    """
    n = len(k)
    for bars_ago in range(within):
        i = n - 1 - bars_ago
        if i <= 0:
            continue
        if k[i] > d[i] and k[i - 1] <= d[i - 1]:
            return bars_ago
    return None


def get_signal_summary(signal: BuySignal) -> str:
    """生成信号摘要字符串。"""
    type_label = "MACD二次金叉" if signal.signal_type == "second_golden_cross" else "MACD即将二次金叉"
    return (
        f"{signal.code} {signal.name} | {type_label} | "
        f"DIF={signal.dif:.4f} DEA={signal.dea:.4f} | "
        f"K={signal.k:.2f} D={signal.d:.2f} | "
        f"KDJ金叉[{signal.kdj_cross_bars_ago}]根K线前"
    )
