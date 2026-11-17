"""Validated distribution models, Pearson limits and G/Z process indices.

No Streamlit dependency. Fitting/verification is shared by desktop and API callers.
"""
from collections import OrderedDict
import hashlib
import json
import threading
import warnings

import numpy as np
from scipy import special, stats

MODEL_VERSION = '2026.11.17.3'
BOOTSTRAP_SAMPLES = 999
GOF_MAX = 2000
SEED = 20261117
ALPHA = float(stats.norm.cdf(-3))
CHART_TAIL = .00135  # AIAG–VDA 9.3.5.2: 0.135% and 99.865%.
FAMILIES = {
    'norm': '정규', 'pearson3': 'Pearson III', 'lognorm': '로그정규',
    'gamma': '감마', 'weibull_min': 'Weibull', 'expon': '지수',
    'foldnorm': '접힌 정규', 'rayleigh': 'Rayleigh', 'rice': 'Rice',
}
POSITIVE = set(FAMILIES) - {'norm', 'pearson3'}
_fit_cache = OrderedDict()
_lock = threading.Lock()


def _get(cache, key):
    with _lock:
        if key not in cache:
            return None
        cache.move_to_end(key)
        return dict(cache[key])


def _put(cache, key, value, size=24):
    with _lock:
        cache[key] = dict(value)
        cache.move_to_end(key)
        while len(cache) > size:
            cache.popitem(last=False)
    return value


def validate_settings(config):
    if not isinstance(config, dict):
        raise ValueError('분포 설정은 객체여야 합니다.')
    mode = config.get('mode', 'normal')
    if mode not in ('normal', 'nonnormal'):
        raise ValueError('분포 모드는 normal 또는 nonnormal이어야 합니다.')
    family = config.get('family')
    if family is not None and family not in FAMILIES:
        raise ValueError('지원하지 않는 분포입니다.')
    chart_family = config.get('chart_family', family)
    if chart_family is not None and chart_family not in FAMILIES:
        raise ValueError('지원하지 않는 위치 관리도 분포입니다.')
    method = config.get('method', 'G')
    if method not in ('G', 'Z'):
        raise ValueError('계산 방법은 G 또는 Z여야 합니다.')
    lower = config.get('natural_lower')
    if lower is not None and (isinstance(lower, bool) or not np.isfinite(float(lower))):
        raise ValueError('자연하한은 유한한 숫자여야 합니다.')
    return dict(mode=mode, family=family, chart_family=chart_family, method=method,
                natural_lower=float(lower) if lower is not None else None,
                confirmed_digest=str(config.get('confirmed_digest', '')),
                performance_digest=str(config.get('performance_digest', '')))


def normalize_settings_map(settings):
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise ValueError('변수별 분포 설정은 객체여야 합니다.')
    return {str(key): validate_settings(value) for key, value in settings.items()}


def fingerprint(it, natural_lower=None):
    """Confirmation is bound to the data, grouping, specifications and baseline."""
    raw = np.asarray(it['raw_series'], dtype='<f8')
    indices = np.asarray(it['source_indices'], dtype=int)
    if it['control_phase'] == 'phase2':
        end = int(it['baseline_requested_count']) * int(it['n'])
        raw = raw[:end]
        indices = indices[indices < end]
    natural_lower = float(natural_lower) if natural_lower is not None else None
    info = dict(version=MODEL_VERSION, n=int(it['n']), phase=it['control_phase'],
                baseline=int(it['baseline_requested_count']), lower=natural_lower,
                usl=float(it['usl']), lsl=float(it['lsl']),
                source_indices=indices.tolist())
    return hashlib.sha256(raw.tobytes() + json.dumps(info, sort_keys=True).encode()).hexdigest()


def reference_values(it):
    values = np.asarray(it['series'], dtype=float)
    if it['control_phase'] == 'phase2':
        mask = np.asarray(it['source_indices']) < int(it['baseline_requested_count']) * int(it['n'])
        return values[mask]
    return values


