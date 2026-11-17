"""One workbook upload with independent data/spec sheet selection."""
import hashlib
import io

import streamlit as st
from spc_core import get_sheet_names

SAME = '같은 Excel 파일'
SEPARATE = '별도 규격 파일'
NONE = '규격 없음'
_SPEC_NAMES = {'spec', 'specs', 'specification', 'specifications', '규격', '규격표', '규격정보'}
_DATA_NAMES = {'data', '데이터', '측정데이터', '측정값', '측정치'}


def _normalized(value):
    return ''.join(str(value).split()).casefold()


def workbook_sheets(raw):
    return get_sheet_names(io.BytesIO(raw)) if raw else []


def guess_sheets(sheets):
    spec = next((name for name in sheets if _normalized(name) in _SPEC_NAMES), None)
    data = next((name for name in sheets if _normalized(name) in _DATA_NAMES),
                next((name for name in sheets if name != spec), sheets[0] if sheets else None))
    return data, spec


def clear_spec(state):
    state.update(spec_bytes=None, spec_sheet=None, spec_file_name='', active_spec_path='')


def invalidate_analysis(state):
    for job in state.get('_distribution_jobs', {}).values():
        if job.get('cancel') is not None:
            job['cancel'].set()
    state.update(analysis_done=False, results=[], summary_df=None, ai_var_cache={},
                 _trigger_analysis=False)
    for key in ('_distribution_jobs', '_preflight_cached', '_displayed_signature', '_analysis_request'):
        state.pop(key, None)


def accept_data_upload(state, raw, name):
    invalidate_analysis(state)
    state.update(data_bytes=raw, data_file_name=name, active_data_path='',
                 data_sheet=None, _loaded_fav_name=name, selected_columns=None,
                 measurement_date_column='')
    clear_spec(state)
    state.pop('_separate_spec', None)
    state.pop('_input_identity', None)


def prepare_input_state(state, sheets):
    raw = state.get('data_bytes') or b''
    identity = hashlib.sha256(raw).hexdigest()+str(state.get('data_file_name', ''))
    if state.get('_input_identity') == identity:
        return
    state['_input_identity'] = identity
    if state.get('spec_bytes'):
        mode = SAME if sheets and state['spec_bytes'] == raw else SEPARATE
    else:
        mode = SAME if sheets else NONE
    state['_spec_source'] = mode
    state['_spec_source_previous'] = mode
    if mode == SEPARATE:
        state['_separate_spec'] = {key: state.get(key) for key in
            ('spec_bytes', 'spec_sheet', 'spec_file_name', 'active_spec_path')}
    for key in ('data_sheet_sel', 'spec_sheet_sel'):
        state.pop(key, None)


def bind_shared_spec(state, selected):
    if selected:
        state.update(spec_bytes=state['data_bytes'], spec_sheet=selected,
                     spec_file_name=state.get('data_file_name', ''),
                     active_spec_path=state.get('active_data_path', ''))
    else:
        clear_spec(state)


def _on_data_upload():
    state = st.session_state
    uploaded = state.get('data_uploader')
    if uploaded is not None:
        accept_data_upload(state, uploaded.getvalue(), uploaded.name)
    else:
        invalidate_analysis(state)
        state.update(data_bytes=None, data_sheet=None, data_file_name='', active_data_path='')
        clear_spec(state)
        state.pop('_input_identity', None)
        state.pop('_separate_spec', None)


def _on_source_change():
    state = st.session_state
    if state.get('_spec_source_previous') == SEPARATE:
        state['_separate_spec'] = {key: state.get(key) for key in
            ('spec_bytes', 'spec_sheet', 'spec_file_name', 'active_spec_path')}
    clear_spec(state)
    if state['_spec_source'] == SEPARATE:
        state.update(state.get('_separate_spec', {}))
    state.pop('spec_sheet_sel', None)
    invalidate_analysis(state)


