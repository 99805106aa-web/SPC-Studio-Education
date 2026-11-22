"""Shared, display-only models for attribute SPC screens, charts and reports."""
from datetime import datetime
from html import escape
import base64

import numpy as np
import pandas as pd
from scipy import stats

TITLE = 'AIAG - VDA SPC Studio 계수형'
VIEWS = ('핵심 요약', '관리한계·규칙', '전체 정보')
VALUE_LABELS = {'p': '불량률 (p)', 'np': '불량수 (np)', 'c': '결점수 (c)', 'u': '단위당결점수 (u)'}
CELL_STYLES = {
    'bad': 'background-color:#fee2e2;color:#991b1b;font-weight:bold',
    'warn': 'background-color:#fef3c7;color:#92400e;font-weight:bold',
    'good': 'background-color:#dcfce7;color:#166534;font-weight:bold',
    'neutral': 'background-color:#f1f5f9;color:#475569',
}


def finite(value):
    try:
        return value is not None and bool(np.isfinite(float(value)))
    except (ValueError, TypeError):
        return False


def number_text(value, decimals=4, percent=False):
    if not finite(value):
        return '—'
    return f'{float(value):,.{decimals}f}' + ('%' if percent else '')


def count_text(value):
    return f'{float(value):,.12g}' if finite(value) else '—'


def signal_summary(result):
    count = len(result.get('values', []))
    violations = result.get('violations', {})
    indices = set().union(*(set(violations.get(rule, [])) for rule in ('T1', 'T2', 'T3', 'T4')))
    indices = {int(index) for index in indices if 0 <= index < count}
    phase2 = result.get('control_phase') == 'phase2'
    boundary = min(count, max(0, int(result.get('baseline_display_count', count))))
    baseline = sum(index < boundary for index in indices) if phase2 else len(indices)
    monitor = sum(index >= boundary for index in indices) if phase2 else 0
    text = f'활성 규칙 이상 부분군 {len(indices)}개' if indices else '활성 규칙 신호 없음'
    if phase2:
        text += f' · 기준구간 {baseline}개 / 감시구간 {monitor}개'
        if boundary == count:
            text += ' · 감시 데이터 없음'
    return dict(indices=indices, total=len(indices), baseline=baseline, monitor=monitor,
                state='bad' if indices else 'good', text=text)


def core_metrics(result, capability=None):
    cap = capability if capability is not None else result.get('capability', {})
    cap = cap or {}
    ct = result.get('chart_type', 'p')
    units = result.get('total_inspected', result.get('total_units', 0))
    defects = result.get('total_defects', 0)
    signals = signal_summary(result)
    metrics = [dict(label='총 검사수' if ct in ('p', 'np') else '총 검사단위', value=count_text(units),
                    note='c 관리도는 부분군을 동일 검사단위로 계산' if ct == 'c' else '분석에 포함된 검사 단위 합계', state='neutral'),
               dict(label='총 불량품수' if ct in ('p', 'np') else '총 결점수', value=count_text(defects), note='관측 합계', state='neutral')]
    if ct in ('p', 'np'):
        rate = cap.get('DPU', np.nan)
        metrics.extend([
            dict(label='관측 불량률', value=number_text(rate * 100 if finite(rate) else None, 3, True), note='전체 불량품수 / 전체 검사수', state='neutral'),
            dict(label='불량품 PPM', value=number_text(cap.get('PPM'), 0), note='검사 백만 개당 관측 불량품수', state='neutral'),
            dict(label='관측 수율', value=number_text(cap.get('yield_pct'), 3, True), note='1 − 관측 불량품률', state='neutral'),
        ])
    else:
        metrics.extend([
            dict(label='DPU', value=number_text(cap.get('DPU')), note='전체 결점수 / 전체 검사단위', state='neutral'),
            dict(label='포아송 무결점 추정 수율', value=number_text(cap.get('yield_pct'), 3, True), note='exp(−DPU) · 관측 양품률과 구분', state='neutral'),
        ])
    metrics.append(dict(label='이상 부분군 수', value=f"{signals['total']}개", note='활성 T1~T4 · 중복 부분군 제외', state=signals['state']))
    return metrics


