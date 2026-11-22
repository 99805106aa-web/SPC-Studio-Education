# -*- coding: utf-8 -*-
"""
spc_export.py
=============
Excel / PDF 내보내기 모듈
원본: 공정능력_관리도_Rev26.py _export_selection / _export_all_pdf 에서 추출
"""

import io
from spc_access import day_guard
from spc_core import WITHIN_CAPABILITY_NOTICE, assess_process, fmt_ppm, mean_confidence_interval, screening_index_pair
import math
import base64
import numpy as np
import pandas as pd
from spc_normality import diagnostics_html, shape_summary_display
from spc_extended import EXTENDED_MODE, GUIDE as EXTENDED_GUIDE, TITLE as EXTENDED_TITLE, calculation_note, monitor_counts, monitor_signals
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
        EXTENDED_MODE: "확장 슈하르트 관리도",
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
    summary_df = shape_summary_display(summary_df)
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


def _report_metric_cls(v, it=None, metric='cpk') -> str:
    try:
        fv = float(v)
        if not np.isfinite(fv):
            return "na"
        from spc_policy import band
        return band(fv, it, metric)
    except Exception:
        return "na"


def _report_summary_spec(summary_df: pd.DataFrame):
    df = _summary_visible_df(summary_df).copy()
    cap_cols = [c for c in ["Cp", "Cpk", "Pp", "Ppk", "Cpm", "Cpmk"] if c in df.columns]
    ppm_cols = [c for c in df.columns if "PPM" in str(c)]
    cnt_cols = [c for c in df.columns if ("이탈" in str(c) or "위반" in str(c))]
    return df, cap_cols, ppm_cols, cnt_cols


def _extended_report_monitors(results):
    return {str(it['column']): monitor_signals(it['extended_shewhart']) if it.get('extended_shewhart') else None
            for it in results if it.get('chart_usage_mode') == EXTENDED_MODE}


def _apply_extended_monitor_summary(summary, monitors):
    if not monitors or summary.empty or '변수' not in summary:
        return
    names = summary['변수'].astype(str)
    mask = names.isin(monitors)
    counts = {name: monitor_counts(monitor) for name, monitor in monitors.items()}
    for i, column in enumerate(('X관리이탈', 'R관리이탈', '관리이탈', '최근관리이탈')):
        if column in summary:
            summary[column] = summary[column].astype(object)
            summary.loc[mask, column] = [counts[name][i] for name in names[mask]]


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
            cls = _report_metric_cls(fv, it, col)
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


