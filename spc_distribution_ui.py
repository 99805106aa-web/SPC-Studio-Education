"""Explicit, cancellable per-variable distribution diagnosis and confirmation."""
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import pandas as pd
import streamlit as st

from spc_nonnormal import (BOOTSTRAP_SAMPLES, FAMILIES, diagnose_distribution, fingerprint,
                           frozen_model, reference_values, location_reference_values)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='spc-fit')


def preview_figure(values, model):
    import matplotlib.pyplot as plt
    values = np.sort(np.asarray(values, dtype=float))
    if len(values) > 10000:
        values = values[np.linspace(0, len(values)-1, 10000).astype(int)]
    frozen = frozen_model(model)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), layout='constrained')
    axes[0].hist(values, bins=30, density=True, color='#93b5db', alpha=.7)
    grid = np.linspace(values.min(), values.max(), 400)
    axes[0].plot(grid, frozen.pdf(grid), color='#2456a5', lw=2)
    axes[0].set_title(model['name'] + ' 적합곡선')
    p = (np.arange(len(values))+.5)/len(values)
    theoretical = frozen.ppf(p)
    axes[1].scatter(theoretical, values, s=8, color='#2456a5')
    lo, hi = min(theoretical.min(), values.min()), max(theoretical.max(), values.max())
    axes[1].plot([lo, hi], [lo, hi], color='#64748b')
    axes[1].set_title('선택 분포 Q-Q')
    axes[1].set_xlabel('모형 분위수')
    axes[1].set_ylabel('관측 분위수')
    return fig


