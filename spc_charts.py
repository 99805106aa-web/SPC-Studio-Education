# -*- coding: utf-8 -*-
"""
spc_charts.py
=============
愿由щ룄 & 怨듭젙?λ젰 李⑦듃 ?앹꽦 紐⑤뱢 (Matplotlib 湲곕컲, GUI ?섏〈 ?놁쓬)
?먮낯: 怨듭젙?λ젰_愿由щ룄_Rev26.py ?먯꽌 異붿텧/蹂??"""

import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # ?쒕쾭/?ㅻ뱶由ъ뒪 ?섍꼍
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.offsetbox import AnchoredText
from matplotlib.ticker import AutoMinorLocator
from scipy import stats
from typing import Any, Dict, Optional, Tuple

try:
    import seaborn as sns
except ModuleNotFoundError:
    sns = None

# ??????????????????????????? ?고듃 ?ㅼ젙 ???????????????????????????
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

# ??????????????????????????? ?뚮쭏/?붾젅?????????????????????????????
PALETTES = {
    'trust': {
        'primary': '#1E3A8A', 'accent': '#0E7490', 'success': '#0F766E',
        'warning': '#B45309', 'danger': '#B42318', 'muted': '#475569',
        'mean': '#0EA5E9', 'usl': '#C2410C', 'lsl': '#1D4ED8',
        'grid': '#D3DCE8',
        'zoneC': '#DCFCE7', 'zoneB': '#BAE6FD', 'zoneA': '#FEF08A', 'zone3': '#FECACA',
        'bg': '#F2F5FA', 'ax_bg': '#FFFFFF',
    },
    'analytics': {
        'primary': '#1F6FEB', 'accent': '#0F766E', 'success': '#1D9A6C',
        'warning': '#D97706', 'danger': '#C62828', 'muted': '#64748B',
        'mean': '#EA8C00', 'usl': '#D9363E', 'lsl': '#2453A6',
        'grid': '#D6DFEA',
        'zoneC': '#D1FAE5', 'zoneB': '#BAE6FD', 'zoneA': '#FEF08A', 'zone3': '#FECACA',
        'bg': '#F4F7FB', 'ax_bg': '#FFFFFF',
    },
    'dark': {
        'primary': '#60A5FA', 'accent': '#22D3EE', 'success': '#34D399',
        'warning': '#F59E0B', 'danger': '#F87171', 'muted': '#94A3B8',
        'mean': '#FBBF24', 'usl': '#FB7185', 'lsl': '#93C5FD',
        'grid': '#334155',
        'zoneC': '#064E3B', 'zoneB': '#451A03', 'zoneA': '#431407', 'zone3': '#450A0A',
        'bg': '#0F172A', 'ax_bg': '#111827',
    },
}
DEFAULT_PALETTE = 'trust'


def _apply_palette(palette_name: str = DEFAULT_PALETTE):
    p = PALETTES.get(palette_name, PALETTES[DEFAULT_PALETTE])
    plt.rcParams.update({
        'figure.facecolor': p['bg'],
        'axes.facecolor': p['ax_bg'],
        'axes.edgecolor': p['grid'],
        'grid.color': p['grid'],
        'axes.grid': True,
        'grid.alpha': 0.7,
    })
    return p


def _fmt(v, decimals: int = 3) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "--"
    return f"{v:.{decimals}f}"


def _side_label(ax, y: float, text: str, color: str, fontsize: float = 8):
    if not np.isfinite(y):
        return
    xmin, xmax = ax.get_xlim()
    x = xmin + 0.01 * (xmax - xmin)
    ax.text(x, y, text, color=color, ha='left', va='center', fontsize=fontsize,
            bbox=dict(boxstyle='round,pad=0.2', fc='white', ec=color, lw=0.8, alpha=0.85),
            zorder=6, clip_on=True)


def _make_xticklabels(m: int, n: int, is_xmr: bool):
    # 異?媛?낆꽦???꾪빐 紐⑤뱺 愿由щ룄?먯꽌 x異??쇰꺼? "遺遺꾧뎔 ?쒕쾲"?쇰줈 ?듭씪?쒕떎.
    # ?? n=5????湲곗〈 1~5, 6~10 ... ???1, 2, 3 ... ?쒓린
    return [str(i + 1) for i in range(m)]


def _labels_or_fallback(raw_labels, m: int, n: int, is_xmr: bool):
    fallback = _make_xticklabels(m, n, is_xmr)
    if not raw_labels or len(raw_labels) != m:
        return fallback
    out = []
    for idx, label in enumerate(raw_labels):
        s = str(label).strip()
        out.append(s if s else fallback[idx])
    return out


# ??????????????????????????? 媛쒕퀎 李⑦듃 ?⑥닔?????????????????????????????

