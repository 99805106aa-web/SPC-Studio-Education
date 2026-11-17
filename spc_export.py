# -*- coding: utf-8 -*-
"""
spc_export.py
=============
Excel / PDF 내보내기 모듈
원본: 공정능력_관리도_Rev26.py _export_selection / _export_all_pdf 에서 추출
"""

import io
from spc_access import day_guard
from spc_core import WITHIN_CAPABILITY_NOTICE, assess_process, fmt_ppm
import math
import base64
import numpy as np
import pandas as pd
from spc_normality import diagnostics_html
from typing import Any, Dict, List, Optional, Tuple

PROCESS_INFO_LABELS = ["공정명", "라인/호기", "설비명", "제품명", "품번", "Lot/Batch", "작업자/교대", "비고"]
SUMMARY_HIDDEN_COLUMNS = {
    "원본N",
    "분석N",
    "신뢰도 상태",
    "신뢰도 사유",
    "원본평균",
    "원본σ(전체)",
    "한계기준",
    "기준구간상태",
    "기준구간사유",
    "능력기준",
    "비고",
    "성능 해석 안내",
    "능력 적용 사유",
    "분포 모드", "기준분포 상태", "성능분포 상태",
    *{f'{name}.{method}' for name in ('Cp', 'Cpk', 'Pp', 'Ppk') for method in ('G', 'Z')},
}


def _outlier_setting_text(meta: Dict[str, Any]) -> str:
    if not meta.get("preprocess_outliers"):
        return "사용 안 함"
    if str(meta.get("outlier_method", "iqr")).lower() == "quantile":
        return "사용 (분위수 범위, 꼬리 0.1, Q=3)"
    return "사용 (IQR)"


def _process_info_rows(meta: Optional[Dict[str, Any]]) -> List[tuple]:
    if not meta:
        return []
    info = meta.get("process_info", {}) or {}
    if not isinstance(info, dict):
        return []
    rows = []
    for label in PROCESS_INFO_LABELS:
        value = str(info.get(label, "") or "").strip()
        if value:
            rows.append((label, value))
    return rows


def _chart_mode_label(meta: Optional[Dict[str, Any]]) -> str:
    if not meta:
        return ""
    label = str(meta.get("chart_usage_mode_label", "") or "").strip()
    if label:
        return label
    mode = str(meta.get("chart_usage_mode", "") or "").strip()
    return {
        "phase1": "해석용 관리도",
        "phase2": "관리용 관리도",
    }.get(mode, mode)


