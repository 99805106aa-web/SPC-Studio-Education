"""Shared assessment criteria, documented exclusions and sequence review.

These checks support a study; they do not certify sampling or measurement systems.
"""
import hashlib
import json
import math
import re
from datetime import datetime
import numpy as np
from scipy import stats, special

CHARACTERISTIC_CLASSES = {'critical': 'Critical(치명)', 'major': 'Major(주요)',
                          'minor': 'Minor(경미)', 'others': 'Others(기타)'}
STUDY_STAGES = {'preliminary': '예비 평가', 'final': '최종 평가'}
AUTO_CONFIDENCES = (.95, .99, .999, .9999)
# User-supplied AIAG/VDA example targets, reference sample size 125.
TARGETS = {'critical': ((2.00, 1.67), (1.67, 1.67)),
           'major': ((1.67, 1.33), (1.33, 1.33)),
           'minor': ((1.33, 1.00), (1.00, 1.00)),
           'others': ((1.00, 1.00), (1.00, 1.00))}
AUTO_NOTICE = 'AIAG·VDA 첨부 표·식의 예시 목표값 · 고객 합의 필요 · 출하/공정 승인을 의미하지 않습니다.'


def _classification(value):
    cls = value.get('characteristic_class', 'major')
    stage = value.get('study_stage', 'final')
    if cls not in CHARACTERISTIC_CLASSES or stage not in STUDY_STAGES:
        raise ValueError('특성 중요도 또는 평가 단계가 올바르지 않습니다.')
    return dict(characteristic_class=cls, study_stage=stage)


