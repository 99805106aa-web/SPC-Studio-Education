# -*- coding: utf-8 -*-
"""
attr_charts.py
==============
계수형(Attribute) SPC 관리도 차트 생성 모듈 (Matplotlib 기반, GUI 의존 없음)
- p (불량률), np (불량수), c (결점수), u (단위당 결점수) 관리도
- 시료크기 막대 차트, 요약 패널, 파레토 차트, 2×2 개요 그림
참고: web_spc/spc_charts.py 패턴을 따름
"""

import math
import numpy as np
import matplotlib
matplotlib.use("Agg")  # 서버/헤드리스 환경
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import AutoMinorLocator, PercentFormatter
from typing import Any, Dict, List, Optional, Tuple
from attr_presentation import summary_lines

# ──────────────────────────── 폰트 설정 ────────────────────────────
from matplotlib import font_manager

_KR_FONTS = ["Malgun Gothic", "NanumGothic", "Nanum Gothic", "AppleGothic",
             "Noto Sans KR", "Noto Sans CJK KR", "Batang", "Dotum"]
_available = {f.name for f in font_manager.fontManager.ttflist}
_chosen_font = next((f for f in _KR_FONTS if f in _available), "DejaVu Sans")

plt.rcParams.update({
    'font.family': _chosen_font,
    'axes.unicode_minus': False,
    'mathtext.fontset': 'dejavusans',
    'path.simplify': True,
    'path.simplify_threshold': 1.0,
    'agg.path.chunksize': 10000,
})

# ──────────────────────────── 팔레트/테마 ────────────────────────────
PALETTES = {
    'trust': {
        'primary': '#1E3A8A', 'accent': '#0E7490', 'success': '#0F766E',
        'warning': '#B45309', 'danger': '#B42318', 'muted': '#475569',
        'mean': '#0369A1', 'usl': '#C2410C', 'lsl': '#1D4ED8',
        'grid': '#D3DCE8', 'bg': '#F2F5FA', 'ax_bg': '#FFFFFF', 'text': '#1E293B',
    },
    'analytics': {
        'primary': '#1D4ED8', 'accent': '#0F766E', 'success': '#047857',
        'warning': '#92400E', 'danger': '#B42318', 'muted': '#475569',
        'mean': '#9A4B00', 'usl': '#B42318', 'lsl': '#2453A6',
        'grid': '#D6DFEA', 'bg': '#F4F7FB', 'ax_bg': '#FFFFFF', 'text': '#1E293B',
    },
    'dark': {
        'primary': '#60A5FA', 'accent': '#22D3EE', 'success': '#34D399',
        'warning': '#FBBF24', 'danger': '#F87171', 'muted': '#CBD5E1',
        'mean': '#FBBF24', 'usl': '#FB7185', 'lsl': '#93C5FD',
        'grid': '#334155', 'bg': '#0F172A', 'ax_bg': '#111827', 'text': '#E2E8F0',
    },
}
DEFAULT_PALETTE = 'trust'

# ──────────────────────────── 관리도 유형별 한국어 라벨 ────────────────────────────
_CHART_TYPE_KR = {
    'p':  'P (불량률)',
    'np': 'NP (불량수)',
    'c':  'C (결점수)',
    'u':  'U (단위당 결점수)',
}

_Y_LABEL_KR = {
    'p':  '불량률 P',
    'np': '불량수 NP',
    'c':  '결점수 C',
    'u':  '단위당 결점수 U',
}


# ──────────────────────────── 내부 유틸리티 ────────────────────────────

def _apply_palette(palette_name: str = DEFAULT_PALETTE) -> dict:
    """팔레트를 rcParams에 적용하고 색상 사전을 반환한다."""
    p = PALETTES.get(palette_name, PALETTES[DEFAULT_PALETTE])
    plt.rcParams.update({
        'figure.facecolor': p['bg'],
        'axes.facecolor': p['ax_bg'],
        'axes.edgecolor': p['grid'],
        'text.color': p['text'],
        'axes.labelcolor': p['text'],
        'xtick.color': p['text'],
        'ytick.color': p['text'],
        'grid.color': p['grid'],
        'axes.grid': True,
        'grid.alpha': 0.7,
        'font.size': 11,
        'axes.labelsize': 11,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10,
        'legend.fontsize': 10,
        'legend.labelcolor': p['text'],
    })
    return p