def detail_metrics(result, capability=None):
    cap = capability if capability is not None else result.get('capability', {})
    cap = cap or {}
    opportunity = finite(cap.get('total_opportunities')) and float(cap['total_opportunities']) > 0
    opportunity_warning = cap.get('opportunity_warning')
    opportunity_reason = opportunity_warning or ('계산 조건 확인' if opportunity else '단위당 결점 기회수 미입력')
    sigma_reason = opportunity_warning or ('기회수 미입력: 결점 밀도를 불량 확률로 환산하지 않음'
                    if result.get('chart_type') in ('c', 'u') and not opportunity
                    else '관측 0건 또는 확률 경계·데이터 부족으로 Z 환산 불가')
    return [dict(label=label, value=number_text(cap.get(key), decimals),
                 note=note if finite(cap.get(key)) else reason,
                 state=detail_state(key, cap.get(key)))
            for key, label, decimals, note, reason in (
                ('DPU', '단위당 불량·결점수', 4, '관측 합계 기준', '유효한 검사단위가 필요합니다'),
                ('DPO', 'DPO', 6, '단위당 결점 기회수 반영', opportunity_reason),
                ('DPMO', 'DPMO', 0, '백만 기회당 결점수', opportunity_reason),
                ('process_z', '공정 Z (Z-bench)', 3, '기존 확률 환산값', sigma_reason),
                ('sigma_level', '시그마 수준', 3, 'Z + 1.5 · 기존 시프트 가정', sigma_reason),
            )]


def detail_state(key, value):
    if not finite(value):
        return 'neutral'
    if key == 'DPMO':
        return 'good' if value < 3400 else 'warn' if value < 66800 else 'bad'
    if key == 'sigma_level':
        return 'good' if value >= 4 else 'warn' if value >= 3 else 'bad'
    return 'neutral'


def summary_lines(result, capability=None):
    lines = [('관리도 유형', result.get('chart_type', '').upper()),
             ('운영 모드', result.get('control_phase_label', '해석용'))]
    lines.extend((metric['label'], metric['value']) for metric in core_metrics(result, capability))
    for key in ('cl', 'ucl', 'lcl'):
        values = np.asarray(result.get(key, np.nan), dtype=float)
        value = np.mean(values) if values.size else np.nan
        lines.append((key.upper() + (' (평균)' if values.ndim else ''), number_text(value)))
    if result.get('control_phase') == 'phase2':
        signals = signal_summary(result)
        lines.extend([('기준구간 이상 부분군', str(signals['baseline'])), ('감시구간 이상 부분군', str(signals['monitor']))])
    return lines


def result_frame(result):
    values = np.asarray(result.get('values', []), dtype=float)
    length = len(values)
    ct = result.get('chart_type', 'p')
    data = {'부분군': np.arange(1, length + 1)}
    labels = result.get('x_labels', [])
    if len(labels) == length:
        data['측정일자/로트'] = [str(label) for label in labels]
    if result.get('control_phase') == 'phase2':
        boundary = int(result.get('baseline_display_count', length))
        data['구간'] = ['기준구간' if i < boundary else '감시구간' for i in range(length)]
    sizes = result.get('sample_sizes', result.get('inspection_units', []))
    if len(sizes) == length:
        data['검사수 (n)' if ct in ('p', 'np') else '검사단위'] = np.asarray(sizes, dtype=float)
    elif ct == 'c':
        data['검사단위'] = np.ones(length)
    defects = result.get('defects', [])
    if len(defects) == length:
        data['불량품수' if ct in ('p', 'np') else '결점수'] = np.asarray(defects)
    data[VALUE_LABELS[ct]] = values
    for key in ('cl', 'ucl', 'lcl'):
        raw = np.asarray(result.get(key, np.nan), dtype=float)
        data[key.upper()] = np.full(length, float(raw)) if raw.ndim == 0 else raw
    violations = result.get('violations', {})
    for rule in ('T1', 'T2', 'T3', 'T4'):
        data[rule] = ['감지' if i in violations.get(rule, []) else '' for i in range(length)]
    data['감지 규칙'] = [', '.join(rule for rule in ('T1', 'T2', 'T3', 'T4') if data[rule][i]) for i in range(length)]
    frame = pd.DataFrame(data)
    frame.attrs.clear()
    return frame


