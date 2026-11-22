"""Attribute analysis within the shared SPC Studio entry point."""

import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from attr_charts import build_attribute_control_chart, build_attribute_control_chart_plotly
from attr_core import (DEFAULT_BASELINE_SUBGROUPS, LIMIT_METHODS, auto_detect_columns, get_sheet_names,
                       load_data_df, run_attribute_analysis)
from attr_presentation import (PRESENTATION_CSS, build_attribute_report, core_metrics, detail_metrics,
                               metrics_html, render_result_table, result_frame, signal_summary)
from spc_access import education_mode, ensure_active_day
from spc_chart_guide import ATTRIBUTE_CHARTS, render_chart_guide, render_studio_header
from spc_export import fig_to_bytes


def accept_attribute_upload(state, name, raw):
    state["_attr_file"] = (name, raw) if raw else None
    state.pop('_attr_tutorial_example', None)
    for key in list(state):
        if key in ("_attr_sheets", "_attr_frame", "_attr_result") or key.startswith("_attr_input_"):
            del state[key]


def _on_upload():
    upload = st.session_state.get("_attr_upload")
    accept_attribute_upload(st.session_state, upload.name if upload else "", upload.getvalue() if upload else None)


def _load_limit_example():
    ensure_active_day()
    state = st.session_state
    state.pop('_attr_upload', None)
    frame = pd.DataFrame({'defects': [1] * 25 + [5, 6], 'sample_size': [100] * 27})
    accept_attribute_upload(state, 'SPC_교육_계수형_한계비교.csv', frame.to_csv(index=False).encode())
    state.update(_attr_chart='p', _attr_input_phase='phase2', _attr_input_baseline=25)


TUTORIAL_BASELINE = 20


