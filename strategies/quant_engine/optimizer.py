"""组合优化器 — 跟踪误差约束 + 行业中性 + 换手惩罚"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


def optimize_portfolio(
    alpha_scores: pd.Series,
    benchmark_weights: pd.Series,
    cov_matrix: pd.DataFrame | None = None,
    industry: pd.Series | None = None,
    benchmark_industry: pd.Series | None = None,
    current_weights: pd.Series | None = None,
    max_weight: float = 0.05,
    max_industry_dev: float = 0.03,
    max_tracking_error: float = 0.08,
    turnover_penalty: float = 0.001,
    hold_count: int = 50,
) -> pd.Series:
    """
    指数增强组合优化

    先尝试 cvxpy 凸优化；若不可用则退化为 Alpha排序 + 约束裁剪。

    Parameters
    ----------
    alpha_scores : 综合Alpha得分 (stock_code → score)
    benchmark_weights : 基准权重 (stock_code → weight)
    cov_matrix : 收益协方差矩阵（可选，用于跟踪误差约束）
    industry : 个股行业 (stock_code → industry_name)
    benchmark_industry : 基准行业权重 (industry_name → weight)
    current_weights : 当前持仓权重（用于换手惩罚）
    max_weight : 单股最大权重
    max_industry_dev : 行业偏离上限
    max_tracking_error : 年化跟踪误差上限
    turnover_penalty : 换手惩罚系数
    hold_count : 目标持仓数

    Returns
    -------
    Series[stock_code → optimized_weight]
    """
    # 基准为等权时（如BaoStock），行业约束无意义，直接用简化优化
    bm_is_equal = benchmark_weights.std() < 1e-6 if len(benchmark_weights) > 0 else True

    if bm_is_equal:
        logger.info("基准为等权，使用Alpha排序优化（放宽行业约束）")
        return _optimize_simple(
            alpha_scores, benchmark_weights, industry=industry,
            benchmark_industry=benchmark_industry, max_weight=max_weight,
            max_industry_dev=max_industry_dev, hold_count=hold_count,
        )

    try:
        return _optimize_cvxpy(
            alpha_scores, benchmark_weights, cov_matrix,
            industry, benchmark_industry, current_weights,
            max_weight, max_industry_dev, turnover_penalty, hold_count,
        )
    except ImportError:
        logger.warning("cvxpy不可用，使用简化优化")
        return _optimize_simple(
            alpha_scores, benchmark_weights, industry,
            benchmark_industry, max_weight, max_industry_dev, hold_count,
        )
    except Exception as e:
        logger.warning(f"cvxpy优化失败({e})，退化为简化优化")
        return _optimize_simple(
            alpha_scores, benchmark_weights, industry,
            benchmark_industry, max_weight, max_industry_dev, hold_count,
        )


def _optimize_cvxpy(
    alpha_scores, benchmark_weights, cov_matrix,
    industry, benchmark_industry, current_weights,
    max_weight, max_industry_dev, turnover_penalty, hold_count,
) -> pd.Series:
    """cvxpy凸优化实现"""
    import cvxpy as cp

    # 先按alpha预选候选股（hold_count的2倍），再在子集内优化
    all_valid = alpha_scores.dropna().sort_values(ascending=False)
    candidate_count = min(len(all_valid), hold_count * 2)
    universe = all_valid.head(candidate_count).index
    n = len(universe)
    if n == 0:
        return pd.Series(dtype=float)

    alpha = alpha_scores.loc[universe].values
    bm_w = benchmark_weights.reindex(universe).fillna(0).values

    # 决策变量
    w = cp.Variable(n)

    # 目标：最大化alpha - 换手惩罚
    objective_terms = [alpha @ w]

    if current_weights is not None:
        prev_w = current_weights.reindex(universe).fillna(0).values
        objective_terms.append(-turnover_penalty * cp.norm(w - prev_w, 1))

    objective = cp.Maximize(sum(objective_terms))

    # 约束
    constraints = [
        cp.sum(w) == 1,       # 满仓
        w >= 0,                # 不做空
        w <= max_weight,       # 单股上限
    ]

    # 行业约束
    if industry is not None and benchmark_industry is not None:
        # 检测基准是否为等权（BaoStock无真实权重时自动放宽）
        bm_w_std = benchmark_weights.std()
        is_equal_weight = bm_w_std < 1e-6
        effective_dev = max_industry_dev * 3 if is_equal_weight else max_industry_dev

        ind_aligned = industry.reindex(universe).fillna("未知")
        for ind_name in benchmark_industry.index:
            mask = (ind_aligned == ind_name).values.astype(float)
            bm_ind_w = benchmark_industry.get(ind_name, 0)
            if mask.sum() > 0:
                constraints.append(mask @ w <= bm_ind_w + effective_dev)
                constraints.append(mask @ w >= max(bm_ind_w - effective_dev, 0))

    # 跟踪误差约束（若有协方差矩阵）
    if cov_matrix is not None:
        cov_aligned = cov_matrix.reindex(index=universe, columns=universe).fillna(0)
        Sigma = cov_aligned.values
        active_w = w - bm_w
        # TE² = active_w' Σ active_w
        constraints.append(cp.quad_form(active_w, Sigma) <= (0.08 ** 2))

    prob = cp.Problem(objective, constraints)
    prob.solve(solver=cp.ECOS, max_iters=500)

    if prob.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"优化未收敛: {prob.status}")

    weights = pd.Series(w.value, index=universe, name="weight")
    weights = weights.clip(lower=0)  # 数值精度修正
    weights = weights / weights.sum()

    # 只保留前hold_count个
    weights = weights.nlargest(hold_count)
    weights = weights / weights.sum()

    logger.info(f"cvxpy优化完成: {len(weights)}只股票, 最大权重{weights.max():.2%}")
    return weights


def _optimize_simple(
    alpha_scores, benchmark_weights, industry,
    benchmark_industry, max_weight, max_industry_dev, hold_count,
) -> pd.Series:
    """
    简化优化: Alpha排序选股 + 行业约束裁剪

    无需cvxpy，纯pandas实现
    """
    scores = alpha_scores.dropna().sort_values(ascending=False)

    if industry is not None and benchmark_industry is not None:
        # 按行业分配名额
        ind_aligned = industry.reindex(scores.index).fillna("未知")
        selected = []

        for ind_name, bm_w in benchmark_industry.items():
            # 行业内名额 ∝ 基准行业权重
            ind_quota = max(1, int(round(hold_count * bm_w)))
            ind_stocks = scores.loc[ind_aligned == ind_name].head(ind_quota)
            selected.append(ind_stocks)

        if selected:
            weights = pd.concat(selected).head(hold_count)
        else:
            weights = scores.head(hold_count)
    else:
        weights = scores.head(hold_count)

    # 按Alpha得分加权
    raw_weights = weights.clip(lower=0)
    if raw_weights.sum() == 0:
        raw_weights = pd.Series(1.0 / len(weights), index=weights.index)
    else:
        raw_weights = raw_weights / raw_weights.sum()

    # 裁剪单股上限
    raw_weights = raw_weights.clip(upper=max_weight)
    raw_weights = raw_weights / raw_weights.sum()

    raw_weights.name = "weight"
    logger.info(f"简化优化完成: {len(raw_weights)}只股票")
    return raw_weights


def calc_benchmark_industry_weights(
    benchmark_weights: pd.Series,
    industry: pd.Series,
) -> pd.Series:
    """计算基准的行业权重分布"""
    df = pd.DataFrame({
        "weight": benchmark_weights,
        "industry": industry,
    }).dropna()
    return df.groupby("industry")["weight"].sum()
