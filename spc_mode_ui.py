"""Small, guided controls and paginated charts shared by both experience levels."""
import io
import hashlib
import numpy as np
import pandas as pd
import streamlit as st
from spc_fast import BoundedCache, chart_window, signal_indices
from spc_charts import build_control_chart, build_interactive_control_chart
from spc_export import fig_to_bytes
import matplotlib.pyplot as plt


def preserve_settings():
    # Detach persistent values from widget cleanup before conditionally rendering widgets.
    keys = ['preprocess_outliers', 'outlier_method', 'box_cox_transform',
            'manual_target_enabled', 'auto_watch_enabled', 'auto_watch_fav_idx',
            'gemini_api_key', 'groq_api_key', 'openrouter_api_key', 'detail_var_idx',
            'detail_var_sel', 'sb_smtp_host', 'sb_smtp_port', 'sb_smtp_user', 'sb_smtp_pass',
            'sb_from_addr', 'sb_to_addrs', 'sb_use_ssl', 'sb_use_starttls', 'sb_only_recent']
    keys += [k for k in st.session_state if k.startswith('process_info_') and not k.endswith('_btn')]
    keys += [k for k in st.session_state if k.startswith(('dist_bounded_', 'dist_lower_',
                                                         'dist_family_', 'dist_chart_family_', 'dist_method_', 'dist_confirm_'))]
    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def beginner_inputs(frame):
    st.markdown('### ② 측정 방식 확인')
    layouts = ['long', 'wide_subgroup_cols']
    current = st.session_state.get('data_layout_mode', 'long')
    layout = st.radio('측정값이 어떻게 배치되어 있나요?', layouts,
        index=layouts.index(current), format_func=lambda v: '한 열에 시간 순서대로' if v == 'long' else '한 행에 함께 측정한 값 여러 개')
    st.session_state['data_layout_mode'] = layout
    if frame is None:
        st.info('파일을 올리면 측정 항목과 데이터 미리보기가 표시됩니다.')
        return
    dates = ['사용 안 함'] + [str(c) for c in frame.columns]
    saved = st.session_state.get('measurement_date_column') or '사용 안 함'
    data_id = hashlib.sha256(st.session_state.get('data_bytes') or b'').hexdigest()
    if saved == '사용 안 함' and st.session_state.get('_beginner_date_file') != data_id:
        saved = next((c for c in dates if c.lower() in ('date', 'datetime', 'timestamp', '측정일자', '날짜')), saved)
    st.session_state['_beginner_date_file'] = data_id
    selected = st.selectbox('측정 날짜 열 (선택)', dates, index=dates.index(saved) if saved in dates else 0)
    st.session_state['measurement_date_column'] = '' if selected == '사용 안 함' else selected
    numeric = [str(c) for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c]) and str(c) != selected]
    if layout == 'long':
        saved_cols = st.session_state.get('selected_columns')
        default = [c for c in saved_cols if c in numeric] if saved_cols is not None else numeric
        st.session_state['selected_columns'] = st.multiselect('분석할 측정 항목', numeric, default=default)
        st.session_state['n'] = int(st.number_input('한 번에 함께 측정한 개수', 1, 25,
            value=int(st.session_state['n']), help='한 번에 한 값이면 1, 같은 조건에서 5개씩 측정했다면 5를 선택하세요.'))
        st.caption('입력한 개수씩 위에서부터 묶습니다. 날짜 순서와 묶음을 미리보기로 확인하세요.')
    else:
        previous = st.session_state.get('subgroup_columns', [])
        selected_cols = st.multiselect('함께 측정한 값이 들어 있는 열', numeric,
            default=[c for c in previous if c in numeric])
        st.session_state['subgroup_columns'] = selected_cols
        if len(selected_cols) >= 2:
            st.session_state['n'] = len(selected_cols)
        st.session_state['subgroup_var_name'] = st.text_input('측정 항목 이름', value=st.session_state.get('subgroup_var_name', '측정치'))
    st.dataframe(frame.head(10), hide_index=True, use_container_width=True)
    n = st.session_state['n']
    st.caption(f'사용할 관리도: {"I-MR" if n == 1 else "Xbar-R" if n < 10 else "Xbar-S"}')


def applied_conditions(it=None):
    request = st.session_state.get('_analysis_request', {}) if it else {}
    settings = request or st.session_state
    phase = (it or {}).get('control_phase', settings.get('chart_usage_mode', 'phase1'))
    descriptions = ['초기 기준구간으로 고정한 한계' if phase == 'phase2' else '전체 데이터로 계산한 시험 한계']
    if settings.get('preprocess_outliers'):
        descriptions.append('이상치 포함 묶음 제외 적용')
    if settings.get('box_cox_transform'):
        descriptions.append('Box-Cox 변환 설정 적용')
    enabled = settings.get('ksq_toggles', {})
    descriptions.append('감지 규칙: ' + (', '.join(k for k in [f'T{i}' for i in range(1, 9)] if enabled.get(k, True)) or '선택 없음'))
    st.caption('적용된 분석 조건 · ' + ' / '.join(descriptions))
    if st.session_state.get('auto_watch_enabled'):
        st.caption('파일 변경 자동 감시가 켜져 있습니다. 중급자 이상 모드에서 변경할 수 있습니다.')