def view_columns(frame, view, chart_type):
    identity = [col for col in ('부분군', '측정일자/로트', '구간') if col in frame]
    size = [col for col in ('검사수 (n)', '검사단위') if col in frame]
    if view == VIEWS[0]:
        counts = [col for col in ('불량품수', '결점수') if col in frame] if chart_type in ('p', 'u') else []
        columns = identity + size + counts + [VALUE_LABELS[chart_type], '감지 규칙']
    elif view == VIEWS[1]:
        columns = identity + size + [VALUE_LABELS[chart_type], 'CL', 'UCL', 'LCL', 'T1', 'T2', 'T3', 'T4', '감지 규칙']
    else:
        columns = list(frame)
    return [column for column in columns if column in frame]


def filter_frame(frame, query='', signals_only=False):
    shown = frame.copy()
    if signals_only:
        shown = shown[shown['감지 규칙'].ne('')]
    if query.strip():
        names = shown['부분군'].astype(str)
        if '측정일자/로트' in shown:
            names = names + ' ' + shown['측정일자/로트'].astype(str)
        shown = shown[names.str.contains(query.strip(), case=False, regex=False)]
    shown.attrs.clear()
    return shown


def highlight_row(row):
    return [CELL_STYLES['bad'] if row.get('감지 규칙', '') else '' for _ in row]


def metrics_html(metrics):
    return '<div class="attr-metrics">' + ''.join(
        f'<article class="attr-metric {metric["state"]}"><div>{escape(metric["label"])}</div>'
        f'<strong>{escape(metric["value"])}</strong><small>{escape(metric["note"])}</small></article>'
        for metric in metrics) + '</div>'


def render_dashboard(result, capability, grade_evaluator, show_figure):
    import streamlit as st
    from attr_charts import build_summary_panel
    st.markdown('#### 계수형 품질성과 대시보드')
    signals = signal_summary(result)
    (st.warning if signals['total'] else st.success)(signals['text'])
    st.caption('관리 규칙 신호와 관측 품질성과는 별도로 해석합니다. 신호가 없어도 품질성과 충족을 보증하지 않습니다.')
    st.markdown('<style>' + PRESENTATION_CSS + '</style>' + metrics_html(core_metrics(result, capability)), unsafe_allow_html=True)
    with st.expander('품질성과 상세 · DPO·DPMO·Z·시그마 수준'):
        detail = pd.DataFrame(detail_metrics(result, capability))
        styles = pd.DataFrame('', index=detail.index, columns=['label','value','note'])
        styles['value'] = detail['state'].map(CELL_STYLES)
        st.dataframe(detail[['label','value','note']].style.apply(lambda _:styles, axis=None), hide_index=True, width='stretch',
                     column_config={'label':'지표', 'value':'값', 'note':'산정 기준·미계산 사유'})
        st.caption('색상은 기존 DPMO·시그마 참고 구간입니다. 공식 능력 판정이나 관리상태 판정과 구분하세요.')
        cap = capability or {}
        st.markdown('**관측 품질성과 등급 · 관리상태 별도**')
        if finite(cap.get('process_z')) and finite(cap.get('yield_pct')):
            grade = grade_evaluator(cap['process_z'], cap['yield_pct'], signals['total'])
            st.write(grade['grade'] + ' · ' + grade['label'])
            st.caption(grade['desc'])
        else:
            st.info('성과 등급 미계산: ' + next(metric['note'] for metric in detail_metrics(result, capability) if metric['label']=='공정 Z (Z-bench)'))
    with st.expander('관리한계·규칙 요약 패널'):
        show_figure(build_summary_panel(result, capability or {}, palette=st.session_state.get('palette', 'trust')))


