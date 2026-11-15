"""Small, deterministic synthetic classroom exercises; no business data."""
import numpy as np
import pandas as pd


def sample_csvs():
    rng = np.random.default_rng(20261115)
    data = pd.DataFrame({"직경": rng.normal(10.0, 0.025, 150),
                         "두께": rng.normal(2.0, 0.012, 150)})
    spec = pd.DataFrame({"항목": ["직경", "두께"], "USL": [10.1, 2.05],
                         "LSL": [9.9, 1.95], "Target": [10.0, 2.0]})
    return tuple(df.to_csv(index=False).encode("utf-8-sig") for df in (data, spec))


def render_samples():
    import streamlit as st
    from spc_access import ensure_active_day
    data, spec = sample_csvs()
    with st.expander("🎓 교육용 예제", expanded=True):
        st.caption("합성 데이터 150행 · 직경/두께 2항목 · 부분군 크기 5로 실습하세요.")
        # Callback runs before uploader widgets are instantiated in the next rerun.
        def load():
            ensure_active_day()
            for key in list(st.session_state):
                if key.startswith(("data_uploader", "spec_uploader")):
                    del st.session_state[key]
            st.session_state.update(data_bytes=data, spec_bytes=spec, n=5,
                data_file_name="SPC_교육_데이터.csv", spec_file_name="SPC_교육_규격.csv",
                active_data_path="", active_spec_path="", data_sheet=None, spec_sheet=None,
                data_layout_mode="long", selected_columns=None, measurement_date_column="",
                manual_target_enabled=False, analysis_done=False, results=[], summary_df=None,
                _loaded_fav_name="교육용 합성 예제", ai_var_cache={}, chat_history=[])
            st.session_state.pop("_preflight_cached", None)
        st.button("예제 불러오기", on_click=load, use_container_width=True)
        st.download_button("예제 데이터 CSV", data, "SPC_교육_데이터.csv", "text/csv")
        st.download_button("예제 규격 CSV", spec, "SPC_교육_규격.csv", "text/csv")
