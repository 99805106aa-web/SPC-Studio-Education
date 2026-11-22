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


@lru_cache(maxsize=2)
def sample_workbook(scenario="stable"):
    data, spec = sample_csvs(scenario)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.read_csv(io.BytesIO(data)).to_excel(writer, sheet_name='DATA', index=False)
        pd.read_csv(io.BytesIO(spec)).to_excel(writer, sheet_name='SPEC', index=False)
    return output.getvalue()


def load_sample(scenario="stable", chart="xbar_r"):
    import streamlit as st
    from spc_access import ensure_active_day
    from spc_file_ui import accept_data_upload

    ensure_active_day()
    state = st.session_state
    extended = scenario == EXTENDED_MODE
    attached = {EXTENDED_MODE: EXTENDED_SAMPLE_PATH, 'pearson': PEARSON_SAMPLE_PATH}.get(scenario)
    raw = attached.read_bytes() if attached else sample_workbook(scenario)
    for key in list(state):
        if key.startswith(("data_uploader", "spec_uploader")):
            del state[key]
    name = attached.name if attached else (
        "SPC_교육_평균이동.xlsx" if scenario == "shift" else "SPC_교육_통합예제.xlsx")
    phase = "phase2" if scenario == "shift" else "phase1"
    if extended:
        phase = state.get('chart_usage_mode', 'phase1')
        if phase == EXTENDED_MODE:
            phase = state.get('_standard_chart_usage_mode', 'phase1')
    accept_data_upload(state, raw, name)
    state.update(spec_bytes=raw, spec_file_name=name, data_sheet='DATA', spec_sheet='SPEC',
                 n=5, data_layout_mode="long", selected_control_chart=chart, chart_usage_mode=phase,
                 _standard_chart_usage_mode=phase, baseline_subgroups=25, preprocess_outliers=False,
                 box_cox_transform=False, distribution_settings={}, manual_target_enabled=False,
                 _loaded_fav_name="교육용 합성 예제", _education_shift_data=raw if scenario == 'shift' else None,
                 chat_history=[])
    state.pop('_education_extended_data', None)
    if extended:
        state.update(data_sheet=EXTENDED_SAMPLE_SHEET, spec_sheet='규격', selected_columns=['외경'],
                     measurement_date_column='부분군', n=EXTENDED_SAMPLE_N, baseline_subgroups=EXTENDED_SAMPLE_BASELINE,
                     chart_usage_mode=EXTENDED_MODE, selected_control_chart=EXTENDED_MODE,
                     _loaded_fav_name=name, _education_extended_data=raw)
    elif scenario == 'pearson':
        state.update(data_sheet=PEARSON_SAMPLE_SHEET, spec_sheet='규격', selected_columns=['1'],
                     n=1, selected_control_chart='imr', _loaded_fav_name=name)
    state.pop('_education_ocap', None)
    state.pop('_guide_pearson_requested', None)


def render_samples(*, expanded):
    import streamlit as st

    data, spec = sample_csvs()
    workbook = sample_workbook('stable')
    with st.expander("🎓 교육용 예제", expanded=expanded):
        st.caption("기본 예제의 직경과 두께 관리도를 비교하세요. 평균 이동 예제에서는 고정 한계로 이상 신호를 찾아 조치를 기록할 수 있습니다.")
        st.button("예제 불러오기", on_click=load_sample, width="stretch")
        st.button("평균 이동 예제 불러오기", on_click=load_sample, args=("shift",), width="stretch")
        st.download_button('통합 예제 Excel 내려받기', workbook, 'SPC_교육_통합예제.xlsx',
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        st.download_button("예제 데이터 CSV", data, "SPC_교육_데이터.csv", "text/csv")
        st.download_button("예제 규격 CSV", spec, "SPC_교육_규격.csv", "text/csv")


def render_ocap_exercise(results):
    import streamlit as st

    shift_data = st.session_state.get('_education_shift_data')
    if not shift_data or st.session_state.get('data_bytes') != shift_data:
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
