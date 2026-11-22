"""Structured, shared normality and shape diagnostics; no automatic model choice."""
from collections import OrderedDict
from threading import RLock
import hashlib
import warnings
import numpy as np
from scipy import stats

_cache = OrderedDict()
_lock = RLock()
SHAPE_GUIDANCE = ('정규성 근사 참고 기준: 왜도 절댓값 ≤ 2이고 첨도 절댓값 ≤ 7이면 정규성을 근사적으로 판단할 수 있습니다. '
                  '정규분포의 왜도는 0, 첨도는 3입니다. 이 경험 기준만으로 정규성이 입증되는 것은 아니며, '
                  '정규성 p값·히스토그램·Q-Q 도표를 함께 확인하세요.')

STATUS_LABELS = {'rejected':'정규성 기각', 'not_rejected':'정규성 기각되지 않음',
                 'unavailable':'검정 불가', 'not_evaluated':'상세 분석 필요'}


def shape_magnitude(value):
    return abs(value) if value is not None and np.isfinite(value) else None


def shape_text(value):
    magnitude = shape_magnitude(value)
    return '—' if magnitude is None else f'{magnitude:.3f}'


def shape_summary_display(frame):
    import pandas as pd

    if '일반 첨도' in frame and '첨도' not in frame:
        frame = frame.rename(columns={'일반 첨도': '첨도'})
    for label in ('왜도', '첨도'):
        if label in frame:
            frame[label] = pd.to_numeric(frame[label], errors='coerce').abs()
    return frame


def pvalue_text(value):
    if value is None or not np.isfinite(value):
        return '—'
    if value < .001:
        return '<0.001'
    # Do not round across the decision boundary or hide a p-value near 1.
    if value < .05 <= round(float(value),3):
        return repr(float(value))
    return f'{value:.3f}'


def normality_diagnostics(values, alpha=.05):
    from spc_core import (_normality_sample, _ad_pvalue_from_A2,
                          NORMALITY_MAX, NORMALITY_SW_CUTOFF, CALCULATION_VERSION)
    if not np.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError('유의수준은 0과 1 사이여야 합니다.')
    values = np.asarray(values,dtype=float)
    values = values[np.isfinite(values)]
    key = (CALCULATION_VERSION, hashlib.sha256(values.astype('<f8').tobytes()).hexdigest(), float(alpha),
           NORMALITY_MAX, NORMALITY_SW_CUTOFF, id(stats.anderson), id(stats.shapiro))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return dict(_cache[key])
    sample = _normality_sample(values)
    record = dict(test=None, statistic=None, adjusted_statistic=None, pvalue=None,
                  alpha=float(alpha), status='unavailable', reason='',
                  test_n=len(sample), total_n=len(values), skewness=None, kurtosis=None, excess_kurtosis=None,
                  sampling='전체 유효값' if len(sample)==len(values) else '전체 구간 고정 시드 표본',
                  message='')
    variable = len(values)>1 and np.ptp(values)>0
    if variable:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            if len(values)>=3:
                skew=float(stats.skew(values,bias=False))
                record['skewness']=skew if np.isfinite(skew) else None
            if len(values)>=4:
                kurt=float(stats.kurtosis(values,bias=False,fisher=True))
                record['excess_kurtosis']=kurt if np.isfinite(kurt) else None
                record['kurtosis']=kurt+3 if np.isfinite(kurt) else None
    if len(sample)<3:
        record['reason']=f'유효 표본수 {len(sample)} < 3'
    elif np.std(sample)==0:
        record['reason']='검정 표본의 표준편차=0'
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                if len(sample)<=NORMALITY_SW_CUTOFF:
                    statistic,p=stats.shapiro(sample)
                    record.update(test='Shapiro-Wilk',statistic=float(statistic),pvalue=float(p))
                else:
                    try:
                        ad=stats.anderson(sample,dist='norm')
                        n=len(sample)
                        record.update(test='Anderson-Darling', statistic=float(ad.statistic),
                            adjusted_statistic=float(ad.statistic)*(1+.75/n+2.25/n**2),
                            pvalue=_ad_pvalue_from_A2(float(ad.statistic),n))
                    except Exception:
                        # Retain the existing representative 5,000-point fallback policy.
                        sample=_normality_sample(sample,5000)
                        statistic,p=stats.shapiro(sample)
                        record.update(test='Shapiro-Wilk fallback',statistic=float(statistic),
                            pvalue=float(p),test_n=len(sample),
                            sampling='전체 유효값' if len(sample)==len(values) else '전체 구간 고정 시드 표본')
            if record['pvalue'] is None or not np.isfinite(record['pvalue']):
                raise ValueError('비유한 검정 결과')
            record['status']='not_rejected' if record['pvalue']>=alpha else 'rejected'
        except Exception as exc:
            record.update(status='unavailable',pvalue=None,reason=f'검정 예외: {type(exc).__name__}')
    if record['status']=='unavailable':
        record['message']='정규성 검정 불가 ('+record['reason']+')'
    else:
        # Compatibility text only: capability logic still uses these historical labels.
        verdict='만족' if record['status']=='not_rejected' else '의심'
        record['message']=f"P-값 = {pvalue_text(record['pvalue'])} ({verdict}) [{record['test']}]"
        if record['test_n']<record['total_n']:
            record['message']+=f" · 검정 표본 {record['test_n']:,}/전체 {record['total_n']:,}"
    with _lock:
        _cache[key]=dict(record)
        _cache.move_to_end(key)
        while len(_cache)>32:
            _cache.popitem(last=False)
    return record


