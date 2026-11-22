"""Configuration and results panel for extended Shewhart Xbar-S."""

import hashlib

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from spc_extended import (EXTENDED_MODE, GUIDE, METHODS, STAGES, TITLE, calculate,
                          calculation_note, display_panels, mean_axis_limits, monitor_signals, settings_key,
                          subgroup_ids, tool_states, unsupported_reason)
from spc_export import fig_to_bytes
from spc_fast import cached_frame
from spc_charts import (DEFAULT_PALETTE, EXTENDED_BASELINE_OPACITY, EXTENDED_PANEL_TITLES, PALETTES,
                        build_extended_figure, control_chart_layout, control_chart_limits,
                        extended_chart_phases, extended_chart_window, extended_transition_labels)


def _source_frame():
    request = st.session_state.get("_analysis_request", {})
    if not request.get("data_bytes"):
        return None
    return cached_frame(st.session_state["_bounded_cache"], request["data_bytes"], request.get("data_sheet"))


def _key(item):
    return settings_key(item, st.session_state.get("analysis_meta", {}).get("analysis_id"))


def _source(frame, config):
    name = config.get("column")
    return frame[name] if frame is not None and name in frame.columns else None


def with_extended_results(items):
    state = st.session_state
    configurations = state.get("_extended_config", {})
    analysis_id = state.get("analysis_meta", {}).get("analysis_id")
    active = {key: value for key, value in configurations.items() if key[0] == analysis_id}
    sample = state.get('_education_extended_data')
    request = state.get('_analysis_request', {})
    if sample and request.get('data_bytes') == sample and request.get('chart_usage_mode') == EXTENDED_MODE:
        from spc_education import EXTENDED_SAMPLE_N, EXTENDED_SAMPLE_SHEET
        if request.get('data_sheet') == EXTENDED_SAMPLE_SHEET:
            for item in items:
                if item['column'] == '외경' and item['n'] == EXTENDED_SAMPLE_N and not unsupported_reason(item):
                    active.setdefault(_key(item), {'method': 'stages', 'column': '채취 단계', 'confidence': .99})
    state["_extended_config"] = active
    if not active:
        return items
    frame = _source_frame() if any(c.get("column") is not None for c in active.values()) else None
    results = []
    for item in items:
        config = active.get(_key(item)) if not unsupported_reason(item) else None
        if config:
            item = dict(item)
            item.pop("extended_shewhart", None)
            item.pop("extended_shewhart_error", None)
            try:
                item["extended_shewhart"] = calculate(item, config, _source(frame, config))
            except ValueError as exc:
                item["extended_shewhart_error"] = str(exc)
        results.append(item)
    return results


