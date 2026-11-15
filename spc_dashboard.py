"""Interactive dashboard for prioritising capability review."""
import html
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from spc_charts import PALETTES, DEFAULT_PALETTE


def dashboard_rows(results):
    def number(value):
        return float(value) if value is not None and np.isfinite(value) else np.nan
    return pd.DataFrame([
        {'항목': str(it['column']), 'Cpk': number(it.get('cpk')),
         'Ppk': number(it.get('ppk')), '예측 PPM': number(it.get('ppm_ppk')),
         '관측 PPM': number(it.get('emp_ppm')),
         '규격 이탈': it.get('spec_violation_count', 0)}
        for it in results
    ], columns=['항목', 'Cpk', 'Ppk', '예측 PPM', '관측 PPM', '규격 이탈'])


def capability_figure(frame, metric='Cpk', focus=True, compare=False, palette=DEFAULT_PALETTE):
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
                marker_color=[colors['danger'] if v < 1 else colors['warning'] if v < 1.33
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
                        color=[colors['danger'] if v < 1 else colors['warning'] if v < 1.33
                               else colors['success'] for v in values]
                              if field == metric else colors['muted'],
                        line=dict(width=1.5)),
            customdata=[[html.escape(str(row['항목'])), row[field]] for _, row in shown.iterrows()],
            hovertemplate=f'<b>%{{customdata[0]}}</b><br>{field} %{{customdata[1]:.4f}}<extra></extra>'))
    fig.add_vline(x=1, line_dash='dot', line_color=colors['danger'],
                  annotation_text='1.00', annotation_position='top left')
    fig.add_vline(x=1.33, line_dash='dot', line_color=colors['warning'],
                  annotation_text='1.33', annotation_position='top right')
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
    frame = dashboard_rows(results)
    m1, m2, m3 = st.columns(3)
    m1.metric('능력 부족 · Cpk < 1.00', f"{int((frame['Cpk'] < 1).sum())}개")
    m2.metric('주의 · Cpk 1.00–1.33 미만', f"{int(((frame['Cpk'] >= 1) & (frame['Cpk'] < 1.33)).sum())}개")
    m3.metric('기준 충족 · Cpk ≥ 1.33', f"{int((frame['Cpk'] >= 1.33).sum())}개")
    missing = int(frame['Cpk'].isna().sum())
    st.caption(f'전체 {len(frame)}개 항목 · Cpk 계산 불가 {missing}개')
    with st.expander('검색 · 표시 설정'):
        c1, c2 = st.columns(2)
        query = c1.text_input('항목 검색', key='dash_query')
        metric = c2.selectbox('평가 지표', ['Cpk', 'Ppk'], key='dash_metric')
        status = c1.selectbox('평가 범위', ['전체', '기준 미달 (<1.33)', '기준 충족 (≥1.33)', '계산 불가'], key='dash_status')
        count = c2.selectbox('최대 표시 항목', [10, 20, 50, 100], key='dash_simple_count')
        focus = st.checkbox('주요 구간 확대 (-1~3)', value=True, key='dash_simple_focus')
        compare = st.checkbox('Cpk / Ppk 비교', value=False, key='dash_simple_compare')
    selected = frame[frame['항목'].str.contains(query.strip(), case=False, regex=False)].copy()
    if status == '기준 미달 (<1.33)':
        selected = selected[selected[metric] < 1.33]
    elif status == '기준 충족 (≥1.33)':
        selected = selected[selected[metric] >= 1.33]
    elif status == '계산 불가':
        selected = selected[selected[metric].isna()]
    st.caption(f'현재 범위: {metric} · {status} · 검색: {query.strip() or "전체"} · {len(selected)}개 항목')
    if selected.empty:
        st.info('선택 조건에 해당하는 항목이 없습니다.')
        return
    st.markdown(f'#### {metric} 현황')
    chart_rows = selected.dropna(subset=[metric]).sort_values(metric, kind='stable').head(count)
    if not chart_rows.empty:
        st.caption(f'낮은 순서로 {len(chart_rows)}개 표시 · 빨강 < 1.00 / 주황 < 1.33 / 초록 ≥ 1.33')
        st.plotly_chart(capability_figure(chart_rows, metric, focus, compare, palette),
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
        st.caption('예측 PPM은 정규분포 가정에 따른 값이며, 관측 PPM은 실제 규격 이탈 비율입니다.')
        for name in ['예측 PPM', '관측 PPM']:
            ppm[name] = ppm[name].map(lambda v: '—' if pd.isna(v) else '< 0.001' if 0 < v < .001 else f'{v:,.3f}')
        st.dataframe(ppm[['항목', '예측 PPM', '관측 PPM']], hide_index=True, use_container_width=True)
    with st.expander('전체 상세 수치 · CSV 다운로드'):
        detail = selected.sort_values(metric, kind='stable', na_position='last')
        st.dataframe(detail, hide_index=True, use_container_width=True,
                     column_config={name: st.column_config.NumberColumn(format='%.3f')
                                    for name in ['Cpk', 'Ppk', '예측 PPM', '관측 PPM']})
        st.download_button('현재 목록 CSV 다운로드', detail.to_csv(index=False).encode('utf-8-sig'),
                           file_name='공정능력_대시보드.csv', mime='text/csv', key='dash_csv')