def render_result_table(result):
    import streamlit as st
    frame = result_frame(result)
    view = st.radio('표시 항목', VIEWS, horizontal=True, key='attr_table_view')
    search, options = st.columns([2, 1])
    query = search.text_input('부분군·날짜/로트 검색', key='attr_subgroup_search')
    signals_only = options.checkbox('이상 부분군만', key='attr_signals_only')
    shown = filter_frame(frame, query, signals_only)
    columns = view_columns(frame, view, result.get('chart_type', 'p'))
    display = shown[columns].copy()
    formats = {col:'{:.4f}' if col in ('CL','UCL','LCL','불량률 (p)','단위당결점수 (u)') else '{:,.12g}'
               for col in columns if pd.api.types.is_numeric_dtype(frame[col])}
    st.caption(f'표시 {len(shown)} / 전체 {len(frame)}개 부분군 · 행을 선택하면 상세 정보를 표시합니다.')
    config = {col:st.column_config.Column(col, pinned=True) for col in ('부분군','측정일자/로트') if col in columns}
    event = st.dataframe(display.style.apply(highlight_row, axis=1).format(formats, na_rep='—'),
                         width='stretch', height=420, hide_index=True, column_config=config,
                         on_select='rerun', selection_mode='single-row',
                         key=f'attr_results_{view}_{hash(tuple(shown.index))}')
    selected = event.selection.rows if hasattr(event, 'selection') else []
    if selected and selected[0] < len(shown):
        row = shown.iloc[selected[0]]
        st.markdown(f"**부분군 {int(row['부분군'])} 상세**")
        detail = row.rename_axis('항목').reset_index(name='값')
        detail['값'] = detail['값'].map(str)
        st.dataframe(detail, hide_index=True, width='stretch')


PRESENTATION_CSS = '''
.attr-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0 18px}
.attr-metric{background:#f8fafc;color:#1e293b;border:1px solid #cbd5e1;border-radius:8px;padding:14px;min-width:0;overflow-wrap:anywhere}
.attr-metric>div{font-size:14px;line-height:1.5}.attr-metric strong{display:block;font-size:26px;line-height:1.4;font-variant-numeric:tabular-nums}
.attr-metric small{display:block;color:#475569;font-size:13px;line-height:1.5}
.attr-metric.bad{border-left:4px solid #b42318}.attr-metric.good{border-left:4px solid #047857}
@media(max-width:800px){.attr-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:420px){.attr-metrics{grid-template-columns:1fr}}
'''


def _rate_interval(result):
    chart = result.get('chart_type', 'p')
    label = '불량률 95% 신뢰구간' if chart in ('p', 'np') else 'DPU 95% 신뢰구간'
    count, exposure = result.get('total_defects'), result.get('total_units', result.get('total_inspected'))
    if not (finite(count) and finite(exposure)):
        return label, '—', '유효한 관측 합계 필요'
    count, exposure = float(count), float(exposure)
    if count < 0 or not count.is_integer() or exposure <= 0:
        return label, '—', '유효한 관측 합계 필요'
    if chart in ('p', 'np'):
        if not exposure.is_integer() or count > exposure:
            return label, '—', '불량품수·검사수 확인'
        interval = stats.binomtest(int(count), int(exposure), p=count / exposure).proportion_ci(method='wilson')
        value = f'{interval.low:.3%} ~ {interval.high:.3%}'
        method = 'Wilson · 이항모형'
    else:
        lower = stats.chi2.ppf(.025, 2 * count) / (2 * exposure) if count else 0
        upper = stats.chi2.ppf(.975, 2 * (count + 1)) / (2 * exposure)
        value = f'{lower:.5g} ~ {upper:.5g}'
        method = '포아송 정확 구간 · 검사단위 기준'
    if signal_summary(result)['total']:
        method += ' · 관리 신호 있음, 참고값'
    return label, value, method