def _interactive(result, table, palette=DEFAULT_PALETTE, *, compare=False):
    colors = PALETTES.get(palette, PALETTES[DEFAULT_PALETTE])
    panels = display_panels(compare)
    last_row = len(panels)
    fig = make_subplots(rows=last_row, cols=1, shared_xaxes=True,
                        vertical_spacing=.12 if compare else .15,
                        subplot_titles=[EXTENDED_PANEL_TITLES[name] for name, _ in panels])
    left, right = extended_chart_window(table)
    _, _, boundary = extended_chart_phases(result["table"], result.get("baseline_requested_count"))
    transition = extended_transition_labels(table, boundary)
    shaded_end = right if boundary is None else min(right, boundary)
    control_chart_layout(fig, colors, "<b>확장 슈하르트 관리도</b>",
                         height=(800 if compare else 640) + (34 if transition else 0),
                         top=106, right=128, bottom=94 if transition else 60)
    ink = fig.layout.font.color
    for annotation in fig.layout.annotations:
        annotation.update(x=0, xanchor="left", font=dict(size=15, color=ink))
    for row, (name, value) in enumerate(panels, 1):
        lower, center, upper = result["limits"][name]
        bad = table[name + " 판정"] != "이내"
        custom = table[["구간", "공구 상태", name + " 판정"]].to_numpy()
        hover = ("<b>부분군 %{x} · %{customdata[0]}</b><br>" + value + " %{y:.8g}<br>"
                 "%{customdata[1]} · %{customdata[2]}<br>"
                 f"UCL {upper:.6g} / CL {center:.6g} / LCL {lower:.6g}<extra></extra>")
        fig.add_trace(go.Scatter(x=table["부분군"], y=table[value], mode="lines+markers",
                                 name="관측값", showlegend=row == 1, legendgroup="values",
                                 line=dict(color=colors["primary"], width=2.2), marker=dict(size=7),
                                 customdata=custom, hovertemplate=hover), row=row, col=1)
        fig.add_trace(go.Scatter(x=table.loc[bad, "부분군"], y=table.loc[bad, value], mode="markers",
                                 name="관리한계 이탈", showlegend=row == 1, legendgroup="signals",
                                 marker=dict(color=colors["danger"], size=12,
                                             line=dict(color=colors["ax_bg"], width=1.2)),
                                 customdata=custom[bad], hovertemplate=hover), row=row, col=1)
        if left < shaded_end:
            fig.add_vrect(x0=left, x1=shaded_end, fillcolor=colors["primary"], opacity=EXTENDED_BASELINE_OPACITY,
                          line_width=0, layer="below", row=row, col=1)
            suffix = "" if row == 1 else str(row)
            fig.add_shape(type="line", x0=left, x1=shaded_end, y0=.993, y1=.993,
                          xref=f"x{suffix}", yref=f"y{suffix} domain", line=dict(color=colors["primary"], width=3))
        if boundary is not None and left < boundary < right:
            fig.add_vline(x=boundary, line_dash="dash", line_width=2.5, line_color=colors["primary"], row=row, col=1)
        if name == "확장 X̄":
            fig.add_hrect(y0=result["lower"], y1=result["upper"], fillcolor=colors["success"],
                          opacity=.12, line_width=0, layer="below", row=row, col=1)
        limits = [("UCL", upper), ("CL", center), ("LCL", lower)]
        control_chart_limits(fig, colors, table[value], limits, row=row,
                             axis_range=mean_axis_limits(result, table) if value == "평균" else None)
    fig.update_xaxes(range=[left, right], showspikes=False)
    fig.update_xaxes(title=dict(text="원본 부분군", standoff=40 if transition else 12), row=last_row, col=1)
    for number, text, align in transition:
        fig.add_annotation(x=number, y=0, yshift=-30, xref=f"x{last_row}", yref=f"y{last_row} domain",
                           text=f"<b>{text}</b>", showarrow=False, xanchor=align, yanchor="top",
                           font=dict(size=12, color=ink))
    if len(table) <= 20:
        fig.update_xaxes(tickvals=table["부분군"].tolist(), row=last_row, col=1)
    return fig


def render_extended(item, items, palette):
    if item.get("chart_usage_mode") != EXTENDED_MODE:
        return
    with st.container():
        st.subheader(TITLE)
        reason = unsupported_reason(item)
        if reason:
            st.caption(reason)
            return
        key = _key(item)
        prefix = "extended_" + hashlib.sha256(repr(key).encode()).hexdigest()[:18]
        result = item.get("extended_shewhart")
        error = item.get("extended_shewhart_error")
        if error:
            st.warning(error)
        if result is not None:
            st.caption(calculation_note(result))
            st.caption(GUIDE)
            monitor = monitor_signals(result)
            if monitor.empty:
                st.info("감시구간이 비어 있습니다. 기준구간 이후의 부분군을 추가하면 이탈을 확인할 수 있습니다.")
            else:
                for card, (name, _) in zip(st.columns(2), display_panels()):
                    signals = int(monitor[name].sum())
                    card.metric(f"감시구간 · {name} 이탈", f"{signals:,} / {len(monitor):,}", border=True)
            if (result["table"]["S 판정"] != "이내").any():
                st.warning("S 관리도 이탈이 있습니다. 확장 X̄를 해석하기 전에 산포 이탈 원인을 확인하세요.")
        ids = subgroup_ids(item)
        pages = (len(ids) + 199) // 200
        page = int(st.number_input("부분군 표 페이지 (200개씩)", 1, pages, key=prefix + "_page")) if pages > 1 else 1
        window = slice((page - 1) * 200, page * 200)
        _render_settings(item, items, key, prefix, ids[window], window.start, expanded=bool(error))
        if result is None:
            return
        compare = st.checkbox("일반 X̄ 비교 표시", key=prefix + "_comparison")
        reference, monitoring, _ = extended_chart_phases(result["table"], result.get("baseline_requested_count"))
        st.markdown(f"**{reference}**　|　**{monitoring}**")
        st.caption("음영은 초기 기준구간이며 세로 점선은 설정한 기준 경계입니다. 녹색 띠는 예상 평균 범위, 빨간 점은 관리한계 이탈입니다."
                   if not monitor.empty else "음영은 초기 기준구간입니다. 녹색 띠는 예상 평균 범위, 빨간 점은 관리한계 이탈입니다.")
        table = result["table"].iloc[window]
        st.plotly_chart(_interactive(result, table, palette, compare=compare), key=prefix + "_chart", theme=None,
                        config={"scrollZoom": False, "displaylogo": False})
        decisions = table if compare else table.drop(columns="일반 X̄ 판정")
        with st.expander("부분군별 판정"):
            st.dataframe(decisions, hide_index=True)
            st.download_button("현재 구간 판정 CSV 저장", decisions.to_csv(index=False).encode("utf-8-sig"),
                               file_name="extended-shewhart-decisions.csv", mime="text/csv",
                               key=prefix + "_decisions", on_click="ignore")
        if st.button("현재 구간 확장 관리도 PNG 생성", key=prefix + "_png_button"):
            fig = build_extended_figure(result, palette, table=table, compare=compare)
            try:
                st.download_button("확장 관리도 PNG 저장", fig_to_bytes(fig), "extended-shewhart.png",
                                   "image/png", key=prefix + "_png", on_click="ignore")
            finally:
                plt.close(fig)