def performance_fingerprint(it, natural_lower=None):
    values = np.asarray(it['raw_series'], dtype='<f8')
    natural_lower = float(natural_lower) if natural_lower is not None else None
    info = [MODEL_VERSION, int(it['n']), natural_lower, float(it['usl']), float(it['lsl'])]
    return hashlib.sha256(values.tobytes()+json.dumps(info).encode()).hexdigest()


def diagnosis_fingerprint(it, natural_lower=None):
    return fingerprint(it, natural_lower)+performance_fingerprint(it, natural_lower)


def location_reference_values(it):
    """Fit the plotted statistic, keeping original subgroup/baseline boundaries."""
    values = reference_values(it)
    return values if int(it['n']) == 1 else values.reshape(-1, int(it['n'])).mean(axis=1)


def check_location_sample(values, n):
    minimum = 50 if n == 1 else 20
    if len(values) < minimum or not np.isfinite(values).all() or np.ptp(values) <= 0:
        raise ValueError(f'위치 관리도 분포 적합에는 유효 {"개별값 50" if n == 1 else "부분군 평균 20"}개 이상과 양의 산포가 필요합니다.')


def check_sample(values, n):
    if len(values) < 50:
        raise ValueError('비정규 적합에는 유효 측정값 50개 이상이 필요합니다.')
    if n > 1 and len(values) // n < 20:
        raise ValueError('비정규 관리도에는 완전 부분군 20개 이상이 필요합니다.')
    if not np.isfinite(values).all() or np.ptp(values) <= 0:
        raise ValueError('유효한 유한 측정값과 0보다 큰 산포가 필요합니다.')


def _estimate_params(family, values, lower):
    """Estimate parameters without repeating tail/moment checks on bootstrap draws."""
    dist = getattr(stats, family)
    if lower is not None and family not in POSITIVE:
        raise ValueError('자연하한이 있는 자료에는 이 분포를 적용하지 않습니다.')
    if lower is not None and np.min(values) < lower:
        raise ValueError('자연하한보다 작은 측정값이 있습니다. 측정·자료 조건을 확인하세요.')
    kwargs = {'floc': lower} if lower is not None else {}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        params = ((float(np.mean(values)), float(np.std(values, ddof=0)))
                  if family == 'norm' else tuple(float(x) for x in dist.fit(values, **kwargs)))
    if not np.isfinite(params).all() or params[-1] <= 0:
        raise ValueError('유효한 분포 모수를 추정하지 못했습니다.')
    return params


def _fit_params(family, values, lower):
    params = _estimate_params(family, values, lower)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        frozen = getattr(stats, family)(*params)
        log_pdf = frozen.logpdf(values)
        quantiles = frozen.ppf([ALPHA, .5, 1-ALPHA])
        moments = frozen.stats(moments='mv')
    if (not np.isfinite(params).all() or params[-1] <= 0 or not np.isfinite(log_pdf).all()
            or not np.isfinite(quantiles).all() or not np.all(np.diff(quantiles) > 0)
            or not np.isfinite(moments).all()):
        raise ValueError('유한한 모수·확률밀도·꼬리 분위수를 추정하지 못했습니다.')
    return params, float(np.sum(log_pdf))


def _ad(values, frozen):
    x = np.sort(values)
    weight = 2*np.arange(1, len(x)+1)-1
    return float(-len(x) - np.mean(weight*(frozen.logcdf(x)+frozen.logsf(x[::-1]))))


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('분포 진단을 취소했습니다.')


