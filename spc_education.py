"""Built-in classroom datasets for the SPC tutorials."""
import io
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd
from spc_extended import EXTENDED_MODE


EXTENDED_SAMPLE_PATH = Path(__file__).with_name('examples') / '03 확장관리도 예제.xlsx'
EXTENDED_SAMPLE_SHEET = '확장 슈하르트 관리도'
EXTENDED_SAMPLE_N = 5
EXTENDED_SAMPLE_BASELINE = 9
PEARSON_SAMPLE_PATH = Path(__file__).with_name('examples') / '02. 피어슨 관리도 예제.xlsx'
PEARSON_SAMPLE_SHEET = '평면도 예제(피어슨관리도)'

SAMPLE_FILE_NAME = 'SPC_교육_통합예제.xlsx'
SPEC_SHEET = 'SPEC'
ATTRIBUTE_LIMIT_SAMPLE = 'attribute_limits'
SAMPLE_OPTIONS = {
    'stable': ('기본 · X̄–R', 'DATA'),
    'imr': ('기본 · I–MR', 'DATA'),
    'xbar_s': ('기본 · X̄–S', 'DATA'),
    'shift': ('평균 이동 · 관리이탈 조치 실습', '평균 이동'),
    'pearson': ('피어슨 · 평면도', PEARSON_SAMPLE_SHEET),
    EXTENDED_MODE: ('확장 슈하르트 · 외경', EXTENDED_SAMPLE_SHEET),
    'p': ('p · 불량률', 'p 관리도'),
    'np': ('np · 불량품 수', 'np 관리도'),
    'c': ('c · 결점 수', 'c 관리도'),
    'u': ('u · 단위당 결점 수', 'u 관리도'),
    ATTRIBUTE_LIMIT_SAMPLE: ('p · 직접 계산과 정규근사 비교', '계수형 한계 비교'),
}
_ATTRIBUTE_SAMPLES = ('p', 'np', 'c', 'u', ATTRIBUTE_LIMIT_SAMPLE)



def sample_csvs(scenario="stable"):
    rng = np.random.default_rng(20261115)
    data = pd.DataFrame({"직경": rng.normal(10.0, 0.025, 150),
                         "두께": rng.normal(2.0, 0.012, 150)})
    if scenario == "shift":
        data.loc[125:, "직경"] += 0.05
    else:
        # Shift one complete thickness subgroup while keeping all five values inside the specifications.
        data.loc[100:104, "두께"] += 0.027
    spec = pd.DataFrame({"항목": ["직경", "두께"], "USL": [10.1, 2.05],
                         "LSL": [9.9, 1.95], "Target": [10.0, 2.0]})
    return tuple(df.to_csv(index=False).encode("utf-8-sig") for df in (data, spec))