def build_control_chart(
    it: Dict[str, Any],
    palette: str = DEFAULT_PALETTE,
    figsize: Tuple[float, float] = (12, 7),
    ksq_toggles: Optional[Dict[str, bool]] = None,
    show_zones: Optional[Dict[str, bool]] = None,
    display_mode: str = "detail",
) -> Figure:
    """
    Xbar(?먮뒗 Individuals) + R/S/MR 愿由щ룄 (?몃줈 2遺꾪븷).
    KSQ T1~T8 洹쒖튃 ?꾨컲??留덊궧 ?ы븿.
    """
    C = _apply_palette(palette)
    compact = str(display_mode).lower() == "compact"
    if compact and figsize == (12, 7):
        figsize = (12, 6.2)
    style = {
        "height_ratios": [3.0, 1.7] if compact else [3, 2],
        "hspace": 0.18 if compact else 0.35,
        "marker": 4.2 if compact else 4.8,
        "r_marker": 4.2 if compact else 4.8,
        "line": 1.65 if compact else 1.8,
        "title": 11 if compact else 12,
        "title_pad": 2 if compact else 4,
        "label": 10 if compact else 11,
        "tick": 9 if compact else 9.5,
        "side": 9 if compact else 9.5,
        "info": 6.6 if compact else 8.0,
        "zone": 7.0 if compact else 8.5,
        "legend": 5.8 if compact else 7.5,
        "legend_marker": 4.5 if compact else 6.0,
        "r_violation": 34 if compact else 45,
        "margin_y": 0.08 if compact else 0.13,
    }
    text_color = '#E2E8F0' if palette == 'dark' else '#1E293B'
    if ksq_toggles is None:
        ksq_toggles = {f'T{i}': True for i in range(1, 9)}
    if show_zones is None:
        show_zones = {'C': True, 'B': True, 'A': True, 'Z3': True}
    if it.get('distribution_mode') == 'nonnormal':
        show_zones = {key: False for key in ('C', 'B', 'A', 'Z3')}
        ksq_toggles = it.get('effective_rules', {'T1': True})

    means = it['means']
    ranges = it['ranges']
    is_xmr = it['is_xmr']
    n = it['n']
    xbarbar = it['xbarbar']
    ucl = it['ucl']
    lcl = it['lcl']
    r_bar = it['r_bar']
    r_ucl = it['r_ucl']
    r_lcl = it['r_lcl']
    col = it['column']
    ksq_counts = it.get('ksq_counts', {f'T{i}': 0 for i in range(1, 9)})
    ksq_idx = it.get('ksq_idx', {f'T{i}': set() for i in range(1, 9)})
    A2_plot = it.get('A2_plot', np.nan)
    std_within = it['std_within']
    control_phase = str(it.get('control_phase', 'phase1'))
    phase_label = str(it.get('control_phase_label', '해석용' if control_phase != 'phase2' else '관리용'))
    limit_reference_label = str(it.get('limit_reference_label', '전체 데이터'))
    baseline_display_count = int(it.get('baseline_display_count', len(means)))
    baseline_r_display_count = int(it.get('baseline_r_display_count', len(ranges)))

    fig, (ax_x, ax_r) = plt.subplots(2, 1, figsize=figsize, facecolor=C['ax_bg'],
                                      gridspec_kw={'height_ratios': style["height_ratios"]})
    fig.subplots_adjust(hspace=style["hspace"])

    x_lab = "Individuals" if is_xmr else "Xbar"
    r_lab = str(it.get('dispersion_kind', "MR" if is_xmr else "R"))

    spread = abs(ucl - lcl) if np.isfinite(ucl) and np.isfinite(lcl) else 1.0
    decimals = min(8, max(3, int(-math.floor(math.log10(spread))) + 2)) if spread > 0 else 3
    fig.text(0.075, 0.97, f"{col} · {it.get('chart_mode', '관리도')}", fontsize=14, weight='bold',
             color=C['primary'], va='top')
    fig.text(0.075, 0.925,
             f"{phase_label}  ·  {limit_reference_label} 기준  ·  표시 {len(means):,} / 전체 {it.get('total_points', len(means)):,}점  ·  전체 집계: "
             f"상한 초과 {it['xbar_ucl_cnt']}건 / 하한 미달 {it['xbar_lcl_cnt']}건  ·  "
             f"{r_lab} 이탈 {it['r_ucl_cnt'] + it['r_lcl_cnt']}건",
             fontsize=8.5, color=C['muted'], va='top')

    def _limit_label(ax, value, text, color, fontsize=8):
        if np.isfinite(value):
            ax.text(1.015, value, text, transform=ax.get_yaxis_transform(),
                    fontsize=fontsize, color=color, va='center', clip_on=False)

    def _shade_phase_window(ax, ref_count: int, total_count: int):
        if control_phase != 'phase2':
            return
        ref_count = max(0, min(int(ref_count), int(total_count)))
        if ref_count <= 0 or ref_count >= total_count:
            return
        ax.axvspan(-0.5, ref_count - 0.5, facecolor=C['accent'], alpha=0.08, zorder=0.1)
        ax.axvline(ref_count - 0.5, color=C['muted'], linestyle=':', linewidth=1.1, alpha=0.9)
        ax.text(
            0.01,
            0.98,
            "기준구간",
            transform=ax.transAxes,
            ha='left',
            va='top',
            fontsize=7.5,
            color=C['muted'],
            bbox=dict(boxstyle='round,pad=0.18', facecolor='white', edgecolor='none', alpha=0.8),
        )
        ax.text(
            0.99,
            0.98,
            "감시구간",
            transform=ax.transAxes,
            ha='right',
            va='top',
            fontsize=7.5,
            color=C['muted'],
            bbox=dict(boxstyle='round,pad=0.18', facecolor='white', edgecolor='none', alpha=0.8),
        )

    # ?? ?쒓렇留?議???
    if np.isfinite(ucl) and np.isfinite(lcl) and (ucl > lcl):
        sigma_x = (ucl - lcl) / 6.0
    else:
        if is_xmr:
            sigma_x = std_within
        else:
            from spc_core import _d2_mr
            sigma_x = (A2_plot * r_bar) / 3.0 if (np.isfinite(r_bar) and np.isfinite(A2_plot)) else np.nan

    if np.isfinite(sigma_x) and sigma_x > 0 and np.isfinite(xbarbar):
        z1u = xbarbar + sigma_x
        z2u = xbarbar + 2 * sigma_x
        z3u = xbarbar + 3 * sigma_x
        z1l = xbarbar - sigma_x
        z2l = xbarbar - 2 * sigma_x
        z3l = xbarbar - 3 * sigma_x
        # ?? ?쒓렇留?議?諛?寃쎄퀎??(A=1?쒓렇留? B=2?쒓렇留? C=3?쒓렇留덈줈 蹂寃? ??
        _za = 0.14
        if show_zones.get('C', True):
            ax_x.axhspan(z1l, z1u, facecolor=C['zoneC'], alpha=_za, zorder=0)
            # 1?쒓렇留?寃쎄퀎??(?쇰꺼 A)
            ax_x.axhline(z1u, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.4, zorder=1)
            ax_x.axhline(z1l, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.4, zorder=1)
            ax_x.text(len(means) - 0.5, (xbarbar + z1u)/2, ' C', va='center', ha='left', fontsize=style["zone"], color=C['muted'], fontweight='bold')
            
        if show_zones.get('B', True):
            ax_x.axhspan(z1u, z2u, facecolor=C['zoneB'], alpha=_za, zorder=0)
            ax_x.axhspan(z2l, z1l, facecolor=C['zoneB'], alpha=_za, zorder=0)
            # 2?쒓렇留?寃쎄퀎??(?쇰꺼 B)
            ax_x.axhline(z2u, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.45, zorder=1)
            ax_x.axhline(z2l, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.45, zorder=1)
            ax_x.text(len(means) - 0.5, (z1u + z2u)/2, ' B', va='center', ha='left', fontsize=style["zone"], color=C['muted'], fontweight='bold')
            
        if show_zones.get('A', True):
            ax_x.axhspan(z2u, min(z3u, ucl) if np.isfinite(ucl) else z3u,
                         facecolor=C['zoneA'], alpha=_za, zorder=0)
            ax_x.axhspan(max(z3l, lcl) if np.isfinite(lcl) else z3l, z2l,
                         facecolor=C['zoneA'], alpha=_za, zorder=0)
            # 3?쒓렇留?寃쎄퀎??(?쇰꺼 C)
            ax_x.axhline(z3u if np.isfinite(z3u) else ucl, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.45, zorder=1)
            ax_x.axhline(z3l if np.isfinite(z3l) else lcl, color=C['muted'], linestyle='-', linewidth=0.6, alpha=0.45, zorder=1)
            ax_x.text(len(means) - 0.5, (z2u + z3u)/2, ' A', va='center', ha='left', fontsize=style["zone"], color=C['muted'], fontweight='bold')
                         
        if show_zones.get('Z3', True) and np.isfinite(ucl) and np.isfinite(lcl):
            ax_x.axhspan(ucl, ucl + 0.5 * sigma_x, facecolor=C['zone3'], alpha=_za, zorder=0)
            ax_x.axhspan(lcl - 0.5 * sigma_x, lcl, facecolor=C['zone3'], alpha=_za, zorder=0)

    # ?? X / Individuals 愿由щ룄 ??
    xs = list(range(len(means)))
    ax_x.plot(xs, means, marker='o', markersize=style["marker"], color=C['primary'], linewidth=style["line"],
              label='Individual' if is_xmr else 'Average')
    ax_x.axhline(xbarbar, linestyle='--', color=C['muted'], linewidth=style["line"],
                 label='X 평균' if is_xmr else 'Xbar')
    if np.isfinite(ucl):
        ax_x.axhline(ucl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=style["line"], label='UCL')
    if np.isfinite(lcl):
        ax_x.axhline(lcl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=style["line"], label='LCL')

    # 痢〓㈃ ?쇰꺼
    ax_x.set_xlim(-0.5, len(means) - 0.5)
    _shade_phase_window(ax_x, baseline_display_count, len(means))
    _limit_label(ax_x, ucl, f"UCL={_fmt(ucl, decimals)}", C['danger'], fontsize=style["side"])
    _limit_label(ax_x, lcl, f"LCL={_fmt(lcl, decimals)}", C['danger'], fontsize=style["side"])
    _limit_label(ax_x, xbarbar, f"CL={_fmt(xbarbar, decimals)}", C['muted'], fontsize=style["side"])

    # ?뺣낫 諛뺤뒪
    mark_map = {
        'T1': dict(marker='o', s=90, c='#E53935', zorder=10, edgecolors='white', linewidths=0.9),
        'T2': dict(marker='s', s=60, c=C['accent'], zorder=8, edgecolors='white', linewidths=0.6),
        'T3': dict(marker='^', s=65, c=C['warning'], zorder=8, edgecolors='white', linewidths=0.6),
        'T4': dict(marker='D', s=60, c=C['muted'], zorder=8, edgecolors='white', linewidths=0.6),
        'T5': dict(marker='+', s=130, c=C['primary'], zorder=8, linewidths=1.5),
        'T6': dict(marker='X', s=75, c=C['danger'], zorder=8, linewidths=1.3),
        'T7': dict(marker='o', s=65, facecolors='none', edgecolors=C['success'], linewidths=1.5, zorder=9),
        'T8': dict(marker='v', s=65, c='#8e44ad', zorder=8, edgecolors='white', linewidths=0.6),
    }
    if compact:
        for cfg in mark_map.values():
            if 's' in cfg:
                cfg['s'] = max(28, cfg['s'] * 0.62)
            if 'linewidths' in cfg:
                cfg['linewidths'] = max(0.5, cfg['linewidths'] * 0.75)
    for key, cfg in mark_map.items():
        if key != 'T1' and not ksq_toggles.get(key, True):
            continue
        pts = sorted(ksq_idx.get(key, set()))
        if pts:
            ax_x.scatter(pts, [means[i] for i in pts], **cfg)

    # KSQ 踰붾?
    sym = {
        'T1': ('o', '#E53935', 'white'),
        'T2': ('s', C['accent'], 'white'),
        'T3': ('^', C['warning'], 'white'),
        'T4': ('D', C['muted'], 'white'),
        'T5': ('+', C['primary'], C['primary']),
        'T6': ('X', C['danger'], 'none'),
        'T7': ('o', 'none', C['success']),
        'T8': ('v', '#8e44ad', 'white'),
    }
    ksq_handles = [
        Line2D([0], [0], marker=sym[f'T{i}'][0], linestyle='None',
               markerfacecolor=sym[f'T{i}'][1], markeredgecolor=sym[f'T{i}'][2],
               markersize=style["legend_marker"], label=f"T{i} ({ksq_counts.get(f'T{i}', 0)})")
        for i in range(1, 9) if ksq_counts.get(f'T{i}', 0) > 0 and ksq_toggles.get(f'T{i}', True)
    ]
    if ksq_handles:
        fig.legend(handles=ksq_handles, loc='upper left', bbox_to_anchor=(0.075, 0.895),
                   ncol=8, frameon=False, fontsize=8, labelcolor=C['muted'],
                   handlelength=1.0, columnspacing=1.1)

    ax_x.set_title('측정값 · Individuals' if is_xmr else '부분군 평균 · Xbar', loc='left',
                   color=text_color, fontweight='bold', pad=8, fontsize=style["title"])
    unit = str(it.get('unit', '') or '').strip()
    ax_x.set_ylabel(f'측정값 ({unit})' if unit else '측정값', color=text_color, fontsize=style["label"])
    ax_x.margins(y=style["margin_y"])

    # 실제 관리한계 이탈은 KSQ 토글과 관계없이 표시한다. 주석은 최대 3개로 제한한다.
    violations = [i for i, v in enumerate(means)
                  if (np.isfinite(ucl) and v > ucl) or (np.isfinite(lcl) and v < lcl)]
    if violations:
        ax_x.scatter(violations, [means[i] for i in violations], s=62,
                     color=C['danger'], edgecolors='white', linewidths=1.2, zorder=12)
    point_labels = _labels_or_fallback(it.get('x_labels'), len(means), n, is_xmr)
    for i in violations[:3]:
        high = means[i] > ucl
        ax_x.annotate(f"{point_labels[i]} · {'상한 초과' if high else '하한 미달'}",
                      xy=(i, means[i]), xytext=(-10, 14 if high else -19),
                      textcoords='offset points', ha='right' if i > len(means) / 2 else 'left',
                      fontsize=9, color=C['danger'], fontweight='bold', zorder=13,
                      bbox=dict(boxstyle='round,pad=0.25', fc=C['ax_bg'], ec='none', alpha=0.94))
    if violations:
        ax_x.margins(y=0.22)

    # ?? R/S/MR 愿由щ룄 ??
    r_offset = 1 if is_xmr else 0
    ax_r.plot(list(range(r_offset, len(ranges) + r_offset)), ranges, marker='o', markersize=style["r_marker"],
              color=C['primary'], linewidth=style["line"])
    if np.isfinite(r_bar):
        ax_r.axhline(r_bar, linestyle='--', color=C['muted'], linewidth=style["line"], label=f'{r_lab}-bar')
    if np.isfinite(r_ucl):
        ax_r.axhline(r_ucl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=style["line"], label=f'UCL({r_lab})')
    if np.isfinite(r_lcl) and r_lcl > 0:
        ax_r.axhline(r_lcl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=style["line"], label=f'LCL({r_lab})')

    # R ?꾨컲??媛뺤“
    for i_r, rval in enumerate(ranges):
        if (np.isfinite(r_ucl) and rval > r_ucl) or (np.isfinite(r_lcl) and rval < r_lcl):
            ax_r.scatter(i_r + r_offset, rval, s=style["r_violation"], c=C['danger'], zorder=3, edgecolors='white', linewidths=0.5)

    ax_r.set_xlim(ax_x.get_xlim())
    _shade_phase_window(ax_r, baseline_display_count, len(means))
    _limit_label(ax_r, r_ucl, f"UCL={_fmt(r_ucl, decimals)}", C['danger'], fontsize=style["side"])
    _limit_label(ax_r, r_bar, f"{r_lab}-bar={_fmt(r_bar, decimals)}", C['muted'], fontsize=style["side"])
    if np.isfinite(r_lcl) and r_lcl > 0:
        _limit_label(ax_r, r_lcl, f"LCL={_fmt(r_lcl, decimals)}", C['danger'], fontsize=style["side"])

    ax_r.set_title(f'변동 · {r_lab}', loc='left', color=text_color,
                   fontweight='bold', pad=8, fontsize=style["title"])
    ax_r.set_ylabel(r_lab, color=text_color, fontsize=style["label"])
    ax_r.set_xlabel('측정일자' if it.get('x_labels') else '측정 순서' if is_xmr else '부분군 순서',
                    fontsize=style['label'], color=text_color)
    ax_r.margins(y=style["margin_y"])

    # X異??쇰꺼
    m_x = len(means)
    labels_x = _labels_or_fallback(it.get('x_labels'), m_x, n, is_xmr)
    m_r = len(ranges)
    labels_r = _labels_or_fallback(it.get('r_labels'), m_r, n, is_xmr)

    for ax_, lbls in [(ax_x, labels_x), (ax_r, labels_x)]:
        m_ = len(lbls)
        ticks = list(range(m_))
        if m_ > 15:
            step = math.ceil(m_ / 15)
            ax_.set_xticks(ticks[::step])
            ax_.set_xticklabels(lbls[::step], rotation=30, ha='right', fontsize=style["tick"])
        else:
            ax_.set_xticks(ticks)
            ax_.set_xticklabels(lbls, rotation=30, ha='right', fontsize=style["tick"])

    for ax_ in (ax_x, ax_r):
        ax_.set_facecolor(C['ax_bg'])
        ax_.grid(False, axis='x')
        ax_.grid(True, axis='y', alpha=0.55, linewidth=0.65)
        ax_.spines['top'].set_visible(False)
        ax_.spines['right'].set_visible(False)
        ax_.tick_params(colors=text_color)
        ax_.tick_params(axis='both', which='major', direction='in', labelsize=style["tick"])
        ax_.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax_.tick_params(axis='y', which='minor', direction='in', length=3, colors=text_color)

    fig.subplots_adjust(left=0.075, right=0.87, top=0.77, bottom=0.12, hspace=0.48)
    return fig