def result_diagnostics(it):
    """Original-scale included individuals, baseline location, optional transformed data."""
    if it.get('detail_level')!='full':
        return {}
    native=np.asarray(it['raw_series'],dtype=float)[np.asarray(it['source_indices'],dtype=int)]
    def annotated(values,target,scope,scale):
        return dict(normality_diagnostics(values),target=target,scope=scope,scale=scale)
    result={'individual':annotated(native,'개별 측정값','전체 분석 대상 · 승인 제외 반영','원단위')}
    if int(it['n'])>1:
        location=native.reshape(-1,int(it['n'])).mean(axis=1)
        if it['control_phase']=='phase2':
            location=location[:int(it['baseline_display_count'])]
        result['location']=annotated(location,'부분군 평균',it['limit_reference_label'],'원단위')
    if it.get('box_cox_applied'):
        result['transformed']=annotated(it['series'],'Box–Cox 변환 후 개별값','전체 분석 대상','변환 단위')
    return result


def diagnostic_rows(results):
    """Flat numeric records shared by Excel, HTML/PDF and the API."""
    return [dict(variable=str(it['column']),view=view,**record)
            for it in results for view,record in it.get('normality_diagnostics',{}).items()]


def diagnostic_hint(record):
    if record.get('status')=='rejected':
        return ('정규성 기각은 비대칭 분포의 확정을 뜻하지 않습니다. 왜도·첨도와 히스토그램·Q-Q·시간 패턴을 확인하고 '
                '비정규 분포 진단에서 후보를 비교하세요. 꼬리 형태·혼합 집단·공정 변화도 검토하세요.')
    if record.get('status')=='not_rejected':
        return '정규성을 기각할 근거가 부족합니다. 정규분포나 공정 안정성이 입증된 것은 아닙니다.'
    return '검정에 필요한 유효 표본수와 산포를 확인하세요. 검정 불가를 정규성 기각으로 해석하지 마세요.'


def diagnostics_html(it, compact=False):
    from html import escape
    if compact:
        key = 'transformed' if it.get('box_cox_applied') else 'individual'
        record = it.get('normality_diagnostics', {}).get(key)
        if not record:
            return '<div class="norm">정규성: 상세 분석 필요</div>'
        unit = '변환값' if key == 'transformed' else '개별값'
        text = (f"정규성({unit}): p={pvalue_text(record.get('pvalue'))} · {STATUS_LABELS[record['status']]}"
                f" · 왜도 {shape_text(record.get('skewness'))} · 첨도 {shape_text(record.get('kurtosis'))}")
        return '<div class="norm compact-diagnostics">'+escape(text)+'</div>'
    parts=[]
    for name,record in it.get('normality_diagnostics',{}).items():
        text=(f"{record['target']} · {record['scope']} · {record['scale']} | "
              f"{record.get('test') or '검정 불가'} p={pvalue_text(record.get('pvalue'))} · {STATUS_LABELS[record['status']]} | "
              f"왜도 {shape_text(record.get('skewness'))} · 첨도 {shape_text(record.get('kurtosis'))} | "
              f"검정 N={record['test_n']:,}/전체 N={record['total_n']:,}")
        parts.append('<p>'+escape(text)+'</p>')
    if not parts:
        return '<div class="norm">분포 진단: 상세 재분석 필요</div>'
    return '<div class="norm"><b>분포 진단 · 정규성·형태</b>'+''.join(parts)+'<p>'+escape(SHAPE_GUIDANCE+' '+diagnostic_hint(
        it['normality_diagnostics']['individual']))+'</p></div>'
