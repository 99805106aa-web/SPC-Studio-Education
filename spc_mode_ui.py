"""Small, guided controls and paginated charts shared by both experience levels."""
import io
import hashlib
import json
import numpy as np
import pandas as pd
import streamlit as st
from spc_extended import EXTENDED_MODE
from spc_fast import BoundedCache, chart_window, signal_indices
from spc_charts import build_control_chart, build_interactive_control_chart
from spc_export import fig_to_bytes
import matplotlib.pyplot as plt


def show_scrollable_interactive_chart(fig, width, config, *, presentation=False):
    """Always-visible controls outside the scroller; hover and pan work directly."""
    import streamlit.components.v1 as components
    height = 500 if presentation else 760
    fig.update_layout(width=width, height=height, autosize=width is None, dragmode='pan')
    fig.update_yaxes(fixedrange=True)
    base = list(fig.layout.xaxis.range)
    chart_html = fig.to_html(full_html=False, include_plotlyjs=True,
                             default_width='100%',
                             config=dict(config, responsive=width is None, scrollZoom=False, displayModeBar=False))
    controls = '''<style>
      body{margin:0;font-family:Arial,sans-serif} .chart-controls{height:40px;display:flex;gap:6px;align-items:center}
      .chart-controls button{padding:5px 12px;border:1px solid #cbd5e1;background:#f8fafc;color:#172b4d;border-radius:5px;cursor:pointer;font-size:13px}
      .chart-controls button:focus-visible{outline:2px solid #2375ff}
      .chart-controls span{font-size:13px;color:#475569;margin-left:6px}
      </style><div class="chart-controls" role="toolbar" aria-label="관리도 보기 조절">
      <button type="button" data-chart-action="in">＋ 확대</button>
      <button type="button" data-chart-action="out">－ 축소</button>
      <button type="button" data-chart-action="reset">전체 보기</button>
      <span>점 위: 측정값 · 드래그: 좌우 이동</span></div>'''
    script = '''<script>
    const originalRange=BASE_RANGE;
    const scroller=document.getElementById('chart-scroll');
    document.querySelectorAll('[data-chart-action]').forEach(button=>button.addEventListener('click',()=>{
      const plot=document.querySelector('.plotly-graph-div');
      if(!plot || !plot._fullLayout) return;
      const action=button.dataset.chartAction;
      let range=originalRange.slice();
      if(action!=='reset') {
        const current=plot._fullLayout.xaxis.range;
        const size=plot._fullLayout._size;
        const fraction=Math.max(0,Math.min(1,(scroller.scrollLeft+scroller.clientWidth/2-size.l)/size.w));
        const center=current[0]+(current[1]-current[0])*fraction;
        const span=Math.min(originalRange[1]-originalRange[0],Math.max(2,(current[1]-current[0])*(action==='in' ? 0.7 : 1.4)));
        let low=center-span*fraction;
        low=Math.max(originalRange[0],Math.min(originalRange[1]-span,low));
        range=[low,low+span];
      } else { scroller.scrollLeft=0; }
      Plotly.relayout(plot,{'xaxis.range':range,'xaxis2.range':range});
    }));
    </script>'''.replace('BASE_RANGE', json.dumps(base))
    components.html(controls+'<div id="chart-scroll" role="region" aria-label="관리도 가로 스크롤" tabindex="0" '
                    'style="width:100%;overflow-x:auto;overflow-y:hidden;">'+chart_html+'</div>'+script,
                    height=height + 60, scrolling=False)


