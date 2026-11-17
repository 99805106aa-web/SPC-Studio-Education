"""Small, deterministic synthetic classroom exercises; no business data."""
import io
import numpy as np
import pandas as pd


def sample_csvs():
    rng = np.random.default_rng(20261115)
    data = pd.DataFrame({"직경": rng.normal(10.0, 0.025, 150),
                         "두께": rng.normal(2.0, 0.012, 150)})
    spec = pd.DataFrame({"항목": ["직경", "두께"], "USL": [10.1, 2.05],
                         "LSL": [9.9, 1.95], "Target": [10.0, 2.0]})
    return tuple(df.to_csv(index=False).encode("utf-8-sig") for df in (data, spec))


def sample_workbook():
    data, spec = sample_csvs()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.read_csv(io.BytesIO(data)).to_excel(writer, sheet_name='DATA', index=False)
        pd.read_csv(io.BytesIO(spec)).to_excel(writer, sheet_name='SPEC', index=False)
    return output.getvalue()


def render_samples():
    import streamlit as st
    from spc_access import ensure_active_day
    data, spec = sample_csvs()
    workbook = sample_workbook()
    with st.expander("🎓 교육용 예제", expanded=True):
        st.caption("합성 데이터 150행 · 직경/두께 2항목 · 부분군 크기 5로 실습하세요.")
        # Callback runs before uploader widgets are instantiated in the next rerun.
        def load():
            ensure_active_day()
            for key in list(st.session_state):
                if key.startswith(("data_uploader", "spec_uploader")):
                    del st.session_state[key]
            st.session_state.update(data_bytes=workbook, spec_bytes=workbook, n=5,
                data_file_name="SPC_교육_통합예제.xlsx", spec_file_name="SPC_교육_통합예제.xlsx",
                active_data_path="", active_spec_path="", data_sheet='DATA', spec_sheet='SPEC',
                data_layout_mode="long", selected_columns=None, measurement_date_column="",
                manual_target_enabled=False, analysis_done=False, results=[], summary_df=None,
                _loaded_fav_name="교육용 합성 예제", ai_var_cache={}, chat_history=[])
            st.session_state.pop("_preflight_cached", None)
        st.button("예제 불러오기", on_click=load, use_container_width=True)
        st.download_button('통합 예제 Excel 내려받기', workbook, 'SPC_교육_통합예제.xlsx',
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        st.download_button("예제 데이터 CSV", data, "SPC_교육_데이터.csv", "text/csv")
        st.download_button("예제 규격 CSV", spec, "SPC_교육_규격.csv", "text/csv")
