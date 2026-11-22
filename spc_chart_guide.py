"""Control chart selection, practice examples, and pre-analysis input checks."""

from typing import NamedTuple

import streamlit as st

from spc_extended import EXTENDED_MODE
from spc_file_ui import invalidate_analysis


IMR_WIDE_GUIDANCE = ('현재 파일은 한 행을 부분군으로 사용합니다. I–MR을 사용하려면 시간순 개별 측정값 한 열을 '
                     '준비하고 사이드바에서 데이터 구조를 세로형으로 지정하세요.')


# Each entry gives the display name, allowed group sizes, use case, inputs and plotted values.
CHARTS = {
    "imr": (
        "I-MR", range(1, 2), "시점마다 한 번씩 측정할 때",
        "측정값 한 열을 시간순으로 정렬합니다.",
        "개별 측정값과 연속 두 측정값의 이동범위를 확인합니다.",
    ),
    "xbar_r": (
        "X̄-R", range(2, 10), "같은 조건에서 2–9개씩 측정할 때",
        "측정값을 한 열에 부분군 순서대로 놓거나, 한 행에 같은 부분군의 측정값을 나란히 배치합니다.",
        "부분군별 평균과 범위(최댓값−최솟값)를 확인합니다.",
    ),
    "xbar_s": (
        "X̄-S", range(2, 26), "같은 조건에서 2–25개씩 측정할 때",
        "측정값을 한 열에 부분군 순서대로 놓거나, 한 행에 같은 부분군의 측정값을 나란히 배치합니다.",
        "부분군별 평균과 표준편차를 확인합니다. n=5 자료로 X̄-R과 비교할 수 있습니다.",
    ),
    EXTENDED_MODE: (
        "확장 슈하르트 X̄-S", range(2, 26), "공구 교체 등으로 예상되는 평균 이동을 고려할 때",
        "원단위의 완전한 부분군(2–25개씩)을 준비합니다. 범위를 자동 산정하려면 유효 기준 부분군이 6개 이상 필요합니다.",
        "예상 평균 이동을 반영한 확장 X̄와 부분군 내 표준편차(S)를 확인합니다. 일반 X̄ 기준과 비교할 수도 있습니다.",
    ),
}

class AttributeChart(NamedTuple):
    name: str
    use_case: str
    plotted: str
    inputs: str
    interpretation: str


ATTRIBUTE_CHARTS = {
    "p": AttributeChart("p 불량률 관리도", "검사수가 다르거나 불량률로 비교할 때",
                        "불량품 수 ÷ 검사수", "부분군별 불량품 수와 검사수. 검사수가 일정하면 고정 검사수를 입력합니다.",
                        "검사수가 달라지면 부분군별 관리한계가 달라질 수 있습니다. 검사수가 일정하면 같은 자료의 p와 np는 비율과 개수로 표현할 수 있습니다."),
    "np": AttributeChart("np 불량품 수 관리도", "검사수가 같고 불량품 개수를 직접 보고 싶을 때",
                         "불량품 수 · 검사수 고정", "부분군별 불량품 수와 공통 검사수. 같은 검사수를 기록한 열도 사용할 수 있습니다.",
                         "총 검사수가 같아야 불량품 개수를 그대로 비교할 수 있습니다. 한 제품에서 결점이 여러 개 발견돼도 불량품은 1개로 셉니다."),
    "c": AttributeChart("c 결점 수 관리도", "같은 검사량에서 발견된 결점 개수를 비교할 때",
                        "결점 수 · 검사량 고정", "동일한 검사 면적·길이·제품 개수에서 센 부분군별 결점 수.",
                        "같은 검사단위와 검사 기준을 유지해야 합니다. 검사하는 면적이나 제품 수가 늘어나는 날은 결점 개수가 늘어날 기회도 늘어납니다."),
    "u": AttributeChart("u 단위당 결점 수 관리도", "면적·길이·검사 개수가 다른 부분군을 비교할 때",
                        "결점 수 ÷ 검사단위", "부분군별 결점 수와 검사량. 예를 들어 면적은 모두 ㎡, 길이는 모두 m로 기록합니다.",
                        "같은 단위당 결점 수를 비교합니다. 검사량이 다른 부분군은 단위당 결점 수가 같아도 관리한계가 다를 수 있습니다."),
}

