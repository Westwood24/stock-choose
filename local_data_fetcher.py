r"""
本地 Parquet 数据库适配器 — 从 D:\Trae test\stock data\data\daily_qfq 读取 QFQ 数据并转换周线。
"""

import os
import time
from typing import Optional

import pandas as pd
import numpy as np

from config import WEEKLY_MIN_BARS, DAILY_MIN_BARS

# 本地数据库路径
LOCAL_DATA_DIR = r"D:\Trae test\stock data\data"
LOCAL_DAILY_QFQ_DIR = os.path.join(LOCAL_DATA_DIR, "daily_qfq")
LOCAL_STOCK_BASIC = os.path.join(LOCAL_DATA_DIR, "stock_basic")


def _ts_code_to_code(ts_code: str) -> str:
    """将 tushare 格式的 ts_code (如 000001.SZ) 转为纯数字代码 (000001)。"""
    return ts_code.split(".")[0]


def _code_to_ts_code_pattern(code: str) -> str:
    """将纯数字代码转为 ts_code 前缀匹配。"""
    if code.startswith(("60", "68")):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


def _daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线转周线。使用 QFQ 调整后的价格列。"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("date")
    weekly = df.resample("W").agg({
        "open_adj": "first",
        "high_adj": "max",
        "low_adj": "min",
        "close_adj": "last",
        "amount": "sum",
    }).dropna()
    weekly = weekly.reset_index()
    # resample("W") 标签为周日，修正为周五
    weekly["date"] = weekly["date"] - pd.Timedelta(days=2)
    # 重命名为标准列名
    weekly = weekly.rename(columns={
        "open_adj": "open",
        "high_adj": "high",
        "low_adj": "low",
        "close_adj": "close",
    })
    return weekly


def get_all_stock_codes() -> list[tuple[str, str]]:
    """获取本地数据库中所有 A 股代码及名称。
    Returns:
        list of (code, name) tuples — code 为纯数字格式如 '000001'
    """
    if not os.path.isdir(LOCAL_STOCK_BASIC):
        raise FileNotFoundError(f"stock_basic 目录不存在: {LOCAL_STOCK_BASIC}")

    df = pd.read_parquet(LOCAL_STOCK_BASIC)
    codes = []
    for _, row in df.iterrows():
        ts_code = row["ts_code"]
        code = _ts_code_to_code(ts_code)
        name = row.get("name", "")
        codes.append((code, name))
    return codes


def get_all_ts_codes_from_daily() -> list[str]:
    """直接从 daily_qfq 目录获取所有出现过的 ts_code 列表（更准确）。"""
    if not os.path.isdir(LOCAL_DAILY_QFQ_DIR):
        raise FileNotFoundError(f"daily_qfq 目录不存在: {LOCAL_DAILY_QFQ_DIR}")

    df = pd.read_parquet(LOCAL_DAILY_QFQ_DIR, columns=["ts_code"])
    return df["ts_code"].unique().tolist()


def fetch_weekly_from_local(code: str) -> Optional[pd.DataFrame]:
    """从本地 Parquet 数据库获取单只股票的周线数据。
    Args:
        code: 纯数字股票代码，如 '000001'
    Returns:
        DataFrame with columns: date, open, high, low, close, amount
    """
    if not os.path.isdir(LOCAL_DAILY_QFQ_DIR):
        raise FileNotFoundError(f"daily_qfq 目录不存在: {LOCAL_DAILY_QFQ_DIR}")

    ts_code = _code_to_ts_code_pattern(code)

    try:
        df = pd.read_parquet(
            LOCAL_DAILY_QFQ_DIR,
            filters=[("ts_code", "=", ts_code)],
        )
    except Exception:
        return None

    if df.empty:
        return None

    # 按日期排序
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 日线转周线
    weekly = _daily_to_weekly(df)

    if len(weekly) < WEEKLY_MIN_BARS:
        return None

    return weekly


