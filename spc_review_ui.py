"""Study criteria and traceable outlier review controls."""
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from spc_policy import normalize_assessment


def render_assessment_settings(save_config):
    cfg = normalize_assessment(st.session_state.get('assessment_settings'))
    with st.expander('평가 기준 · 조사 조건', expanded=False):
        st.caption('고객과 합의한 기준을 입력하세요. 기본 1.00/1.33은 예시이며 모든 특성의 의무 기준이 아닙니다.')
        with st.form('assessment_criteria'):
            warning = st.number_input('개선 경계', min_value=.01, value=cfg['warning_limit'], step=.01)
            required = st.number_input('고객 요구값', min_value=.01, value=cfg['required_limit'], step=.01)
            confidence = st.selectbox('신뢰수준', [.90,.95,.99,.999],
                                    index=[.90,.95,.99,.999].index(cfg['confidence']) if cfg['confidence'] in [.90,.95,.99,.999] else 1)
            confirmed = st.checkbox('측정 적합성·대표 기간·5M·합리적 샘플링 조건을 확인했습니다.', value=cfg['study_confirmed'])
            reviewer = st.text_input('조사 조건 확인자', value=cfg['reviewer'])
            note = st.text_area('조사 기간·샘플링 계획·측정 적합성의 확인 근거', value=cfg['study_note'])
            submit = st.form_submit_button('평가 조건 저장 · 재분석')
        if submit:
            try:
                new = normalize_assessment(dict(cfg, warning_limit=warning, required_limit=required,
                    confidence=confidence, study_confirmed=confirmed, reviewer=reviewer, study_note=note))
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state['assessment_settings'] = new
                save_config()
                if st.session_state.get('results'):
                    st.session_state['_trigger_analysis'] = True
                st.rerun()
        st.caption('125개 부품·25개 부분군 미만이나 조사 조건 미확인 시 Pp/Ppk를 예비·참고로 제공하고 Cp/Cpk 판정을 보류합니다. '
                   '정규 독립 자료는 모수 bootstrap 신뢰하한을 제공합니다. 비정규·Box–Cox 신뢰구간과 소표본 승인 예외는 별도 검토가 필요합니다.')
        if cfg['outlier_approvals'] and st.button('저장된 이상치 검토 기록 초기화'):
            cfg['outlier_approvals'] = {}
            st.session_state['assessment_settings'] = cfg
            save_config()
            st.session_state['_trigger_analysis'] = True
            st.rerun()


def render_result_review(it, save_config):
    from spc_normality import STATUS_LABELS, pvalue_text
    diagnostics=it.get('normality_diagnostics',{})
    extra=[(name,record) for name,record in diagnostics.items() if name!='individual']
    if extra:
        with st.expander('부분군 평균·변환 후 정규성'):
            for name,record in extra:
                st.markdown(f"**{record['target']} · {record['scope']} · {record['scale']}**")
                st.write(f"{STATUS_LABELS[record['status']]} · {record.get('test') or '검정 불가'} · "
                         f"p={pvalue_text(record.get('pvalue'))} · 검정 N={record['test_n']:,}/전체 N={record['total_n']:,}")
                st.caption(f"왜도 {record.get('skewness')} · 초과첨도 {record.get('excess_kurtosis')}")
            st.caption('부분군 평균과 개별값의 정규성은 다를 수 있습니다. 원단위 평균 진단과 변환 후 개별값 진단도 서로 구분하세요.')
    individual=diagnostics.get('individual',{})
    if individual.get('status')=='rejected':
        st.info('아래의 「비정규 분포 진단 · Pearson / G·Z 설정」에서 후보를 비교할 수 있습니다. 자동으로 분포를 선택하거나 전환하지 않습니다.')
    if it.get('box_cox_applied'):
        st.info(it['performance_note'])
        st.caption(f"원단위 규격 USL={it.get('original_usl')} / LSL={it.get('original_lsl')} · "
                   f"관리도 변환단위 규격 USL={it.get('usl')} / LSL={it.get('lsl')}")
    review = it.get('sequence_review', {})
    if review.get('count'):
        st.warning(f"시간 패턴 검토 {review['count']}점: {review['note']} 능력 판정을 보류합니다.")
        with st.expander('시간 패턴 검토 위치'):
            st.dataframe(pd.DataFrame({'관리도 점 순서':[i+1 for i in review['indices']]}), hide_index=True)
    st.caption(it.get('study_conclusion', ''))
    ci = it.get('capability_confidence')
    if ci:
        st.caption(f"Ppk 단측 {ci['confidence']:.1%} 신뢰하한 {ci['ppk_lower']:.4f} · {ci['method']}")
    info = it.get('outlier_info') or {}
    candidates = info.get('candidate_items', info.get('items', []))
    if not candidates:
        return
    column = str(it['column'])
    with st.expander('이상치 후보 검토 · 제외 승인', expanded=not info.get('reviewed')):
        st.caption('통계적 극단값만으로 제외하지 마세요. 공정의 유효 관측이면 유지하고, 무효임이 확인된 행만 선택하세요. '
                   '선택한 행이 포함된 부분군 전체가 분석에서 제외되며 원자료와 규격이탈은 보존됩니다.')
        if info.get('approval_expired'):
            st.warning('이전 승인 이후 자료·부분군·기준구간이 변경되어 모든 후보를 유지했습니다. 다시 검토하세요.')
        st.dataframe(pd.DataFrame(candidates).rename(columns={'index':'원본 순서','value':'측정값'}),hide_index=True)
        with st.form('exclusion_review_'+column):
            indices = st.multiselect('무효로 확인되어 제외할 원본 행 (빈 선택은 전부 유효값으로 유지)',
                options=[r['index'] for r in candidates], default=info.get('approved_indices', []))
            reason = st.text_area('검토 결론과 근거 (제외 또는 유효값 유지 사유)')
            reviewer = st.text_input('검토 확인자')
            submit = st.form_submit_button('검토 기록 저장 · 재분석')
        if submit:
            if not reason.strip() or not reviewer.strip():
                st.error('검토 근거와 확인자를 입력하세요.')
            else:
                cfg = normalize_assessment(st.session_state.get('assessment_settings'))
                cfg['outlier_approvals'] = dict(cfg['outlier_approvals'])
                cfg['outlier_approvals'][column] = dict(indices=indices, reason=reason.strip(), reviewer=reviewer.strip(),
                    reviewed_at=datetime.now(timezone.utc).isoformat(), digest=info['digest'])
                st.session_state['assessment_settings'] = cfg
                save_config()
                st.session_state['_trigger_analysis'] = True
                st.rerun()
