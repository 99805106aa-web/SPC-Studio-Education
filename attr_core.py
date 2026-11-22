# -*- coding: utf-8 -*-
"""
attr_core.py
============
계수형(Attribute) 관리도 & 공정능력 핵심 계산 로직 (GUI 의존 없음)

지원 관리도 유형:
  - p  관리도 (불량률)
  - np 관리도 (불량수)
  - c  관리도 (결점수)
  - u  관리도 (단위당 결점수)

이탈 규칙: T1 ~ T4 (ISO 7870-2 기반 간소화)
공정능력: DPU, DPO, DPMO, Yield, Sigma Level, PPM
"""

import io
import math
import re
import os
from spc_access import day_guard
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import norm
from typing import Optional, Tuple, Dict, List, Any


DEFAULT_BASELINE_SUBGROUPS = 25
LIMIT_METHODS = {"exact": "분포 직접 계산", "normal": "정규근사 ±3σ"}
LIMIT_TAIL = 0.00135


def _discrete_limits(distribution, *parameters):
    # 하한은 누적확률이 꼬리 확률 이하인 가장 큰 정수다. 해당 값이 없으면 0을 사용한다.
    lower = distribution.ppf(LIMIT_TAIL, *parameters)
    lower = np.maximum(0, lower - (distribution.cdf(lower, *parameters) > LIMIT_TAIL))
    upper = distribution.ppf(1 - LIMIT_TAIL, *parameters)
    return lower, upper


# ════════════════════════════════════════════════════════════════
# 1. 데이터 로딩 함수
# ════════════════════════════════════════════════════════════════

