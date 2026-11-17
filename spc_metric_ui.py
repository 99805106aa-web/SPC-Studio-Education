"""Consistent, responsive cards for the variable detail view."""
from html import escape
import math

from spc_core import fmt_value, fmt_ppm


METRIC_CARD_CSS = """
<style>
.spc-metrics { color:var(--text-color,#172b4d); margin:6px 0 12px; }
.spc-metrics * { box-sizing:border-box; }
.spc-metrics section { margin:0 0 12px; }
.spc-section-heading { display:flex; align-items:baseline; gap:10px; margin:0 0 6px; flex-wrap:wrap; }
.spc-section-heading h3 { margin:0; padding:0; font-size:14px; font-weight:700; }
.spc-section-heading p { margin:0; font-size:11px; opacity:.7; }
.spc-card-grid { display:grid; gap:8px; }
.spc-grid-stats { grid-template-columns:repeat(var(--spc-stat-cols,7),minmax(0,1fr)); }
.spc-grid-primary { grid-template-columns:repeat(6,minmax(0,1fr)); }
.spc-grid-secondary { grid-template-columns:repeat(5,minmax(0,1fr)); }
.spc-kpi { position:relative; min-width:0; min-height:100px; padding:10px 12px 8px;
  border:1px solid #d8e1eb; border-radius:8px; background:var(--secondary-background-color,#f8fafc);
  display:flex; flex-direction:column; }
.spc-kpi-head { display:flex; justify-content:space-between; align-items:center; gap:5px; }
.spc-kpi-label { font-size:13px; font-weight:700; line-height:20px; }
.spc-kpi-value { font-size:27px; font-weight:650; line-height:34px; letter-spacing:-.035em;
  font-variant-numeric:tabular-nums; margin:6px 0 2px; overflow-wrap:anywhere; }
.spc-kpi-value.spc-value-status { font-size:16px; line-height:34px; letter-spacing:-.04em; }
.spc-kpi-footer { margin-top:auto; height:16px; font-size:10px; line-height:16px; }
.spc-kpi-badge { display:inline-block; border-radius:3px; padding:0 5px; font-weight:600; }
.spc-grid-stats .spc-kpi { min-height:72px; padding:8px 10px; }
.spc-grid-stats .spc-kpi-value { font-size:23px; line-height:28px; margin:3px 0 0; }
.spc-grid-stats .spc-kpi-footer { display:none; }
.spc-kpi.spc-state-hold, .spc-kpi.spc-state-warn { border-left:3px solid #d69b35; }
.spc-state-warn .spc-kpi-badge { color:#92400e; background:#fff0cf; }
.spc-kpi.spc-state-bad { border-left:3px solid #d85a55; }
.spc-state-bad .spc-kpi-badge { color:#a12424; background:#fee7e5; }
.spc-kpi.spc-state-good { border-left:3px solid #168568; }
.spc-state-good .spc-kpi-badge { color:#08604c; background:#d9f2e9; }
.spc-card-note { margin:2px 0 0; font-size:12px; line-height:1.5; opacity:.8; }
.spc-help { flex:0 0 auto; font-size:10px; color:#718299; }
.spc-help summary { cursor:pointer; list-style:none; border:1px solid #a6b3c3; border-radius:50%;
  width:15px; height:15px; line-height:13px; text-align:center; }
.spc-help summary::-webkit-details-marker { display:none; }
.spc-help[open] p { position:absolute; left:4px; top:30px; width:max(100%,190px); max-width:270px;
  z-index:5; margin:0; padding:10px; font-size:12px; line-height:1.6; border:1px solid #c7d3e1;
  border-radius:6px; background:var(--background-color,#fff); color:var(--text-color,#172b4d);
  box-shadow:0 5px 18px rgba(15,23,42,.12); }
.spc-kpi:last-child .spc-help[open] p { left:auto; right:4px; }
@media(max-width:1100px) {
  .spc-grid-stats { grid-template-columns:repeat(4,minmax(0,1fr)); }
  .spc-grid-primary, .spc-grid-secondary { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
@media(max-width:640px) {
  .spc-grid-stats, .spc-grid-primary, .spc-grid-secondary { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .spc-kpi-value { font-size:25px; }
  .spc-help[open] p { width:100%; min-width:0; }
}
@media(max-width:320px) {
  .spc-card-grid { grid-template-columns:1fr; }
}
</style>
"""


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def _ppm(value):
    return fmt_ppm(value)


