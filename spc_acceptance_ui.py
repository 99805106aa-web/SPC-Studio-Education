"""Interactive X-bar acceptance control chart lesson."""

import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import norm
import streamlit as st

from spc_charts import DEFAULT_PALETTE, PALETTES, control_chart_layout, control_chart_limits, control_chart_phase
from spc_core import load_data_df, s_chart_probability_limits


LSL, USL = 9.900, 10.100
TARGET = (LSL + USL) / 2
BASELINE_COUNT = 8
SUBGROUP_SIZE = 5
MEASUREMENT_NAME = '가공 직경 (mm)'
WITHIN_SIGMA_FRACTION = 0.10
S_TAIL_PROBABILITY = 0.00135
MEASUREMENT_COLUMNS = [f'측정 {i} (mm)' for i in range(1, SUBGROUP_SIZE + 1)]


def example_measurements():
    means = np.array([9.998, 10.003, 9.996, 10.002, 10.001, 9.997, 10.004, 9.999,
                      10.007, 10.014, 10.024, 10.034, 10.046, 10.056, 10.066, 10.076,
                      10.008, 10.019])
    scales = np.array([.91, 1.04, .96, 1.12, .90, 1.07, .99, 1.01, .93,
                       1.09, .97, 1.05, .89, 1.12, .95, 1.03, 1.01, .94])
    return np.round(means[:, None] + scales[:, None] * np.array([-.011, -.005, 0, .005, .011]), 4)


def read_acceptance_measurements(raw, sheet):
    frame = load_data_df(raw, sheet)
    values = frame[MEASUREMENT_NAME].to_numpy(dtype=float)
    if (values.size % SUBGROUP_SIZE or values.size <= BASELINE_COUNT * SUBGROUP_SIZE
            or not np.isfinite(values).all()):
        raise ValueError('완전한 부분군과 감시 구간이 필요합니다. 공구 마모 예제를 다시 불러오세요.')
    return values.reshape(-1, SUBGROUP_SIZE)


def _acceptance_limits(sigma, n, p, intervention_probability):
    k = norm.isf(p) + norm.ppf(intervention_probability) / np.sqrt(n)
    return k, LSL + k * sigma, USL - k * sigma


def _add_practice_panel(fig, colors, row, groups, values, signals, limits, y_title):
    hover = f'<b>부분군 %{{x}}</b><br>{y_title} %{{y:.5f}} mm<extra></extra>'
    fig.add_trace(go.Scatter(x=groups, y=values, mode='lines+markers', name='관측값',
                             showlegend=row == 1, legendgroup='values',
                             line=dict(color=colors['primary'], width=2.2), marker=dict(size=7),
                             hovertemplate=hover), row=row, col=1)
    fig.add_trace(go.Scatter(x=groups[signals], y=values[signals], mode='markers', name='관리한계 이탈',
                             showlegend=row == 1, legendgroup='signals',
                             marker=dict(color=colors['danger'], size=12, line=dict(color=colors['ax_bg'], width=1.2)),
                             hovertemplate=hover), row=row, col=1)
    control_chart_phase(fig, colors, .5, groups[-1] + .5, BASELINE_COUNT + .5, row=row, labels=row == 1)
    control_chart_limits(fig, colors, values, limits, row=row, formatter=lambda value: f'{value:.5f}')
    fig.update_yaxes(title_text=f'{y_title} (mm)', row=row, col=1)


def _probability_chart(p, pa, sigma, n, upper, colors):
    mean = USL - norm.isf(p) * sigma
    fig = make_subplots(rows=2, cols=1, vertical_spacing=.2, shared_xaxes=True,
                        subplot_titles=('개별 제품 직경', f'{n}개를 뽑아 계산한 부분군 평균'))
    axis = np.linspace(mean - 4.5 * sigma, mean + 4.5 * sigma, 400)
    for row, (scale, boundary, label, color) in enumerate([
        (sigma, USL, f'p = {p:.2%}', colors['usl']),
        (sigma / np.sqrt(n), upper, f'Pₐ = {pa:.2%}', colors['primary']),
    ], start=1):
        shaded = np.linspace(boundary, axis[-1], 250)
        fig.add_trace(go.Scatter(x=axis, y=norm.pdf(axis, loc=mean, scale=scale), mode='lines',
                                 line=dict(color=colors['muted']), hoverinfo='skip'), row=row, col=1)
        fig.add_trace(go.Scatter(x=shaded, y=norm.pdf(shaded, loc=mean, scale=scale), mode='lines',
                                 fill='tozeroy', line=dict(color=color), opacity=.6, name=label,
                                 hoverinfo='skip'), row=row, col=1)
        fig.add_vline(x=boundary, line_dash='dash', line_color=color, row=row, col=1)
        fig.add_annotation(x=boundary, y=.94, yref='y domain', text=label, xanchor='left',
                           xshift=6, showarrow=False, font=dict(color=color), row=row, col=1)
    control_chart_layout(fig, colors, '<b>개별 제품과 부분군 평균의 확률</b>', height=400, top=74, right=30, bottom=52)
    fig.update_yaxes(showticklabels=False, rangemode='tozero', showgrid=False)
    fig.update_xaxes(title_text='직경 (mm)', row=2, col=1)
    fig.update_layout(showlegend=False)
    return fig