ATTRIBUTE_PRACTICE = {
    "p": ("검사수가 다른 두 로트", "A 로트는 100개 중 5개, B 로트는 200개 중 5개가 불량입니다. 로트별 불량률을 어떻게 비교할까요?",
          "p 관리도에서 A는 5%, B는 2.5%입니다. 불량품 수가 같아도 검사수가 달라 비율이 다릅니다."),
    "np": ("매일 100개씩 검사", "매일 제품 100개를 검사합니다. 첫날 5개, 다음날 2개가 불량이었습니다. 불량품 개수를 그대로 표시하려면?",
           "np 관리도에서 5개와 2개를 그대로 비교합니다. 검사수는 매일 100개입니다. 비율로 보여주려면 p 관리도를 사용할 수도 있습니다."),
    "c": ("같은 면적의 판재 검사", "매번 2㎡씩 검사해 흠집 3개와 5개를 발견했습니다. 같은 검사량에서 흠집 개수를 비교하려면?",
          "c 관리도에서 3개와 5개를 비교합니다. 검사 면적이 매번 2㎡로 같습니다."),
    "u": ("다른 면적의 판재 검사", "A 판재는 1㎡에 흠집 3개, B 판재는 2㎡에 흠집 4개입니다. 어느 판재의 흠집 밀도가 더 높을까요?",
          "u 관리도에서 A는 3개/㎡, B는 2개/㎡입니다. 흠집이 3개인 A의 단위 면적당 흠집 수가 더 높습니다."),
}

VARIABLE_PRACTICE = {
    "imr": ("한 시간에 하나씩 측정", "한 시간에 제품 하나의 길이만 측정했습니다. 처음 두 값은 10.0 mm와 10.3 mm입니다. 어떤 관리도를 사용할까요?",
            "I–MR을 사용합니다. I는 각 측정값을, MR은 인접한 두 값의 차이인 0.3 mm를 표시합니다."),
    "xbar_r": ("부분군 5개의 최대·최소 차이", "같은 시점에 잰 5개 값이 9, 9, 10, 11, 11 mm입니다. 부분군 평균과 최댓값−최솟값을 표시하려면?",
               "X̄–R을 사용합니다. 이 부분군의 평균은 10 mm, 범위는 2 mm입니다."),
    "xbar_s": ("같은 5개의 표준편차", "같은 자료 9, 9, 10, 11, 11 mm의 평균과 표준편차를 표시하려면?",
               "X̄–S를 사용합니다. 평균은 10 mm, 표본표준편차는 1 mm입니다. 같은 n=5 자료를 X̄–R과 비교할 수 있습니다."),
    EXTENDED_MODE: ("공구 마모에 따른 평균 이동", "공구 수명 주기마다 평균이 일정한 범위에서 반복해 움직이고, 부분군 내 산포는 안정적입니다. 예상 이동 범위를 반영하면서 그 밖의 변화를 감시하려면?",
                    "확장 슈하르트 X̄–S를 사용합니다. S의 안정성을 먼저 확인하고, 확장 X̄에서 예상 이동을 반영한 관리한계 이탈을 봅니다."),
}

VARIABLE_NOTES = {
    "imr": "이동범위는 원본에서 서로 인접한 유효 관측값으로 계산합니다. 결측으로 끊긴 구간을 연결하지 않습니다.",
    "xbar_r": "범위는 두 극단값의 차이입니다. n=5에서는 같은 자료의 R과 S를 각각 그려 산포를 보는 방법을 비교할 수 있습니다.",
    "xbar_s": "S는 부분군의 모든 값을 사용합니다. S 관리도에서 산포가 안정적인지 확인한 뒤 X̄ 관리도의 평균 이동을 해석합니다.",
    EXTENDED_MODE: "기준구간에서 예상 평균 범위를 정하고 감시구간에서 적용합니다. 녹색 예상 범위와 실제 관리한계는 각각의 경계로 표시됩니다.",
}

