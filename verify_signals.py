"""
随机抽取 100 支 A 股，验证买入信号发出后 20 周的平均涨跌幅。
"""

import time
import numpy as np
import pandas as pd

from config import KDJ_GOLDEN_CROSS_WINDOW
from data_fetcher import fetch_all_stock_codes, fetch_weekly_kline
from indicators import calc_all_indicators


# 备选股票列表（API 不稳定时使用）：100 只上市超 10 年的老股票
FALLBACK_STOCKS = [
    ("000001", "平安银行"), ("000002", "万科A"), ("000006", "深振业A"),
    ("000012", "南玻A"), ("000016", "深康佳A"), ("000021", "深科技"),
    ("000027", "深圳能源"), ("000028", "国药一致"), ("000031", "大悦城"),
    ("000039", "中集集团"), ("000049", "德赛电池"), ("000050", "深天马A"),
    ("000059", "华锦股份"), ("000060", "中金岭南"), ("000061", "农产品"),
    ("000062", "深圳华强"), ("000063", "中兴通讯"), ("000069", "华侨城A"),
    ("000070", "特发信息"), ("000078", "海王生物"), ("000088", "盐田港"),
    ("000089", "深圳机场"), ("000090", "天健集团"), ("000096", "广聚能源"),
    ("000099", "中信海直"), ("000100", "TCL科技"), ("000155", "川能动力"),
    ("000157", "中联重科"), ("000301", "东方盛虹"), ("000333", "美的集团"),
    ("000338", "潍柴动力"), ("000400", "许继电气"), ("000401", "冀东水泥"),
    ("000402", "金融街"), ("000403", "派林生物"), ("000404", "长虹华意"),
    ("000407", "胜利股份"), ("000408", "藏格矿业"), ("000410", "沈阳机床"),
    ("000411", "英特集团"), ("000415", "渤海租赁"), ("000417", "合肥百货"),
    ("000419", "通程控股"), ("000420", "吉林化纤"), ("000421", "南京公用"),
    ("000422", "湖北宜化"), ("000423", "东阿阿胶"), ("000425", "徐工机械"),
    ("000426", "兴业银锡"), ("000428", "华天酒店"), ("000429", "粤高速A"),
    ("000430", "张家界"), ("000488", "晨鸣纸业"), ("000498", "山东路桥"),
    ("000501", "武商集团"), ("000503", "国新健康"), ("000505", "京粮控股"),
    ("000506", "中润资源"), ("000507", "珠海港"), ("000509", "华塑控股"),
    ("000510", "新金路"), ("000513", "丽珠集团"), ("000514", "渝开发"),
    ("000516", "国际医学"), ("000517", "荣安地产"), ("000518", "四环生物"),
    ("000519", "中兵红箭"), ("000520", "凤凰航运"), ("000521", "长虹美菱"),
    ("000523", "红棉股份"), ("000525", "ST红太阳"), ("000528", "柳工"),
    ("000529", "广弘控股"), ("000530", "冰山冷热"), ("000531", "穗恒运A"),
    ("000532", "华金资本"), ("000533", "顺钠股份"), ("000534", "万泽股份"),
    ("000536", "华映科技"), ("000537", "中绿电"), ("000538", "云南白药"),
    ("000539", "粤电力A"), ("000541", "佛山照明"), ("000543", "皖能电力"),
    ("000544", "中原环保"), ("000545", "金浦钛业"), ("000546", "金圆股份"),
    ("000547", "航天发展"), ("000550", "江铃汽车"), ("000551", "创元科技"),
    ("000552", "甘肃能化"), ("000553", "安道麦A"), ("000554", "泰山石油"),
    ("000555", "神州信息"), ("000557", "西部创业"), ("000558", "莱茵体育"),
    ("000559", "万向钱潮"), ("000560", "我爱我家"), ("000561", "烽火电子"),
    ("000563", "陕国投A"), ("000565", "渝三峡A"), ("000566", "海南海药"),
    ("000567", "海德股份"), ("000568", "泸州老窖"), ("000570", "苏常柴A"),
]


