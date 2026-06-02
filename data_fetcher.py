"""
AKshare 数据获取模块 — 获取 A 股周线数据（腾讯源，日线转周线）。
"""

import time
from typing import Optional

import akshare as ak
import pandas as pd

from config import WEEKLY_MIN_BARS


def _code_to_tx_symbol(code: str) -> str:
    """将股票代码转为腾讯接口格式（sh/sz 前缀）。"""
    if code.startswith(("60", "68")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def _daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线转周线。"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    weekly = df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "amount": "sum",
    }).dropna()
    weekly = weekly.reset_index()
    # resample("W") labels bars with Sunday — shift to Friday (last trading day)
    weekly["date"] = weekly["date"] - pd.Timedelta(days=2)
    return weekly


def fetch_all_stock_codes() -> pd.DataFrame:
    """获取全部 A 股代码列表（含重试）。"""
    for attempt in range(3):
        try:
            df = ak.stock_info_a_code_name()
            df = df.rename(columns={"code": "code", "name": "name"})
            return df[["code", "name"]]
        except Exception:
            if attempt < 2:
                time.sleep(2)
    raise RuntimeError("获取股票列表失败，请检查网络")


def fetch_weekly_kline(code: str, start_date: str = "20100101",
                       end_date: str = "20500101") -> Optional[pd.DataFrame]:
    """获取单只股票的周线 K 线数据（腾讯源→日转周）。
    Returns:
        DataFrame with columns: date, open, high, low, close, amount
    """
    df = None
    for attempt in range(3):
        try:
            symbol = _code_to_tx_symbol(code)
            df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date,
                                       end_date=end_date, adjust="qfq")
            break
        except Exception:
            if attempt < 2:
                time.sleep(1)
    if df is None:
        return None

    if df is None or df.empty:
        return None

    df = df.rename(columns={
        "成交额": "amount",
    })

    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _daily_to_weekly(df)

    if "amount" not in df.columns:
        df["amount"] = 0

    if len(df) < WEEKLY_MIN_BARS:
        return None

    return df


def fetch_daily_kline(code: str, start_date: str = "20100101",
                       end_date: str = "20500101") -> Optional[pd.DataFrame]:
    """获取单只股票的日线 K 线数据（腾讯源，不复权转周线）。
    与 fetch_weekly_kline 的区别：跳过 _daily_to_weekly，直接返回日线。
    Returns:
        DataFrame with columns: date, open, high, low, close, amount
    """
    from config import DAILY_MIN_BARS

    df = None
    for attempt in range(3):
        try:
            symbol = _code_to_tx_symbol(code)
            df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date,
                                       end_date=end_date, adjust="qfq")
            break
        except Exception:
            if attempt < 2:
                time.sleep(1)
    if df is None or df.empty:
        return None

    df = df.rename(columns={
        "成交额": "amount",
    })

    for col in ["open", "high", "low", "close", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 保持日线，不做周线 resample
    if "amount" not in df.columns:
        df["amount"] = 0

    if len(df) < DAILY_MIN_BARS:
        return None

    return df


def fetch_all_weekly(stock_codes: list[str], delay: float = 0.1,
                     progress_callback=None) -> dict[str, pd.DataFrame]:
    """批量获取多只股票的周线数据。"""
    result = {}
    total = len(stock_codes)

    for idx, code in enumerate(stock_codes):
        df = fetch_weekly_kline(code)
        if df is not None:
            result[code] = df
        if progress_callback:
            progress_callback(idx + 1, total)
        time.sleep(delay)

    return result
