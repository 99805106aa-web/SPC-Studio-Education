"""Interactive dashboard for prioritising capability review."""
import html
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from spc_charts import PALETTES, DEFAULT_PALETTE
from spc_policy import limits, format_criteria


def dashboard_rows(results):
    def number(value):
        return float(value) if value is not None and np.isfinite(value) else np.nan
    return pd.DataFrame([
        {'항목': str(it['column']), 'Cpk': number(it.get('cpk')),
         'Ppk': number(it.get('ppk')), '예측 PPM': number(it.get('ppm_ppk')),
         '관측 PPM': number(it.get('emp_ppm')),
         '규격 이탈': it.get('spec_violation_count', 0),
         '능력 적용 상태': it.get('capability_status', '재분석 필요'),
         '능력 적용 사유': '; '.join(it.get('capability_reasons', [])),
         '계산법': it.get('capability_method', 'normal'),
         '적용 분포': (it.get('distribution_model') or {}).get('name', '정규' if it.get('distribution_mode') != 'nonnormal' else '미확인')}
        for it in results
    ], columns=['항목', 'Cpk', 'Ppk', '예측 PPM', '관측 PPM', '규격 이탈', '능력 적용 상태', '능력 적용 사유', '계산법', '적용 분포'])


def capability_figure(frame, metric='Cpk', focus=True, compare=False, palette=DEFAULT_PALETTE, assessment=None):
    warning, required = limits(assessment)
    criteria_text = lambda text: format_criteria(text, warning, required)
    colors = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    ink = '#E2E8F0' if palette == 'dark' else '#24364B'
    fig = go.Figure()
    shown = frame.dropna(subset=[metric]).sort_values(metric, kind='stable')
    ids = [str(i) for i in range(len(shown))]
    other = 'Ppk' if metric == 'Cpk' else 'Cpk'
    for field, symbol in [(metric, 'circle'), (other, 'diamond-open')]:
        if field != metric and not compare:
            continue
        values = shown[field].to_numpy()
        plotted = np.clip(values, -1, 3) if focus else values
        if field == metric:
            fig.add_trace(go.Bar(
                x=plotted, y=ids, orientation='h', name=field,
                marker_color=[colors['danger'] if v < warning else colors['warning'] if v < required
                              else colors['success'] for v in values],
                text=[f'{v:.3f}' + (' →' if focus and v > 3 else ' ←' if focus and v < -1 else '') for v in values],
                textposition='outside', cliponaxis=False, width=.52,
                customdata=[[html.escape(str(row['항목'])), row[field]] for _, row in shown.iterrows()],
                hovertemplate=f'<b>%{{customdata[0]}}</b><br>{field} %{{customdata[1]:.4f}}<extra></extra>'))
            continue
        fig.add_trace(go.Scatter(
            x=plotted, y=ids, mode='markers', name=field,
            marker=dict(size=12 if field == metric else 10,
                        symbol=[('triangle-right' if v > 3 else 'triangle-left')
                                if focus and np.isfinite(v) and (v > 3 or v < -1) else symbol
                                for v in values],
                        color=[colors['danger'] if v < warning else colors['warning'] if v < required
                               else colors['success'] for v in values]
                              if field == metric else colors['muted'],
                        line=dict(width=1.5)),
            customdata=[[html.escape(str(row['항목'])), row[field]] for _, row in shown.iterrows()],
            hovertemplate=f'<b>%{{customdata[0]}}</b><br>{field} %{{customdata[1]:.4f}}<extra></extra>'))
    fig.add_vline(x=warning, line_dash='dot', line_color=colors['danger'],
                  annotation_text=criteria_text('1.00'), annotation_position='top left')
    fig.add_vline(x=required, line_dash='dot', line_color=colors['warning'],
                  annotation_text=criteria_text('1.33'), annotation_position='top right')
    fig.update_layout(height=max(350, 32*len(shown)+100),
                      paper_bgcolor=colors['ax_bg'], plot_bgcolor=colors['ax_bg'],
                      font=dict(color=ink, size=13), margin=dict(l=20, r=80, t=45, b=45),
                      showlegend=compare, legend=dict(orientation='h', y=1.12), hovermode='closest')
    fig.update_yaxes(type='category', categoryorder='array', categoryarray=ids,
                     autorange='reversed', tickvals=ids,
                     ticktext=[html.escape(str(v)) for v in shown['항목']],
                     gridcolor=colors['grid'])
    fig.update_xaxes(title=f'{metric} · 낮은 항목부터 표시', gridcolor=colors['grid'],
                     range=[-1.15, 3.2] if focus else None, zeroline=True)
    return fig