def load_attribute_tutorial_sample(chart):
    ensure_active_day()
    state = st.session_state
    state.pop('_attr_upload', None)
    variable_exposure = chart in ('p', 'u')
    counts = ([1, 3, 2, 5] if variable_exposure else [1, 2, 3, 2]) * 5
    frame = pd.DataFrame({'defects': [*counts, 2, 18 if variable_exposure else 12]})
    if chart != 'c':
        sizes = {'p': [100, 200], 'np': [100, 100], 'u': [1, 2]}[chart]
        frame['sample_size'] = sizes * (len(frame) // 2)
    name = f'SPC_교육_{chart}_관리도_실습.csv'
    accept_attribute_upload(state, name, frame.to_csv(index=False).encode())
    state.update(_attr_chart=chart, _attr_tutorial_example=chart, _attr_input_phase='phase2')
    state[f'_attr_input_{chart}_defect'] = 'defects'
    if chart != 'c':
        state[f'_attr_input_{chart}_size'] = 'sample_size'


def _choose(label, options, key, *, default=None, empty_label="사용 안 함"):
    state = st.session_state
    if key not in state or state[key] not in options:
        state[key] = default if default in options else options[0]
    return st.selectbox(label, options, key=key,
                        format_func=lambda value: empty_label if value is None else str(value))


def _read_frame(raw):
    state = st.session_state
    if "_attr_sheets" not in state:
        state["_attr_sheets"] = get_sheet_names(raw)
    sheets = state["_attr_sheets"]
    sheet = _choose("계수형 데이터 시트", sheets, "_attr_input_sheet") if sheets else None
    cached = state.get("_attr_frame")
    if cached is None or cached[0] != sheet:
        cached = (sheet, load_data_df(raw, sheet_name=sheet))
        state["_attr_frame"] = cached
    return cached


def _analysis_options(frame, chart):
    state = st.session_state
    numeric = [column for column in frame.select_dtypes(include="number").columns]
    if not numeric:
        st.warning("파일에 숫자로 입력된 불량품 수 또는 결점 수 열이 필요합니다.")
        return None
    detected = auto_detect_columns(frame, chart)
    defect_label = "불량품 수 열" if chart in ("p", "np") else "결점 수 열"
    defect = _choose(defect_label, [None, *numeric], f"_attr_input_{chart}_defect",
                     default=detected["defect_count"], empty_label="선택하세요")
    denominator, fixed = None, None
    if chart != "c":
        unit = "검사단위" if chart == "u" else "검사수"
        denominator = _choose(f"{unit} 열", [None, *(col for col in numeric if col != defect)],
                              f"_attr_input_{chart}_size", default=detected["sample_size"],
                              empty_label="고정값 사용")
        if denominator is None:
            fixed = st.number_input(f"고정 {unit}", min_value=0.000001 if chart == "u" else 1,
                                    value=None, step=0.1 if chart == "u" else 1,
                                    key=f"_attr_input_{chart}_fixed")
    labels = [None, *(col for col in frame.columns if col not in (defect, denominator))]
    date = _choose("날짜·로트 열 (선택)", labels, "_attr_input_label")
    tutorial = bool(state.get('_attr_tutorial_example'))
    default_baseline = TUTORIAL_BASELINE if tutorial else DEFAULT_BASELINE_SUBGROUPS
    default_rules = ['T1'] if tutorial else ['T1', 'T2', 'T3', 'T4']
    with st.expander("계수형 분석 설정"):
        limit_method = st.radio("관리한계 계산", tuple(LIMIT_METHODS), format_func=LIMIT_METHODS.get,
                                key="_attr_input_limit_method",
                                help="직접 계산은 이항분포(p·np) 또는 포아송분포(c·u)를 사용합니다. 정규근사는 비교에 사용할 수 있습니다.")
        phase = st.radio("운영 모드", ("phase1", "phase2"), key="_attr_input_phase",
                         format_func={"phase1": "해석용 (Phase 1)", "phase2": "관리용 (Phase 2)"}.get)
        baseline = st.number_input("초기 기준 부분군 수", min_value=1,
                                   value='min' if '_attr_input_baseline' in state else default_baseline,
                                   help="파일 순서대로 처음 지정한 개수의 유효 부분군으로 관리한계를 계산합니다. 이후 부분군은 감시에 사용합니다.",
                                   key="_attr_input_baseline") if phase == "phase2" else None
        opportunities = st.number_input("검사단위당 결점 기회수 (선택)", min_value=1, value=None,
                                        key="_attr_input_opportunities") if chart in ("c", "u") else None
        rules = st.multiselect("이상 신호 규칙", ("T1", "T2", "T3", "T4"),
                               default=None if '_attr_input_rules' in state else default_rules,
                               key="_attr_input_rules")
    if defect is None or (chart != "c" and denominator is None and fixed is None):
        st.info("계수 열과 필요한 검사수 또는 검사단위를 지정하세요.")
        return None
    return dict(chart_type=chart, defect_col=defect, sample_size_col=denominator, fixed_sample_size=fixed,
                chart_usage_mode=phase, baseline_subgroups=baseline, opportunities=opportunities,
                measurement_date_column=date or "", limit_method=limit_method,
                ksq_toggles={rule: rule in rules for rule in ("T1", "T2", "T3", "T4")})


def _render_result(result, filename, palette):
    chart = result["chart_type"]
    name = ATTRIBUTE_CHARTS[chart].name
    st.subheader(name)
    st.caption(f"{result['control_phase_label']} · {result['limit_reference_label']} · {signal_summary(result)['text']}")
    st.caption(result["limit_method_label"])
    if result.get('excluded_missing_groups'):
        st.caption(f"결측 부분군 {result['excluded_missing_groups']:,}개 제외 · T2~T4는 결측 앞뒤를 이어서 계산하지 않습니다.")
    if warning := result.get('capability', {}).get('opportunity_warning'):
        st.warning(warning)
    st.markdown(f"<style>{PRESENTATION_CSS}</style>" + metrics_html(core_metrics(result)), unsafe_allow_html=True)
    st.plotly_chart(build_attribute_control_chart_plotly(result, palette), theme=None,
                    config={"scrollZoom": False, "displaylogo": False}, key="_attr_chart_plot")
    if chart == st.session_state.get('_attr_tutorial_example'):
        _render_tutorial_exercise(result)
    with st.expander("부분군별 결과", expanded=True):
        render_result_table(result)
    with st.expander("품질성과 상세"):
        st.markdown(metrics_html(detail_metrics(result)), unsafe_allow_html=True)
    with st.expander("내보내기"):
        st.download_button("부분군 결과 CSV", result_frame(result).to_csv(index=False).encode("utf-8-sig"),
                           f"{chart}-관리도.csv", "text/csv", on_click="ignore")
        if st.button("관리도 PNG·HTML 생성", key="_attr_build_report"):
            figure = build_attribute_control_chart(result, palette)
            try:
                png = fig_to_bytes(figure)
            finally:
                plt.close(figure)
            html = build_attribute_report(result, result["capability"], filename=filename,
                                          control_image=(name, "image/png", png))
            st.download_button("관리도 PNG", png, f"{chart}-관리도.png", "image/png", on_click="ignore")
            st.download_button("HTML 보고서", html, f"{chart}-관리도.html", "text/html", on_click="ignore")


def _render_tutorial_exercise(result):
    chart = result['chart_type']
    st.subheader('실습 · 관리도 읽기')
    if result['control_phase'] == 'phase2' and result['baseline_display_count'] < result['subgroup_count']:
        st.write('감시구간에서 어느 부분군이 관리한계를 벗어났나요?')
        first = result['baseline_display_count']
    else:
        st.write('어느 부분군이 관리한계를 벗어났나요?')
        first = 0
    exposure = '검사수' if chart in ('p', 'np') else '검사량'
    st.write(f'부분군별 {exposure}와 관리상한(UCL)을 비교해 보세요. 어떤 관계가 있나요?' if chart in ('p', 'np')
             else '부분군별 검사량과 관리상한(UCL)을 비교해 보세요. 어떤 관계가 있나요?')
    with st.expander('실습 정답 확인'):
        indices = sorted(i for i in result['violations']['T1'] if i >= first)
        if indices:
            groups = ', '.join(str(i + 1) for i in indices)
            st.write(f'부분군 {groups}에서 관리한계 이탈(T1)이 감지됐습니다.')
        else:
            st.write('현재 설정에서 감지된 T1 신호가 없습니다. 실습 설정의 이상 신호 규칙에서 T1을 확인하세요.')
        if first + 1 < result['subgroup_count']:
            rows = result_frame(result).iloc[first:first + 2]
            columns = ['부분군', '검사수 (n)' if chart in ('p', 'np') else '검사단위', 'UCL']
            st.dataframe(rows[columns], hide_index=True, width='stretch')
        if chart in ('p', 'u'):
            st.caption(f'부분군별 {exposure}에 따라 관리상한이 달라질 수 있습니다. 각 점은 해당 부분군의 관리한계와 비교합니다.')
        else:
            st.caption(f'이 예제에서는 부분군별 {exposure}에 차이가 없어 같은 관리상한을 사용합니다.')


def render_attribute_studio():
    state = st.session_state
    chart = state.get("_attr_chart")
    upload = state.get("_attr_file")
    result = None
    show_guide = False
    ready = False
    guide_run = state.pop('_guide_attribute_run', False)
    with st.sidebar:
        st.subheader("계수형 분석 설정")
        if chart:
            st.caption(ATTRIBUTE_CHARTS[chart].name)
        guide_slot = st.empty()
        if education_mode():
            st.button('계수형 한계 비교 예제 불러오기', on_click=_load_limit_example, width='stretch',
                      help='기준 부분군 25개를 고정하고 p 관리도의 직접 계산과 정규근사를 비교하세요.')
        st.file_uploader("계수형 Excel / CSV 업로드", type=["xlsx", "xls", "csv"],
                         key="_attr_upload", on_change=_on_upload)
        if upload:
            filename, raw = upload
            st.caption(f"현재 파일 · {filename}")
            try:
                sheet, frame = _read_frame(raw)
            except Exception as exc:
                st.error(f"파일을 읽을 수 없습니다. {exc}")
            else:
                if chart:
                    options = _analysis_options(frame, chart)
                    if options is not None:
                        ready = True
                        signature = (sheet, options)
                        if st.button("▶ 계수형 분석 시작", type="primary", width="stretch",
                                     key="_attr_run") or guide_run:
                            state.pop("_attr_result", None)
                            try:
                                result = run_attribute_analysis(frame, **options)
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                state["_attr_result"] = (signature, result)
                        previous = state.get("_attr_result")
                        if previous is not None and previous[0] == signature:
                            result = previous[1]
        if result is not None:
            show_guide = guide_slot.toggle("관리도 선택 가이드 열기", key="_attr_show_guide")
    render_studio_header()
    if result is None or show_guide:
        render_chart_guide(attribute_ready=ready)
    if result is not None:
        _render_result(result, upload[0], state.get("palette", "trust"))