def _limit_text(values):
    values = np.asarray(values, dtype=float).ravel()
    values = values[np.isfinite(values)]
    if not values.size:
        return '—'
    low, high = values.min(), values.max()
    return number_text(low) if low == high else f'{number_text(low)} ~ {number_text(high)}'


def _report_qr(share_url, filename, chart, generated_at):
    from urllib.parse import urlsplit

    import qrcode
    from qrcode.exceptions import DataOverflowError
    from qrcode.image.svg import SvgPathFillImage

    share_url = share_url.strip()
    if share_url:
        try:
            parsed = urlsplit(share_url)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
                     and not any(ch.isspace() or ord(ch) < 32 for ch in share_url))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('QR 공유 주소는 http:// 또는 https://로 시작하는 전체 주소를 입력하세요.')
        payload, caption = share_url, '보고서 공유 링크'
    else:
        payload = '\n'.join((TITLE, f'데이터: {filename}', f'관리도: {chart.upper()}', f'생성: {generated_at}'))
        caption = '보고서 식별 정보'
    try:
        qr = qrcode.make(payload, image_factory=SvgPathFillImage)
    except DataOverflowError as exc:
        raise ValueError('QR에 담을 정보가 너무 깁니다. 짧은 공유 주소를 입력하세요.') from exc
    encoded = base64.b64encode(qr.to_string()).decode('ascii')
    image = f'<img src="data:image/svg+xml;base64,{encoded}" alt="{caption} QR 코드">'
    if share_url:
        image = f'<a href="{escape(share_url, quote=True)}">{image}</a>'
    return f'<figure class="report-qr">{image}<figcaption>{caption}</figcaption></figure>'


