"""Interactive dashboard for prioritising capability review."""
import html
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from spc_charts import PALETTES, DEFAULT_PALETTE
from spc_policy import limits, format_criteria, band, reference_only_decision
from spc_core import screening_index_pair, screening_index_band


def dashboard_rows(results):
    def number(value):
        return float(value) if value is not None and np.isfinite(value) else np.nan
    def target(it, metric):
        r = it.get('capability_criteria', {})
        if r.get('mode') != 'aiag_vda':
            return limits(it, metric)[1]
        decision = r.get('indices', {}).get(metric.lower(), {})
        return np.nan if reference_only_decision(decision) else number(decision.get('required'))
    frame = pd.DataFrame([
        {'항목': str(it['column']), 'Cpk': number(it.get('cpk')),
         '유효N': len(it.get('series', [])),
         'Ppk': number(it.get('ppk')), '예측 PPM': number(it.get('ppm_ppk')),
         '관측 PPM': number(it.get('emp_ppm')),
         '규격 이탈': it.get('spec_violation_count', 0),
         '능력 적용 상태': it.get('capability_status', '재분석 필요'),
         '능력 적용 사유': '; '.join(it.get('capability_reasons', [])),
         '자동 기준': it.get('capability_criteria', {}).get('mode') == 'aiag_vda',
         **{f'{metric} 요구값': target(it, metric) for metric in ('Cpk', 'Ppk')},
         **{f'{metric} 상태': band(it.get(metric.lower()), it, metric) for metric in ('Cpk', 'Ppk')},
         **{f'{metric} 판정': it.get('capability_criteria', {}).get('indices', {}).get(metric.lower(), {}).get('status', '') for metric in ('Cpk', 'Ppk')},
         '계산법': it.get('capability_method', 'normal'),
         '적용 분포': (it.get('distribution_model') or {}).get('name', '정규' if it.get('distribution_mode') != 'nonnormal' else '미확인')}
        for it in results
    ])
    selected = []
    for it in results:
        pair = screening_index_pair(it)
        metric = pair['shifted_metric']
        value = number(it.get(metric)) if metric else np.nan
        decision = it.get('capability_criteria', {}).get('indices', {}).get(metric, {})
        state = 'reference' if reference_only_decision(decision) else screening_index_band(it)
        selected.append({'지표': pair['shifted'].split(' ')[0] if metric else '—',
                         '평가 상태': pair['state'], '표시 지수':value,
                         '표시 지수 요구값':target(it, metric) if metric else np.nan,
                         '표시 지수 상태':state, '표시 지수 판정':decision.get('status', '')})
    return pd.concat([frame, pd.DataFrame(selected, columns=['지표','평가 상태','표시 지수','표시 지수 요구값','표시 지수 상태','표시 지수 판정'])], axis=1)


def capability_figure(frame, metric='표시 지수', focus=True, compare=False, palette=DEFAULT_PALETTE, assessment=None):
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
                marker_color=[colors['warning'] if row.get(f'{field} 판정') == 'conditional' else
                              {'bad':colors['danger'], 'warn':colors['warning'], 'good':colors['success']}.get(
                                  row.get(f'{field} 상태', 'bad' if row[field] < warning else 'warn' if row[field] < required else 'good'), colors['muted'])
                              for _, row in shown.iterrows()],
                text=[(str(row['지표']) + ' ' if field == '표시 지수' else '') + f'{row[field]:.3f}'
                      + (' →' if focus and row[field] > 3 else ' ←' if focus and row[field] < -1 else '') for _, row in shown.iterrows()],
                textfont=dict(size=14, color=ink),
                textposition='none', cliponaxis=False, width=.52,
                customdata=[[html.escape(str(row['항목'])), row[field], row.get(f'{field} 요구값', required), row.get(f'{field} 판정', ''),
                             html.escape(str(row.get('지표', field))) if field == '표시 지수' else field,
                             html.escape(str(row.get('평가 상태', ''))),
                             f"<br>요구값 {row.get(f'{field} 요구값', required):.3f}"
                             if pd.notna(row.get(f'{field} 요구값', required)) else ''] for _, row in shown.iterrows()],
                hovertemplate='<b>%{customdata[0]}</b><br>%{customdata[4]} %{customdata[1]:.4f}%{customdata[6]}<br>%{customdata[5]}<extra></extra>'))
            for position, category, label, value in zip(plotted, ids, fig.data[-1].text, values):
                fig.add_annotation(x=float(position), y=category, xref='x', yref='y', text=label,
                                   showarrow=False, xanchor='right' if value < 0 else 'left',
                                   xshift=-6 if value < 0 else 6, bgcolor=colors['ax_bg'], borderpad=2,
                                   font=dict(size=14, color=ink))
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
    if '자동 기준' in shown and shown['자동 기준'].any():
        targets = shown[f'{metric} 요구값'].to_numpy()
        fig.add_trace(go.Scatter(x=np.clip(targets, -1, 3) if focus else targets, y=ids,
            mode='markers', name=f'{metric} 항목별 요구값', marker=dict(symbol='line-ns',size=18,color=colors['warning']),
            customdata=targets, hovertemplate='조정 목표값 %{customdata:.3f}<extra></extra>'))
    else:
        fig.add_vline(x=warning, line_dash='dot', line_color=colors['danger'],
                      annotation_text=criteria_text('1.00'), annotation_position='top left')
        fig.add_vline(x=required, line_dash='dot', line_color=colors['warning'],
                      annotation_text=criteria_text('1.33'), annotation_position='top right')
    fig.update_layout(height=max(380, 40*len(shown)+110),
                      paper_bgcolor=colors['ax_bg'], plot_bgcolor=colors['ax_bg'],
                      font=dict(color=ink, size=15), margin=dict(l=20, r=145, t=45, b=45),
                      showlegend=compare, legend=dict(orientation='h', y=1.12), hovermode='closest')
    fig.update_yaxes(type='category', categoryorder='array', categoryarray=ids,
                     autorange='reversed', tickvals=ids,
                     ticktext=[html.escape(str(v)) for v in shown['항목']],
                     showgrid=False, tickfont=dict(size=14), automargin=True)
    fig.update_xaxes(title=('항목별 Cpk / Ppk' if metric == '표시 지수' else metric) + ' · 낮은 항목부터 표시', gridcolor=colors['grid'],
                     range=[-1.15, 3.2] if focus else None, zeroline=True)
    return fig