TUTORIAL_QUESTIONS = {
    'variable_start': ('한 시점에 몇 개를 함께 측정하나요?',
                       '부분군은 같은 시점과 조건에서 함께 측정한 값의 묶음입니다.',
                       (('시점마다 한 개씩 측정', 'single_distribution'), ('같은 조건에서 여러 개씩 측정', 'group_size'))),
    'single_distribution': ('개별 측정값의 분포는 어떤가요?',
                            '한쪽 꼬리가 긴 데이터나 자연하한에 몰린 데이터는 정규분포 가정을 먼저 점검합니다.',
                            (('정규에 가깝거나 아직 미확인', 'imr'), ('비대칭·비정규가 의심됨', 'pearson'))),
    'subgroup_distribution': ('부분군 평균의 분포는 어떤가요?',
                              '개별값과 부분군 평균의 분포는 다를 수 있습니다. 위치 관리도에 타점할 부분군 평균의 형태를 기준으로 답하세요.',
                              (('정규에 가깝거나 아직 미확인', 'shift'), ('비대칭·비정규가 의심됨', 'pearson'))),
    'shift': ('예상되는 평균 이동을 관리한계에 반영하나요?',
              '공구 마모처럼 반복되는 이동 범위를 미리 확인했고, 부분군 내 산포가 안정적일 때 확장 슈하르트를 고려합니다.',
              (('일반적인 평균 변화를 감시', 'dispersion'), ('확인된 반복 이동 범위를 반영', EXTENDED_MODE))),
    'dispersion': ('부분군 내 변동을 어떻게 보고 싶나요?',
                   'R은 최댓값과 최솟값의 차이이고, S는 모든 측정값으로 계산한 표본표준편차입니다.',
                   (('최댓값과 최솟값의 차이로 보기', 'xbar_r'), ('표준편차로 보기', 'xbar_s'))),
    'attribute_start': ('무엇을 세고 있나요?',
                        '흠집이 3개인 불합격 판재 한 장은 불량품 1개이면서 결점 3개입니다.',
                        (('합격·불합격으로 분류한 불량품 수', 'inspection_count'), ('흠집·누락처럼 각각 셀 수 있는 결점 수', 'inspection_amount'))),
    'inspection_count': ('부분군마다 검사한 제품 수가 같나요?',
                         '불량품이 5개로 같아도 100개 중 5개는 5%, 200개 중 5개는 2.5%입니다.',
                         (('검사한 제품 수가 모두 같음', 'count_or_rate'), ('검사한 제품 수가 달라짐', 'p'))),
    'count_or_rate': ('어떤 단위로 결과를 표시하려고 하나요?',
                      '검사수가 일정하면 같은 자료를 불량품 개수 또는 불량률로 나타낼 수 있습니다.',
                      (('불량품 개수를 그대로 표시', 'np'), ('검사수 대비 불량률로 표시', 'p'))),
    'inspection_amount': ('매번 같은 양을 검사하나요?',
                          '검사량은 검사한 면적·길이·제품 수처럼 결점이 생길 수 있는 범위를 뜻합니다.',
                          (('매번 같은 면적·길이·수량을 검사', 'c'), ('검사한 면적·길이·수량이 달라짐', 'u'))),
}


def render_studio_header():
    st.markdown('<div class="main-header"><h1>AIAG - VDA SPC Studio</h1></div>', unsafe_allow_html=True)


def _change_analysis_kind():
    st.session_state["_spc_analysis_kind"] = st.session_state["_chart_guide_kind"]


def _select_attribute_chart(chart):
    st.session_state["_attr_chart"] = chart


def _tutorial_move(path_key, target):
    state = st.session_state
    path = state[path_key]
    if target is None:
        state[path_key] = path[:-1]
    elif target == 'restart':
        state[path_key] = path[:1]
        if path_key == '_guide_tutorial_variable':
            state.pop('_guide_tutorial_group_size', None)
    else:
        state[path_key] = [*path, target]
        if target == 'pearson':
            for key in ('guide_pearson_chart', 'guide_pearson_n_xbar_r', 'guide_pearson_n_xbar_s'):
                state.pop(key, None)


def _set_tutorial_group_size(path_key):
    state = st.session_state
    current_n = _current_group_size(state)
    state['_guide_tutorial_group_size'] = (current_n if state.get('data_layout_mode') == 'wide_subgroup_cols'
                                          and 2 <= current_n <= 25 else int(state['_guide_tutorial_group_input']))
    _tutorial_move(path_key, 'subgroup_distribution')


def _prepare_pearson(chart, subgroup_size, *, use_example=False):
    state = st.session_state
    if use_example:
        from spc_education import load_sample
        load_sample('pearson')
    else:
        wide = state.get('data_layout_mode') == 'wide_subgroup_cols'
        if not state.get('data_bytes') or (wide and (chart == 'imr' or _current_group_size(state) != subgroup_size)):
            return
    _select_chart(chart, subgroup_size=subgroup_size)
    state.update(_guide_pearson_requested=subgroup_size, beginner_mode=False, advanced_tabs='📉 관리도')


def _tutorial_run(attribute):
    st.session_state['_guide_attribute_run' if attribute else '_trigger_analysis'] = True


def _render_tutorial_run(attribute, applied, ready, *, blocked=False):
    st.button('▶ 분석 시작', key='guide_tutorial_run', type='primary', on_click=_tutorial_run,
              args=(attribute,), disabled=not (applied and ready) or blocked)
    if applied and not ready:
        st.caption('왼쪽에서 데이터와 입력 설정을 확인하면 분석을 시작할 수 있습니다.')