def _example_workbook(measurements, settings, results):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        measurements.to_excel(writer, sheet_name='예제 측정값', index=False)
        pd.DataFrame(settings, columns=['항목', '값']).to_excel(writer, sheet_name='계산 조건', index=False)
        results.to_excel(writer, sheet_name='부분군별 결과', index=False)
    return output.getvalue()


def render_acceptance_tutorial():
    st.markdown(
        '**언제 쓰나요?** 공구 마모로 평균이 서서히 움직이는 등 예상된 이동을 허용하면서, '
        '제품 규격과 허용 부적합률로 공정에 개입할 시점을 정할 때 검토합니다.\n\n'
        '**무엇을 준비하나요?** 같은 조건에서 함께 잰 측정값, 제품의 상·하한 규격, 경계별 허용 부적합률과 개입 확률이 필요합니다.\n\n'
        '**그래프를 어떻게 읽나요?** S에서 같은 묶음 안의 산포를 먼저 확인합니다. 그다음 X̄에서 부분군 평균이 판정한계를 넘는지 봅니다.\n\n'
        '**간단한 예** · 공구 마모 실습에서는 직경 5개를 한 묶음으로 봅니다. 기본 설정에서 16번 묶음은 개별 값이 모두 '
        '규격 안에 있어도 평균이 판정 상한을 넘습니다.'
    )
    st.markdown(
        '#### 적용 기준\n'
        '- **허용 부적합률을 정할 수 있는 특성**에 적용합니다. 고객 요구사항과 품질 기준에 따라 '
        '경계별 허용률 p를 정합니다. 실습의 1.00%는 한쪽 규격 경계에 대한 예시입니다.\n'
        '- **군내 산포가 안정적이고 공정능력이 충분히 높아야** 합니다. S 또는 R 관리도로 산포의 '
        '안정성을 확인합니다. AIAG–VDA 매뉴얼은 군내 표준편차 σ̂가 **공차 폭(USL − LSL)의 '
        '1/10 미만**인 경우를 일반적인 적용 조건으로 제시합니다.\n'
        '- **공정 평균의 일정한 이동을 허용할 수 있는 경우**에 사용합니다. 공구 마모에 따른 비핵심 '
        '가공 치수의 이동이 예입니다. 이 실습의 X̄ 계산은 정규분포를 가정합니다.'
    )
    st.warning('**고객이 해당 특성에 무결점을 요구하면 합격판정 관리도를 적용할 수 없습니다.** '
               '이 관리도는 허용 부적합률을 전제로 개입 시점을 판단합니다. '
               '무결점 요구 특성의 제품 적합 여부는 고객과 합의한 검사·출하 기준으로 판단하세요.')

    st.markdown(
        '#### 1%와 99%는 각각 무엇을 뜻하나요?\n'
        f'공구 마모 예제의 제품 직경 규격은 **{TARGET:.3f} ± {USL - TARGET:.3f} mm**입니다. '
        f'규격 하한(LSL)은 **{LSL:.3f} mm**, 규격 상한(USL)은 **{USL:.3f} mm**이며, '
        f'두 규격 사이의 공차 폭은 **{USL - LSL:.3f} mm**입니다.\n\n'
        '- **경계별 허용 부적합률** p = 1%는 개별 제품의 규격 이탈률 기준입니다. 공구 마모로 직경이 커질 때 '
        f'가공품의 1%가 규격 상한인 {USL:.3f} mm를 넘는 공정 상태를 가정합니다.\n'
        '- **개입 확률** Pₐ = 99%는 그 상태에서 5개씩 표본을 뽑았을 때, 부분군 평균이 판정 상한을 넘을 '
        '확률입니다. 같은 조건에서 반복하면 장기적으로 부분군 100개 중 약 99개가 개입 신호를 냅니다.\n'
        '- **보상계수 kₐ**는 이 감지 확률에 맞춰 X̄ 판정 상한을 규격 상한에서 얼마나 안쪽에 둘지 '
        '정합니다. 신호가 없는 동안에도 규격을 벗어난 제품이 생산될 수 있습니다.'
    )

    with st.expander('계산 방법'):
        st.markdown(
            '1. 기준 부분군의 표본분산($s_i^2$)을 평균하고 제곱근을 취해 군내 표준편차를 구합니다. '
            '감시 구간에도 이 기준 산포를 적용합니다.\n'
            '2. 규격 경계별 허용 부적합률 $p$와 개입 확률 $P_A$를 정합니다. 해당 경계의 부적합률이 '
            '$p$에 이르는 평균에서, 그쪽 판정한계를 넘을 확률이 $P_A$가 되도록 한계를 설정합니다.\n'
            '3. S 관리도로 산포를 먼저 확인하고, 부분군 평균이 X̄ 판정한계를 벗어나면 공구 마모나 '
            '셋업 상태를 점검합니다. 산포 이탈 시 판정한계의 전제를 다시 확인합니다.'
        )
        st.latex(r'\hat\sigma=\sqrt{\overline{s^2}},\qquad k_A=\Phi^{-1}(1-p)+\frac{\Phi^{-1}(P_A)}{\sqrt{n}}')
        st.latex(r'LCL_A=LSL+k_A\hat\sigma,\qquad UCL_A=USL-k_A\hat\sigma')
        st.caption('매뉴얼 9.3.4절, 127–129쪽. 실습은 양측 규격에 동일한 경계별 p와 개입 확률을 적용합니다.')