def render_dashboard(results, palette=DEFAULT_PALETTE):
    import streamlit as st
    if not results:
        st.info('분석 결과가 없습니다.')
        return
    metric = '표시 지수'
    frame = dashboard_rows(results)
    for column, (label, state) in zip(st.columns(5), [('표시 지수 기준 미달', 'bad'), ('주의·조건부 충족', 'warn'),
                                                    ('표시 지수 기준 충족', 'good'), ('자동 비교 미지원', 'reference'),
                                                    ('판정 보류·계산 불가', 'na')]):
        column.metric(label, f"{int((frame['표시 지수 상태'] == state).sum())}개")
    capability_count = int(frame['지표'].str.startswith('Cpk').sum())
    performance_count = int(frame['지표'].str.startswith('Ppk').sum())
    st.caption(f'전체 {len(frame)}개 항목 · Cpk {capability_count}개 · Ppk 참고 {performance_count}개 · 나머지는 계산 불가')
    st.caption('능력 적용 가능 시 Cpk, 관리상태·분포 확인이 필요하면 Ppk를 표시합니다. Ppk의 수치 기준 충족은 공정 안정성 충족을 뜻하지 않습니다. 막대에서 지표명, 마우스 설명에서 평가 상태를 확인하세요.')
    with st.expander('검색 · 표시 설정'):
        c1, c2 = st.columns(2)
        query = c1.text_input('항목 검색', key='dash_query')
        c2.caption('평가 지표: 항목별 Cpk / Ppk 자동 선택')
        status = c1.selectbox('평가 범위', ['전체', '기준 미달·주의', '기준 충족', '자동 비교 미지원', '판정 보류·계산 불가'], key='dash_status_auto')
        count = c2.selectbox('최대 표시 항목', [10, 20, 50, 100], key='dash_simple_count')
        focus = st.checkbox('주요 구간 확대 (-1~3)', value=True, key='dash_simple_focus')
    selected = frame[frame['항목'].str.contains(query.strip(), case=False, regex=False)].copy()
    if status == '기준 미달·주의':
        selected = selected[selected[f'{metric} 상태'].isin(['bad', 'warn'])]
    elif status == '기준 충족':
        selected = selected[selected[f'{metric} 상태'] == 'good']
    elif status == '판정 보류·계산 불가':
        selected = selected[selected[f'{metric} 상태'] == 'na']
    elif status == '자동 비교 미지원':
        selected = selected[selected[f'{metric} 상태'] == 'reference']
    st.caption(f'현재 범위: Cpk / Ppk 자동 · {status} · 검색: {query.strip() or "전체"} · {len(selected)}개 항목')
    if selected.empty:
        st.info('선택 조건에 해당하는 항목이 없습니다.')
        return
    st.markdown('#### 능력·성능 지수 현황')
    chart_rows = selected.dropna(subset=[metric]).sort_values(metric, kind='stable').head(count)
    if not chart_rows.empty:
        st.caption(f'낮은 순서로 {len(chart_rows)}개 표시 · 빨강: 미달 / 주황: 주의·조건부 / 초록: 수치 기준 충족 / 회색: 참고값 또는 판정 보류')
        st.plotly_chart(capability_figure(chart_rows, metric, focus, False, palette, results[0]),
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
        detail = detail[['항목','지표','표시 지수','평가 상태','유효N','표시 지수 요구값','표시 지수 상태',
                         '예측 PPM','관측 PPM','규격 이탈','능력 적용 사유','적용 분포','계산법']]
        detail['표시 지수 상태'] = detail['표시 지수 상태'].map({'bad':'미달', 'warn':'주의·조건부', 'good':'수치 기준 충족', 'reference':'참고값 · 자동 비교 미지원', 'na':'판정 보류'})
        st.dataframe(detail, hide_index=True, use_container_width=True,
                     column_config={name: st.column_config.NumberColumn(format='%.3f')
                                    for name in ['표시 지수', '표시 지수 요구값', '예측 PPM', '관측 PPM']})
        st.download_button('현재 목록 CSV 다운로드', detail.to_csv(index=False).encode('utf-8-sig'),
                           file_name='공정능력_대시보드.csv', mime='text/csv', key='dash_csv')

