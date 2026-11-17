# -*- coding: utf-8 -*-
"""
spc_core.py
===========
관리도 & 공정능력 핵심 계산 로직 (GUI 의존 없음)
원본: 공정능력_관리도_Rev26.py 에서 추출
"""

import math
import re
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from scipy import stats
from spc_policy import normalize_assessment, finalize_assessment, exclusion_digest, approved_mask, band, limits
from typing import Optional, Tuple, Dict, List, Any

# ===== 관리계수 표 (n: 2~25) =====
_A2 = {
    2: 1.880, 3: 1.023, 4: 0.729, 5: 0.577, 6: 0.483, 7: 0.419, 8: 0.373,
    9: 0.337, 10: 0.308, 11: 0.285, 12: 0.266, 13: 0.249, 14: 0.235, 15: 0.223,
    16: 0.212, 17: 0.203, 18: 0.194, 19: 0.187, 20: 0.180, 21: 0.173, 22: 0.167,
    23: 0.162, 24: 0.157, 25: 0.153
}
_D3 = {
    2: 0.000, 3: 0.000, 4: 0.000, 5: 0.000, 6: 0.000, 7: 0.076, 8: 0.136,
    9: 0.184, 10: 0.223, 11: 0.256, 12: 0.283, 13: 0.307, 14: 0.328, 15: 0.347,
    16: 0.363, 17: 0.378, 18: 0.391, 19: 0.403, 20: 0.415, 21: 0.425, 22: 0.434,
    23: 0.443, 24: 0.451, 25: 0.459
}
_D4 = {
    2: 3.267, 3: 2.575, 4: 2.282, 5: 2.114, 6: 2.004, 7: 1.924, 8: 1.864,
    9: 1.816, 10: 1.777, 11: 1.744, 12: 1.717, 13: 1.693, 14: 1.672, 15: 1.653,
    16: 1.637, 17: 1.622, 18: 1.608, 19: 1.597, 20: 1.585, 21: 1.575, 22: 1.566,
    23: 1.557, 24: 1.548, 25: 1.541
}
_d2 = {
    2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847,
    9: 2.970, 10: 3.078, 11: 3.173, 12: 3.258, 13: 3.336, 14: 3.407, 15: 3.472,
    16: 3.532, 17: 3.588, 18: 3.640, 19: 3.689, 20: 3.735, 21: 3.778, 22: 3.819,
    23: 3.858, 24: 3.895, 25: 3.931
}
_d2_mr = 1.128  # I-MR에서 MRbar→σ 변환

NORMALITY_MAX = 10000
NORMALITY_SW_CUTOFF = 50  # n<=50: Shapiro-Wilk, 초과: Anderson-Darling

CALCULATION_VERSION = "2026.11.17.4"
WITHIN_CAPABILITY_NOTICE = (
    "Cw·Cwk는 군내 변동에 기반한 공정 진단 지수입니다. "
    "이 지수만으로 공정 전체의 능력 충족을 판정하거나 입증하지 않습니다. "
    "합리적 부분군과 군내 정규성을 가정합니다. Cp·Cpk와 Pp·Ppk는 전체 변동을 사용하며, "
    "Cp·Cpk는 관측 구간의 적용 조건이 충족될 때만 표시합니다. "
    "Cwk/Ppk 비율만으로 공정 안정성을 확정할 수 없습니다."
)
OUTLIER_METHOD_IQR = "iqr"
OUTLIER_METHOD_QUANTILE = "quantile"


def normalize_outlier_method(method: Any) -> str:
    """이상치 기준 이름을 계산에 쓰는 값으로 맞춘다. 알 수 없는 값은 IQR이다."""
    key = str(method or OUTLIER_METHOD_IQR).strip().lower()
    if key in (OUTLIER_METHOD_QUANTILE, "quantile_range", "percentile"):
        return OUTLIER_METHOD_QUANTILE
    return OUTLIER_METHOD_IQR


def _outlier_fences(values: np.ndarray, method: str) -> Optional[Dict[str, float]]:
    """기준 데이터로 이상치 울타리를 만든다. 표본이 부족하면 None."""
    method = normalize_outlier_method(method)
    if method == OUTLIER_METHOD_QUANTILE:
        if len(values) < 11:
            return None
        p10, p90 = np.percentile(values, [10, 90])
        width = p90 - p10
        return {
            "p10": float(p10),
            "p90": float(p90),
            "width": float(width),
            "lower": float(p10 - 3.0 * width),
            "upper": float(p90 + 3.0 * width),
        }
    if len(values) <= 4:
        return None
    q1, q3 = np.percentile(values, [25, 75])
    iqr = q3 - q1
    return {
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(iqr),
        "lower": float(q1 - 1.5 * iqr),
        "upper": float(q3 + 1.5 * iqr),
    }


def analysis_options(state) -> Dict[str, Any]:
    """화면 분석과 즐겨찾기 일괄 분석에 동일한 설정을 전달한다."""
    defaults = dict(n=5, include_incomplete=False, data_layout_mode="long",
                    subgroup_columns=[], subgroup_var_name="측정치", measurement_date_column="",
                    ksq_toggles=None, chart_usage_mode="phase1", baseline_subgroups=25,
                    preprocess_outliers=False, box_cox_transform=False)
    options = {key: state.get(key, default) for key, default in defaults.items()}
    options["outlier_method"] = normalize_outlier_method(state.get("outlier_method", OUTLIER_METHOD_IQR))
    options["distribution_settings"] = state.get("distribution_settings", {})
    options["manual_target_map"] = state.get("manual_target_map", {}) if state.get("manual_target_enabled", False) else None
    options["assessment_settings"] = state.get("assessment_settings", {})
    return options


def assess_process(it: Dict[str, Any]) -> Dict[str, str]:
    """관측된 이탈, 안정성, 계산 신뢰도를 능력 지수보다 먼저 판단한다."""
    if it.get("reliability_status") == "계산불가":
        return dict(level="unknown", text="⬜ 계산 조건 확인 필요", action="표본수·분산·규격과 오류 사유를 확인하세요.")
    if it.get("spec_violation_count", 0) > 0:
        return dict(level="high", text="🔴 원본 규격이탈 확인", action="원본 이탈 측정값과 해당 Lot를 확인하세요. 전처리 후 능력 지수가 높아도 이탈은 유지됩니다.")
    unstable = (it.get("x_ctrl_violation_count", 0) + it.get("r_ctrl_violation_count", 0) > 0
                or any(it.get("ksq_counts", {}).values()) or it.get("baseline_status") == "부적합"
                or it.get("sequence_review", {}).get("count", 0) > 0)
    if unstable:
        return dict(level="high", text="🔴 공정 안정성 검토 필요", action="관리이탈·규칙 위반의 발생 시점과 작업 조건을 확인하세요. 능력 지수만으로 정상 운영을 판정하지 않습니다.")
    if it.get("reliability_status") in ("검토필요", "주의"):
        return dict(level="medium", text="🟡 분석 조건 검토 필요", action="정규성·규격·전처리 및 제외 데이터 사유를 먼저 확인하세요.")
    if it.get("capability_status") and it.get("capability_status") != "적용 가능":
        return dict(level="medium", text="🟡 공정능력 적용 조건 확인", action="; ".join(it.get("capability_reasons", [])))
    cpk = it.get("cpk", np.nan)
    if not np.isfinite(cpk):
        return dict(level="unknown", text="⬜ 능력 판정 불가", action="규격과 유효 표본을 확인하세요.")
    warning, required = limits(it)
    if cpk < warning:
        return dict(level="high", text="🔴 공정능력 부족", action="중심 치우침과 산포를 검토하세요. 예상 능력 부족은 실제 규격이탈 건수와 구분합니다.")
    if cpk < required:
        return dict(level="medium", text="🟡 공정능력 주의", action="변동과 중심 이동을 추적하고 공정별 요구 기준을 확인하세요.")
    return dict(level="low", text="🟢 관측 구간 양호", action="관측 구간에서 뚜렷한 이상 신호가 없습니다. 공정별 요구 능력 기준을 확인하고 감시를 유지하세요.")


def _input_digest(source):
    if source is None:
        return None
    if isinstance(source, (str, Path)):
        payload = Path(source).read_bytes()
    elif isinstance(source, (bytes, bytearray)):
        payload = bytes(source)
    else:
        position = source.tell()
        source.seek(0)
        try:
            payload = source.read()
        finally:
            source.seek(position)
    return hashlib.sha256(payload).hexdigest()


def get_control_constants(n: int) -> Tuple[float, float, float, float]:
    """n에 대한 관리계수 (A2, D3, D4, d2) 반환. 범위 밖이면 nan."""
    A2 = _A2.get(n, np.nan)
    D3 = _D3.get(n, np.nan)
    D4 = _D4.get(n, np.nan)
    d2 = _d2.get(n, np.nan)
    return A2, D3, D4, d2


def get_s_chart_constants(n: int) -> Tuple[float, float, float, float]:
    """
    n에 대한 Xbar-S 관리계수 (A3, B3, B4, c4) 반환.
    - c4 = E[s]/sigma (정규분포 가정)
    - A3 = 3 / (c4 * sqrt(n))
    - B3 = max(0, 1 - (3/c4)*sqrt(1-c4^2))
    - B4 = 1 + (3/c4)*sqrt(1-c4^2)
    """
    if n < 2:
        return np.nan, np.nan, np.nan, np.nan
    try:
        # 수치안정성을 위해 lgamma 사용
        c4 = math.sqrt(2.0 / (n - 1.0)) * math.exp(
            math.lgamma(n / 2.0) - math.lgamma((n - 1.0) / 2.0)
        )
        if not np.isfinite(c4) or c4 <= 0:
            return np.nan, np.nan, np.nan, np.nan
        k = (3.0 / c4) * math.sqrt(max(0.0, 1.0 - c4 * c4))
        A3 = 3.0 / (c4 * math.sqrt(n))
        B3 = max(0.0, 1.0 - k)
        B4 = 1.0 + k
        return float(A3), float(B3), float(B4), float(c4)
    except Exception:
        return np.nan, np.nan, np.nan, np.nan