def paged_window(it, key='main'):
    total = len(it['means'])
    pages = max(1, (total + 999) // 1000)
    ident = hashlib.sha256((str(it['column']) + str(st.session_state.get('analysis_meta', {}).get('analysis_id', ''))).encode()).hexdigest()[:12]
    page_key = f'page_{key}_{ident}'
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    signals = signal_indices(it)
    if pages > 1:
        c1, c2, c3 = st.columns([2, 1, 1])
        current = min(pages, int(st.session_state[page_key]))
        st.session_state[page_key] = current
        with c2:
            if st.button('이전 이상 구간', key=f'prev_signal_{key}'):
                candidates = signals[signals < (current - 1) * 1000]
                if len(candidates):
                    st.session_state[page_key] = int(candidates[-1]) // 1000 + 1
        with c3:
            if st.button('다음 이상 구간', key=f'next_signal_{key}'):
                candidates = signals[signals >= current * 1000]
                if len(candidates):
                    st.session_state[page_key] = int(candidates[0]) // 1000 + 1
        with c1:
            st.number_input('표시 구간', 1, pages, key=page_key)
    page = int(st.session_state[page_key]) - 1
    view = chart_window(it, page)
    st.caption(f"전체 {total:,}점 중 {view['window_start'] + 1:,}–{view['window_end']:,}점 · 전체 이상 신호 위치 {len(signals):,}개 (중복 제외)")
    if view['window_offset'] < view['window_start']:
        st.caption('이동범위 연결을 위해 직전 측정점 1개를 함께 표시합니다.')
    return view, signals


def png_bytes(view, palette, zones, toggles):
    fig = build_control_chart(view, palette=palette, show_zones=zones, ksq_toggles=toggles)
    try:
        return fig_to_bytes(fig, 'png')
    finally:
        plt.close(fig)


def quick_results(results, beginner=True):
    if not results:
        st.warning('분석할 수 있는 결과가 없습니다. 데이터와 측정 개수를 확인하세요.')
        return
    previous = min(st.session_state.get('detail_var_idx', 0), len(results) - 1)
    def changed():
        st.session_state['detail_var_idx'] = st.session_state['quick_item']
        st.session_state['detail_var_sel'] = st.session_state['quick_item']
    st.session_state['quick_item'] = previous
    selected = st.selectbox('측정 항목', range(len(results)),
        format_func=lambda i: str(results[i]['column']), key='quick_item', on_change=changed)
    st.session_state['detail_var_idx'] = selected
    it = results[selected]
    applied_conditions(it)
    chart_tab, save_tab = st.tabs(['관리도 보기', '결과 저장'], key='quick_tabs', on_change='rerun')
    if chart_tab.open:
        with chart_tab:
            view, signals = paged_window(it, 'quick')
            has_spec = np.isfinite(it['original_usl']) or np.isfinite(it['original_lsl'])
            a, b, c = st.columns(3)
            a.metric('이상 신호 위치', f'{len(signals):,}개')
            b.metric('규격 이탈', f"{it['spec_violation_count']:,}건" if has_spec else '규격 미입력')
            c.metric('분석 제외 측정값', f"{len(it['raw_series']) - it['analysis_n']:,}개")
            if len(signals):
                st.warning('확인할 이상 신호가 있습니다. 해당 시점의 설비·자재·측정 조건을 확인하세요.')
                st.caption('발견된 관리도 점 번호 (처음 20개): ' + ', '.join(str(int(i) + 1) for i in signals[:20]))
            else:
                st.info('선택한 규칙에서 이상 신호가 발견되지 않았습니다. 제품 합격을 의미하지는 않습니다.')
            if it.get('baseline_status') == '부적합':
                st.warning('고정 관리한계의 기준구간 확인이 필요합니다: ' + '; '.join(it.get('baseline_reasons', [])))
            if it.get('pre_msg'):
                st.info(it['pre_msg'])
            st.plotly_chart(build_interactive_control_chart(view, st.session_state['palette'],
                st.session_state['ksq_toggles']), use_container_width=True, key='quick_chart')
            st.caption('위: 측정값의 위치 변화 · 아래: 측정값 사이의 흔들림. 빨간 점은 관리한계 이탈, 노란 점은 패턴 신호입니다.')
            if has_spec and it['spec_violation_count']:
                raw = it['raw_series']
                bad = np.flatnonzero((raw > it['original_usl']) | (raw < it['original_lsl']))
                st.caption('규격 이탈 원본 측정행 (처음 20개): ' + ', '.join(str(int(i) + 1) for i in bad[:20]))
    if save_tab.open:
        with save_tab:
            view, signals = paged_window(it, 'quick')
            rows = []
            for item in results:
                has_spec = np.isfinite(item['original_usl']) or np.isfinite(item['original_lsl'])
                rows.append({'측정 항목': item['column'], '관리도': item['chart_mode'],
                    '분석 측정값 수': item['analysis_n'], '이상 신호 위치 수': len(signal_indices(item)),
                    '규격 이탈 수': item['spec_violation_count'] if has_spec else '규격 미입력',
                    '제외 측정값 수': len(item['raw_series']) - item['analysis_n'],
                    '관리상한': item['ucl'], '중심선': item['xbarbar'], '관리하한': item['lcl']})
            st.download_button('요약 CSV 저장', pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig'),
                file_name='관리도_요약.csv', mime='text/csv')
            png = png_bytes(view, st.session_state['palette'], {k: False for k in ('A','B','C','Z3')}, st.session_state['ksq_toggles'])
            st.download_button('현재 구간 PNG 저장', png,
                file_name=f"관리도_{selected + 1}_{view['window_start'] + 1}-{view['window_end']}.png", mime='image/png')