def fit_checked(values, family, lower=None, *, cancel=None, progress=None):
    values = np.asarray(values, dtype=float)
    key = (MODEL_VERSION, family, lower, BOOTSTRAP_SAMPLES,
           hashlib.sha256(values.astype('<f8').tobytes()).hexdigest())
    _cancelled(cancel)
    cached = _get(_fit_cache, key)
    if cached is not None:
        return cached
    record = dict(family=family, name=FAMILIES[family], eligible=False, fit_n=len(values),
                  test_n=min(len(values), GOF_MAX), bootstrap_samples=BOOTSTRAP_SAMPLES,
                  seed=SEED, natural_lower=lower)
    try:
        params, ll = _fit_params(family, values, lower)
        # Count only estimated parameters: a known natural boundary is fixed.
        k = len(params) - int(lower is not None)
        aicc = 2*k-2*ll + 2*k*(k+1)/(len(values)-k-1)
        rng = np.random.default_rng(SEED)
        sample = values if len(values) <= GOF_MAX else values[np.sort(rng.choice(len(values), GOF_MAX, replace=False))]
        sample_params = params if len(values) <= GOF_MAX else _fit_params(family, sample, lower)[0]
        frozen = getattr(stats, family)(*sample_params)
        observed = _ad(sample, frozen)
        if not np.isfinite(observed):
            raise ValueError('경계·0값의 적합도 검증 불가: 측정 분해능 또는 중도절단 조건을 확인하세요.')
        extreme = 0
        for index in range(BOOTSTRAP_SAMPLES):
            _cancelled(cancel)
            draw = frozen.rvs(size=len(sample), random_state=rng)
            reestimated = _estimate_params(family, draw, lower)
            statistic = _ad(draw, getattr(stats, family)(*reestimated))
            if not np.isfinite(statistic):
                raise ValueError('재표본 적합도 검증 중 수치적 실패가 발생했습니다.')
            extreme += statistic >= observed
            if progress is not None and index % 10 == 0:
                progress(f'{FAMILIES[family]} 검증 {index+1}/{BOOTSTRAP_SAMPLES}')
        pvalue = (extreme+1)/(BOOTSTRAP_SAMPLES+1)
        record.update(params=list(params), aicc=float(aicc), ad=observed, pvalue=float(pvalue),
                      eligible=pvalue >= .05, reason='적합도 기준 통과' if pvalue >= .05 else 'AD 적합도 기준 미충족')
    except InterruptedError:
        raise
    except (ValueError, RuntimeError, FloatingPointError, OverflowError) as exc:
        record['reason'] = str(exc)
    return _put(_fit_cache, key, record)


def diagnose_distribution(it, natural_lower=None, *, cancel=None, progress=None, max_candidates=None):
    """Optionally validate AICc-ranked batches; every selectable model gets 999 refits.

    Screening is not a goodness-of-fit rejection. Untested candidates stay visible
    and unavailable for selection. If a batch has no accepted model, expand it.
    """
    if max_candidates is not None and (isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int) or max_candidates < 1):
        raise ValueError('빠른 진단 후보 수는 1 이상의 정수여야 합니다.')
    if it.get('preprocess_applied') or it.get('box_cox_applied'):
        raise ValueError('비정규 분석은 자동 이상치 제거·Box-Cox를 끈 뒤 원자료로 다시 분석하세요.')
    lower = validate_settings({'natural_lower': natural_lower})['natural_lower']
    values = np.asarray(it['series'], dtype=float)
    check_sample(reference_values(it), int(it['n']))
    location = location_reference_values(it)
    check_location_sample(location, int(it['n']))
    candidates = _diagnose_values(values, lower, cancel=cancel, progress=progress, max_candidates=max_candidates)
    if progress is not None:
        progress('위치 관리도 분포 진단: 부분군 평균' if int(it['n']) > 1 else '위치 관리도 분포 진단: 개별값')
    chart_candidates = (candidates if np.array_equal(values, location) else
                        _diagnose_values(location, lower, cancel=cancel, progress=progress, max_candidates=max_candidates))
    eligible = [row for row in candidates if row['eligible']]
    chart_eligible = [row for row in chart_candidates if row['eligible']]
    return dict(digest=fingerprint(it, lower), candidates=candidates, chart_candidates=chart_candidates,
                performance_digest=performance_fingerprint(it, lower),
                recommended=eligible[0]['family'] if eligible else None,
                chart_recommended=chart_eligible[0]['family'] if chart_eligible else None,
                reference=it['limit_reference_label'], n=int(it['n']),
                natural_lower=lower, version=MODEL_VERSION, max_candidates=max_candidates,
                chart_fit_target='individual' if int(it['n']) == 1 else 'subgroup_mean')