def _summary_visible_df(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df is None or summary_df.empty:
        return summary_df
    summary_df = summary_df.copy()
    if "신뢰도 상태" in summary_df.columns or "신뢰도 사유" in summary_df.columns:
        status = summary_df.get(
            "신뢰도 상태",
            pd.Series([""] * len(summary_df), index=summary_df.index),
        ).fillna("").astype(str).str.strip()
        reason = summary_df.get(
            "신뢰도 사유",
            pd.Series([""] * len(summary_df), index=summary_df.index),
        ).fillna("").astype(str).str.strip()
        combined = [
            f"{s} · {r}" if s and r else (s or r)
            for s, r in zip(status, reason)
        ]
        insert_at = summary_df.columns.get_loc("신뢰도 상태") if "신뢰도 상태" in summary_df.columns else min(3, len(summary_df.columns))
        if "신뢰도" in summary_df.columns:
            summary_df["신뢰도"] = combined
        else:
            summary_df.insert(insert_at, "신뢰도", combined)
    if "군내/전체 지수 비율(Cwk/Ppk)" in summary_df.columns:
        summary_df = summary_df.rename(columns={"군내/전체 지수 비율(Cwk/Ppk)": "군내/전체 지수 비율"})
    drop_cols = [c for c in SUMMARY_HIDDEN_COLUMNS if c in summary_df.columns]
    return summary_df.drop(columns=drop_cols) if drop_cols else summary_df


_REPORT_FILL = {
    "good": "#dcfce7",
    "warn": "#fef3c7",
    "bad": "#fee2e2",
    "na": "#ffffff",
}
_REPORT_TEXT = {
    "good": "#166534",
    "warn": "#92400e",
    "bad": "#991b1b",
    "na": "#64748b",
}


def _report_fmt_value(v, d: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        if np.isinf(v):
            return '∞' if v > 0 else '−∞'
        if not np.isfinite(v):
            return "—"
        return f"{float(v):.{d}f}"
    return str(v)


def _report_fmt_ppm_int(v) -> str:
    return fmt_ppm(v)


def _report_metric_cls(v, it=None) -> str:
    try:
        fv = float(v)
        if not np.isfinite(fv):
            return "na"
        from spc_policy import limits
        warning, required = limits(it)
        if fv < warning:
            return "bad"
        if fv < required:
            return "warn"
        return "good"
    except Exception:
        return "na"


def _report_summary_spec(summary_df: pd.DataFrame):
    df = _summary_visible_df(summary_df).copy()
    cap_cols = [c for c in ["Cp", "Cpk", "Cpm", "Cpmk"] if c in df.columns]
    ppm_cols = [c for c in df.columns if "PPM" in str(c)]
    cnt_cols = [c for c in df.columns if ("이탈" in str(c) or "위반" in str(c))]
    return df, cap_cols, ppm_cols, cnt_cols


def _report_cell_display(col, val, cap_cols, ppm_cols, cnt_cols, it=None) -> Tuple[str, str]:
    if val is None or (isinstance(val, float) and not np.isfinite(val)):
        return "—", "na"
    sval = str(val)
    cls = ""
    if col == '정규성 p값':
        from spc_normality import pvalue_text
        return pvalue_text(float(val)), ''
    try:
        fv = float(str(val).replace(",", ""))
        if col in cap_cols:
            cls = _report_metric_cls(fv, it)
        elif col in ppm_cols:
            sval = _report_fmt_ppm_int(fv)
            if fv > 50000:
                cls = "bad"
            elif fv > 10000:
                cls = "warn"
        elif col in cnt_cols and fv > 0:
            cls = "bad"
    except Exception:
        pass
    return sval, cls


def _report_meta_items(analysis_meta: Optional[Dict[str, Any]], now: str) -> List[Tuple[str, Any]]:
    meta = analysis_meta or {}
    return [
        ("데이터", meta.get("data_name", "")),
        *_process_info_rows(meta),
        ("분석시각", meta.get("analyzed_at", now)),
        ("n", meta.get("n", "")),
        ("관리도 모드", _chart_mode_label(meta)),
        ("KSQ", meta.get("ksq_rules", "")),
        ("계산 버전", meta.get("calculation_version", "")),
        ("분석 ID", meta.get("analysis_id", "")[:16]),
    ]


def _report_variable_detail(it: Dict[str, Any]) -> Dict[str, Any]:
    is_xmr = bool(it.get("is_xmr", False))
    x_lab = "X" if is_xmr else "Xbar"
    disp_kind_raw = str(it.get("dispersion_kind", "MR" if is_xmr else "R"))
    try:
        n_subgroup = int(it.get("n", 0))
    except Exception:
        n_subgroup = 0
    r_lcl_applicable = not (disp_kind_raw == "R" and n_subgroup <= 6)
    if it.get('distribution_mode') == 'nonnormal':
        r_lcl_applicable = n_subgroup > 1

    cpk = it.get("cpk", np.nan)
    spec_v = int(it.get("spec_violation_count", 0))
    ctrl_v = int(it.get("x_ctrl_violation_count", 0) + it.get("r_ctrl_violation_count", 0))
    assessment = assess_process(it)
    risk_cls = {"high": "bad", "medium": "warn", "unknown": "warn", "low": "good"}[assessment['level']]
    if risk_cls == 'good' and it.get('capability_status') != '적용 가능':
        risk_cls = 'warn'

    ppk = it.get("ppk", np.nan)
    metric_items = [
        ("N", _report_fmt_value(len(it.get("series", [])), 0), ""),
        ("Cpk", _report_fmt_value(cpk) if not np.isnan(cpk) else str(it.get("capability_status", "재분석 필요")), _report_metric_cls(cpk, it)),
        ("PPM(Cwk, 진단용)", _report_fmt_ppm_int(it.get("ppm_within", np.nan)), ""),
        ("Ppk (모델 참고)", _report_fmt_value(ppk), ""),
        ("PPM(Ppk, 모델 참고)", _report_fmt_ppm_int(it.get("ppm_ppk", np.nan)), ""),
        ("Cpm", _report_fmt_value(it.get("cpm", np.nan)), _report_metric_cls(it.get("cpm", np.nan), it)),
        ("Cpmk", _report_fmt_value(it.get("cpmk", np.nan)), _report_metric_cls(it.get("cpmk", np.nan), it)),
        ("Cw (MR 참고)" if it.get('within_estimator')=='moving_range' and not it.get('box_cox_applied') else "Cw (진단용)", _report_fmt_value(it.get("cw", np.nan)), ""),
        ("Cwk (MR 참고)" if it.get('within_estimator')=='moving_range' and not it.get('box_cox_applied') else "Cwk (진단용)", _report_fmt_value(it.get("cwk", np.nan)), ""),
        ("규격 이탈", f"{spec_v:,}", "bad" if spec_v > 0 else "good"),
        ("관리 이탈", f"{ctrl_v:,}", "bad" if ctrl_v > 0 else "good"),
        ("USL (원단위)" if it.get('box_cox_applied') else "USL", _report_fmt_value(it.get("original_usl" if it.get('box_cox_applied') else "usl", np.nan)), ""),
        ("LSL (원단위)" if it.get('box_cox_applied') else "LSL", _report_fmt_value(it.get("original_lsl" if it.get('box_cox_applied') else "lsl", np.nan)), ""),
        ("목표치(T)", _report_fmt_value(it.get("target", np.nan)), ""),
        (f"{x_lab}-UCL", _report_fmt_value(it.get("ucl", np.nan)), ""),
        (f"{x_lab}-LCL", _report_fmt_value(it.get("lcl", np.nan)), ""),
        (f"{disp_kind_raw}-UCL", _report_fmt_value(it.get("r_ucl", np.nan)), ""),
    ]
    if r_lcl_applicable:
        metric_items.append((f"{disp_kind_raw}-LCL", _report_fmt_value(it.get("r_lcl", np.nan)), ""))

    performance_note = str(it.get('performance_note', '이전 결과: 재분석 필요'))
    performance_note += ' · ' + it.get('study_conclusion', '')
    cfg = it.get('assessment_settings', {})
    performance_note += f" · 고객 요구 {cfg.get('required_limit', 1.33):.2f} / 개선 경계 {cfg.get('warning_limit', 1.):.2f}"
    if it.get('capability_confidence'):
        ci = it['capability_confidence']
        performance_note += f" · Ppk 단측 {ci['confidence']:.1%} 신뢰하한 {ci['ppk_lower']:.4f} ({ci['method']})"
    if it.get('box_cox_applied'):
        metric_items = [(key+'.G' if key in ('Cpk', 'Ppk (모델 참고)') else key, value, cls)
                        for key,value,cls in metric_items]
    if it.get('distribution_mode') == 'nonnormal':
        method = it.get('capability_method', 'G')
        metric_items = [(key + '.' + method if key in ('Cpk', 'Ppk (모델 참고)') else key, val, cls)
                        for key, val, cls in metric_items]
        model = it.get('distribution_model') or it.get('chart_model') or {}
        params_text = ', '.join(f'{value:.6g}' for value in model.get('params', []))
        model_note = f"분포 {model.get('name', '미확인')} · 모수 [{params_text}] · 적합 N {model.get('fit_n', '—')} / 검정 N {model.get('test_n', '—')} · AD p={model.get('pvalue', '—')}"
        chart_model = it.get('chart_model') or {}
        model_note += (f" · 위치 분포 {chart_model.get('name', '미확인')} · 위치 적합 N {chart_model.get('fit_n', '—')}"
                       f" · 기준 {it.get('limit_reference_label')} · {it.get('chart_limit_method', '위치 분포 확인 필요')}"
                       f" · {it.get('dispersion_limit_method', '')} · 자연하한 {(it.get('distribution_settings') or {}).get('natural_lower')}")
        comparisons = ' / '.join(f"{m}: Cp={_report_fmt_value(v['cp'])}, Cpk={_report_fmt_value(v['cpk'])}, Pp={_report_fmt_value(v['pp'])}, Ppk={_report_fmt_value(v['ppk'])}"
                                 for m, v in it.get('capability_methods', {}).items())
        performance_note += ' ' + model_note + ' · ' + comparisons + ' · ' + it.get('pearson_rules_note', '')

    return {
        "column": str(it.get("column", "")),
        "risk_cls": risk_cls,
        "risk_label": {"bad": "위험", "warn": "주의", "good": "양호"}[risk_cls],
        "norm_msg": str(it.get("norm_msg", "")),
        "within_note": str(it.get("within_capability_note", "이전 분석 결과: 재분석 필요")),
        "capability_note": str(it.get("capability_status", "재분석 필요")) + ": " + "; ".join(it.get("capability_reasons", [])),
        "performance_note": performance_note,
        "metric_items": metric_items,
    }


@day_guard
def export_summary_excel(
    results: List[Dict[str, Any]],
    summary_df: pd.DataFrame,
    analysis_meta: Optional[Dict[str, Any]] = None,
) -> bytes:
    """선별 요약 Excel 파일 생성 (숫자는 실제 숫자값으로 저장 + 하이라이트)."""
    analysis_meta = {**summary_df.attrs.get('audit', {}), **(analysis_meta or {})}
    from openpyxl import Workbook
    from openpyxl.styles import Border, Side, PatternFill, Font, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # 공통 스타일
    header_fill = PatternFill("solid", fgColor="1E3A8A")
    header_font = Font(bold=True, color="FFFFFF", size=10)
    thin = Side(style='thin', color='B0B8C8')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center', wrap_text=False)

    # 하이라이트 팔레트
    fill_warn = PatternFill("solid", fgColor="FFF4CC")
    fill_crit = PatternFill("solid", fgColor="FCE4D6")
    fill_good = PatternFill("solid", fgColor="E2F0D9")
    fill_count = PatternFill("solid", fgColor="F8D7DA")
    fill_review = PatternFill("solid", fgColor="FCE4D6")
    fill_caution = PatternFill("solid", fgColor="FFF4CC")
    font_warn = Font(bold=True, color="9C5700")
    font_crit = Font(bold=True, color="9C0006")
    font_good = Font(bold=True, color="1D6F42")
    font_count = Font(bold=True, color="9C0006")

    def _nv(v):
        if v is None:
            return None
        if isinstance(v, (int, np.integer)):
            return int(v)
        if isinstance(v, (float, np.floating)):
            return float(v) if np.isfinite(v) else None
        s = str(v).strip()
        if s in {"", "—", "-", "None", "none", "nan", "NaN", "N/A", "n/a"}:
            return None
        s = s.replace(",", "")
        try:
            return float(s)
        except Exception:
            return None

    def _is_int_like(name: str) -> bool:
        n = str(name)
        return (
            n == "N"
            or "PPM" in n
            or "이탈" in n
            or "위반" in n
            or n.startswith("KSQ T")
        )

    capability_names = {"Cp", "Cpk", "Cpm", "Cpmk"}

    def _style_value_cell(cell, col_name: str):
        v = cell.value
        if not isinstance(v, (int, float)):
            return
        fv = float(v)
        if col_name in capability_names:
            from spc_policy import limits
            warning, required = limits(results[0] if results else None)
            if fv < warning:
                cell.fill = fill_crit
                cell.font = font_crit
            elif fv < required:
                cell.fill = fill_warn
                cell.font = font_warn
            else:
                cell.fill = fill_good
                cell.font = font_good
        elif "PPM" in str(col_name):
            if fv > 50000:
                cell.fill = fill_crit
                cell.font = font_crit
            elif fv > 10000:
                cell.fill = fill_warn
                cell.font = font_warn
        elif ("이탈" in str(col_name) or "위반" in str(col_name)) and fv > 0:
            cell.fill = fill_count
            cell.font = font_count

    def _auto_width(sheet, max_width: int = 34):
        for col_idx, col in enumerate(sheet.columns, 1):
            max_len = 0
            for c in col:
                try:
                    max_len = max(max_len, len(str(c.value or "")))
                except Exception:
                    pass
            sheet.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 9), max_width)

    def _meta_rows(meta: Optional[Dict[str, Any]]) -> List[tuple]:
        if not meta:
            return []
        preview = meta.get("preview", {}) or {}
        return [
            ("데이터 파일", meta.get("data_name", "")),
            ("규격 파일", meta.get("spec_name", "") or "없음"),
            *_process_info_rows(meta),
            ("분석 시각", meta.get("analyzed_at", "")),
            ("계산 버전", meta.get("calculation_version", "")),
            ("분석 ID", meta.get("analysis_id", "")),
            ("데이터 SHA256", meta.get("data_sha256", "")),
            ("규격 SHA256", meta.get("spec_sha256", "")),
            ("데이터 구조", meta.get("data_layout_mode", "")),
            ("시료 크기 n", meta.get("n", "")),
            ("관리도 모드", _chart_mode_label(meta)),
            ("기준구간", meta.get("baseline_subgroups", "")),
            ("불완전 부분군", "관리도/능력 제외, 원본 규격이탈 포함"),
            ("실측 PPM/규격이탈 기준", "전처리 전 원본 유효 측정값"),
            ("이상치 제거", _outlier_setting_text(meta)),
            ("Box-Cox", meta.get("box_cox_transform", "")),
            ("KSQ 규칙", meta.get("ksq_rules", "")),
            ("행/수치형 변수", f"{preview.get('rows', 0)}행 / {preview.get('numeric_count', 0)}개"),
            ("결측 수", preview.get("missing_total", 0)),
            ("규격 매칭률", f"{preview.get('match_rate') * 100:.0f}%" if isinstance(preview.get("match_rate"), float) else "규격 없음"),
        ]

    meta_rows = _meta_rows(analysis_meta)
    if meta_rows:
        ws_meta = wb.active
        ws_meta.title = "분석 조건"
        ws_meta.append(["항목", "값"])
        for c in ws_meta[1]:
            c.fill = header_fill
            c.font = header_font
            c.alignment = center
            c.border = border
        for row in meta_rows:
            ws_meta.append(list(row))
        ws_meta.append(["해석 주의", "전처리/Box-Cox가 적용된 경우 능력지수는 원본 기준과 직접 비교하지 말고 '능력기준'을 함께 확인하세요."])
        for row in ws_meta.iter_rows(min_row=2):
            for c in row:
                c.border = border
                c.alignment = left
        _auto_width(ws_meta)
        ws = wb.create_sheet("선별 요약")
    else:
        ws = wb.active
        ws.title = "선별 요약"

    # Sheet 1/2: 선별 요약
    summary_export_df = _summary_visible_df(summary_df)
    headers = list(summary_export_df.columns)
    ws.append(headers)
    from openpyxl.comments import Comment
    for label in ("Cw", "Cwk"):
        if label in headers:
            ws.cell(1, headers.index(label) + 1).comment = Comment(WITHIN_CAPABILITY_NOTICE, "SPC")
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        c.border = border

    # 숫자형 컬럼 추정(요약 DF 내 문자열 숫자 -> 실제 숫자로 변환)
    numeric_cols = set()
    for col in headers:
        vals = summary_export_df[col].tolist()
        parsed = [_nv(v) for v in vals[:80]]
        n_ok = sum(v is not None for v in parsed)
        n_tot = sum((str(v).strip() != "") for v in vals[:80])
        if n_tot > 0 and n_ok / n_tot >= 0.65:
            numeric_cols.add(col)

    for row_data in summary_export_df.itertuples(index=False, name=None):
        out = []
        for col_name, v in zip(headers, row_data):
            if col_name in numeric_cols:
                out.append(_nv(v))
            else:
                out.append("" if v is None else str(v))
        ws.append(out)

    var_col_idx = headers.index("변수") + 1 if "변수" in headers else None
    for row_idx in range(2, ws.max_row + 1):
        for col_idx, col_name in enumerate(headers, 1):
            c = ws.cell(row=row_idx, column=col_idx)
            c.border = border
            c.alignment = left if (var_col_idx and col_idx == var_col_idx) else center

            if isinstance(c.value, (int, float)):
                if col_name == '정규성 p값':
                    c.number_format = '[<0.001]"<0.001";0.000000'
                elif _is_int_like(col_name):
                    c.number_format = "#,##0"
                else:
                    c.number_format = "0.000"
                _style_value_cell(c, col_name)
            elif col_name in {"신뢰도 상태", "신뢰도"}:
                reliability_value = str(c.value or "").strip()
                if reliability_value.startswith("계산불가") or reliability_value.startswith("검토필요"):
                    c.fill = fill_review
                    c.font = font_crit
                elif reliability_value.startswith("주의"):
                    c.fill = fill_caution
                    c.font = font_warn
                elif reliability_value.startswith("정상"):
                    c.fill = fill_good
                    c.font = font_good

    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"
    _auto_width(ws)

    # Sheet 2: 변수별 상세
    ws2 = wb.create_sheet("변수별 상세")
    detail_headers = [
        "변수", "N", "원본N", "분석N", "신뢰도 상태", "신뢰도 사유", "평균", "원본평균", "σ(단기)", "σ(전체)", "원본σ(전체)",
        "Cp", "Cpk", "PPM(Within)", "Pp", "Ppk", "PPM(Overall)", "Cpm", "Cpmk", "군내/전체 지수 비율(Cwk/Ppk)",
        "PPM(실측)",
        "USL", "LSL", "목표치(T)", "원본USL", "원본LSL", "원본목표치(T)", "능력기준",
        "X-CL", "X-UCL", "X-LCL",
        "산포관리도", "산포-CL", "산포-UCL", "산포-LCL",
        "X관리이탈", "산포관리이탈", "규격이탈",
        "기준구간상태", "기준구간사유", "이상치제거", "정규성",
        "KSQ T1", "KSQ T2", "KSQ T3", "KSQ T4", "KSQ T5", "KSQ T6", "KSQ T7", "KSQ T8",
        "Cw", "Cwk", "군내 능력 설명", "능력 적용 상태", "능력 적용 사유", "성능 해석 안내",
    ]
    ws2.append(detail_headers)
    for col_idx in range(1, len(detail_headers) + 1):
        c = ws2.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        c.border = border

    def _f(v):
        nv = _nv(v)
        return nv if nv is not None else None

    for it in results:
        ksq_c = it.get("ksq_counts", {}) or {}
        row = [
            str(it.get("column", "")),
            int(len(it.get("series", []))),
            int(it.get("original_n", len(it.get("series", [])))),
            int(it.get("analysis_n", len(it.get("series", [])))),
            str(it.get("reliability_status", "")),
            str(it.get("reliability_reason_text", "")),
            _f(it.get("mu")),
            _f(it.get("original_mu")),
            _f(it.get("std_within")),
            _f(it.get("std_all")),
            _f(it.get("original_std_all")),
            _f(it.get("cp")),
            _f(it.get("cpk")),
            _f(it.get("ppm_within")),
            _f(it.get("pp")),
            _f(it.get("ppk")),
            _f(it.get("ppm_ppk")),
            _f(it.get("cpm")),
            _f(it.get("cpmk")),
            _f(it.get("within_overall_ratio")),
            _f(it.get("emp_ppm")),
            _f(it.get("usl")),
            _f(it.get("lsl")),
            _f(it.get("target")),
            _f(it.get("original_usl")),
            _f(it.get("original_lsl")),
            _f(it.get("original_target")),
            "전처리 후" if it.get("preprocess_applied") else "원본",
            _f(it.get("xbarbar")),
            _f(it.get("ucl")),
            _f(it.get("lcl")),
            str(it.get("dispersion_kind", "")),
            _f(it.get("r_bar")),
            _f(it.get("r_ucl")),
            _f(it.get("r_lcl")),
            int(it.get("xbar_ucl_cnt", 0) + it.get("xbar_lcl_cnt", 0)),
            int(it.get("r_ucl_cnt", 0) + it.get("r_lcl_cnt", 0)),
            int(it.get("spec_violation_count", 0)),
            str(it.get("baseline_status", "")),
            "; ".join(it.get("baseline_reasons", [])),
            int((it.get("outlier_info") or {}).get("count", 0)),
            str(it.get("norm_msg", "")),
            int(ksq_c.get("T1", 0)),
            int(ksq_c.get("T2", 0)),
            int(ksq_c.get("T3", 0)),
            int(ksq_c.get("T4", 0)),
            int(ksq_c.get("T5", 0)),
            int(ksq_c.get("T6", 0)),
            int(ksq_c.get("T7", 0)),
            int(ksq_c.get("T8", 0)),
            _f(it.get("cw")),
            _f(it.get("cwk")),
            WITHIN_CAPABILITY_NOTICE + " " + str(it.get("within_capability_note", "이전 분석 결과: 재분석 필요")),
            str(it.get("capability_status", "재분석 필요")),
            "; ".join(it.get("capability_reasons", [])),
            str(it.get("performance_note", "이전 결과: 재분석 필요")),
        ]
        ws2.append(row)

    detail_int_cols = {"N", "원본N", "분석N", "X관리이탈", "산포관리이탈", "규격이탈", "이상치제거", "KSQ T1", "KSQ T2", "KSQ T3", "KSQ T4", "KSQ T5", "KSQ T6", "KSQ T7", "KSQ T8"}
    detail_text_cols = {"변수", "신뢰도 상태", "신뢰도 사유", "산포관리도", "능력기준", "기준구간상태", "기준구간사유", "정규성", "군내 능력 설명", "능력 적용 상태", "능력 적용 사유", "성능 해석 안내"}

    for row_idx in range(2, ws2.max_row + 1):
        for col_idx, col_name in enumerate(detail_headers, 1):
            c = ws2.cell(row=row_idx, column=col_idx)
            c.border = border
            c.alignment = left if col_name in detail_text_cols else center
            if isinstance(c.value, (int, float)):
                c.number_format = "#,##0" if (col_name in detail_int_cols or "PPM" in col_name) else "0.000"
                _style_value_cell(c, col_name)

    ws2.freeze_panes = "A2"
    _auto_width(ws2, max_width=24)

    ws_note = wb.create_sheet("군내 능력 안내")
    ws_note.append(["항목", "설명"])
    ws_note.append(["Cw·Cwk", WITHIN_CAPABILITY_NOTICE])
    ws_note.append(["계산식", "Cw=(USL−LSL)/(6σw); Cwk=min((USL−평균)/(3σw), (평균−LSL)/(3σw))"])
    ws_note.append(["적용", "n=1은 MR 기반 앱 참고값, n≥2는 군내 진단. Cw는 양측 규격, Cwk는 최소 한쪽 규격이 필요합니다."])
    ws_note.append(["추정 방법", "n=1: MR̄/1.128; 2≤n≤9: R̄/d₂; n≥10: √mean(s²). 관리용 모드는 기준구간 산포와 전체 평균을 사용합니다. Box–Cox 군내 지수는 미적용입니다."])
    ws_note.column_dimensions['A'].width = 18
    ws_note.column_dimensions['B'].width = 90
    for row in ws_note.iter_rows():
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        ws_note.row_dimensions[row[0].row].height = 48

    import json
    ws_review = wb.create_sheet('평가·검토')
    ws_review.append(['변수', '항목', '값'])
    for it in results:
        for key in ('assessment_settings','sequence_review','capability_confidence','study_conclusion','outlier_info','within_estimator','within_capability_note',
                    'dispersion_limit_method','capability_quantile_scale','capability_quantiles'):
            value = it.get(key)
            ws_review.append([str(it['column']),key,json.dumps(value,ensure_ascii=False,default=str) if isinstance(value,(dict,list)) else value])
    _auto_width(ws_review, max_width=100)
    from spc_normality import diagnostic_rows, STATUS_LABELS
    ws_normality=wb.create_sheet('정규성·형태')
    ws_normality.append(['변수','대상','범위','단위','검정법','통계량','보정 AD 통계량','p값','판정','유의수준','검정N','전체N','왜도','초과첨도','표본 방식','사유'])
    for row in diagnostic_rows(results):
        ws_normality.append([row['variable'],row['target'],row['scope'],row['scale'],row['test'],row['statistic'],
            row['adjusted_statistic'],row['pvalue'],STATUS_LABELS[row['status']],row['alpha'],row['test_n'],row['total_n'],
            row['skewness'],row['excess_kurtosis'],row['sampling'],row['reason']])
        ws_normality.cell(ws_normality.max_row,8).number_format='[<0.001]"<0.001";0.000000'
    ws_normality.freeze_panes='A2'
    _auto_width(ws_normality,max_width=65)
    nonlinear = [it for it in results if it.get('distribution_mode') == 'nonnormal' or it.get('box_cox_applied')]
    if nonlinear:
        import json
        ws_model = wb.create_sheet('분포·GZ')
        ws_model.append(['변수', '항목', '값'])
        for it in nonlinear:
            keys = ('capability_method', 'distribution_settings', 'chart_model', 'distribution_model',
                    'chart_fit_status', 'performance_fit_status', 'capability_methods', 'capability_quantiles',
                    'numeric_status', 'effective_rules', 'pearson_rules_note', 'chart_fit_target',
                    'chart_limit_method', 'dispersion_limit_method', 'simulation_samples', 'seed')
            for key in keys:
                value = it.get(key)
                ws_model.append([str(it['column']), key,
                                 json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else value])
        ws_model.freeze_panes = 'A2'
        _auto_width(ws_model, max_width=100)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@day_guard