def _pearson_inputs_ready(state, frame, n):
    import numpy as np
    from spc_core import complete_subgroup_mask
    from spc_nonnormal import (MIN_DIAGNOSIS_GROUPS, MIN_DIAGNOSIS_VALUES, check_location_sample,
                               check_sample)

    if frame is None:
        return False
    if state.get('preprocess_outliers') or state.get('box_cox_transform'):
        st.warning('피어슨 분포 진단 전에 사이드바에서 자동 이상치 제거와 Box-Cox 변환을 해제하세요.')
        return False
    phase2 = state.get('chart_usage_mode') == 'phase2'
    wide = state.get('data_layout_mode') == 'wide_subgroup_cols'
    baseline_count = max(3, int(state.get('baseline_subgroups', 25)))
    reference_rows = baseline_count if wide else baseline_count * n
    reference = frame.iloc[:reference_rows] if phase2 else frame
    column_keys = {str(c): c for c in reference.select_dtypes(include='number').columns
                   if str(c) != state.get('measurement_date_column')}
    if wide:
        columns = [column_keys[c] for c in state.get('subgroup_columns', []) if c in column_keys]
        measurements = [(state.get('subgroup_var_name') or '측정치', reference[columns].to_numpy(dtype=float, na_value=np.nan).ravel())]
    else:
        columns = state.get('selected_columns')
        measurements = [(name, reference[key].to_numpy(dtype=float, na_value=np.nan)) for name, key in column_keys.items()
                        if columns is None or name in columns]
    if not measurements:
        st.warning('사이드바에서 숫자형 측정 열을 선택하세요.')
        return False
    scope = '초기 기준구간' if phase2 else '전체 데이터'
    st.caption(f'{scope} 기준 · 유효 측정값 {MIN_DIAGNOSIS_VALUES}개 이상' +
               (f' · 완전한 부분군 {MIN_DIAGNOSIS_GROUPS}개 이상' if n > 1 else ''))
    valid_items = 0
    for name, raw in measurements:
        values = raw[complete_subgroup_mask(np.isfinite(raw), n)]
        location = values if n == 1 else values.reshape(-1, n).mean(axis=1)
        counts = f'유효 측정값 {len(values):,}개' + (f' · 완전한 부분군 {len(location):,}개' if n > 1 else '')
        try:
            if phase2 and len(raw) < baseline_count * n:
                raise ValueError(f'초기 기준구간에 원본 {baseline_count}개 {"부분군" if n > 1 else "관측값"}이 필요합니다.')
            check_sample(values, n)
            check_location_sample(location, n)
        except ValueError as exc:
            st.warning(f'{name} · {counts}. {exc}')
        else:
            valid_items += 1
            st.caption(f'{name} · {counts} · 진단 조건 충족')
    return valid_items > 0


