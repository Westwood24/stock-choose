"""
MACD、KDJ 指标计算模块。
"""

import pandas as pd
import numpy as np
from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    KDJ_N, KDJ_K_SMOOTH, KDJ_D_SMOOTH,
    ATR_PERIOD,
)


def calc_macd(df: pd.DataFrame) -> pd.DataFrame:
    """计算周线 MACD 指标（EMA 方式）。
    Returns:
        df: 新增 dif, dea, macd_hist 列
    """
    close = df["close"].values
    ema_fast = _ema(close, MACD_FAST)
    ema_slow = _ema(close, MACD_SLOW)

    dif = ema_fast - ema_slow
    dea = _ema(dif, MACD_SIGNAL)
    macd_hist = 2 * (dif - dea)

    df = df.copy()
    df["dif"] = dif
    df["dea"] = dea
    df["macd_hist"] = macd_hist
    return df


def calc_kdj(df: pd.DataFrame) -> pd.DataFrame:
    """计算 KDJ 指标（标准 SMA 平滑方式，权重 = 1/N）。
    与通达信/同花顺等主流平台的 KDJ 公式一致。
    Returns:
        df: 新增 k, d, j 列
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(close)

    # 初始 K=50, D=50
    k = np.full(n, 50.0)
    d = np.full(n, 50.0)

    # 标准 KDJ 平滑：K = (1/N)*RSV + ((N-1)/N)*K_prev，等价于通达信 SMA(RSV,N,1)
    weight_k = 1.0 / KDJ_K_SMOOTH
    weight_d = 1.0 / KDJ_D_SMOOTH

    for i in range(KDJ_N, n):
        high_n = high[i - KDJ_N + 1 : i + 1].max()
        low_n = low[i - KDJ_N + 1 : i + 1].min()
        rsv = (close[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50.0
        k[i] = weight_k * rsv + (1 - weight_k) * k[i - 1]
        d[i] = weight_d * k[i] + (1 - weight_d) * d[i - 1]

    j = 3 * k - 2 * d

    df = df.copy()
    df["k"] = k
    df["d"] = d
    df["j"] = j
    return df


def calc_atr(df: pd.DataFrame) -> pd.DataFrame:
    """计算 ATR(14) 平均真实波幅（EMA 平滑）。
    True Range = max(H-L, |H-prev_C|, |L-prev_C|)
    Returns:
        df: 新增 atr 列
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(close)

    tr = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    tr[0] = high[0] - low[0]

    atr = _ema(tr, ATR_PERIOD)

    df = df.copy()
    df["atr"] = atr
    return df


def calc_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """一次性计算 MACD、KDJ、ATR 指标。"""
    df = calc_macd(df)
    df = calc_kdj(df)
    df = calc_atr(df)
    return df


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """计算 EMA，自动跳过 NaN 起始段。"""
    result = np.full_like(data, np.nan, dtype=np.float64)
    if len(data) < period:
        return result
    # 找到第一个有效值的位置（跳过前段 NaN）
    valid_start = 0
    while valid_start < len(data) and np.isnan(data[valid_start]):
        valid_start += 1
    if valid_start + period > len(data):
        return result
    start_idx = valid_start + period - 1
    # 初始值用首个有效窗口的 SMA
    result[start_idx] = data[valid_start:valid_start + period].mean()
    multiplier = 2.0 / (period + 1)
    for i in range(start_idx + 1, len(data)):
        if np.isnan(data[i]):
            result[i] = result[i - 1]
        else:
            result[i] = multiplier * data[i] + (1 - multiplier) * result[i - 1]
    return result