def _index_text(value, it=None):
    if value == float('inf'):
        return '∞'
    if value == -float('inf'):
        return '−∞'
    if not _finite(value):
        return '—'
    value = float(value)
    from spc_policy import limits
    if any(value < boundary <= round(value, 3) for boundary in limits(it)):
        return repr(value)
    return fmt_value(value)


def _index_card(label, value, help_text, context='', it=None):
    badge, state = _capability_badge(value, it)
    if context and _finite(value):
        badge += ' · ' + context
    return _card(label, _index_text(value, it), '', badge, help_text, state)


def _card(label, value, subtitle, badge, help_text, state="neutral", status_value=False):
    value_cls = "spc-kpi-value" + (" spc-value-status" if status_value else "")
    return (
        f'<article class="spc-kpi spc-state-{state}" data-metric="{escape(label, quote=True)}">'
        f'<div class="spc-kpi-head"><span class="spc-kpi-label">{escape(label)}</span>'
        f'<details class="spc-help"><summary aria-label="{escape(label, quote=True)} 계산 설명">?</summary>'
        f'<p>{escape(help_text)}</p></details></div>'
        
        f'<div class="{value_cls}">{escape(str(value))}</div>'
        f'<div class="spc-kpi-footer">' + (f'<span class="spc-kpi-badge">{escape(badge)}</span>' if badge else '') + '</div></article>'
    )


def _group(title, subtitle, cards, *, layout):
    return (
        f'<section aria-label="{escape(title, quote=True)}"><div class="spc-section-heading">'
        f'<h3>{escape(title)}</h3>' + (f'<p>{escape(subtitle)}</p>' if subtitle else '') + '</div>'
        f'<div class="spc-card-grid spc-grid-{layout}" style="--spc-stat-cols:{len(cards)}">{"".join(cards)}</div></section>'
    )


def process_metric_cards_html(it):
    """Render existing result values; eligibility and calculations stay in spc_core."""
    cap_status = it.get('capability_status', '재분석 필요')
    reasons = "; ".join(it.get('capability_reasons', []))
    performance_note = it.get('performance_note', '전체 산포의 정규모델 가정 참고값입니다.')
    within_note = it.get('within_capability_note', '재분석이 필요합니다.')
    nonnormal = it.get('distribution_mode') == 'nonnormal'
    method = '.' + it.get('capability_method', 'G') if nonnormal or it.get('box_cox_applied') else ''
    def index_card(*args, **kwargs):
        return _index_card(*args, it=it, **kwargs)
    kind = str(it.get('dispersion_kind', 'R'))
    stats = [
        _card('평균 μ', fmt_value(it.get('mu')), '', '', '분석 데이터의 평균입니다.'),
        _card('σ 전체', fmt_value(it.get('std_all')), '', '', '전체 분석 데이터의 표본표준편차입니다.'),
        _card('σ 군내', fmt_value(it.get('std_within')), '', '', '부분군 내 산포 추정값입니다.'),
        _card('X-UCL', fmt_value(it.get('ucl')), '', '', 'X/Xbar 관리상한입니다. 고객·도면 규격과 구분하세요.'),
        _card('X-LCL', fmt_value(it.get('lcl')), '', '', 'X/Xbar 관리하한입니다. 고객·도면 규격과 구분하세요.'),
        _card(f'{kind}-UCL', fmt_value(it.get('r_ucl')), '', '', f'{kind} 산포 관리상한입니다.'),
    ]
    if (nonnormal and int(it.get('n', 0)) > 1) or (not nonnormal and not (kind == 'R' and int(it.get('n', 0)) <= 6)):
        stats.append(_card(f'{kind}-LCL', fmt_value(it.get('r_lcl')), '', '', f'{kind} 산포 관리하한입니다.'))
    groups = [_group('측정 통계 · 관리한계', '', stats, layout='stats')]
    groups.append(normality_cards_html(it))
    primary = []
    for key, label in [('cp', 'Cp'), ('cpk', 'Cpk')]:
        value = it.get(key)
        if cap_status != '적용 가능':
            primary.append(_card(label+method, cap_status, '', '적용 조건 확인', reasons, 'hold', True))
        elif not _finite(value) and value not in (float('inf'), -float('inf')):
            primary.append(_card(label+method, '—', '', '미적용', reasons or '양측 규격이 필요합니다.'))
        else:
            primary.append(index_card(label+method, value, '관측 구간의 적용 조건 충족 시 표시합니다. ' + performance_note))
    primary.extend([
        index_card('Pp'+method, it.get('pp'), performance_note, '참고'),
        index_card('Ppk'+method, it.get('ppk'), performance_note, '참고'),
        index_card('Cw', it.get('cw'), within_note, '진단'),
        index_card('Cwk', it.get('cwk'), within_note, '진단'),
    ])
    groups.append(_group('능력 · 성능 지표', 'Cp·Cpk / Pp·Ppk: 전체 변동  ·  Cw·Cwk: 군내 진단',
                         primary, layout='primary'))
    secondary = []
    for key, label in [('cpm','Cpm'),('cpmk','Cpmk')]:
        value = it.get(key)
        secondary.append(index_card(label, value, within_note if nonnormal else
                                     '목표치와 전체 표준편차를 사용하는 기존 목표치 반영 지수입니다.', '참고'))
    secondary.extend([
        _card('|Cwk/Ppk|', fmt_value(it.get('within_overall_ratio')), '', '',
              '군내/전체 지수 비율입니다. 이 값만으로 안정성을 확정하지 않습니다. Ppk=0 또는 Cwk 미계산 시 표시하지 않습니다.'),
        _card('PPM(Cwk)', _ppm(it.get('ppm_within')), '', '미적용' if nonnormal else '', within_note + ' 군내 산포의 정규모델 가정 예상 PPM입니다. n≥2에서 계산하는 진단용 참고값입니다.'),
        _card('PPM(적합분포)' if nonnormal else 'PPM(전체)', _ppm(it.get('ppm_ppk')), '', '', performance_note),
    ])
    groups.append(_group('목표치 · 변동 비교 · 예상 PPM',
                         'PPM은 선택 분포 모형의 참고값' if nonnormal else 'PPM은 정규모델 가정 참고값',
                         secondary, layout='secondary'))
    if cap_status != '적용 가능':
        groups.append('<p class="spc-card-note">' + escape(f'Cp·Cpk: {cap_status} · {reasons}') + '</p>')
    if nonnormal:
        groups.append('<p class="spc-card-note">' + escape(it.get('chart_mode', '') + ' · ' + it.get('pearson_rules_note', '')) + '</p>')
    return METRIC_CARD_CSS + '<div class="spc-metrics">' + ''.join(groups) + '</div>'