def export_analysis_html(
    results: List[Dict[str, Any]],
    summary_df: pd.DataFrame,
    analysis_meta: Optional[Dict[str, Any]] = None,
    palette: str = 'trust',
    hist_bins: int = 30,
    ksq_toggles: Optional[Dict[str, bool]] = None,
    show_zones: Optional[Dict[str, bool]] = None,
    include_charts: bool = True,
    include_histogram: bool = True,
    include_qq: bool = False,
    include_hist_qq: bool = False,
) -> bytes:
    """분석 결과를 단일 HTML 리포트로 내보내기."""
    analysis_meta = {**summary_df.attrs.get('audit', {}), **(analysis_meta or {})}
    from datetime import datetime
    from html import escape
    import matplotlib.pyplot as plt
    from spc_charts import build_control_chart, build_histogram, build_qq_plot

    _df, cap_cols, ppm_cols, cnt_cols = _report_summary_spec(summary_df)

    def _cell_html(col, val):
        sval, cls = _report_cell_display(col, val, cap_cols, ppm_cols, cnt_cols, results[0] if results else None)
        if cls == "na" and sval == "—":
            return "<span class='na'>—</span>"
        esc = escape(sval)
        return f"<span class='{cls}'>{esc}</span>" if cls and cls != "na" else esc

    text_cols = {"변수", "판정", "신뢰도", "관리도", "운영모드"}

    def _col_class(col) -> str:
        return " class='text'" if str(col) in text_cols else ""

    th = "".join([f"<th{_col_class(c)}>{escape(str(c))}</th>" for c in _df.columns])
    trs = []
    for _, row in _df.iterrows():
        tds = "".join([f"<td{_col_class(c)}>{_cell_html(c, row[c])}</td>" for c in _df.columns])
        trs.append(f"<tr>{tds}</tr>")
    summary_table_html = f"<table class='summary'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

    detail_html = []
    for it in results:
        detail = _report_variable_detail(it)
        col = escape(detail["column"])
        risk_cls = detail["risk_cls"]
        norm_msg = escape(detail["norm_msg"])
        within_note = escape(detail["within_note"])
        capability_note = escape(detail["capability_note"])
        performance_note = escape(detail["performance_note"])

        metric_cards = []
        for key, val, cls in detail["metric_items"]:
            cls_attr = f" {cls}" if cls else ""
            metric_cards.append(
                f"<div class='metric'><div class='k'>{escape(str(key))}</div><div class='v{cls_attr}'>{escape(str(val))}</div></div>"
            )

        chart_main_html = ""
        histogram_html = ""
        chart_sub_html = ""
        if include_charts:
            try:
                fig = build_control_chart(
                    it,
                    palette=palette,
                    ksq_toggles=ksq_toggles,
                    show_zones=show_zones,
                    figsize=(11, 7.2),
                )
                png = fig_to_bytes(fig, fmt='png', dpi=140)
                plt.close(fig)
                b64 = base64.b64encode(png).decode("ascii")
                chart_main_html = f"<img class='chart chart-main' loading='lazy' decoding='async' alt='control-{col}' src='data:image/png;base64,{b64}'/>"
            except Exception:
                pass

        if include_histogram or include_qq or include_hist_qq:
            _sub_imgs = []
            if include_histogram or include_hist_qq:
                try:
                    fig_h = build_histogram(it, palette=palette, bins=hist_bins, figsize=(4.5, 3.4))
                    for ax in fig_h.axes:
                        legend = ax.get_legend()
                        if legend is not None:
                            legend.remove()
                    png_h = fig_to_bytes(fig_h, fmt='png', dpi=140)
                    plt.close(fig_h)
                    b64_h = base64.b64encode(png_h).decode("ascii")
                    histogram_html = (
                        f"<div class='side-chart'>"
                        f"<img class='chart chart-side' loading='lazy' decoding='async' alt='hist-{col}' src='data:image/png;base64,{b64_h}'/>"
                        f"</div>"
                    )
                except Exception:
                    pass
            if include_qq or include_hist_qq:
                try:
                    fig_q = build_qq_plot(it, palette=palette)
                    png_q = fig_to_bytes(fig_q, fmt='png', dpi=128)
                    plt.close(fig_q)
                    b64_q = base64.b64encode(png_q).decode("ascii")
                    _sub_imgs.append(
                        f"<div class='subchart-block'><h4>Q-Q 플롯</h4>"
                        f"<img class='chart chart-sub' loading='lazy' decoding='async' alt='qq-{col}' src='data:image/png;base64,{b64_q}'/>"
                        f"</div>"
                    )
                except Exception:
                    pass
            if _sub_imgs:
                chart_sub_html = f"<div class='subcharts'>{''.join(_sub_imgs)}</div>"

        metrics_html = f"""
        <div class="metrics">
          {''.join(metric_cards)}
        </div>
        <div class="norm">정규성 (계산 단위): {norm_msg}</div>
        {diagnostics_html(it)}
        {histogram_html}
        """

        detail_html.append(
            f"<section class='var'>"
            f"<div class='var-heading'>"
            f"<div class='var-head'><h3>{col}</h3><span class='pill {risk_cls}'>{detail['risk_label']}</span></div>"
            f"<div class='norm'>Cp·Cpk: {capability_note}</div>"
            f"<div class='norm'>Cw·Cwk (진단용): {within_note}</div>"
            f"<div class='norm'>{performance_note}</div></div>"
            f"<div class='var-main'>"
            f"<div class='var-side'>{metrics_html}</div>"
            f"<div class='var-chart'>{chart_main_html if chart_main_html else '<div class=\"chart-empty\">차트 없음</div>'}</div>"
            f"</div>"
            f"{chart_sub_html}"
            f"</section>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_items = _report_meta_items(analysis_meta, now)
    meta_html = "".join(
        f"<div class='metric'><div class='k'>{escape(str(k))}</div><div class='v'>{escape(str(v))}</div></div>"
        for k, v in meta_items
    )
    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SPC 분석 결과</title>
<style>
:root {{
  --a4-landscape-w: 297mm;
  --a4-landscape-h: 210mm;
  --print-margin: 6mm;
  --print-content-w: calc(var(--a4-landscape-w) - (var(--print-margin) * 2));
  --border-strong: #475569;
  --border-medium: #64748b;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 16px;
  font-family: "Malgun Gothic","Nanum Gothic",Arial,sans-serif;
  background:#f5f7fb; color:#0f172a;
  font-size: 13px;
  line-height: 1.35;
}}
.page {{
  max-width: 1100px;
  margin: 0 auto;
}}
h1 {{ margin: 0 0 8px 0; font-size: 22px; }}
h2 {{ margin: 0 0 8px 0; font-size: 16px; }}
h3 {{ margin: 0; font-size: 15px; }}
.meta {{ color:#475569; font-size:12px; margin-bottom:12px; }}
.card {{
  background:#fff; border:1.5px solid var(--border-medium); border-radius:10px; padding:12px; margin-bottom:12px;
}}
.summary-wrap {{
  overflow-x: auto;
  overflow-y: hidden;
  border: 1px solid #475569;
  border-radius: 6px;
  background: #fff;
}}
table.summary {{
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  table-layout: auto;
  border: none;
  margin: 0;
}}
table.summary th {{
  background:#1e3a8a; color:#fff; padding:6px 8px;
  position:sticky; top:0; white-space:nowrap; word-break:keep-all; line-height:1.25;
  font-size:10px; font-weight:700;
  vertical-align: middle;
  text-align: center;
  border: none;
  border-right: 1px solid rgba(255,255,255,0.22);
  border-bottom: 1px solid rgba(255,255,255,0.32);
}}
table.summary th:last-child {{ border-right: none; }}
table.summary td {{
  padding:6px 8px; text-align:center; background:#fff;
  white-space:nowrap; word-break:keep-all; line-height:1.25; font-size:10px;
  vertical-align: middle;
  border: none;
  border-right: 1px solid #d1d5db;
  border-bottom: 1px solid #d1d5db;
}}
table.summary td:last-child {{ border-right: none; }}
table.summary tbody tr:last-child td {{ border-bottom: none; }}
table.summary thead tr:first-child th:first-child {{ border-top-left-radius: 5px; }}
table.summary thead tr:first-child th:last-child {{ border-top-right-radius: 5px; }}
table.summary tbody tr:last-child td:first-child {{ border-bottom-left-radius: 5px; }}
table.summary tbody tr:last-child td:last-child {{ border-bottom-right-radius: 5px; }}
.good, .warn, .bad {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: middle;
  text-align: center;
  line-height: 1.25;
  white-space: nowrap;
  border-radius: 4px;
  padding: 2px 6px;
  font-weight: 700;
}}
.good {{ color:#166534; background:#dcfce7; }}
.warn {{ color:#92400e; background:#fef3c7; }}
.bad  {{ color:#991b1b; background:#fee2e2; }}
.na {{ color:#64748b; }}
.details-heading {{
  margin: 14px 0 8px 0;
  font-size: 16px;
}}
.var {{
  background:#fff; border:1.5px solid var(--border-medium); border-radius:10px; padding:10px; margin-top:10px;
}}
.var-head {{
  display:flex; align-items:center; justify-content:space-between; gap:8px;
  border-bottom:1.5px solid var(--border-medium); padding-bottom:8px; margin-bottom:8px;
}}
.pill {{
  font-size:11px; font-weight:700; border-radius:999px; padding:2px 8px;
}}
.pill.good {{ color:#166534; background:#dcfce7; }}
.pill.warn {{ color:#92400e; background:#fef3c7; }}
.pill.bad {{ color:#991b1b; background:#fee2e2; }}
.var-main {{
  display:grid;
  grid-template-columns: 300px 1fr;
  gap:10px;
  align-items:stretch;
}}
.var-side {{
  border:1.5px solid var(--border-medium);
  border-radius:8px;
  padding:8px;
  background:#f8fafc;
  display:flex;
  flex-direction:column;
  min-height:0;
}}
.var-chart {{
  min-width:0;
  display:flex;
  align-items:stretch;
  min-height:420px;
}}
.metrics {{
  display:grid; grid-template-columns: repeat(2, minmax(100px,1fr)); gap:6px; margin-bottom:6px;
}}
.meta-metrics {{
  grid-template-columns: repeat(3, minmax(120px, 1fr));
}}
.metric {{
  border:1px solid var(--border-medium); border-radius:8px; padding:6px; background:#fff;
}}
.metric .k {{ font-size:10px; color:#64748b; margin-bottom:2px; }}
.metric .v {{ font-size:14px; font-weight:700; line-height:1.15; }}
.norm {{ font-size:11px; color:#334155; margin-bottom:6px; }}
.side-chart {{
  flex:1;
  display:flex;
  min-height:180px;
  margin-top:4px;
}}
.chart {{
  width:100%; display:block; margin:0; border:1.5px solid var(--border-strong); border-radius:8px; background:#fff;
}}
.chart-main {{ width:100%; height:100%; object-fit:contain; }}
.chart-side {{ width:100%; height:100%; object-fit:contain; }}
.subcharts {{
  margin-top:8px;
  display:grid;
  grid-template-columns: 1fr 1fr;
  gap:8px;
}}
.subchart-block {{ min-width:0; }}
.subchart-block:only-child {{ grid-column: 1 / -1; }}
.subchart-block h4 {{ margin: 0 0 4px 0; font-size: 12px; color:#0f172a; }}
.chart-sub {{ width:100%; object-fit:contain; }}
.chart-empty {{
  border:1px dashed #cbd5e1; border-radius:8px; min-height:100%;
  width:100%;
  display:flex; align-items:center; justify-content:center; color:#64748b; background:#f8fafc;
}}
.print-note {{
  display:none;
}}
@media (max-width: 900px) {{
  .page {{ max-width: 100%; }}
  .var-main {{ grid-template-columns: 1fr; }}
  .subcharts {{ grid-template-columns: 1fr; }}
  .meta-metrics {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
}}
@page {{
  size: A4 landscape;
  margin: 6mm;
}}
@media print {{
  * {{
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
    box-shadow: none !important;
  }}
  html, body {{
    margin: 0;
    padding: 0;
    width: var(--a4-landscape-w);
    background: #fff !important;
    color: #0f172a;
    font-size: 10pt;
  }}
  .page {{
    max-width: none;
    width: var(--print-content-w);
    margin: 0 auto;
    padding: 0;
  }}
  .print-note {{
    display: block;
    font-size: 9pt;
    color: #64748b;
    margin-bottom: 8px;
  }}
  h1 {{ font-size: 18pt; }}
  h2, .details-heading {{ font-size: 12pt; }}
  h3 {{ font-size: 11pt; }}
  .meta {{ font-size: 9pt; margin-bottom: 8px; }}
  .card {{
    background: #fff;
    border: 1pt solid #1e293b !important;
    border-radius: 6px;
    padding: 8px;
    margin-bottom: 8px;
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  .print-cover {{
    break-after: avoid;
    page-break-after: avoid;
  }}
  .print-summary {{
    break-inside: avoid;
    page-break-inside: avoid;
    break-after: page;
    page-break-after: always;
  }}
  .summary-wrap {{
    overflow: visible !important;
    width: 100%;
    max-width: 100%;
    border: 0.75pt solid #334155;
    border-radius: 4px;
    background: #fff;
  }}
  table.summary {{
    width: 100% !important;
    min-width: 0 !important;
    max-width: 100% !important;
    font-size: 6.5pt;
    table-layout: fixed !important;
    border: none !important;
  }}
  table.summary th, table.summary td {{
    padding: 2px 1px;
    white-space: normal !important;
    word-break: keep-all;
    overflow-wrap: anywhere;
    vertical-align: middle;
    text-align: center;
    line-height: 1.15;
    font-size: 6.5pt;
    border: none !important;
  }}
  .good, .warn, .bad {{
    display: inline-block;
    max-width: 100%;
    box-sizing: border-box;
    white-space: normal !important;
    word-break: keep-all;
    overflow-wrap: anywhere;
    text-align: center;
    vertical-align: middle;
    line-height: 1.2;
    padding: 1px 2px;
  }}
  table.summary th {{
    position: static;
    background: #1e3a8a !important;
    color: #fff !important;
    font-weight: 700;
    border-right: 0.5pt solid rgba(255,255,255,0.28) !important;
    border-bottom: 0.75pt solid #1e40af !important;
  }}
  table.summary th:last-child {{
    border-right: none !important;
  }}
  table.summary td {{
    background: #fff !important;
    color: #0f172a !important;
    border-right: 0.5pt solid #94a3b8 !important;
    border-bottom: 0.5pt solid #94a3b8 !important;
  }}
  table.summary td:last-child {{
    border-right: none !important;
  }}
  table.summary tbody tr:last-child td {{
    border-bottom: none !important;
  }}
  .details-heading {{
    display: none;
  }}
  .print-details .var {{
    break-before: page;
    page-break-before: always;
    break-inside: avoid;
    page-break-inside: avoid;
    margin-top: 0;
    padding: 3mm;
    height: calc(var(--a4-landscape-h) - (var(--print-margin) * 2) - 1mm);
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    gap: 2mm;
    box-sizing: border-box;
    border: 1pt solid #1e293b !important;
  }}
  .var-head {{
    margin-bottom: 0;
    padding-bottom: 2mm;
    flex: 0 0 auto;
    border-bottom: 0.9pt solid #334155 !important;
  }}
  .var-main {{
    flex: 1 1 auto;
    display: grid;
    grid-template-columns: 74mm 1fr;
    gap: 3mm;
    align-items: stretch;
    min-height: 0;
  }}
  .var-side {{
    padding: 2mm;
    display: flex;
    flex-direction: column;
    min-height: 0;
    border: 0.9pt solid #334155 !important;
    background: #f8fafc !important;
  }}
  .var-chart {{
    min-height: 0;
    height: 100%;
    display: flex;
  }}
  .metrics {{
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1mm;
    margin-bottom: 2mm;
  }}
  .meta-metrics {{
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }}
  .metric {{
    padding: 1mm;
    border: 0.8pt solid #334155 !important;
    background: #fff !important;
  }}
  .metric .k {{ font-size: 7.5pt; }}
  .metric .v {{ font-size: 9.5pt; }}
  .norm {{ font-size: 8pt; margin-bottom: 2mm; }}
  .side-chart {{
    flex: 1 1 auto;
    min-height: 0;
    height: 0;
    margin-top: 2mm;
    display: flex;
  }}
  .chart {{
    width: 100%;
    height: 100%;
    max-height: none !important;
    border: 0.9pt solid #1e293b !important;
    border-radius: 4px;
    background: #fff;
    object-fit: contain;
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  .chart-main, .chart-side, .chart-sub {{
    width: 100%;
    height: 100%;
    max-height: none !important;
    object-fit: contain;
  }}
  .subcharts {{
    flex: 0 0 auto;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2mm;
    margin-top: 2mm;
    max-height: 42mm;
  }}
  .subchart-block h4 {{ font-size: 8.5pt; margin: 0 0 1mm 0; }}
  .chart-empty {{ min-height: 100%; }}
}}
</style>
</head>
<body>
  <div class="page">
  <header class="print-cover">
  <div class="print-note">인쇄 설정: A4, 가로, 배경 그래픽 켜기</div>
  <h1>SPC 분석 결과 리포트</h1>
  <div class="meta">생성 시각: {escape(now)} · 변수 수: {len(results):,}개</div>
  <div class="meta">실측 PPM·규격이탈은 전처리 전 원본 유효 측정값 기준입니다. 관리도·능력 지수는 분석 대상 데이터 기준입니다.</div>
  <div class="card">
    <h2>분석 조건 요약</h2>
    <div class="metrics meta-metrics">{meta_html}</div>
  </div>
  </header>
  <section class="card print-summary">
    <h2>선별 요약</h2>
    <p class="norm">{escape(WITHIN_CAPABILITY_NOTICE)}</p>
    <div class="summary-wrap">{summary_table_html}</div>
  </section>
  <section class="print-details">
    <h2 class="details-heading">변수별 상세</h2>
    {''.join(detail_html)}
  </section>
  </div>
</body>
</html>"""

    return html_doc.encode("utf-8")


def _render_html_file_to_pdf(html_path: str) -> bytes:
    """임시 HTML 파일을 Chromium으로 PDF로 렌더링한다."""
    from pathlib import Path

    from _pdf_worker import render_html_file_to_pdf

    return render_html_file_to_pdf(html_path)


def _html_bytes_to_pdf(html_bytes: bytes) -> bytes:
    """HTML 리포트를 브라우저 화면과 동일한 레이아웃으로 PDF 변환한다."""
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    html_path = ""
    pdf_path = ""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PDF 생성에 playwright가 필요합니다. "
            "py -m pip install playwright 후 py -m playwright install chromium 을 실행하세요."
        ) from exc

    worker_script = Path(__file__).resolve().parent / "_pdf_worker.py"
    if not worker_script.is_file():
        raise RuntimeError("PDF 워커 스크립트를 찾을 수 없습니다: _pdf_worker.py")

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as tmp_html:
        tmp_html.write(html_bytes)
        html_path = tmp_html.name

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        pdf_path = tmp_pdf.name

    try:
        result = subprocess.run(
            [sys.executable, str(worker_script), html_path, pdf_path],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(worker_script.parent),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            if "Executable doesn't exist" in err or "playwright install" in err.lower():
                raise RuntimeError(
                    "Chromium 브라우저가 설치되지 않았습니다. "
                    "터미널에서 py -m playwright install chromium 을 실행하세요."
                )
            raise RuntimeError(err or "PDF 변환에 실패했습니다.")
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
            raise RuntimeError("PDF 파일이 생성되지 않았습니다.")
        return Path(pdf_path).read_bytes()
    finally:
        for path in (html_path, pdf_path):
            if path and os.path.exists(path):
                os.unlink(path)


@day_guard
def export_all_pdf(
    results: List[Dict[str, Any]],
    summary_df: pd.DataFrame,
    analysis_meta: Optional[Dict[str, Any]] = None,
    palette: str = 'trust',
    hist_bins: int = 30,
    ksq_toggles: Optional[Dict[str, bool]] = None,
    show_zones: Optional[Dict[str, bool]] = None,
    include_charts: bool = True,
    include_histogram: bool = True,
    include_qq: bool = False,
    include_hist_qq: bool = False,
    include_detail_chart: Optional[bool] = None,
) -> bytes:
    """HTML 리포트와 동일한 화면 레이아웃으로 PDF를 생성한다."""
    if include_detail_chart is not None:
        include_charts = True
        include_histogram = bool(include_detail_chart)
        include_qq = bool(include_detail_chart)
        include_hist_qq = bool(include_detail_chart)

    html_bytes = export_analysis_html(
        results=results,
        summary_df=summary_df,
        analysis_meta=analysis_meta,
        palette=palette,
        hist_bins=hist_bins,
        ksq_toggles=ksq_toggles,
        show_zones=show_zones,
        include_charts=include_charts,
        include_histogram=include_histogram,
        include_qq=include_qq,
        include_hist_qq=include_hist_qq,
    )
    return _html_bytes_to_pdf(html_bytes)


@day_guard
def html_bytes_to_pdf(html_bytes: bytes) -> bytes:
    """HTML 리포트 bytes를 PDF로 변환한다."""
    return _html_bytes_to_pdf(html_bytes)


@day_guard
def fig_to_bytes(fig, fmt: str = 'png', dpi: int = 120) -> bytes:
    """Matplotlib Figure를 bytes로 변환 (Streamlit st.image 용)."""
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches='tight')
    buf.seek(0)
    return buf.getvalue()