def render_dashboard(results, palette=DEFAULT_PALETTE):
    import streamlit as st
    warning, required = limits(results[0] if results else None)
    criteria_text = lambda text: format_criteria(text, warning, required)
    frame = dashboard_rows(results)
    m1, m2, m3 = st.columns(3)
    m1.metric(criteria_text('능력 부족 · Cpk < 1.00'), f"{int((frame['Cpk'] < warning).sum())}개")
    m2.metric(criteria_text('주의 · Cpk 1.00–1.33 미만'), criteria_text(f"{int(((frame['Cpk'] >= warning) & (frame['Cpk'] < required)).sum())}개"))
    m3.metric(criteria_text('기준 충족 · Cpk ≥ 1.33'), criteria_text(f"{int((frame['Cpk'] >= required).sum())}개"))
    missing = int(frame['Cpk'].isna().sum())
    st.caption(f'전체 {len(frame)}개 항목 · Cpk 적용 조건 미충족/계산 불가 {missing}개')
    st.caption('Cp·Cpk는 관측 구간의 적용 조건을 확인합니다. 비정규 결과는 선택한 G/Z 방법과 적합분포를 확인하세요. Ppk·예측 PPM은 모형 참고값이며 공식 능력 입증과 구분하세요.')
    with st.expander('검색 · 표시 설정'):
        c1, c2 = st.columns(2)
        query = c1.text_input('항목 검색', key='dash_query')
        metric = c2.selectbox('평가 지표', ['Cpk', 'Ppk'], key='dash_metric')
        status = c1.selectbox('평가 범위', ['전체', criteria_text('기준 미달 (<1.33)'), criteria_text('기준 충족 (≥1.33)'), '계산 불가'], key='dash_status')
        count = c2.selectbox('최대 표시 항목', [10, 20, 50, 100], key='dash_simple_count')
        focus = st.checkbox('주요 구간 확대 (-1~3)', value=True, key='dash_simple_focus')
        compare = st.checkbox('Cpk / Ppk 비교', value=False, key='dash_simple_compare')
    selected = frame[frame['항목'].str.contains(query.strip(), case=False, regex=False)].copy()
    if status == criteria_text('기준 미달 (<1.33)'):
        selected = selected[selected[metric] < required]
    elif status == criteria_text('기준 충족 (≥1.33)'):
        selected = selected[selected[metric] >= required]
    elif status == '계산 불가':
        selected = selected[selected[metric].isna()]
    st.caption(f'현재 범위: {metric} · {status} · 검색: {query.strip() or "전체"} · {len(selected)}개 항목')
    if selected.empty:
        st.info('선택 조건에 해당하는 항목이 없습니다.')
        return
    st.markdown(f'#### {metric} 현황')
    chart_rows = selected.dropna(subset=[metric]).sort_values(metric, kind='stable').head(count)
    if not chart_rows.empty:
        st.caption(criteria_text(f'낮은 순서로 {len(chart_rows)}개 표시 · 빨강 < 1.00 / 주황 < 1.33 / 초록 ≥ 1.33'))
        st.plotly_chart(capability_figure(chart_rows, metric, focus, compare, palette, results[0] if results else None),
                        use_container_width=True, key='dashboard_capability', config={'displaylogo': False})
        if focus:
            st.caption('막대 끝 숫자는 실제 값입니다. 화살표가 있는 막대는 표시 범위에서 잘렸습니다.')
    else:
        st.info('선택한 지표를 계산할 수 없습니다. 규격과 측정값을 확인하세요.')
    st.markdown('#### 예측 규격 이탈 상위 5개')
    ppm = selected.dropna(subset=['예측 PPM']).sort_values('예측 PPM', ascending=False).head(5).copy()
    if ppm.empty:
        st.info('예측 PPM을 계산할 수 있는 항목이 없습니다.')
    else:
        st.caption('예측 PPM은 선택한 분포 모형의 규격 밖 확률이며, 관측 PPM은 원본의 실제 규격 이탈 비율입니다.')
        from spc_core import fmt_ppm
        for name in ['예측 PPM', '관측 PPM']:
            ppm[name] = ppm[name].map(fmt_ppm)
        st.dataframe(ppm[['항목', '예측 PPM', '관측 PPM']], hide_index=True, use_container_width=True)
    with st.expander('전체 상세 수치 · CSV 다운로드'):
        detail = selected.sort_values(metric, kind='stable', na_position='last')
        st.dataframe(detail, hide_index=True, use_container_width=True,
                     column_config={name: st.column_config.NumberColumn(format='%.3f')
                                    for name in ['Cpk', 'Ppk', '예측 PPM', '관측 PPM']})
        st.download_button('현재 목록 CSV 다운로드', detail.to_csv(index=False).encode('utf-8-sig'),
                           file_name='공정능력_대시보드.csv', mime='text/csv', key='dash_csv')