def find_historical_signals(df: pd.DataFrame) -> list[int]:
    """在历史数据中高效扫描所有买入信号索引。
    查找 MACD 二次金叉 + KDJ 4周内金叉 的组合。
    """
    n = len(df)
    dif = df["dif"].values
    dea = df["dea"].values
    k = df["k"].values
    d = df["d"].values

    # 1. 找所有 MACD 金叉位置
    macd_gc = []
    for i in range(1, n):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            macd_gc.append(i)

    if len(macd_gc) < 2:
        return []

    # 2. 找所有死叉位置
    dead_cross = set()
    for i in range(1, n):
        if dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            dead_cross.add(i)

    # 3. 找所有 KDJ 金叉位置
    kdj_gc = set()
    for i in range(1, n):
        if k[i] > d[i] and k[i - 1] <= d[i - 1]:
            kdj_gc.add(i)

    # 4. 筛选：二次金叉（与上一次金叉之间有死叉）+ KDJ 确认
    signals = []
    for idx in range(1, len(macd_gc)):
        prev_gc = macd_gc[idx - 1]
        curr_gc = macd_gc[idx]

        # 检查两次金叉之间是否有死叉
        has_dc = any(dc in dead_cross for dc in range(prev_gc + 1, curr_gc))
        if not has_dc:
            continue

        # 检查 KDJ 在 [curr_gc - KDJ_WINDOW, curr_gc] 范围内是否有金叉
        kdj_found = False
        for offset in range(0, KDJ_GOLDEN_CROSS_WINDOW + 1):
            if (curr_gc - offset) in kdj_gc:
                kdj_found = True
                break
        if kdj_found:
            signals.append(curr_gc)

    return signals


def compute_forward_return(df: pd.DataFrame, signal_idx: int,
                           periods: int = 20) -> float:
    """计算信号发出后 N 个周期的涨跌幅。"""
    buy_price = df.iloc[signal_idx]["close"]
    future_idx = min(signal_idx + periods, len(df) - 1)
    sell_price = df.iloc[future_idx]["close"]
    return (sell_price - buy_price) / buy_price


def main():
    # 1. 获取股票列表
    stock_list = []
    try:
        print("尝试从 API 获取 A 股列表...")
        all_stocks = fetch_all_stock_codes()
        # 过滤 ST
        name_blacklist = ["ST", r"\*ST", "退市"]
        mask = ~all_stocks["name"].str.contains("|".join(name_blacklist), na=False, regex=True)
        filtered = all_stocks[mask]
        filtered = filtered[filtered["code"].str.match(r"^(60|00)\d{4}$")]
        sample = filtered.sample(n=min(100, len(filtered)), random_state=42)
        stock_list = list(zip(sample["code"], sample["name"]))
        print(f"从 API 获取到 {len(stock_list)} 只候选股票")
    except Exception as e:
        print(f"API 失败: {e}")
        print("使用备选股票列表...")
        stock_list = FALLBACK_STOCKS

    total = len(stock_list)
    print(f"共 {total} 只股票\n")

    # 2. 逐只扫描历史信号
    all_returns = []
    stats_per_stock = []

    for idx, (code, name) in enumerate(stock_list):
        df = fetch_weekly_kline(code)
        if df is None or len(df) < 80:
            print(f"[{idx + 1:3d}/{total:3d}] {code} {name} — 数据不足，跳过")
            continue

        df = calc_all_indicators(df)
        signal_indices = find_historical_signals(df)

        if not signal_indices:
            print(f"[{idx + 1:3d}/{total:3d}] {code} {name} — 无历史信号")
            continue

        returns = [compute_forward_return(df, si) for si in signal_indices]
        avg_r = np.mean(returns)
        all_returns.extend(returns)
        stats_per_stock.append({
            "code": code, "name": name,
            "signals": len(returns),
            "avg_return_20w": round(avg_r * 100, 2),
            "win_rate": round(sum(1 for r in returns if r > 0) / len(returns) * 100, 1),
        })
        print(f"[{idx + 1:3d}/{total:3d}] {code} {name} — "
              f"信号{len(returns)}次, 20周平均涨跌 {avg_r:+.2%}")
        time.sleep(0.1)

    # 3. 汇总
    print("\n" + "=" * 60)
    print("                     验证结果汇总")
    print("=" * 60)

    if not all_returns:
        print("无有效信号数据，无法统计。")
        return

    all_returns = np.array(all_returns)

    print(f"有效股票数:           {len(stats_per_stock)}")
    print(f"总信号次数:           {len(all_returns)}")
    print(f"信号后20周平均涨跌:    {np.mean(all_returns):+.2%}")
    print(f"信号后20周中位数涨跌:   {np.median(all_returns):+.2%}")
    print(f"标准差:               {np.std(all_returns):.4f}")
    print(f"最大涨幅:              {np.max(all_returns):+.2%}")
    print(f"最大跌幅:              {np.min(all_returns):+.2%}")
    print(f"上涨概率:              {np.mean(all_returns > 0):.1%}")

    print(f"\n分位统计:")
    for p in [10, 25, 50, 75, 90]:
        val = np.percentile(all_returns, p)
        print(f"  P{p}: {val:+.2%}")

    # 保存明细
    df_stats = pd.DataFrame(stats_per_stock)
    df_stats.to_csv("verify_result.csv", index=False, encoding="utf-8-sig")
    print(f"\n明细已保存至 verify_result.csv")


if __name__ == "__main__":
    main()