def _ad_pvalue_from_A2(A2: float, n: Optional[int] = None) -> float:
    """Anderson-Darling A2 통계량으로 근사 p-value 계산 (Stephens/Thode 스타일)."""
    if n is not None:
        A2 *= 1.0 + 0.75/n + 2.25/(n*n)
    if A2 < 0.2:
        p = 1.0 - math.exp(-13.436 + 101.14 * A2 - 223.73 * (A2 ** 2))
    elif A2 < 0.34:
        p = 1.0 - math.exp(-8.318 + 42.796 * A2 - 59.938 * (A2 ** 2))
    elif A2 < 0.6:
        p = math.exp(0.9177 - 4.279 * A2 - 1.38 * (A2 ** 2))
    elif A2 < 10:
        p = math.exp(1.2937 - 5.709 * A2 + 0.0186 * (A2 ** 2))
    else:
        p = 0.0
    return float(max(0.0, min(1.0, p)))


def _normality_sample(arr: np.ndarray, limit: int = NORMALITY_MAX) -> np.ndarray:
    """Reproducible sample from the whole observation interval, not its prefix."""
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size > limit:
        indices = np.random.default_rng(20261117).choice(arr.size, limit, replace=False)
        arr = arr[np.sort(indices)]
    return arr


def normality_message(arr: np.ndarray, alpha: float = 0.05) -> str:
    """
    정규성 검정 결과 문자열 반환.
    - n <= 50: Shapiro-Wilk
    - n > 50: Anderson-Darling (p 근사)
    """
    arr = np.asarray(arr, dtype=float)
    total_n = int(np.isfinite(arr).sum())
    arr = _normality_sample(arr)
    n = arr.size
    sample_note = f" · 검정 표본 {n:,}/전체 {total_n:,}" if n < total_n else ""
    if n < 3:
        return f"정규성 검정 불가 (유효 표본수 {n} < 3)"
    if np.nanstd(arr) == 0.0:
        return "정규성 검정 불가 (모든 값이 동일: 표준편차=0)"

    if n <= NORMALITY_SW_CUTOFF:
        try:
            _, p = stats.shapiro(arr)
            return f"P-값 = {p:.3f} ({'만족' if p >= alpha else '의심'}) [Shapiro-Wilk]"
        except Exception as e:
            return f"정규성 검정 불가 (예외: {e.__class__.__name__})"

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ad = stats.anderson(arr, dist='norm')
        p = _ad_pvalue_from_A2(float(ad.statistic), n)
        return f"P-값 = {p:.3f} ({'만족' if p >= alpha else '의심'}) [Anderson-Darling]{sample_note}"
    except Exception:
        try:
            sample = _normality_sample(arr, 5000)
            _, p = stats.shapiro(sample)
            return (f"P-값 = {p:.3f} ({'만족' if p >= alpha else '의심'}) [Shapiro-Wilk fallback]"
                    + (f" · 검정 표본 {len(sample):,}/전체 {total_n:,}" if len(sample) < total_n else ""))
        except Exception as e2:
            return f"정규성 검정 불가 (예외: {e2.__class__.__name__})"


def qq_stats_text(arr: np.ndarray) -> str:
    """QQ플롯 하단에 표시할 왜도/첨도/SW/AD p-값 문자열."""
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return "데이터 없음"

    skew = float(stats.skew(a, nan_policy='omit', bias=False))
    kurt_ex = float(stats.kurtosis(a, nan_policy='omit', fisher=True, bias=False))

    sw_txt = "—"
    try:
        if 3 <= a.size <= 5000:
            _, p_sw = stats.shapiro(a)
            sw_txt = f"{p_sw:.3f}"
    except Exception:
        pass

    ad_txt = "—"
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ad = stats.anderson(a, dist='norm')
        ad_txt = f"{_ad_pvalue_from_A2(float(ad.statistic), a.size):.3f}"
    except Exception:
        pass

    return (f"왜도 {skew:.3f}   ·   첨도 {kurt_ex:.3f}\n"
            f"Shapiro-Wilk p {sw_txt}   ·   Anderson-Darling p {ad_txt}")


def detect_ksq_rules(
    means: np.ndarray,
    cl: float,
    A2: float,
    r_bar: float,
    is_xmr: bool = False,
    sigma_override: Optional[float] = None,
    ucl: Optional[float] = None,
    lcl: Optional[float] = None,
    t1_by_manual: bool = False
) -> Dict[str, Any]:
    """
    KS Q ISO 7870-2 T1~T8 규칙 검출.
    반환: {'counts': {T1..T8: int}, 'idx': {T1..T8: set}}
    """
    vals = np.asarray(means, dtype=float)
    idxs = {f'T{i}': set() for i in range(1, 9)}
    counts = {f'T{i}': 0 for i in range(1, 9)}

    if sigma_override is not None and np.isfinite(sigma_override) and sigma_override > 0:
        sigma = float(sigma_override)
    else:
        if is_xmr:
            sigma = (r_bar / _d2_mr) if (np.isfinite(r_bar) and r_bar > 0) else np.nan
        else:
            sigma = (A2 * r_bar) / 3.0 if (np.isfinite(A2) and np.isfinite(r_bar)) else np.nan

    if not np.isfinite(sigma) or sigma <= 0 or len(vals) < 2:
        return {'counts': counts, 'idx': idxs}

    dev = vals - cl
    eps = sigma * 1e-6
    dev_adj = np.where(np.abs(dev) < eps, 0.0, dev)
    sgn = np.sign(dev_adj)

    # T1: 3σ 초과
    if t1_by_manual and ucl is not None and lcl is not None and np.isfinite(ucl) and np.isfinite(lcl):
        t1 = np.where((vals > ucl) | (vals < lcl))[0]
    else:
        t1 = np.where(np.abs(dev) > 3 * sigma)[0]
    idxs['T1'].update(t1.tolist())

    # Rolling counts and interval unions avoid Python loops over every window.
    def rolling(mask, width):
        sums = np.r_[0, np.cumsum(mask, dtype=np.int64)]
        return sums[width:] - sums[:-width]

    def covered(starts, width):
        delta = np.zeros(len(vals) + 1, dtype=np.int32)
        np.add.at(delta, starts, 1)
        np.add.at(delta, starts + width, -1)
        return np.cumsum(delta[:-1]) > 0

    def mark(rule, hits, width, matching=None):
        mask = covered(np.flatnonzero(hits), width)
        if matching is not None:
            mask &= matching
        idxs[rule].update(np.flatnonzero(mask).tolist())

    if len(vals) >= 9:
        mark('T2', (rolling(sgn > 0, 9) == 9) | (rolling(sgn < 0, 9) == 9), 9)
    diffs = np.diff(vals)
    if len(vals) >= 6:
        mark('T3', (rolling(diffs > 0, 5) == 5) | (rolling(diffs < 0, 5) == 5), 6)
    if len(vals) >= 14:
        mark('T4', rolling(diffs[:-1] * diffs[1:] < 0, 12) == 12, 14)
    for rule, width, required, zone in [('T5', 3, 2, 2), ('T6', 5, 4, 1)]:
        if len(vals) >= width:
            for side in (sgn > 0, sgn < 0):
                matching = (np.abs(dev) > zone * sigma) & side
                mark(rule, rolling(matching, width) >= required, width, matching)
    if len(vals) >= 15:
        mark('T7', rolling(np.abs(dev) < sigma, 15) == 15, 15)
    if len(vals) >= 8:
        hits = ((rolling(np.abs(dev) > sigma, 8) == 8)
                & (rolling(dev > 0, 8) > 0) & (rolling(dev < 0, 8) > 0))
        mark('T8', hits, 8)

    for k in idxs:
        counts[k] = len(idxs[k])
    return {'counts': counts, 'idx': idxs}


def fmt_value(v, decimals: int = 3, comma: bool = False) -> str:
    """숫자 포맷팅 (nan/inf → '—')."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    try:
        if comma:
            return f"{v:,.{decimals}f}"
        return f"{v:.{decimals}f}"
    except Exception:
        return str(v)


def fmt_ppm(v) -> str:
    """Display only: do not round the probability used by indices or APIs."""
    try:
        value = float(str(v).replace(',', ''))
    except (ValueError, TypeError):
        return '—'
    if not np.isfinite(value):
        return '—'
    return '0' if value < .001 else f'{value:,.3f}'


def fmt_spec(v) -> str:
    """규격값 포맷팅."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "없음"
    return f"{v:.6g}"


def safe_div(a: float, b: float) -> float:
    """0으로 나누기 안전 처리."""
    if np.isfinite(a) and np.isfinite(b) and b != 0:
        return a / b
    return np.nan


def _boxcox_transform_value(v: float, shift: float, lmbda: float) -> float:
    """Box-Cox와 같은 shift/lambda로 단일 값을 변환한다."""
    if not np.isfinite(v):
        return np.nan
    shifted = float(v) + float(shift)
    if shifted <= 0:
        return np.nan
    if abs(float(lmbda)) < 1e-12:
        return float(math.log(shifted))
    return float((shifted ** float(lmbda) - 1.0) / float(lmbda))


def _build_reliability(status_inputs: Dict[str, Any]) -> Tuple[str, List[str]]:
    """계산 결과를 과신하지 않도록 간단한 신뢰도 상태/사유를 만든다."""
    reasons: List[str] = []
    fatal = False

    if int(status_inputs.get("analysis_n", 0)) < int(status_inputs.get("min_len", 1)):
        reasons.append("N부족")
        fatal = True
    if bool(status_inputs.get("variance_zero", False)):
        reasons.append("분산0")
        fatal = True
    if bool(status_inputs.get("capability_unavailable", False)):
        reasons.append("계산불가")
        fatal = True

    if not bool(status_inputs.get("has_spec", False)):
        reasons.append("규격없음")
    if bool(status_inputs.get("normality_suspect", False)):
        reasons.append("비정규의심")
    if bool(status_inputs.get("baseline_unstable", False)):
        reasons.append("기준구간불안정")
    if bool(status_inputs.get("process_unstable", False)):
        reasons.append("공정불안정")
    if bool(status_inputs.get("missing_groups", False)):
        reasons.append("결측부분군제외")
    if bool(status_inputs.get("preprocess_applied", False)):
        reasons.append("전처리적용")

    reasons = list(dict.fromkeys(reasons))
    if fatal:
        return "계산불가", reasons
    if any(reason in reasons for reason in ("기준구간불안정", "공정불안정", "비정규의심")):
        return "검토필요", reasons
    if reasons:
        return "주의", reasons
    return "정상", []