def preserve_settings():
    # Detach persistent values from widget cleanup before conditionally rendering widgets.
    keys = ['preprocess_outliers', 'outlier_method', 'box_cox_transform',
            'manual_target_enabled', 'auto_watch_enabled', 'auto_watch_fav_idx',
            'beginner_mode',
            'gemini_api_key', 'groq_api_key', 'openrouter_api_key', 'detail_var_idx',
            'detail_var_sel', 'advanced_tabs', 'sb_smtp_host', 'sb_smtp_port', 'sb_smtp_user', 'sb_smtp_pass',
            'sb_from_addr', 'sb_to_addrs', 'sb_use_ssl', 'sb_use_starttls', 'sb_only_recent']
    keys += [k for k in st.session_state if k.startswith('process_info_') and not k.endswith('_btn')]
    keys += [k for k in st.session_state if k.startswith(('dist_bounded_', 'dist_lower_',
                                                         'dist_family_', 'dist_chart_family_', 'dist_method_', 'dist_confirm_',
                                                         '_attr_input_'))]
    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def measurement_column_picker(options):
    state = st.session_state
    key = "_measurement_column_picker"
    saved = state.get("selected_columns")
    selected = list(options) if saved is None else [column for column in saved if column in options]
    if state.get(key) != selected:
        state[key] = selected
    state["selected_columns"] = selected

    def save_selection():
        state["selected_columns"] = list(state[key])

    st.multiselect("분석할 측정 항목", options, key=key, on_change=save_selection)


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
        measurement_column_picker(numeric)
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
    with st.expander('데이터 미리보기', expanded=False):
        st.dataframe(frame.head(10), hide_index=True, use_container_width=True)
    if not st.session_state.get('selected_control_chart'):
        n = st.session_state['n']
        if st.session_state.get('chart_usage_mode') == EXTENDED_MODE:
            chart = "확장 슈하르트 X̄-S"
        else:
            chart = "I-MR" if n == 1 else "X̄-R" if n < 10 else "X̄-S"
        st.caption(f'사용할 관리도 · {chart}')


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


def beginner_conditions(it=None):
    """Brief context, while keeping editing and diagnostic details in advanced mode."""
    from spc_policy import CHARACTERISTIC_CLASSES, STUDY_STAGES
    cfg = (it or {}).get('assessment_settings', st.session_state.get('assessment_settings', {}))
    record = (it or {}).get('capability_criteria', {})
    if cfg.get('criteria_mode') == 'aiag_vda':
        cls = record.get('characteristic_class', cfg.get('characteristic_class', 'major'))
        stage = record.get('study_stage', cfg.get('study_stage', 'final'))
        st.caption(f"평가 기준: AIAG·VDA 자동 · {CHARACTERISTIC_CLASSES[cls]} · {STUDY_STAGES[stage]}")
    else:
        st.caption(f"평가 기준: 고객 지정 · 요구값 {cfg.get('required_limit', 1.33):.2f}")
    settings = st.session_state.get('_analysis_request', {}) if it else st.session_state
    active = []
    if settings.get('preprocess_outliers'):
        active.append('이상치 검토')
    if settings.get('box_cox_transform'):
        active.append('데이터 변환')
    if (it or {}).get('distribution_mode') == 'nonnormal' or any(
            x.get('mode') == 'nonnormal' for x in settings.get('distribution_settings', {}).values() if isinstance(x, dict)):
        active.append('분포 설정')
    if settings.get('chart_usage_mode') in ('phase2', EXTENDED_MODE):
        active.append('관리한계 고정')
    if active:
        st.caption('중급자 설정 적용 중: ' + ' · '.join(active))


def beginner_hold_message(it):
    """Explain the most actionable blocker without exposing a diagnostic report."""
    if not any(np.isfinite(it.get(key, np.nan)) for key in ('original_usl', 'original_lsl')):
        return '규격이 입력되지 않았습니다. 규격을 선택하면 공정능력 지수를 확인할 수 있습니다.'
    if it.get('baseline_status') == '부적합' or it.get('sequence_review', {}).get('count') or it.get('capability_status') == '관리상태 확인':
        return '공정 안정성 확인이 필요합니다. 관리도의 이상 신호를 먼저 확인하세요.'
    record = it.get('capability_criteria', {})
    if record.get('mode') == 'aiag_vda' and record.get('status') == 'conditional':
        return '소표본 조건부 충족입니다. 일반 충족이나 공정 승인과 구분하여 확인하세요.'
    if it.get('capability_status') != '적용 가능' or record.get('status') == 'hold':
        reasons = list(dict.fromkeys(it.get('capability_reasons', ())))
        if reasons:
            return '평가 보류 사유 · ' + ' · '.join(reasons)
        return '평가 조건 확인이 필요합니다. 자세한 사유는 중급자 모드에서 확인하세요.'
    return ''


