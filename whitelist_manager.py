"""
股票白名单池管理模块 — 基于自由现金流（每股经营现金流 > 0）筛选。
支持手动维护 CSV 和自动更新两种模式。
"""

import time
import os
from datetime import datetime

import pandas as pd

from data_fetcher import fetch_all_stock_codes

WHITELIST_FILE = "whitelist.csv"
WHITELIST_COLUMNS = ["code", "name", "ocf_per_share", "report_date", "source", "updated_at"]


def load_whitelist() -> pd.DataFrame:
    """加载白名单。若文件不存在则返回空 DataFrame。"""
    path = _whitelist_path()
    if not os.path.exists(path):
        return pd.DataFrame(columns=WHITELIST_COLUMNS)
    df = pd.read_csv(path, dtype={"code": str})
    return df


def save_whitelist(df: pd.DataFrame) -> None:
    """保存白名单到 CSV。"""
    df = df[WHITELIST_COLUMNS] if not df.empty else pd.DataFrame(columns=WHITELIST_COLUMNS)
    df.to_csv(_whitelist_path(), index=False, encoding="utf-8-sig")


def get_whitelist_codes() -> set[str]:
    """获取白名单股票代码集合（用于快速查找）。"""
    df = load_whitelist()
    if df.empty:
        return set()
    return set(df["code"].astype(str).values)


def is_whitelisted(code: str) -> bool:
    """检查某只股票是否在白名单中。"""
    return code in get_whitelist_codes()


def update_whitelist_auto(delay: float = 0.05,
                          progress_callback=None) -> pd.DataFrame:
    """自动更新白名单：遍历全市场，筛选 每股经营现金流 > 0 的股票。
    Args:
        delay: 请求间隔秒数
        progress_callback: 进度回调 (current, total)
    Returns:
        DataFrame of whitelisted stocks
    """
    import akshare as ak

    print("获取 A 股列表...")
    stock_df = fetch_all_stock_codes()
    codes = stock_df["code"].tolist()
    names = dict(zip(stock_df["code"], stock_df["name"]))
    total = len(codes)

    whitelist_rows = []
    skipped = 0
    failed = 0

    for idx, code in enumerate(codes):
        try:
            fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        except Exception:
            failed += 1
            if progress_callback:
                progress_callback(idx + 1, total)
            time.sleep(delay)
            continue

        if fin is None or fin.empty:
            skipped += 1
            if progress_callback:
                progress_callback(idx + 1, total)
            time.sleep(delay)
            continue

        # 取最新一期的每股经营现金流
        if "每股经营现金流" not in fin.columns:
            skipped += 1
            if progress_callback:
                progress_callback(idx + 1, total)
            time.sleep(delay)
            continue

        fin = fin.dropna(subset=["每股经营现金流"])
        if fin.empty:
            skipped += 1
            if progress_callback:
                progress_callback(idx + 1, total)
            time.sleep(delay)
            continue

        latest = fin.iloc[-1]
        ocf = float(latest["每股经营现金流"])

        if ocf > 0:
            report_date = str(latest.get("报告日期", ""))
            whitelist_rows.append({
                "code": code,
                "name": names.get(code, ""),
                "ocf_per_share": round(ocf, 4),
                "report_date": report_date,
                "source": "auto",
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
            })

        if progress_callback:
            progress_callback(idx + 1, total)

        if (idx + 1) % 200 == 0:
            print(f"  进度: {idx + 1}/{total}, 已入选 {len(whitelist_rows)} 只")

        time.sleep(delay)

    df = pd.DataFrame(whitelist_rows, columns=WHITELIST_COLUMNS)
    save_whitelist(df)
    print(f"\n白名单更新完成: {len(df)} 只入选, "
          f"{skipped} 只跳过(无数据), {failed} 只失败")
    return df


def add_to_whitelist(code: str, name: str = "") -> None:
    """手动添加单只股票到白名单。"""
    df = load_whitelist()
    existing = df["code"].astype(str).values
    if code in existing:
        return

    new_row = {
        "code": code, "name": name,
        "ocf_per_share": 0, "report_date": "",
        "source": "manual", "updated_at": datetime.now().strftime("%Y-%m-%d"),
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_whitelist(df)


def remove_from_whitelist(code: str) -> bool:
    """从白名单中移除股票。返回是否成功。"""
    df = load_whitelist()
    mask = df["code"].astype(str) == code
    if not mask.any():
        return False
    df = df[~mask]
    save_whitelist(df)
    return True


def _whitelist_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), WHITELIST_FILE)
