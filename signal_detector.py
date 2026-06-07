"""
选股信号检测模块 — 价格行为盘整区间 + MACD 周线二次金叉 + KDJ 金叉确认。
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    KDJ_GOLDEN_CROSS_WINDOW,
    MACD_APPROACHING_THRESHOLD,
    UPTREND_CONSECUTIVE,
    VOLUME_MA_PERIOD,
    RANGE_BREAK_TOLERANCE,
    ZONE_MIN_ATR_MULT,
    ZONE_SUPPORT_BARS,
    STOP_LOSS_ATR_BUFFER,
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
    range_low: float = 0.0
    range_high: float = 0.0
    stop_loss: float = 0.0


# ============================================================
# 盘整区间检测
# ============================================================

def detect_all_zones(df: pd.DataFrame) -> list[dict]:
    """基于价格行为检测盘整区间。

    上升状态定义（三个条件同时满足）：
        - 最高价高于前一根最高价
        - 最低价高于前一根最低价
        - 成交量大于前 VOLUME_MA_PERIOD 根 K 线均值

    连续 UPTREND_CONSECUTIVE 个周期为上升状态时，开始记录上涨区间：
        - 区间起始 = 第一个上升状态截面的上一个截面最低价
        - 区间终点 = 第一个"最高价 < 前一根最高价"截面的上一个截面最高价

    Returns:
        list[dict] with keys: range_low, range_high, zone_end_idx, closed
    """
    high = df["high"].values
    low = df["low"].values
    amount = df["amount"].values
    n = len(high)

    if n < VOLUME_MA_PERIOD + UPTREND_CONSECUTIVE + 1:
        return []

    # 计算成交量 MA(N)（滚动窗口，包含当前 bar）
    vol_ma = pd.Series(amount).rolling(
        window=VOLUME_MA_PERIOD, min_periods=VOLUME_MA_PERIOD
    ).mean().values

    # 标记上升状态
    is_uptrend = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if not np.isnan(vol_ma[i]):
            is_uptrend[i] = (
                high[i] > high[i - 1]
                and low[i] > low[i - 1]
                and amount[i] > vol_ma[i]
            )

    zones = []
    i = UPTREND_CONSECUTIVE  # 从第 N 根 bar 开始检查（需要 N 根确认）

    while i < n:
        # 检查最近 UPTREND_CONSECUTIVE 个周期是否连续为上升状态
        triggered = True
        for j in range(i - UPTREND_CONSECUTIVE + 1, i + 1):
            if not is_uptrend[j]:
                triggered = False
                break

        if not triggered:
            i += 1
            continue

        # 第一个上升状态的位置
        first_uptrend_idx = i - UPTREND_CONSECUTIVE + 1
        if first_uptrend_idx < 1:
            i += 1
            continue

        # 区间起始 = 第一个上升状态截面的上一个截面最低价
        # A2: 多 bar 支撑 — 取 uptrend 前 N 根 bar 的最低点
        if ZONE_SUPPORT_BARS > 1:
            start = max(0, first_uptrend_idx - ZONE_SUPPORT_BARS)
            range_low = float(np.min(low[start:first_uptrend_idx]))
        else:
            range_low = low[first_uptrend_idx - 1]

        # 往后找第一个"最高价 < 前一根最高价"的位置（趋势破坏点）
        # 区间终点 = 该截面的上一个截面最高价
        fwd_idx = i + 1
        while fwd_idx < n and high[fwd_idx] >= high[fwd_idx - 1]:
            fwd_idx += 1

        if fwd_idx < n:
            # 找到了趋势破坏点
            range_high = high[fwd_idx - 1]
            zone_end_idx = fwd_idx - 1
            closed = True
        else:
            # 数据结束时未找到趋势破坏，区间延伸到末尾
            range_high = high[-1]
            zone_end_idx = n - 1
            closed = False

        # A1: ATR 区间宽度过滤 — 跳过过窄的区间
        if ZONE_MIN_ATR_MULT > 0 and "atr" in df.columns:
            atr_val = df["atr"].values[zone_end_idx]
            if not np.isnan(atr_val) and (range_high - range_low) < ZONE_MIN_ATR_MULT * atr_val:
                i = max(fwd_idx, i + 1)
                continue

        if range_high > range_low:
            zones.append({
                "range_low": range_low,
                "range_high": range_high,
                "zone_end_idx": zone_end_idx,
                "closed": closed,
            })

        # 跳过已处理区域，继续扫描后续
        i = max(fwd_idx, i + 1)

    return zones


# ============================================================
# 综合信号检测
# ============================================================

def detect_all_signals(df: pd.DataFrame, code: str, name: str) -> list[BuySignal]:
    """检测历史所有买入信号（区间 + MACD + KDJ 综合判定）。
    Returns:
        list[BuySignal]
    """
    zones = detect_all_zones(df)
    if not zones:
        return []

    dif = df["dif"].values
    dea = df["dea"].values
    k_vals = df["k"].values
    d_vals = df["d"].values
    high = df["high"].values
    low = df["low"].values
    dates = df["date"].values
    n = len(df)

    signals = []

    for zone in zones:
        zone_end = zone["zone_end_idx"]
        range_low = zone["range_low"]
        range_high = zone["range_high"]

        for i in range(zone_end + 1, n):
            # 检查是否突破区间
            if high[i] > range_high * (1 + RANGE_BREAK_TOLERANCE):
                break
            if low[i] < range_low * (1 - RANGE_BREAK_TOLERANCE):
                break

            # 检查 MACD 二次金叉（使用截至 i 的数据）
            macd_result = _check_macd_second_cross_at(dif, dea, i, zone_end)
            if macd_result is None:
                continue

            signal_type, cross_idx = macd_result

            # 检查 KDJ 金叉
            kdj_bars_ago = _find_kdj_golden_cross_at(k_vals, d_vals, i,
                                                     within=KDJ_GOLDEN_CROSS_WINDOW)
            if kdj_bars_ago is None:
                continue

            # 构造信号 — 信号日期取所有条件（MACD+KDJ）都满足的当前 bar
            signal_date = str(pd.Timestamp(dates[i]).date())

            # 止损价 = 区间生成后后续的最低价（≥ 区间下限）
            # 从 zone_end_idx+1 到当前信号 bar i 之间取最低价
            post_zone_lows = low[zone_end + 1 : i + 1]
            if len(post_zone_lows) > 0:
                stop_loss = float(np.min(post_zone_lows))
                # 确保止损价不低于区间下限
                stop_loss = max(stop_loss, range_low)
            else:
                stop_loss = range_low

            # C1: ATR 止损缓冲 — 给止损一点呼吸空间，减少震出
            if STOP_LOSS_ATR_BUFFER > 0 and "atr" in df.columns:
                atr_val = df["atr"].values[i]
                if not np.isnan(atr_val):
                    stop_loss = stop_loss - STOP_LOSS_ATR_BUFFER * atr_val
                    stop_loss = max(stop_loss, 0.01)  # 不能为负

            stop_loss = round(stop_loss, 2)

            signals.append(BuySignal(
                code=code,
                name=name,
                date=signal_date,
                signal_type=signal_type,
                dif=round(float(dif[i]), 4),
                dea=round(float(dea[i]), 4),
                k=round(float(k_vals[i]), 2),
                d=round(float(d_vals[i]), 2),
                kdj_cross_bars_ago=kdj_bars_ago,
                range_low=round(range_low, 2),
                range_high=round(range_high, 2),
                stop_loss=round(stop_loss, 2),
            ))
            break  # 每个区间只取第一个信号

    # 同一天多个区间产生信号时，保留区间范围（range_high - range_low）最大的
    if signals:
        date_to_best = {}
        for sig in signals:
            sig_range = sig.range_high - sig.range_low
            if sig.date not in date_to_best or sig_range > (date_to_best[sig.date].range_high - date_to_best[sig.date].range_low):
                date_to_best[sig.date] = sig
        signals = sorted(date_to_best.values(), key=lambda s: s.date)

    return signals


def detect_buy_signal(df: pd.DataFrame, code: str, name: str) -> Optional[BuySignal]:
    """在单只股票的周线数据上检测当前买入信号（仅返回最新 K 线上的信号）。"""
    all_signals = detect_all_signals(df, code, name)
    if not all_signals:
        return None

    latest = all_signals[-1]
    latest_date = str(pd.Timestamp(df["date"].values[-1]).date())

    # 仅当信号日期是最新一根 K 线时才返回
    if latest.date == latest_date:
        return latest

    # "即将二次金叉"的信号日期取的是当前 bar，通常能匹配
    return None


# ============================================================
# MACD / KDJ 底层检测（在指定 bar 上判定）
# ============================================================

def _check_macd_second_cross_at(dif: np.ndarray, dea: np.ndarray, idx: int,
                               min_idx: int = 0):
    """在 idx 位置检测 MACD 二次金叉或即将二次金叉。
    使用截至 idx 的数据。
    Args:
        min_idx: 最近一次金叉必须在此索引之后（用于区间约束）
    Returns:
        (signal_type, cross_idx) 或 None
    """
    # 截取到 idx（包含）
    dif_slice = dif[:idx + 1]
    dea_slice = dea[:idx + 1]

    # 找所有金叉位置
    golden_crosses = []
    for j in range(1, len(dif_slice)):
        if dif_slice[j] > dea_slice[j] and dif_slice[j - 1] <= dea_slice[j - 1]:
            golden_crosses.append(j)

    if len(golden_crosses) >= 2:
        last_gc = golden_crosses[-1]
        # 最近一次金叉必须在 min_idx 之后
        if last_gc > min_idx:
            prev_gc = golden_crosses[-2]
            has_dead_cross = False
            for j in range(prev_gc + 1, last_gc):
                if dif_slice[j] < dea_slice[j] and dif_slice[j - 1] >= dea_slice[j - 1]:
                    has_dead_cross = True
                    break
            if has_dead_cross:
                return ("second_golden_cross", last_gc)
            # 无死叉 → 去掉最后一个"伪金叉"
            golden_crosses = golden_crosses[:-1]

    # 只有 0 或 1 次有效金叉 → 检查"即将二次金叉"
    return _check_approaching_second_cross(dif_slice, dea_slice, golden_crosses)


def _check_approaching_second_cross(dif: np.ndarray, dea: np.ndarray,
                                     golden_crosses: list):
    """检测是否即将形成二次金叉。"""
    if not golden_crosses:
        return None

    if dif[-1] >= dea[-1]:
        return None

    if len(dif) < 4:
        return None

    dif_recent = dif[-4:]
    if dif_recent[-1] - dif_recent[-2] < 0:
        return None
    if dif_recent[-1] - dif_recent[0] <= 0:
        return None

    gap_now = abs(dif[-1] - dea[-1])
    gap_before = abs(dif[-4] - dea[-4])
    if gap_now / max(gap_before, 1e-10) > (1 - MACD_APPROACHING_THRESHOLD):
        return None

    return ("approaching", -1)


def _find_kdj_golden_cross_at(k: np.ndarray, d: np.ndarray, idx: int,
                               within: int = 4):
    """在 idx 位置的最近 N 个周期内寻找 KDJ 金叉。
    Returns:
        bars_ago 或 None
    """
    for bars_ago in range(within):
        j = idx - bars_ago
        if j <= 0:
            continue
        if k[j] > d[j] and k[j - 1] <= d[j - 1]:
            return bars_ago
    return None


# ============================================================
# 工具函数
# ============================================================

def get_signal_summary(signal: BuySignal) -> str:
    """生成信号摘要字符串。"""
    type_label = "MACD二次金叉" if signal.signal_type == "second_golden_cross" else "MACD即将二次金叉"
    return (
        f"{signal.code} {signal.name} | {type_label} | "
        f"DIF={signal.dif:.4f} DEA={signal.dea:.4f} | "
        f"K={signal.k:.2f} D={signal.d:.2f} | "
        f"KDJ金叉[{signal.kdj_cross_bars_ago}]根K线前 | "
        f"区间[{signal.range_low:.2f}, {signal.range_high:.2f}] "
        f"止损={signal.stop_loss:.2f}"
    )