def _attribute_sample_frame(scenario):
    if scenario == ATTRIBUTE_LIMIT_SAMPLE:
        return pd.DataFrame({'defects': [1] * 25 + [5, 6], 'sample_size': [100] * 27})
    varying_exposure = scenario in ('p', 'u')
    counts = ([1, 3, 2, 5] if varying_exposure else [1, 2, 3, 2]) * 5
    frame = pd.DataFrame({'defects': [*counts, 2, 18 if varying_exposure else 12]})
    if scenario != 'c':
        sizes = {'p': [100, 200], 'np': [100, 100], 'u': [1, 2]}[scenario]
        frame['sample_size'] = sizes * (len(frame) // 2)
    return frame


@lru_cache(maxsize=1)
def sample_workbook():
    data, spec = [pd.read_csv(io.BytesIO(csv)) for csv in sample_csvs()]
    shift_data, _ = sample_csvs('shift')
    sheets = {
        SAMPLE_OPTIONS['stable'][1]: data,
        SAMPLE_OPTIONS['shift'][1]: pd.read_csv(io.BytesIO(shift_data)),
    }
    specs = [spec]
    for scenario, path in (('pearson', PEARSON_SAMPLE_PATH), (EXTENDED_MODE, EXTENDED_SAMPLE_PATH)):
        name = SAMPLE_OPTIONS[scenario][1]
        sheets[name] = pd.read_excel(path, sheet_name=name)
        specs.append(pd.read_excel(path, sheet_name='규격').rename(columns={'Column': '항목'}))
    sheets.update((SAMPLE_OPTIONS[key][1], _attribute_sample_frame(key)) for key in _ATTRIBUTE_SAMPLES)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
        pd.concat(specs, ignore_index=True).to_excel(writer, sheet_name=SPEC_SHEET, index=False)
    return output.getvalue()


def load_sample(scenario="stable", chart="xbar_r"):
    import streamlit as st
    from spc_access import ensure_active_day
    from spc_file_ui import accept_data_upload

    ensure_active_day()
    state = st.session_state
    extended = scenario == EXTENDED_MODE
    raw = sample_workbook()
    label, data_sheet = SAMPLE_OPTIONS[scenario]
    for key in list(state):
        if key.startswith(("data_uploader", "spec_uploader")):
            del state[key]
    phase = "phase2" if scenario == "shift" else "phase1"
    if extended:
        phase = state.get('chart_usage_mode', 'phase1')
        if phase == EXTENDED_MODE:
            phase = state.get('_standard_chart_usage_mode', 'phase1')
    accept_data_upload(state, raw, SAMPLE_FILE_NAME)
    state.update(spec_bytes=raw, spec_file_name=SAMPLE_FILE_NAME, data_sheet=data_sheet, spec_sheet=SPEC_SHEET,
                 n=1 if chart == 'imr' else 5, data_layout_mode="long", selected_control_chart=chart,
                 chart_usage_mode=phase, _standard_chart_usage_mode=phase, baseline_subgroups=25,
                 preprocess_outliers=False, box_cox_transform=False, distribution_settings={},
                 manual_target_enabled=False, _spc_analysis_kind='variable', _loaded_fav_name=label,
                 _education_shift_data=raw if scenario == 'shift' else None, chat_history=[])
    state.pop('_education_shift_sheet', None)
    state.pop('_education_extended_data', None)
    state.pop('_education_ocap', None)
    state.pop('_guide_pearson_requested', None)
    if scenario == 'shift':
        state['_education_shift_sheet'] = data_sheet
    elif extended:
        state.update(selected_columns=['외경'], measurement_date_column='부분군', n=EXTENDED_SAMPLE_N,
                     baseline_subgroups=EXTENDED_SAMPLE_BASELINE, chart_usage_mode=EXTENDED_MODE,
                     selected_control_chart=EXTENDED_MODE, _education_extended_data=raw)
    elif scenario == 'pearson':
        state.update(selected_columns=['1'], n=1, selected_control_chart='imr', _guide_pearson_requested=1,
                     beginner_mode=False, advanced_tabs='📉 관리도')


def _load_selected_sample():
    import streamlit as st

    scenario = st.session_state['_education_sample_selection']
    if scenario in _ATTRIBUTE_SAMPLES:
        from spc_attribute_ui import load_attribute_tutorial_sample

        load_attribute_tutorial_sample(scenario)
    elif scenario in ('imr', 'xbar_s'):
        load_sample(scenario, chart=scenario)
    else:
        load_sample(scenario)


def render_samples(*, expanded):
    import streamlit as st

    with st.expander("🎓 교육용 예제", expanded=expanded):
        st.selectbox('실습 예제 선택', tuple(SAMPLE_OPTIONS), key='_education_sample_selection',
                     format_func=lambda key: SAMPLE_OPTIONS[key][0])
        st.button('예제 불러오기', key='_education_sample_load', on_click=_load_selected_sample, width='stretch')
        st.download_button('통합 예제 Excel 내려받기', sample_workbook(), SAMPLE_FILE_NAME,
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           on_click='ignore')


def render_ocap_exercise(results):
    import streamlit as st

    shift_data = st.session_state.get('_education_shift_data')
    shift_sheet = st.session_state.get('_education_shift_sheet', 'DATA')
    if (not shift_data or st.session_state.get('data_bytes') != shift_data
            or st.session_state.get('data_sheet') != shift_sheet):
        return
    item = next((it for it in results if it.get('column') == '직경' and it.get('control_phase') == 'phase2'), None)
    if item is None or not item.get('x_ctrl_violation_count_monitor'):
        return
    prompts = ('첫 이상 부분군과 측정 확인', '영향 범위와 임시 조치', '원인 가설과 시정조치', '효과 확인 방법과 담당자')
    with st.expander('실습 · 관리이탈 조치계획(OCAP)', expanded=True):
        st.caption('직경 관리도의 감시구간을 확인하고, 작업자가 취할 조치를 기록하세요.')
        with st.form('_education_ocap_form'):
            notes = {label: st.text_area(label) for label in prompts}
            if st.form_submit_button('실습 기록 저장'):
                if all(note.strip() for note in notes.values()):
                    st.session_state['_education_ocap'] = notes
                else:
                    st.warning('네 항목을 모두 작성하면 기록을 저장할 수 있습니다.')
        saved = st.session_state.get('_education_ocap')
        if saved:
            st.success('실습 기록을 저장했습니다.')
            rows = [(label, "'" + note if note.lstrip().startswith(('=', '+', '-', '@')) else note)
                    for label, note in saved.items()]
            csv = pd.DataFrame(rows, columns=['실습 항목', '작성 내용']).to_csv(index=False)
            st.download_button('조치 기록 CSV 저장', csv.encode('utf-8-sig'), 'SPC_실습_조치기록.csv',
                               'text/csv', on_click='ignore')