def normalize_assessment(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('평가 조건은 객체여야 합니다.')
    warning = float(value.get('warning_limit', 1.0))
    required = float(value.get('required_limit', 1.33))
    confidence = float(value.get('confidence', .95))
    if not (math.isfinite(warning) and math.isfinite(required) and 0 < warning <= required):
        raise ValueError('능력 기준은 0 < 개선 경계 ≤ 고객 요구값이어야 합니다.')
    if not .8 <= confidence <= .9999:
        raise ValueError('신뢰수준은 80%~99.99% 범위여야 합니다.')
    mode = value.get('criteria_mode', 'manual')
    if mode not in ('manual', 'aiag_vda'):
        raise ValueError('평가 모드는 manual 또는 aiag_vda여야 합니다.')
    if mode == 'aiag_vda' and confidence not in AUTO_CONFIDENCES:
        raise ValueError('자동 기준 신뢰수준은 95%, 99%, 99.9%, 99.99% 중 선택하세요.')
    classification = _classification(value)
    overrides = value.get('variable_overrides', {})
    if not isinstance(overrides, dict) or any(not isinstance(v, dict) for v in overrides.values()):
        raise ValueError('항목별 평가 설정은 항목 이름별 객체여야 합니다.')
    overrides = {str(k): _classification(dict(classification, **v)) for k, v in overrides.items()}
    approvals = value.get('outlier_approvals', {})
    if not isinstance(approvals, dict):
        raise ValueError('제외 승인 기록은 변수별 객체여야 합니다.')
    return dict(warning_limit=warning, required_limit=required, confidence=confidence,
                criteria_mode=mode, **classification, variable_overrides=overrides,
                study_confirmed=value.get('study_confirmed') is True,
                study_note=str(value.get('study_note', '')).strip(),
                reviewer=str(value.get('reviewer', '')).strip(), outlier_approvals=approvals)


def limits(it=None, metric='cpk'):
    record = (it or {}).get('capability_criteria', {})
    key = str(metric).lower().split('.')[0].split('_')[0]
    if record.get('mode') == 'aiag_vda' and key in record.get('indices', {}):
        required = record['indices'][key]['required']
        if required is not None:
            return required, required
    cfg = (it or {}).get('assessment_settings', {})
    return float(cfg.get('warning_limit', 1.0)), float(cfg.get('required_limit', 1.33))


def band(value, it=None, metric='cpk'):
    if value is None or not np.isfinite(value):
        return 'na'
    record = (it or {}).get('capability_criteria', {})
    if record.get('mode') == 'aiag_vda':
        key = str(metric).lower().split('.')[0].split('_')[0]
        decision = record.get('indices', {}).get(key)
        if not decision or decision['status'] == 'hold':
            return 'na'
    warning, required = limits(it, metric)
    return 'bad' if value < warning else 'warn' if value < required else 'good'


def adjusted_target(base, count, confidence, centered=False):
    """User-supplied formula; retain full precision for comparisons."""
    if count < 2:
        return None
    if count >= 125:
        return float(base)
    v = count - 1
    factor = math.sqrt(stats.chi2.ppf(1-confidence, 124)/124 * v/stats.chi2.ppf(1-confidence, v))
    if centered:
        factor *= (1+1/(2*count))/(1+1/(2*125))
    return float(base*factor)


def build_capability_criteria(it, cfg, blockers=()):
    """A single auditable record consumed by cards, summaries, exports and API."""
    if cfg['criteria_mode'] == 'manual':
        return dict(mode='manual', indices={}, note='고객 지정 수동 기준')
    selection = dict(_classification(cfg), **cfg['variable_overrides'].get(str(it['column']), {}))
    count = len(it['series'])
    normal = (it.get('distribution_mode') == 'normal' and not it.get('box_cox_applied')
              and it.get('detail_level') == 'full')
    bases = TARGETS[selection['characteristic_class']][selection['study_stage'] == 'final']
    warnings = list(blockers)
    if not normal:
        warnings.append('비정규·변환 자료는 첨부 정규모형 보정식 미적용')
    elif it.get('normality_diagnostics', {}).get('individual', {}).get('status') != 'not_rejected':
        warnings.append('정규성 확인 필요: 조정 목표값은 정규모형 가정 참고값')
    indices = {}
    for key in ('cp', 'cpk', 'pp', 'ppk'):
        base = bases[key.endswith('k')]
        required = adjusted_target(base, count, cfg['confidence'], key.endswith('k')) if normal else base
        reasons = []
        notes = list(warnings)
        if key.startswith('cp') and selection['study_stage'] == 'preliminary':
            notes.append('예비 평가는 Pp·Ppk 기준; Cp·Cpk 비교는 참고')
        value = it.get(key)
        value = float(value) if value is not None and np.isfinite(value) else None
        if value is None:
            reasons.append('해당 지수 미계산 또는 규격/적용 조건 미충족')
        if not normal:
            reasons.append('해당 모형의 표본 수 보정식 미지원')
        status = 'hold' if reasons or required is None else 'below' if value < required else 'met'
        indices[key] = dict(base=base, required=required, value=value, status=status, reasons=reasons, warnings=notes)
    return dict(mode='aiag_vda', **selection, sample_n=count, reference_n=125,
                confidence=cfg['confidence'], degrees_of_freedom=max(0, count-1),
                correction_applied=normal and 2 <= count < 125, indices=indices, warnings=warnings,
                rounding='소수 셋째 자리 표시 · 판정은 반올림 전 값',
                note=AUTO_NOTICE, source='사용자 제공 AIAG·VDA 표 8-3~8-5 및 표본 크기 보정식')


CRITERIA_STATUS = {'hold': '판정 보류', 'below': '기준 미달', 'conditional': '소표본 조건부 충족', 'met': '기준 충족'}
REFERENCE_COMPARISON_LABEL = '참고값 표시 · 자동 기준 비교 미지원'


def reference_only_decision(decision):
    return (decision.get('status') == 'hold' and decision.get('value') is not None
            and '해당 모형의 표본 수 보정식 미지원' in decision.get('reasons', ()))


def criteria_status_text(record):
    selected = record.get('evaluated_indices', ())
    if record.get('status') == 'hold' and selected and all(
            reference_only_decision(record['indices'].get(key, {})) for key in selected):
        return REFERENCE_COMPARISON_LABEL
    return CRITERIA_STATUS[record.get('status', 'hold')]


def criteria_description(it):
    r = it.get('capability_criteria', {})
    if r.get('mode') != 'aiag_vda':
        warning, required = limits(it)
        return f'고객 지정 기준: 요구값 {required:.2f} / 개선 경계 {warning:.2f}'
    parts = [f"AIAG·VDA 자동 · {CHARACTERISTIC_CLASSES[r['characteristic_class']]} · {STUDY_STAGES[r['study_stage']]} · "
             f"유효 N={r['sample_n']} / 기준 N=125 · 신뢰수준 {r['confidence']:.2%}"]
    for key, d in r['indices'].items():
        if reference_only_decision(d):
            parts.append(f'{key.capitalize()} · {REFERENCE_COMPARISON_LABEL}')
            continue
        target = '계산 불가' if d['required'] is None else f"{d['required']:.3f}"
        parts.append(f"{key.capitalize()}: 기본 {d['base']:.2f} → 요구 {target} · {CRITERIA_STATUS[d['status']]}"
                     + (' ('+'; '.join(d['reasons'])+')' if d['reasons'] else ''))
    if r.get('warnings'):
        parts.append('별도 확인: '+'; '.join(dict.fromkeys(r['warnings'])))
    return ' | '.join(parts) + ' · ' + r['note']


def format_criteria(text, warning, required):
    return re.sub(r'1\.33|1\.00',lambda m: format(required if m[0]=='1.33' else warning,'.2f'),text)


def exclusion_digest(raw, n, method, phase, baseline):
    values = np.asarray(raw, dtype='<f8').copy()
    values[np.isnan(values)] = np.nan  # Canonical NaN payload.
    info = json.dumps([int(n), method, phase, int(baseline)])
    return hashlib.sha256(values.tobytes()+info.encode()).hexdigest()


def approved_mask(info, record, source_indices):
    """Only current, documented, explicitly selected candidate rows may be excluded."""
    selected = set()
    if record:
        if not isinstance(record, dict):
            raise ValueError('제외 승인 기록 형식이 올바르지 않습니다.')
        if record.get('digest') != info['digest']:
            info['approval_expired'] = True
            return np.zeros(len(source_indices), dtype=bool)
        if not all(str(record.get(key, '')).strip() for key in ('reason', 'reviewer', 'reviewed_at')):
            raise ValueError('제외에는 사유·확인자·확인 시점이 필요합니다.')
        try:
            reviewed_at = datetime.fromisoformat(record['reviewed_at'])
            if reviewed_at.tzinfo is None:
                raise ValueError('timezone missing')
        except (TypeError, ValueError):
            raise ValueError('제외 확인 시점은 시간대가 있는 ISO 날짜·시간이어야 합니다.')
        indices = record.get('indices', [])
        if not isinstance(indices, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in indices):
            raise ValueError('제외 승인 행 번호는 정수 목록이어야 합니다.')
        selected = set(indices)
        if not selected.issubset({item['index'] for item in info['items']}):
            raise ValueError('현재 이상치 후보에 없는 행을 제외할 수 없습니다.')
    return np.isin(np.asarray(source_indices)+1, list(selected))


def sequence_review(it):
    """Additional review flags, distinct from normal sigma-zone tests.

    Strict 6-point trends and 9 points on one side of the fitted median are
    application review rules. They do not prove independence when absent.
    Original missing/excluded subgroup gaps break a sequence.
    """
    x = np.asarray(it['means'], dtype=float)
    n = int(it['n'])
    src = np.asarray(it['source_indices'], dtype=int)
    ids = src if n == 1 else src.reshape(-1, n)[:, 0]//n
    median = float(it['xbarbar'])
    if it.get('chart_model') and it['chart_model'].get('eligible'):
        model = it['chart_model']
        median = float(getattr(stats, model['family'])(*model['params']).ppf(.5))
    hits = {}
    for name, width in [('trend', 6), ('median_run', 9)]:
        if len(x) < width:
            hits[name] = []
            continue
        windows = np.lib.stride_tricks.sliding_window_view(x, width)
        spans = np.lib.stride_tricks.sliding_window_view(ids, width)
        contiguous = np.all(np.diff(spans, axis=1) == 1, axis=1)
        if name == 'trend':
            diff = np.diff(windows, axis=1)
            bad = np.all(diff > 0, axis=1) | np.all(diff < 0, axis=1)
        else:
            bad = np.all(windows > median, axis=1) | np.all(windows < median, axis=1)
        hits[name] = (np.flatnonzero(bad & contiguous)+width-1).tolist()
    indices = sorted(set(hits['trend']) | set(hits['median_run']))
    return dict(**hits, indices=indices, count=len(indices),
                baseline_count=sum(i < it['baseline_display_count'] for i in indices),
                note='추가 시간 패턴 검토: 6점 단조 추세 / 적합 중앙값 한쪽 9점. 앱 검토 규칙이며 독립성 입증은 아닙니다.')


def normal_intervals(it, confidence):
    """Parametric percentile intervals using independent normal sufficient statistics.

    Assumes independent normal observations. Does not apply to Box–Cox or skew fits.
    """
    count = len(it['series'])
    sd = float(it['std_all'])
    if count < 3 or not np.isfinite(sd) or sd <= 0:
        return None
    rng = np.random.default_rng(20261117)
    means = rng.normal(it['mu'], sd/np.sqrt(count), 20000)
    deviations = sd*np.sqrt(rng.chisquare(count-1, 20000)/(count-1))
    sides = []
    if np.isfinite(it['usl']):
        sides.append((it['usl']-means)/(3*deviations))
    if np.isfinite(it['lsl']):
        sides.append((means-it['lsl'])/(3*deviations))
    if not sides:
        return None
    samples = np.minimum.reduce(sides)
    return dict(confidence=confidence, ppk_lower=float(np.quantile(samples, 1-confidence)),
                ppk_interval=np.quantile(samples, [(1-confidence)/2,(1+confidence)/2]).tolist(),
                method='정규 독립 관측 가정 · 모수 bootstrap percentile 20,000회 · 하한은 단측',
                samples=20000)


def finalize_assessment(it, settings=None):
    cfg = normalize_assessment(settings)
    it['assessment_settings'] = cfg
    auto = cfg['criteria_mode'] == 'aiag_vda'
    blockers = list(it.get('capability_reasons', [])) if it['capability_status'] != '적용 가능' else []
    it['sequence_review'] = sequence_review(it)
    reasons = []
    if it['sequence_review']['count']:
        reasons.append('시간 패턴 검토 필요: 추세 또는 중앙값 기준 런')
        if it['control_phase'] == 'phase2' and it['sequence_review']['baseline_count']:
            it['baseline_status'] = '부적합'
            it['baseline_reasons'] = list(it.get('baseline_reasons', []))+['기준구간 시간 패턴: 관리한계 잠정값']
    groups = len(it['means'])
    if not auto and (len(it['series']) < 125 or groups < 25):
        reasons.append('예비/참고 평가: 125개 부품·25개 부분군 기본 조사 수량 미충족')
    if not (cfg['study_confirmed'] and cfg['study_note'] and cfg['reviewer']):
        note = '조사 조건 미확인: 측정 적합성·대표 기간·5M·합리적 샘플링 근거 필요'
        if auto:
            blockers.append(note)
        else:
            reasons.append(note)
    candidates = it.get('outlier_info', {})
    if candidates.get('candidate_count', 0) and not candidates.get('reviewed'):
        reasons.append('이상치 후보 미확인: 원인 검토 후 유효값 유지 또는 제외 승인')
    blockers += [r for r in reasons if not r.startswith('예비/참고 평가:')]
    if it.get('spec_violation_count', 0):
        blockers.append('원본 규격이탈 확인 필요')
    it['assessment_reasons'] = reasons
    it['capability_confidence'] = (normal_intervals(it, cfg['confidence'])
        if it.get('distribution_mode') == 'normal' and not it.get('box_cox_applied')
        and '만족' in it.get('norm_msg', '') and it.get('detail_level') == 'full' else None)
    if reasons:
        if it['capability_status'] == '적용 가능':
            it['capability_status'] = '관리상태 확인' if it['sequence_review']['count'] else '예비/참고 평가'
        it['capability_reasons'] = list(it.get('capability_reasons', []))+reasons
        for key in ('cp','cpk','cp_g','cpk_g','cp_z','cpk_z','ppm_cpk'):
            it[key] = np.nan
        for method in it.get('capability_methods', {}).values():
            method['cp'] = method['cpk'] = np.nan
        it['reliability_status'] = '검토필요'
        it['reliability_reasons'] = list(it.get('reliability_reasons', []))+reasons
    interval = it['capability_confidence']
    required = cfg['required_limit']
    it['study_conclusion'] = ('검토 보류: '+ '; '.join(it.get('capability_reasons', []))
        if it['capability_status'] != '적용 가능' else
        '지수 추정치 평가: 신뢰하한 미지원, 별도 승인 검토 필요' if interval is None else
        '고객 요구값의 단측 신뢰하한 충족(기록한 조사 가정 내)' if interval['ppk_lower'] >= required else
        '단측 신뢰하한이 고객 요구값에 미달: 승인 검토 보류')
    it['capability_criteria'] = build_capability_criteria(it, cfg, blockers)
    if auto:
        record = it['capability_criteria']
        prefix = 'cp' if record['study_stage'] == 'final' and it['capability_status'] == '적용 가능' else 'pp'
        selected = [key for key in (prefix, prefix+'k') if np.isfinite(it.get(key, np.nan))]
        states = [record['indices'][key]['status'] for key in selected]
        overall = ('hold' if not states or 'hold' in states else 'below' if 'below' in states
                   else 'conditional' if 'conditional' in states else 'met')
        record.update(evaluated_indices=selected, status=overall)
        it['study_conclusion'] = ('AIAG·VDA '+criteria_status_text(record)+' · '+ '/'.join(k.capitalize() for k in selected)
                                  + ' · '+AUTO_NOTICE)
    it['performance_note'] += ' 소표본·미확인 조사 조건의 값은 예비/참고이며 출하·공정 승인과 구분합니다.'
    it['reliability_reason_text'] = ', '.join(dict.fromkeys(it.get('reliability_reasons', [])))
    return it


def boxcox_capability(it):
    """G uses inverse-transformed quantiles; Z uses transformed tail probabilities."""
    from spc_nonnormal import capability_indices
    transformed = stats.norm(it['mu'], it['std_all'])
    result = capability_indices(transformed, it['usl'], it['lsl'])
    yq = transformed.ppf([.00135, .5, .99865])
    xq = special.inv_boxcox(yq, it['box_cox_lambda'])-it['box_cox_shift']
    sides = []
    upper, lower = it['original_usl'], it['original_lsl']
    usable = np.isfinite(xq).all() and np.all(np.diff(xq)>0)
    if usable:
        if np.isfinite(upper):
            sides.append((upper-xq[1])/(xq[2]-xq[1]))
        if np.isfinite(lower):
            sides.append((xq[1]-lower)/(xq[1]-xq[0]))
    bilateral = np.isfinite(upper) and np.isfinite(lower)
    result['methods']['G'] = dict(pp=(upper-lower)/(xq[2]-xq[0]) if usable and bilateral else np.nan,
                                 ppk=min(sides) if sides else np.nan)
    eligible = it['capability_status'] == '적용 가능' and usable
    for method, values in result['methods'].items():
        values['cp'] = values['pp'] if eligible else np.nan
        values['cpk'] = values['ppk'] if eligible else np.nan
        for name, value in values.items():
            it[f'{name}_{method.lower()}'] = value
    it['capability_methods'] = result['methods']
    it['capability_method'] = 'G'
    it.update(result['methods']['G'])
    it['capability_quantiles'] = dict(zip(('low','median','high'),map(float,xq)))
    it['capability_quantile_scale'] = '원단위 · Box–Cox 역변환'
    it['performance_note'] = ('Box–Cox: G는 원단위 역변환 분위수, Z는 변환 정규모형의 규격 초과확률로 계산합니다. '
                              '관리도·정규성 그림과 통계는 변환 단위이며 원본 규격과 구분하세요.')
    for name in ('cw','cwk','cpm','cpmk','ppm_within','within_overall_ratio'):
        it[name] = np.nan
    it['within_capability_note'] = '비선형 변환의 군내·목표치 지수는 미적용입니다.'
    if not usable:
        it['capability_status'] = '분포 검토 필요'
        it['capability_reasons'].append('Box–Cox 역변환 꼬리 분위수의 유효 범위 초과')
    return it