def _report_variable_detail(it: Dict[str, Any], monitor=None) -> Dict[str, Any]:
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
    is_extended = it.get('chart_usage_mode') == EXTENDED_MODE
    if is_extended:
        _, _, ctrl_v, _ = monitor_counts(monitor)
    assessment = assess_process(it)
    risk_cls = {"high": "bad", "medium": "warn", "unknown": "warn", "low": "good"}[assessment['level']]
    if risk_cls == 'good' and it.get('capability_status') != '적용 가능':
        risk_cls = 'warn'

    pair = screening_index_pair(it)
    main_pair = (pair['width_metric'], pair['shifted_metric']) if pair['width_metric'] else ('pp', 'ppk')
    remaining_pair = ('pp', 'ppk') if main_pair[0] == 'cp' else ('cp', 'cpk')
    suffix = '.' + it.get('capability_method', 'G') if it.get('box_cox_applied') or it.get('distribution_mode') == 'nonnormal' else ''
    pair_items = []
    for metric in (*main_pair, *remaining_pair):
        value = it.get(metric, np.nan)
        label = metric.capitalize() + suffix + (' (전체산포 고려)' if metric.startswith('pp') else '')
        display = _report_fmt_value(value)
        if metric == 'cpk' and display == '—' and it.get('capability_status') != '적용 가능':
            display = it.get('capability_status', '재분석 필요')
        pair_items.append((label, display, _report_metric_cls(value, it, metric)))
    core_metrics = [
        ("N", _report_fmt_value(len(it.get("series", [])), 0), ""),
        *pair_items[:2],
        ("Cw (MR 참고)" if it.get('within_estimator')=='moving_range' and not it.get('box_cox_applied') else "Cw (군내산포 고려)", _report_fmt_value(it.get("cw", np.nan)), ""),
        ("Cwk (MR 참고)" if it.get('within_estimator')=='moving_range' and not it.get('box_cox_applied') else "Cwk (군내산포 고려)", _report_fmt_value(it.get("cwk", np.nan)), ""),
        ("규격 이탈", f"{spec_v:,}", "bad" if spec_v > 0 else "good"),
        ("감시 관리 이탈 (확장 X̄·S)" if is_extended else "관리 이탈",
         '—' if ctrl_v is None else f"{ctrl_v:,}",
         '' if ctrl_v is None else "bad" if ctrl_v > 0 else "good"),
    ]
    capability_metrics = [
        *pair_items[2:],
        ("Cpm", _report_fmt_value(it.get("cpm", np.nan)), _report_metric_cls(it.get("cpm", np.nan), it, 'cpm')),
        ("Cpmk", _report_fmt_value(it.get("cpmk", np.nan)), _report_metric_cls(it.get("cpmk", np.nan), it, 'cpmk')),
        ("PPM(Cwk, 군내산포 고려)", _report_fmt_ppm_int(it.get("ppm_within", np.nan)), ""),
        ("PPM(Ppk, 전체산포 고려)", _report_fmt_ppm_int(it.get("ppm_ppk", np.nan)), ""),
    ]
    specification_metrics = [
        ("USL (원단위)" if it.get('box_cox_applied') else "USL", _report_fmt_value(it.get("original_usl" if it.get('box_cox_applied') else "usl", np.nan)), ""),
        ("LSL (원단위)" if it.get('box_cox_applied') else "LSL", _report_fmt_value(it.get("original_lsl" if it.get('box_cox_applied') else "lsl", np.nan)), ""),
        ("목표치(T)", _report_fmt_value(it.get("target", np.nan)), ""),
    ]
    control_metrics = [
        (f"{x_lab}-UCL", _report_fmt_value(it.get("ucl", np.nan)), ""),
        (f"{x_lab}-LCL", _report_fmt_value(it.get("lcl", np.nan)), ""),
        (f"{disp_kind_raw}-UCL", _report_fmt_value(it.get("r_ucl", np.nan)), ""),
    ]
    if r_lcl_applicable:
        control_metrics.append((f"{disp_kind_raw}-LCL", _report_fmt_value(it.get("r_lcl", np.nan)), ""))
    if it.get('dispersion_limit_method'):
        control_metrics.append(('산포 관리한계 계산', it['dispersion_limit_method'], ''))
    if is_extended:
        control_metrics = []
        extended = it.get('extended_shewhart')
        if extended is not None:
            for name, (lcl, _, ucl) in extended['limits'].items():
                label = f'{name} (비교)' if name == '일반 X̄' else name
                control_metrics.extend((f'{label} {limit}', _report_fmt_value(value), '')
                                       for limit, value in (('LCL', lcl), ('UCL', ucl)))
                if monitor is not None and not monitor.empty:
                    control_metrics.append((f'{label} 감시 이탈', str(int(monitor[name].sum())), ''))

    performance_note = str(it.get('performance_note', '이전 결과: 재분석 필요'))
    performance_note = pair['state'] + ' · ' + performance_note
    performance_note += ' · ' + it.get('study_conclusion', '')
    cfg = it.get('assessment_settings', {})
    from spc_policy import criteria_description
    performance_note += ' · ' + criteria_description(it)
    if it.get('capability_confidence'):
        ci = it['capability_confidence']
        performance_note += f" · Ppk 단측 {ci['confidence']:.1%} 신뢰하한 {ci['ppk_lower']:.4f} ({ci['method']})"
    if it.get('distribution_mode') == 'nonnormal':
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

    metric_groups = [("능력·예상 PPM", capability_metrics), ("규격·목표", specification_metrics)]
    if control_metrics:
        metric_groups.append(("관리한계", control_metrics))
    return {
        "column": str(it.get("column", "")),
        "risk_cls": risk_cls,
        "risk_label": {"bad": "위험", "warn": "주의", "good": "양호"}[risk_cls],
        "norm_msg": str(it.get("norm_msg", "")),
        "within_note": str(it.get("within_capability_note", "이전 분석 결과: 재분석 필요")),
        "capability_note": str(it.get("capability_status", "재분석 필요")) + ": " + "; ".join(it.get("capability_reasons", [])),
        "performance_note": performance_note,
        "core_metrics": core_metrics,
        "metric_groups": metric_groups,
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

    capability_names = {"Cp", "Cpk", "Pp", "Ppk", "Cpm", "Cpmk"}

    result_by_name = {str(it['column']): it for it in results}
    def _style_value_cell(cell, col_name: str, item=None):
        v = cell.value
        if not isinstance(v, (int, float)):
            return
        fv = float(v)
        if col_name in capability_names:
            from spc_policy import band
            state = band(fv, item, col_name)
            if state == 'bad':
                cell.fill = fill_crit
                cell.font = font_crit
            elif state == 'warn':
                cell.fill = fill_warn
                cell.font = font_warn
            elif state == 'good':
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
    extended_monitors = _extended_report_monitors(results)
    _apply_extended_monitor_summary(summary_export_df, extended_monitors)
    headers = list(summary_export_df.columns)
    ws.append(headers)
    from openpyxl.comments import Comment
    for label in ("Cw", "Cwk"):
        if label in headers:
            ws.cell(1, headers.index(label) + 1).comment = Comment(WITHIN_CAPABILITY_NOTICE, "SPC")
    if extended_monitors:
        for label, chart in (("X관리이탈", "확장 X̄"), ("R관리이탈", "S")):
            if label in headers:
                ws.cell(1, headers.index(label) + 1).comment = Comment(f"확장 슈하르트 행은 {chart} 관리도의 감시구간 이탈 건수입니다.", "SPC")
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = center
        c.border = border

    # 숫자형 컬럼 추정(요약 DF 내 문자열 숫자 -> 실제 숫자로 변환)
    numeric_cols = ({'X관리이탈', 'R관리이탈', '관리이탈', '최근관리이탈'} & set(headers)
                    if extended_monitors else set())
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
                item = result_by_name.get(str(ws.cell(row_idx, var_col_idx).value)) if var_col_idx else None
                _style_value_cell(c, col_name, item)
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
        name = str(it['column'])
        is_extended = name in extended_monitors
        x_cl, x_ucl, x_lcl = it.get('xbarbar'), it.get('ucl'), it.get('lcl')
        r_cl, r_ucl, r_lcl = it.get('r_bar'), it.get('r_ucl'), it.get('r_lcl')
        kind = str(it.get('dispersion_kind', ''))
        x_count = int(it.get('xbar_ucl_cnt', 0) + it.get('xbar_lcl_cnt', 0))
        r_count = int(it.get('r_ucl_cnt', 0) + it.get('r_lcl_cnt', 0))
        if is_extended:
            ext = it.get('extended_shewhart')
            limits = ext['limits'] if ext else {}
            x_lcl, x_cl, x_ucl = limits.get('확장 X̄', (None, None, None))
            r_lcl, r_cl, r_ucl = limits.get('S', (None, None, None))
            x_count, r_count, _, _ = monitor_counts(extended_monitors[name])
            kind = 'S (확장 X̄와 함께, 감시구간 집계)'
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
            _f(x_cl),
            _f(x_ucl),
            _f(x_lcl),
            kind,
            _f(r_cl),
            _f(r_ucl),
            _f(r_lcl),
            x_count,
            r_count,
            int(it.get("spec_violation_count", 0)),
            str(it.get("baseline_status", "")),
            "; ".join(it.get("baseline_reasons", [])),
            int((it.get("outlier_info") or {}).get("count", 0)),
            str(it.get("norm_msg", "")),
            *(None if is_extended else int(ksq_c.get(f'T{i}', 0)) for i in range(1, 9)),
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
                _style_value_cell(c, col_name, result_by_name.get(str(ws2.cell(row_idx, 1).value)))

    ws2.freeze_panes = "A2"
    _auto_width(ws2, max_width=24)

    ws_note = wb.create_sheet("군내 능력 안내")
    ws_note.append(["항목", "설명"])
    ws_note.append(["Cw·Cwk", WITHIN_CAPABILITY_NOTICE])
    ws_note.append(["계산식", "Cw=(USL−LSL)/(6σw); Cwk=min((USL−평균)/(3σw), (평균−LSL)/(3σw))"])
    ws_note.append(["적용", "n=1은 MR 기반 앱 참고값, n≥2는 군내 진단. Cw는 양측 규격, Cwk는 최소 한쪽 규격이 필요합니다."])
    ws_note.append(["추정 방법", "I–MR: 원본 인접 관측값 MR̄/1.128. X̄–R: R̄/d₂. X̄–S: √mean(s²). 관리용 모드는 기준구간 산포와 전체 평균을 사용합니다. Box–Cox 군내 지수는 미적용입니다."])
    ws_note.column_dimensions['A'].width = 18
    ws_note.column_dimensions['B'].width = 90
    for row in ws_note.iter_rows():
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        ws_note.row_dimensions[row[0].row].height = 48

    import json
    ws_review = wb.create_sheet('평가·검토')
    ws_review.append(['변수', '항목', '값'])
    for it in results:
        for key in ('assessment_settings','capability_criteria','sequence_review','capability_confidence','study_conclusion','outlier_info','within_estimator','within_capability_note',
                    'dispersion_limit_method','capability_quantile_scale','capability_quantiles'):
            value = it.get(key)
            ws_review.append([str(it['column']),key,json.dumps(value,ensure_ascii=False,default=str) if isinstance(value,(dict,list)) else value])
    _auto_width(ws_review, max_width=100)
    from spc_policy import CRITERIA_STATUS, reference_only_decision, REFERENCE_COMPARISON_LABEL
    ws_criteria = wb.create_sheet('표본수별 평가기준')
    ws_criteria.append(['변수','방식','중요도','평가 단계','유효N','기준N','신뢰수준','지수','기본 목표','적용 요구값','실제값','판정','사유','출처'])
    for it in results:
        record = it.get('capability_criteria', {})
        for key, d in record.get('indices', {}).items():
            reference_only = reference_only_decision(d)
            ws_criteria.append([str(it['column']),record['mode'],record['characteristic_class'],record['study_stage'],
                record['sample_n'],record['reference_n'],record['confidence'],key.capitalize(),d['base'],
                None if reference_only else d['required'],d['value'],
                REFERENCE_COMPARISON_LABEL if reference_only else CRITERIA_STATUS[d['status']],
                '; '.join(d['reasons']),record['source']])
    ws_criteria.freeze_panes = 'A2'
    _auto_width(ws_criteria, max_width=80)
    from spc_normality import diagnostic_rows, shape_magnitude, STATUS_LABELS
    ws_normality=wb.create_sheet('정규성·형태')
    ws_normality.append(['변수','대상','범위','단위','검정법','통계량','보정 AD 통계량','p값','판정','유의수준','검정N','전체N','왜도','첨도','표본 방식','사유'])
    for row in diagnostic_rows(results):
        ws_normality.append([row['variable'],row['target'],row['scope'],row['scale'],row['test'],row['statistic'],
            row['adjusted_statistic'],row['pvalue'],STATUS_LABELS[row['status']],row['alpha'],row['test_n'],row['total_n'],
            shape_magnitude(row['skewness']),shape_magnitude(row.get('kurtosis')),row['sampling'],row['reason']])
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

    extended_items = [it for it in results if it.get("extended_shewhart")]
    if extended_items:
        settings = wb.create_sheet("확장 Xbar-S 한계")
        settings.append(["변수", "관리도", "LCL", "CL", "UCL", "기준구간", "평균 범위 방식",
                         "비개입 확률", "n", "군내 표준편차 추정", "관리 여유", "예상 평균 하단", "예상 평균 상단",
                         "직후 기준 부분군", "직전 기준 부분군"])
        decisions = wb.create_sheet("확장 Xbar-S 판정")
        decisions.append(["변수", *extended_items[0]["extended_shewhart"]["table"].columns])
        for it in extended_items:
            result = it["extended_shewhart"]
            for name, limits in result["limits"].items():
                settings.append([str(it["column"]), name, *limits, result["reference"], result["method"],
                                 result["confidence"], result["n"], result["sigma"], result["margin"],
                                 result["lower"], result["upper"], result["counts"].get("교체 직후"),
                                 result["counts"].get("교체 직전")])
                settings.cell(settings.max_row, 1).data_type = "s"
                settings.cell(settings.max_row, 8).number_format = "0.00%"
            for row in result["table"].itertuples(index=False, name=None):
                decisions.append([str(it["column"]), *row])
                decisions.cell(decisions.max_row, 1).data_type = "s"
            settings.append([EXTENDED_GUIDE])
        for sheet in (settings, decisions):
            sheet.freeze_panes = "B2"
            _auto_width(sheet)
        decisions.auto_filter.ref = decisions.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _distribution_detail_metrics(it, record):
    from html import escape

    values = np.asarray(it.get('series', []), dtype=float)
    values = values[np.isfinite(values)]
    spread = ''
    if values.size:
        low, q1, q3, high = np.quantile(values, [0, .25, .75, 1])
        spread = (
            "<div class='distribution-spread'><h4>측정값 범위</h4><dl>"
            f"<div><dt>최솟값–최댓값</dt><dd>{low:.7g} ~ {high:.7g}</dd></div>"
            f"<div><dt>가운데 50% (Q1–Q3)</dt><dd>{q1:.7g} ~ {q3:.7g}</dd></div>"
            f"<div><dt>사분위 범위 (IQR)</dt><dd>{q3 - q1:.7g}</dd></div>"
            "</dl></div>"
        )

    interval = '—'
    interval_note = ''
    if values.size >= 2:
        lower, upper = mean_confidence_interval(np.mean(values), np.std(values, ddof=1), values.size)
        if np.isfinite([lower, upper]).all():
            interval = f'{lower:.7g} ~ {upper:.7g}'
            has_control_signal = any(it.get(key, 0) for key in ('x_ctrl_violation_count', 'r_ctrl_violation_count'))
            has_control_signal = has_control_signal or any((it.get('ksq_counts') or {}).values())
            if record.get('status') == 'rejected' or has_control_signal:
                interval_note = " <span class='warn'>참고값</span>"

    observed = fmt_ppm(it.get('emp_ppm'))
    if observed != '—':
        observed += ' PPM'
        original_n = it.get('original_n')
        if original_n is None and it.get('original_series') is not None:
            original_n = np.count_nonzero(np.isfinite(it['original_series']))
        count = it.get('spec_violation_count')
        if original_n and count is not None:
            observed += f' ({count:,}건 / {original_n:,}개)'
    predicted_value = it.get('ppm_ppk')
    predicted = fmt_ppm(predicted_value)
    if predicted != '—':
        predicted = '<0.001' if 0 < float(predicted_value) < .001 else predicted
        predicted += ' PPM (모형 추정)'

    return (
        f"{spread}<div class='distribution-spread distribution-estimates'><h4>평균 추정</h4><dl>"
        f"<div><dt>평균의 95% 신뢰구간</dt><dd>{interval}{interval_note}</dd></div>"
        f"</dl></div><div class='distribution-spread distribution-estimates'><h4>규격 이탈</h4><dl>"
        f"<div><dt>실측 PPM</dt><dd>{escape(observed)}</dd></div>"
        f"<div><dt>예측 PPM (전체산포)</dt><dd>{escape(predicted)}</dd></div>"
        "</dl></div>"
    )


def _distribution_summary(it, *, inline=False):
    from html import escape
    from spc_normality import STATUS_LABELS, pvalue_text, shape_text

    transformed = it.get('box_cox_applied')
    key = 'transformed' if transformed else 'individual'
    record = (it.get('normality_diagnostics') or {}).get(key) or {}
    test = record.get('test')
    status = STATUS_LABELS.get(record.get('status'), '검정 불가')
    p_value = pvalue_text(record.get('pvalue'))
    p_label = f"p {p_value}" if p_value.startswith('<') else f"p = {p_value}"
    verdict = f"{status} ({p_label})" if p_value != '—' else status
    verdict_class = ' bad' if record.get('status') == 'rejected' else ''
    rows = [('검정법', test)] if test else []
    if test == 'Anderson-Darling':
        rows.append(('정규분포 AD', _report_fmt_value(record.get('statistic'))))
    rows.extend([
        ('왜도', shape_text(record.get('skewness'))),
        ('첨도', shape_text(record.get('kurtosis'))),
    ])
    model = it.get('distribution_model') if it.get('distribution_mode') == 'nonnormal' else None
    if model and model.get('name'):
        rows.append(('적합 분포', f"{model['name']} · AD {_report_fmt_value(model.get('ad'))}"))
    entries = ''.join(f'<div><dt>{label}</dt><dd>{escape(value)}</dd></div>' for label, value in rows)
    unit = '변환값' if transformed else '개별 측정값'
    summary_class = ' distribution-inline' if inline else ''
    title = '' if inline else f"<h4>분포 진단 요약 · {unit}</h4>"
    inline_unit = f"<small>{unit}</small>" if inline else ''
    extra_metrics = '' if inline else _distribution_detail_metrics(it, record)
    return (f"<aside class='distribution-summary{summary_class}'>{title}{inline_unit}"
            f"<strong class='distribution-verdict{verdict_class}'>{escape(verdict)}</strong>"
            f"<dl>{entries}</dl>{extra_metrics}</aside>")


def _report_qr_html(share_url, meta, generated_at, variable_count):
    from html import escape
    from urllib.parse import urlsplit

    import qrcode
    from qrcode.exceptions import DataOverflowError
    from qrcode.image.svg import SvgPathFillImage

    share_url = share_url.strip()
    if share_url:
        try:
            parsed = urlsplit(share_url)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
                     and not any(char.isspace() or ord(char) < 32 for char in share_url))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('QR 공유 주소는 http:// 또는 https://로 시작하는 전체 주소를 입력하세요.')
        payload = share_url
        caption = '보고서 공유 링크'
    else:
        fields = (
            ('분석 ID', meta.get('analysis_id')),
            ('데이터', meta.get('data_name')),
            ('분석시각', meta.get('analyzed_at') or generated_at),
            ('변수 수', variable_count),
        )
        payload = '\n'.join(['SPC 분석 결과 리포트', *(f'{key}: {value}' for key, value in fields if value or value == 0)])
        caption = '보고서 식별 정보'

    try:
        qr = qrcode.make(payload, image_factory=SvgPathFillImage)
    except DataOverflowError as exc:
        raise ValueError('QR에 담을 정보가 너무 깁니다. 짧은 공유 주소를 입력하세요.') from exc
    encoded = base64.b64encode(qr.to_string()).decode('ascii')
    image_html = f"<img src='data:image/svg+xml;base64,{encoded}' alt='{caption} QR 코드'>"
    if share_url:
        image_html = f"<a href='{escape(share_url, quote=True)}'>{image_html}</a>"
    return f"<figure class='report-qr'>{image_html}<figcaption>{caption}</figcaption></figure>"


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
    *,
    qr_share_url: Optional[str] = None,
    published: bool = False,
) -> bytes:
    """분석 결과를 단일 HTML 리포트로 내보내기."""
    analysis_meta = {**summary_df.attrs.get('audit', {}), **(analysis_meta or {})}
    from datetime import datetime
    from html import escape
    import matplotlib.pyplot as plt
    from spc_charts import build_control_chart, build_histogram, build_qq_plot

    source_df, _, _, _ = _report_summary_spec(summary_df)
    result_by_name = {str(it['column']): it for it in results}
    extended_monitors = _extended_report_monitors(results)
    detail_ids = {str(it['column']): f'variable-{index}' for index, it in enumerate(results)}
    row_items = [result_by_name.get(str(name)) for name in source_df.get('변수', [])]
    pairs = [screening_index_pair(it) for it in row_items]
    for column, key in (('평가 상태', 'state'), ('폭 지수', 'width'), ('치우침 반영 지수', 'shifted')):
        source_df[column] = [pair[key] for pair in pairs]
    source_df['판정'] = [assess_process(it)['text'] if it is not None else '계산 불가' for it in row_items]
    source_df['관리이탈'] = [sum(it.get(key + ('_monitor' if it.get('control_phase') == 'phase2' else ''), 0)
                                    for key in ('x_ctrl_violation_count', 'r_ctrl_violation_count'))
                              if it is not None else None for it in row_items]
    _apply_extended_monitor_summary(source_df, extended_monitors)
    # Keep the full screening list; additional diagnostic fields belong in details.
    summary_columns = [
        '변수', '평가 상태', '판정', 'N', '폭 지수', '치우침 반영 지수', '신뢰도', '관리도', '평균', 'σ(전체)', 'σ(군내)',
        'Cp', 'Cpk', 'Cw', 'Cwk', 'PPM(Within)', 'Pp', 'Ppk', 'PPM(Overall)',
        'Cpm', 'Cpmk', '목표치(T)', '안정성', '군내/전체 지수 비율',
        'PPM(실측)', '규격이탈', '운영모드', 'X관리이탈', 'R관리이탈',
        '최근규격이탈', '최근관리이탈', '이상치제거', '관리이탈',
        '정규성 검정법', '정규성 p값', '정규성 판정',
    ]
    for key in ('Cw', 'Cwk'):
        if key not in source_df.columns:
            source_df[key] = [result_by_name.get(str(name), {}).get(key.lower())
                              for name in source_df.get('변수', pd.Series(index=source_df.index, dtype=str))]
    _df = source_df[[col for col in summary_columns if col in source_df.columns]].copy()
    cap_cols = [col for col in ('Cp', 'Cpk', 'Pp', 'Ppk', 'Cpm', 'Cpmk') if col in _df.columns]
    ppm_cols = [col for col in _df.columns if 'PPM' in col]
    cnt_cols = [col for col in _df.columns if '이탈' in col or '위반' in col]

    from spc_policy import reference_only_decision, REFERENCE_COMPARISON_LABEL

    def _cell_html(col, val, item):
        if col in ('폭 지수', '치우침 반영 지수'):
            pair = screening_index_pair(item)
            metric = pair['width_metric' if col == '폭 지수' else 'shifted_metric']
            cls = _report_metric_cls(item.get(metric), item, metric) if item and metric else 'na'
            cell = f"<span class='{cls}'>{escape(str(val))}</span>"
            decision = (item or {}).get('capability_criteria', {}).get('indices', {}).get(metric, {})
            if reference_only_decision(decision):
                cell += f"<small class='summary-target'>{REFERENCE_COMPARISON_LABEL}</small>"
            elif decision.get('required') is not None:
                cell += f"<small class='summary-target'>조정 목표값 {decision['required']:.3f}</small>"
            return cell
        sval, cls = _report_cell_display(col, val, cap_cols, ppm_cols, cnt_cols, item)
        if col in cap_cols and val is not None:
            sval = _report_fmt_value(val)
        esc = escape(sval)
        if col == '변수' and str(val) in detail_ids:
            esc = f"<a href='#{detail_ids[str(val)]}'>{esc}</a>"
        cell = f"<span class='{cls}'>{esc}</span>" if cls else esc
        if item and col in cap_cols:
            d = item.get('capability_criteria', {}).get('indices', {}).get(col.lower())
            if d and reference_only_decision(d):
                cell += f"<small class='summary-target'>{REFERENCE_COMPARISON_LABEL}</small>"
            elif d and d.get('required') is not None:
                cell += f"<small class='summary-target'>조정 목표값 {d['required']:.3f}</small>"
        return cell

    text_cols = {"변수", "평가 상태", "판정", "신뢰도", "관리도", "운영모드"}
    summary_views = {
        'core': ('핵심 요약', {'변수', '평가 상태', 'N', '폭 지수', '치우침 반영 지수', '규격이탈', '관리이탈'}),
        'capability': ('능력 지표', {'변수', '판정', 'Cp', 'Cpk', 'Cw', 'Cwk', 'Pp', 'Ppk', 'Cpm', 'Cpmk'}),
        'statistics': ('통계·정규성', {'변수', '평균', 'σ(전체)', 'σ(군내)', '정규성 검정법', '정규성 p값', '정규성 판정'}),
        'all': ('전체 정보', set(_df.columns)),
    }
    summary_controls_html = '<div class="summary-controls" role="group" aria-label="표시 항목">' + ''.join(
        f'<button type="button" data-summary-view="{key}" aria-pressed="{"true" if key == "core" else "false"}">{label}</button>'
        for key, (label, _) in summary_views.items()) + '</div>'

    def _col_class(col) -> str:
        classes = ['text'] if str(col) in text_cols else []
        classes += [f'view-{key}' for key, (_, columns) in summary_views.items() if col in columns]
        if col in ('변수', '판정'):
            classes.append('pin-name' if col == '변수' else 'pin-status')
        if col == '평가 상태':
            classes.append('pin-evaluation')
        return f" class='{' '.join(classes)}'"

    control_headers = {'X관리이탈': '평균 관리이탈', 'R관리이탈': '산포 관리이탈'} if extended_monitors else {}
    extended_summary_note = ('<p class="meta">확장 슈하르트의 관리이탈은 감시구간의 확장 X̄와 S 이탈 건수를 합산합니다.</p>'
                             if extended_monitors else '')
    th = "".join(f"<th{_col_class(c)}>{escape(control_headers.get(c, str(c)))}</th>" for c in _df.columns)
    trs = []
    for _, row in _df.iterrows():
        item = result_by_name.get(str(row.get('변수', '')))
        tds = "".join([f"<td{_col_class(c)}>{_cell_html(c, row[c], item)}</td>" for c in _df.columns])
        trs.append(f"<tr>{tds}</tr>")
    summary_table_html = f"<table class='summary' id='screening-summary' data-view='core'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

    detail_html = []
    for it in results:
        detail = _report_variable_detail(it, extended_monitors.get(str(it['column'])))
        col = escape(detail["column"])
        risk_cls = detail["risk_cls"]
        within_note = escape(detail["within_note"])
        capability_note = escape(detail["capability_note"])
        performance_note = escape(detail["performance_note"])

        metric_cards = ''.join(
            f"<div class='metric'><div class='k'>{escape(key)}</div>"
            f"<div class='v {cls}'>{escape(str(val))}</div></div>"
            for key, val, cls in detail["core_metrics"]
        )
        metric_tables = []
        for title, items in detail["metric_groups"]:
            rows = ''.join(
                f"<tr><th scope='row'>{escape(key)}</th><td><span class='{cls}'>{escape(str(val))}</span></td></tr>"
                for key, val, cls in items
            )
            metric_tables.append(f"<table><caption>{escape(title)}</caption><tbody>{rows}</tbody></table>")

        chart_main_html = ""
        is_extended = it.get("chart_usage_mode") == EXTENDED_MODE
        group_count = len(it.get('means', []))
        wide_px = min(3200, group_count * 18) if group_count > 65 else 0
        has_distribution_plots = include_histogram or include_qq or include_hist_qq
        if include_charts and not is_extended:
            def control_svg(figsize, display_mode='detail'):
                fig = build_control_chart(it, palette=palette, ksq_toggles=ksq_toggles,
                                          show_zones=show_zones, figsize=figsize, display_mode=display_mode)
                try:
                    return base64.b64encode(fig_to_bytes(fig, fmt='svg')).decode('ascii')
                finally:
                    plt.close(fig)

            try:
                standard_chart = control_svg((16.5, 7.2))
                print_img = ''
                if has_distribution_plots:
                    try:
                        print_chart = control_svg((17, 5.0), display_mode='print')
                        print_img = (f"<img class='chart chart-main print-control' alt='{col} 관리도' "
                                     f"src='data:image/svg+xml;base64,{print_chart}'>")
                    except Exception:
                        pass  # The standard image still fits within the print height limit.
                wide_source = ''
                if wide_px:
                    try:
                        wide_chart = control_svg((wide_px / 96, 7.2))
                        wide_source = f"<source media='screen' srcset='data:image/svg+xml;base64,{wide_chart}'>"
                    except Exception:
                        wide_px = 0
                scroll_attrs = " tabindex='0' role='region' aria-label='관리도 가로 스크롤'" if wide_px else ''
                picture_class = " class='screen-control'" if print_img else ''
                chart_main_html = (
                    f"<div class='var-chart' style='--control-min-width:{wide_px}px'{scroll_attrs}>"
                    f"<picture{picture_class}>{wide_source}<img class='chart chart-main' alt='{col} 관리도' "
                    f"src='data:image/svg+xml;base64,{standard_chart}'></picture>{print_img}</div>"
                )
            except Exception:
                chart_main_html = "<p class='chart-empty'>관리도 이미지를 생성할 수 없습니다.</p>"

        subcharts = []
        has_histogram = False
        if has_distribution_plots:
            if include_histogram or include_hist_qq:
                try:
                    def histogram_svg(range_mode, *, printing=False):
                        size = (4.65, 1.51) if printing else (7.25, 3.55)
                        fig = build_histogram(it, palette=palette, bins=hist_bins, figsize=size,
                                              range_mode=range_mode, print_layout=printing,
                                              report_layout=not printing)
                        try:
                            return base64.b64encode(fig_to_bytes(fig, fmt='svg', tight=False)).decode('ascii')
                        finally:
                            plt.close(fig)

                    b64_h = histogram_svg('spec')
                    zoom_histogram = ''
                    controls = ''
                    try:
                        b64_zoom = histogram_svg('data')
                        zoom_histogram = (f"<img class='chart chart-sub histogram-zoom' alt='{col} 히스토그램 · 분포 확대' "
                                          f"src='data:image/svg+xml;base64,{b64_zoom}'>")
                        mode_group = detail_ids[str(it['column'])] + '-histogram-mode'
                        controls = (
                            "<fieldset class='histogram-modes' aria-label='히스토그램 표시 범위'>"
                            f"<label><input type='radio' name='{mode_group}' value='zoom' checked> 분포 확대</label>"
                            f"<label><input type='radio' name='{mode_group}' value='spec'> 규격 전체</label></fieldset>"
                        )
                    except Exception:
                        pass  # The full specification view is already available.
                    print_histogram = ''
                    try:
                        b64_print_h = histogram_svg('spec', printing=True)
                        print_histogram = (f"<img class='chart chart-sub print-histogram' alt='{col} 히스토그램' "
                                           f"src='data:image/svg+xml;base64,{b64_print_h}'>")
                    except Exception:
                        pass  # Keep the standard histogram available for printing.
                    screen_class = ' screen-histogram' if print_histogram else ''
                    subcharts.append(
                        f"<div class='subchart-block histogram-block'>"
                        f"<div class='histogram-heading'><h4>히스토그램</h4>{controls}</div>"
                        f"<img class='chart chart-sub histogram-spec{screen_class}' alt='{col} 히스토그램 · 규격 전체' "
                        f"src='data:image/svg+xml;base64,{b64_h}'>{zoom_histogram}{print_histogram}</div>"
                    )
                    has_histogram = True
                except Exception:
                    pass
            if include_qq or include_hist_qq:
                try:
                    fig_q = build_qq_plot(it, palette=palette)
                    try:
                        b64_q = base64.b64encode(fig_to_bytes(fig_q, fmt='svg')).decode('ascii')
                    finally:
                        plt.close(fig_q)
                    subcharts.append(
                        f"<div class='subchart-block'><h4>Q-Q 플롯</h4>"
                        f"<img class='chart chart-sub' alt='{col} Q-Q 플롯' src='data:image/svg+xml;base64,{b64_q}'/>"
                        f"</div>"
                    )
                except Exception:
                    pass
        histogram_only = has_histogram and len(subcharts) == 1
        distribution_summary = _distribution_summary(it, inline=not histogram_only) if subcharts else ''
        side_summary = distribution_summary if histogram_only else ''
        inline_summary = distribution_summary if not histogram_only else ''
        text_diagnostics = diagnostics_html(it, compact=True) if not subcharts else ''
        histogram_class = ' has-histogram' if has_histogram else ''
        summary_class = ' has-distribution-summary' if histogram_only else ''
        chart_sub_html = (
            f"<section class='var-diagnostics'><h4>분포 진단 · {col}</h4>"
            f"<div class='subcharts{histogram_class}{summary_class}'>{''.join(subcharts)}{side_summary}</div>"
            f"{inline_summary}</section>"
        ) if subcharts else ''
        has_control_chart = chart_main_html.startswith('<div')
        print_layout = ' with-distribution-plots' if has_control_chart and subcharts else ''
        scroll_hint = "<p class='norm chart-scroll-hint'>차트가 화면보다 넓으면 좌우로 스크롤하세요.</p>" if wide_px and has_control_chart else ''

        extended_html = ""
        extended = it.get("extended_shewhart")
        if extended is not None:
            extended_image = ""
            if include_charts:
                from spc_charts import build_extended_figure
                fig = build_extended_figure(extended, palette)
                try:
                    svg = base64.b64encode(fig_to_bytes(fig, "svg")).decode("ascii")
                    extended_image = (f"<img class='chart extended-chart' alt='{EXTENDED_TITLE}' "
                                      f"src='data:image/svg+xml;base64,{svg}'>")
                finally:
                    plt.close(fig)
            extended_html = (
                f"<section style='break-inside:avoid'><h4>{EXTENDED_TITLE}</h4>"
                f"<p>{escape(calculation_note(extended))}</p><p>{EXTENDED_GUIDE}</p>"
                f"{extended_image}</section>"
            )
        elif is_extended and it.get("extended_shewhart_error"):
            extended_html = f"<p>{EXTENDED_TITLE} · {escape(it['extended_shewhart_error'])}</p>"
        detail_html.append(
            f"<section class='var{print_layout}' id='{detail_ids[str(it['column'])]}'>"
            f"<div class='var-overview'>"
            f"<div class='var-heading'>"
            f"<div class='var-head'><h3>{col}</h3><span class='pill {risk_cls}'>{detail['risk_label']}</span></div>"
            f"<div class='norm'>{escape(screening_index_pair(it)['state'])}</div></div>"
            f"<div class='metrics core-metrics'>{metric_cards}</div>"
            f"<div class='metric-tables'>{''.join(metric_tables)}</div>"
            f"{text_diagnostics}"
            f"<details class='analysis-notes'><summary>판정·분포 상세 설명</summary>"
            f"<div class='norm'>Cp·Cpk: {capability_note}</div>"
            f"<div class='norm'>Cw·Cwk: {within_note}</div>"
            f"<div class='norm'>{performance_note}</div>"
            f"{diagnostics_html(it)}</details>"
            f"{chart_sub_html}"
            f"{scroll_hint}"
            f"{chart_main_html}"
            f"</div>"
            f"{extended_html}"
            f"</section>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    qr_html = _report_qr_html(qr_share_url, analysis_meta, now, len(results)) if qr_share_url is not None else ''
    robots = '<meta name="robots" content="noindex, nofollow">' if published else ''
    meta_items = _report_meta_items(analysis_meta, now)
    meta_html = "".join(
        f"<div class='metric'><div class='k'>{escape(str(k))}</div><div class='v'>{escape(str(v))}</div></div>"
        for k, v in meta_items
    )
    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
{robots}
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
.report-intro {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
.report-qr {{ flex:0 0 auto; margin:0 0 8px; text-align:center; font-size:10px; }}
.report-qr img {{ display:block; width:94px; height:94px; margin:0 auto; }}
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
.summary-controls {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }}
.summary-controls button {{ padding:8px 16px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; color:#334155; cursor:pointer; font:inherit; }}
.summary-controls button[aria-pressed="true"] {{ background:#1e3a8a; color:#fff; border-color:#1e3a8a; }}
.summary-controls button:focus-visible {{ outline:3px solid #60a5fa; outline-offset:2px; }}
table.summary[data-view="core"] tr > :not(.view-core),
table.summary[data-view="capability"] tr > :not(.view-capability),
table.summary[data-view="statistics"] tr > :not(.view-statistics) {{ display:none; }}
table.summary .pin-name {{ position:sticky; left:0; min-width:96px; max-width:96px; width:96px; box-sizing:border-box; z-index:2; }}
table.summary .pin-status {{ position:sticky; left:96px; min-width:180px; max-width:220px; z-index:2; white-space:normal; }}
table.summary th.pin-name, table.summary th.pin-status {{ z-index:3; }}
table.summary[data-view="core"] .pin-evaluation {{ position:sticky; left:96px; min-width:180px; max-width:220px; z-index:2; white-space:normal; }}
table.summary[data-view="core"] th.pin-evaluation {{ z-index:3; }}
table.summary a {{ color:#1d4ed8; text-underline-offset:3px; }}
.var {{ scroll-margin-top:16px; }}
table.summary {{
  width: 100%;
  min-width: 720px;
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
table.summary td:first-child, table.summary td:last-child {{white-space:normal;overflow-wrap:anywhere;}}
.summary-target {{display:block;font-size:11px;color:#475569;margin-top:4px;font-weight:400;}}
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
.var-chart {{
  min-width:0;
  overflow-x:auto;
  margin-top:10px;
}}
.var-chart picture {{ display:block; min-width:var(--control-min-width, 0px); }}
.var-chart .print-control {{ display:none; }}
.var-chart:focus-visible {{ outline:2px solid #1d4ed8; outline-offset:2px; }}
.analysis-notes {{ margin:8px 0; }}
.analysis-notes summary {{ cursor:pointer; }}
.metrics {{
  display:grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 126px), 1fr)); gap:6px; margin-bottom:6px;
}}
.meta-metrics {{
  grid-template-columns: repeat(3, minmax(120px, 1fr));
}}
.metric {{
  border:1px solid var(--border-medium); border-radius:8px; padding:6px; background:#fff;
}}
.metric .k {{ font-size:12px; color:#475569; margin-bottom:3px; }}
.metric .v {{ font-size:14px; font-weight:700; line-height:1.15; }}
.core-metrics {{ grid-template-columns:repeat(7, minmax(0, 1fr)); }}
.core-metrics .metric:has(.v.bad) {{
  border-color:#dc2626;
  border-inline-start-width:3px;
}}
.metric-tables {{
  display:grid;
  grid-template-columns:minmax(0, 1.6fr) repeat(2, minmax(0, 1fr));
  align-items:start;
  gap:12px;
  margin:8px 0;
}}
.metric-tables table {{
  width:100%;
  border-collapse:collapse;
  font-size:12px;
  font-variant-numeric:tabular-nums;
}}
.metric-tables caption {{
  background:#eef2f7;
  color:#0f172a;
  text-align:left;
  font-weight:700;
  padding:4px 6px;
  border-bottom:1px solid #94a3b8;
}}
.metric-tables th, .metric-tables td {{
  padding:3px 6px;
  border-bottom:1px solid #e2e8f0;
}}
.metric-tables th {{ text-align:left; font-weight:400; color:#334155; }}
.metric-tables td {{ text-align:right; font-weight:700; }}
.norm {{ font-size:11px; color:#334155; margin-bottom:6px; }}
.chart {{
  width:100%; display:block; margin:0; border:1.5px solid var(--border-strong); border-radius:8px; background:#fff;
}}
.chart-main, .chart-sub {{ height:auto; }}
.chart.print-histogram, .chart.histogram-zoom {{ display:none; }}
.histogram-heading {{
  display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:4px 12px;
}}
.histogram-modes {{
  display:flex; flex-wrap:wrap; gap:4px 12px; margin:0 0 8px; padding:0; border:0; min-width:0;
}}
.histogram-modes label {{ display:inline-flex; align-items:center; gap:4px; cursor:pointer; }}
.histogram-modes input {{ margin:0; accent-color:#1d4ed8; }}
.var-diagnostics {{ margin:10px 0; }}
.var-diagnostics > h4 {{ margin:0 0 6px; font-size:14px; }}
.subcharts {{
  margin-top:8px;
  display:grid;
  grid-template-columns: repeat(2, minmax(0, 390px));
  gap:8px;
}}
.subchart-block {{ min-width:0; }}
.subchart-block:only-child {{ grid-column: 1 / -1; width:min(100%, 440px); }}
.subchart-block h4 {{ margin: 0 0 4px 0; font-size: 12px; color:#0f172a; }}
.has-distribution-summary {{ grid-template-columns:minmax(0, 440px) minmax(0, 1fr); gap:12px; }}
.histogram-block, .distribution-summary {{
  padding:9px 12px; border:1px solid var(--border-medium); border-radius:8px; background:#fff;
}}
.histogram-block .chart {{ border:0; }}
.distribution-summary h4 {{ margin:0 0 4px; font-size:12px; color:#0f172a; }}
.distribution-verdict {{ display:inline-block; font-size:14px; }}
.distribution-summary dl {{
  margin:8px 0 0; display:grid; grid-template-columns:max-content minmax(0,1fr);
  gap:4px 12px; font-size:12px;
}}
.distribution-summary dt {{ color:#334155; }}
.distribution-summary dd {{ margin:0; font-weight:700; }}
.distribution-spread {{ margin-top:16px; padding-top:12px; border-top:1px solid #dbe2ea; }}
.distribution-inline {{
  display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 10px;
  margin-top:6px; padding:4px 8px;
}}
.distribution-inline .distribution-verdict, .distribution-inline small {{ font-size:12px; }}
.distribution-inline dl {{
  display:flex; flex-wrap:wrap; gap:2px 8px; margin:0;
}}
.distribution-summary dl > div {{ display:contents; }}
.distribution-inline dl > div {{ display:flex; gap:4px; margin-inline-end:6px; }}
.distribution-inline dl > div:last-child {{ margin-inline-end:0; }}
.chart-sub {{ width:100%; object-fit:contain; }}
.chart-empty {{
  border:1px dashed #cbd5e1; border-radius:8px; padding:12px;
  color:#475569; background:#f8fafc;
}}
.print-note {{
  display:none;
}}
@media (max-width: 900px) {{
  .page {{ max-width: 100%; }}
}}
@media screen {{
  .has-distribution-summary {{ grid-template-columns:minmax(0, 2fr) minmax(250px, 1fr); }}
  .has-distribution-summary .distribution-summary {{ align-self:start; }}
  .histogram-block:has(input[value="zoom"]:checked) .chart.histogram-zoom {{ display:block; }}
  .histogram-block:has(input[value="zoom"]:checked) .chart.histogram-spec {{ display:none; }}
  .subcharts.has-histogram:not(.has-distribution-summary) {{
    grid-template-columns:repeat(2, minmax(0, 1fr));
  }}
  .has-histogram:not(.has-distribution-summary) .subchart-block {{
    padding:9px 12px; border:1px solid var(--border-medium); border-radius:8px; background:#fff;
  }}
  .has-histogram:not(.has-distribution-summary) .chart {{ border:0; }}
}}
@media screen and (max-width: 760px) {{
  .subcharts {{ grid-template-columns: minmax(0, 390px); }}
  .has-distribution-summary, .subcharts.has-histogram:not(.has-distribution-summary) {{
    grid-template-columns:minmax(0, 1fr);
  }}
  .core-metrics {{ grid-template-columns:repeat(auto-fit, minmax(min(100%, 118px), 1fr)); }}
  .metric-tables {{ grid-template-columns:1fr; }}
  .meta-metrics {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 120px), 1fr)); }}
}}
@page {{
  size: A4 landscape;
  margin: 6mm;
}}
@media print {{
  .summary-controls, .summary-screen-hint, .chart-scroll-hint {{ display:none !important; }}
  table.summary tr > th, table.summary tr > td {{ display:table-cell !important; }}
  table.summary .pin-name, table.summary .pin-status, table.summary .pin-evaluation {{ position:static !important; min-width:0 !important; max-width:none !important; width:auto !important; }}
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
  .report-qr img {{ width:24mm; height:24mm; }}
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
    break-inside: avoid;
    margin-top: 0;
    padding: 3mm;
    border: 1pt solid #1e293b !important;
  }}
  .var-overview {{ break-inside: avoid; }}
  .var .analysis-notes {{ display:none; }}
  .var-head {{
    margin-bottom: 0;
    padding-bottom: 2mm;
    flex: 0 0 auto;
    border-bottom: 0.9pt solid #334155 !important;
  }}
  .var-chart {{
    overflow: visible;
    margin-top: 2mm;
    break-inside: avoid;
  }}
  .var-chart picture {{ min-width:0; }}
  .var-chart .screen-control {{ display:none; }}
  .var-chart .print-control {{ display:block; }}
  .metrics {{
    grid-template-columns: repeat(7, minmax(0, 1fr));
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
  .core-metrics .metric:has(.v.bad) {{ border-inline-start:2pt solid #991b1b !important; }}
  .metric-tables {{ gap:3mm; margin:1mm 0; break-inside:avoid; }}
  .metric-tables table {{ font-size:8pt; line-height:1.1; }}
  .metric-tables caption {{ padding:0.4mm 1mm; }}
  .metric-tables th, .metric-tables td {{ padding:0.1mm 1mm; }}
  .norm {{ font-size: 8pt; margin-bottom: 2mm; }}
  .chart {{
    width: 100%;
    height: auto;
    border: 0.9pt solid #1e293b !important;
    border-radius: 4px;
    background: #fff;
    object-fit: contain;
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  .chart-main {{ max-height: 150mm; }}
  .extended-chart {{ max-height: 135mm; }}
  .chart-sub {{ max-height: 110mm; }}
  .with-distribution-plots .chart-main {{ max-height: 88mm; }}
  .with-distribution-plots .chart-sub {{ max-height: 42mm; }}
  .with-distribution-plots .subcharts {{ grid-template-columns:repeat(2, minmax(0, 61mm)); }}
  .with-distribution-plots .subchart-block:only-child {{ width:min(100%, 61mm); }}
  .var .has-histogram {{ grid-template-columns:minmax(0, 118mm) minmax(0, 61mm); }}
  .var .has-distribution-summary {{ grid-template-columns:minmax(0, 118mm) minmax(0, 1fr); gap:4mm; }}
  .var .has-distribution-summary .subchart-block {{ grid-column:auto; width:100%; }}
  .chart.screen-histogram {{ display:none; }}
  .histogram-modes, .distribution-spread:not(.distribution-estimates), .distribution-estimates h4 {{ display:none; }}
  .distribution-estimates {{ margin-top:0.6mm; padding-top:0.6mm; }}
  .histogram-block .chart.print-histogram {{ display:block; border:0 !important; border-radius:0; }}
  .distribution-summary, .histogram-block {{
    border:0.75pt solid #64748b;
    border-radius:4px; padding:1mm 2mm; color:#0f172a;
  }}
  .var .subchart-block h4, .distribution-summary h4 {{ margin:0 0 0.6mm; font-size:9pt; }}
  .distribution-verdict {{ font-size:10pt; }}
  .distribution-summary dl {{
    margin:1.2mm 0 0;
    gap:1mm 3mm; font-size:9pt;
  }}
  .distribution-summary.distribution-inline {{
    margin-top:1mm; padding:0 0.5mm; border:0; gap:0.5mm 2mm;
  }}
  .distribution-inline .distribution-verdict, .distribution-inline small,
  .distribution-inline dl {{ font-size:8pt; line-height:1.1; }}
  .distribution-inline dl {{ margin:0; gap:0.5mm 1mm; }}
  .with-distribution-plots .metrics {{ margin-bottom:1mm; gap:0.8mm; }}
  .with-distribution-plots .metric {{ padding:0.6mm; }}
  .with-distribution-plots .metric .k {{ margin-bottom:0; font-size:7pt; line-height:1.15; }}
  .with-distribution-plots .metric .v {{ font-size:8.5pt; line-height:1.15; }}
  .with-distribution-plots .var-head {{ padding-bottom:1mm; }}
  .with-distribution-plots .norm {{ margin-bottom:1mm; }}
  .with-distribution-plots .var-diagnostics > h4 {{ font-size:9pt; margin:0; }}
  .var-diagnostics {{ margin: 0; padding-top: 2mm; break-inside:avoid; }}
  .subcharts {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2mm;
    margin-top: 2mm;
  }}
  .subchart-block h4 {{ font-size: 8.5pt; margin: 0 0 1mm 0; }}
}}
</style>
</head>
<body>
  <div class="page">
  <header class="print-cover">
  <div class="print-note">인쇄 설정: A4, 가로, 배경 그래픽 켜기</div>
  <div class="report-intro">
    <div>
      <h1>SPC 분석 결과 리포트</h1>
      <div class="meta">생성 시각: {escape(now)} · 변수 수: {len(results):,}개</div>
      <div class="meta">실측 PPM·규격이탈은 전처리 전 원본 유효 측정값 기준입니다. 관리도·능력 지수는 분석 대상 데이터 기준입니다.</div>
    </div>
    {qr_html}
  </div>
  <div class="card">
    <h2>분석 조건 요약</h2>
    <div class="metrics meta-metrics">{meta_html}</div>
  </div>
  </header>
  <section class="card print-summary">
    <h2>선별 요약</h2>
    <p class="meta">지수 아래는 조정 목표값입니다. 자세한 진단은 항목별 결과에서 확인하세요.</p>
    {summary_controls_html}
    <p class="meta summary-screen-hint">핵심 요약은 능력 적용 가능 시 Cp·Cpk, 그 외에는 Pp·Ppk 참고값을 표시합니다. 변수명을 누르면 상세 정보와 관리도로 이동합니다. 인쇄·PDF에는 전체 요약 열이 표시됩니다.</p>
    {extended_summary_note}
    <details><summary>지수 해석 안내</summary><p class="norm">{escape(WITHIN_CAPABILITY_NOTICE)}</p></details>
    <div class="summary-wrap">{summary_table_html}</div>
  </section>
  <section class="print-details">
    <h2 class="details-heading">변수별 상세</h2>
    {''.join(detail_html)}
  </section>
  </div>
<script>
document.querySelectorAll('[data-summary-view]').forEach(function(button) {{
  button.addEventListener('click', function() {{
    document.getElementById('screening-summary').dataset.view = button.dataset.summaryView;
    document.querySelectorAll('[data-summary-view]').forEach(function(other) {{
      other.setAttribute('aria-pressed', String(other === button));
    }});
  }});
}});
</script>
</body>
</html>"""

    return html_doc.encode("utf-8")


def publish_analysis_html(report_dir: str, public_base_url: str, **export_options) -> Tuple[str, bytes]:
    """Write an HTML report to a configured static web directory and return its URL."""
    import os
    import secrets
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from urllib.parse import urlsplit

    if not report_dir.strip():
        raise ValueError('공개 보고서 저장 폴더를 설정하세요.')
    folder = Path(report_dir).resolve()
    if not folder.is_dir():
        raise ValueError('공개 보고서 저장 폴더에 접근할 수 없습니다.')
    public_base_url = public_base_url.strip().rstrip('/')
    address = urlsplit(public_base_url)
    if (address.scheme != 'https' or not address.hostname or address.username
            or address.query or address.fragment or any(char.isspace() for char in public_base_url)):
        raise ValueError('공개 보고서 기본 주소를 HTTPS 경로로 설정하세요.')

    filename = secrets.token_urlsafe(24) + '.html'
    public_url = f'{public_base_url}/{filename}'
    html = export_analysis_html(**export_options, qr_share_url=public_url, published=True)
    temp_path = None
    try:
        with NamedTemporaryFile(dir=folder, prefix='.report-', suffix='.tmp', delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(html)
        if os.name != 'nt':
            temp_path.chmod(0o644)
        os.replace(temp_path, folder / filename)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return public_url, html


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
def fig_to_bytes(fig, fmt: str = 'png', dpi: int = 120, *, tight: bool = True) -> bytes:
    """Matplotlib Figure를 bytes로 변환 (Streamlit st.image 용)."""
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches='tight' if tight else None)
    buf.seek(0)
    return buf.getvalue()