def _excel_engine(source):
    """Choose the reader from file content, including unnamed uploaded bytes."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as handle:
            header = handle.read(8)
    else:
        position = source.tell()
        source.seek(0)
        header = source.read(8)
        source.seek(position)
    return "xlrd" if header.startswith(b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1') else "openpyxl"


def get_sheet_names(file_obj) -> list[str]:
    """Excel 파일의 시트 목록을 반환한다.

    Parameters
    ----------
    file_obj : str | bytes | file-like
        파일 경로 또는 바이트/스트림 객체 (Streamlit UploadedFile 등)

    Returns
    -------
    list[str]
        시트 이름 목록. 실패 시 빈 리스트.
    """
    try:
        if isinstance(file_obj, (bytes, bytearray)):
            file_obj = io.BytesIO(file_obj)
        with pd.ExcelFile(file_obj, engine=_excel_engine(file_obj)) as xl:
            return xl.sheet_names
    except Exception:
        return []


def load_data_df(path_or_bytes, sheet_name=None) -> pd.DataFrame:
    """Excel 또는 CSV 파일을 DataFrame으로 로드한다.

    인코딩 처리:
      - CSV: UTF-8 → CP949 자동 폴백
      - Excel: xlsx는 openpyxl, 구형 xls는 xlrd 엔진 사용

    Parameters
    ----------
    path_or_bytes : str | bytes | file-like
        파일 경로(str) 또는 bytes-like 객체 (Streamlit 업로드용)
    sheet_name : str | int | None
        Excel 시트 이름/인덱스. None이면 첫 번째 시트.

    Returns
    -------
    pd.DataFrame
        로드된 데이터프레임
    """
    # ── 파일 경로(str)인 경우 ──
    if isinstance(path_or_bytes, os.PathLike):
        path_or_bytes = os.fspath(path_or_bytes)
    if isinstance(path_or_bytes, str):
        lower = path_or_bytes.lower()
        if lower.endswith('.csv'):
            try:
                return pd.read_csv(path_or_bytes, encoding='utf-8-sig')
            except UnicodeDecodeError:
                return pd.read_csv(path_or_bytes, encoding='cp949')
        else:
            return pd.read_excel(
                path_or_bytes,
                sheet_name=sheet_name or 0,
                engine=_excel_engine(path_or_bytes),
            )

    # ── bytes / file-like 객체인 경우 ──
    stream = path_or_bytes
    if isinstance(path_or_bytes, (bytes, bytearray)):
        stream = io.BytesIO(path_or_bytes)

    name = str(getattr(stream, 'name', '') or '').lower()

    # CSV 파일명 힌트
    if name.endswith('.csv'):
        if hasattr(stream, 'seek'):
            stream.seek(0)
        try:
            return pd.read_csv(stream, encoding='utf-8-sig')
        except Exception:
            if hasattr(stream, 'seek'):
                stream.seek(0)
            return pd.read_csv(stream, encoding='cp949')

    # Excel 파일명 힌트
    if name.endswith(('.xlsx', '.xls', '.xlsm')):
        if hasattr(stream, 'seek'):
            stream.seek(0)
        return pd.read_excel(
            stream, sheet_name=sheet_name or 0, engine=_excel_engine(stream)
        )

    # ── 파일명 없을 때 헤더 시그니처로 추정 ──
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

    # 기본: Excel로 시도
    if hasattr(stream, 'seek'):
        stream.seek(0)
    return pd.read_excel(
        stream, sheet_name=sheet_name or 0, engine=_excel_engine(stream)
    )


# ════════════════════════════════════════════════════════════════
# 2. 컬럼 자동 탐지
# ════════════════════════════════════════════════════════════════

# 컬럼 매칭용 키워드 사전 (소문자 비교)
_SUBGROUP_KEYWORDS = [
    '부분군', '번호', 'no', 'group', 'sample', 'subgroup',
    'lot', '로트', 'batch', '시료번호', '순번', 'index', 'id',
]
_SAMPLE_SIZE_KEYWORDS = [
    '검사수', '시료수', 'n', '부분군크기', 'sample size', 'samplesize',
    'sample_size', 'inspection', '검사량', '수량', 'size',
    'count', '총수', 'total', 'units', '단위수', '검사단위',
]
_DEFECT_COUNT_KEYWORDS = [
    '불량수', '불량', '결점수', '결점', 'd', 'c',
    'defect', 'defects', 'reject', 'rejects',
    'nonconforming', 'nonconformities', 'nc',
    '부적합수', '부적합', '불량개수', '결함수', '결함',
    'np', '불량품수',
]


def auto_detect_columns(df: pd.DataFrame, chart_type: str) -> dict:
    """DataFrame 컬럼명을 관리도 유형에 맞게 자동 매칭한다.

    한글/영문 키워드를 기반으로 각 역할(부분군, 시료수, 불량/결점수)에
    가장 적합한 컬럼을 탐지한다.

    Parameters
    ----------
    df : pd.DataFrame
        데이터프레임
    chart_type : str
        관리도 유형: 'p', 'np', 'c', 'u'

    Returns
    -------
    dict
        매칭 결과. 키: 'subgroup', 'sample_size', 'defect_count'
        값: 매칭된 컬럼명 또는 None
    """
    result = {
        'subgroup': None,
        'sample_size': None,
        'defect_count': None,
    }

    columns = list(df.columns)
    col_lower_map = {str(c).strip().lower(): c for c in columns}
    matched_cols = set()  # 이미 매칭된 컬럼 추적 (중복 방지)

    def _find_best_match(keywords: list, exclude: set) -> Optional[str]:
        """키워드 리스트에서 가장 먼저 매칭되는 컬럼을 반환한다."""
        for kw in keywords:
            kw_lower = kw.lower()
            for col_lower, col_orig in col_lower_map.items():
                if col_orig in exclude:
                    continue
                # 정확 일치 우선
                if col_lower == kw_lower:
                    return col_orig
        for kw in keywords:
            kw_lower = kw.lower()
            if len(kw_lower) < 2:
                continue
            for col_lower, col_orig in col_lower_map.items():
                if col_orig in exclude:
                    continue
                # 부분 일치
                if kw_lower in col_lower:
                    return col_orig
        return None

    # 1단계: 부분군(번호) 컬럼 탐지
    subgroup_match = next((col_lower_map[key] for key in _SUBGROUP_KEYWORDS if key in col_lower_map), None)
    if subgroup_match:
        result['subgroup'] = subgroup_match
        matched_cols.add(subgroup_match)

    # 2단계: 불량/결점수 컬럼 탐지 (가장 중요)
    defect_match = _find_best_match(_DEFECT_COUNT_KEYWORDS, matched_cols)
    if defect_match:
        result['defect_count'] = defect_match
        matched_cols.add(defect_match)

    # 3단계: 시료수/검사수 컬럼 탐지 (p, np, u 차트에 필요)
    if chart_type in ('p', 'np', 'u'):
        size_match = _find_best_match(_SAMPLE_SIZE_KEYWORDS, matched_cols)
        if size_match:
            result['sample_size'] = size_match
            matched_cols.add(size_match)

    # 4단계: 매칭 안 된 경우 숫자 컬럼 기반 추정
    if subgroup_match is None:
        subgroup_match = _find_best_match(_SUBGROUP_KEYWORDS, matched_cols)
        if subgroup_match:
            result['subgroup'] = subgroup_match
            matched_cols.add(subgroup_match)

    numeric_cols = [
        c for c in columns
        if c not in matched_cols and pd.api.types.is_numeric_dtype(df[c])
    ]

    if result['defect_count'] is None and numeric_cols:
        # 첫 번째 숫자 컬럼을 불량/결점수로 추정
        result['defect_count'] = numeric_cols[0]
        matched_cols.add(numeric_cols[0])
        numeric_cols = numeric_cols[1:]

    if result['sample_size'] is None and chart_type in ('p', 'np', 'u'):
        if numeric_cols:
            # 두 번째 숫자 컬럼을 시료수로 추정
            result['sample_size'] = numeric_cols[0]
            matched_cols.add(numeric_cols[0])

    return result


# ════════════════════════════════════════════════════════════════
# 3. 관리도 계산 함수
# ════════════════════════════════════════════════════════════════

def _safe_sqrt(x: float) -> float:
    """음수 입력 방지 제곱근. 음수이면 0 반환."""
    if not np.isfinite(x) or x < 0:
        return 0.0
    return math.sqrt(x)


def _clamp_zero(arr_or_val):
    """LCL 값을 0 이상으로 클램핑한다. 배열 또는 스칼라 지원."""
    if isinstance(arr_or_val, np.ndarray):
        return np.maximum(0.0, arr_or_val)
    return max(0.0, float(arr_or_val)) if np.isfinite(arr_or_val) else 0.0


# ────────────────────────────────────────────────────────────────
# 3-1. p 관리도 (불량률 관리도)
# ────────────────────────────────────────────────────────────────

def calc_p_chart(defects: np.ndarray, sample_sizes: np.ndarray,
                 baseline_count: int = None, *, limit_method: str = "exact") -> dict:
    """p 관리도를 계산한다.

    불량률(proportion defective) 관리도.
    시료 크기가 부분군마다 다를 수 있으므로 관리한계가 가변이다.

    공식:
      p̄ = Σd / Σn  (기준구간 사용 시 baseline_count개)
      p_i = d_i / n_i
      UCL_i = p̄ + 3√(p̄(1-p̄)/n_i)
      LCL_i = max(0, p̄ - 3√(p̄(1-p̄)/n_i))

    Parameters
    ----------
    defects : np.ndarray
        각 부분군의 불량수 (정수 배열)
    sample_sizes : np.ndarray
        각 부분군의 시료수 (정수 배열)
    baseline_count : int | None
        기준구간 부분군 수. None이면 전체 사용.

    Returns
    -------
    dict
        chart_type, p_values, p_bar, ucl (array), lcl (array),
        sample_sizes, defects, subgroup_count,
        violations, stats
    """
    defects = np.asarray(defects, dtype=float)
    sample_sizes = np.asarray(sample_sizes, dtype=float)
    k = len(defects)  # 부분군 수

    # 기준구간 설정
    if baseline_count is not None and 0 < baseline_count < k:
        bl = int(baseline_count)
    else:
        bl = k

    # p̄ 계산 (기준구간)
    total_d_bl = np.sum(defects[:bl])
    total_n_bl = np.sum(sample_sizes[:bl])
    p_bar = total_d_bl / total_n_bl if total_n_bl > 0 else 0.0

    # 각 부분군 불량률
    p_values = np.where(sample_sizes > 0, defects / sample_sizes, 0.0)

    # 가변 관리한계
    lcl = np.full(k, p_bar)
    ucl = lcl.copy()
    positive = sample_sizes > 0
    sizes = sample_sizes[positive]
    if limit_method == "exact":
        unique_sizes, positions = np.unique(sizes, return_inverse=True)
        lo, hi = _discrete_limits(stats.binom, unique_sizes, p_bar)
        lcl[positive], ucl[positive] = lo[positions] / sizes, hi[positions] / sizes
    else:
        width = 3 * np.sqrt(p_bar * (1 - p_bar) / sizes)
        lcl[positive], ucl[positive] = np.maximum(0, p_bar - width), p_bar + width

    # 이탈 규칙 검출
    violations = detect_violations(p_values, ucl, lcl, p_bar)

    # 요약 통계
    avg_n = float(np.mean(sample_sizes))
    total_defects = int(np.sum(defects))
    total_samples = int(np.sum(sample_sizes))
    avg_sigma = _safe_sqrt(p_bar * (1 - p_bar) / avg_n) if avg_n > 0 and 0 < p_bar < 1 else 0.0

    stats_summary = {
        'p_bar': float(p_bar),
        'avg_sample_size': avg_n,
        'total_defects': total_defects,
        'total_samples': total_samples,
        'avg_ucl': float(np.mean(ucl)),
        'avg_lcl': float(np.mean(lcl)),
        'avg_sigma': avg_sigma,
        'baseline_count': bl,
    }

    return {
        'chart_type': 'p',
        'chart_type_name': 'p 관리도 (불량률)',
        'p_values': p_values,
        'p_bar': float(p_bar),
        'cl': float(p_bar),
        'ucl': ucl,
        'lcl': lcl,
        'sample_sizes': sample_sizes,
        'defects': defects,
        'subgroup_count': k,
        'baseline_count': bl,
        'violations': violations,
        'stats': stats_summary,
    }


# ────────────────────────────────────────────────────────────────
# 3-2. np 관리도 (불량수 관리도)
# ────────────────────────────────────────────────────────────────

def calc_np_chart(defects: np.ndarray, sample_size: int,
                  baseline_count: int = None, *, limit_method: str = "exact") -> dict:
    """np 관리도를 계산한다.

    불량수(number of defectives) 관리도.
    시료 크기가 일정해야 한다. 관리한계는 고정값.

    공식:
      np̄ = mean(d)  (기준구간)
      p̄ = np̄ / n
      UCL = np̄ + 3√(np̄(1 - p̄))
      LCL = max(0, np̄ - 3√(np̄(1 - p̄)))

    Parameters
    ----------
    defects : np.ndarray
        각 부분군의 불량수 (정수 배열)
    sample_size : int
        고정 시료 크기
    baseline_count : int | None
        기준구간 부분군 수. None이면 전체 사용.

    Returns
    -------
    dict
        chart_type, np_values, np_bar, ucl (scalar), lcl (scalar),
        sample_size, defects, subgroup_count, violations, stats
    """
    defects = np.asarray(defects, dtype=float)
    n = int(sample_size)
    k = len(defects)

    # 기준구간 설정
    if baseline_count is not None and 0 < baseline_count < k:
        bl = int(baseline_count)
    else:
        bl = k

    # np̄ 및 p̄ 계산 (기준구간)
    np_bar = float(np.mean(defects[:bl]))
    p_bar = np_bar / n if n > 0 else 0.0

    # 고정 관리한계
    if n > 0 and 0 < p_bar < 1:
        sigma = math.sqrt(np_bar * (1 - p_bar))
        ucl = np_bar + 3 * sigma
        lcl = max(0.0, np_bar - 3 * sigma)
    else:
        sigma = 0.0
        ucl = np_bar
        lcl = np_bar
    if limit_method == "exact":
        lcl, ucl = _discrete_limits(stats.binom, n, p_bar)

    # np 값 (= 불량수 그대로, 각 부분군의 불량수)
    np_values = defects.copy()

    # 이탈 규칙 검출 (고정 한계 → 배열로 확장)
    ucl_arr = np.full(k, ucl)
    lcl_arr = np.full(k, lcl)
    violations = detect_violations(np_values, ucl_arr, lcl_arr, np_bar)

    # 요약 통계
    total_defects = int(np.sum(defects))
    stats_summary = {
        'np_bar': float(np_bar),
        'p_bar': float(p_bar),
        'sample_size': n,
        'total_defects': total_defects,
        'total_samples': n * k,
        'sigma': sigma,
        'baseline_count': bl,
    }

    return {
        'chart_type': 'np',
        'chart_type_name': 'np 관리도 (불량수)',
        'np_values': np_values,
        'np_bar': float(np_bar),
        'p_bar': float(p_bar),
        'cl': float(np_bar),
        'ucl': float(ucl),
        'lcl': float(lcl),
        'sample_size': n,
        'sample_sizes': np.full(k, n),
        'defects': defects,
        'subgroup_count': k,
        'baseline_count': bl,
        'violations': violations,
        'stats': stats_summary,
    }


# ────────────────────────────────────────────────────────────────
# 3-3. c 관리도 (결점수 관리도)
# ────────────────────────────────────────────────────────────────

def calc_c_chart(defects: np.ndarray,
                 baseline_count: int = None, *, limit_method: str = "exact") -> dict:
    """c 관리도를 계산한다.

    결점수(count of nonconformities) 관리도.
    검사 단위가 일정해야 한다. 관리한계는 고정값.

    공식:
      c̄ = mean(c)  (기준구간)
      UCL = c̄ + 3√c̄
      LCL = max(0, c̄ - 3√c̄)

    Parameters
    ----------
    defects : np.ndarray
        각 부분군(검사 단위)의 결점수 (정수 배열)
    baseline_count : int | None
        기준구간 부분군 수. None이면 전체 사용.

    Returns
    -------
    dict
        chart_type, c_values, c_bar, ucl (scalar), lcl (scalar),
        defects, subgroup_count, violations, stats
    """
    defects = np.asarray(defects, dtype=float)
    k = len(defects)

    # 기준구간 설정
    if baseline_count is not None and 0 < baseline_count < k:
        bl = int(baseline_count)
    else:
        bl = k

    # c̄ 계산 (기준구간)
    c_bar = float(np.mean(defects[:bl]))

    # 고정 관리한계
    if c_bar > 0:
        sigma = math.sqrt(c_bar)
        ucl = c_bar + 3 * sigma
        lcl = max(0.0, c_bar - 3 * sigma)
    else:
        sigma = 0.0
        ucl = 0.0
        lcl = 0.0
    if limit_method == "exact":
        lcl, ucl = _discrete_limits(stats.poisson, c_bar)

    # c 값 (결점수 그대로)
    c_values = defects.copy()

    # 이탈 규칙 검출 (고정 한계 → 배열로 확장)
    ucl_arr = np.full(k, ucl)
    lcl_arr = np.full(k, lcl)
    violations = detect_violations(c_values, ucl_arr, lcl_arr, c_bar)

    # 요약 통계
    total_defects = int(np.sum(defects))
    stats_summary = {
        'c_bar': float(c_bar),
        'total_defects': total_defects,
        'subgroup_count': k,
        'sigma': sigma,
        'baseline_count': bl,
    }

    return {
        'chart_type': 'c',
        'chart_type_name': 'c 관리도 (결점수)',
        'c_values': c_values,
        'c_bar': float(c_bar),
        'cl': float(c_bar),
        'ucl': float(ucl),
        'lcl': float(lcl),
        'defects': defects,
        'sample_sizes': np.ones(k),
        'subgroup_count': k,
        'baseline_count': bl,
        'violations': violations,
        'stats': stats_summary,
    }


# ────────────────────────────────────────────────────────────────
# 3-4. u 관리도 (단위당 결점수 관리도)
# ────────────────────────────────────────────────────────────────

def calc_u_chart(defects: np.ndarray, inspection_units: np.ndarray,
                 baseline_count: int = None, *, limit_method: str = "exact") -> dict:
    """u 관리도를 계산한다.

    단위당 결점수(defects per unit) 관리도.
    검사 단위 수가 부분군마다 다를 수 있으므로 관리한계가 가변이다.

    공식:
      ū = Σc / Σn  (기준구간)
      u_i = c_i / n_i
      UCL_i = ū + 3√(ū / n_i)
      LCL_i = max(0, ū - 3√(ū / n_i))

    Parameters
    ----------
    defects : np.ndarray
        각 부분군의 결점수 (정수 배열)
    inspection_units : np.ndarray
        각 부분군의 검사 단위 수 (배열)
    baseline_count : int | None
        기준구간 부분군 수. None이면 전체 사용.

    Returns
    -------
    dict
        chart_type, u_values, u_bar, ucl (array), lcl (array),
        inspection_units, defects, subgroup_count, violations, stats
    """
    defects = np.asarray(defects, dtype=float)
    inspection_units = np.asarray(inspection_units, dtype=float)
    k = len(defects)

    # 기준구간 설정
    if baseline_count is not None and 0 < baseline_count < k:
        bl = int(baseline_count)
    else:
        bl = k

    # ū 계산 (기준구간)
    total_c_bl = np.sum(defects[:bl])
    total_n_bl = np.sum(inspection_units[:bl])
    u_bar = total_c_bl / total_n_bl if total_n_bl > 0 else 0.0

    # 각 부분군 단위당 결점수
    u_values = np.where(inspection_units > 0, defects / inspection_units, 0.0)

    # 가변 관리한계
    lcl = np.full(k, u_bar)
    ucl = lcl.copy()
    positive = inspection_units > 0
    sizes = inspection_units[positive]
    if limit_method == "exact":
        unique_sizes, positions = np.unique(sizes, return_inverse=True)
        lo, hi = _discrete_limits(stats.poisson, unique_sizes * u_bar)
        lcl[positive], ucl[positive] = lo[positions] / sizes, hi[positions] / sizes
    else:
        width = 3 * np.sqrt(u_bar / sizes)
        lcl[positive], ucl[positive] = np.maximum(0, u_bar - width), u_bar + width

    # 이탈 규칙 검출
    violations = detect_violations(u_values, ucl, lcl, u_bar)

    # 요약 통계
    avg_n = float(np.mean(inspection_units))
    total_defects = int(np.sum(defects))
    total_units = float(np.sum(inspection_units))
    avg_sigma = _safe_sqrt(u_bar / avg_n) if avg_n > 0 and u_bar > 0 else 0.0

    stats_summary = {
        'u_bar': float(u_bar),
        'avg_inspection_units': avg_n,
        'total_defects': total_defects,
        'total_units': total_units,
        'avg_ucl': float(np.mean(ucl)),
        'avg_lcl': float(np.mean(lcl)),
        'avg_sigma': avg_sigma,
        'baseline_count': bl,
    }

    return {
        'chart_type': 'u',
        'chart_type_name': 'u 관리도 (단위당 결점수)',
        'u_values': u_values,
        'u_bar': float(u_bar),
        'cl': float(u_bar),
        'ucl': ucl,
        'lcl': lcl,
        'inspection_units': inspection_units,
        'defects': defects,
        'subgroup_count': k,
        'baseline_count': bl,
        'violations': violations,
        'stats': stats_summary,
    }


# ════════════════════════════════════════════════════════════════
# 4. 이탈 규칙 검출 (T1 ~ T4)
# ════════════════════════════════════════════════════════════════

def detect_violations(values: np.ndarray, ucl: np.ndarray, lcl: np.ndarray,
                      cl: float) -> dict:
    """관리도 이탈 규칙 T1 ~ T4를 검출한다.

    ISO 7870-2 기반 간소화 규칙:
      T1: UCL 초과 또는 LCL 미만 (관리한계 이탈)
      T2: CL 같은쪽 9점 연속 (Run)
      T3: 6점 연속 단조증가 또는 단조감소 (Trend)
      T4: 14점 교대 상하 (Alternation)

    Parameters
    ----------
    values : np.ndarray
        관리도에 타점되는 값 (p, np, c, u 등)
    ucl : np.ndarray
        상한 관리한계 (배열 또는 스칼라를 배열화)
    lcl : np.ndarray
        하한 관리한계 (배열 또는 스칼라를 배열화)
    cl : float
        중심선 (p̄, np̄, c̄, ū 등)

    Returns
    -------
    dict
        'T1' ~ 'T4': 이탈 인덱스 set,
        'counts': {'T1': int, 'T2': int, 'T3': int, 'T4': int}
    """
    vals = np.asarray(values, dtype=float)
    k = len(vals)

    # 스칼라 → 배열 변환
    ucl_arr = np.full(k, ucl) if np.isscalar(ucl) or (isinstance(ucl, np.ndarray) and ucl.ndim == 0) else np.asarray(ucl, dtype=float)
    lcl_arr = np.full(k, lcl) if np.isscalar(lcl) or (isinstance(lcl, np.ndarray) and lcl.ndim == 0) else np.asarray(lcl, dtype=float)

    idxs: Dict[str, set] = {f'T{i}': set() for i in range(1, 5)}

    # ── T1: 관리한계 이탈 ──
    # UCL 초과 또는 LCL 미만
    for i in range(k):
        v = vals[i]
        if not np.isfinite(v):
            continue
        if np.isfinite(ucl_arr[i]) and v > ucl_arr[i]:
            idxs['T1'].add(i)
        elif np.isfinite(lcl_arr[i]) and v < lcl_arr[i]:
            idxs['T1'].add(i)

    # ── T2: CL 같은쪽 9점 연속 ──
    run_len = 0
    last_side = 0  # +1: 위, -1: 아래, 0: CL 위
    for i in range(k):
        if not np.isfinite(vals[i]):
            run_len = 0
            last_side = 0
            continue
        diff = vals[i] - cl
        if abs(diff) < 1e-15:
            # CL 위의 점은 연속 초기화하지 않고 현재 방향 유지
            # (일반적 해석: CL 정확히 위의 점은 제외)
            run_len = 0
            last_side = 0
            continue
        side = 1 if diff > 0 else -1
        if side == last_side:
            run_len += 1
        else:
            run_len = 1
            last_side = side
        if run_len >= 9:
            idxs['T2'].update(range(i - run_len + 1, i + 1))

    # ── T3: 6점 연속 단조증가/감소 ──
    if k >= 6:
        for i in range(k - 5):
            window = vals[i:i + 6]
            if not np.all(np.isfinite(window)):
                continue
            diffs = np.diff(window)
            if np.all(diffs > 0) or np.all(diffs < 0):
                idxs['T3'].update(range(i, i + 6))

    # ── T4: 14점 교대 ──
    if k >= 14:
        for i in range(k - 13):
            window = vals[i:i + 14]
            if not np.all(np.isfinite(window)):
                continue
            diffs = np.diff(window)
            if np.any(diffs == 0):
                continue
            # 인접한 차분의 부호가 번갈아야 함
            if np.all(diffs[:-1] * diffs[1:] < 0):
                idxs['T4'].update(range(i, i + 14))

    counts = {key: len(val) for key, val in idxs.items()}

    return {
        'T1': idxs['T1'],
        'T2': idxs['T2'],
        'T3': idxs['T3'],
        'T4': idxs['T4'],
        'counts': counts,
    }


def detect_ksq_rules_attribute(values: np.ndarray, cl: float, ucl: np.ndarray, lcl: np.ndarray,
                               *, source_positions: np.ndarray | None = None) -> dict:
    """계수형 관리도용 KSQ T1~T4 이탈 규칙 검출.

    비대칭성 및 가변 시그마 한계를 Z-Score로 환산하여 KSQ 규칙을 적용한다.
    """
    vals = np.asarray(values, dtype=float)
    k = len(vals)
    adjacent = (np.ones(max(0, k - 1), dtype=bool) if source_positions is None
                else np.diff(source_positions) == 1)

    ucl_arr = np.full(k, ucl) if np.isscalar(ucl) or (isinstance(ucl, np.ndarray) and ucl.ndim == 0) else np.asarray(ucl, dtype=float)
    lcl_arr = np.full(k, lcl) if np.isscalar(lcl) or (isinstance(lcl, np.ndarray) and lcl.ndim == 0) else np.asarray(lcl, dtype=float)

    z_scores = np.zeros(k, dtype=float)
    for i in range(k):
        x = vals[i]
        u = ucl_arr[i]
        l = lcl_arr[i]

        # ucl과 cl 사이의 거리를 3.0으로 나눈 값을 sigma로 쓴다. (양측 근사 대칭 가정)
        sig_up = (u - cl) / 3.0 if u > cl else 0.0
        sig_lo = (cl - l) / 3.0 if cl > l else 0.0

        sig = sig_up if sig_up > 0 else (sig_lo if sig_lo > 0 else 0.0)
        if sig > 0:
            z_scores[i] = (x - cl) / sig
        else:
            z_scores[i] = 0.0

    idxs = {f'T{i}': set() for i in range(1, 5)}
    counts = {f'T{i}': 0 for i in range(1, 5)}

    eps = 1e-6
    z_adj = np.where(np.abs(z_scores) < eps, 0.0, z_scores)
    sgn = np.sign(z_adj)

    # T1은 비대칭·이산 한계를 포함해 실제 표시 한계와 직접 비교한다.
    idxs['T1'].update(np.flatnonzero((vals > ucl_arr) | (vals < lcl_arr)).tolist())

    # T2: 같은쪽 9점 연속
    run_len = 0
    last_side = 0
    for i, si in enumerate(sgn):
        if i and not adjacent[i - 1]:
            run_len = 0
            last_side = 0
        if si == 0:
            run_len = 0
            last_side = 0
            continue
        if si == last_side:
            run_len += 1
        else:
            run_len = 1
            last_side = si
        if run_len >= 9:
            idxs['T2'].update(range(i - run_len + 1, i + 1))

    # T3: 6점 연속 단조증가/감소
    if k >= 6:
        for i in range(k - 5):
            if not adjacent[i:i + 5].all():
                continue
            w = vals[i:i + 6]
            d = np.diff(w)
            if np.all(d > 0) or np.all(d < 0):
                idxs['T3'].update(range(i, i + 6))

    # T4: 14점 교대
    if k >= 14:
        for i in range(k - 13):
            if not adjacent[i:i + 13].all():
                continue
            w = vals[i:i + 14]
            d = np.diff(w)
            if np.any(d == 0):
                continue
            if np.all(d[:-1] * d[1:] < 0):
                idxs['T4'].update(range(i, i + 14))

    for key in idxs:
        counts[key] = len(idxs[key])

    return {'counts': counts, 'idx': idxs, 'z_scores': z_scores}


# ════════════════════════════════════════════════════════════════
# 5. 계수형 공정능력 지수
# ════════════════════════════════════════════════════════════════

OPPORTUNITY_WARNING = '결점 수가 입력한 검사단위의 결점 기회수를 초과합니다. 검사단위당 기회수와 집계 기준을 확인하세요.'


def calc_attribute_capability(total_defects: int, total_units: int,
                               total_opportunities: int = None,
                               chart_type: str = None) -> dict:
    """계수형 공정능력 지수를 계산한다.

    지표:
      DPU  = 총 결점수 / 총 단위수
      DPO  = 총 결점수 / (총 단위수 × 기회수)  (기회수 지정 시)
      DPMO = DPO × 1,000,000
      Yield(수율):
        - p/np 관리도: (1 - 불량품률) × 100
        - c/u 관리도:  exp(-DPU) × 100 (포아송 무결점 수율)
        - 관리도 미지정 + 기회수 지정: (1 - DPO) × 100
      Sigma_Level = Φ⁻¹(1 - DPMO/1e6) + 1.5  (1.5σ 시프트 보정)
      PPM = DPU × 1,000,000

    Parameters
    ----------
    total_defects : int
        총 불량/결점 수
    total_units : int
        총 검사 단위 수 (시료 수)
    total_opportunities : int | None
        단위당 결점 기회 수 (공정능력 계산용, 선택)

    Returns
    -------
    dict
        DPU, DPO, DPMO, yield_pct, sigma_level, ppm 등
    """
    total_defects = int(total_defects)
    total_units = float(total_units)
    chart_type = str(chart_type or '').lower().strip()

    result = {
        'total_defects': total_defects,
        'total_units': total_units,
        'total_opportunities': total_opportunities,
        'opportunity_warning': '',
        'DPU': np.nan,
        'DPO': np.nan,
        'DPMO': np.nan,
        'yield_simple_pct': np.nan,
        'yield_poisson_pct': np.nan,
        'yield_pct': np.nan,
        'yield_basis': '',
        'sigma_level': np.nan,
        'process_z': np.nan,
        'PPM': np.nan,
    }

    if total_units <= 0:
        return result

    # DPU 계산
    dpu = total_defects / total_units
    result['DPU'] = dpu

    # PPM (DPU 기반)
    result['PPM'] = dpu * 1_000_000

    # 수율 계산
    # 단순 수율: (1 - DPU) × 100 (DPU ≤ 1인 경우만 유의미)
    yield_simple = max(0.0, (1.0 - dpu)) * 100.0
    result['yield_simple_pct'] = yield_simple

    # 포아송 기반 수율: exp(-DPU) × 100 (Throughput Yield, c/u 차트용)
    try:
        yield_poisson = math.exp(-dpu) * 100.0
    except OverflowError:
        yield_poisson = 0.0
    result['yield_poisson_pct'] = yield_poisson

    # DPO / DPMO (기회수 지정 시)
    if total_opportunities is not None and int(total_opportunities) > 0:
        opp = int(total_opportunities)
        result['total_opportunities'] = opp
        frac = total_defects / (total_units * opp)
        if frac > 1 and not math.isclose(frac, 1, rel_tol=1e-12):
            result['opportunity_warning'] = OPPORTUNITY_WARNING
        else:
            result['DPO'] = min(frac, 1)
            result['DPMO'] = result['DPO'] * 1_000_000
            if 0.0 < frac < 1.0:
                z_val = norm.ppf(1.0 - frac)
                result['process_z'] = float(z_val)
                result['sigma_level'] = float(z_val + 1.5)

    else:
        # p/np는 불량품률을 불량 확률로 볼 수 있어 PPM 기반 Z 환산이 가능하다.
        # c/u의 DPU는 한 단위에서 1을 넘을 수 있으므로 기회수 없이 Z로 환산하지 않는다.
        ppm_frac = result['PPM'] / 1_000_000.0
        if chart_type not in ('c', 'u') and 0.0 < ppm_frac < 1.0:
            z_val = norm.ppf(1.0 - ppm_frac)
            result['process_z'] = float(z_val)
            result['sigma_level'] = float(z_val + 1.5)

    # 관리도 데이터의 의미에 맞게 대표 수율을 선택한다.
    if chart_type in ('c', 'u'):
        result['yield_pct'] = yield_poisson
        result['yield_basis'] = '포아송 무결점 수율 exp(-DPU)'
    elif chart_type in ('p', 'np'):
        result['yield_pct'] = yield_simple
        result['yield_basis'] = '불량품 기준 수율 1-p'
    elif result['opportunity_warning']:
        result['yield_basis'] = '결점 기회수 확인 필요'
    elif total_opportunities is not None and int(total_opportunities) > 0:
        result['yield_pct'] = max(0.0, (1.0 - result['DPO'])) * 100.0
        result['yield_basis'] = '기회당 양품률 1-DPO'
    else:
        result['yield_pct'] = yield_simple
        result['yield_basis'] = '단순 수율 1-DPU'

    return result


# ════════════════════════════════════════════════════════════════
# 6. 포맷팅 헬퍼
# ════════════════════════════════════════════════════════════════

def fmt_value(v, decimals: int = 3, comma: bool = False) -> str:
    """숫자를 포맷팅한다. nan/inf/None은 '—'으로 표시.
    """
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    try:
        if comma:
            return f"{v:,.{decimals}f}"
        return f"{v:.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v, decimals: int = 2) -> str:
    """백분율(%) 포맷팅. nan/inf/None은 '—'으로 표시.
    """
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    try:
        return f"{v:.{decimals}f}%"
    except (TypeError, ValueError):
        return str(v)


def fmt_ppm(v) -> str:
    """PPM 포맷팅. 천단위 구분 쉼표 포함.
    """
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    try:
        return f"{v:,.0f} PPM"
    except (TypeError, ValueError):
        return str(v)


@day_guard
def run_attribute_analysis(df: pd.DataFrame, chart_type: str,
                             defect_col: str, sample_size_col: str = None,
                             fixed_sample_size: int = None,
                             opportunities: int = None,
                             baseline_subgroups: int = None,
                             chart_usage_mode: str = 'phase1',
                             preprocess_outliers: bool = False,
                             ksq_toggles: dict = None,
                             measurement_date_column: str = '', limit_method: str = "exact") -> dict:
    """계수형 관리도 전체 분석을 실행한다.

    데이터프레임에서 필요한 컬럼을 추출하고, 관리도 유형에 맞는
    계산 함수를 호출한 뒤, 공정능력 지수까지 포함한 결과를 반환한다.
    """
    chart_type = chart_type.lower().strip()
    if chart_type not in ('p', 'np', 'c', 'u'):
        raise ValueError(f"지원하지 않는 관리도 유형: '{chart_type}'. 'p', 'np', 'c', 'u' 중 선택하세요.")
    if limit_method not in LIMIT_METHODS:
        raise ValueError("관리한계 계산 방식을 확인하세요.")
    if defect_col not in df.columns:
        raise ValueError(f"불량수/결점수 열을 찾을 수 없습니다: {defect_col}")
    if sample_size_col and sample_size_col == defect_col:
        raise ValueError("불량수/결점수 열과 검사수/검사단위 열은 서로 달라야 합니다.")

    ksq_toggles = ksq_toggles or {f'T{i}': True for i in range(1, 5)}

    # 1. 결측치 제거
    subset_cols = [defect_col]
    if sample_size_col is not None and sample_size_col in df.columns:
        subset_cols.append(sample_size_col)
    valid_rows = df[subset_cols].notna().all(axis=1).to_numpy()
    source_positions = np.flatnonzero(valid_rows)
    df_clean = df.iloc[source_positions]

    original_len = len(df_clean)
    if original_len == 0:
        raise ValueError("유효한 데이터가 비어 있습니다.")

    # 2. IQR 의심점 탐지. SPC의 특별원인 신호를 숨기지 않도록 제거하지 않는다.
    outlier_info = {
        "enabled": bool(preprocess_outliers),
        "method": "IQR (표시만, 분석에 포함)",
        "lower": np.nan,
        "upper": np.nan,
        "count": 0,
        "items": [],
    }
    if preprocess_outliers and original_len > 4:
        vals = df_clean[defect_col].values.astype(float)
        q1, q3 = np.percentile(vals, [25, 75])
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_info.update({
            "q1": float(q1),
            "q3": float(q3),
            "iqr": float(iqr),
            "lower": float(lower),
            "upper": float(upper)
        })

        for position, (idx, row) in enumerate(df_clean.iterrows(), start=1):
            val = float(row[defect_col])
            if val < lower or val > upper:
                outlier_info["items"].append({
                    "index": position,
                    "source_index": str(idx),
                    "value": val
                })

        outlier_info["count"] = len(outlier_info["items"])

    defects = df_clean[defect_col].values.astype(float)

    # ── 시료수/검사단위 추출 ──
    sample_sizes = None
    if sample_size_col is not None and sample_size_col in df_clean.columns:
        sample_sizes = df_clean[sample_size_col].values.astype(float)

    # ── 계수 데이터 유효성 검증 ──
    if not np.all(np.isfinite(defects)):
        raise ValueError("불량수/결점수에는 유한한 숫자만 입력할 수 있습니다.")
    if np.any(defects < 0):
        raise ValueError("불량수/결점수는 0 이상이어야 합니다.")
    if not np.all(defects == np.round(defects)):
        raise ValueError("불량수/결점수는 정수여야 합니다.")

    if sample_sizes is not None:
        if not np.all(np.isfinite(sample_sizes)):
            raise ValueError("검사수/검사단위에는 유한한 숫자만 입력할 수 있습니다.")
        if np.any(sample_sizes <= 0):
            raise ValueError("검사수/검사단위는 0보다 커야 합니다.")
        if chart_type in ('p', 'np') and not np.all(sample_sizes == np.round(sample_sizes)):
            raise ValueError("p/np 관리도의 검사수는 정수여야 합니다.")

    if chart_type in ('p', 'np') and sample_sizes is not None and np.any(defects > sample_sizes):
        raise ValueError("p/np 관리도에서는 불량수가 검사수보다 클 수 없습니다.")

    # ── 날짜 라벨 추출 ──
    x_labels = []
    r_labels = []
    if measurement_date_column and measurement_date_column in df_clean.columns:
        for position, value in zip(source_positions, df_clean[measurement_date_column]):
            label = '' if pd.isna(value) else str(value).strip()
            x_labels.append(label or f"원본 데이터 {position + 1}행")
        r_labels = list(x_labels)

    # ── 관리용/해석용 모드 ──
    control_phase = "phase2" if str(chart_usage_mode).lower() == "phase2" else "phase1"
    control_phase_label = "관리용" if control_phase == "phase2" else "해석용"

    # 기준구간 설정
    bl = None
    if control_phase == "phase2":
        bl = DEFAULT_BASELINE_SUBGROUPS if baseline_subgroups is None else int(baseline_subgroups)
        if bl < 1:
            raise ValueError("기준 부분군 수는 1개 이상이어야 합니다.")
        if len(defects) < bl:
            raise ValueError(f"Phase II 기준구간에 유효 부분군이 {bl}개 필요합니다. 현재 {len(defects)}개입니다.")

    # ── 관리도 유형별 계산 ──
    chart_result = {}

    if chart_type == 'p':
        if sample_sizes is None:
            fixed_n = float(fixed_sample_size) if fixed_sample_size is not None else 0.0
            if fixed_n > 0 and fixed_n.is_integer():
                sample_sizes = np.full(len(defects), int(round(fixed_n)), dtype=float)
            else:
                raise ValueError("p 관리도에는 양의 정수 검사수 컬럼 또는 고정 시료 크기가 필요합니다.")
        if np.any(defects > sample_sizes):
            raise ValueError("p 관리도에서는 불량수가 검사수보다 클 수 없습니다.")
        chart_result = calc_p_chart(defects, sample_sizes, baseline_count=bl, limit_method=limit_method)

    elif chart_type == 'np':
        if sample_sizes is not None and len(sample_sizes) > 0:
            unique_sizes = np.unique(sample_sizes)
            if len(unique_sizes) != 1:
                raise ValueError("np 관리도는 모든 부분군의 검사수가 동일해야 합니다. 검사수가 다르면 p 관리도를 사용하세요.")
            fixed_sample_size = int(unique_sizes[0])
        else:
            fixed_n = float(fixed_sample_size) if fixed_sample_size is not None else 0.0
            if fixed_n <= 0 or not fixed_n.is_integer():
                raise ValueError("np 관리도에는 양의 정수 고정 시료 크기가 필요합니다.")
            fixed_sample_size = int(round(fixed_n))
        if np.any(defects > int(fixed_sample_size)):
            raise ValueError("np 관리도에서는 불량수가 고정 시료수보다 클 수 없습니다.")
        sample_sizes = np.full(len(defects), int(fixed_sample_size), dtype=float)
        chart_result = calc_np_chart(defects, int(fixed_sample_size), baseline_count=bl, limit_method=limit_method)

    elif chart_type == 'c':
        chart_result = calc_c_chart(defects, baseline_count=bl, limit_method=limit_method)

    elif chart_type == 'u':
        if sample_sizes is None:
            fixed_n = float(fixed_sample_size) if fixed_sample_size is not None else 0.0
            if np.isfinite(fixed_n) and fixed_n > 0:
                sample_sizes = np.full(len(defects), fixed_n, dtype=float)
            else:
                raise ValueError("u 관리도에는 검사 단위수 컬럼 또는 고정 단위 크기가 필요합니다.")
        chart_result = calc_u_chart(defects, sample_sizes, baseline_count=bl, limit_method=limit_method)

    # ── KSQ T1~T4 재계산 및 필터링 ──
    cl_val = chart_result.get('cl', 0.0)
    ucl_val = chart_result.get('ucl', 0.0)
    lcl_val = chart_result.get('lcl', 0.0)

    values_key = f"{chart_type}_values"
    vals_for_ksq = chart_result.get(values_key, defects)

    ksq_det = detect_ksq_rules_attribute(vals_for_ksq, cl_val, ucl_val, lcl_val, source_positions=source_positions)

    # toggles 필터링 적용
    filtered_idx = {}
    filtered_counts = {}
    for rk in ['T1', 'T2', 'T3', 'T4']:
        if ksq_toggles.get(rk, True):
            filtered_idx[rk] = ksq_det['idx'].get(rk, set())
            filtered_counts[rk] = len(filtered_idx[rk])
        else:
            filtered_idx[rk] = set()
            filtered_counts[rk] = 0

    violations = {
        **filtered_idx,
        'counts': filtered_counts,
        'z_scores': ksq_det['z_scores']
    }
    chart_result['violations'] = violations

    # ── 공정능력 지수 계산 ──
    total_defects = int(np.sum(defects))

    # 총 단위수 결정
    if chart_type in ('p', 'np'):
        if sample_sizes is not None:
            total_units = int(np.sum(sample_sizes))
        else:
            total_units = int(fixed_sample_size) * len(defects) if fixed_sample_size else len(defects)
    elif chart_type == 'u':
        total_units = float(np.sum(sample_sizes)) if sample_sizes is not None else len(defects)
    else:  # c 차트
        total_units = len(defects)

    opp = int(opportunities) if opportunities is not None and int(opportunities) > 0 else None
    invalid_opportunities = False
    if opp is not None and chart_type in ('c', 'u'):
        group_units = chart_result['inspection_units'] if chart_type == 'u' else 1
        capacity = np.asarray(group_units, dtype=float) * opp
        invalid_opportunities = bool(np.any((defects > capacity) & ~np.isclose(defects, capacity, rtol=1e-12, atol=0)))
    capability = calc_attribute_capability(
        total_defects, total_units, total_opportunities=None if invalid_opportunities else opp, chart_type=chart_type
    )
    if invalid_opportunities:
        capability.update(total_opportunities=opp, opportunity_warning=OPPORTUNITY_WARNING)

    # ── 이탈 요약 ──
    violated_point_indices = set()
    for rule in ('T1', 'T2', 'T3', 'T4'):
        violated_point_indices.update(violations.get(rule, set()))
    total_violation_points = len(violated_point_indices)
    violation_summary = []
    rule_desc = {
        'T1': '관리한계 이탈 (1점)',
        'T2': '연속 편향 (연속 9점 이상)',
        'T3': '추세 (연속 6점 증가/감소)',
        'T4': '반복 패턴 (연속 14점 교대)',
    }
    for rule in ['T1', 'T2', 'T3', 'T4']:
        cnt = violations.get('counts', {}).get(rule, 0)
        if cnt > 0:
            violation_summary.append(f"{rule}({rule_desc.get(rule, '')}): {cnt}개 점")

    # ── 기준구간/감시구간 분리 통계 ──
    baseline_display_count = chart_result.get('baseline_count', len(defects))
    if control_phase == 'phase2' and bl is not None:
        baseline_display_count = bl
        limit_reference_label = f"부분군 1~{baseline_display_count}"
        limit_reference_desc = f"초기 {limit_reference_label} 기준 고정 관리한계"
    else:
        baseline_display_count = len(defects)
        limit_reference_label = "전체 데이터"
        limit_reference_desc = "전체 데이터 기준 시험 관리한계"

    # ── 분석 메타 정보 ──
    analysis_info = {
        'chart_type': chart_type,
        'chart_type_name': chart_result.get('chart_type_name', ''),
        'control_phase': control_phase,
        'control_phase_label': control_phase_label,
        'baseline_display_count': baseline_display_count,
        'limit_reference_label': limit_reference_label,
        'limit_reference_desc': limit_reference_desc,
        'limit_method': limit_method,
        'limit_method_label': (f"{LIMIT_METHODS[limit_method]} · "
                               + ("이항분포" if chart_type in ('p', 'np') else "포아송분포")
                               + f" · 양측 명목 α {2 * LIMIT_TAIL:.2%}" if limit_method == "exact"
                               else LIMIT_METHODS[limit_method]),
        'subgroup_count': len(defects),
        'excluded_missing_groups': len(df) - len(df_clean),
        'total_defects': total_defects,
        'total_units': total_units,
        'total_inspected': total_units,
        'total_violation_points': total_violation_points,
        'violation_summary': violation_summary,
        'defect_col': defect_col,
        'sample_size_col': sample_size_col,
        'fixed_sample_size': fixed_sample_size,
        'opportunities': opp,
    }

    # 'values' 키 통일화
    values_key = f"{chart_type}_values"
    values = chart_result.get(values_key, chart_result.get('defects', []))

    flat_result = {
        **chart_result,
        **analysis_info,
        'values': values,
        'capability': capability,
        'outlier_info': outlier_info,
        'x_labels': x_labels,
        'r_labels': r_labels,
    }
    return flat_result
