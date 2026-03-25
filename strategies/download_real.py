#!/usr/bin/env python3
"""下载真实BaoStock数据 — 带连接保活和超时跳过"""
import sys
import os
import time
import socket

import baostock as bs
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

POOL_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 100
START = "2022-01-01"
END = "2025-12-31"

# 设置socket超时，防止BaoStock挂起
socket.setdefaulttimeout(30)


def safe_query(query_fn, *args, **kwargs):
    """带超时保护的BaoStock查询"""
    try:
        rs = query_fn(*args, **kwargs)
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        return rows, rs.fields
    except (socket.timeout, Exception) as e:
        return None, str(e)


def main():
    lg = bs.login()
    print(f"login: {lg.error_msg}", flush=True)

    # 1. 成分股
    rows, fields = safe_query(bs.query_zz500_stocks)
    if rows is None:
        print("获取成分股失败", flush=True)
        return
    stocks = pd.DataFrame(rows, columns=fields)
    codes = stocks["code"].tolist()[:POOL_SIZE]
    print(f"股票池: {len(codes)} 只", flush=True)

    # 2. 日线数据
    all_data = []
    failed = []
    skipped = []
    t0 = time.time()

    for i, bs_code in enumerate(codes):
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        rows, fields = safe_query(
            bs.query_history_k_data_plus,
            bs_code, "date,code,open,high,low,close,volume,amount",
            start_date=START, end_date=END,
            frequency="d", adjustflag="1",
        )
        if rows is None:
            skipped.append(std_code)
            print(f"  超时跳过 {std_code}: {fields}", flush=True)
            # 重连
            try:
                bs.logout()
            except Exception:
                pass
            time.sleep(1)
            bs.login()
            continue
        if rows:
            df = pd.DataFrame(rows, columns=fields)
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["stock_code"] = std_code
            df["date"] = pd.to_datetime(df["date"])
            all_data.append(df[["date", "stock_code", "open", "high", "low", "close", "volume", "amount"]])

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(codes) - i - 1)
            print(f"进度: {i+1}/{len(codes)} 成功{len(all_data)} 跳过{len(skipped)} ({elapsed:.0f}s, ETA {eta:.0f}s)", flush=True)
            # 增量保存
            if all_data:
                pd.concat(all_data, ignore_index=True).to_parquet(
                    os.path.join(DATA_DIR, "bs_daily_20220101_20251231.parquet"), index=False
                )

    # 最终保存
    if all_data:
        result = pd.concat(all_data, ignore_index=True)
        result.to_parquet(os.path.join(DATA_DIR, "bs_daily_20220101_20251231.parquet"), index=False)
        print(f"日线: {len(result)}行, {len(all_data)}只", flush=True)
    else:
        print("无日线数据!", flush=True)
        return

    # 3. 基准指数
    rows, fields = safe_query(
        bs.query_history_k_data_plus,
        "sh.000905", "date,close", start_date=START, end_date=END, frequency="d",
    )
    if rows:
        bench = pd.DataFrame(rows, columns=fields)
        bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
        bench["date"] = pd.to_datetime(bench["date"])
        bench[["date", "close"]].to_parquet(
            os.path.join(DATA_DIR, "bs_index_000905.SH_20220101_20251231.parquet"), index=False
        )
        print(f"基准: {len(bench)}行", flush=True)

    # 4. 财务数据
    fin_data = []
    for bs_code in codes:
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        rows, fields = safe_query(bs.query_profit_data, code=bs_code, year=2024, quarter=3)
        if rows:
            r = rows[-1]
            f = fields
            fin_data.append({
                "stock_code": std_code,
                "roe": float(r[f.index("roeAvg")]) if "roeAvg" in f and r[f.index("roeAvg")] else None,
                "eps": float(r[f.index("epsTTM")]) if "epsTTM" in f and r[f.index("epsTTM")] else None,
            })
    if fin_data:
        pd.DataFrame(fin_data).to_parquet(os.path.join(DATA_DIR, "bs_financial_20240930.parquet"), index=False)
    print(f"财务: {len(fin_data)}只", flush=True)

    # 5. 行业
    ind_data = []
    for bs_code in codes:
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        rows, fields = safe_query(bs.query_stock_industry, code=bs_code)
        if rows and "industry" in fields:
            ind = rows[0][fields.index("industry")]
            ind_data.append({"stock_code": std_code, "industry": ind})
        else:
            ind_data.append({"stock_code": std_code, "industry": "未知"})
    pd.DataFrame(ind_data).to_parquet(os.path.join(DATA_DIR, "bs_industry_20251231.parquet"), index=False)
    print(f"行业: {len(ind_data)}只", flush=True)

    bs.logout()
    total = time.time() - t0
    print(f"\n完成! 总耗时 {total:.0f}s ({total/60:.1f}min)", flush=True)


if __name__ == "__main__":
    main()