def ppm_by_sigma(mu: float, sig: float, usl: float, lsl: float) -> float:
    """정규분포 기반 PPM 계산."""
    if not (np.isfinite(usl) or np.isfinite(lsl)):
        return np.nan
    if not (np.isfinite(sig) and sig > 0 and np.isfinite(mu)):
        return np.nan
    p_lo = stats.norm.cdf((lsl - mu) / sig) if np.isfinite(lsl) else 0.0
    p_hi = stats.norm.sf((usl - mu) / sig) if np.isfinite(usl) else 0.0
    return (p_lo + p_hi) * 1e6


def _build_subgroup_series(
    series_all: np.ndarray,
    n: int,
    include_incomplete: bool,
    use_s_chart: bool,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """부분군 평균/산포 시퀀스와 완전 부분군 정보를 생성한다."""
    total = len(series_all)
    m, r = divmod(total, n)
    if m == 0:
        raise ValueError("완전한 부분군이 부족합니다.")

    x_complete = series_all[:m * n]
    groups = x_complete.reshape(m, n)
    means = groups.mean(axis=1)

    if use_s_chart:
        ranges = groups.std(axis=1, ddof=1)
    else:
        ranges = groups.max(axis=1) - groups.min(axis=1)

    if include_incomplete and r > 0:
        tail = series_all[m * n:]
        tail_mean = float(np.mean(tail))
        if use_s_chart:
            tail_range = float(np.std(tail, ddof=1)) if len(tail) > 1 else np.nan
        else:
            tail_range = float(np.max(tail) - np.min(tail))
        means = np.concatenate([means, [tail_mean]])
        ranges = np.concatenate([ranges, [tail_range]])

    return means, ranges, groups, m, r


def _format_timestamp_label(ts: pd.Timestamp) -> str:
    """Timestamp를 관리도 x축 표시용 문자열로 변환한다."""
    if ts.time() == pd.Timestamp(ts.date()).time():
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d %H:%M")


def _try_parse_ymd_integer(num: float) -> Optional[pd.Timestamp]:
    """YYYYMMDD 정수(예: 20240615)를 날짜로 파싱한다."""
    if not np.isfinite(num) or num != int(num):
        return None
    iv = int(num)
    if not (19000101 <= iv <= 29991231):
        return None
    ts = pd.to_datetime(str(iv), format="%Y%m%d", errors="coerce")
    return ts if pd.notna(ts) else None


def _try_parse_excel_serial(num: float) -> Optional[pd.Timestamp]:
    """Excel 일련번호(예: 45321)를 날짜로 파싱한다."""
    if not np.isfinite(num) or not (20000 <= num <= 80000):
        return None
    ts = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    return ts if pd.notna(ts) else None


def _try_parse_date_string(text: str) -> Optional[pd.Timestamp]:
    """날짜 형식 문자열을 파싱한다. 순수 정수 문자열은 날짜로 보지 않는다."""
    s = str(text).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        ts = _try_parse_ymd_integer(float(n))
        if ts is not None:
            return ts
        ts = _try_parse_excel_serial(float(n))
        if ts is not None:
            return ts
        return None
    if not re.search(r"[-/.:T]", s):
        return None
    ts = pd.to_datetime(s, errors="coerce")
    return ts if pd.notna(ts) else None


def _format_numeric_label(num: float, raw_text: str) -> str:
    """날짜가 아닌 숫자 값을 x축 라벨 문자열로 변환한다."""
    if np.isfinite(num) and num == int(num):
        return str(int(num))
    return str(raw_text).strip()


def _format_axis_labels_from_series(values: pd.Series) -> List[str]:
    """측정일자 컬럼 값을 관리도 x축 라벨로 변환한다. 날짜는 날짜로, 숫자는 숫자로 표시."""
    raw = values.copy()
    # Parse uniform ISO dates in one call instead of invoking pandas once per row.
    if not pd.api.types.is_numeric_dtype(raw) and not pd.api.types.is_datetime64_any_dtype(raw):
        text = raw.astype('string')
        iso = text.str.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?')
        if bool(iso.fillna(True).all()):
            parsed = pd.to_datetime(text, format='ISO8601', errors='coerce')
            if bool((parsed.notna() | raw.isna()).all()):
                dates = parsed.dt.strftime('%Y-%m-%d')
                times = parsed.dt.strftime('%Y-%m-%d %H:%M')
                return dates.where(parsed == parsed.dt.normalize(), times).fillna('').tolist()
    if pd.api.types.is_datetime64_any_dtype(raw):
        return [
            "" if pd.isna(ts) else _format_timestamp_label(ts)
            for ts in raw
        ]

    numeric = pd.to_numeric(raw, errors="coerce")
    labels: List[str] = []
    for i, val in enumerate(raw):
        if pd.isna(val):
            labels.append("")
            continue

        text = str(val).strip()
        num = numeric.iloc[i] if i < len(numeric) else np.nan

        if pd.notna(num):
            ts = _try_parse_ymd_integer(float(num))
            if ts is not None:
                labels.append(_format_timestamp_label(ts))
                continue
            ts = _try_parse_excel_serial(float(num))
            if ts is not None:
                labels.append(_format_timestamp_label(ts))
                continue
            labels.append(_format_numeric_label(float(num), text))
            continue

        ts = _try_parse_date_string(text)
        if ts is not None:
            labels.append(_format_timestamp_label(ts))
            continue
        labels.append(text)
    return labels


def _build_chart_date_labels(
    labels: Optional[List[str]],
    n: int,
    is_xmr: bool,
    mean_count: int,
    range_count: int,
    include_incomplete: bool,
) -> Tuple[List[str], List[str], str]:
    """측정일자 라벨을 관리도 점 개수에 맞게 축약한다."""
    if not labels:
        return [], [], ""
    clean = [str(v).strip() for v in labels]
    if len(clean) == 0:
        return [], [], ""
    if is_xmr:
        x_labels = clean[:mean_count]
        r_labels = clean[1:1 + range_count]
    else:
        complete_count = len(clean) // max(1, n)
        x_labels = [clean[i * n] for i in range(min(complete_count, mean_count))]
        if include_incomplete and len(x_labels) < mean_count:
            tail_start = complete_count * n
            if tail_start < len(clean):
                x_labels.append(clean[tail_start])
        x_labels = x_labels[:mean_count]
        r_labels = x_labels[:range_count]
    if len(x_labels) != mean_count:
        x_labels = []
    if len(r_labels) != range_count:
        r_labels = []
    return x_labels, r_labels, "측정일자" if (x_labels or r_labels) else ""


def _segment_violation_counts(values: np.ndarray, ucl: float, lcl: float, baseline_count: int) -> Dict[str, int]:
    """전체/기준/감시 구간의 관리이탈 건수를 분리 집계한다."""
    vals = np.asarray(values, dtype=float)
    split = max(0, min(int(baseline_count), len(vals)))

    def _counts(arr: np.ndarray) -> Tuple[int, int, int]:
        u_cnt = int(np.count_nonzero(arr > ucl)) if np.isfinite(ucl) else 0
        l_cnt = int(np.count_nonzero(arr < lcl)) if np.isfinite(lcl) else 0
        return u_cnt, l_cnt, int(u_cnt + l_cnt)

    u_all, l_all, t_all = _counts(vals)
    u_base, l_base, t_base = _counts(vals[:split])
    u_mon, l_mon, t_mon = _counts(vals[split:])

    return {
        'ucl_total': u_all,
        'lcl_total': l_all,
        'total': t_all,
        'ucl_baseline': u_base,
        'lcl_baseline': l_base,
        'baseline': t_base,
        'ucl_monitor': u_mon,
        'lcl_monitor': l_mon,
        'monitor': t_mon,
    }


def _split_ksq_counts(ksq_idx: Dict[str, set], baseline_count: int) -> Tuple[Dict[str, int], Dict[str, int]]:
    """KSQ 위반 인덱스를 기준/감시 구간으로 분리 집계한다."""
    split = max(0, int(baseline_count))
    baseline_counts: Dict[str, int] = {}
    monitor_counts: Dict[str, int] = {}
    for i in range(1, 9):
        key = f'T{i}'
        idxs = set(ksq_idx.get(key, set()) or set())
        baseline_counts[key] = int(sum(1 for idx in idxs if idx < split))
        monitor_counts[key] = int(sum(1 for idx in idxs if idx >= split))
    return baseline_counts, monitor_counts


def analyze_column(
    series_all: np.ndarray,
    col: str,
    n: int,
    usl: float = np.nan,
    lsl: float = np.nan,
    target: float = np.nan,
    include_incomplete: bool = False,
    ksq_toggles: Optional[Dict[str, bool]] = None,
    chart_usage_mode: str = "phase1",
    baseline_subgroups: int = 25,
    preprocess_outliers: bool = False,
    box_cox_transform: bool = False,
    sample_labels: Optional[List[str]] = None,
    outlier_method: str = OUTLIER_METHOD_IQR,
    detail_level: str = "full",
    distribution_config: Optional[Dict[str, Any]] = None,
    assessment_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    단일 컬럼에 대한 SPC + 공정능력 전체 분석.
    - preprocess_outliers: 이상치 포함 부분군 제외 (실측 불량은 원본 기준)
    - outlier_method: iqr 또는 quantile. 관리용 모드는 기준구간에서만 울타리를 추정
    - box_cox_transform: 관리용 모드에서는 원본 기준구간으로만 변환 추정
    - include_incomplete: 기존 호출 호환용. 불완전 부분군은 항상 분석에서 제외
    """
    assessment_settings = normalize_assessment(assessment_settings)
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 1:
        raise ValueError("시료 크기 n은 1 이상의 정수여야 합니다.")
    if detail_level not in ('core', 'full'):
        raise ValueError('detail_level은 core 또는 full이어야 합니다.')
    from spc_nonnormal import validate_settings
    distribution_config = validate_settings(distribution_config or {})
    if distribution_config['mode'] == 'nonnormal' and (preprocess_outliers or box_cox_transform):
        raise ValueError('비정규 모드에서는 자동 이상치 제거·Box-Cox를 끄고 다시 분석하세요.')
    if np.isfinite(usl) and np.isfinite(lsl) and usl <= lsl:
        raise ValueError("규격 상한 USL은 규격 하한 LSL보다 커야 합니다.")
    if any(np.isinf(v) for v in (usl, lsl, target)):
        raise ValueError("규격/목표치는 유한한 숫자 또는 빈 값이어야 합니다.")
    # ── 데이터 전처리 (원본/분석용 분리) ──
    raw_series = np.array(series_all, dtype=float, copy=True)
    label_values: Optional[np.ndarray] = None
    if sample_labels is not None and len(sample_labels) == len(raw_series):
        label_values = np.asarray([str(v).strip() for v in sample_labels], dtype=object)

    finite_mask_initial = np.isfinite(raw_series)
    original_series = raw_series[finite_mask_initial]
    control_phase = "phase2" if str(chart_usage_mode).lower() == "phase2" else "phase1"
    baseline_requested_count = max(3, int(baseline_subgroups))
    baseline_end = baseline_requested_count * n
    if control_phase == "phase2" and len(raw_series) < baseline_end:
        raise ValueError(f"기준구간 데이터가 부족합니다. 원본 기준 {baseline_requested_count}개 부분군/샘플이 필요합니다.")

    # 부분군 경계를 먼저 확정한다. 한 값이 누락된 부분군은 통째로 제외한다.
    def complete_mask(mask):
        if n == 1:
            return mask.copy()
        count, tail = divmod(len(mask), n)
        kept = np.zeros(len(mask), dtype=bool)
        if count:
            kept[:count * n] = np.repeat(mask[:count * n].reshape(count, n).all(axis=1), n)
        return kept

    eligible_mask = complete_mask(finite_mask_initial)
    source_indices = np.flatnonzero(eligible_mask)
    working_labels = label_values[eligible_mask] if label_values is not None else None
    series_all = raw_series[eligible_mask].copy()
    reference_mask = source_indices < baseline_end if control_phase == "phase2" else np.ones(len(series_all), dtype=bool)
    x_working = series_all[reference_mask]

    original_usl = usl
    original_lsl = lsl
    original_target = target
    original_mu = float(np.mean(original_series)) if len(original_series) else np.nan
    original_std_all = float(np.std(original_series, ddof=1)) if len(original_series) > 1 else np.nan

    pre_msg = ""
    excluded_missing_groups = 0
    if n > 1:
        full_count = len(raw_series) // n
        excluded_missing_groups = full_count - int(eligible_mask.sum()) // n
        if excluded_missing_groups:
            pre_msg += f"결측/비유한 값 포함 부분군 {excluded_missing_groups}개 제외(원래 부분군 유지). "
        if len(raw_series) % n:
            pre_msg += f"마지막 불완전 부분군 {len(raw_series) % n}개 측정값은 관리도/능력 계산에서 제외, 원본 규격이탈에는 포함. "
    method_key = normalize_outlier_method(outlier_method)
    outlier_info: Dict[str, Any] = {
        "enabled": bool(preprocess_outliers),
        "method": method_key if preprocess_outliers else "",
        "lower": np.nan,
        "upper": np.nan,
        "count": 0,
        "items": [],
    }
    if preprocess_outliers:
        fences = _outlier_fences(x_working, method_key)
        if fences is None:
            if method_key == OUTLIER_METHOD_QUANTILE and len(x_working) < 11:
                pre_msg += "분위수 범위 이상치 기준은 기준 데이터 11개 이상일 때만 적용합니다. "
        else:
            outlier_info.update(fences)
            lower = fences["lower"]
            upper = fences["upper"]
            # 이상치를 nan으로 표시한 뒤 전처리 마지막에 유효값만 남긴다.
            out_mask = np.isfinite(series_all) & ((series_all < lower) | (series_all > upper))
            outlier_info["items"] = [
                {"index": int(source_indices[i] + 1), "value": float(v)}
                for i, v in enumerate(series_all)
                if bool(out_mask[i])
            ]
            # 다열/세로형 모두 해당 부분군 전체를 제외하여 이웃 부분군과 섞이지 않게 한다.
            outlier_info['candidate_count'] = int(np.sum(out_mask))
            outlier_info['digest'] = exclusion_digest(raw_series, n, method_key, control_phase, baseline_requested_count)
            approval = assessment_settings['outlier_approvals'].get(str(col))
            accepted = approved_mask(outlier_info, approval, source_indices)
            outlier_info['reviewed'] = bool(approval) and not outlier_info.get('approval_expired', False)
            outlier_info['approval'] = approval
            outlier_info['approved_indices'] = (source_indices[accepted]+1).tolist()
            outlier_info['candidate_items'] = list(outlier_info['items'])
            outlier_info['items'] = [item for item in outlier_info['items'] if item['index'] in outlier_info['approved_indices']]
            out_mask = accepted
            out_mask_groups = ~complete_mask(~out_mask)
            series_all[out_mask_groups] = np.nan
            out_cnt = int(np.sum(out_mask))
            outlier_info["count"] = out_cnt
            if out_cnt > 0:
                pre_msg += f"이상치 {out_cnt}개 검출, 분석에서 {int(out_mask_groups.sum())}개 측정값 제외. "
                x_working = series_all[reference_mask & np.isfinite(series_all)]

    # ── [추가] Box-Cox 변환 ──
    lambda_val = np.nan
    box_cox_shift = 0.0
    box_cox_applied = False
    box_cox_spec_ok = True
    capability_unavailable = False
    box_cox_msg = ""
    if box_cox_transform and len(x_working) <= 5:
        raise ValueError("Box-Cox 추정에 사용할 유효 기준 데이터가 6개 미만입니다.")
    if box_cox_transform:
        # 모든 데이터가 양수여야 함. 아니면 최소값+1 만큼 쉬프트
        shift = 0
        min_v = np.min(x_working)
        if min_v <= 0:
            shift = abs(min_v) + 1.0
        
        try:
            # 변환 수행 (scipy.stats.boxcox)
            x_trans, lmbda = stats.boxcox(x_working + shift)
            lambda_val = lmbda
            box_cox_shift = float(shift)
            if np.any(series_all[np.isfinite(series_all)] + shift <= 0):
                raise ValueError("고정 Box-Cox 기준으로 변환할 수 없는 감시 데이터가 있습니다.")
            # 원본 series_all을 변환값으로 대체 (nan 위치 유지 필요)
            series_all_trans = np.full_like(series_all, np.nan)
            valid_mask = np.isfinite(series_all)
            series_all_trans[valid_mask] = [
                _boxcox_transform_value(v, shift, lmbda) for v in series_all[valid_mask]
            ]
            if not np.all(np.isfinite(series_all_trans[valid_mask])):
                raise ValueError("Box-Cox 변환 결과에 비유한 값이 있습니다.")
            series_all = series_all_trans
            box_cox_applied = True

            spec_vals = {
                "usl": _boxcox_transform_value(usl, shift, lmbda),
                "lsl": _boxcox_transform_value(lsl, shift, lmbda),
                "target": _boxcox_transform_value(target, shift, lmbda),
            }
            if np.isfinite(usl) and not np.isfinite(spec_vals["usl"]):
                box_cox_spec_ok = False
            if np.isfinite(lsl) and not np.isfinite(spec_vals["lsl"]):
                box_cox_spec_ok = False
            if np.isfinite(target) and not np.isfinite(spec_vals["target"]):
                box_cox_spec_ok = False

            if box_cox_spec_ok:
                usl, lsl, target = spec_vals["usl"], spec_vals["lsl"], spec_vals["target"]
                box_cox_msg = f"Box-Cox 변환(λ={lmbda:.3f}, shift={shift:.3g}) 적용. "
            else:
                usl, lsl, target = np.nan, np.nan, np.nan
                capability_unavailable = True
                box_cox_msg = f"Box-Cox 변환(λ={lmbda:.3f}) 적용, 규격 변환 불가로 능력 계산 제외. "
            pre_msg += box_cox_msg
        except Exception as e:
            if control_phase == "phase2":
                raise ValueError(f"관리용 Box-Cox 변환 실패: {e}") from e
            box_cox_msg = f"Box-Cox 변환 실패: {str(e)}. "
            pre_msg += box_cox_msg

    final_valid_mask = np.isfinite(series_all)
    source_indices = source_indices[final_valid_mask]
    series_all = series_all[final_valid_mask]
    final_labels = (
        [str(v).strip() for v in working_labels[final_valid_mask].tolist()]
        if working_labels is not None
        else []
    )
    min_len = n if n > 1 else 3
    if len(series_all) < min_len:
        raise ValueError(f"전처리 후 유효 표본수가 부족합니다. (필요 {min_len}개, 현재 {len(series_all)}개)")

    is_xmr = (n == 1)
    use_s_chart = (not is_xmr and n >= 10)
    if is_xmr:
        dispersion_kind = "MR"
        chart_mode = "I-MR"
    elif use_s_chart:
        dispersion_kind = "S"
        chart_mode = "Xbar-S"
    else:
        dispersion_kind = "R"
        chart_mode = "Xbar-R"

    control_phase = "phase2" if str(chart_usage_mode).lower() == "phase2" else "phase1"
    control_phase_label = "관리용" if control_phase == "phase2" else "해석용"
    baseline_used_count = int(np.sum(source_indices < baseline_end)) // n
    if control_phase == "phase2" and baseline_used_count < 3:
        raise ValueError("전처리 후 기준구간의 유효 부분군/샘플이 3개 미만입니다.")

    if is_xmr:
        x = series_all
        means = x.copy()
        mr = np.abs(np.diff(x))
        if control_phase == "phase2":
            baseline_display_count = baseline_used_count
            x_limit = x[:baseline_display_count]
            mr_limit = np.abs(np.diff(x_limit))
            limit_reference_label = f"원본 샘플 1~{baseline_requested_count} (유효 {baseline_display_count}개)"
        else:
            baseline_display_count = len(means)
            x_limit = x
            mr_limit = mr
            limit_reference_label = "전체 데이터"

        baseline_r_display_count = len(mr_limit)
        monitor_start_index = baseline_display_count
        monitor_r_start_index = baseline_r_display_count

        r_bar = float(np.mean(mr_limit)) if len(mr_limit) > 0 else np.nan
        xbarbar = float(np.mean(x_limit))
        sigma_within = (r_bar / _d2_mr) if np.isfinite(r_bar) else np.nan
        ucl = xbarbar + 3 * sigma_within if np.isfinite(sigma_within) else np.nan
        lcl = xbarbar - 3 * sigma_within if np.isfinite(sigma_within) else np.nan
        r_ucl = 3.267 * r_bar if np.isfinite(r_bar) else np.nan
        r_lcl = 0.0
        ranges = mr
        A2_plot = np.nan
        D3_val = np.nan
        D4_val = np.nan
        d2_val = _d2_mr
    else:
        means, ranges, groups, complete_group_count, _ = _build_subgroup_series(
            series_all=series_all,
            n=n,
            include_incomplete=include_incomplete,
            use_s_chart=use_s_chart,
        )

        if control_phase == "phase2":
            baseline_display_count = baseline_used_count
            groups_limit = groups[:baseline_display_count]
            limit_reference_label = f"원본 부분군 1~{baseline_requested_count} (유효 {baseline_display_count}개)"
        else:
            baseline_display_count = len(means)
            groups_limit = groups
            limit_reference_label = "전체 데이터"

        baseline_r_display_count = baseline_display_count
        monitor_start_index = baseline_display_count
        monitor_r_start_index = baseline_r_display_count

        means_limit = groups_limit.mean(axis=1)
        xbarbar = float(np.mean(means_limit))

        if use_s_chart:
            ranges_limit = groups_limit.std(axis=1, ddof=1)
            r_bar = float(np.mean(ranges_limit))
            A3_val, B3_val, B4_val, c4_val = get_s_chart_constants(n)

            # AIAG–VDA 9.3.3.2: RMS subgroup variance and chi-square limits.
            sigma_within = float(np.sqrt(np.mean(ranges_limit**2)))
            tail = .00135
            z_limit = float(stats.norm.ppf(1-tail))
            ucl = xbarbar + z_limit*sigma_within/np.sqrt(n)
            lcl = xbarbar - z_limit*sigma_within/np.sqrt(n)
            r_lcl, r_ucl = (sigma_within*np.sqrt(stats.chi2.ppf([tail,1-tail], n-1)/(n-1))).tolist()

            A2_plot = np.nan
            D3_val = np.nan
            D4_val = np.nan
            d2_val = np.nan
        else:
            ranges_limit = groups_limit.max(axis=1) - groups_limit.min(axis=1)
            r_bar = float(np.mean(ranges_limit))
            A2_plot, D3_val, D4_val, d2_val = get_control_constants(n)

            ucl = xbarbar + A2_plot * r_bar if np.isfinite(A2_plot) and np.isfinite(r_bar) else np.nan
            lcl = xbarbar - A2_plot * r_bar if np.isfinite(A2_plot) and np.isfinite(r_bar) else np.nan
            r_ucl = D4_val * r_bar if np.isfinite(r_bar) and np.isfinite(D4_val) else np.nan
            r_lcl = D3_val * r_bar if np.isfinite(r_bar) and np.isfinite(D3_val) else np.nan
            sigma_within = (r_bar / d2_val) if np.isfinite(r_bar) and np.isfinite(d2_val) else np.nan

    x_labels, r_labels, x_label_source = _build_chart_date_labels(
        final_labels,
        n=n,
        is_xmr=is_xmr,
        mean_count=len(means),
        range_count=len(ranges),
        include_incomplete=include_incomplete,
    )

    mu = float(np.mean(series_all))
    std_all = float(np.std(series_all, ddof=1)) if len(series_all) > 1 else np.nan

    if detail_level == "full":
        # 공정능력 지수 (단측 규격 지원)
        span = (usl - lsl) if (np.isfinite(usl) and np.isfinite(lsl)) else np.nan
        # 목표치 미입력 시 양측 규격이 있으면 규격 중심을 사용
        target_eff = target if np.isfinite(target) else (
            (usl + lsl) / 2.0 if np.isfinite(usl) and np.isfinite(lsl) else np.nan
        )
        # Cp/Cpk are assigned from the overall estimates only after eligibility checks.
        cp = cpk = np.nan

        pp = safe_div(span, 6 * std_all)

        ppk = np.nan
        if np.isfinite(std_all) and std_all > 0:
            cands = []
            if np.isfinite(usl):
                cands.append((usl - mu) / (3 * std_all))
            if np.isfinite(lsl):
                cands.append((mu - lsl) / (3 * std_all))
            if cands:
                ppk = min(cands)

        cpm = np.nan
        if np.isfinite(span) and np.isfinite(std_all) and std_all > 0 and np.isfinite(target_eff):
            denom = 6.0 * math.sqrt((std_all ** 2) + ((mu - target_eff) ** 2))
            cpm = safe_div(span, denom)

        cpmk = np.nan
        if np.isfinite(std_all) and std_all > 0 and np.isfinite(target_eff):
            denom = 3.0 * math.sqrt((std_all ** 2) + ((mu - target_eff) ** 2))
            cands = []
            if np.isfinite(usl):
                cands.append((usl - mu) / denom)
            if np.isfinite(lsl):
                cands.append((mu - lsl) / denom)
            if cands:
                cpmk = min(cands)

        # PPM
        ppm_cpk = np.nan
        ppm_ppk = ppm_by_sigma(mu, std_all, usl, lsl)

    else:
        cp = cpk = pp = ppk = cpm = cpmk = stab_ratio = ppm_cpk = ppm_ppk = np.nan
        target_eff = target if np.isfinite(target) else ((usl + lsl) / 2 if np.isfinite(usl) and np.isfinite(lsl) else np.nan)

    # Compute within capability independently of overall capability eligibility.
    cw = cwk = ppm_within = np.nan
    within_notes = []
    if n == 1:
        within_notes.append("n=1은 부분군 내 산포를 정의할 수 없어 Cw·Cwk 계산 제외")
    else:
        within_notes.append("σw 추정: " + ("√mean(s²)" if use_s_chart else "R̄/d₂"))
        if detail_level != "full":
            within_notes.append("상세(full) 분석에서 계산")
        elif capability_unavailable:
            within_notes.append("규격 변환 불가로 Cw·Cwk 계산 제외")
        elif not np.isfinite(sigma_within) or sigma_within <= 0:
            within_notes.append("군내 산포가 0이거나 유효하지 않아 Cw·Cwk 계산 제외")
        else:
            cw = safe_div(usl - lsl, 6 * sigma_within)
            distances = []
            if np.isfinite(usl):
                distances.append(usl - mu)
            if np.isfinite(lsl):
                distances.append(mu - lsl)
            if distances:
                cwk = min(distances) / (3 * sigma_within)
            ppm_within = ppm_by_sigma(mu, sigma_within, usl, lsl)
            if not (np.isfinite(usl) and np.isfinite(lsl)):
                within_notes.append("Cw는 양측 규격 필요")
            if not (np.isfinite(usl) or np.isfinite(lsl)):
                within_notes.append("Cwk는 최소 한쪽 규격 필요")
        within_notes.append(f"산포 기준: {limit_reference_label}; 평균 기준: 전체 분석 데이터")
        if outlier_info.get("count", 0) > 0 or box_cox_applied:
            within_notes.append("전처리/변환 후 데이터 및 규격 기준")
    within_capability_note = "; ".join(within_notes)
    within_overall_ratio = (
        abs(cwk / ppk) if np.isfinite(cwk) and np.isfinite(ppk) and ppk != 0 else np.nan
    )
    stab_ratio = within_overall_ratio  # compatibility alias; now |Cwk/Ppk|

    # 실측 불량은 전처리/변환 전의 원본과 원본 규격으로 판정한다.
    emp_ppm = np.nan
    if np.isfinite(original_usl) or np.isfinite(original_lsl):
        bad = int(np.count_nonzero((original_series > original_usl) | (original_series < original_lsl)))
        emp_ppm = (bad / len(original_series)) * 1e6

    # 한계선 위반 카운트
    x_counts = _segment_violation_counts(means, ucl, lcl, baseline_display_count)
    r_counts = _segment_violation_counts(ranges, r_ucl, r_lcl, baseline_r_display_count)
    xbar_ucl_cnt = x_counts['ucl_total']
    xbar_lcl_cnt = x_counts['lcl_total']
    r_ucl_cnt = r_counts['ucl_total']
    r_lcl_cnt = r_counts['lcl_total']

    # 규격 이탈 카운트
    spec_violation_count = int(np.count_nonzero((original_series > original_usl) | (original_series < original_lsl)))

    x_ctrl_violation_count = x_counts['total']
    r_ctrl_violation_count = r_counts['total']

    # 최근 이탈 (마지막 점)
    last_x = original_series[-1] if len(original_series) else np.nan
    recent_spec_violation = int(
        (np.isfinite(original_usl) and np.isfinite(last_x) and last_x > original_usl) or
        (np.isfinite(original_lsl) and np.isfinite(last_x) and last_x < original_lsl)
    )
    last_mean = means[-1] if len(means) else np.nan
    last_range = ranges[-1] if len(ranges) else np.nan
    recent_ctrl_violation = int(
        (np.isfinite(ucl) and np.isfinite(last_mean) and last_mean > ucl) or
        (np.isfinite(lcl) and np.isfinite(last_mean) and last_mean < lcl) or
        (np.isfinite(r_ucl) and np.isfinite(last_range) and last_range > r_ucl) or
        (np.isfinite(r_lcl) and np.isfinite(last_range) and last_range < r_lcl)
    )

    # 정규성 검정
    try:
        norm_msg = normality_message(series_all) if detail_level == "full" else "상세 통계 실행 전"
    except Exception:
        norm_msg = "정규성 검정 불가"

    # KSQ 규칙 검출
    # KSQ 규칙은 X/Xbar 관리도의 1σ/2σ/3σ 존 기준으로 판정.
    # 따라서 공정 sigma(std_within) 대신 X/Xbar 한계폭으로부터 sigma를 복원해 사용한다.
    sigma_ksq = ((ucl - lcl) / 6.0) if (np.isfinite(ucl) and np.isfinite(lcl) and ucl > lcl) else np.nan
    det = detect_ksq_rules(
        means=means,
        cl=xbarbar if np.isfinite(xbarbar) else mu,
        A2=A2_plot,
        r_bar=r_bar,
        is_xmr=is_xmr,
        sigma_override=sigma_ksq,
        ucl=ucl,
        lcl=lcl,
        t1_by_manual=True,
    )
    ksq_counts, ksq_idx = det['counts'], det['idx']

    # KSQ 토글 반영: 비활성 규칙은 집계/설명/알림에서 제외
    if isinstance(ksq_toggles, dict):
        for i in range(1, 9):
            rk = f'T{i}'
            if not bool(ksq_toggles.get(rk, True)):
                ksq_counts[rk] = 0
                ksq_idx[rk] = set()

    ksq_counts_baseline, ksq_counts_monitor = _split_ksq_counts(ksq_idx, baseline_display_count)
    baseline_ksq_total = int(sum(ksq_counts_baseline.values()))
    baseline_ctrl_total = int(x_counts['baseline'] + r_counts['baseline'])
    baseline_recommended_min = 20
    baseline_n_short = bool(control_phase == "phase2" and baseline_display_count < baseline_recommended_min)
    baseline_unstable = bool(
        control_phase == "phase2"
        and (baseline_ctrl_total > 0 or baseline_ksq_total > 0 or baseline_n_short)
    )
    baseline_status = "부적합" if baseline_unstable else ("해당없음" if control_phase != "phase2" else "적합")
    baseline_reasons: List[str] = []
    if control_phase == "phase2":
        if baseline_ctrl_total > 0:
            baseline_reasons.append("기준구간 관리이탈")
        if baseline_ksq_total > 0:
            baseline_reasons.append("기준구간 KSQ 위반")
        if baseline_n_short:
            baseline_reasons.append(f"기준구간 {baseline_display_count}개(<{baseline_recommended_min} 권장)")

    limit_reference_desc = (
        f"초기 {limit_reference_label} 기준 고정 관리한계"
        if control_phase == "phase2"
        else "전체 데이터 기준 시험 관리한계"
    )

    has_spec = bool(np.isfinite(usl) or np.isfinite(lsl))
    variance_zero = bool(
        (np.isfinite(std_all) and std_all == 0)
        or (not np.isfinite(std_all))
        or (not np.isfinite(sigma_within))
        or (np.isfinite(sigma_within) and sigma_within <= 0)
    )
    normality_suspect = "의심" in str(norm_msg)
    preprocess_applied = bool(outlier_info.get("count", 0) > 0 or box_cox_applied)
    reliability_status, reliability_reasons = _build_reliability({
        "analysis_n": len(series_all),
        "min_len": min_len,
        "variance_zero": variance_zero,
        "capability_unavailable": capability_unavailable,
        "has_spec": has_spec,
        "normality_suspect": normality_suspect,
        "baseline_unstable": baseline_unstable,
        "process_unstable": x_ctrl_violation_count + r_ctrl_violation_count > 0 or any(ksq_counts.values()),
        "missing_groups": excluded_missing_groups > 0,
        "preprocess_applied": preprocess_applied,
    })

    capability_reasons = []
    if detail_level != "full":
        capability_status = "미평가"
        capability_reasons.append("상세(full) 분석에서 평가")
    elif capability_unavailable or not has_spec or not np.isfinite(std_all) or std_all <= 0:
        capability_status = "계산불가"
        if capability_unavailable:
            capability_reasons.append("규격 변환 불가")
        if not has_spec:
            capability_reasons.append("규격없음")
        if not np.isfinite(std_all) or std_all <= 0:
            capability_reasons.append("전체 산포가 0이거나 유효하지 않음")
    else:
        if x_ctrl_violation_count + r_ctrl_violation_count > 0 or any(ksq_counts.values()):
            capability_reasons.append("공정불안정: 관리이탈 또는 활성 KSQ 규칙 위반")
        if baseline_unstable:
            capability_reasons.append("관리용 기준구간 부적합")
        if normality_suspect:
            capability_reasons.append("비정규 의심: 분포 모델 검토 필요")
        elif "만족" not in norm_msg:
            capability_reasons.append("정규성 확인 불가")
        if x_ctrl_violation_count + r_ctrl_violation_count > 0 or any(ksq_counts.values()) or baseline_unstable:
            capability_status = "관리상태 확인"
        elif normality_suspect:
            capability_status = "분포 검토 필요"
        elif "만족" not in norm_msg:
            capability_status = "정규성 확인 필요"
        else:
            capability_status = "적용 가능"
        if capability_status == "적용 가능":
            cp, cpk, ppm_cpk = pp, ppk, ppm_ppk
            if not (np.isfinite(usl) and np.isfinite(lsl)):
                capability_reasons.append("Cp는 양측 규격 필요; Cpk만 적용 가능")
    performance_note = (
        "Pp·Ppk 및 예상 PPM은 전체 데이터의 정규모델 가정 참고값입니다. "
        "관측 구간의 적용 조건 충족은 공식 공정능력 입증을 의미하지 않습니다."
    )
    if normality_suspect or "만족" not in norm_msg:
        performance_note += " 비정규 의심 또는 정규성 미확인: 모델 검토 전 능력 판정에 사용하지 마세요."
        within_capability_note += "; 군내 정규성은 별도 확인 필요"

    result = dict(
        calculation_version=CALCULATION_VERSION,
        distribution_mode='normal',
        capability_method='normal',
        capability_methods={},
        capability_status=capability_status,
        capability_reasons=capability_reasons,
        performance_note=performance_note,
        within_overall_ratio=within_overall_ratio,
        ppm_within=ppm_within,
        detail_level=detail_level,
        column=col,
        original_series=original_series,
        raw_series=raw_series,
        series=series_all,
        original_n=int(len(original_series)),
        source_indices=source_indices,
        excluded_missing_groups=excluded_missing_groups,
        empirical_basis="원본 유효 측정값",
        analysis_n=int(len(series_all)),
        means=means,
        ranges=ranges,
        x_labels=x_labels,
        r_labels=r_labels,
        x_label_source=x_label_source,
        usl=usl,
        lsl=lsl,
        target=target_eff,
        original_usl=original_usl,
        original_lsl=original_lsl,
        original_target=original_target,
        original_mu=original_mu,
        original_std_all=original_std_all,
        mu=mu,
        std_all=std_all,
        std_within=sigma_within,
        cp=cp,
        cpk=cpk,
        pp=pp,
        ppk=ppk,
        cpm=cpm,
        cpmk=cpmk,
        cw=cw,
        cwk=cwk,
        within_capability_note=within_capability_note,
        ppm_cpk=ppm_cpk,
        ppm_ppk=ppm_ppk,
        emp_ppm=emp_ppm,
        xbarbar=xbarbar,
        ucl=ucl,
        lcl=lcl,
        r_bar=r_bar,
        r_ucl=r_ucl,
        r_lcl=r_lcl,
        xbar_ucl_cnt=int(xbar_ucl_cnt),
        xbar_lcl_cnt=int(xbar_lcl_cnt),
        r_ucl_cnt=int(r_ucl_cnt),
        r_lcl_cnt=int(r_lcl_cnt),
        stab_ratio=stab_ratio,
        norm_msg=norm_msg,
        ksq_counts=ksq_counts,
        ksq_idx=ksq_idx,
        ksq_counts_baseline=ksq_counts_baseline,
        ksq_counts_monitor=ksq_counts_monitor,
        baseline_status=baseline_status,
        baseline_reasons=baseline_reasons,
        baseline_ksq_total=baseline_ksq_total,
        baseline_ctrl_total=baseline_ctrl_total,
        baseline_recommended_min=baseline_recommended_min,
        # 요약용
        spec_violation_count=int(spec_violation_count),
        x_ctrl_violation_count=x_ctrl_violation_count,
        r_ctrl_violation_count=r_ctrl_violation_count,
        xbar_ucl_cnt_baseline=x_counts['ucl_baseline'],
        xbar_lcl_cnt_baseline=x_counts['lcl_baseline'],
        xbar_ucl_cnt_monitor=x_counts['ucl_monitor'],
        xbar_lcl_cnt_monitor=x_counts['lcl_monitor'],
        r_ucl_cnt_baseline=r_counts['ucl_baseline'],
        r_lcl_cnt_baseline=r_counts['lcl_baseline'],
        r_ucl_cnt_monitor=r_counts['ucl_monitor'],
        r_lcl_cnt_monitor=r_counts['lcl_monitor'],
        x_ctrl_violation_count_baseline=x_counts['baseline'],
        r_ctrl_violation_count_baseline=r_counts['baseline'],
        x_ctrl_violation_count_monitor=x_counts['monitor'],
        r_ctrl_violation_count_monitor=r_counts['monitor'],
        recent_spec_violation=recent_spec_violation,
        recent_ctrl_violation=recent_ctrl_violation,
        is_xmr=is_xmr,
        chart_mode=chart_mode,
        dispersion_kind=dispersion_kind,
        n=n,
        A2_plot=A2_plot,
        control_phase=control_phase,
        control_phase_label=control_phase_label,
        baseline_requested_count=baseline_requested_count,
        baseline_display_count=baseline_display_count,
        baseline_r_display_count=baseline_r_display_count,
        monitor_start_index=monitor_start_index,
        monitor_r_start_index=monitor_r_start_index,
        limit_reference_label=limit_reference_label,
        limit_reference_desc=limit_reference_desc,
        pre_msg=pre_msg.strip(),
        outlier_info=outlier_info,
        preprocess_applied=preprocess_applied,
        box_cox_applied=box_cox_applied,
        box_cox_lambda=lambda_val,
        box_cox_shift=box_cox_shift,
        box_cox_spec_ok=box_cox_spec_ok,
        capability_unavailable=capability_unavailable,
        reliability_status=reliability_status,
        reliability_reasons=reliability_reasons,
        reliability_reason_text=", ".join(reliability_reasons),
    )
    if distribution_config['mode'] == 'nonnormal':
        from spc_nonnormal import apply_analysis
        result = apply_analysis(result, distribution_config)
    result['dispersion_limit_method'] = ('AIAG–VDA 9.3.3.2 · √mean(s²), χ² 정확 한계' if use_s_chart
        else result.get('dispersion_limit_method', 'Shewhart R/MR · D3/D4 근사 한계'))
    if box_cox_applied and not capability_unavailable and detail_level == 'full':
        from spc_policy import boxcox_capability
        result = boxcox_capability(result)
    return finalize_assessment(result, assessment_settings)


def _excel_engine(path_or_bytes, head: bytes = b'') -> str:
    """구형 XLS(OLE)는 xlrd, OOXML 계열은 openpyxl로 읽는다."""
    name = str(path_or_bytes if isinstance(path_or_bytes, (str, Path)) else getattr(path_or_bytes, 'name', '') or '').lower()
    if name.endswith('.xls') and not name.endswith('.xlsx'):
        return 'xlrd'
    if bytes(head or b'').startswith(b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'):
        return 'xlrd'
    return 'openpyxl'


def load_data_df(path_or_bytes, sheet_name=None) -> pd.DataFrame:
    """
    Excel 또는 CSV 파일 로드.
    path_or_bytes: 파일 경로(str) 또는 bytes-like 객체 (Streamlit 업로드용)
    """
    import io

    # 확장자 확인
    if isinstance(path_or_bytes, str):
        lower = path_or_bytes.lower()
        if lower.endswith('.csv'):
            return pd.read_csv(path_or_bytes, encoding='utf-8-sig')
        else:
            return pd.read_excel(path_or_bytes, sheet_name=sheet_name or 0,
                                 engine=_excel_engine(path_or_bytes))
    else:
        # bytes-like (Streamlit UploadedFile/BytesIO/bytes)
        stream = path_or_bytes
        if isinstance(path_or_bytes, (bytes, bytearray)):
            stream = io.BytesIO(path_or_bytes)

        name = str(getattr(stream, 'name', '') or '').lower()

        # 파일명 힌트 우선
        if name.endswith('.csv'):
            if hasattr(stream, 'seek'):
                stream.seek(0)
            try:
                return pd.read_csv(stream, encoding='utf-8-sig')
            except Exception:
                if hasattr(stream, 'seek'):
                    stream.seek(0)
                return pd.read_csv(stream, encoding='cp949')

        if name.endswith(('.xlsx', '.xls', '.xlsm')):
            if hasattr(stream, 'seek'):
                stream.seek(0)
            return pd.read_excel(stream, sheet_name=sheet_name or 0,
                                 engine=_excel_engine(stream))

        # 파일명 없을 때는 헤더 시그니처로 추정
        head = b''
        if hasattr(stream, 'seek') and hasattr(stream, 'read'):
            try:
                stream.seek(0)
                head = stream.read(4096)
                stream.seek(0)
            except Exception:
                head = b''

        is_zip_like = head.startswith(b'PK')
        is_ole_like = head.startswith(b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1')

        # 텍스트 파일이면 CSV 우선 시도
        if not (is_zip_like or is_ole_like):
            try:
                if hasattr(stream, 'seek'):
                    stream.seek(0)
                return pd.read_csv(stream, encoding='utf-8-sig')
            except Exception:
                try:
                    if hasattr(stream, 'seek'):
                        stream.seek(0)
                    return pd.read_csv(stream, encoding='cp949')
                except Exception:
                    pass

        if hasattr(stream, 'seek'):
            stream.seek(0)
        return pd.read_excel(stream, sheet_name=sheet_name or 0,
                             engine=_excel_engine(stream, head))


def get_sheet_names(path_or_bytes) -> List[str]:
    """Excel 시트 이름 목록 반환."""
    import io
    try:
        head = b''
        if not isinstance(path_or_bytes, (str, Path)) and hasattr(path_or_bytes, 'read'):
            position = path_or_bytes.tell() if hasattr(path_or_bytes, 'tell') else 0
            path_or_bytes.seek(0)
            head = path_or_bytes.read(8)
            path_or_bytes.seek(position)
        xl = pd.ExcelFile(path_or_bytes, engine=_excel_engine(path_or_bytes, head))
        return xl.sheet_names
    except Exception:
        return []


def load_spec_df(path_or_bytes, sheet_name=None) -> Dict[str, Tuple[float, float, float]]:
    """
    규격 Excel 로드.
    형식: 컬럼명 | USL | LSL | Target(선택)  (헤더 포함)
    반환: {컬럼명: (usl, lsl, target)}
    """
    try:
        df = load_data_df(path_or_bytes, sheet_name=sheet_name)
        spec_map = {}
        cols = [str(c).strip().lower() for c in df.columns]

        # 컬럼명 자동 탐지
        col_idx = None
        usl_idx = None
        lsl_idx = None
        target_idx = None
        for i, c in enumerate(cols):
            if c in ('column', 'item', 'variable', '항목', '변수', '컬럼', '품목', 'name', '이름'):
                col_idx = i
            if c in ('usl', '규격 상한', '상한', 'upper', 'upper spec', '규격상한'):
                usl_idx = i
            if c in ('lsl', '규격 하한', '하한', 'lower', 'lower spec', '규격하한'):
                lsl_idx = i
            if c in ('target', 'target value', 'nominal', 't', '목표', '목표치', '타겟'):
                target_idx = i

        if col_idx is None:
            col_idx = 0
        has_named_limit = usl_idx is not None or lsl_idx is not None
        if usl_idx is None and not has_named_limit:
            usl_idx = 1 if len(df.columns) > 1 else None
        if lsl_idx is None and not has_named_limit:
            lsl_idx = 2 if len(df.columns) > 2 else None
        if target_idx is None and not has_named_limit:
            target_idx = 3 if len(df.columns) > 3 else None

        for _, row in df.iterrows():
            raw_key = row.iloc[col_idx]
            # iterrows()가 int 열도 float으로 변환(1→1.0)하므로 정수화 처리
            try:
                f = float(raw_key)
                key = str(int(f)) if f == int(f) else str(f)
            except (TypeError, ValueError):
                key = str(raw_key)
            key = key.strip()
            if not key or key.lower() in ('nan', 'none', ''):
                continue
            if key in spec_map:
                raise ValueError(f"{key}: 규격 항목이 중복되었습니다. 항목별 규격을 하나로 정리하세요.")
            def spec_number(idx, name):
                value = row.iloc[idx] if idx is not None else np.nan
                if pd.isna(value) or not str(value).strip():
                    return np.nan
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key}: {name}은 숫자 또는 빈 칸이어야 합니다.") from exc
                if not np.isfinite(number):
                    raise ValueError(f"{key}: {name}은 유한한 숫자여야 합니다.")
                return number
            usl = spec_number(usl_idx, "USL")
            lsl = spec_number(lsl_idx, "LSL")
            target = spec_number(target_idx, "Target")
            if np.isfinite(usl) and np.isfinite(lsl) and usl <= lsl:
                raise ValueError(f"{key}: 규격 상한 USL은 규격 하한 LSL보다 커야 합니다.")
            spec_map[key] = (usl, lsl, target)

        return spec_map
    except Exception as e:
        raise ValueError(f"규격 파일 로드 실패: {e}")


def run_full_analysis(
    data_bytes,
    data_sheet: Optional[str],
    spec_bytes=None,
    spec_sheet: Optional[str] = None,
    n: int = 5,
    include_incomplete: bool = False,
    data_layout_mode: str = "long",
    subgroup_columns: Optional[List[str]] = None,
    subgroup_var_name: str = "측정치",
    ksq_toggles: Optional[Dict[str, bool]] = None,
    chart_usage_mode: str = "phase1",
    baseline_subgroups: int = 25,
    manual_target_map: Optional[Dict[str, float]] = None,
    preprocess_outliers: bool = False,
    box_cox_transform: bool = False,
    measurement_date_column: Optional[str] = None,
    outlier_method: str = OUTLIER_METHOD_IQR,
    detail_level: str = "full",
    selected_columns: Optional[List[str]] = None,
    progress_callback=None,
    distribution_settings: Optional[Dict[str, Any]] = None,
    assessment_settings: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict], pd.DataFrame]:
    """
    전체 분석 실행.
    반환: (results_list, summary_df)
    """
    from spc_nonnormal import MODEL_VERSION, normalize_settings_map
    distribution_settings = normalize_settings_map(distribution_settings)
    assessment_settings = normalize_assessment(assessment_settings)
    settings = dict(assessment_settings=assessment_settings, distribution_settings=distribution_settings, distribution_model_version=MODEL_VERSION,
                    detail_level=detail_level, selected_columns=selected_columns, data_sheet=data_sheet, spec_sheet=spec_sheet, n=n,
                    include_incomplete=include_incomplete, data_layout_mode=data_layout_mode,
                    subgroup_columns=subgroup_columns, subgroup_var_name=subgroup_var_name,
                    ksq_toggles=ksq_toggles, chart_usage_mode=chart_usage_mode,
                    baseline_subgroups=baseline_subgroups, manual_target_map=manual_target_map,
                    preprocess_outliers=preprocess_outliers, box_cox_transform=box_cox_transform,
                    measurement_date_column=measurement_date_column,
                    outlier_method=normalize_outlier_method(outlier_method))
    audit = dict(calculation_version=CALCULATION_VERSION,
                 calculation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 distribution_sha256=hashlib.sha256(Path(__file__).with_name('spc_nonnormal.py').read_bytes()).hexdigest(),
                 policy_sha256=hashlib.sha256(Path(__file__).with_name('spc_policy.py').read_bytes()).hexdigest(),
                 data_sha256=_input_digest(data_bytes), spec_sha256=_input_digest(spec_bytes),
                 settings=settings, numpy_version=np.__version__, pandas_version=pd.__version__)
    import scipy
    audit["scipy_version"] = scipy.__version__
    audit["analysis_id"] = hashlib.sha256(json.dumps(audit, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    audit["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    df = load_data_df(data_bytes, sheet_name=data_sheet)
    date_col = None
    if measurement_date_column:
        label_to_col_all = {str(c): c for c in df.columns}
        date_col = label_to_col_all.get(str(measurement_date_column))

    date_labels_all = _format_axis_labels_from_series(df[date_col]) if date_col is not None else []
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != date_col]
    if selected_columns is not None and data_layout_mode == "long":
        num_cols = [c for c in num_cols if str(c) in selected_columns]
    if not num_cols:
        raise ValueError("수치형 컬럼이 없습니다. 데이터를 확인해주세요.")

    # 규격 로드
    spec_map: Dict[str, Tuple[float, float, float]] = {}
    if spec_bytes is not None:
        spec_map = load_spec_df(spec_bytes, sheet_name=spec_sheet)

    manual_targets: Dict[str, float] = {}
    manual_targets_norm: Dict[str, float] = {}
    if isinstance(manual_target_map, dict):
        for mk, mv in manual_target_map.items():
            key_raw = str(mk).strip()
            if not key_raw:
                continue
            try:
                v = float(mv)
            except Exception:
                continue
            if not np.isfinite(v):
                continue
            manual_targets[key_raw] = v
            manual_targets_norm[re.sub(r'\s+', '', key_raw).lower()] = v

    def _resolve_spec(candidates: List[Any]) -> Tuple[float, float, float]:
        usl, lsl, target = np.nan, np.nan, np.nan
        if spec_map:
            keys_norm = {re.sub(r'\s+', '', str(k)).lower(): k for k in spec_map.keys()}
            for cand in candidates:
                if cand in spec_map:
                    vals = spec_map[cand]
                    if len(vals) >= 3:
                        usl, lsl, target = vals[0], vals[1], vals[2]
                    else:
                        usl, lsl, target = vals[0], vals[1], np.nan
                    break

                c_norm = re.sub(r'\s+', '', str(cand)).lower()
                if c_norm in keys_norm:
                    vals = spec_map[keys_norm[c_norm]]
                    if len(vals) >= 3:
                        usl, lsl, target = vals[0], vals[1], vals[2]
                    else:
                        usl, lsl, target = vals[0], vals[1], np.nan
                    break

        # 수동 목표치가 있으면 규격 파일 Target보다 우선 적용
        if manual_targets:
            for cand in candidates:
                c_raw = str(cand).strip()
                if c_raw in manual_targets:
                    target = manual_targets[c_raw]
                    break
                c_norm = re.sub(r'\s+', '', c_raw).lower()
                if c_norm in manual_targets_norm:
                    target = manual_targets_norm[c_norm]
                    break
        return usl, lsl, target

    results: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []
    layout_mode = str(data_layout_mode or "long").lower()

    targets: List[Dict[str, Any]] = []
    if layout_mode == "wide_subgroup_cols":
        label_to_col = {str(c): c for c in num_cols}
        selected_cols: List[Any] = []
        for raw in (subgroup_columns or []):
            key = str(raw)
            if key in label_to_col:
                c = label_to_col[key]
                if c not in selected_cols:
                    selected_cols.append(c)

        if len(selected_cols) < 2:
            targets_for_auto = [c for c in num_cols if str(c).lower() not in ("id", "no", "lot")]
            selected_cols = targets_for_auto if len(targets_for_auto) >= 2 else num_cols[:2]

        wide_df = df[selected_cols].apply(pd.to_numeric, errors='coerce')
        valid_mask = np.isfinite(wide_df.to_numpy(dtype=float)).all(axis=1)
        wide_group_labels = date_labels_all
        wide_labels = [
            label
            for label in wide_group_labels
            for _ in selected_cols
        ]

        if int(valid_mask.sum()) < 3:
            raise ValueError("부분군 다열 모드에서 유효 부분군 수가 너무 적습니다.")

        var_name = str(subgroup_var_name).strip() or "측정치"
        series_all = wide_df.to_numpy(dtype=float).reshape(-1)
        targets.append({
            "col": var_name,
            "series_all": series_all,
            "n": int(len(selected_cols)),
            "include_incomplete": False,
            "spec_candidates": [var_name] + list(selected_cols),
            "sample_labels": wide_labels,
        })
    else:
        for col in num_cols:
            series_raw = pd.to_numeric(df[col], errors='coerce')
            series_all = series_raw.astype(float).values
            col_labels = date_labels_all
            min_len = n if n > 1 else 3
            if len(series_all) < min_len:
                continue
            targets.append({
                "col": col,
                "series_all": series_all,
                "n": int(n),
                "include_incomplete": bool(include_incomplete),
                "spec_candidates": [col],
                "sample_labels": col_labels,
            })

    for target_index, t in enumerate(targets):
        if progress_callback is not None:
            progress_callback(target_index, len(targets), str(t["col"]))
        col = t["col"]
        series_all = t["series_all"]
        n_eff = int(t["n"])
        include_eff = bool(t["include_incomplete"])
        usl, lsl, target = _resolve_spec(t.get("spec_candidates", [col]))

        try:
            it = analyze_column(
                series_all=series_all,
                col=col,
                n=n_eff,
                usl=usl,
                lsl=lsl,
                target=target,
                include_incomplete=include_eff,
                ksq_toggles=ksq_toggles,
                chart_usage_mode=chart_usage_mode,
                baseline_subgroups=baseline_subgroups,
                preprocess_outliers=preprocess_outliers,
                box_cox_transform=box_cox_transform,
                sample_labels=t.get("sample_labels"),
                outlier_method=outlier_method,
                detail_level=detail_level,
                distribution_config=distribution_settings.get(str(col)),
                assessment_settings=assessment_settings,
            )
            it['assessment'] = assess_process(it)
            results.append(it)

            summary_rows.append({
                '변수': str(col),
                '판정': it['assessment']['text'],
                'N': len(it['series']),
                '원본N': it.get('original_n', len(series_all)),
                '분석N': it.get('analysis_n', len(it['series'])),
                '신뢰도 상태': it.get('reliability_status', ''),
                '신뢰도 사유': it.get('reliability_reason_text', ''),
                '관리도': it.get('chart_mode', ''),
                '분포 모드': it.get('distribution_mode', 'normal'),
                '적용 분포': (it.get('distribution_model') or it.get('chart_model') or {}).get('name', '정규' if it.get('distribution_mode') != 'nonnormal' else '미확인'),
                '위치 관리도 분포': (it.get('chart_model') or {}).get('name', ''),
                '위치 한계 계산': it.get('chart_limit_method', ''),
                '능력 계산법': it.get('capability_method', 'normal'),
                '고객 요구값': assessment_settings['required_limit'],
                '개선 경계': assessment_settings['warning_limit'],
                '조사 결론': it.get('study_conclusion', ''),
                '시간 패턴 검토': it.get('sequence_review', {}).get('count', 0),
                '기준분포 상태': it.get('chart_fit_status', ''),
                '성능분포 상태': it.get('performance_fit_status', ''),
                **{f'{name}.{method}': fmt_value(it.get(f'{name.lower()}_{method.lower()}', np.nan))
                   for name in ('Cp', 'Cpk', 'Pp', 'Ppk') for method in ('G', 'Z')},
                '평균': fmt_value(it['mu']),
                '원본평균': fmt_value(it.get('original_mu', np.nan)),
                'σ(전체)': fmt_value(it['std_all']),
                '원본σ(전체)': fmt_value(it.get('original_std_all', np.nan)),
                'σ(군내)': fmt_value(it['std_within']),
                'Cp': fmt_value(it['cp']),
                'Cpk': fmt_value(it['cpk']),
                'PPM(Within)': fmt_value(it['ppm_within'], comma=True),
                'Pp': fmt_value(it['pp']),
                'Ppk': fmt_value(it['ppk']),
                'PPM(Overall)': fmt_value(it['ppm_ppk'], comma=True),
                'Cpm': fmt_value(it['cpm']),
                'Cpmk': fmt_value(it['cpmk']),
                'Cw': fmt_value(it['cw']),
                'Cwk': fmt_value(it['cwk']),
                '목표치(T)': fmt_value(it['target'], 3),
                '군내/전체 지수 비율(Cwk/Ppk)': fmt_value(it['within_overall_ratio']),
                '능력 적용 상태': it['capability_status'],
                '능력 적용 사유': "; ".join(it['capability_reasons']),
                '성능 해석 안내': it['performance_note'],
                'PPM(실측)': fmt_value(it['emp_ppm'], comma=True),
                '규격이탈': it['spec_violation_count'],
                '운영모드': it.get('control_phase_label', ''),
                '한계기준': it.get('limit_reference_label', ''),
                'X관리이탈': (
                    it['x_ctrl_violation_count_monitor']
                    if it.get('control_phase') == 'phase2'
                    else it['x_ctrl_violation_count']
                ),
                'R관리이탈': (
                    it['r_ctrl_violation_count_monitor']
                    if it.get('control_phase') == 'phase2'
                    else it['r_ctrl_violation_count']
                ),
                '최근규격이탈': it['recent_spec_violation'],
                '최근관리이탈': it['recent_ctrl_violation'],
                '기준구간상태': it.get('baseline_status', ''),
                '기준구간사유': "; ".join(it.get('baseline_reasons', [])),
                '이상치제거': int((it.get('outlier_info') or {}).get('count', 0)),
                '능력기준': "전처리 후" if it.get('preprocess_applied') else "원본",
                '비고': it.get('pre_msg', ''),
            })
        except Exception as e:
            summary_rows.append({'변수': str(col), 'N': len(series_all),
                                 '신뢰도 상태': '계산불가', '신뢰도 사유': str(e), '오류': str(e)})

    summary_df = pd.DataFrame(summary_rows)
    summary_df.attrs['audit'] = audit
    summary_df.attrs['numeric_results'] = {
        str(it['column']): {**{name: it.get(name.lower(), np.nan) for name in ('Cp', 'Cpk', 'Pp', 'Ppk', 'Cw', 'Cwk')},
                           **{f'{name}.{method}': it.get(f'{name.lower()}_{method.lower()}', np.nan)
                              for name in ('Cp', 'Cpk', 'Pp', 'Ppk') for method in ('G', 'Z')},
                           'PPM(Within)': it['ppm_within'], 'PPM(Overall)': it['ppm_ppk'], 'PPM(실측)': it['emp_ppm']}
        for it in results}
    return results, summary_df