def _diagnose_values(values, lower, *, cancel=None, progress=None, max_candidates=None):
    candidates = []
    if max_candidates is None:
        for family in FAMILIES:
            _cancelled(cancel)
            if progress is not None:
                progress(f'{FAMILIES[family]} 분포 적합 중')
            candidates.append(fit_checked(values, family, lower, cancel=cancel, progress=progress))
    else:
        for family, name in FAMILIES.items():
            _cancelled(cancel)
            if progress is not None:
                progress(f'{name} 후보 적합 · AICc 비교 중')
            row = dict(family=family, name=name, eligible=False, fit_n=len(values),
                       test_n=min(len(values), GOF_MAX), bootstrap_samples=0,
                       validation_status='미검증',
                       reason='빠른 진단에서 미검증: 전체 후보 진단으로 확인할 수 있습니다.')
            try:
                params, ll = _fit_params(family, values, lower)
                k = len(params)-int(lower is not None)
                row['aicc'] = float(2*k-2*ll+2*k*(k+1)/(len(values)-k-1))
            except (ValueError, RuntimeError, FloatingPointError, OverflowError) as exc:
                row.update(validation_status='적합 불가', reason=str(exc))
            candidates.append(row)
        pending = sorted((row for row in candidates if 'aicc' in row),
                         key=lambda row: (row['aicc'], row['family']))
        for start in range(0, len(pending), max_candidates):
            batch = pending[start:start+max_candidates]
            for row in batch:
                _cancelled(cancel)
                checked = fit_checked(values, row['family'], lower, cancel=cancel, progress=progress)
                row.clear()
                row.update(checked)
                row['validation_status'] = '검증 완료' if 'pvalue' in checked else '검증 불가'
            if any(row['eligible'] for row in batch):
                break
    candidates.sort(key=lambda row: (not row['eligible'], row.get('aicc', np.inf), row['family']))
    return candidates


def frozen_model(record):
    return getattr(stats, record['family'])(*record['params'])


def pearson_limits(model, n, *, cancel=None):
    """Direct quantiles of the fitted plotted statistic; n does not rescale it."""
    _cancelled(cancel)
    frozen = frozen_model(model)
    low, high = frozen.ppf([CHART_TAIL, 1-CHART_TAIL])
    if not np.isfinite([low, high]).all() or low >= high:
        raise ValueError('Pearson 위치 분포의 꼬리 분위수가 유효하지 않습니다.')
    return dict(lcl=float(low), ucl=float(high), simulation_samples=0, seed=SEED,
                chart_fit_target='individual' if n == 1 else 'subgroup_mean',
                chart_limit_method='AIAG–VDA 9.3.5.2 · 위치 통계량 직접 적합 · 0.135%/99.865% 분위수')


def capability_indices(frozen, usl=np.nan, lsl=np.nan):
    """G and Z indices; natural bounds are never substituted for specifications."""
    low, median, high = map(float, frozen.ppf([ALPHA, .5, 1-ALPHA]))
    g, z, tails = [], [], []
    if np.isfinite(usl):
        g.append((usl-median)/(high-median))
        z.append(float(-special.ndtri_exp(frozen.logsf(usl))/3))
        tails.append(float(frozen.sf(usl)))
    if np.isfinite(lsl):
        g.append((median-lsl)/(median-low))
        z.append(float(-special.ndtri_exp(frozen.logcdf(lsl))/3))
        tails.append(float(frozen.cdf(lsl)))
    bilateral = np.isfinite(usl) and np.isfinite(lsl)
    methods = {
        'G': dict(pp=(usl-lsl)/(high-low) if bilateral else np.nan, ppk=min(g) if g else np.nan),
        'Z': dict(pp=sum(z)/2 if bilateral else np.nan, ppk=min(z) if z else np.nan),
    }
    return dict(methods=methods, quantiles=dict(low=low, median=median, high=high),
                ppm=float(sum(tails)*1e6) if tails else np.nan,
                numeric_status={method: {key: ('+inf' if value == np.inf else '-inf' if value == -np.inf
                                               else 'finite' if np.isfinite(value) else 'not_applicable')
                                         for key, value in items.items()} for method, items in methods.items()})