def publish_attribute_report(report_dir, public_base_url, build_report):
    """Write one report to a configured static web directory."""
    import os
    import secrets
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from urllib.parse import urlsplit

    if not report_dir.strip() or not (folder := Path(report_dir).resolve()).is_dir():
        raise ValueError('공개 보고서 저장 폴더에 접근할 수 없습니다.')
    host_url = public_base_url.strip().rstrip('/')
    address = urlsplit(host_url)
    if (address.scheme != 'https' or not address.hostname or address.username
            or address.query or address.fragment or any(ch.isspace() for ch in host_url)):
        raise ValueError('공개 보고서 기본 주소를 HTTPS 경로로 설정하세요.')
    filename = secrets.token_urlsafe(24) + '.html'
    url = f'{host_url}/{filename}'
    html = build_report(qr_share_url=url, published=True)
    temp_path = None
    try:
        with NamedTemporaryFile(dir=folder, prefix='.report-', suffix='.tmp', delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(html.encode('utf-8'))
        if os.name != 'nt':
            temp_path.chmod(0o644)
        os.replace(temp_path, folder / filename)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return url, html


def _report_figure(image, css_class=''):
    if not image or not image[2]:
        return ''
    label, mime, data = image
    encoded = base64.b64encode(data).decode('ascii')
    return (f'<figure class="{css_class}"><figcaption>{escape(label)}</figcaption>'
            f'<img alt="{escape(label, quote=True)}" src="data:{mime};base64,{encoded}"></figure>')


def build_attribute_report(result, capability, process_info=(), filename='', chart_images=(), *,
                           trend_image=None, control_image=None, qr_share_url=None, published=False):
    """Offline HTML: the same metrics and subgroup values as the screen."""
    signals = signal_summary(result)
    ct = result.get('chart_type', 'p')
    frame = result_frame(result)
    headers = ''.join('<th class="' + ' '.join(f'view-{i}' for i, view in enumerate(VIEWS) if col in view_columns(frame, view, ct)) + '">' + escape(col) + '</th>' for col in frame)
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for col in frame:
            value = row[col]
            if isinstance(value, (float, np.floating)):
                text = number_text(value, 4) if col in ('CL','UCL','LCL','불량률 (p)','단위당결점수 (u)') else count_text(value)
            else:
                text = str(value)
            classes = ' '.join(f'view-{i}' for i, view in enumerate(VIEWS) if col in view_columns(frame, view, ct))
            cells.append(f'<td class="{classes}">{escape(text)}</td>')
        search = str(row['부분군']) + ' ' + str(row.get('측정일자/로트', ''))
        flagged = bool(row['감지 규칙'])
        rows.append(f'<tr tabindex="0" role="button" aria-label="부분군 {row["부분군"]} 상세" data-search="{escape(search, quote=True)}" data-signal="{int(flagged)}" class="{"signal" if flagged else ""}">' + ''.join(cells) + '</tr>')
    controls = '<div class="table-controls">' + ''.join(
        f'<button type="button" data-view="{i}" aria-pressed="{str(i == 0).lower()}">{view}</button>' for i, view in enumerate(VIEWS))
    controls += '<label>부분군·날짜/로트 검색 <input id="subgroup-search" type="search"></label><label><input id="signals-only" type="checkbox">이상 부분군만</label></div>'
    process_html = ''.join(f'<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>' for key, value in process_info)
    detail_html = ''.join(f'<tr><th>{escape(metric["label"])}</th><td style="{CELL_STYLES[metric["state"]]}">{escape(metric["value"])}</td><td>{escape(metric["note"])}</td></tr>' for metric in detail_metrics(result, capability))
    images_html = ''.join(_report_figure(image, 'extra-chart') for image in chart_images)
    trend_html = _report_figure(trend_image, 'trend-chart')
    control_html = _report_figure(control_image, 'control-chart')
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
    qr_html = _report_qr(qr_share_url, filename, ct, generated_at) if qr_share_url is not None else ''
    robots = '<meta name="robots" content="noindex, nofollow">' if published else ''
    interval_label, interval, method = _rate_interval(result)
    limits = ''.join(f'<div><dt>{key.upper()}</dt><dd>{_limit_text(result.get(key))}</dd></div>'
                     for key in ('cl', 'ucl', 'lcl'))
    variable_limits = ' · 변동 한계는 부분군별 최솟값–최댓값' if ct in ('p', 'u') else ''
    limit_method = result.get('limit_method_label', '')
    method_note = f'<p class="note">{escape(limit_method)}</p>' if limit_method else ''
    diagnose = (f'<aside class="report-diagnostics"><h2>품질성과·관리한계</h2>'
                f'<strong>{escape(interval_label)} {escape(interval)}</strong><p class="note">{escape(method)}</p>'
                f'<dl>{limits}</dl><p class="note">기준 {escape(str(result.get("limit_reference_label", "전체 데이터")))}{variable_limits}</p>'
                f'{method_note}</aside>')
    trend_section = f'<div class="report-trend{"" if trend_html else " no-trend"}">{trend_html}{diagnose}</div>'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">{robots}<meta name="viewport" content="width=device-width,initial-scale=1"><title>{TITLE}</title>
<style>{PRESENTATION_CSS}
body{{font-family:"Malgun Gothic",sans-serif;background:#fff;color:#1e293b;margin:24px;line-height:1.6}}main{{max-width:1400px;margin:auto}}
h1{{background:linear-gradient(135deg,#1e3a8a,#0e7490);color:white;padding:22px;border-radius:8px;font-size:26px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:9px 12px;border:1px solid #cbd5e1;text-align:left;white-space:nowrap}}th{{background:#e2e8f0;color:#1e293b}}
.table-wrap{{overflow:auto;max-height:650px;border:1px solid #cbd5e1}}#attr-results th{{position:sticky;top:0;z-index:2}}#attr-results th:first-child,#attr-results td:first-child{{position:sticky;left:0;background:#f1f5f9;color:#1e293b;z-index:1}}#attr-results th:first-child{{z-index:3}}
#attr-results tr.signal td{{background:#fee2e2;color:#991b1b}}#attr-results tbody tr{{cursor:pointer}}#attr-results tr:focus{{outline:2px solid #1d4ed8;outline-offset:-2px}}
.table-controls{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}}button,input{{font:inherit}}button{{padding:7px 12px;border:1px solid #94a3b8;border-radius:5px;background:#fff;color:#1e293b;cursor:pointer}}button[aria-pressed=true]{{background:#1e3a8a;color:#fff}}button:focus-visible,input:focus-visible{{outline:2px solid #1d4ed8}}
#attr-results[data-view="0"] tr>:not(.view-0),#attr-results[data-view="1"] tr>:not(.view-1){{display:none}}
small,.note{{color:#475569}}img{{max-width:100%;height:auto}}figure{{margin:0}}.state{{padding:8px 12px;background:{'#fee2e2' if signals['total'] else '#dcfce7'};color:{'#991b1b' if signals['total'] else '#166534'};border-radius:6px;font-weight:700}}
.report-intro{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}
.report-intro h1{{margin:0;padding:12px 16px}}
.report-intro p{{margin:5px 0}}
.report-qr{{flex:0 0 auto;text-align:center;font-size:11px}}
.report-qr img{{display:block;width:94px;height:94px;margin:auto}}
.report-overview .attr-metrics{{grid-template-columns:repeat({6 if ct in ('p','np') else 5},minmax(0,1fr));gap:8px;margin:8px 0}}
.report-overview .attr-metric{{padding:8px}}.report-overview .attr-metric strong{{font-size:20px}}
.report-overview .attr-metric small{{font-size:11px}}
.report-trend{{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:10px;margin:8px 0}}
.report-trend.no-trend{{grid-template-columns:1fr}}
.trend-chart,.control-chart,.report-diagnostics,.extra-chart{{border:1px solid #94a3b8;border-radius:8px;padding:8px;min-width:0;break-inside:avoid}}
.trend-chart img,.control-chart img,.extra-chart img{{display:block;width:100%;object-fit:contain}}
.trend-chart figcaption,.control-chart figcaption,.extra-chart figcaption,.report-diagnostics h2{{margin:0 0 4px;font-size:14px;font-weight:700}}
.report-diagnostics strong{{color:#0f172a}}.report-diagnostics .note{{margin:4px 0;font-size:12px}}
.report-diagnostics dl{{margin:8px 0;font-variant-numeric:tabular-nums}}
.report-diagnostics dl div{{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #e2e8f0;padding:2px 0}}
.report-diagnostics dd{{margin:0;font-weight:700}}
.report-tables{{margin-top:14px}}.report-tables details{{margin:6px 0}}
.report-tables details td{{white-space:normal;overflow-wrap:anywhere}}
.extra-chart{{margin:8px 0}}
@media screen and (max-width:850px){{
  .report-overview .attr-metrics{{grid-template-columns:repeat(3,minmax(0,1fr))}}
  .report-trend{{grid-template-columns:1fr}}
}}
@media screen and (max-width:500px){{
  body{{margin:10px}}.report-intro h1{{font-size:18px;padding:7px 9px}}.report-qr img{{width:78px;height:78px}}
  .report-overview .attr-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}
}}
@page{{size:A4 landscape;margin:6mm}}
@media print{{
  *{{box-sizing:border-box;print-color-adjust:exact;-webkit-print-color-adjust:exact}}
  body{{margin:0;font-size:9pt;line-height:1.2}}main{{max-width:none}}
  .report-intro h1{{font-size:16pt;padding:2mm 3mm}}.report-intro p{{margin:1mm 0}}
  .report-qr img{{width:22mm;height:22mm}}.report-qr figcaption{{font-size:7pt}}
  .state{{margin:1mm 0;padding:1mm 2mm}}
  .report-overview .attr-metrics{{gap:1mm;margin:1.5mm 0}}
  .report-overview .attr-metric{{padding:1mm 1.5mm}}
  .report-overview .attr-metric div,.report-overview .attr-metric small{{font-size:7.5pt;line-height:1.2}}
  .report-overview .attr-metric strong{{font-size:12pt}}
  .report-trend{{gap:2mm;margin:1.5mm 0;grid-template-columns:minmax(0,125mm) minmax(0,1fr)}}
  .trend-chart,.control-chart,.report-diagnostics,.extra-chart{{padding:1.5mm}}
  .report-diagnostics .note{{font-size:8pt}}.report-diagnostics dl{{margin:1.5mm 0}}
  .trend-chart figcaption,.control-chart figcaption,.extra-chart figcaption,.report-diagnostics h2{{font-size:9pt}}
  .trend-chart img{{max-height:46mm}}.control-chart img{{max-height:80mm}}.extra-chart img{{max-height:178mm}}
  .report-tables,.extra-chart{{break-before:page;margin-top:0}}
  .table-controls,#selected-row{{display:none!important}}#attr-results tr{{display:table-row!important;break-inside:avoid}}
  #attr-results th,#attr-results td{{display:table-cell!important;position:static!important;white-space:normal;overflow-wrap:anywhere}}
  .table-wrap{{overflow:visible;max-height:none}}details{{break-inside:avoid}}
  table{{font-size:8pt}}th,td{{padding:0.8mm}}
}}
</style></head><body><main>
<section class="report-overview">
<header class="report-intro"><div><h1>{TITLE}</h1>
<p class="note">생성 {generated_at} · {escape(filename)} · {escape(ct.upper())} 관리도 · {escape(str(result.get('control_phase_label', '해석용')))}</p></div>{qr_html}</header>
<p class="state">{escape(signals['text'])}</p>
{metrics_html(core_metrics(result, capability))}{trend_section}{control_html}
</section>
{images_html}
<section class="report-tables">
<details><summary>품질성과 상세 · DPMO·Z·시그마</summary><div><table>{detail_html}</table></div></details>
<details><summary>공정 식별 정보</summary><div><table>{process_html}</table></div></details>
<h2>부분군 분석 결과</h2>{controls}<p id="row-count" class="note"></p><div class="table-wrap"><table id="attr-results" data-view="0"><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div><section id="selected-row" aria-live="polite"></section>
</section>
</main><script>
const table=document.getElementById('attr-results'), rows=[...table.tBodies[0].rows], detail=document.getElementById('selected-row');
function filterRows(){{const query=document.getElementById('subgroup-search').value.trim().toLowerCase(), signals=document.getElementById('signals-only').checked; rows.forEach(row=>row.hidden=!row.dataset.search.toLowerCase().includes(query)||(signals&&row.dataset.signal!=='1'));document.getElementById('row-count').textContent='표시 '+rows.filter(row=>!row.hidden).length+' / 전체 '+rows.length+'개 부분군';detail.replaceChildren();}}
document.querySelectorAll('[data-view]').forEach(button=>{{if(button.tagName!=='BUTTON')return;button.addEventListener('click',()=>{{table.dataset.view=button.dataset.view;document.querySelectorAll('button[data-view]').forEach(other=>other.setAttribute('aria-pressed',String(other===button)));}});}});
document.getElementById('subgroup-search').addEventListener('input',filterRows);document.getElementById('signals-only').addEventListener('change',filterRows);
function selectRow(row){{detail.replaceChildren();const heading=document.createElement('h3');heading.textContent='부분군 '+row.cells[0].textContent+' 상세';detail.append(heading);const list=document.createElement('dl');[...row.cells].forEach((cell,i)=>{{const key=document.createElement('dt'),value=document.createElement('dd');key.textContent=table.tHead.rows[0].cells[i].textContent;value.textContent=cell.textContent||'—';list.append(key,value);}});detail.append(list);}}
rows.forEach(row=>{{row.addEventListener('click',()=>selectRow(row));row.addEventListener('keydown',event=>{{if(event.key==='Enter'||event.key===' '){{event.preventDefault();selectRow(row);}}}});}});filterRows();
window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(el=>{{el.dataset.wasOpen=String(el.open);el.open=true;}}));window.addEventListener('afterprint',()=>document.querySelectorAll('details').forEach(el=>el.open=el.dataset.wasOpen==='true'));
</script></body></html>'''
