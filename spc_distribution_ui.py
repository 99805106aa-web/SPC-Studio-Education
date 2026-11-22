"""Explicit, cancellable per-variable distribution diagnosis and confirmation."""
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import pandas as pd
import streamlit as st

from spc_nonnormal import (BOOTSTRAP_SAMPLES, FAMILIES, diagnose_distribution, fingerprint,
                           frozen_model, reference_values, location_reference_values, diagnosis_fingerprint,
                           pearson_limits)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='spc-fit')


def _location_limit_preview(it, model):
    from spc_core import _segment_violation_counts

    limits = pearson_limits(model, int(it['n']))
    values = np.asarray(it['means'], dtype=float)
    counts = _segment_violation_counts(values, limits['ucl'], limits['lcl'], it['baseline_display_count'])
    return limits, counts


def _location_preview_chart(it, model, limits, center):
    from html import escape
    import plotly.graph_objects as go
    from spc_charts import PALETTES, DEFAULT_PALETTE

    colors = PALETTES.get(st.session_state.get('palette'), PALETTES[DEFAULT_PALETTE])
    values = np.asarray(it['means'], dtype=float)
    positions = np.arange(1, len(values) + 1)
    labels = it.get('x_labels')
    labels = ([escape(str(label)) for label in labels] if labels is not None and len(labels) == len(values)
              else positions.astype(str))
    outside = (values < limits['lcl']) | (values > limits['ucl'])
    statistic = '부분군 평균' if it['n'] > 1 else '개별값'
    fig = go.Figure(go.Scatter(x=positions, y=values, text=labels, mode='lines+markers',
                               name=statistic, line=dict(color=colors['primary'], width=2),
                               hovertemplate='%{text}<br>%{y:.6g}<extra></extra>'))
    if outside.any():
        fig.add_scatter(x=positions[outside], y=values[outside], text=np.asarray(labels)[outside],
                        mode='markers', name='한계 이탈',
                        marker=dict(color=colors['danger'], size=10, symbol='diamond'),
                        hovertemplate='%{text}<br>%{y:.6g}<extra></extra>')
    for label, value, color, dash in (
        ('LCL', limits['lcl'], colors['success'], 'dash'),
        ('CL', center, colors['muted'], 'dot'),
        ('UCL', limits['ucl'], colors['danger'], 'dash'),
    ):
        fig.add_hline(y=value, line_color=color, line_dash=dash, line_width=2,
                      annotation_text=f'{label} {value:.6g}', annotation_position='top right')
    boundary = it['baseline_display_count']
    if it.get('control_phase') == 'phase2' and 0 < boundary < len(values):
        fig.add_vrect(x0=.5, x1=boundary + .5, fillcolor=colors['accent'], opacity=.08, line_width=0,
                      layer='below')
        fig.add_vline(x=boundary + .5, line_dash='dot', line_color=colors['muted'])
    fig.update_layout(title=f"{model['name']} · 위치 관리도 미리보기", height=380,
                      xaxis_title='측정 순서', yaxis_title=statistic,
                      paper_bgcolor=colors['ax_bg'], plot_bgcolor=colors['ax_bg'],
                      font=dict(color=colors['muted'], size=14),
                      legend=dict(orientation='h', y=-.23), margin=dict(t=56, b=88))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor=colors['grid'])
    return fig


def preview_figure(values, model):
    import matplotlib.pyplot as plt
    from spc_charts import _apply_palette, annotate_qq_diagnostics
    colors = _apply_palette()
    values = np.sort(np.asarray(values, dtype=float))
    full_values = values
    if len(values) > 10000:
        values = values[np.linspace(0, len(values)-1, 10000).astype(int)]
    frozen = frozen_model(model)
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), layout='constrained')
    axes[0].hist(values, bins=30, density=True, color=colors['primary'], alpha=.9,
                 edgecolor=colors['ax_bg'], linewidth=.6)
    grid = np.linspace(values.min(), values.max(), 400)
    axes[0].plot(grid, frozen.pdf(grid), color=colors['accent'], lw=2.5)
    axes[0].set_title(model['name'] + ' 적합곡선')
    p = (np.arange(len(values))+.5)/len(values)
    theoretical = frozen.ppf(p)
    axes[1].scatter(theoretical, values, s=24, color=colors['primary'])
    lo, hi = min(theoretical.min(), values.min()), max(theoretical.max(), values.max())
    axes[1].plot([lo, hi], [lo, hi], color=colors['muted'], lw=1.8, ls='--')
    axes[1].set_title('선택 분포 Q-Q')
    axes[1].set_xlabel('모형 분위수')
    axes[1].set_ylabel('관측 분위수')
    annotate_qq_diagnostics(axes[1], full_values, colors, model=model, above=False)
    return fig