def _render_settings(item, items, key, prefix, visible, start, *, expanded):
    with st.expander("확장 관리도 설정 · 예상 평균 범위와 공구 상태", expanded=expanded):
        configs = st.session_state.setdefault("_extended_config", {})
        saved = configs.get(key, {})
        frame = _source_frame()
        used = {str(i["column"]) for i in items}
        used.update(item.get("subgroup_columns", []))
        used.add(st.session_state.get("_analysis_request", {}).get("measurement_date_column"))
        columns = [None] + ([c for c in frame.columns if str(c) not in used] if frame is not None else [])
        previous_column = saved.get("column")
        column = st.selectbox("공구 상태 열", columns,
                              index=columns.index(previous_column) if previous_column in columns else 0,
                              format_func=lambda c: "화면에서 지정" if c is None else str(c),
                              help="측정 항목으로 선택하지 않은 열을 사용합니다. 세로형은 같은 부분군의 모든 행에 상태를 입력하세요.",
                              key=prefix + "_column")
        method = st.radio("예상 평균 범위", list(METHODS), format_func=METHODS.get,
                          index=list(METHODS).index(saved.get("method", "extremes")), key=prefix + "_method")
        confidence = st.selectbox("비개입 확률", [.99, .9973], format_func=lambda v: f"{v:.2%}",
                                  index=int(saved.get("confidence") == .9973), key=prefix + "_confidence")
        config = dict(method=method, confidence=confidence, column=column)
        if method == "manual":
            lo, hi = st.columns(2)
            config["lower"] = lo.text_input("예상 평균 하단", value=saved.get("lower", ""), key=prefix + "_lower")
            config["upper"] = hi.text_input("예상 평균 상단", value=saved.get("upper", ""), key=prefix + "_upper")
        overrides = dict(saved.get("overrides", {}))
        states = tool_states(visible, item["n"], item.get("data_layout_mode"), _source(frame, config))
        editor = pd.DataFrame({"부분군": visible,
                               "구간": np.where(start + np.arange(len(visible)) < item["baseline_display_count"], "기준", "감시"),
                               "파일 상태": [label for _, label in states],
                               "화면 지정": [overrides.get(int(i), "파일 상태 사용") for i in visible]})
        editor_id = hashlib.sha256(repr((column, start, overrides)).encode()).hexdigest()[:12]
        with st.form(prefix + "_form"):
            edited = st.data_editor(editor, hide_index=True, disabled=["부분군", "구간", "파일 상태"],
                                    column_config={"화면 지정": st.column_config.SelectboxColumn(
                                        options=["파일 상태 사용", *STAGES], required=True)},
                                    key=prefix + "_states_" + editor_id)
            if st.form_submit_button("확장 관리도 설정 적용"):
                for number, value in zip(visible, edited["화면 지정"]):
                    if value in STAGES:
                        overrides[int(number)] = value
                    else:
                        overrides.pop(int(number), None)
                configs[key] = dict(config, overrides=overrides)
                st.rerun()