def render_distribution_panel(it, *, save_config=None):
    column = str(it['column'])
    settings = st.session_state.get('distribution_settings', {})
    existing = settings.get(column, {})
    with st.expander('비정규 분포 진단 · Pearson / G·Z 설정',
                     expanded=bool(st.session_state.get('_distribution_jobs', {}).get(column)),
                     key=f'dist_panel_{column}', on_change='rerun'):
        st.caption('자연하한은 규격 LSL과 다릅니다. 분포를 확인·선택한 뒤 적용하며, Pearson은 T1만 검출합니다.')
        st.caption('AIAG–VDA 9.3.5.2: 위치 관리도는 기준구간의 부분군 평균(n=1은 개별값)에 직접 적합합니다. '
                   '능력·성능은 개별 측정값 분포로 계산합니다. 산포는 Shewhart R/S 관리도를 사용합니다.')
        if existing.get('mode') == 'nonnormal':
            st.info(f"위치 분포: {FAMILIES.get(existing.get('chart_family', existing.get('family')), '미확인')} · "
                    f"개별값 분포: {FAMILIES.get(existing.get('family'), '미확인')} · 계산법 {existing.get('method', 'G')}")
            st.caption(it.get('pearson_rules_note', ''))
            if it.get('capability_methods'):
                st.dataframe(pd.DataFrame(it['capability_methods']).T.rename_axis('계산법'), use_container_width=True)
            st.json({'위치 통계량 기준분포': it.get('chart_model'), '전체 개별값 성능분포': it.get('distribution_model'),
                     '위치 한계 계산': it.get('chart_limit_method'), '산포 한계 계산': it.get('dispersion_limit_method')}, expanded=False)
            if st.button('이 변수 정규 분석으로 복원', key=f'dist_reset_{column}'):
                st.session_state['distribution_settings'] = {k:v for k,v in settings.items() if k != column}
                _rerun_analysis(save_config)
        if st.session_state.get('preprocess_outliers') or st.session_state.get('box_cox_transform'):
            st.warning('자동 이상치 제거·Box-Cox를 끄고 다시 분석한 뒤 분포 진단을 실행하세요.')
            return
        bounded = st.checkbox('물리적 자연하한 적용 (기하공차 등)', value=existing.get('natural_lower') is not None,
                              key=f'dist_bounded_{column}')
        lower = st.number_input('자연하한', value=float(existing.get('natural_lower') or 0),
                                key=f'dist_lower_{column}') if bounded else None
        diagnostic_scope = st.radio('분포 진단 범위', ['빠른 진단 (추천)', '전체 후보 진단'],
                                    horizontal=True, key=f'dist_scope_{column}')
        st.caption('빠른 진단은 AICc 상위 3종부터 검증하고, 통과 후보가 없으면 다음 후보를 검증합니다. '
                   '검증은 분포마다 999회를 유지합니다. 모든 후보를 비교하려면 전체 후보 진단을 선택하세요.')
        digest = fingerprint(it, lower)
        jobs = st.session_state.setdefault('_distribution_jobs', {})
        job = jobs.get(column)
        busy = bool(job and job.get('future') is not None)
        if busy and job['digest'] != digest:
            job['cancel'].set()
            st.caption('분석 조건이 바뀌어 이전 진단을 취소합니다. 취소 후 새 조건으로 진단하세요.')
        if st.button('후보 분포 진단 시작', key=f'dist_start_{column}', disabled=busy):
            cancel = threading.Event()
            progress = {'text': '분포 진단 준비 중'}
            future = _pool.submit(diagnose_distribution, dict(it), lower, cancel=cancel,
                                  progress=lambda text: progress.update(text=text),
                                  max_candidates=3 if diagnostic_scope == '빠른 진단 (추천)' else None)
            jobs[column] = dict(digest=digest, future=future, cancel=cancel, progress=progress)
            st.rerun()
        if busy:
            @st.fragment(run_every='1s')
            def show_job():
                if job['future'].done():
                    try:
                        job['result'] = job['future'].result()
                    except Exception as exc:
                        job['error'] = str(exc)
                    job.pop('future', None)
                    st.rerun()
                st.info(job['progress']['text'])
                if st.button('분포 진단 취소', key=f'dist_cancel_{column}'):
                    job['cancel'].set()
                    st.caption('현재 적합 단계가 끝나면 취소합니다.')
            show_job()
            return
        if not job:
            return
        if job['digest'] != digest:
            st.warning('자료·부분군·규격·기준구간·자연하한이 변경되었습니다. 분포 진단을 다시 실행하세요.')
            return
        if job.get('error'):
            st.warning(job['error'])
            return
        diagnosis = job.get('result')
        if not diagnosis:
            return
        candidates = diagnosis['candidates']
        scope_note = '빠른 진단 · AICc 순 단계 검증' if diagnosis.get('max_candidates') else '전체 후보 진단'
        st.caption(f"적합 기준: {diagnosis['reference']} · {scope_note} · AD bootstrap {BOOTSTRAP_SAMPLES}회 · 유의수준 0.05 · 검증 통과 후보 중 AICc 순 추천")
        st.markdown('**능력·성능용 분포 · 개별 측정값**')
        st.dataframe(pd.DataFrame([
            dict(분포=r['name'], 검증=r.get('validation_status', '검증 완료' if 'pvalue' in r else '검증 불가'),
                 적용가능=r['eligible'], AICc=r.get('aicc'), AD=r.get('ad'),
                 p=r.get('pvalue'), 적합N=r['fit_n'], 검정N=r['test_n'], 사유=r['reason'])
            for r in candidates]), hide_index=True, use_container_width=True)
        if diagnosis.get('max_candidates'):
            st.caption('미검증 후보는 부적합 판정이 아닙니다. 전체 후보 진단을 실행하면 나머지 후보도 검증합니다.')
        available = [r['family'] for r in candidates if r['eligible']]
        if not available:
            st.warning('적합 기준을 통과한 분포가 없습니다. 측정 분해능·0값·혼합 집단·공정 조건을 확인하세요.')
            return
        family = st.selectbox('개별 측정값 분포 (능력·성능)', available, format_func=FAMILIES.get, key=f'dist_family_{column}')
        chosen = next(r for r in candidates if r['family'] == family)
        import matplotlib.pyplot as plt
        fig = preview_figure(reference_values(it), chosen)
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        st.markdown('**위치 관리도용 분포 · 부분군 평균**' if it['n'] > 1 else '**위치 관리도용 분포 · 개별값**')
        chart_candidates = diagnosis.get('chart_candidates', [])
        st.dataframe(pd.DataFrame([
            dict(분포=r['name'], 적용가능=r['eligible'], AICc=r.get('aicc'), AD=r.get('ad'),
                 p=r.get('pvalue'), 적합N=r['fit_n'], 검정N=r['test_n'], 사유=r['reason'])
            for r in chart_candidates]), hide_index=True, use_container_width=True)
        chart_available = [r['family'] for r in chart_candidates if r['eligible']]
        if not chart_available:
            st.warning('위치 통계량에 적합한 분포가 없습니다. 진단을 다시 실행하거나 공정 조건을 확인하세요.')
            return
        chart_family = st.selectbox('위치 관리도 분포', chart_available, format_func=FAMILIES.get,
                                    key=f'dist_chart_family_{column}')
        chart_chosen = next(r for r in chart_candidates if r['family'] == chart_family)
        fig = preview_figure(location_reference_values(it), chart_chosen)
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        method = st.radio('주 판정 방법 (G·Z 모두 계산)', ['G', 'Z'],
                          index=0 if existing.get('method', 'G') == 'G' else 1,
                          format_func=lambda x: 'G · 분위수법' if x == 'G' else 'Z · 규격 초과 확률법',
                          horizontal=True, key=f'dist_method_{column}')
        checked = st.checkbox('개별값·위치 분포의 그래프·적합도와 공정의 물리적 조건을 확인했습니다.',
                              key=f'dist_confirm_{column}_{digest}_{family}_{chart_family}')
        if st.button('선택 분포 적용 · Pearson/G·Z 분석', key=f'dist_apply_{column}', disabled=not checked):
            st.session_state['distribution_settings'] = {**settings, column: dict(
                mode='nonnormal', family=family, chart_family=chart_family, method=method, natural_lower=lower,
                confirmed_digest=diagnosis['digest'])}
            _rerun_analysis(save_config)


def _rerun_analysis(save_config):
    if save_config is not None:
        save_config()
    st.session_state['_trigger_analysis'] = True
    st.rerun()