def _render_pearson_recommendation(planned_n, ready, frame):
    from spc_education import PEARSON_SAMPLE_PATH

    state = st.session_state
    st.success('추천 검토 대상 · 피어슨 관리도')
    choices = ('imr', 'xbar_r', 'xbar_s')
    default_chart = 'imr' if planned_n == 1 else 'xbar_r' if planned_n <= 9 else 'xbar_s'
    selected = state.get('selected_control_chart')
    current_n = _current_group_size(state)
    if state.get('_guide_pearson_requested') == current_n == planned_n and selected in choices:
        default_chart = selected
    chart = st.radio('피어슨 분석에 사용할 관리도', choices, index=choices.index(default_chart),
                     format_func=lambda key: CHARTS[key][0], horizontal=True, key='guide_pearson_chart')
    subgroup_size = 1
    if chart != 'imr':
        sizes = CHARTS[chart][1]
        if planned_n > 1:
            initial = min(planned_n, sizes.stop - 1)
        else:
            initial = current_n if current_n in sizes else 5
        subgroup_size = int(st.number_input('피어슨 분석용 부분군 크기', min_value=sizes.start, max_value=sizes.stop - 1,
                                           value=initial, key=f'guide_pearson_n_{chart}'))
        if planned_n >= sizes.stop:
            st.caption(f'{CHARTS[chart][0]}의 부분군 크기는 최대 {sizes.stop - 1}개입니다. 앞에서 입력한 {planned_n}개를 그대로 사용하려면 X̄–S를 선택하세요.')
    statistic = '개별 측정값' if chart == 'imr' else f'{subgroup_size}개 값의 부분군 평균'
    spread = {'imr': 'MR 참고', 'xbar_r': 'R 관리도', 'xbar_s': 'S 관리도'}[chart]
    st.markdown(f'**위치 관리도의 타점** · {statistic}  \n**관리한계** · 확인한 위치 분포의 하위·상위 분위수')
    st.caption(f'산포 표시 · {spread}')
    st.write('정규성 검정 결과와 함께 히스토그램, Q-Q 플롯, 후보 분포 적합도, 공정에서 가능한 값의 범위를 검토해 적용할 분포를 선택하세요.')
    example_grouping = ('개별 측정값을 사용합니다.' if chart == 'imr' else
                        f'실습에서는 파일 순서대로 {subgroup_size}개씩 묶어 봅니다. 내 데이터의 부분군은 같은 시점과 조건에서 함께 측정한 값으로 구성하세요.')
    st.caption(f'{PEARSON_SAMPLE_PATH.name} · 평면도 측정값 500개. {example_grouping}')
    st.button('피어슨 예제 불러오기 · 분포 진단 준비', key='guide_tutorial_pearson',
              on_click=_prepare_pearson, args=(chart, subgroup_size), kwargs={'use_example': True})
    wide = state.get('data_layout_mode') == 'wide_subgroup_cols'
    wide_mismatch = wide and (chart == 'imr' or current_n != subgroup_size)
    st.button('내 데이터로 진단 준비', key='guide_tutorial_pearson_own', on_click=_prepare_pearson,
              args=(chart, subgroup_size), disabled=wide_mismatch or not state.get('data_bytes'))
    if _imr_layout_blocked(chart, state):
        st.caption(IMR_WIDE_GUIDANCE)
    elif wide_mismatch:
        st.caption(f'다열 데이터의 측정 열은 {current_n}개입니다. 내 데이터로 시작하려면 부분군 크기와 측정 열 수를 맞추세요.')
    elif not state.get('data_bytes'):
        st.caption('내 데이터로 진단하려면 왼쪽에서 파일을 올리세요.')
    applied = (state.get('_guide_pearson_requested') == subgroup_size == current_n
               and selected == chart and not state.get('beginner_mode'))
    diagnosis_ready = applied and ready and _pearson_inputs_ready(state, frame, subgroup_size)
    if diagnosis_ready:
        st.caption('분석을 시작한 뒤 관리도 화면에서 ‘후보 분포 진단 시작’을 누르세요. 적합 결과를 확인하고 분포를 적용할 수 있습니다.')
    _render_tutorial_run(False, applied, ready, blocked=not diagnosis_ready)


def _render_tutorial_recommendation(chart, attribute, selected, planned_n, ready):
    state = st.session_state
    imr_blocked = _imr_layout_blocked(chart, state)
    if attribute:
        option = ATTRIBUTE_CHARTS[chart]
        name, plots, inputs, reason = option.name, option.plotted, option.inputs, option.use_case
        applied = selected == chart
        select, kwargs = _select_attribute_chart, {}
    else:
        name, _, reason, inputs, plots = CHARTS[chart]
        if chart == 'xbar_s' and planned_n >= 10:
            reason = "이 앱은 부분군이 10개 이상이면 X̄–S로 분석합니다. S는 부분군의 모든 측정값으로 계산합니다."
        applied = selected == chart and _current_group_size(state) == planned_n and not chart_selection_error(state)
        select, kwargs = _select_chart, {'subgroup_size': planned_n}
    st.success(f"추천 관리도 · {name}")
    st.write(reason)
    if not attribute:
        st.markdown(f"**부분군 크기** · {planned_n}개")
    st.markdown(f"**표시값** · {plots}  \n**데이터 준비** · {inputs}")
    if chart == EXTENDED_MODE:
        from spc_education import EXTENDED_SAMPLE_BASELINE, EXTENDED_SAMPLE_N, EXTENDED_SAMPLE_PATH, load_sample

        example = state.get('_education_extended_data')
        example_loaded = bool(example and example == state.get('data_bytes'))
        example_applied = (example_loaded and selected == chart and _current_group_size(state) == EXTENDED_SAMPLE_N
                           and state.get('baseline_subgroups') == EXTENDED_SAMPLE_BASELINE
                           and not chart_selection_error(state))
        wide_mismatch = state.get('data_layout_mode') == 'wide_subgroup_cols' and _current_group_size(state) != planned_n
        st.button('내 데이터로 분석 준비', key='guide_tutorial_extended_own', on_click=select,
                  args=(chart,), kwargs=kwargs, disabled=wide_mismatch or not state.get('data_bytes'))
        if wide_mismatch:
            st.caption('내 데이터의 부분군 크기에 맞게 사이드바의 측정 열을 선택하세요.')
        elif not state.get('data_bytes'):
            st.caption('내 데이터로 분석하려면 왼쪽에서 파일을 올리세요.')
        st.button('확장 슈하르트 예제 불러오기', key='guide_tutorial_apply', on_click=load_sample,
                  args=(chart,), disabled=example_applied)
        st.caption(f'예제는 {EXTENDED_SAMPLE_PATH.name}의 외경을 사용합니다. 부분군 크기는 {EXTENDED_SAMPLE_N}개, 초기 기준 부분군은 {EXTENDED_SAMPLE_BASELINE}개입니다.')
        if example_loaded:
            st.caption(f'현재 예제 설정 · 부분군 크기 {_current_group_size(state)}개 · 초기 기준 부분군 {state.get("baseline_subgroups")}개')
        applied = applied or (example_loaded and selected == chart and not chart_selection_error(state))
    else:
        st.button("적용됨" if applied else "추천 관리도 적용", key='guide_tutorial_apply', type='primary',
                  on_click=select, args=(chart,), kwargs=kwargs, disabled=applied or imr_blocked)
        if imr_blocked:
            st.caption(IMR_WIDE_GUIDANCE)
    if attribute:
        from spc_attribute_ui import TUTORIAL_BASELINE, load_attribute_tutorial_sample

        st.button(f'{chart} 실습 데이터 불러오기', key='guide_tutorial_attribute_example',
                  on_click=load_attribute_tutorial_sample, args=(chart,))
        st.caption(f'실습은 초기 부분군 {TUTORIAL_BASELINE}개로 관리한계를 정하고, 이후 2개에서 관리한계 이탈을 찾아봅니다.')
    if not attribute and chart in ('xbar_r', 'xbar_s') and planned_n == 5:
        from spc_education import load_sample
        st.button("n=5 실습 데이터 불러오기", on_click=load_sample, kwargs={'chart': chart})
    _render_tutorial_run(attribute, applied, ready)