def render_distribution_panel(it, *, save_config=None):
    column = str(it['column'])
    settings = st.session_state.get('distribution_settings', {})
    existing = settings.get(column, {})
    with st.expander('비정규 분포 진단 · Pearson / G·Z 설정',
                     expanded=bool(st.session_state.get('_guide_pearson_requested')
                                   or st.session_state.get('_distribution_jobs', {}).get(column)),
                     key=f'dist_panel_{column}', on_change='rerun'):
        st.caption('자연하한은 규격 LSL과 다릅니다. 분포를 확인·선택한 뒤 적용하며, Pearson은 T1만 검출합니다.')
        st.caption('AIAG–VDA 9.3.5.2: 위치 관리도는 기준구간의 부분군 평균(n=1은 개별값)에 직접 적합합니다. '
                   '능력·성능은 개별 측정값 분포로 계산합니다. 산포는 Shewhart R/S 관리도를 사용합니다.')
        if existing.get('mode') == 'nonnormal':
            state = '저장된 선택 · 현재 미적용'
            if it.get('distribution_mode') == 'nonnormal':
                state = ('적용 중' if it.get('performance_fit_status') == '적합도 기준 통과'
                         else '위치 관리한계 적용 중 · 전체 성능분포 재확인 필요')
            st.info(f"{state} · 위치 분포: {FAMILIES.get(existing.get('chart_family', existing.get('family')), '미확인')} · "
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
        digest = diagnosis_fingerprint(it, lower)
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
        st.caption(f"위치 기준: {diagnosis['reference']} / 성능 기준: 전체 분석 데이터 · {scope_note} · AD bootstrap {BOOTSTRAP_SAMPLES}회 · 유의수준 0.05 · 검증 통과 후보 중 AICc 순 추천")
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
        else:
            family = st.selectbox('개별 측정값 분포 (능력·성능)', available, format_func=FAMILIES.get, key=f'dist_family_{column}')
            chosen = next(r for r in candidates if r['family'] == family)
            with st.expander('개별 측정값 적합곡선·Q-Q'):
                _show_fit_preview(it['series'], chosen)
        st.markdown('**위치 관리도용 분포 · 부분군 평균**' if it['n'] > 1 else '**위치 관리도용 분포 · 개별값**')
        chart_candidates = diagnosis.get('chart_candidates', [])
        previews = {}
        chart_rows = []
        phase2 = it.get('control_phase') == 'phase2'
        for model in chart_candidates:
            reason = model['reason']
            limits, counts = {}, {}
            if model['eligible']:
                try:
                    limits, counts = _location_limit_preview(it, model)
                    previews[model['family']] = (model, limits, counts)
                except (ValueError, FloatingPointError, OverflowError) as exc:
                    reason = str(exc)
            row = dict(분포=model['name'], 적용가능=bool(limits), LCL=limits.get('lcl'), UCL=limits.get('ucl'))
            row['한계 이탈'] = counts.get('total')
            if phase2:
                row.update({'기준 이탈': counts.get('baseline'), '감시 이탈': counts.get('monitor')})
            row.update(AICc=model.get('aicc'), AD=model.get('ad'), p=model.get('pvalue'),
                       적합N=model['fit_n'], 검정N=model['test_n'], 사유=reason)
            chart_rows.append(row)
        st.dataframe(pd.DataFrame(chart_rows), hide_index=True, width='stretch',
                     column_config={key: st.column_config.NumberColumn(format='%.6g') for key in ('LCL', 'UCL')})
        chart_available = list(previews)
        if not chart_available:
            st.warning('위치 통계량에 적합한 분포가 없습니다. 진단을 다시 실행하거나 공정 조건을 확인하세요.')
            return
        saved_family = existing.get('chart_family', existing.get('family'))
        chart_family = st.selectbox('위치 관리도 분포', chart_available, format_func=FAMILIES.get,
                                    index=chart_available.index(saved_family) if saved_family in previews else 0,
                                    key=f'dist_chart_family_{column}')
        chart_chosen, limits, counts = previews[chart_family]
        center = float(np.mean(reference_values(it)))
        st.plotly_chart(_location_preview_chart(it, chart_chosen, limits, center), width='stretch',
                        config={'displaylogo': False}, key=f'dist_location_preview_{column}')
        count_note = f"위치 한계 이탈 {counts['total']}점"
        if phase2:
            count_note += f" (기준 {counts['baseline']}점, 감시 {counts['monitor']}점)"
        st.caption(count_note + ' · 본 관리도는 아래 적용 버튼을 누르면 갱신됩니다.')
        with st.expander('위치 관리도 분포 적합곡선·Q-Q'):
            _show_fit_preview(location_reference_values(it), chart_chosen)
        if not available:
            return
        method = st.radio('주 판정 방법 (G·Z 모두 계산)', ['G', 'Z'],
                          index=0 if existing.get('method', 'G') == 'G' else 1,
                          format_func=lambda x: 'G · 분위수법' if x == 'G' else 'Z · 규격 초과 확률법',
                          horizontal=True, key=f'dist_method_{column}')
        checked = st.checkbox('개별값·위치 분포의 그래프·적합도와 공정의 물리적 조건을 확인했습니다.',
                              key=f'dist_confirm_{column}_{digest}_{family}_{chart_family}')
        if st.button('선택 분포 적용 · Pearson/G·Z 분석', key=f'dist_apply_{column}', disabled=not checked):
            st.session_state['distribution_settings'] = {**settings, column: dict(
                mode='nonnormal', family=family, chart_family=chart_family, method=method, natural_lower=lower,
                confirmed_digest=diagnosis['digest'], performance_digest=diagnosis['performance_digest'])}
            _rerun_analysis(save_config)


def _show_fit_preview(values, model):
    import matplotlib.pyplot as plt

    fig = preview_figure(values, model)
    try:
        st.pyplot(fig, width='stretch')
    finally:
        plt.close(fig)


def _rerun_analysis(save_config):
    if save_config is not None:
        save_config()
    st.session_state['_trigger_analysis'] = True
    st.rerun()