def build_interactive_control_chart(it, palette=DEFAULT_PALETTE, ksq_toggles=None):
    """확대·축소 및 측정점 확인용 관리도. 정적 차트와 같은 분석 결과를 사용한다."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    colors = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    means, ranges = np.asarray(it['means']), np.asarray(it['ranges'])
    offset = int(it.get('window_offset', 0))
    positions = np.arange(1, len(means) + 1) + offset
    labels = _labels_or_fallback(it.get('x_labels'), len(means), it['n'], it['is_xmr'])
    enabled = it.get('effective_rules', {}) if it.get('distribution_mode') == 'nonnormal' else (ksq_toggles if ksq_toggles is not None else {})
    rules = [', '.join(k for k, indices in it.get('ksq_idx', {}).items()
                       if enabled.get(k, True) and i in indices) or '없음'
             for i in range(len(means))]
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.13,
                           row_heights=[.65, .35], subplot_titles=['측정값 / 부분군 평균', '변동 · ' + it['dispersion_kind']])
    custom = [[labels[i], rules[i]] for i in range(len(means))]
    figure.add_trace(go.Scatter(x=positions, y=means, mode='lines+markers', name='측정값',
        line=dict(color=colors['primary'], width=2.2), marker=dict(size=7), customdata=custom,
        hovertemplate='순서 %{x}<br>측정 라벨 %{customdata[0]}<br>값 %{y:.6g}<br>규칙 %{customdata[1]}<extra></extra>'), row=1,col=1)
    bad = (means > it['ucl']) | (means < it['lcl'])
    figure.add_trace(go.Scatter(x=positions[bad], y=means[bad], mode='markers', name='관리한계 이탈',
        marker=dict(color=colors['danger'],size=11,line=dict(color=colors['ax_bg'],width=1)),
        customdata=np.asarray(custom, dtype=object)[bad],
        hovertemplate='측정 라벨 %{customdata[0]}<br>이탈 값 %{y:.6g}<br>규칙 %{customdata[1]}<extra></extra>'),row=1,col=1)
    rpositions = positions[1:] if it['is_xmr'] else positions
    figure.add_trace(go.Scatter(x=rpositions,y=ranges,mode='lines+markers',name=it['dispersion_kind'],
        line=dict(color=colors['primary'],width=2.2),marker=dict(size=7),
        hovertemplate='순서 %{x}<br>산포 %{y:.6g}<extra></extra>'),row=2,col=1)
    rbad = (ranges > it['r_ucl']) | (ranges < it['r_lcl'])
    figure.add_trace(go.Scatter(x=rpositions[rbad], y=ranges[rbad], mode='markers',
        name='산포 관리한계 이탈',
        marker=dict(color=colors['danger'], size=11, line=dict(color=colors['ax_bg'], width=1)),
        hovertemplate='순서 %{x}<br>산포 이탈 %{y:.6g}<extra></extra>'), row=2, col=1)
    rule_bad = np.array([rule != '없음' for rule in rules]) & ~bad
    figure.add_trace(go.Scatter(x=positions[rule_bad], y=means[rule_bad], mode='markers',
        name='KSQ 규칙 위반', customdata=np.asarray(custom, dtype=object)[rule_bad],
        marker=dict(color=colors['warning'], size=10, symbol='diamond',
                    line=dict(color=colors['ax_bg'], width=1)),
        hovertemplate='측정 라벨 %{customdata[0]}<br>값 %{y:.6g}<br>규칙 %{customdata[1]}<extra></extra>'), row=1, col=1)
    for row, keys in [(1,('ucl','xbarbar','lcl')), (2,('r_ucl','r_bar','r_lcl'))]:
        for key,label in zip(keys,('UCL','CL','LCL')):
            value=it[key]
            if np.isfinite(value):
                figure.add_hline(y=value,row=row,col=1,line_dash='dash',line_width=1.5,
                    line_color=colors['muted'] if label=='CL' else colors['danger'],
                    annotation_text=f'{label} {value:.6g}',annotation_position='right')
        if it.get('control_phase')=='phase2':
            figure.add_vline(x=offset+it['baseline_display_count']+.5,row=row,col=1,line_dash='dot',line_color=colors['muted'])
    figure.update_layout(height=620,hovermode='closest',dragmode='zoom',
        paper_bgcolor=colors['ax_bg'],plot_bgcolor=colors['ax_bg'],
        font=dict(color='#E2E8F0' if palette == 'dark' else '#1E293B', size=14),
        margin=dict(l=60,r=110,t=70,b=50),legend=dict(orientation='h',y=1.14),
        uirevision=str(it['column'])+str(len(means)))
    figure.update_xaxes(range=[offset+.5,offset+len(means)+.5],showgrid=False)
    figure.update_xaxes(title_text='측정 순서',row=2,col=1)
    figure.update_xaxes(title_text=None,row=1,col=1)
    figure.update_yaxes(gridcolor=colors['grid'],zeroline=False)
    return figure


def _distribution_canvas(it, palette, figsize, title):
    """Shared editorial layout for distribution diagnostics and exports."""
    C = _apply_palette(palette)
    ink = '#E2E8F0' if palette == 'dark' else '#24364B'
    fig, ax = plt.subplots(figsize=figsize, facecolor=C['ax_bg'])
    ax.set_facecolor(C['ax_bg'])
    fig.subplots_adjust(left=.11, right=.97, top=.70, bottom=.25)
    fig.text(.07, .94, title, fontsize=16, weight='bold', color=ink)
    fig.text(.07, .885, f"측정 항목  {it['column']}", fontsize=10, color=C['muted'])
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(False)
    ax.yaxis.grid(True, color=C['grid'], alpha=.5, linewidth=.7)
    ax.set_axisbelow(True)
    ax.tick_params(colors=ink, labelsize=9, length=3)
    ax.xaxis.label.set_color(ink)
    ax.yaxis.label.set_color(ink)
    ax.ticklabel_format(axis='both', style='plain', useOffset=False)
    return fig, ax, C, ink


def _distribution_metrics(fig, entries, C, ink):
    for x, (label, value) in zip(np.linspace(.07, .76, len(entries)), entries):
        fig.text(x, .815, label, fontsize=9, color=C['muted'])
        fig.text(x, .762, value, fontsize=13, weight='bold', color=ink)


def build_histogram(
    it: Dict[str, Any],
    palette: str = DEFAULT_PALETTE,
    bins: int = 30,
    figsize: Tuple[float, float] = (9, 5.6),
    range_mode: str = 'spec',
) -> Figure:
    """Histogram with fitted density, specification context and summary metrics."""
    fig, ax, C, ink = _distribution_canvas(it, palette, figsize, '분포 분석  /  Histogram')
    a = np.asarray(it['series'], dtype=float)
    a = a[np.isfinite(a)]
    if not a.size:
        ax.text(.5, .5, '유효한 데이터가 없습니다', transform=ax.transAxes, ha='center', color=ink)
        return fig
    mu = float(np.mean(a))
    sd = float(np.std(a, ddof=1)) if a.size > 1 else 0.
    _distribution_metrics(fig, [('표본 수', f'{a.size:,}'), ('평균', f'{mu:.4f}'),
                               ('표준편차 (전체)', f'{sd:.4f}'), ('중앙값', f'{np.median(a):.4f}')], C, ink)
    lo, hi = min(a.min(), mu - 3.5*sd), max(a.max(), mu + 3.5*sd)
    refs = [('평균', mu, C['accent'], '--'),
            ('목표', it.get('target', np.nan), C['warning'], '-.'),
            ('LSL', it.get('lsl', np.nan), C['lsl'], ':'),
            ('USL', it.get('usl', np.nan), C['usl'], ':')]
    refs = [(name, float(v), color, style) for name, v, color, style in refs
            if v is not None and np.isfinite(v)]
    if range_mode == 'spec':
        lo, hi = min([lo]+[r[1] for r in refs]), max([hi]+[r[1] for r in refs])
    pad = (hi-lo)*.08 if hi > lo else max(abs(mu)*.001, .001)
    lo, hi = lo-pad, hi+pad
    ax.hist(a, bins=max(5, bins), density=True, color=C['primary'], alpha=.65,
            edgecolor=C['ax_bg'], linewidth=.7, label='관측 분포')
    if it.get('distribution_mode') == 'nonnormal' and (it.get('distribution_model') or {}).get('eligible'):
        from spc_nonnormal import frozen_model
        model = it['distribution_model']
        xs = np.linspace(lo, hi, 600)
        ax.plot(xs, frozen_model(model).pdf(xs), color=C['accent'], lw=2, label='적합 ' + model['name'] + ' 분포')
    elif sd > 0 and it.get('distribution_mode') != 'nonnormal':
        xs = np.linspace(lo, hi, 600)
        ax.plot(xs, stats.norm.pdf(xs, mu, sd), color=C['accent'], lw=2, label='적합 정규분포')
    for name, value, color, style in refs:
        if lo <= value <= hi:
            ax.axvline(value, color=color, ls=style, lw=1.3, label=f'{name} {value:.4f}')
    ax.set_xlim(lo, hi)
    ax.set_xlabel('측정값', fontsize=10)
    ax.set_ylabel('확률밀도', fontsize=10)
    ax.legend(loc='upper center', bbox_to_anchor=(.5, -.22), ncol=3,
              frameon=False, fontsize=8, labelcolor=ink)
    specs = '   ·   '.join(f'{name} {v:.4f}' for name,v,_,_ in refs if name != '평균')
    fig.text(.07, .045, specs or '규격 / 목표 미설정', fontsize=9, color=C['muted'])
    fig.text(.07, .012, '분포 확대: 화면 밖 규격은 하단 수치로 확인' if range_mode != 'spec'
             else '규격 전체: 관측 분포와 규격 위치를 함께 표시', fontsize=8, color=C['muted'])
    return fig


def build_qq_plot(
    it: Dict[str, Any],
    palette: str = DEFAULT_PALETTE,
    figsize: Tuple[float, float] = (9, 5.6),
) -> Figure:
    """Normal Q-Q diagnostic; no misleading observation-centred confidence band."""
    if it.get('distribution_mode') == 'nonnormal':
        from spc_nonnormal import frozen_model
        fig, ax, C, ink = _distribution_canvas(it, palette, figsize, '적합분포 진단 / Q-Q Plot')
        model = it.get('distribution_model') or {}
        if model.get('eligible'):
            a = np.sort(np.asarray(it['series'], dtype=float))
            a = a[np.linspace(0, len(a)-1, min(len(a), 10000)).astype(int)]
            theory = frozen_model(model).ppf((np.arange(len(a))+.5)/len(a))
            ax.scatter(theory, a, s=12, color=C['primary'], alpha=.75)
            lo, hi = min(theory.min(), a.min()), max(theory.max(), a.max())
            ax.plot([lo, hi], [lo, hi], color=C['muted'])
            _distribution_metrics(fig, [('적용 분포', model['name']), ('AD', _fmt(model['ad'])),
                                       ('bootstrap p', _fmt(model['pvalue'])), ('검정 N', str(model['test_n']))], C, ink)
            fig.text(.07, .055, '모수 재추정 bootstrap AD · 적합도 통과는 공정 안정성의 증거가 아닙니다.', fontsize=9, color=C['muted'])
        else:
            ax.text(.5, .5, '검증된 분포 없음 · 분포 진단과 선택을 확인하세요.', ha='center', transform=ax.transAxes)
        ax.set_xlabel('선택 분포 이론 분위수')
        ax.set_ylabel('관측값 분위수')
        return fig
    fig, ax, C, ink = _distribution_canvas(it, palette, figsize, '정규성 진단  /  Q-Q Plot')
    a = np.asarray(it['series'], dtype=float)
    a = a[np.isfinite(a)]
    valid = a.size >= 3 and np.ptp(a) > 0
    from spc_normality import normality_diagnostics
    diagnostic=it.get('normality_diagnostics',{}).get('transformed' if it.get('box_cox_applied') else 'individual')
    diagnostic=diagnostic or normality_diagnostics(a)
    skew = diagnostic.get('skewness')
    kurt = diagnostic.get('excess_kurtosis')
    _distribution_metrics(fig, [('표본 수', f'{a.size:,}'), ('왜도', _fmt(skew)),
                               ('초과첨도', _fmt(kurt)), ('기준 분포', '정규분포')], C, ink)
    if valid:
        from spc_core import _normality_sample
        (osm, osr), (slope, intercept, _) = stats.probplot(_normality_sample(a), dist='norm')
        ax.plot(osm, slope*osm+intercept, color=C['muted'], lw=1.4,
                label='최소제곱 기준선', zorder=2)
        ax.scatter(osm, osr, s=20, color=C['primary'], alpha=.75,
                   edgecolors=C['ax_bg'], linewidths=.4, label='관측값', zorder=3)
        summary = diagnostic['message']
        verdict = ('정규성 이탈 신호 · 꼬리와 집단 혼합 여부를 확인하세요.' if '의심' in summary
                   else '유의수준 5%에서 정규성을 기각할 근거가 부족합니다.' if '만족' in summary
                   else '정규성 검정 불가 · 측정값과 검정 조건을 확인하세요.')
        fig.text(.07, .105, summary, fontsize=10, color=ink, weight='bold')
        fig.text(.07, .055, verdict, fontsize=9, color=C['muted'])
        fig.text(.07, .015, '점이 기준선에서 벗어나는 위치와 크기를 함께 확인하세요.', fontsize=8, color=C['muted'])
        ax.legend(loc='upper left', frameon=False, fontsize=9, labelcolor=ink)
    else:
        ax.text(.5, .5, '정규성 진단 불가: 표본 3개 이상과 변동이 필요합니다',
                ha='center', transform=ax.transAxes, fontsize=10, color=ink)
    ax.set_xlabel('정규분포 이론 분위수', fontsize=10)
    ax.set_ylabel('관측값 분위수', fontsize=10)
    return fig


def stability_assessment(results, ppk_limit=1.33, ratio_limit=1.25):
    """Classify finite capability pairs; undefined ratios stay unclassified."""
    if not (np.isfinite(ppk_limit) and ppk_limit > 0
            and np.isfinite(ratio_limit) and ratio_limit > 1):
        raise ValueError('Ppk 기준은 0보다 크고 비율 기준은 1보다 커야 합니다.')
    rows = []
    for it in results:
        ratio, ppk = it.get('within_overall_ratio'), it.get('ppk')
        if ratio is None or ppk is None or not np.isfinite(ratio) or not np.isfinite(ppk) or ratio < 0:
            continue
        group = (0 if ppk >= ppk_limit else 2) + (1 if ratio > ratio_limit else 0)
        rows.append(dict(column=str(it['column']), ratio=float(ratio), ppk=float(ppk),
                         cwk=it.get('cwk', np.nan), group=group))
    return rows


def build_interactive_stability_scatter(results, palette=DEFAULT_PALETTE,
                                        ppk_limit=1.33, ratio_limit=1.25,
                                        focus=True, show_contour=False, show_labels=False):
    """Four-region capability diagnostic with explicit clipping and hover details."""
    import plotly.graph_objects as go
    import html
    C = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    ink = '#E2E8F0' if palette == 'dark' else '#24364B'
    rows = stability_assessment(results, ppk_limit, ratio_limit)
    xmax = max(2.5, ratio_limit * 1.5)
    ymin, ymax = -.5, max(3., ppk_limit * 2)
    if not focus and rows:
        xmax = max(xmax, max(r['ratio'] for r in rows) * 1.1)
        ymin = min(ymin, min(r['ppk'] for r in rows) - .2)
        ymax = max(ymax, max(r['ppk'] for r in rows) * 1.12)
    regions = [
        ('Ppk 기준 이상 · 변동 차이 작음', '#16A085', (0, ratio_limit, ppk_limit, ymax)),
        ('Ppk 기준 이상 · 변동 차이 큼', '#C49A17', (ratio_limit, xmax, ppk_limit, ymax)),
        ('Ppk 기준 미만 · 변동 차이 작음', '#D98236', (0, ratio_limit, ymin, ppk_limit)),
        ('Ppk 기준 미만 · 변동 차이 큼', '#D84F65', (ratio_limit, xmax, ymin, ppk_limit)),
    ]
    fig = go.Figure()
    for group, (label, color, (x0, x1, y0, y1)) in enumerate(regions):
        fig.add_shape(type='rect', x0=x0, x1=x1, y0=y0, y1=y1,
                      fillcolor=color, opacity=.16, line_width=0, layer='below')
        fig.add_annotation(x=(x0+x1)/2, y=y1, yshift=-16, text=label,
                           showarrow=False, font=dict(size=12, color=ink))
        members = [r for r in rows if r['group'] == group]
        fig.add_trace(go.Scatter(
            x=[r['ratio'] for r in members], y=[r['ppk'] for r in members],
            mode='markers+text' if show_labels else 'markers',
            text=[html.escape(r['column']) for r in members], textposition='top right',
            textfont=dict(size=11, color=ink), name=label,
            marker=dict(size=11, color=color, line=dict(color=C['ax_bg'], width=1.5)),
            customdata=[[html.escape(r['column']), r['cwk']] for r in members],
            hovertemplate='<b>%{customdata[0]}</b><br>Ppk %{y:.4f}<br>Cwk %{customdata[1]:.4f}'
                          '<br>|Cwk/Ppk| %{x:.4f}<extra>%{fullData.name}</extra>'))
    fig.add_vline(x=ratio_limit, line_color=C['muted'], line_dash='dash', line_width=1)
    fig.add_hline(y=ppk_limit, line_color=C['muted'], line_dash='dash', line_width=1)
    if show_contour:
        xs = np.linspace(max(.02, 1/ymax), xmax, 300)
        fig.add_trace(go.Scatter(x=xs, y=1/xs, mode='lines', name='Cwk = 1 (Ppk > 0)',
                                line=dict(color=C['danger'], width=1.8),
                                hovertemplate='Cwk = 1<br>비율 %{x:.3f}<br>Ppk %{y:.3f}<extra></extra>'))
    outside = sum(not (0 <= r['ratio'] <= xmax and ymin <= r['ppk'] <= ymax) for r in rows)
    fig.update_layout(height=600, paper_bgcolor=C['ax_bg'], plot_bgcolor=C['ax_bg'],
                      font=dict(family=_chosen_font, color=ink), margin=dict(l=65, r=30, t=25, b=75),
                      showlegend=False, hovermode='closest',
                      annotations=list(fig.layout.annotations) + [dict(
                          text=f'화면 밖 {outside}개 · 평가 제외 {len(results)-len(rows)}개',
                          x=1, y=1.06, xref='paper', yref='paper', xanchor='right',
                          showarrow=False, font=dict(size=11, color=C['muted']))])
    fig.update_xaxes(title=f'군내/전체 지수 비율 |Cwk/Ppk| · 구분 기준 {ratio_limit:g}',
                     range=[0, xmax], gridcolor=C['grid'], zeroline=False)
    fig.update_yaxes(title=f'전체 성능 Ppk (참고) · 충족 기준 {ppk_limit:g}',
                     range=[ymin, ymax], gridcolor=C['grid'], zeroline=False)
    return fig


def build_stability_scatter(
    results: list,
    palette: str = DEFAULT_PALETTE,
    figsize: Tuple[float, float] = (9, 6),
) -> Figure:
    """
    ?덉젙???곗젏?? X異?Cwk/Ppk 鍮꾩쑉(?덉젙??, Y異?Ppk.
    湲곗??? Cwk/Ppk=1 (?섏쭅), Ppk=1.33 / 1.0 (?섑룊).
    """
    C = _apply_palette(palette)
    fig, ax = plt.subplots(figsize=figsize, facecolor=C['bg'])
    ax.set_facecolor(C['ax_bg'])

    for it in results:
        ratio = it.get('within_overall_ratio', np.nan)
        ppk = it.get('ppk', np.nan)
        col = it['column']
        cpk = it.get('cpk', np.nan)

        if not (np.isfinite(ratio) and np.isfinite(ppk)):
            continue

        # ?됱긽: ppk<1.0=danger, 1.0~1.33=warning, >=1.33=success
        if ppk < 1.0:
            color = C['danger']
        elif ppk < 1.33:
            color = C['warning']
        else:
            color = C['success']

        ax.scatter(ratio, ppk, s=80, color=color, zorder=5, edgecolors='white', linewidths=0.8)
        ax.annotate(str(col), (ratio, ppk), textcoords='offset points', xytext=(5, 3),
                    fontsize=7.5, color=C['muted'])

    ax.axvline(1.0, linestyle='--', color=C['muted'], linewidth=1.1, alpha=0.7, label='군내/전체 비율=1')
    ax.axhline(1.33, linestyle='--', color=C['success'], linewidth=1.1, alpha=0.7, label='Ppk=1.33')
    ax.axhline(1.00, linestyle='--', color=C['warning'], linewidth=1.1, alpha=0.7, label='Ppk=1.00')

    ax.set_xlabel('군내/전체 지수 비율 (|Cwk/Ppk|)', fontsize=10)
    ax.set_ylabel('Ppk', fontsize=10)
    ax.set_title('군내·전체 변동 진단 산점도 (Ppk vs Cwk/Ppk 비율)', fontweight='bold')
    ax.legend(frameon=True, framealpha=0.88, fontsize=8.5)

    from matplotlib.patches import Patch
    legend_els = [
        Patch(facecolor=C['success'], label='Ppk >= 1.33 (참고 기준 이상)'),
        Patch(facecolor=C['warning'], label='1.00 <= Ppk < 1.33 (참고 구간)'),
        Patch(facecolor=C['danger'], label='Ppk < 1.00 (참고 기준 미만)'),
    ]
    ax.legend(handles=legend_els, loc='upper left', fontsize=8, framealpha=0.88)

    ax.tick_params(axis='both', which='major', direction='in')
    fig.tight_layout()
    return fig


def build_overview_2x2(
    it: Dict[str, Any],
    palette: str = DEFAULT_PALETTE,
    hist_bins: int = 30,
    figsize: Tuple[float, float] = (13, 8.5),
    ksq_toggles: Optional[Dict[str, bool]] = None,
    show_zones: Optional[Dict[str, bool]] = None,
) -> Figure:
    """
    2횞2 媛쒖슂 Figure:
      [0,0] ?붿빟 ?뺣낫 ?⑤꼸  |  [0,1] ?덉뒪?좉렇??      [1,0] X/Xbar 愿由щ룄   |  [1,1] R/S/MR 愿由щ룄
    """
    C = _apply_palette(palette)
    if ksq_toggles is None:
        ksq_toggles = {f'T{i}': True for i in range(1, 9)}
    if show_zones is None:
        show_zones = {'C': True, 'B': True, 'A': True, 'Z3': True}

    col = it['column']
    series = it['series']
    means = it['means']
    ranges = it['ranges']
    usl = it['usl']
    lsl = it['lsl']
    target = it.get('target', np.nan)
    mu = it['mu']
    std_all = it['std_all']
    std_within = it['std_within']
    cp = it['cp']
    cpk = it['cpk']
    pp = it['pp']
    ppk = it['ppk']
    cpm = it.get('cpm', np.nan)
    cpmk = it.get('cpmk', np.nan)
    ppm_cpk = it.get('ppm_within', np.nan)
    ppm_ppk = it['ppm_ppk']
    emp_ppm = it['emp_ppm']
    xbarbar = it['xbarbar']
    ucl = it['ucl']
    lcl = it['lcl']
    r_bar = it['r_bar']
    r_ucl = it['r_ucl']
    r_lcl = it['r_lcl']
    stab_ratio = it['stab_ratio']
    norm_msg = it['norm_msg']
    is_xmr = it['is_xmr']
    n = it['n']
    A2_plot = it.get('A2_plot', np.nan)
    phase_label = str(it.get('control_phase_label', '해석용'))
    limit_reference_label = str(it.get('limit_reference_label', '전체 데이터'))
    ksq_counts = it.get('ksq_counts', {f'T{i}': 0 for i in range(1, 9)})
    ksq_idx = it.get('ksq_idx', {f'T{i}': set() for i in range(1, 9)})

    fig = plt.figure(figsize=figsize, facecolor=C['bg'])
    gs = fig.add_gridspec(2, 2, left=0.06, right=0.995, top=0.93, bottom=0.07,
                          wspace=0.06, hspace=0.35)
    ax1 = fig.add_subplot(gs[0, 0])  # ?붿빟 ?뺣낫
    ax2 = fig.add_subplot(gs[0, 1])  # 히스토그램
    ax3 = fig.add_subplot(gs[1, 0])  # X 관리도
    ax4 = fig.add_subplot(gs[1, 1])  # ?고룷 愿由щ룄

    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor(C['ax_bg'])

    x_lab = "Individuals" if is_xmr else "Xbar"
    r_lab = str(it.get('dispersion_kind', "MR" if is_xmr else "R"))

    # ???????????? [0,0] ?붿빟 ?뺣낫 ?⑤꼸 ????????????
    ax1.axis('off')
    ax1.set_title(f'{col} - 요약 정보', fontsize=11, fontweight='bold', color='#1a1a1a', pad=12)

    lines = [
        ("USL / LSL", f"{_fmt(usl, 3) if np.isfinite(usl) else '없음'} / {_fmt(lsl, 3) if np.isfinite(lsl) else '없음'}"),
        ("목표치(T)", _fmt(target, 3) if np.isfinite(target) else "없음"),
        ("운영 모드", phase_label),
        ("한계 기준", limit_reference_label),
        ("평균", _fmt(mu)),
        ("표준편차 (군내 / 전체)", f"{_fmt(std_within)} / {_fmt(std_all)}"),
        ("Cp / Cpk", f"{_fmt(cp)} / {_fmt(cpk)}"),
        ("Cp/Cpk 적용 상태", it.get('capability_status', '재분석 필요')),
        ("Cw / Cwk (진단용)", f"{_fmt(it.get('cw', np.nan))} / {_fmt(it.get('cwk', np.nan))}"),
        ("Pp / Ppk", f"{_fmt(pp)} / {_fmt(ppk)}"),
        ("Cpm / Cpmk", f"{_fmt(cpm)} / {_fmt(cpmk)}"),
        ("진단 비율 (Cwk/Ppk)", _fmt(stab_ratio)),
        ("PPM (Within)", f"{ppm_cpk:,.0f}" if np.isfinite(ppm_cpk) else "N/A"),
        ("PPM (Overall)", f"{ppm_ppk:,.0f}" if np.isfinite(ppm_ppk) else "N/A"),
        ("PPM (Observed)", f"{emp_ppm:,.0f}" if np.isfinite(emp_ppm) else "N/A"),
        (f"{x_lab} / UCL / LCL", f"{_fmt(xbarbar)} / {_fmt(ucl)} / {_fmt(lcl)}"),
        (f"{r_lab}-bar / UCL / LCL", f"{_fmt(r_bar)} / {_fmt(r_ucl)} / {_fmt(r_lcl)}"),
        (f"한계위반 ({x_lab}/{r_lab})",
         f"UCL {it['xbar_ucl_cnt']}/{it['r_ucl_cnt']}  LCL {it['xbar_lcl_cnt']}/{it['r_lcl_cnt']}"),
        ("Normality", norm_msg),
    ]
    if it.get('distribution_mode') == 'nonnormal':
        lines.append(('분포 / 계산법', (it.get('distribution_model') or {}).get('name', '미확인') + ' / ' + it.get('capability_method', 'G')))

    # 諛곌꼍 諛뺤뒪
    ax1.add_patch(Rectangle((0.01, 0.01), 0.98, 0.98, linewidth=1,
                             edgecolor=C['grid'], facecolor='white', alpha=0.9,
                             transform=ax1.transAxes, zorder=1))
    y = 0.94
    step = 0.88 / max(len(lines) - 1, 1)
    for lab, val in lines:
        ax1.text(0.05, y, f"{lab} :", color=C['muted'], fontsize=8.5, va='center',
                 ha='left', transform=ax1.transAxes, zorder=2)
        ax1.text(0.50, y, val, color='#111111', fontsize=8.5, va='center',
                 ha='left', transform=ax1.transAxes, zorder=2)
        y -= step

    # ???????????? [0,1] ?덉뒪?좉렇??????????????
    if np.isfinite(std_all) and std_all > 0:
        lo = mu - 4 * std_all
        hi = mu + 4 * std_all
    else:
        lo = float(np.min(series))
        hi = float(np.max(series))
        if lo == hi:
            lo -= 0.5
            hi += 0.5

    cands = [lo, hi]
    if np.isfinite(lsl): cands.append(float(lsl))
    if np.isfinite(usl): cands.append(float(usl))
    if np.isfinite(target): cands.append(float(target))
    xmin_h, xmax_h = min(cands), max(cands)
    pad_h = max(1e-6, (xmax_h - xmin_h) * 0.04)
    xmin_h -= pad_h
    xmax_h += pad_h

    ax2.hist(series, bins=max(5, hist_bins), density=True, alpha=0.85, color=C['primary'],
             edgecolor='white', linewidth=0.4)
    xs_h = np.linspace(xmin_h, xmax_h, 300)
    if it.get('distribution_mode') == 'nonnormal' and (it.get('distribution_model') or {}).get('eligible'):
        from spc_nonnormal import frozen_model
        model = it['distribution_model']
        ax2.plot(xs_h, frozen_model(model).pdf(xs_h), linewidth=2., color=C['accent'], label=model['name']+' PDF')
    elif np.isfinite(std_all) and std_all > 0 and it.get('distribution_mode') != 'nonnormal':
        ax2.plot(xs_h, stats.norm.pdf(xs_h, mu, std_all), linewidth=2.0, color=C['accent'], label='정규PDF')
    ax2.axvline(mu, linestyle='--', color=C['mean'], linewidth=1.2, label='평균')
    if np.isfinite(target):
        ax2.axvline(target, linestyle='-.', color=C['warning'], linewidth=1.3, label='목표치(T)')
    if np.isfinite(lsl):
        ax2.axvline(lsl, linestyle='--', color=C['lsl'], linewidth=1.2, label='LSL')
    if np.isfinite(usl):
        ax2.axvline(usl, linestyle='--', color=C['usl'], linewidth=1.2, label='USL')
    ax2.set_xlim(xmin_h, xmax_h)
    ax2.set_title(f'{col} - Histogram', fontsize=10, fontweight='bold')
    ax2.set_ylabel('Density', fontsize=9)
    ax2.legend(loc='upper left', frameon=True, framealpha=0.88, fontsize=7.5)
    ax2.xaxis.set_minor_locator(AutoMinorLocator(2))
    ax2.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax2.tick_params(axis='both', which='minor', direction='in', length=3)

    # ???????????? [1,0] X/Xbar 愿由щ룄 ????????????
    if np.isfinite(ucl) and np.isfinite(lcl) and (ucl > lcl):
        sigma_x = (ucl - lcl) / 6.0
    else:
        sigma_x = std_within if is_xmr else (
            (A2_plot * r_bar) / 3.0 if (np.isfinite(r_bar) and np.isfinite(A2_plot)) else np.nan
        )

    if np.isfinite(sigma_x) and sigma_x > 0 and np.isfinite(xbarbar) and it.get('distribution_mode') != 'nonnormal':
        z1u = xbarbar + sigma_x
        z2u = xbarbar + 2 * sigma_x
        z1l = xbarbar - sigma_x
        z2l = xbarbar - 2 * sigma_x
        if show_zones.get('C', True):
            ax3.axhspan(z1l, z1u, facecolor=C['zoneC'], alpha=0.55, zorder=0)
        if show_zones.get('B', True):
            ax3.axhspan(z1u, z2u, facecolor=C['zoneB'], alpha=0.55, zorder=0)
            ax3.axhspan(z2l, z1l, facecolor=C['zoneB'], alpha=0.55, zorder=0)
        if show_zones.get('A', True):
            ax3.axhspan(z2u, min(xbarbar + 3 * sigma_x, ucl) if np.isfinite(ucl) else xbarbar + 3 * sigma_x,
                        facecolor=C['zoneA'], alpha=0.55, zorder=0)
            ax3.axhspan(max(xbarbar - 3 * sigma_x, lcl) if np.isfinite(lcl) else xbarbar - 3 * sigma_x, z2l,
                        facecolor=C['zoneA'], alpha=0.55, zorder=0)

    xs3 = list(range(len(means)))
    ax3.plot(xs3, means, marker='o', markersize=3.2, color=C['primary'], linewidth=1.2)
    ax3.axhline(xbarbar, linestyle='--', color=C['warning'], linewidth=1.2)
    if np.isfinite(ucl):
        ax3.axhline(ucl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=1.2, label='UCL')
    if np.isfinite(lcl):
        ax3.axhline(lcl, linestyle=(0, (6, 4)), color=C['success'], linewidth=1.2, label='LCL')
    ax3.set_xlim(-0.5, len(means) - 0.5)

    # KSQ 留덊궧 (媛쒖슂??異뺤냼)
    small_mark = {
        'T1': dict(marker='o', s=55, c='#E53935', zorder=10, edgecolors='white', linewidths=0.7),
        'T2': dict(marker='s', s=38, c=C['accent'], zorder=8),
        'T3': dict(marker='^', s=40, c=C['warning'], zorder=8),
    }
    for key in ('T1', 'T2', 'T3'):
        pts = sorted(ksq_idx.get(key, set()))
        if pts:
            cfg = small_mark.get(key, dict(marker='*', s=30, c='gray', zorder=8))
            ax3.scatter(pts, [means[i] for i in pts], **cfg)

    ax3.set_title(f'{"Individuals" if is_xmr else "Xbar"} 관리도', fontsize=10, fontweight='bold')
    ax3.set_ylabel('X' if is_xmr else 'Xbar', fontsize=9)
    ax3.margins(y=0.13)

    # ???????????? [1,1] R/S/MR 愿由щ룄 ????????????
    ax4.plot(list(range(len(ranges))), ranges, marker='o', markersize=3.5,
             color=C['primary'], linewidth=1.2)
    if np.isfinite(r_bar):
        ax4.axhline(r_bar, linestyle='--', color=C['warning'], linewidth=1.2, label=f'{r_lab}-bar')
    if np.isfinite(r_ucl):
        ax4.axhline(r_ucl, linestyle=(0, (6, 4)), color=C['danger'], linewidth=1.2, label=f'UCL({r_lab})')
    if np.isfinite(r_lcl) and r_lcl > 0:
        ax4.axhline(r_lcl, linestyle=(0, (6, 4)), color=C['success'], linewidth=1.2, label=f'LCL({r_lab})')

    for i_r, rval in enumerate(ranges):
        if (np.isfinite(r_ucl) and rval > r_ucl) or (np.isfinite(r_lcl) and rval < r_lcl):
            ax4.scatter(i_r, rval, s=30, c=C['danger'], zorder=3)

    ax4.set_xlim(-0.5, len(ranges) - 0.5)
    ax4.set_title(f'{r_lab} 관리도', fontsize=10, fontweight='bold')
    ax4.set_ylabel(r_lab, fontsize=9)
    ax4.margins(y=0.13)
    ax4.legend(loc='upper right', frameon=False, fontsize=7.5)

    # X異??쇰꺼 (ax3, ax4 怨듯넻)
    for ax_, m_, n_, raw_labels in [
        (ax3, len(means), n, it.get('x_labels')),
        (ax4, len(ranges), n, it.get('r_labels')),
    ]:
        lbls = _labels_or_fallback(raw_labels, m_, n_, is_xmr)
        ticks = list(range(m_))
        if m_ > 12:
            step = math.ceil(m_ / 12)
            ax_.set_xticks(ticks[::step])
            ax_.set_xticklabels(lbls[::step], rotation=28, ha='right', fontsize=7)
        else:
            ax_.set_xticks(ticks)
            ax_.set_xticklabels(lbls, rotation=28, ha='right', fontsize=7)

    for ax_ in (ax3, ax4):
        ax_.tick_params(axis='both', which='major', direction='in')
        ax_.yaxis.set_minor_locator(AutoMinorLocator(2))

    return fig


# ??????????????????????????? ?ㅻ????곴? 遺꾩꽍 ?쒓컖?????????????????????????????

def build_correlation_heatmap(df: pd.DataFrame, cols: list[str], palette: str = DEFAULT_PALETTE) -> Figure:
    """?좏깮??蹂?섎뱾 媛꾩쓽 ?곴?愿怨??덊듃留듭쓣 ?앹꽦?쒕떎."""
    C = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    _apply_palette(palette)
    
    # ?섏튂???곗씠?곕쭔 異붿텧 諛??곴?怨꾩닔 怨꾩궛
    df_sub = df[cols].apply(pd.to_numeric, errors='coerce').dropna()
    if df_sub.empty:
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.text(0.5, 0.5, "유효한 수치 데이터가 부족합니다.", ha='center', va='center')
        return fig
        
    corr = df_sub.corr(method='pearson')
    
    # ?덊듃留??앹꽦
    fig, ax = plt.subplots(figsize=(max(6, len(cols)*0.8), max(5, len(cols)*0.7)))
    fig.patch.set_facecolor(C['bg'])
    ax.set_facecolor(C['ax_bg'])
    
    # Seaborn ?덊듃留?(?쒓? ?고듃 ?곸슜??
    if sns is not None:
        sns.heatmap(
            corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
            square=True, linewidths=.5, cbar_kws={"shrink": .8},
            ax=ax, annot_kws={"size": 9}
        )
    else:
        corr_vals = corr.to_numpy(dtype=float)
        im = ax.imshow(corr_vals, cmap="RdBu_r", vmin=-1.0, vmax=1.0, aspect="equal")
        ax.set_xticks(np.arange(len(corr.columns)))
        ax.set_yticks(np.arange(len(corr.index)))
        ax.set_xticklabels(corr.columns, rotation=45, ha='right', fontsize=9)
        ax.set_yticklabels(corr.index, fontsize=9)

        for i in range(corr_vals.shape[0]):
            for j in range(corr_vals.shape[1]):
                v = corr_vals[i, j]
                text_color = 'white' if abs(v) >= 0.5 else 'black'
                ax.text(j, i, f"{v:.2f}", ha='center', va='center', fontsize=8, color=text_color)

        cbar = fig.colorbar(im, ax=ax, shrink=0.8)
        cbar.ax.set_ylabel('Pearson r', rotation=270, labelpad=12)
    
    ax.set_title("변수 간 상관관계 히트맵 (Pearson)", fontsize=12, fontweight='bold', pad=20)
    plt.tight_layout()
    return fig


def build_scatter_matrix(df: pd.DataFrame, cols: list[str], palette: str = DEFAULT_PALETTE) -> Figure:
    """Create scatter matrix for selected numeric columns."""
    C = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    _apply_palette(palette)

    df_sub = df[cols].apply(pd.to_numeric, errors='coerce').dropna()
    if df_sub.empty or len(cols) < 2:
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.text(0.5, 0.5, "Select at least two numeric columns for scatter matrix.", ha='center', va='center')
        return fig

    title = f"Scatter Matrix (N={len(df_sub)})"

    if sns is not None:
        g = sns.PairGrid(df_sub, height=2.0)
        g.map_upper(sns.scatterplot, s=20, alpha=0.6, color=C['primary'])
        g.map_lower(sns.kdeplot, fill=True, alpha=0.3, color=C['accent'])
        g.map_diag(sns.histplot, kde=True, color=C['primary'], alpha=0.4)
        g.fig.subplots_adjust(top=0.92)
        g.fig.suptitle(title, fontsize=14, fontweight='bold')
        g.fig.set_facecolor(C['bg'])
        return g.fig

    axes = pd.plotting.scatter_matrix(
        df_sub,
        alpha=0.65,
        figsize=(max(6, len(cols) * 2.2), max(6, len(cols) * 2.2)),
        diagonal='hist',
        marker='o',
        color=C['primary'],
        hist_kwds={'alpha': 0.5, 'color': C['accent']},
        s=15,
    )
    fig = axes[0, 0].figure
    fig.patch.set_facecolor(C['bg'])
    for ax_row in axes:
        for ax in ax_row:
            ax.set_facecolor(C['ax_bg'])
            ax.grid(True, alpha=0.2)
    fig.suptitle(title, fontsize=14, fontweight='bold')
    fig.tight_layout()
    fig.subplots_adjust(top=0.92)
    return fig