def _fmt(v, decimals: int = 4) -> str:
    """숫자 포맷: None/NaN이면 '--' 반환."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "--"
    return f"{v:.{decimals}f}"


def _is_variable_limit(chart_type: str) -> bool:
    """p, u 차트는 시료크기에 따라 관리한계가 변동(배열)한다."""
    return chart_type in ('p', 'u')


def _ensure_array(val, length: int) -> np.ndarray:
    """스칼라 또는 리스트를 numpy 배열로 변환한다."""
    arr = np.asarray(val, dtype=float)
    if arr.ndim == 0:
        arr = np.full(length, float(arr))
    return arr


def _avg_if_array(val) -> float:
    """배열이면 평균, 스칼라면 그대로 반환."""
    arr = np.asarray(val, dtype=float)
    if arr.ndim == 0:
        return float(arr)
    return float(np.nanmean(arr))


def _shade_phase_window(ax, C: dict, baseline_count: int, total_count: int):
    """Phase 2 모드일 때 기준구간/감시구간 영역을 음영 처리한다."""
    ref = max(0, min(int(baseline_count), int(total_count)))
    if ref <= 0 or ref >= total_count:
        return
    ax.axvspan(-0.5, ref - 0.5, facecolor=C['accent'], alpha=0.045, zorder=0.1)
    ax.axvline(ref - 0.5, color=C['muted'], linestyle=':', linewidth=1.1, alpha=0.9)
    ax.text(
        0.01, 0.98, "기준구간",
        transform=ax.transAxes, ha='left', va='top', fontsize=7.5,
        color=C['muted'],
        bbox=dict(boxstyle='round,pad=0.18', facecolor=C['ax_bg'], edgecolor='none', alpha=0.8),
    )
    ax.text(
        0.99, 0.98, "감시구간",
        transform=ax.transAxes, ha='right', va='top', fontsize=7.5,
        color=C['muted'],
        bbox=dict(boxstyle='round,pad=0.18', facecolor=C['ax_bg'], edgecolor='none', alpha=0.8),
    )


# ════════════════════════════════════════════════════════════════════
# 1. 계수형 관리도 (p / np / c / u)
# ════════════════════════════════════════════════════════════════════

def build_attribute_control_chart(
    result: dict,
    palette: str = 'trust',
    figsize: tuple = (13, 6),
) -> Figure:
    """
    계수형 관리도 단일 패널을 생성한다.

    Parameters
    ----------
    result : dict
        필수 키:
          chart_type : str — 'p', 'np', 'c', 'u'
          values     : list/array — 관리 통계량 (p값, np, c, u)
          cl         : float — 중심선 (p_bar, np_bar, c_bar, u_bar)
          ucl        : float 또는 array — 상한 관리한계
          lcl        : float 또는 array — 하한 관리한계
          violations : dict — {'T1': set(), 'T2': set(), 'T3': set(), 'T4': set()}
        선택 키:
          sample_sizes      : list/array — 각 부분군 시료 크기 (p/np 차트)
          inspection_units  : list/array — 각 부분군 검사 단위 수 (c/u 차트)
          baseline_count    : int — Phase 2 기준구간 부분군 수
          control_phase     : str — 'phase1' 또는 'phase2'
          control_phase_label : str — '해석용' 또는 '관리용'
    palette : str
        팔레트 이름 ('trust', 'analytics', 'dark')
    figsize : tuple
        그림 크기

    Returns
    -------
    Figure
        Matplotlib Figure 객체
    """
    C = _apply_palette(palette)

    chart_type = result['chart_type']  # 'p', 'np', 'c', 'u'
    values = np.asarray(result['values'], dtype=float)
    m = len(values)
    cl = float(result['cl'])
    ucl_raw = result['ucl']
    lcl_raw = result['lcl']
    violations = result.get('violations', {f'T{i}': set() for i in range(1, 5)})
    control_phase = str(result.get('control_phase', 'phase1'))
    phase_label = str(result.get('control_phase_label',
                                 '해석용' if control_phase != 'phase2' else '관리용'))
    baseline_count = int(result.get('baseline_display_count', result.get('baseline_count', m)))

    # UCL/LCL 배열화
    ucl_arr = _ensure_array(ucl_raw, m)
    lcl_arr = _ensure_array(lcl_raw, m)
    limit_format = '.3%' if chart_type == 'p' else '.6g'
    format_limit = lambda value: format(value, limit_format)

    # ── Figure 생성 ──
    fig, ax = plt.subplots(figsize=figsize, facecolor=C['ax_bg'])
    ax.set_facecolor(C['ax_bg'])

    xs = np.arange(m)

    # ── 데이터 플롯 ──
    ax.plot(xs, values, marker='o', markersize=4.8, color=C['primary'],
            linewidth=1.8, label=_CHART_TYPE_KR.get(chart_type, chart_type), zorder=4)

    # ── 중심선 ──
    ax.axhline(cl, linestyle='--', color=C['muted'], linewidth=1.5, label=f'CL {format_limit(cl)}', zorder=3)

    # ── 관리한계 (UCL / LCL) ──
    for label, limits in (('UCL', ucl_arr), ('LCL', lcl_arr)):
        if not limits.size:
            continue
        if not np.all(limits == limits[-1]):
            ax.plot(np.r_[-.5, xs, m-.5], np.r_[limits[0], limits, limits[-1]],
                    drawstyle='steps-mid', color=C['danger'], linewidth=1.4,
                    linestyle=(0, (6, 4)), label=f'{label} · 부분군별', zorder=3)
        elif np.isfinite(limits[0]):
            ax.axhline(limits[0], linestyle=(0, (6, 4)), color=C['danger'],
                       linewidth=1.4, label=f'{label} {format_limit(limits[0])}', zorder=3)

    # ── 위반점 마킹 ──
    mark_map = {
        'T1': dict(marker='o', s=100, c=C['danger'], zorder=10,
                   edgecolors='white', linewidths=1.0, label='T1 (한계 초과)'),
        'T2': dict(marker='s', s=65, c=C['accent'], zorder=8,
                   edgecolors='white', linewidths=0.7, label='T2 (연속 규칙)'),
        'T3': dict(marker='^', s=70, c=C['warning'], zorder=8,
                   edgecolors='white', linewidths=0.7, label='T3 (경향)'),
        'T4': dict(marker='D', s=60, c=C['muted'], zorder=8,
                   edgecolors='white', linewidths=0.7, label='T4 (기타)'),
    }
    legend_added = set()
    for key, cfg in mark_map.items():
        pts = sorted(violations.get(key, set()))
        if pts:
            valid_pts = [p for p in pts if 0 <= p < m]
            if valid_pts:
                scatter_cfg = {k: v for k, v in cfg.items() if k != 'label'}
                lbl = cfg['label'] if key not in legend_added else None
                ax.scatter(valid_pts, [values[i] for i in valid_pts],
                           label=lbl, **scatter_cfg)
                legend_added.add(key)

    # ── X축 범위 & Phase 음영 ──
    ax.set_xlim(-0.5, m - 0.5)
    if control_phase == 'phase2':
        _shade_phase_window(ax, C, baseline_count, m)

    # ── 제목 / 축 라벨 ──
    chart_kr = _CHART_TYPE_KR.get(chart_type, chart_type)
    fig.suptitle(f'{chart_kr} 관리도 · {phase_label}', x=.06, y=.982,
                 ha='left', fontweight='bold', fontsize=14)
    ax.set_xlabel('부분군 번호', fontsize=10)
    ax.set_ylabel('불량률 (%)' if chart_type == 'p' else _Y_LABEL_KR[chart_type], fontsize=10)
    if chart_type == 'p':
        from matplotlib.ticker import PercentFormatter
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1))
    ax.grid(False, axis='x')
    ax.spines[['top', 'right']].set_visible(False)

    # ── X 틱 라벨 ──
    x_labels = result.get('x_labels', [])
    tick_labels = list(x_labels) if x_labels and len(x_labels) == m else [str(i + 1) for i in range(m)]
    if m > 20:
        step = math.ceil(m / 20)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels(tick_labels[::step], rotation=30, ha='right', fontsize=9.5)
    else:
        ax.set_xticks(xs)
        ax.set_xticklabels(
            tick_labels, rotation=30 if x_labels else 0,
            ha='right' if x_labels else 'center', fontsize=9.5
        )

    ax.tick_params(axis='both', which='major', direction='in', labelsize=9.5)
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.tick_params(axis='y', which='minor', direction='in', length=3)
    ax.margins(y=0.12)
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1), frameon=False, fontsize=8.5, ncol=4)

    fig.tight_layout(pad=1.0, rect=(0, 0, 1, .92))
    return fig


# ════════════════════════════════════════════════════════════════════
# 2. 누적 불량/결점 추이 차트
# ════════════════════════════════════════════════════════════════════

def build_cumulative_defect_chart(
    result: dict,
    palette: str = 'trust',
    figsize: tuple = (7, 4)
) -> Figure:
    """누적 불량/결점 추이 차트 생성"""
    C = _apply_palette(palette)
    defects = result.get('defects', [])
    sizes = np.asarray(
        result.get('sample_sizes', result.get('inspection_units', [])),
        dtype=float
    )
    chart_type = result.get('chart_type', 'p')

    if len(sizes) == 0 or len(defects) == 0:
        fig, ax = plt.subplots(figsize=figsize, facecolor=C['bg'])
        ax.set_facecolor(C['ax_bg'])
        ax.text(0.5, 0.5, '데이터 없음', ha='center', va='center')
        ax.axis('off')
        return fig

    m = len(defects)
    xs = np.arange(m)

    cum_defects = np.cumsum(defects)
    cum_sizes = np.cumsum(sizes)

    if chart_type in ('p', 'np'):
        cum_rates = cum_defects / cum_sizes * 100
        ylabel = '%불량품'
        title = '누적 %불량품'
    else:
        cum_rates = cum_defects / cum_sizes
        ylabel = '누적 단위당 결점'
        title = '누적 단위당 결점'

    fig, ax = plt.subplots(figsize=figsize, facecolor=C['bg'])
    ax.set_facecolor(C['ax_bg'])

    # 추이선
    ax.plot(xs, cum_rates, marker='o', color=C['primary'], linewidth=1.8, markersize=5, zorder=4)

    # 평균선 (마지막 누적값)
    final_rate = cum_rates[-1]
    ax.axhline(final_rate, linestyle='--', color=C['success'], alpha=0.8, zorder=3, label=f'최종 {title}: {final_rate:.2f}')

    # 품질 관리 목표선(Target) 가이드라인 렌더링 지원
    target_rate = result.get('target_rate', result.get('target', None))
    if target_rate is not None:
        try:
            tr = float(target_rate)
            if chart_type in ('p', 'np') and tr <= 1.0:
                tr_disp = tr * 100.0
            else:
                tr_disp = tr
            ax.axhline(tr_disp, linestyle=':', color=C['danger'], linewidth=1.3, alpha=0.9, zorder=3, label=f'목표 {title}: {tr_disp:.2f}')
        except (ValueError, TypeError):
            pass

    # 배경 그리드선
    ax.grid(True, axis='both', linestyle='--', color=C['grid'], alpha=0.6, zorder=1)

    ax.set_xlim(-0.5, m - 0.5)
    ax.set_title(title, fontweight='bold', fontsize=11, pad=10)
    ax.set_xlabel('표본', fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)

    # X 틱
    x_labels = result.get('x_labels', [])
    if x_labels and len(x_labels) == m:
        tick_labels = x_labels
    else:
        x_labels = result.get('x_labels', [])
        tick_labels = list(x_labels) if x_labels and len(x_labels) == m else [str(i + 1) for i in range(m)]

    if m > 25:
        step = math.ceil(m / 25)
        ax.set_xticks(xs[::step])
        ax.set_xticklabels(tick_labels[::step], rotation=30, ha='right', fontsize=9)
    else:
        ax.set_xticks(xs)
        ax.set_xticklabels(tick_labels, rotation=30 if x_labels else 0, ha='right' if x_labels else 'center', fontsize=9)

    ax.tick_params(axis='both', which='major', direction='in', labelsize=7)
    ax.legend(loc='upper right', frameon=True, framealpha=1, fontsize=10)
    ax.margins(y=0.15)

    fig.tight_layout(pad=0.8)
    return fig



# ════════════════════════════════════════════════════════════════════
# 3. 요약 패널
# ════════════════════════════════════════════════════════════════════

def build_summary_panel(
    result: dict,
    capability: dict,
    palette: str = 'trust',
    figsize: tuple = (7, 6),
) -> Figure:
    """
    텍스트 기반 요약 그림을 생성한다.

    Parameters
    ----------
    result : dict
        관리도 분석 결과 (chart_type, values, cl, ucl, lcl, violations,
        sample_sizes/inspection_units, baseline_count 등)
    capability : dict
        공정능력 지표 (dpu, dpmo, sigma_level, yield_pct, ppm 등)
    palette : str
    figsize : tuple

    Returns
    -------
    Figure
    """
    C = _apply_palette(palette)

    chart_type = result.get('chart_type', 'p')
    values = np.asarray(result.get('values', []), dtype=float)
    m = len(values)
    cl = result.get('cl', np.nan)
    ucl_raw = result.get('ucl', np.nan)
    lcl_raw = result.get('lcl', np.nan)
    violations = result.get('violations', {f'T{i}': set() for i in range(1, 5)})
    control_phase = str(result.get('control_phase', 'phase1'))
    phase_label = str(result.get('control_phase_label',
                                 '해석용' if control_phase != 'phase2' else '관리용'))

    # 시료크기/검사단위 합계 (이상치 제거 등 전처리가 반영된 분석 결과 데이터 사용)
    sizes = np.asarray(
        result.get('sample_sizes', result.get('inspection_units', [])),
        dtype=float
    )
    total_inspected = float(result.get('total_inspected', result.get('total_units', 0)))
    total_defects = int(result.get('total_defects', 0))
    if total_inspected == 0 and len(sizes) > 0:
        total_inspected = float(np.nansum(sizes))

    # UCL/LCL 평균 (변동한계인 경우)
    ucl_arr = _ensure_array(ucl_raw, m) if m > 0 else np.array([np.nan])
    lcl_arr = _ensure_array(lcl_raw, m) if m > 0 else np.array([np.nan])
    ucl_display = _avg_if_array(ucl_raw)
    lcl_display = _avg_if_array(lcl_raw)
    is_var = _is_variable_limit(chart_type)

    # 공정능력 지표
    dpu = capability.get('DPU', capability.get('dpu', np.nan))
    dpmo = capability.get('DPMO', capability.get('dpmo', np.nan))
    sigma_level = capability.get('sigma_level', capability.get('sigma_level', np.nan))
    process_z = capability.get('process_z', np.nan)
    yield_pct = capability.get('yield_pct', capability.get('yield_pct', np.nan))
    ppm = capability.get('PPM', capability.get('ppm', np.nan))

    # ── Figure 생성 ──
    fig, ax = plt.subplots(figsize=figsize, facecolor=C['bg'])
    ax.set_facecolor(C['ax_bg'])
    ax.axis('off')

    chart_kr = _CHART_TYPE_KR.get(chart_type, chart_type)
    cl_label = {
        'p': 'P̄ (평균 불량률)',
        'np': 'NP̄ (평균 불량수)',
        'c': 'C̄ (평균 결점수)',
        'u': 'Ū (평균 단위당 결점수)',
    }.get(chart_type, 'CL')

    ucl_label = f"UCL {'(평균)' if is_var else ''}"
    lcl_label = f"LCL {'(평균)' if is_var else ''}"

    # 위반 건수
    viol_counts = {ti: len(violations.get(ti, set())) for ti in ('T1', 'T2', 'T3', 'T4')}
    viol_str = "  /  ".join(f"{k}: {v}건" for k, v in viol_counts.items())

    # 라인 데이터 구성
    lines = summary_lines(result, capability)
    # 계산되지 않은("--", "—") 항목 필터링
    lines = [(lab, val) for lab, val in lines if val not in ("--", "—")]

    # 제목
    ax.set_title(f'{chart_kr} — 요약 정보', fontsize=12, fontweight='bold',
                 color=C['text'], pad=14)

    # 배경 박스
    ax.add_patch(Rectangle(
        (0.01, 0.01), 0.98, 0.98, linewidth=1,
        edgecolor=C['grid'], facecolor=C['ax_bg'], alpha=0.92,
        transform=ax.transAxes, zorder=1,
    ))

    y = 0.93
    total_lines = sum(1 for _, _ in lines if _ != "" or _ == "")
    step = 0.87 / max(total_lines - 1, 1)

    for lab, val in lines:
        if lab == "" and val == "":
            # 구분선
            ax.plot([0.05, 0.95], [y + step * 0.3, y + step * 0.3],
                    color=C['grid'], linewidth=0.6, transform=ax.transAxes)
            y -= step * 0.5
            continue
        ax.text(0.06, y, f"{lab} :", color=C['muted'], fontsize=10.5, va='center',
                ha='left', transform=ax.transAxes, zorder=2)
        ax.text(0.52, y, str(val), color=C['text'], fontsize=10.5, va='center',
                ha='left', transform=ax.transAxes, zorder=2, fontweight='bold')
        y -= step

    fig.tight_layout(pad=0.8)
    return fig


# ════════════════════════════════════════════════════════════════════
# 4. 파레토 차트
# ════════════════════════════════════════════════════════════════════

def build_pareto_chart(
    categories: list,
    counts: list,
    palette: str = 'trust',
    figsize: tuple = (10, 5.5),
    title: str = '불량 유형별 파레토 차트',
) -> Figure:
    """
    파레토 차트 (내림차순 막대 + 누적 비율 곡선)를 생성한다.

    Parameters
    ----------
    categories : list[str]
        불량/결점 유형 이름 목록
    counts : list[int]
        각 유형별 발생 건수
    palette : str
    figsize : tuple
    title : str

    Returns
    -------
    Figure
    """
    C = _apply_palette(palette)

    # 내림차순 정렬
    paired = sorted(zip(counts, categories), reverse=True)
    sorted_counts = [p[0] for p in paired]
    sorted_cats = [p[1] for p in paired]
    total = sum(sorted_counts) if sorted_counts else 1

    # 누적 비율 계산
    cumulative = []
    running = 0
    for c in sorted_counts:
        running += c
        cumulative.append(running / total * 100.0)

    m = len(sorted_cats)
    xs = np.arange(m)

    fig, ax1 = plt.subplots(figsize=figsize, facecolor=C['bg'])
    ax1.set_facecolor(C['ax_bg'])

    # 막대 차트 (왼쪽 y축: 건수)
    bars = ax1.bar(xs, sorted_counts, color=C['primary'], edgecolor='white',
                   linewidth=0.5, alpha=0.88, zorder=3, label='건수')

    # 각 막대 위에 건수 표시
    for i, (x_pos, cnt) in enumerate(zip(xs, sorted_counts)):
        ax1.text(x_pos, cnt + total * 0.01, str(cnt), ha='center', va='bottom',
                 fontsize=10, color=C['text'], fontweight='bold')

    ax1.set_xlabel('불량/결점 유형', fontsize=10)
    ax1.set_ylabel('건수', fontsize=10, color=C['primary'])
    ax1.tick_params(axis='y', labelcolor=C['primary'])
    ax1.set_xticks(xs)
    ax1.set_xticklabels(sorted_cats, rotation=35, ha='right', fontsize=10.5)

    # 누적 비율 선 (오른쪽 y축: %)
    ax2 = ax1.twinx()
    ax2.plot(xs, cumulative, marker='D', markersize=5, color=C['danger'],
             linewidth=2.2, zorder=5, label='누적 비율 (%)')
    ax2.set_ylabel('누적 비율 (%)', fontsize=10, color=C['danger'])
    ax2.set_ylim(0, 110)
    ax2.tick_params(axis='y', labelcolor=C['danger'])
    ax2.yaxis.set_major_formatter(PercentFormatter(decimals=0))

    # 80% 임계선
    ax2.axhline(80, linestyle='--', color=C['warning'], linewidth=1.1, alpha=0.8,
                label='80% 임계선', zorder=4)

    # 80% 이하인 항목까지 배경 음영
    count_80 = 0
    for i, cum_val in enumerate(cumulative):
        if cum_val >= 80:
            count_80 = i
            break
    else:
        count_80 = m - 1
    ax1.axvspan(-0.5, count_80 + 0.5, facecolor=C['warning'], alpha=0.06, zorder=0)

    # 누적 비율 값 텍스트
    for i, cum_val in enumerate(cumulative):
        ax2.text(xs[i], cum_val + 2.5, f'{cum_val:.1f}%', ha='center', va='bottom',
                 fontsize=10, color=C['danger'])

    ax1.set_title(title, fontweight='bold', fontsize=12, pad=10)
    ax1.set_xlim(-0.5, m - 0.5)

    # 범례 합치기
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc='center right', frameon=True,
               framealpha=1, fontsize=10)

    ax1.tick_params(axis='both', which='major', direction='in', labelsize=10)
    ax2.tick_params(axis='both', which='major', direction='in', labelsize=10)

    fig.tight_layout(pad=1.0)
    return fig


# ════════════════════════════════════════════════════════════════════
# 5. 2×2 개요 그림 (Overview)
# ════════════════════════════════════════════════════════════════════

def build_overview_figure(
    result: dict,
    capability: dict,
    palette: str = 'trust',
    figsize: tuple = (14, 9),
) -> Figure:
    """
    2×2 레이아웃 개요 그림을 생성한다.
      [0,0] 요약 정보 텍스트 패널
      [0,1] 관리도 (축약)
      [1,0] 시료크기 막대 차트
      [1,1] 런 차트 / 값 추세

    Parameters
    ----------
    result : dict
        관리도 분석 결과
    capability : dict
        공정능력 지표 (dpu, dpmo, sigma_level, yield_pct, ppm)
    palette : str
    figsize : tuple

    Returns
    -------
    Figure
    """
    C = _apply_palette(palette)

    chart_type = result.get('chart_type', 'p')
    values = np.asarray(result.get('values', []), dtype=float)
    m = len(values)
    cl = float(result.get('cl', np.nan))
    ucl_raw = result.get('ucl', np.nan)
    lcl_raw = result.get('lcl', np.nan)
    violations = result.get('violations', {f'T{i}': set() for i in range(1, 5)})
    control_phase = str(result.get('control_phase', 'phase1'))
    phase_label = str(result.get('control_phase_label',
                                 '해석용' if control_phase != 'phase2' else '관리용'))
    baseline_count = int(result.get('baseline_count', m))

    sizes = np.asarray(
        result.get('sample_sizes', result.get('inspection_units', [])),
        dtype=float
    )
    defects = np.asarray(result.get('defects', []), dtype=float)

    ucl_arr = _ensure_array(ucl_raw, m) if m > 0 else np.array([np.nan])
    lcl_arr = _ensure_array(lcl_raw, m) if m > 0 else np.array([np.nan])
    variable_limits = _is_variable_limit(chart_type)

    chart_kr = _CHART_TYPE_KR.get(chart_type, chart_type)

    # 공정능력
    dpu = capability.get('DPU', capability.get('dpu', np.nan))
    dpmo = capability.get('DPMO', capability.get('dpmo', np.nan))
    sigma_level = capability.get('sigma_level', capability.get('sigma_level', np.nan))
    yield_pct = capability.get('yield_pct', capability.get('yield_pct', np.nan))
    ppm = capability.get('PPM', capability.get('ppm', np.nan))

    # ── Figure 생성 ──
    fig = plt.figure(figsize=figsize, facecolor=C['bg'])
    gs = fig.add_gridspec(2, 2, left=0.06, right=0.98, top=0.93, bottom=0.07,
                          wspace=0.18, hspace=0.35)
    ax_info = fig.add_subplot(gs[0, 0])  # 요약 정보
    ax_ctrl = fig.add_subplot(gs[0, 1])  # 관리도
    ax_size = fig.add_subplot(gs[1, 0])  # 시료크기
    ax_run = fig.add_subplot(gs[1, 1])   # 런 차트

    for ax in (ax_info, ax_ctrl, ax_size, ax_run):
        ax.set_facecolor(C['ax_bg'])

    # ───────── [0,0] 요약 정보 텍스트 패널 ─────────
    ax_info.axis('off')
    ax_info.set_title(f'{chart_kr} — 요약', fontsize=11, fontweight='bold',
                      color=C['text'], pad=10)

    # 총 검사수/불량수 계산
    total_inspected = float(result.get('total_inspected', np.nansum(sizes)))
    if chart_type == 'p':
        total_defects = int(np.nansum(values * sizes)) if len(sizes) == m else 0
    elif chart_type == 'np':
        total_defects = int(np.nansum(values))
    elif chart_type == 'c':
        total_defects = int(np.nansum(values))
    elif chart_type == 'u':
        total_defects = int(np.nansum(values * sizes)) if len(sizes) == m else 0
    else:
        total_defects = 0

    ucl_display = _avg_if_array(ucl_raw)
    lcl_display = _avg_if_array(lcl_raw)
    viol_counts = {ti: len(violations.get(ti, set())) for ti in ('T1', 'T2', 'T3', 'T4')}

    info_lines = summary_lines(result, capability)
    # 계산되지 않은("--", "—") 항목 필터링
    info_lines = [(lab, val) for lab, val in info_lines if val not in ("--", "—")]

    ax_info.add_patch(Rectangle(
        (0.01, 0.01), 0.98, 0.98, linewidth=1.65,
        edgecolor=C['grid'], facecolor=C['ax_bg'], alpha=0.95,
        transform=ax_info.transAxes, zorder=1,
    ))
    y_pos = 0.94
    line_step = 0.88 / max(len(info_lines) - 1, 1)
    for lab, val in info_lines:
        val_color = C['danger'] if 'T1' in lab else C['text']
        val_weight = 'bold'
        ax_info.text(0.06, y_pos, f"{lab} :", color=C['muted'], fontsize=10.0,
                     va='center', ha='left', transform=ax_info.transAxes, zorder=2)
        ax_info.text(0.54, y_pos, str(val), color=val_color, fontsize=10.0,
                     va='center', ha='left', transform=ax_info.transAxes, zorder=2, fontweight=val_weight)
        y_pos -= line_step

    # ───────── [0,1] 관리도 (축약) ─────────
    if m > 0:
        xs = np.arange(m)
        ax_ctrl.plot(xs, values, marker='o', markersize=4.2, color=C['primary'],
                     linewidth=1.65, zorder=4)
        ax_ctrl.axhline(cl, linestyle='--', color=C['mean'], linewidth=1.1, zorder=3)

        if variable_limits:
            ax_ctrl.plot(xs, ucl_arr, drawstyle='steps-mid', color=C['danger'],
                         linewidth=1.65, linestyle=(0, (5, 3)), zorder=3)
            ax_ctrl.plot(xs, lcl_arr, drawstyle='steps-mid', color=C['success'],
                         linewidth=1.65, linestyle=(0, (5, 3)), zorder=3)
        else:
            ucl_val = float(ucl_arr[0])
            lcl_val = float(lcl_arr[0])
            if np.isfinite(ucl_val):
                ax_ctrl.axhline(ucl_val, linestyle=(0, (5, 3)), color=C['danger'],
                                linewidth=1.65, zorder=3)
            if np.isfinite(lcl_val):
                ax_ctrl.axhline(lcl_val, linestyle=(0, (5, 3)), color=C['success'],
                                linewidth=1.65, zorder=3)

        # 위반점 마킹 (축약 모드에서도 T1~T4 상세 매핑 표시)
        mark_map = {
            'T1': dict(marker='o', s=50, c='#E53935', zorder=10,
                       edgecolors='white', linewidths=0.7, label='T1 (한계 초과)'),
            'T2': dict(marker='s', s=35, c=C['accent'], zorder=8,
                       edgecolors='white', linewidths=0.5, label='T2 (연속 규칙)'),
            'T3': dict(marker='^', s=40, c=C['warning'], zorder=8,
                       edgecolors='white', linewidths=0.5, label='T3 (경향)'),
            'T4': dict(marker='D', s=30, c=C['muted'], zorder=8,
                       edgecolors='white', linewidths=0.5, label='T4 (기타)'),
        }
        legend_added = set()
        for key, cfg in mark_map.items():
            pts = sorted(violations.get(key, set()))
            if pts:
                valid_pts = [p for p in pts if 0 <= p < m]
                if valid_pts:
                    scatter_cfg = {k: v for k, v in cfg.items() if k != 'label'}
                    ax_ctrl.scatter(valid_pts, [values[i] for i in valid_pts], **scatter_cfg)

        ax_ctrl.set_xlim(-0.5, m - 0.5)
        if control_phase == 'phase2':
            _shade_phase_window(ax_ctrl, C, baseline_count, m)

        # X 틱 (축약)
        x_labels = result.get('x_labels', [])
        tick_labels = list(x_labels) if len(x_labels) == m else [str(i + 1) for i in range(m)]
        if m > 15:
            step = math.ceil(m / 15)
            ax_ctrl.set_xticks(xs[::step])
            ax_ctrl.set_xticklabels(tick_labels[::step], rotation=28, ha='right', fontsize=9)
        else:
            ax_ctrl.set_xticks(xs)
            ax_ctrl.set_xticklabels(
                tick_labels, rotation=28 if x_labels else 0,
                ha='right' if x_labels else 'center', fontsize=9
            )

    ax_ctrl.set_title(f'{chart_kr} 관리도', fontsize=10, fontweight='bold', pad=6)
    ax_ctrl.set_ylabel(_Y_LABEL_KR.get(chart_type, chart_type), fontsize=9)
    ax_ctrl.margins(y=0.12)
    ax_ctrl.tick_params(axis='both', which='major', direction='in', labelsize=9)
    ax_ctrl.yaxis.set_minor_locator(AutoMinorLocator(2))

    # ───────── [1,0] 누적 불량/결점 추이 차트 ─────────
    if len(sizes) > 0 and len(defects) > 0:
        xs_c = np.arange(m)
        cum_defects = np.cumsum(defects)
        cum_sizes = np.cumsum(sizes)

        if chart_type in ('p', 'np'):
            cum_rates = cum_defects / cum_sizes * 100
            ylabel = '%불량품'
            title = '누적 %불량품'
        else:
            cum_rates = cum_defects / cum_sizes
            ylabel = '누적 단위당 결점'
            title = '누적 단위당 결점'

        ax_size.plot(xs_c, cum_rates, marker='o', color=C['primary'], linewidth=1.65, markersize=4, zorder=4)

        final_rate = cum_rates[-1]
        ax_size.axhline(final_rate, linestyle='--', color=C['success'], alpha=0.8, zorder=3, label=f'최종: {final_rate:.2f}')

        # 품질 관리 목표선(Target) 가이드라인 렌더링 지원 (result 혹은 capability 내 target_rate 확인)
        target_rate = capability.get('target_rate', result.get('target_rate', capability.get('target', result.get('target', None))))
        if target_rate is not None:
            try:
                tr = float(target_rate)
                # p/np 차트의 경우 소수 형태(예: 0.03)로 들어왔다면 % 단위로 환산
                if chart_type in ('p', 'np') and tr <= 1.0:
                    tr_disp = tr * 100.0
                else:
                    tr_disp = tr
                ax_size.axhline(tr_disp, linestyle=':', color=C['danger'], linewidth=1.65, alpha=0.9, zorder=3, label=f'목표: {tr_disp:.2f}')
            except (ValueError, TypeError):
                pass

        ax_size.grid(True, axis='both', linestyle='--', color=C['grid'], alpha=0.6, zorder=1)

        ax_size.set_xlim(-0.5, m - 0.5)

        x_labels = result.get('x_labels', [])
        if x_labels and len(x_labels) == m:
            tick_labels = x_labels
        else:
            tick_labels = [str(i + 1) for i in range(m)]

        if m > 15:
            step = math.ceil(m / 15)
            ax_size.set_xticks(xs_c[::step])
            ax_size.set_xticklabels(tick_labels[::step], rotation=28, ha='right', fontsize=9)
        else:
            ax_size.set_xticks(xs_c)
            ax_size.set_xticklabels(tick_labels, rotation=28 if x_labels else 0, ha='right' if x_labels else 'center', fontsize=9)
        ax_size.legend(loc='upper right', frameon=True, framealpha=0.85, fontsize=9)
    else:
        ax_size.text(0.5, 0.5, '데이터 없음', ha='center', va='center',
                     transform=ax_size.transAxes, fontsize=10, color=C['muted'])
        title = '누적 추이'
        ylabel = '값'

    ax_size.set_title(title, fontsize=10, fontweight='bold', pad=6)
    ax_size.set_xlabel('표본', fontsize=9)
    ax_size.set_ylabel(ylabel, fontsize=9)
    ax_size.tick_params(axis='both', which='major', direction='in', labelsize=9)
    ax_size.margins(y=0.12)

    # ───────── [1,1] 런 차트 / 값 추세 ─────────
    if m > 0:
        xs_r = np.arange(m)
        ax_run.plot(xs_r, values, marker='o', markersize=4.2, color=C['accent'],
                    linewidth=1.65, zorder=4, label=_Y_LABEL_KR.get(chart_type, chart_type))

        # 이동평균 (window=min(5, m))
        win = min(5, m)
        if m >= win:
            ma = np.convolve(values, np.ones(win) / win, mode='valid')
            ma_x = np.arange(win - 1, m)
            ax_run.plot(ma_x, ma, color=C['warning'], linewidth=1.5, alpha=0.7,
                        linestyle='-', label=f'{win}점 이동평균', zorder=5)

        # 중심선
        ax_run.axhline(cl, linestyle=':', color=C['mean'], linewidth=1.65, alpha=0.7,
                       label=f'CL={_fmt(cl)}', zorder=3)

        ax_run.set_xlim(-0.5, m - 0.5)
        x_labels = result.get('x_labels', [])
        if x_labels and len(x_labels) == m:
            tick_labels = x_labels
        else:
            tick_labels = [str(i + 1) for i in range(m)]

        if m > 15:
            step = math.ceil(m / 15)
            ax_run.set_xticks(xs_r[::step])
            ax_run.set_xticklabels(tick_labels[::step], rotation=28, ha='right', fontsize=9)
        else:
            ax_run.set_xticks(xs_r)
            ax_run.set_xticklabels(tick_labels, rotation=28 if x_labels else 0, ha='right' if x_labels else 'center', fontsize=9)

        # 범례가 데이터와 겹치는 것을 완화하기 위해 투명도를 높이고 위치 조정 지원
        ax_run.legend(loc='upper left', frameon=True, framealpha=0.6, fontsize=9)

    ax_run.set_title('런 차트 (값 추세)', fontsize=10, fontweight='bold', pad=6)
    ax_run.set_xlabel('부분군 번호', fontsize=9)
    ax_run.set_ylabel(_Y_LABEL_KR.get(chart_type, chart_type), fontsize=9)
    ax_run.tick_params(axis='both', which='major', direction='in', labelsize=9)
    ax_run.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax_run.margins(y=0.12)

    # ── 전체 제목 ──
    fig.suptitle(f'{chart_kr} 관리도 — 개요 [{phase_label}]',
                 fontsize=13, fontweight='bold', y=0.98)

    return fig


# ════════════════════════════════════════════════════════════════════
# 6. Plotly 기반 인터랙티브 차트 빌더
# ════════════════════════════════════════════════════════════════════

def build_attribute_control_chart_plotly(result: dict, palette_name: str = 'trust') -> Any:
    """Interactive attribute chart, with true subgroup-specific limits."""
    from html import escape

    import plotly.graph_objects as go
    from spc_charts import PALETTES as shared_palettes, control_chart_layout, control_chart_limits, control_chart_phase

    chart_type = result['chart_type']
    values = np.asarray(result['values'], dtype=float)
    count = len(values)
    positions = np.arange(1, count+1)
    left, right = .5, max(1, count)+.5
    upper = _ensure_array(result['ucl'], count)
    lower = np.maximum(0, _ensure_array(result['lcl'], count))
    center = float(result['cl'])
    colors = shared_palettes.get(palette_name, shared_palettes[DEFAULT_PALETTE])
    chart_name = _CHART_TYPE_KR.get(chart_type, chart_type)
    phase = result.get('control_phase', 'phase1')
    phase_label = result.get('control_phase_label', '해석용')
    labels = result.get('x_labels', [])
    named = bool(labels and len(labels) == count)
    baseline = int(result.get('baseline_display_count', result.get('baseline_count', count)))
    rules = result.get('violations', {})
    fmt = '.3%' if chart_type == 'p' else '.6g'
    hover = []
    for i, (value, hi, lo) in enumerate(zip(values, upper, lower)):
        label = f" · {escape(str(labels[i]))}" if named else ''
        active = ', '.join(key for key in ('T1', 'T2', 'T3', 'T4') if i in rules.get(key, ()))
        status = f'위반 규칙: {active}' if active else '이상 신호 없음'
        window = (' · 기준구간' if i < baseline else ' · 감시구간') if phase == 'phase2' else ''
        hover.append(f'<b>부분군 {i+1}{label}{window}</b><br>'
                     f'<b>{chart_name} {value:{fmt}}</b><br>'
                     f'UCL {hi:{fmt}} / CL {center:{fmt}} / LCL {lo:{fmt}}<br>{status}')

    figure = go.Figure()
    figure.add_trace(go.Scatter(x=positions, y=values, mode='lines+markers', name=chart_name,
                                line=dict(color=colors['primary'], width=2.2), marker=dict(size=7),
                                text=hover, hovertemplate='%{text}<extra></extra>'))
    marker_styles = {
        'T2': ('square', colors['accent'], 'T2 (연속 편향)'),
        'T3': ('triangle-up', colors['warning'], 'T3 (추세)'),
        'T4': ('diamond', colors['muted'], 'T4 (교대 반복)'),
        'T1': ('circle', colors['danger'], 'T1 (한계 이탈)'),
    }
    for key, (symbol, color, name) in marker_styles.items():
        indices = [i for i in sorted(rules.get(key, ())) if 0 <= i < count]
        if indices:
            figure.add_trace(go.Scatter(x=positions[indices], y=values[indices], mode='markers', name=name,
                                        marker=dict(symbol=symbol, color=color, size=12 if key == 'T1' else 10,
                                                    line=dict(color=colors['ax_bg'], width=1.2)),
                                        text=[hover[i] for i in indices], hovertemplate='%{text}<extra></extra>'))

    varying = []
    limits = [('UCL', upper), ('CL', center), ('LCL', lower)]
    for label, limit in (limits[0], limits[2]):
        if count and not np.all(limit == limit[-1]):
            varying.append(label)
            figure.add_trace(go.Scatter(x=np.r_[left, positions, right], y=np.r_[limit[0], limit, limit[-1]],
                                        mode='lines', line_shape='hvh', showlegend=False, hoverinfo='skip',
                                        line=dict(color=colors['danger'], width=1.4, dash='dash'), name=label))
    control_chart_layout(figure, colors, f'<b>{chart_name} 관리도</b> · {phase_label}', height=515)
    control_chart_limits(figure, colors, values, limits, formatter=lambda value: format(value, fmt), varying=varying)
    if phase == 'phase2':
        control_chart_phase(figure, colors, left, right, baseline+.5)
    figure.update_xaxes(range=[left, right], title_text='부분군 번호', dtick=1 if count <= 12 else None)
    figure.update_yaxes(title_text='불량률 (%)' if chart_type == 'p' else _Y_LABEL_KR[chart_type])
    if chart_type == 'p':
        figure.update_yaxes(tickformat='.2~%')
    return figure


def build_cumulative_defect_chart_plotly(
    result: dict,
    palette_name: str = 'trust',
) -> Any:
    """Plotly 기반 인터랙티브 누적 불량/결점 추이 차트 생성"""
    import plotly.graph_objects as go

    C = PALETTES.get(palette_name, PALETTES[DEFAULT_PALETTE])
    defects = result.get('defects', [])
    sizes = np.asarray(
        result.get('sample_sizes', result.get('inspection_units', [])),
        dtype=float
    )
    chart_type = result.get('chart_type', 'p')

    if len(sizes) == 0 or len(defects) == 0:
        fig = go.Figure()
        fig.add_annotation(text="데이터 없음", showarrow=False, font=dict(size=14))
        return fig

    m = len(defects)
    x_labels = result.get('x_labels', [])
    has_x_labels = bool(x_labels and len(x_labels) == m)
    xs = np.asarray(x_labels, dtype=object) if has_x_labels else np.arange(1, m + 1)

    cum_defects = np.cumsum(defects)
    cum_sizes = np.cumsum(sizes)

    if chart_type in ('p', 'np'):
        cum_rates = cum_defects / cum_sizes * 100
        ylabel = '%불량품'
        title = '누적 %불량품'
    else:
        cum_rates = cum_defects / cum_sizes
        ylabel = '누적 단위당 결점'
        title = '누적 단위당 결점'

    fig = go.Figure()

    # 1. 추이선
    fig.add_trace(go.Scatter(
        x=xs, y=cum_rates,
        mode='lines+markers',
        marker=dict(color=C['primary'], size=5),
        line=dict(color=C['primary'], width=1.8),
        name=title,
        hovertemplate="표본: %{x}<br>누적 값: %{y:.4f}<extra></extra>"
    ))

    # 2. 최종 평균선
    final_rate = cum_rates[-1]
    fig.add_trace(go.Scatter(
        x=xs, y=np.full(m, final_rate),
        mode='lines',
        line=dict(color=C['success'], width=1.2, dash='dash'),
        name=f"최종: {final_rate:.2f}",
        hoverinfo='skip'
    ))

    # 3. 품질 목표 가이드라인 (결과 사전에서 target 검색)
    target_rate = result.get('target_rate', result.get('target', None))
    if target_rate is not None:
        try:
            tr = float(target_rate)
            if chart_type in ('p', 'np') and tr <= 1.0:
                tr_disp = tr * 100.0
            else:
                tr_disp = tr
            fig.add_trace(go.Scatter(
                x=xs, y=np.full(m, tr_disp),
                mode='lines',
                line=dict(color=C['danger'], width=1.2, dash='dot'),
                name=f"목표: {tr_disp:.2f}",
                hoverinfo='skip'
            ))
        except:
            pass

    fig.update_layout(
        title=dict(
            text=title,
            x=0.5, y=0.94,
            xanchor='center', yanchor='top',
            font=dict(size=13, color=C['text'], family="Malgun Gothic", weight='bold')
        ),
        xaxis=dict(
            title="측정일자/로트" if has_x_labels else "표본 번호",
            nticks=min(20, m),
            tickangle=-35 if has_x_labels else 0,
            gridcolor=C['grid']
        ),
        yaxis=dict(
            title=ylabel,
            gridcolor=C['grid']
        ),
        margin=dict(l=40, r=40, t=50, b=40),
        plot_bgcolor=C['ax_bg'],
        font=dict(family='Malgun Gothic', size=12, color=C['text']),
        dragmode='zoom',
        paper_bgcolor=C['bg'],
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=9)
        ),
        hoverlabel=dict(bgcolor=C['ax_bg'], font_size=12, font_color=C['text']),
        height=320
    )
    return fig


def build_run_chart_plotly(
    result: dict,
    palette_name: str = 'trust',
) -> Any:
    """Plotly 기반 인터랙티브 런 차트 (값 추세) 생성"""
    import plotly.graph_objects as go

    C = PALETTES.get(palette_name, PALETTES[DEFAULT_PALETTE])
    chart_type = result.get('chart_type', 'p')
    values = np.asarray(result.get('values', []), dtype=float)
    m = len(values)
    cl = float(result.get('cl', np.nan))

    if m == 0:
        fig = go.Figure()
        fig.add_annotation(text="데이터 없음", showarrow=False, font=dict(size=14))
        return fig

    x_labels = result.get('x_labels', [])
    has_x_labels = bool(x_labels and len(x_labels) == m)
    xs = np.asarray(x_labels, dtype=object) if has_x_labels else np.arange(1, m + 1)
    fig = go.Figure()

    # 1. 측정값 추이
    val_label = _Y_LABEL_KR.get(chart_type, chart_type)
    val_fmt = ".4f" if chart_type in ('p', 'u') else ".1f"
    fig.add_trace(go.Scatter(
        x=xs, y=values,
        mode='lines+markers',
        marker=dict(color=C['accent'], size=5),
            line=dict(color=C['accent'], width=1.8),
        name=val_label,
        hovertemplate="부분군: %{x}<br>측정값: %{y:" + val_fmt + "}<extra></extra>"
    ))

    # 2. 이동평균선
    win = min(5, m)
    if m >= win:
        ma = np.convolve(values, np.ones(win) / win, mode='valid')
        ma_x = xs[win - 1:]
        fig.add_trace(go.Scatter(
            x=ma_x, y=ma,
            mode='lines',
            line=dict(color=C['warning'], width=1.8),
            name=f"{win}점 이동평균",
            hovertemplate="시점: %{x}<br>이동평균: %{y:.4f}<extra></extra>"
        ))

    # 3. 중심선
    fig.add_trace(go.Scatter(
        x=xs, y=np.full(m, cl),
        mode='lines',
        line=dict(color=C['mean'], width=1.0, dash='dot'),
        name=f"CL ({cl:.4f})",
        hoverinfo='skip'
    ))

    fig.update_layout(
        title=dict(
            text='런 차트 (값 추세)',
            x=0.5, y=0.94,
            xanchor='center', yanchor='top',
            font=dict(size=13, color=C['text'], family="Malgun Gothic", weight='bold')
        ),
        xaxis=dict(
            title="측정일자/로트" if has_x_labels else "부분군 번호",
            nticks=min(20, m),
            tickangle=-35 if has_x_labels else 0,
            gridcolor=C['grid']
        ),
        yaxis=dict(
            title=val_label,
            gridcolor=C['grid']
        ),
        margin=dict(l=40, r=40, t=50, b=40),
        plot_bgcolor=C['ax_bg'],
        font=dict(family='Malgun Gothic', size=12, color=C['text']),
        dragmode='zoom',
        paper_bgcolor=C['bg'],
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=9)
        ),
        hoverlabel=dict(bgcolor=C['ax_bg'], font_size=12, font_color=C['text']),
        height=320
    )
    return fig