def beginner_summary_rows(results):
    from spc_metric_ui import beginner_reference_label
    from spc_policy import CRITERIA_STATUS, band, limits
    rows = []
    for it in results:
        record = it.get('capability_criteria', {})
        row = {'측정 항목': str(it['column']), '관리도': it['chart_mode'],
               '분석 측정값 수': len(it['series']), '이상 신호 위치 수': len(signal_indices(it)),
               '규격 이탈 수': it.get('spec_violation_count', 0) if any(np.isfinite(it.get(key, np.nan)) for key in ('original_usl', 'original_lsl')) else None,
               '평가 안내': beginner_hold_message(it)}
        for key in ('cp', 'cpk', 'pp', 'ppk'):
            label = key.capitalize()
            value = it.get(key)
            row[label] = float(value) if value is not None and np.isfinite(value) else None
            decision = record.get('indices', {}).get(key)
            row[label+' 요구값'] = decision['required'] if decision else limits(it, key)[1]
            row[label+' 판정'] = beginner_reference_label(it, key, value) or (CRITERIA_STATUS[decision['status']] if decision else {
                'na':'판정 보류', 'bad':'기준 미달', 'warn':'주의', 'good':'기준 충족 (참고)'}[band(value, it, key)])
        rows.append(row)
    return rows


def quick_results(results, *, render_chart):
    """Beginner landing page: four indices and a shared control chart, without tabs."""
    from spc_metric_ui import beginner_capability_cards_html
    st.subheader('공정능력' if render_chart is None else '관리도 · 공정능력')
    if not results:
        st.warning('분석 결과가 없습니다. 데이터와 측정 개수를 확인하세요.')
        return
    previous = max(0, min(int(st.session_state.get('detail_var_idx', 0)), len(results)-1))
    if st.session_state.get('detail_var_sel') not in range(len(results)):
        st.session_state['detail_var_sel'] = previous
    def changed():
        st.session_state['detail_var_idx'] = st.session_state['detail_var_sel']
    selected = st.selectbox('측정 항목', range(len(results)), index=previous,
                           format_func=lambda i: str(results[i]['column']),
                           key='detail_var_sel', on_change=changed)
    st.session_state['detail_var_idx'] = selected
    it = results[selected]
    beginner_conditions(it)
    if it.get('distribution_warning'):
        st.warning(it['distribution_warning'])
    st.markdown(beginner_capability_cards_html(it), unsafe_allow_html=True)
    message = beginner_hold_message(it)
    if message:
        st.info(message)
    st.caption('Cp·Cpk는 적용 조건을 확인한 지수, Pp·Ppk는 이번 데이터의 성능 참고값입니다. 자세한 분석과 기준 변경은 중급자 모드에서 확인하세요.')
    has_spec = any(np.isfinite(it.get(key, np.nan)) for key in ('original_usl', 'original_lsl'))
    spec_status = f"{it.get('spec_violation_count', 0):,}건" if has_spec else '규격 미입력'
    view = None
    if render_chart is None:
        st.metric('실제 규격 이탈', spec_status)
    else:
        signals = signal_indices(it)
        a, b = st.columns(2)
        a.metric('이상 신호 위치', f'{len(signals):,}개')
        b.metric('실제 규격 이탈', spec_status)
        if len(signals):
            st.warning('관리도에서 이상 신호가 발견되었습니다. 해당 시점의 작업 조건을 확인하세요.')
        else:
            st.caption('선택한 규칙에서 이상 신호가 발견되지 않았습니다. 제품 합격을 의미하지는 않습니다.')
        view = render_chart(it)
    if view is not None:
        st.caption('위: 측정값의 위치 변화 · 아래: 변동 크기. 관리한계를 벗어난 점과 표시된 이상 신호를 확인하세요.')
    st.markdown('#### 결과 저장')
    csv_col, png_col = st.columns(2)
    csv_col.download_button('핵심 지수 요약 CSV 저장',
        pd.DataFrame(beginner_summary_rows(results)).to_csv(index=False).encode('utf-8-sig'),
        file_name='공정능력_핵심요약.csv', mime='text/csv', key='beginner_csv', on_click='ignore')
    if view is not None:
        png = png_bytes(view, st.session_state['palette'], st.session_state['show_zones'], st.session_state['ksq_toggles'])
        png_col.download_button('현재 구간 관리도 PNG 저장', png,
            file_name=f"관리도_{selected+1}_{view['window_start']+1}-{view['window_end']}.png",
            mime='image/png', key='beginner_png', on_click='ignore')