def _render_tutorial(attribute, selected, ready, frame):
    state = st.session_state
    kind = 'attribute' if attribute else 'variable'
    path_key = f'_guide_tutorial_{kind}'
    path = state.setdefault(path_key, [f'{kind}_start'])
    step = path[-1]
    planned_n = 1 if 'single_distribution' in path else state.get('_guide_tutorial_group_size', 5)
    charts = ATTRIBUTE_CHARTS if attribute else CHARTS
    with st.container(border=True):
        if step == 'pearson':
            _render_pearson_recommendation(planned_n, ready, frame)
        elif step in charts:
            _render_tutorial_recommendation(step, attribute, selected, planned_n, ready)
        elif step == 'group_size':
            st.caption(f"{len(path)}단계")
            current_n = _current_group_size(state)
            locked = state.get('data_layout_mode') == 'wide_subgroup_cols' and 2 <= current_n <= 25
            initial = current_n if locked else state.get('_guide_tutorial_group_size', current_n if 2 <= current_n <= 25 else 5)
            if locked and state.get('_guide_tutorial_group_input') != initial:
                state['_guide_tutorial_group_input'] = initial
            st.number_input("한 부분군에 몇 개가 들어가나요?", min_value=2, max_value=25, value=initial,
                            disabled=locked, key='_guide_tutorial_group_input')
            if locked:
                st.caption("사이드바에서 선택한 부분군 측정 열 수를 적용합니다.")
            st.button("다음", key='guide_tutorial_size_next', type='primary', on_click=_set_tutorial_group_size,
                      args=(path_key,))
        else:
            question, hint, choices = TUTORIAL_QUESTIONS[step]
            st.caption(f"{len(path)}단계")
            st.markdown(f"**{question}**")
            st.write(hint)
            for label, target in choices:
                destination = 'xbar_s' if target == 'dispersion' and planned_n >= 10 else target
                st.button(label, key=f'guide_tutorial_{step}_{target}', width='stretch',
                          on_click=_tutorial_move, args=(path_key, destination))
        if len(path) > 1:
            back, restart = st.columns(2)
            back.button("이전 단계", key='guide_tutorial_back', on_click=_tutorial_move, args=(path_key, None))
            restart.button("처음부터", key='guide_tutorial_restart', on_click=_tutorial_move, args=(path_key, 'restart'))