def _on_sheet_change(kind):
    state = st.session_state
    state[kind+'_sheet'] = state[kind+'_sheet_sel']
    invalidate_analysis(state)


def _on_spec_upload():
    state = st.session_state
    uploaded = state.get('spec_uploader')
    if uploaded is None:
        clear_spec(state)
    else:
        state.update(spec_bytes=uploaded.getvalue(), spec_file_name=uploaded.name,
                     active_spec_path='', spec_sheet=None)
    state.pop('spec_sheet_sel', None)
    state['_separate_spec'] = {key: state.get(key) for key in
        ('spec_bytes', 'spec_sheet', 'spec_file_name', 'active_spec_path')}
    invalidate_analysis(state)


def _select_sheet(label, kind, options, preferred):
    key = kind+'_sheet_sel'
    if key not in st.session_state or st.session_state[key] not in options:
        st.session_state[key] = preferred if preferred in options else options[0]
    selected = st.selectbox(label, options, key=key,
        format_func=lambda value: '규격 없음' if value is None else value,
        on_change=_on_sheet_change, args=(kind,))
    st.session_state[kind+'_sheet'] = selected
    return selected


def render_file_inputs():
    state = st.session_state
    st.markdown('### 📁 데이터 · 규격 파일')
    st.file_uploader('Excel / CSV 파일 업로드', type=['xlsx', 'xls', 'csv'], key='data_uploader',
        help='Excel 한 파일을 업로드한 뒤 데이터 시트와 규격 시트를 각각 선택하세요.',
        on_change=_on_data_upload)
    raw = state.get('data_bytes')
    if not raw:
        st.caption('데이터와 규격이 서로 다른 시트에 있는 Excel을 한 번만 업로드하세요.')
        return
    sheets = workbook_sheets(raw)
    prepare_input_state(state, sheets)
    st.caption('현재 불러온 파일 · 분석 시작 시 사용')
    st.code(state.get('active_data_path') or state.get('data_file_name') or '업로드 파일', language=None)
    data_default, spec_default = guess_sheets(sheets)
    if sheets:
        _select_sheet('데이터 시트', 'data', sheets, state.get('data_sheet') or data_default)
    else:
        state['data_sheet'] = None
    options = [SAME, SEPARATE, NONE] if sheets else [NONE, SEPARATE]
    if state.get('_spec_source') not in options:
        state['_spec_source'] = NONE
    mode = st.radio('규격 선택', options, key='_spec_source', on_change=_on_source_change)
    state['_spec_source_previous'] = mode
    if mode == SAME:
        available = [name for name in sheets if name != state.get('data_sheet')]
        preferred = state.get('spec_sheet')
        if preferred not in available:
            preferred = spec_default if spec_default in available else None
        selected = _select_sheet('규격 시트 (같은 파일)', 'spec', [None]+available, preferred)
        bind_shared_spec(state, selected)
        if selected:
            st.caption('같은 파일의 선택한 규격 시트를 사용합니다. 규격 열: 항목/Column, USL, LSL, Target(선택).')
        else:
            st.caption('규격 시트를 선택하면 공정능력을 계산합니다. 규격 없이 관리도만 분석할 수도 있습니다.')
    elif mode == SEPARATE:
        st.file_uploader('별도 규격 Excel / CSV 업로드', type=['xlsx','xls','csv'],
                         key='spec_uploader', on_change=_on_spec_upload)
        if state.get('spec_bytes'):
            st.code(state.get('active_spec_path') or state.get('spec_file_name') or '규격 파일', language=None)
            spec_sheets = workbook_sheets(state['spec_bytes'])
            if spec_sheets:
                _select_sheet('규격 시트', 'spec', spec_sheets,
                              state.get('spec_sheet') or guess_sheets(spec_sheets)[1])
            else:
                state['spec_sheet'] = None
        else:
            st.caption('규격 파일을 선택하지 않으면 관리도만 분석합니다.')
    else:
        clear_spec(state)