def apply_analysis(result, config):
    """Replace normal limits and indices, retaining source grouping and raw defects."""
    from spc_core import _segment_violation_counts, _split_ksq_counts
    it = dict(result)
    cfg = validate_settings(config)
    it.update(distribution_mode='nonnormal', capability_method=cfg['method'],
              distribution_settings=cfg, distribution_model=None, capability_methods={},
              chart_fit_status='미확인', performance_fit_status='미확인',
              effective_rules={'T1': True, **{f'T{i}': False for i in range(2, 9)}},
              pearson_rules_note='Pearson 위치 관리도는 직접 적합한 위치 분포의 분위수 이탈(T1)만 검출합니다. 산포는 Shewhart R/S 한계를 사용합니다. T2~T8·정규 σ 구역은 미적용입니다.',
              dispersion_limit_method='Shewhart R/S · AIAG–VDA 9.3.5.2 산포 관리도 참조',
              within_capability_note='비정규 모드에서 정규식 기반 군내·목표치 지수는 미적용입니다.',
              performance_note='선택·검증된 개별값 분포의 G/Z 결과입니다. 관측 구간 검증은 공식 능력 입증과 구분하세요.')
    for key in ('cp', 'cpk', 'pp', 'ppk', 'cw', 'cwk', 'cpm', 'cpmk', 'std_within',
                'ppm_cpk', 'ppm_ppk', 'ppm_within', 'within_overall_ratio', 'stab_ratio'):
        it[key] = np.nan
    # Never present normal control limits/signals as Pearson results on failed selection.
    for key in ('ucl', 'lcl'):
        it[key] = np.nan
    if it['n'] == 1:
        it['r_ucl'] = it['r_lcl'] = np.nan
    it['chart_mode'] = 'Pearson (분포 확인 필요)'
    it['ksq_counts'] = {f'T{i}': 0 for i in range(1, 9)}
    it['ksq_idx'] = {f'T{i}': set() for i in range(1, 9)}
    for key in list(it):
        if ('ctrl_violation_count' in key or '_ucl_cnt' in key or '_lcl_cnt' in key
                or key in ('recent_ctrl_violation', 'baseline_ksq_total', 'baseline_ctrl_total')):
            it[key] = 0
    it['baseline_status'] = '미평가'
    it['baseline_reasons'] = ['분포 확인 필요']
    it['ksq_counts_baseline'] = dict(it['ksq_counts'])
    it['ksq_counts_monitor'] = dict(it['ksq_counts'])
    try:
        if not cfg['family'] or not cfg['chart_family'] or cfg['confirmed_digest'] != fingerprint(it, cfg['natural_lower']):
            raise ValueError('분포 선택·확인 필요: 데이터·부분군·규격·기준구간 변경 후에는 다시 확인하세요.')
        reference = reference_values(it)
        location = location_reference_values(it)
        values = np.asarray(it['series'])
        check_sample(reference, int(it['n']))
        check_location_sample(location, int(it['n']))
        chart_model = fit_checked(location, cfg['chart_family'], cfg['natural_lower'])
        chart_model['fit_target'] = 'individual' if it['n'] == 1 else 'subgroup_mean'
        it['chart_model'] = chart_model
        if not chart_model['eligible']:
            raise ValueError('기준분포 부적합: ' + chart_model['reason'])
        it['chart_fit_status'] = '적합도 기준 통과'
        limits = pearson_limits(chart_model, int(it['n']))
        it.update(limits)
        it['chart_mode'] = 'Pearson I (MR 참고)' if it['n'] == 1 else f'Pearson Xbar / Shewhart {it["dispersion_kind"]}'
        it['limit_reference_desc'] += f' · {chart_model["name"]} 위치 분포 직접 적합 · 0.135%/99.865% 분위수'
        it['xbarbar'] = float(np.mean(reference))
        xc = _segment_violation_counts(it['means'], it['ucl'], it['lcl'], it['baseline_display_count'])
        rc = _segment_violation_counts(it['ranges'], it['r_ucl'], it['r_lcl'], it['baseline_r_display_count'])
        for prefix, counts in (('xbar', xc), ('r', rc)):
            it[f'{prefix}_ucl_cnt'] = counts['ucl_total']
            it[f'{prefix}_lcl_cnt'] = counts['lcl_total']
            for scope in ('baseline', 'monitor'):
                it[f'{prefix}_ucl_cnt_{scope}'] = counts[f'ucl_{scope}']
                it[f'{prefix}_lcl_cnt_{scope}'] = counts[f'lcl_{scope}']
        for prefix, counts in (('x', xc), ('r', rc)):
            it[f'{prefix}_ctrl_violation_count'] = counts['total']
            for scope in ('baseline', 'monitor'):
                it[f'{prefix}_ctrl_violation_count_{scope}'] = counts[scope]
        indices = set(np.flatnonzero((it['means'] > it['ucl']) | (it['means'] < it['lcl'])).tolist())
        it['ksq_idx']['T1'] = indices
        it['ksq_counts']['T1'] = len(indices)
        it['ksq_counts_baseline'], it['ksq_counts_monitor'] = _split_ksq_counts(it['ksq_idx'], it['baseline_display_count'])
        it['baseline_ksq_total'] = it['ksq_counts_baseline']['T1']
        it['baseline_ctrl_total'] = xc['baseline'] + rc['baseline']
        baseline_bad = it['control_phase'] == 'phase2' and it['baseline_ctrl_total'] > 0
        it['baseline_status'] = ('부적합' if baseline_bad else '적합') if it['control_phase'] == 'phase2' else '해당없음'
        it['baseline_reasons'] = ['기준구간 관리이탈: 원인 조사 전 관리한계는 잠정값'] if baseline_bad else []
        it['recent_ctrl_violation'] = int(it['means'][-1] > it['ucl'] or it['means'][-1] < it['lcl']
                                           or (it['n'] > 1 and (it['ranges'][-1] > it['r_ucl'] or it['ranges'][-1] < it['r_lcl'])))
        if cfg['performance_digest'] != performance_fingerprint(it, cfg['natural_lower']):
            raise ValueError('전체 데이터 분포 선택·확인 필요: 성능 분포를 다시 진단·확인하세요.')
        performance_model = fit_checked(values, cfg['family'], cfg['natural_lower'])
        performance_model['fit_target'] = 'individual'
        it['distribution_model'] = performance_model
        if not performance_model['eligible']:
            raise ValueError('전체 성능분포 부적합: ' + performance_model['reason'])
        it['performance_fit_status'] = '적합도 기준 통과'
        capability = capability_indices(frozen_model(performance_model), it['usl'], it['lsl'])
        it['capability_quantiles'] = capability['quantiles']
        it['numeric_status'] = capability['numeric_status']
        it['ppm_ppk'] = capability['ppm']
        unstable = xc['total'] + rc['total'] > 0 or baseline_bad
        reasons = ['Pearson 관리한계 이탈'] if unstable else []
        if not (np.isfinite(it['usl']) or np.isfinite(it['lsl'])):
            reasons.append('규격없음')
        if it.get('detail_level') == 'core':
            reasons.append('상세(full) 분석에서 능력 평가')
        eligible = not reasons
        it['capability_status'] = '적용 가능' if eligible else '관리상태 확인' if unstable else '계산불가'
        it['capability_reasons'] = reasons
        for method, items in capability['methods'].items():
            items.update(cp=items['pp'] if eligible else np.nan, cpk=items['ppk'] if eligible else np.nan)
            for name, value in items.items():
                it[f'{name}_{method.lower()}'] = value
        it['capability_methods'] = capability['methods']
        it.update(capability['methods'][cfg['method']])
        if eligible:
            it['ppm_cpk'] = it['ppm_ppk']
        it['reliability_status'] = '검토필요' if reasons else '주의' if it['excluded_missing_groups'] else '정상'
        it['reliability_reasons'] = reasons + (['결측부분군제외'] if it['excluded_missing_groups'] else [])
    except (ValueError, RuntimeError, FloatingPointError) as exc:
        message = str(exc)
        it.update(capability_status='분포 선택 필요' if '선택·확인 필요' in message else '분포 검토 필요',
                  capability_reasons=[message], reliability_status='검토필요', reliability_reasons=[message])
        if it['chart_fit_status'] != '적합도 기준 통과':
            it['chart_fit_status'] = message
            it['r_ucl'] = it['r_lcl'] = np.nan
        else:
            it['performance_fit_status'] = message
    it['reliability_reason_text'] = ', '.join(it['reliability_reasons'])
    return it