def _render_practice(attribute):
    cases = ATTRIBUTE_PRACTICE if attribute else VARIABLE_PRACTICE
    prefix = "guide_practice" if attribute else "guide_variable_practice"
    with st.expander("예제로 선택 확인"):
        correct = st.selectbox("실습 상황", tuple(cases), key=f"{prefix}_case", format_func=lambda key: cases[key][0])
        _, question, reason = cases[correct]
        st.write(question)
        answer = st.radio("어떤 관리도를 선택할까요?", tuple(cases), index=None,
                          horizontal=True, key=f"{prefix}_answer_{correct}",
                          format_func=lambda key: key if attribute else CHARTS[key][0])
        if answer == correct:
            st.success(reason)
            if correct == EXTENDED_MODE:
                from spc_education import EXTENDED_SAMPLE_PATH, load_sample
                st.button("확장 슈하르트 예제 불러오기", on_click=load_sample, args=(correct,))
                st.caption(f"{EXTENDED_SAMPLE_PATH.name}을 불러온 뒤 왼쪽의 ‘분석 시작’을 누르세요.")
            else:
                imr_blocked = _imr_layout_blocked(correct, st.session_state)
                st.button("이 관리도로 분석 준비", on_click=_select_attribute_chart if attribute else _select_chart,
                          args=(correct,), disabled=imr_blocked)
                if imr_blocked:
                    st.caption(IMR_WIDE_GUIDANCE)
            if not attribute and correct in ('xbar_r', 'xbar_s'):
                from spc_education import load_sample
                st.button("실습 데이터 불러오기", on_click=load_sample, kwargs={'chart': correct},
                          help="현재 분석 입력에 합성 실습 데이터를 불러옵니다.")
                st.caption("관리도 실습용 합성 측정값 150개를 5개씩 묶습니다. 데이터를 불러온 뒤 왼쪽의 ‘분석 시작’을 누르세요.")
        elif answer is not None:
            if attribute:
                choice = ATTRIBUTE_CHARTS[answer]
                st.info(f"{choice.name}의 표시값은 ‘{choice.plotted}’입니다. 이 상황에서 비교하려는 값과 검사량을 다시 확인하세요.")
            else:
                name, _, _, _, plotted = CHARTS[answer]
                st.info(f"{name} · {plotted} 문제에서 확인하려는 값과 한 번에 함께 측정한 개수를 다시 살펴보세요.")


def _current_group_size(state):
    if state.get('data_layout_mode') == 'wide_subgroup_cols':
        return len(state.get('subgroup_columns', []))
    return state.get('n', 5)


def _imr_layout_blocked(chart, state):
    return chart == 'imr' and state.get('data_layout_mode') == 'wide_subgroup_cols'


def chart_selection_error(state):
    selected = state.get("selected_control_chart")
    if selected not in CHARTS:
        return ""
    name, sizes, *_ = CHARTS[selected]
    wide = state.get("data_layout_mode") == "wide_subgroup_cols"
    if _imr_layout_blocked(selected, state):
        return IMR_WIDE_GUIDANCE
    n = _current_group_size(state)
    if n not in sizes:
        required = str(sizes.start) if len(sizes) == 1 else f"{sizes.start}–{sizes.stop - 1}"
        setting = "측정 열 수" if wide else "시료 크기"
        return f"{name}의 사용 조건은 n={required}입니다. 현재 {setting}(n={n})를 수정하거나 관리도를 다시 선택하세요."
    extended = state.get("chart_usage_mode") == EXTENDED_MODE
    if (selected == EXTENDED_MODE) != extended:
        mode = "확장 슈하르트 관리도" if selected == EXTENDED_MODE else "해석용 관리도 또는 관리용 관리도"
        return f"{name}의 운영 모드는 ‘{mode}’입니다. 사이드바 설정을 수정하거나 관리도를 다시 선택하세요."
    if extended and state.get("box_cox_transform"):
        return "확장 슈하르트 X̄-S를 사용하려면 중급자 모드의 분석·관리도 설정에서 Box-Cox 변환을 해제하세요."
    return ""


def _select_chart(selected, *, subgroup_size=None):
    state = st.session_state
    if _imr_layout_blocked(selected, state):
        return
    state.pop('_guide_pearson_requested', None)
    previous_chart = state.get("selected_control_chart")
    state["selected_control_chart"] = selected
    if selected is None:
        was_extended = state.get("chart_usage_mode") == EXTENDED_MODE
        if was_extended:
            state["chart_usage_mode"] = state.get("_standard_chart_usage_mode", "phase1")
        if previous_chart is not None or was_extended:
            invalidate_analysis(state)
        return
    previous = (state.get("data_layout_mode"), state.get("n"), state.get("chart_usage_mode"))
    if selected == "imr":
        state.update(data_layout_mode="long", n=1)
    elif subgroup_size is not None and state.get('data_layout_mode') != 'wide_subgroup_cols':
        state['n'] = subgroup_size
    if selected == EXTENDED_MODE:
        if state.get("chart_usage_mode") != EXTENDED_MODE:
            state["_standard_chart_usage_mode"] = state.get("chart_usage_mode", "phase1")
        state["chart_usage_mode"] = EXTENDED_MODE
    elif state.get("chart_usage_mode") == EXTENDED_MODE:
        state["chart_usage_mode"] = state.get("_standard_chart_usage_mode", "phase1")
    current = (state.get("data_layout_mode"), state.get("n"), state.get("chart_usage_mode"))
    applied = state.get("_analysis_request")
    if (previous_chart != selected or previous != current or chart_selection_error(state)
            or (applied and chart_selection_error(dict(applied, selected_control_chart=selected)))):
        invalidate_analysis(state)