def _capability_badge(value, it=None):
    if not _finite(value):
        return ('모형 경계값' if value in (float('inf'), -float('inf')) else '미적용'), 'neutral'
    from spc_policy import limits
    warning, required = limits(it)
    if float(value) < warning:
        return f'기준미달 · < {warning:.2f}', 'bad'
    if float(value) < required:
        return f'주의 · < {required:.2f}', 'warn'
    return f'충족 · ≥ {required:.2f}', 'good'


def normality_cards_html(it):
    from spc_normality import STATUS_LABELS, pvalue_text, diagnostic_hint
    record=it.get('normality_diagnostics',{}).get('individual')
    if not record:
        cards=[_card(label,'—','','상세 분석 필요','상세 분석 후 검정 대상과 함께 표시합니다.')
               for label in ('정규성 p값','왜도','초과첨도')]
        note='상세 분석에서 정규성·분포 형태 지표를 확인할 수 있습니다.'
    else:
        status=record['status']
        method=record.get('test') or '검정 불가'
        badge=STATUS_LABELS[status]+' · '+method
        cards=[
            _card('정규성 p값',pvalue_text(record['pvalue']),'',badge,
                  f"유의수준 {record['alpha']:.2f}. AD p값은 유한 표본 보정을 적용한 근사값입니다. "
                  +diagnostic_hint(record),'warn' if status=='rejected' else 'neutral'),
            _card('왜도',fmt_value(record.get('skewness')),'','분포 형태 참고',
                  '분석 대상 전체 개별값의 보정 표본 왜도. 양수는 오른쪽, 음수는 왼쪽 꼬리 경향입니다. 부호만으로 분포를 선택하지 않습니다.'),
            _card('초과첨도',fmt_value(record.get('excess_kurtosis')),'','정규분포 기준 0',
                  'Fisher 초과첨도(편향 보정). 정규분포 기준은 0이며 꼬리 형태의 참고값입니다. 유효값 4개 이상에서 표시합니다.'),
        ]
        note=(f"{record['scope']} · {record['scale']} · 전체 N={record['total_n']:,}, 검정 N={record['test_n']:,}. "
              +diagnostic_hint(record))
    return ('<style>.spc-grid-diagnostics{grid-template-columns:repeat(3,minmax(0,1fr));}'
            '@media(max-width:640px){.spc-grid-diagnostics{grid-template-columns:1fr;}}</style>'
            +_group('분포 진단 · 개별 측정값','원단위 · 분석 포함 데이터',cards,layout='diagnostics')
            +'<p class="spc-card-note">'+escape(note)+'</p>')
