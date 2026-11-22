"""Consistent, responsive cards for the variable detail view."""
from html import escape
import math

from spc_core import fmt_value, fmt_ppm, screening_index_pair


STATUS_CELL_STYLES = {
    'bad': 'background-color:#fee2e2;color:#991b1b;font-weight:bold',
    'warn': 'background-color:#fef3c7;color:#92400e;font-weight:bold',
    'good': 'background-color:#dcfce7;color:#166534;font-weight:bold',
    'na': 'background-color:#f1f5f9;color:#475569;font-weight:normal',
}


METRIC_CARD_CSS = """
<style>
.spc-metrics { color:var(--text-color,#172b4d); margin:6px 0 12px; }
.spc-metrics * { box-sizing:border-box; }
.spc-metrics section { margin:0 0 12px; }
.spc-section-heading { display:flex; align-items:baseline; gap:10px; margin:0 0 6px; flex-wrap:wrap; }
.spc-section-heading h3 { margin:0; padding:0; font-size:14px; font-weight:700; }
.spc-section-heading p { margin:0; font-size:13px; line-height:1.5; }
.spc-card-grid { display:grid; gap:8px; }
.spc-grid-stats { grid-template-columns:repeat(var(--spc-stat-cols,7),minmax(0,1fr)); }
.spc-grid-primary { grid-template-columns:repeat(var(--spc-stat-cols,4),minmax(0,1fr)); }
.spc-grid-secondary { grid-template-columns:repeat(5,minmax(0,1fr)); }
.spc-kpi { position:relative; min-width:0; min-height:100px; padding:10px 12px 8px;
  border:1px solid #d8e1eb; border-radius:8px; background:var(--secondary-background-color,#f8fafc);
  display:flex; flex-direction:column; }
.spc-kpi-head { display:flex; justify-content:space-between; align-items:center; gap:5px; }
.spc-kpi-label { font-size:13px; font-weight:700; line-height:20px; }
.spc-kpi-value { font-size:27px; font-weight:650; line-height:34px; letter-spacing:-.035em;
  font-variant-numeric:tabular-nums; margin:6px 0 2px; overflow-wrap:anywhere; }
.spc-kpi-value.spc-value-status { font-size:16px; line-height:34px; letter-spacing:-.04em; }
.spc-kpi-footer { margin-top:auto; min-height:20px; font-size:12px; line-height:20px; }
.spc-kpi-badge { display:inline-block; border-radius:3px; padding:0 5px; font-weight:600; }
.spc-grid-stats .spc-kpi { min-height:72px; padding:8px 10px; }
.spc-grid-stats .spc-kpi-value { font-size:23px; line-height:28px; margin:3px 0 0; }
.spc-grid-stats .spc-kpi-footer { display:none; }
.spc-kpi.spc-state-hold, .spc-kpi.spc-state-warn { border-left:3px solid #d69b35; }
.spc-state-warn .spc-kpi-badge { color:#92400e; background:#fff0cf; }
.spc-state-hold .spc-kpi-badge { color:#92400e; background:#fff0cf; }
.spc-kpi.spc-state-bad { border-left:3px solid #d85a55; }
.spc-state-bad .spc-kpi-badge { color:#a12424; background:#fee7e5; }
.spc-kpi.spc-state-good { border-left:3px solid #168568; }
.spc-state-good .spc-kpi-badge { color:#08604c; background:#d9f2e9; }
.spc-card-note { margin:2px 0 0; font-size:13px; line-height:1.6; }
.spc-help { flex:0 0 auto; font-size:12px; color:inherit; }
.spc-help summary { cursor:pointer; list-style:none; border:1px solid #a6b3c3; border-radius:50%;
  width:20px; height:20px; line-height:18px; text-align:center; }
.spc-help summary::-webkit-details-marker { display:none; }
.spc-help[open] p { position:absolute; left:4px; top:30px; width:max(100%,190px); max-width:270px;
  z-index:5; margin:0; padding:10px; font-size:12px; line-height:1.6; border:1px solid #c7d3e1;
  max-height:min(360px,70vh); overflow-y:auto;
  border-radius:6px; background:var(--background-color,#fff); color:var(--text-color,#172b4d);
  box-shadow:0 5px 18px rgba(15,23,42,.12); }
.spc-kpi:last-child .spc-help[open] p { left:auto; right:4px; }
@media(max-width:1100px) {
  .spc-grid-stats { grid-template-columns:repeat(4,minmax(0,1fr)); }
  .spc-grid-primary { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .spc-grid-secondary { grid-template-columns:repeat(3,minmax(0,1fr)); }
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


def _index_text(value, it=None, metric='cpk'):
    if value == float('inf'):
        return '∞'
    if value == -float('inf'):
        return '−∞'
    if not _finite(value):
        return '—'
    value = float(value)
    from spc_policy import limits
    if any(value < boundary <= round(value, 3) for boundary in limits(it, metric)):
        return repr(value)
    return fmt_value(value)


def _index_card(label, value, help_text, context='', it=None, detailed_help=True):
    metric = label.lower().split('.')[0]
    badge, state = _capability_badge(value, it, metric)
    decision = (it or {}).get('capability_criteria', {}).get('indices', {}).get(metric)
    if decision and detailed_help:
        help_text += ' ' + '; '.join(dict.fromkeys(decision['reasons'] + decision.get('warnings', [])))
        if decision['required'] is not None:
            help_text += f" 조정 목표 계산값: {decision['required']:.12g} (표시는 소수 셋째 자리)."
    if context and _finite(value):
        badge += ' · ' + context
    badge = _criterion_footer(it, metric, badge)
    return _card(label, _index_text(value, it, metric), '', badge, help_text, state)


def _criterion_footer(it, metric, fallback=''):
    record = (it or {}).get('capability_criteria', {})
    if record.get('mode') != 'aiag_vda':
        return fallback
    d = record.get('indices', {}).get(metric)
    if not d:
        if metric in ('cpm', 'cpmk'):
            if not _finite((it or {}).get(metric)):
                return '미적용'
            return '목표치 고려' if metric == 'cpm' else '목표치·치우침 고려'
        return '군내산포 고려' if metric in ('cw', 'cwk') else fallback
    from spc_policy import reference_only_decision, REFERENCE_COMPARISON_LABEL
    if reference_only_decision(d):
        return REFERENCE_COMPARISON_LABEL
    state = {'hold':'계산 조건 확인', 'below':'미달', 'conditional':'조건부 충족', 'met':'충족'}[d['status']]
    target = f"조정 목표값 ≥ {d['required']:.3f}" if d['required'] is not None else '목표값 계산 불가'
    return f"N={record['sample_n']:,} · {target} · {state}"


def _card(label, value, subtitle, badge, help_text, state="neutral", status_value=False, metric_name=None):
    value_cls = "spc-kpi-value" + (" spc-value-status" if status_value else "")
    return (
        f'<article class="spc-kpi spc-state-{state}" data-metric="{escape(metric_name or label, quote=True)}">'
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


def beginner_reference_label(it, metric, value):
    """Keep a numerical performance comparison separate from the study decision."""
    if not metric.startswith('pp') or not _finite(value):
        return ''
    from spc_policy import limits, reference_only_decision, REFERENCE_COMPARISON_LABEL
    criteria = it.get('capability_criteria', {})
    decision = criteria.get('indices', {}).get(metric, {})
    if reference_only_decision(decision):
        return REFERENCE_COMPARISON_LABEL
    if it.get('capability_status') == '적용 가능' and decision.get('status') != 'hold':
        return ''
    if criteria.get('mode') == 'aiag_vda' and decision.get('required') is None:
        return '관측 성능 참고값 · 판정 보류'
    _, required = limits(it, metric)
    comparison = '≥' if float(value) >= required else '<'
    return f'관측값 {comparison} 목표 {required:.2f} · 판정 보류'


def beginner_capability_cards_html(it):
    """Show one eligible pair using the same decisions as detailed results."""
    explanations = {
        'cp': 'Cp: 규격 폭에 비해 측정값의 퍼짐이 얼마나 작은지 봅니다. 적용 조건을 충족할 때 표시합니다.',
        'cpk': 'Cpk: 측정값의 퍼짐과 규격 한쪽으로 치우친 정도를 함께 봅니다. 적용 조건을 충족할 때 표시합니다.',
        'pp': 'Pp: 이번 분석 데이터 전체의 퍼짐을 규격 폭과 비교한 참고 지수입니다.',
        'ppk': 'Ppk: 이번 분석 데이터 전체의 퍼짐과 치우침을 함께 보는 참고 지수입니다.',
    }
    cards = []
    pair = screening_index_pair(it)
    keys = (pair['width_metric'], pair['shifted_metric']) if pair['width_metric'] else ('pp', 'ppk')
    method = '.' + it.get('capability_method', 'G') if it.get('distribution_mode') == 'nonnormal' or it.get('box_cox_applied') else ''
    has_spec = any(_finite(it.get(key)) for key in ('original_usl', 'original_lsl'))
    for key in keys:
        help_text = explanations[key]
        label = key.capitalize() + method
        value = it.get(key) if pair['width_metric'] else None
        if not has_spec:
            cards.append(_card(label, '—', '', '규격 미입력', help_text, 'neutral'))
        elif not _finite(value):
            cards.append(_card(label, '—', '', '양측 규격 필요' if key in ('cp', 'pp') else '규격·계산 조건 확인', help_text, 'neutral'))
        else:
            reference = beginner_reference_label(it, key, value)
            if reference:
                cards.append(_card(label, _index_text(value, it, key), '', reference, help_text, 'hold'))
            else:
                cards.append(_index_card(label, value, help_text, context='전체산포 고려' if key.startswith('pp') else '', it=it, detailed_help=False))
    return (METRIC_CARD_CSS
            + '<style>.spc-grid-beginner{grid-template-columns:repeat(2,minmax(0,1fr));}'
              '@media(max-width:480px){.spc-grid-beginner{grid-template-columns:1fr;}}</style>'
            + '<div class="spc-metrics">'
            + _group('능력 · 성능 핵심 지수', pair['state'], cards, layout='beginner')
            + '</div>')


def process_metric_cards_html(it):
    """Render existing result values; eligibility and calculations stay in spc_core."""
    pair = screening_index_pair(it)
    reasons = "; ".join(it.get('capability_reasons', []))
    performance_note = it.get('performance_note', '전체 산포의 정규모델 가정 참고값입니다.')
    within_note = it.get('within_capability_note', '재분석이 필요합니다.')
    nonnormal = it.get('distribution_mode') == 'nonnormal'
    mr_reference = it.get('within_estimator') == 'moving_range' and not it.get('box_cox_applied')
    method = '.' + it.get('capability_method', 'G') if nonnormal or it.get('box_cox_applied') else ''
    def index_card(*args, **kwargs):
        return _index_card(*args, it=it, **kwargs)
    kind = str(it.get('dispersion_kind', 'R'))
    stats = [
        _card('평균 μ', fmt_value(it.get('mu')), '', '', '분석 데이터의 평균입니다.'),
        _card('σ 전체', fmt_value(it.get('std_all')), '', '', '전체 분석 데이터의 표본표준편차입니다.'),
        _card('σ MR' if mr_reference else 'σ 군내', fmt_value(it.get('std_within')), '', '',
              '원본에서 인접한 유효 관측값의 MR̄/1.128 추정값입니다. MR 기반 진단에 사용합니다.' if mr_reference else '부분군 내 산포 추정값입니다.'),
        _card('X-UCL', fmt_value(it.get('ucl')), '', '', 'X/Xbar 관리상한입니다. 고객·도면 규격과 구분하세요.'),
        _card('X-LCL', fmt_value(it.get('lcl')), '', '', 'X/Xbar 관리하한입니다. 고객·도면 규격과 구분하세요.'),
        _card(f'{kind}-UCL', fmt_value(it.get('r_ucl')), '', '', f'{kind} 산포 관리상한입니다.'),
    ]
    if (nonnormal and int(it.get('n', 0)) > 1) or (not nonnormal and not (kind == 'R' and int(it.get('n', 0)) <= 6)):
        stats.append(_card(f'{kind}-LCL', fmt_value(it.get('r_lcl')), '', '', f'{kind} 산포 관리하한입니다.'))
    groups = [_group('측정 통계 · 관리한계', '', stats, layout='stats')]
    groups.append(normality_cards_html(it))
    primary = []
    keys = (pair['width_metric'], pair['shifted_metric']) if pair['width_metric'] else ('pp', 'ppk')
    for key in keys:
        label = key.capitalize()
        value = it.get(key) if pair['width_metric'] else None
        if not _finite(value) and value not in (float('inf'), -float('inf')):
            primary.append(_card(label+method, '—', '', '미적용', reasons or '양측 규격이 필요합니다.'))
        else:
            primary.append(index_card(label+method, value, performance_note,
                                      '전체산포 고려' if key.startswith('pp') else ''))
    primary.extend([
        _card('Cw (MR 참고)', _index_text(it.get('cw')), '', 'MR 기반 · 진단용', within_note, metric_name='Cw') if mr_reference else index_card('Cw', it.get('cw'), within_note, '군내산포 고려'),
        _card('Cwk (MR 참고)', _index_text(it.get('cwk')), '', 'MR 기반 · 진단용', within_note, metric_name='Cwk') if mr_reference else index_card('Cwk', it.get('cwk'), within_note, '군내산포 고려'),
    ])
    groups.append(_group('능력 · 성능 지표', pair['state'] + (' · ' + reasons if reasons else '')
                         + (' · Cw·Cwk: MR 기반 참고' if mr_reference else ' · Cw·Cwk: 군내산포 고려'),
                         primary, layout='primary'))
    secondary = []
    for key, label in [('cpm','Cpm'),('cpmk','Cpmk')]:
        value = it.get(key)
        target_context = '목표치 고려' if key == 'cpm' else '목표치·치우침 고려'
        target_help = ('전체 산포와 평균의 목표치 편차를 함께 반영하는 지수입니다.' if key == 'cpm' else
                       '전체 산포와 목표치 편차에 더해 평균이 가까운 규격 한쪽으로 치우친 정도를 반영하는 지수입니다.')
        secondary.append(index_card(label, value, within_note if nonnormal else
                                     target_help, target_context))
    secondary.extend([
        _card('|Cwk/Ppk|', fmt_value(it.get('within_overall_ratio')), '', '',
              '군내/전체 지수 비율입니다. 이 값만으로 안정성을 확정하지 않습니다. Ppk=0 또는 Cwk 미계산 시 표시하지 않습니다.'),
        _card('PPM(MR 참고)' if mr_reference else 'PPM(Cwk)', _ppm(it.get('ppm_within')), '', 'MR 정규모형 참고' if mr_reference else '미적용' if nonnormal else '', within_note + ' 산포 추정값의 정규모형 가정 예상 PPM이며 선택 분포의 PPM과 구분합니다.', metric_name='PPM(Cwk)'),
        _card('PPM(적합분포)' if nonnormal else 'PPM(전체)', _ppm(it.get('ppm_ppk')), '', '', performance_note),
    ])
    groups.append(_group('목표치 · 변동 비교 · 예상 PPM',
                         'PPM은 선택 분포 모형의 참고값' if nonnormal else 'PPM은 정규모델 가정 참고값',
                         secondary, layout='secondary'))
    if nonnormal:
        groups.append('<p class="spc-card-note">' + escape(it.get('chart_mode', '') + ' · ' + it.get('pearson_rules_note', '')) + '</p>')
    if mr_reference:
        groups.append('<p class="spc-card-note">'+escape(within_note)+'</p>')
    return METRIC_CARD_CSS + '<div class="spc-metrics">' + ''.join(groups) + '</div>'



def _capability_badge(value, it=None, metric='cpk'):
    record = (it or {}).get('capability_criteria', {})
    if record.get('mode') == 'aiag_vda':
        from spc_policy import CRITERIA_STATUS
        decision = record.get('indices', {}).get(metric)
        if not decision:
            return '자동 평가 미적용', 'neutral'
        status = decision['status']
        required = decision['required']
        text = CRITERIA_STATUS[status] + (f' · 요구 ≥ {required:.2f}' if required is not None else '')
        return text, {'hold':'hold', 'below':'bad', 'conditional':'warn', 'met':'good'}[status]
    if not _finite(value):
        return ('모형 경계값' if value in (float('inf'), -float('inf')) else '미적용'), 'neutral'
    from spc_policy import limits
    warning, required = limits(it, metric)
    if float(value) < warning:
        return f'기준미달 · < {warning:.2f}', 'bad'
    if float(value) < required:
        return f'주의 · < {required:.2f}', 'warn'
    return f'충족 · ≥ {required:.2f}', 'good'


def normality_cards_html(it):
    from spc_normality import STATUS_LABELS, pvalue_text, shape_text, diagnostic_hint, SHAPE_GUIDANCE
    record=it.get('normality_diagnostics',{}).get('individual')
    if not record:
        cards=[_card(label,'—','','상세 분석 필요','상세 분석 후 검정 대상과 함께 표시합니다.')
               for label in ('정규성 p값','왜도','첨도')]
        note='상세 분석에서 정규성·분포 형태 지표를 확인할 수 있습니다.'
    else:
        status=record['status']
        method=record.get('test') or '검정 불가'
        badge=STATUS_LABELS[status]+' · '+method
        note=(f"{record['scope']} · {record['scale']} · 전체 N={record['total_n']:,}, 검정 N={record['test_n']:,}. "
              +diagnostic_hint(record))
        cards=[
            _card('정규성 p값',pvalue_text(record['pvalue']),'',badge,
                  f"유의수준 {record['alpha']:.2f}. AD p값은 유한 표본 보정을 적용한 근사값입니다. "
                  +note,'warn' if status=='rejected' else 'neutral'),
            _card('왜도',shape_text(record.get('skewness')),'','|왜도| ≤ 2: 정규성 참고 기준',
                  '분석 대상 전체 개별값의 보정 표본 왜도 절댓값입니다. 꼬리 방향은 히스토그램에서 확인할 수 있습니다. '+SHAPE_GUIDANCE),
            _card('첨도',shape_text(record.get('kurtosis')),'','|첨도| ≤ 7: 정규성 참고 기준',
                  '첨도(Pearson, 편향 보정)는 초과첨도 + 3이며 정규분포 기준 3입니다. 절댓값을 표시합니다. '+SHAPE_GUIDANCE),
        ]
    return ('<style>.spc-grid-diagnostics{grid-template-columns:repeat(3,minmax(0,1fr));}'
            '@media(max-width:640px){.spc-grid-diagnostics{grid-template-columns:1fr;}}</style>'
            +_group('분포 진단 · 개별 측정값','원단위 · 분석 포함 데이터',cards,layout='diagnostics'))