def chart_selection_label(state):
    if state.get('_guide_pearson_requested'):
        if any(item.get('distribution_mode') == 'nonnormal' for item in state.get('results', [])):
            return "피어슨 관리도 · 적용 변수 있음"
        return "피어슨 분포 진단 준비"
    chart = CHARTS.get(state.get("selected_control_chart"))
    if chart:
        return chart[0]
    if state.get("chart_usage_mode") == EXTENDED_MODE:
        return f"{CHARTS[EXTENDED_MODE][0]} (운영 모드)"
    return "자동 선택"


def render_chart_guide(selection_error="", *, attribute_ready=False, frame=None):
    state = st.session_state
    kind = state.get("_spc_analysis_kind", "variable")
    attribute = kind == "attribute"
    selected = state.get("_attr_chart" if attribute else "selected_control_chart")
    title, status = st.columns([3, 2], vertical_alignment="center")
    title.subheader("관리도 선택 가이드")
    with status:
        label = ATTRIBUTE_CHARTS[selected].name if attribute and selected else (
            "관리도 선택 대기" if attribute else chart_selection_label(state))
        st.caption(f"현재 선택 · **{label}**")
        if not attribute and (selected or state.get("chart_usage_mode") == EXTENDED_MODE):
            st.button("자동 선택으로 돌아가기", on_click=_select_chart, args=(None,))
    if state.get("_chart_guide_kind") != kind:
        state["_chart_guide_kind"] = kind
    st.radio("데이터 유형", ("variable", "attribute"), key="_chart_guide_kind",
             format_func={"variable": "계량형 · 길이·중량 등 측정값",
                          "attribute": "계수형 · 불량품 수·결점 수"}.get,
             horizontal=True, on_change=_change_analysis_kind)
    guide, details = st.tabs(["선택 가이드", "관리도별 안내"])
    with guide:
        ready = attribute_ready if attribute else bool(state.get('data_bytes')) and not selection_error
        _render_tutorial(attribute, selected, ready, frame)
        if not attribute and state.get('chart_usage_mode') == EXTENDED_MODE and not state.get('analysis_done'):
            st.markdown("**확장 슈하르트 분석 준비**  \n원단위의 측정값을 2–25개씩 부분군으로 묶고, 사이드바에서 초기 기준 부분군 수를 지정하세요. 예상 평균 범위 자동 계산에는 유효 기준 부분군이 6개 이상 필요합니다.")
            if state.get('distribution_settings'):
                st.caption('확장 슈하르트는 원단위 자료로 계산합니다. 저장한 피어슨 설정은 피어슨 분석에 사용됩니다.')
        _render_practice(attribute)
    with details:
        if attribute:
            for chart_id, chart in ATTRIBUTE_CHARTS.items():
                with st.expander(chart.name, expanded=selected == chart_id):
                    st.markdown(f"**사용 상황**  \n{chart.use_case}")
                    st.markdown(f"**데이터 준비**  \n{chart.inputs}")
                    st.markdown(f"**관리도 읽기**  \n{chart.interpretation}")
                    st.button(f"{chart.name} · 선택됨" if selected == chart_id else f"{chart.name} 선택",
                              key=f'guide_{chart_id}', on_click=_select_attribute_chart, args=(chart_id,))
        else:
            for chart_id, (name, _, use_case, inputs, plots) in CHARTS.items():
                with st.expander(name, expanded=selected == chart_id):
                    st.markdown(f"**사용 상황**  \n{use_case}")
                    st.markdown(f"**데이터 준비**  \n{inputs}")
                    st.markdown(f"**확인할 변화**  \n{plots}")
                    st.markdown(f"**관리도 읽기**  \n{VARIABLE_NOTES[chart_id]}")
                    imr_blocked = _imr_layout_blocked(chart_id, state)
                    st.button(f"{name} · 선택됨" if selected == chart_id else f"{name} 선택",
                              key=f'guide_{chart_id}', on_click=_select_chart, args=(chart_id,), disabled=imr_blocked)
                    if imr_blocked:
                        st.caption(IMR_WIDE_GUIDANCE)
        if selected:
            st.caption("왼쪽에서 파일을 올리고 계수와 검사수 또는 검사단위 열을 확인하세요." if attribute
                       else "왼쪽에서 측정 파일을 올리고 부분군 크기와 측정 열을 확인한 뒤 분석을 시작하세요.")
    if selection_error:
        st.warning(selection_error)
