#!/usr/bin/env python3
"""下载中证500成分股数据（BaoStock）— 带超时和增量保存"""
import sys
import os
import time
import signal

import baostock as bs
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

POOL_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 100
START = "2022-01-01"
END = "2025-12-31"
TIMEOUT_PER_STOCK = 30  # 单只股票超时秒数


class TimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutError("超时")


def rs_to_list(rs):
    data = []
    while (rs.error_code == "0") and rs.next():
        data.append(rs.get_row_data())
    return data, rs.fields


def fetch_one_stock(bs_code, start, end):
    """获取单只股票日线，带超时保护"""
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TIMEOUT_PER_STOCK)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,volume,amount",
            start_date=start, end_date=end,
            frequency="d", adjustflag="1",
        )
        rows, fields = rs_to_list(rs)
        signal.alarm(0)
        return rows, fields
    except TimeoutError:
        signal.alarm(0)
        return None, None
    except Exception:
        signal.alarm(0)
        raise


def main():
    lg = bs.login()
    print(f"baostock login: {lg.error_msg}", flush=True)

    # 1. 成分股
    rows, fields = rs_to_list(bs.query_zz500_stocks())
    stocks_df = pd.DataFrame(rows, columns=fields)
    codes_bs = stocks_df["code"].tolist()[:POOL_SIZE]
    print(f"股票池: {len(codes_bs)} 只", flush=True)

    # 2. 日线数据（带超时+增量保存）
    all_data = []
    failed = []
    timeout_count = 0
    t0 = time.time()

    for i, bs_code in enumerate(codes_bs):
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        try:
            rows, flds = fetch_one_stock(bs_code, START, END)
            if rows is None:
                timeout_count += 1
                print(f"  超时跳过 {std_code}", flush=True)
                # 超时后重新登录
                try:
                    bs.logout()
                except Exception:
                    pass
                time.sleep(2)
                bs.login()
                continue
            if rows:
                df = pd.DataFrame(rows, columns=flds)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df["stock_code"] = std_code
                df["date"] = pd.to_datetime(df["date"])
                all_data.append(
                    df[["date", "stock_code", "open", "high", "low", "close", "volume", "amount"]]
                )
        except Exception as e:
            failed.append(std_code)
            print(f"  失败 {std_code}: {e}", flush=True)
            # 出错后重新登录
            try:
                bs.logout()
            except Exception:
                pass
            time.sleep(2)
            bs.login()

        if (i + 1) % 10 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(codes_bs) - i - 1)
            print(
                f"日线进度: {i+1}/{len(codes_bs)} 成功{len(all_data)} 失败{len(failed)} "
                f"超时{timeout_count} ({elapsed:.0f}s, ETA {eta:.0f}s)",
                flush=True,
            )
            # 增量保存
            if all_data:
                tmp = pd.concat(all_data, ignore_index=True)
                tmp.to_parquet(os.path.join(DATA_DIR, "bs_daily_20220101_20251231.parquet"), index=False)

    # 最终保存
    result = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
    result.to_parquet(os.path.join(DATA_DIR, "bs_daily_20220101_20251231.parquet"), index=False)
    print(f"日线数据: {len(result)} 行 ({len(all_data)} 只)", flush=True)

    # 3. 基准指数
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(30)
    try:
        rows, fields = rs_to_list(
            bs.query_history_k_data_plus("sh.000905", "date,close", start_date=START, end_date=END, frequency="d")
        )
        signal.alarm(0)
        bench = pd.DataFrame(rows, columns=fields)
        bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
        bench["date"] = pd.to_datetime(bench["date"])
        bench[["date", "close"]].to_parquet(
            os.path.join(DATA_DIR, "bs_index_000905.SH_20220101_20251231.parquet"), index=False
        )
        print(f"基准指数: {len(bench)} 行", flush=True)
    except TimeoutError:
        signal.alarm(0)
        print("基准指数获取超时", flush=True)

    # 4. 财务数据
    fin_data = []
    for bs_code in codes_bs:
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        signal.alarm(10)
        try:
            rows, fields = rs_to_list(bs.query_profit_data(code=bs_code, year=2024, quarter=3))
            signal.alarm(0)
            if rows:
                r = rows[-1]
                roe_i = fields.index("roeAvg") if "roeAvg" in fields else -1
                eps_i = fields.index("epsTTM") if "epsTTM" in fields else -1
                fin_data.append({
                    "stock_code": std_code,
                    "roe": float(r[roe_i]) if roe_i >= 0 and r[roe_i] else None,
                    "eps": float(r[eps_i]) if eps_i >= 0 and r[eps_i] else None,
                })
        except (TimeoutError, Exception):
            signal.alarm(0)
    fin_df = pd.DataFrame(fin_data) if fin_data else pd.DataFrame()
    fin_df.to_parquet(os.path.join(DATA_DIR, "bs_financial_20240930.parquet"), index=False)
    print(f"财务数据: {len(fin_df)} 只", flush=True)

    # 5. 行业分类
    ind_data = []
    for bs_code in codes_bs:
        std_code = f"{bs_code.split('.')[1]}.{bs_code.split('.')[0].upper()}"
        signal.alarm(10)
        try:
            rows, fields = rs_to_list(bs.query_stock_industry(code=bs_code))
            signal.alarm(0)
            ind = rows[0][fields.index("industry")] if rows and "industry" in fields else "未知"
            ind_data.append({"stock_code": std_code, "industry": ind})
        except (TimeoutError, Exception):
            signal.alarm(0)
            ind_data.append({"stock_code": std_code, "industry": "未知"})
    ind_df = pd.DataFrame(ind_data)
    ind_df.to_parquet(os.path.join(DATA_DIR, "bs_industry_20251231.parquet"), index=False)
    print(f"行业分类: {len(ind_df)} 只", flush=True)

    bs.logout()
    total = time.time() - t0
    print(f"\n全部完成! 总耗时 {total:.0f}s ({total/60:.1f}min)", flush=True)


if __name__ == "__main__":
    main()