def render_acceptance_results(values, palette=DEFAULT_PALETTE):
    st.subheader('합격판정 관리도 · 공구 마모 실습 결과')
    colors = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    group_count, n = values.shape
    groups = np.arange(1, group_count + 1)
    monitoring = groups > BASELINE_COUNT
    means = values.mean(axis=1)
    deviations = values.std(axis=1, ddof=1)
    baseline = deviations[:BASELINE_COUNT]
    sigma = np.sqrt(np.mean(baseline**2))
    tolerance = USL - LSL
    sigma_threshold = tolerance * WITHIN_SIGMA_FRACTION
    sigma_status = '충족' if sigma < sigma_threshold else '미충족'

    st.write(f'직경 규격은 **{LSL:.3f}–{USL:.3f} mm**, 부분군당 **{n}개**입니다. '
             f'1–{BASELINE_COUNT}번 부분군으로 산포 기준을 정하고 그다음 부분군을 감시합니다.')
    left, right = st.columns(2)
    p_pct = left.number_input('경계별 허용 부적합률 p (%)', min_value=0.01, max_value=10.0,
                              value=1.0, step=0.1, format='%.2f', key='acceptance_p',
                              help='개별 제품이 한쪽 규격을 벗어나는 비율의 기준입니다.')
    pa_pct = right.number_input('개입 확률 Pₐ (%)', min_value=50.0, max_value=99.99,
                                value=99.0, step=0.1, format='%.2f', key='acceptance_pa',
                                help='실제 이탈률이 p에 도달했을 때 한 부분군의 평균이 신호를 낼 확률입니다.')
    k, lower, upper = _acceptance_limits(sigma, n, p_pct / 100, pa_pct / 100)

    s_center = baseline.mean()
    x_center = means[:BASELINE_COUNT].mean()
    s_lower, s_upper = s_chart_probability_limits(sigma, n, S_TAIL_PROBABILITY)
    s_signals = monitoring & ((deviations < s_lower) | (deviations > s_upper))
    mean_signals = monitoring & ((means < lower) | (means > upper))

    c1, c2, c3 = st.columns(3)
    c1.metric('보상계수 kₐ', f'{k:.4f}', border=True)
    c2.metric('판정 하한 LCL', f'{lower:.5f} mm', border=True)
    c3.metric('판정 상한 UCL', f'{upper:.5f} mm', border=True)
    st.caption('음영은 기준구간입니다. 아래쪽 S에서 산포를 먼저 확인한 뒤 위쪽 X̄의 평균 이탈을 읽으세요.')
    chart = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.14,
                          subplot_titles=('X̄ 합격판정 관리도', 'S 관리도 · 99.73% 확률한계'))
    control_chart_layout(chart, colors, '<b>합격판정 관리도</b>', height=640, top=106, right=128)
    for annotation in chart.layout.annotations:
        annotation.update(x=0, xanchor='left', font=dict(size=15, color=chart.layout.font.color))
    _add_practice_panel(chart, colors, 1, groups, means, mean_signals, [
        ('USL', USL), ('UCL', upper), ('CL', x_center), ('LCL', lower), ('LSL', LSL),
    ], '부분군 평균')
    _add_practice_panel(chart, colors, 2, groups, deviations, s_signals, [
        ('UCL', s_upper), ('CL', s_center), ('LCL', s_lower),
    ], '부분군 표준편차')
    chart.update_xaxes(dtick=1, range=[.5, groups[-1] + .5])
    chart.update_xaxes(title_text='부분군', row=2, col=1)
    st.plotly_chart(chart, key='acceptance_control_charts', theme=None,
                    config={'scrollZoom': False, 'displaylogo': False})
    st.caption('USL·LSL은 개별 제품 규격입니다. 파선 UCL·LCL은 관리한계이며, 점선 CL은 기준구간의 중심선입니다.')
    st.caption(f'**예제의 산포 크기 기준 {sigma_status}** · 군내 σ̂ {sigma:.5f} mm는 공차 폭 '
               f'{tolerance:.3f} mm의 {sigma / tolerance:.2%}입니다. '
               f'기준은 {sigma_threshold:.5f} mm 미만이며, 감시 구간의 S 한계 이탈은 {s_signals.sum()}개입니다.')
    with st.expander('규격을 넘는 p와 관리한계를 넘는 Pₐ 보기'):
        st.write('상한 쪽을 예로 들어, 실제 규격 이탈률이 p에 도달한 공정 상태를 그렸습니다. '
                 '위 그림의 색칠한 부분은 개별 제품이 USL을 넘을 확률이고, 아래는 부분군 평균이 UCL을 넘을 확률입니다.')
        st.plotly_chart(_probability_chart(p_pct / 100, pa_pct / 100, sigma, n, upper, colors),
                        key='acceptance_probability_chart', theme=None, config={'displayModeBar': False})

    individual_violations = ((values < LSL) | (values > USL)).sum(axis=1)
    flagged_groups = ', '.join(map(str, groups[mean_signals]))
    if flagged_groups:
        st.warning(f'평균 판정한계 이탈 부분군은 {flagged_groups}번입니다. '
                   f'같은 감시 구간에서 개별 측정값의 규격 이탈은 {individual_violations[monitoring].sum()}개입니다.')
    else:
        st.success('현재 설정에서 감시 구간의 부분군 평균은 모두 판정한계 이내입니다.')
    st.caption('판정한계 이내는 이 표본에서 개입 신호가 없었다는 뜻입니다. 공정 안정성, 개별 제품의 적합성, '
               '출하 승인 여부는 각각 별도로 평가합니다.')

    measurements = pd.DataFrame(values, columns=MEASUREMENT_COLUMNS)
    measurements.insert(0, '부분군', groups)
    results = pd.DataFrame({
        '부분군': groups, '구간': np.where(monitoring, '감시', '기준'),
        '평균 (mm)': means, '표준편차 (mm)': deviations,
        'S 신호': np.where(monitoring, np.where(s_signals, '이탈', '이내'), '기준'),
        'X̄ 판정': np.where(monitoring, np.where(mean_signals, '이탈', '이내'), '기준'),
        '개별 규격 이탈 수': individual_violations,
    })
    with st.expander('부분군별 결과와 원자료'):
        st.dataframe(results.style.format({'평균 (mm)': '{:.5f}', '표준편차 (mm)': '{:.5f}'}),
                     hide_index=True)
        st.dataframe(measurements, hide_index=True)
    st.write('p를 1.00%에서 0.10%로 낮추고 Pₐ를 99.00%로 두세요. '
             '판정한계와 이탈 부분군이 어떻게 바뀌는지 확인하세요.')
    with st.expander('실습 정답 확인'):
        st.write('p가 작아지면 보상계수 kₐ가 커져 X̄ 판정한계가 좁아집니다. '
                 '기본 설정의 16번에 더해 15번도 이탈합니다. S 관리도 한계는 그대로입니다.')
    settings = [
        ('LSL (mm)', LSL), ('USL (mm)', USL), ('부분군 크기', n), ('기준 부분군 수', BASELINE_COUNT),
        ('경계별 p (%)', p_pct), ('Pₐ (%)', pa_pct), ('군내 σ̂ (mm)', sigma), ('kₐ', k),
        ('LCLₐ (mm)', lower), ('X̄ CL (mm)', x_center), ('UCLₐ (mm)', upper), ('S 하한 (mm)', s_lower),
        ('기준 S̄ (mm)', s_center), ('S 상한 (mm)', s_upper),
    ]
    st.download_button('합격판정 관리도 예제 Excel 내려받기',
                       _example_workbook(measurements, settings, results), '합격판정_관리도_예제.xlsx',
                       'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                       key='acceptance_workbook', on_click='ignore')