def fetch_all_weekly_from_local(
    stock_codes: list[str],
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    """批量从本地数据库获取多只股票的周线数据。
    少于 500 只时用 parquet filter 加速；超过时读取全库再过滤。
    Args:
        stock_codes: 纯数字代码列表
        verbose: 是否打印进度
    Returns:
        dict[code, DataFrame]
    """
    if not os.path.isdir(LOCAL_DAILY_QFQ_DIR):
        raise FileNotFoundError(f"daily_qfq 目录不存在: {LOCAL_DAILY_QFQ_DIR}")

    code_set = set(stock_codes)

    if len(stock_codes) <= 500:
        # 少量股票：用 parquet filter 加速
        ts_codes_to_fetch = [_code_to_ts_code_pattern(c) for c in stock_codes]
        if verbose:
            print(f"正在从本地数据库读取 {len(stock_codes)} 只股票日线数据...")
        all_data = pd.read_parquet(
            LOCAL_DAILY_QFQ_DIR,
            filters=[("ts_code", "in", ts_codes_to_fetch)],
        )
    else:
        # 大量股票：读取全库后在 pandas 中过滤（避免 filter 列表过长）
        if verbose:
            print(f"正在读取全库日线数据（{len(stock_codes)} 只股票，全量读取后过滤）...")
        all_data = pd.read_parquet(LOCAL_DAILY_QFQ_DIR)
        ts_code_set = set(_code_to_ts_code_pattern(c) for c in stock_codes)
        all_data = all_data[all_data["ts_code"].isin(ts_code_set)]

    if verbose:
        print(f"  读取完成: {len(all_data)} 条日线记录，{all_data['ts_code'].nunique()} 只股票")

    result = {}
    for ts_code, group in all_data.groupby("ts_code"):
        code = _ts_code_to_code(ts_code)
        if code not in code_set:
            continue

        group = group.sort_values("trade_date").reset_index(drop=True)
        weekly = _daily_to_weekly(group)

        if len(weekly) >= WEEKLY_MIN_BARS:
            result[code] = weekly

    if verbose:
        print(f"  周线转换完成: {len(result)} 只有效股票（≥{WEEKLY_MIN_BARS}根周线）")

    return result


def _daily_raw_to_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """将 tushare QFQ 日线数据转为标准 OHLCV 格式（不转换周线）。
    使用 _adj 列（前复权价格），保持日线频率。
    """
    df = df.copy()
    df = df.sort_values("trade_date").reset_index(drop=True)
    # 只用 _adj 列，避免 rename 后与原始列名冲突导致重复列
    result = pd.DataFrame({
        "date": pd.to_datetime(df["trade_date"]),
        "open": df["open_adj"],
        "high": df["high_adj"],
        "low": df["low_adj"],
        "close": df["close_adj"],
        "amount": df["amount"],
    })
    return result


def fetch_all_daily_from_local(
    stock_codes: list[str],
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    """批量从本地数据库获取多只股票的日线数据（不转换周线）。
    少于 500 只时用 parquet filter 加速；超过时读取全库再过滤。
    Args:
        stock_codes: 纯数字代码列表
        verbose: 是否打印进度
    Returns:
        dict[code, DataFrame] with columns: date, open, high, low, close, amount
    """
    if not os.path.isdir(LOCAL_DAILY_QFQ_DIR):
        raise FileNotFoundError(f"daily_qfq 目录不存在: {LOCAL_DAILY_QFQ_DIR}")

    code_set = set(stock_codes)

    if len(stock_codes) <= 500:
        ts_codes_to_fetch = [_code_to_ts_code_pattern(c) for c in stock_codes]
        if verbose:
            print(f"正在从本地数据库读取 {len(stock_codes)} 只股票日线数据...")
        all_data = pd.read_parquet(
            LOCAL_DAILY_QFQ_DIR,
            filters=[("ts_code", "in", ts_codes_to_fetch)],
        )
    else:
        if verbose:
            print(f"正在读取全库日线数据（{len(stock_codes)} 只股票，全量读取后过滤）...")
        all_data = pd.read_parquet(LOCAL_DAILY_QFQ_DIR)
        ts_code_set = set(_code_to_ts_code_pattern(c) for c in stock_codes)
        all_data = all_data[all_data["ts_code"].isin(ts_code_set)]

    if verbose:
        print(f"  读取完成: {len(all_data)} 条日线记录，{all_data['ts_code'].nunique()} 只股票")

    result = {}
    for ts_code, group in all_data.groupby("ts_code"):
        code = _ts_code_to_code(ts_code)
        if code not in code_set:
            continue

        daily = _daily_raw_to_ohlcv(group)

        if len(daily) >= DAILY_MIN_BARS:
            result[code] = daily

    if verbose:
        print(f"  日线筛选完成: {len(result)} 只有效股票（≥{DAILY_MIN_BARS}根日线）")

    return result
