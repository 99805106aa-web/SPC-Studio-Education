# -*- coding: utf-8 -*-
"""
app.py  —  SPC Studio · 계량형 (Streamlit)
원본: 공정능력_관리도_Rev26.py  →  웹 변환 버전

실행:
    streamlit run app.py
"""

import io
import sys
import os
import re
import math
import json
import html
import base64
import hashlib
import importlib
import importlib.util

import numpy as np
import pandas as pd

# 현재 디렉토리를 경로에 추가 (모듈 임포트용)
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from spc_access import education_mode, render_access_gate, ai_available

# 실행 중 추가된 모듈은 네트워크 드라이브의 디렉터리 캐시에 없을 수 있다.
importlib.invalidate_caches()
try:
    from spc_secrets import (
        KEY_NAMES,
        is_protected_cache,
        migrate_config,
        protect_cache_bytes,
        protect_key,
        unprotect_cache_bytes,
        unprotect_key,
    )
except ModuleNotFoundError as exc:
    if exc.name != "spc_secrets":
        raise
    _secret_spec = importlib.util.spec_from_file_location(
        "spc_secrets", os.path.join(_APP_DIR, "spc_secrets.py")
    )
    _secret_module = importlib.util.module_from_spec(_secret_spec)
    _secret_spec.loader.exec_module(_secret_module)
    sys.modules["spc_secrets"] = _secret_module
    KEY_NAMES = _secret_module.KEY_NAMES
    is_protected_cache = _secret_module.is_protected_cache
    migrate_config = _secret_module.migrate_config
    protect_cache_bytes = _secret_module.protect_cache_bytes
    protect_key = _secret_module.protect_key
    unprotect_cache_bytes = _secret_module.unprotect_cache_bytes
    unprotect_key = _secret_module.unprotect_key

# ─────────────────────────── 설정 파일 저장/불러오기 ───────────────────────────
_CONFIG_PATH = os.path.join(_APP_DIR, "spc_config.json")
_LAST_ENV_CACHE_DIR = os.path.join(_APP_DIR, ".spc_last_env_cache")
_LAST_ENV_DATA_CACHE_PATH = os.path.join(_LAST_ENV_CACHE_DIR, "last_data.bin")
_LAST_ENV_SPEC_CACHE_PATH = os.path.join(_LAST_ENV_CACHE_DIR, "last_spec.bin")

def _browse_file(buf_key: str):
    """Windows 파일 탐색기를 열고 선택한 경로를 버퍼 키에 저장.
    버퍼 키는 위젯 key가 아닌 별도 키 — 위젯 렌더링 전에 복사함."""
    if education_mode():
        return
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    path = filedialog.askopenfilename(
        title="파일 선택",
        filetypes=[
            ("Excel/CSV 파일", "*.xlsx *.xls *.csv"),
            ("모든 파일", "*.*"),
        ],
    )
    root.destroy()
    if path:
        st.session_state[buf_key] = os.path.normpath(path)


def _norm_path(p: str) -> str:
    s = str(p or "").strip().strip('"').strip("'")
    return os.path.normpath(s) if s else ""


def _safe_float(v):
    try:
        fv = float(v)
    except Exception:
        return None
    return fv if math.isfinite(fv) else None


def _safe_nonneg_int(v):
    try:
        iv = int(v)
    except Exception:
        return None
    return iv if iv >= 0 else None


def _ensure_last_env_cache_dir():
    try:
        os.makedirs(_LAST_ENV_CACHE_DIR, exist_ok=True)
    except Exception:
        pass


def _write_last_env_cache(path: str, raw_bytes: bytes | None) -> str:
    if education_mode():
        return ""
    if not raw_bytes:
        return ""
    _ensure_last_env_cache_dir()
    try:
        protected = protect_cache_bytes(raw_bytes)
        with open(path, "wb") as f:
            f.write(protected)
        return path
    except Exception:
        return ""


def _read_last_env_cache(path: str) -> bytes:
    """암호화 캐시를 읽고, 기존 평문 캐시는 읽은 뒤 보호 형식으로 전환한다."""
    if education_mode():
        return b""
    with open(path, "rb") as f:
        stored = f.read()
    was_protected = is_protected_cache(stored)
    raw = unprotect_cache_bytes(stored)
    if raw and not was_protected:
        _write_last_env_cache(path, raw)
    return raw


def _normalize_manual_target_map(raw) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        try:
            fv = float(v)
        except Exception:
            continue
        if math.isfinite(fv):
            out[key] = fv
    return out


def _normalize_last_analysis_env(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    data_path = _norm_path(raw.get("data_path", ""))
    data_cache_path = _norm_path(raw.get("data_cache_path", ""))
    mode = str(raw.get("source_mode", "")).strip().lower()
    if mode not in {"path", "upload_cache"}:
        mode = "path" if data_path else ("upload_cache" if data_cache_path else "")
    if mode == "path" and not data_path:
        mode = "upload_cache" if data_cache_path else ""
    if mode == "upload_cache" and not data_cache_path:
        mode = "path" if data_path else ""
    if mode not in {"path", "upload_cache"}:
        return {}

    data_sheet = raw.get("data_sheet")
    spec_path = _norm_path(raw.get("spec_path", ""))
    spec_cache_path = _norm_path(raw.get("spec_cache_path", ""))
    spec_sheet = raw.get("spec_sheet")
    data_name = str(raw.get("data_name", "")).strip()
    spec_name = str(raw.get("spec_name", "")).strip()
    src = str(raw.get("source", "")).strip()
    analyzed_at = str(raw.get("analyzed_at", "")).strip()
    data_mtime = _safe_float(raw.get("data_mtime"))
    spec_mtime = _safe_float(raw.get("spec_mtime"))
    data_size = _safe_nonneg_int(raw.get("data_size"))
    spec_size = _safe_nonneg_int(raw.get("spec_size"))

    if not data_name:
        data_name = os.path.basename(data_path) if data_path else "uploaded_data"
    if not spec_name and spec_path:
        spec_name = os.path.basename(spec_path)
    return {
        "source_mode": mode,
        "data_path": data_path,
        "data_cache_path": data_cache_path,
        "data_name": data_name,
        "data_sheet": (str(data_sheet).strip() if data_sheet else None),
        "spec_path": spec_path,
        "spec_cache_path": spec_cache_path,
        "spec_name": spec_name,
        "spec_sheet": (str(spec_sheet).strip() if spec_sheet else None),
        "source": src,
        "analyzed_at": analyzed_at,
        "data_mtime": data_mtime,
        "spec_mtime": spec_mtime,
        "data_size": data_size,
        "spec_size": spec_size,
    }


def _resolve_sheet_or_default(file_path: str, raw_bytes: bytes, preferred_sheet):
    ext = os.path.splitext(file_path.lower())[1]
    if ext == ".csv":
        return None
    try:
        sheets = get_sheet_names(io.BytesIO(raw_bytes))
    except Exception:
        sheets = []
    if not sheets:
        return None
    pref = str(preferred_sheet).strip() if preferred_sheet else ""
    return pref if pref in sheets else sheets[0]


def _save_last_analysis_env():
    if education_mode():
        return
    data_bytes = st.session_state.get("data_bytes")
    if not data_bytes:
        return
    data_path = _norm_path(st.session_state.get("active_data_path", ""))
    if data_path and not os.path.isfile(data_path):
        data_path = ""

    spec_path = _norm_path(st.session_state.get("active_spec_path", ""))
    if spec_path and not os.path.isfile(spec_path):
        spec_path = ""
    spec_bytes = st.session_state.get("spec_bytes")

    source_mode = "path" if data_path else "upload_cache"
    data_cache_path = ""
    if source_mode == "upload_cache":
        data_cache_path = _write_last_env_cache(_LAST_ENV_DATA_CACHE_PATH, data_bytes)
        if not data_cache_path:
            return

    spec_cache_path = ""
    if spec_bytes and not spec_path:
        spec_cache_path = _write_last_env_cache(_LAST_ENV_SPEC_CACHE_PATH, spec_bytes)

    data_name = (
        str(st.session_state.get("data_file_name", "")).strip()
        or (os.path.basename(data_path) if data_path else "uploaded_data")
    )
    spec_name = (
        str(st.session_state.get("spec_file_name", "")).strip()
        or (os.path.basename(spec_path) if spec_path else "")
    )

    data_mtime = os.path.getmtime(data_path) if data_path else None
    data_size = os.path.getsize(data_path) if data_path else _safe_nonneg_int(len(data_bytes))
    spec_mtime = os.path.getmtime(spec_path) if spec_path else None
    spec_size = os.path.getsize(spec_path) if spec_path else (
        _safe_nonneg_int(len(spec_bytes)) if spec_bytes else None
    )

    _src = str(st.session_state.get("_loaded_fav_name", "")).strip() or data_name
    env = {
        "source_mode": source_mode,
        "data_path": data_path,
        "data_cache_path": data_cache_path,
        "data_name": data_name,
        "data_sheet": st.session_state.get("data_sheet"),
        "spec_path": spec_path,
        "spec_cache_path": spec_cache_path,
        "spec_name": spec_name,
        "spec_sheet": st.session_state.get("spec_sheet") if (spec_path or spec_cache_path) else None,
        "source": _src,
        "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data_mtime": data_mtime,
        "spec_mtime": spec_mtime,
        "data_size": data_size,
        "spec_size": spec_size,
    }
    st.session_state["last_analysis_env"] = _normalize_last_analysis_env(env)


def _restore_last_analysis_env():
    if education_mode():
        return
    if st.session_state.get("_last_env_restore_done", False):
        return
    st.session_state["_last_env_restore_done"] = True

    env = _normalize_last_analysis_env(st.session_state.get("last_analysis_env", {}))
    if not env:
        return

    data_mode = env.get("source_mode", "path")
    data_path = env.get("data_path", "")
    data_cache_path = env.get("data_cache_path", "")
    data_name = str(env.get("data_name", "")).strip()

    if data_mode == "path":
        if not data_path or not os.path.isfile(data_path):
            st.session_state["_last_env_notice"] = "마지막 데이터 파일을 찾을 수 없어 자동 분석을 건너뜀"
            st.session_state["_last_env_notice_icon"] = "⚠️"
            return
        try:
            with open(data_path, "rb") as f:
                data_raw = f.read()
        except Exception:
            st.session_state["_last_env_notice"] = "마지막 데이터 파일 읽기에 실패해 자동 분석을 건너뜀"
            st.session_state["_last_env_notice_icon"] = "⚠️"
            return
        st.session_state["active_data_path"] = data_path
        st.session_state["data_file_name"] = data_name or os.path.basename(data_path)
        data_sheet_ref = data_path
        data_label = os.path.basename(data_path)
    else:
        if not data_cache_path or not os.path.isfile(data_cache_path):
            st.session_state["_last_env_notice"] = "마지막 업로드 데이터 캐시를 찾을 수 없어 자동 분석을 건너뜀"
            st.session_state["_last_env_notice_icon"] = "⚠️"
            return
        try:
            data_raw = _read_last_env_cache(data_cache_path)
        except Exception:
            st.session_state["_last_env_notice"] = "마지막 업로드 데이터 캐시 읽기에 실패해 자동 분석을 건너뜀"
            st.session_state["_last_env_notice_icon"] = "⚠️"
            return
        st.session_state["active_data_path"] = ""
        st.session_state["data_file_name"] = data_name or os.path.basename(data_cache_path)
        data_sheet_ref = data_name or data_cache_path
        data_label = data_name or os.path.basename(data_cache_path)

    st.session_state["data_bytes"] = data_raw
    st.session_state["data_sheet"] = _resolve_sheet_or_default(
        data_sheet_ref, data_raw, env.get("data_sheet")
    )

    spec_notice = ""
    spec_path = env.get("spec_path", "")
    spec_cache_path = env.get("spec_cache_path", "")
    spec_name = str(env.get("spec_name", "")).strip()
    if spec_path and os.path.isfile(spec_path):
        try:
            with open(spec_path, "rb") as f:
                spec_raw = f.read()
            st.session_state["spec_bytes"] = spec_raw
            st.session_state["spec_sheet"] = _resolve_sheet_or_default(
                spec_path, spec_raw, env.get("spec_sheet")
            )
            st.session_state["active_spec_path"] = spec_path
            st.session_state["spec_file_name"] = spec_name or os.path.basename(spec_path)
        except Exception:
            st.session_state["spec_bytes"] = None
            st.session_state["spec_sheet"] = None
            st.session_state["active_spec_path"] = ""
            st.session_state["spec_file_name"] = ""
            spec_notice = "규격 파일 복원 실패"
    elif spec_cache_path and os.path.isfile(spec_cache_path):
        try:
            spec_raw = _read_last_env_cache(spec_cache_path)
            st.session_state["spec_bytes"] = spec_raw
            st.session_state["spec_sheet"] = _resolve_sheet_or_default(
                spec_name or spec_cache_path, spec_raw, env.get("spec_sheet")
            )
            st.session_state["active_spec_path"] = ""
            st.session_state["spec_file_name"] = spec_name or os.path.basename(spec_cache_path)
        except Exception:
            st.session_state["spec_bytes"] = None
            st.session_state["spec_sheet"] = None
            st.session_state["active_spec_path"] = ""
            st.session_state["spec_file_name"] = ""
            spec_notice = "규격 파일 복원 실패"
    else:
        st.session_state["spec_bytes"] = None
        st.session_state["spec_sheet"] = None
        st.session_state["active_spec_path"] = ""
        st.session_state["spec_file_name"] = ""
        if spec_path or spec_cache_path:
            spec_notice = "규격 파일이 없어 데이터만 복원됨"

    _src = str(env.get("source", "")).strip() or data_label
    st.session_state["_loaded_fav_name"] = _src
    # 시작 시 자동으로 분석을 실행하지 않고, 파일만 복원해 둔 채 초보자 설명 화면을 노출하도록 수정
    # st.session_state["_trigger_analysis"] = True
    st.session_state["_last_env_auto_trigger_done"] = True
    if spec_notice:
        st.session_state["_last_env_notice"] = f"마지막 분석 환경 복원됨: {data_label} ({spec_notice})"
        st.session_state["_last_env_notice_icon"] = "⚠️"
    else:
        st.session_state["_last_env_notice"] = f"마지막 분석 환경 복원됨: {data_label} · 분석 시작을 눌러주세요"
        st.session_state["_last_env_notice_icon"] = "📂"


def _show_loaded_file(kind: str):
    """업로드 여부와 관계없이 실제 분석에 사용할 파일과 시트를 표시한다."""
    raw = st.session_state.get(f"{kind}_bytes")
    path = st.session_state.get(f"active_{kind}_path", "")
    name = st.session_state.get(f"{kind}_file_name", "")
    if not raw:
        env = st.session_state.get("last_analysis_env", {})
        previous = env.get(f"{kind}_path") or env.get(f"{kind}_name")
        if previous:
            st.warning("마지막 분석 파일을 불러오지 못했습니다. 파일을 다시 선택해 주세요.")
            st.code(previous, language=None)
        return

    st.caption("현재 불러온 파일 · 분석 시작 시 사용")
    st.code(path or name or "업로드 파일", language=None)
    if not path:
        st.caption("업로드 파일은 원본 경로 대신 저장된 파일 내용으로 복원됩니다.")

    sheets = []
    if not (name or path).lower().endswith('.csv'):
        try:
            sheets = get_sheet_names(io.BytesIO(raw))
        except Exception:
            pass
    if sheets:
        sheet_key = f"{kind}_sheet"
        widget_key = f"{kind}_sheet_sel"
        preferred = st.session_state.get(sheet_key)
        st.session_state[widget_key] = preferred if preferred in sheets else sheets[0]

        def update_sheet():
            st.session_state[sheet_key] = st.session_state[widget_key]

        st.selectbox("시트 선택" if kind == "data" else "규격 시트",
                     sheets, key=widget_key, on_change=update_sheet)
        st.session_state[sheet_key] = st.session_state[widget_key]
    else:
        st.session_state[f"{kind}_sheet"] = None


def _encode_pw(pw: str) -> str:
    if not pw:
        return ""
    return "__b64__:" + base64.b64encode(pw.encode("utf-8")).decode()

def _decode_pw(encoded: str) -> str:
    if not encoded:
        return ""
    if encoded.startswith("__b64__:"):
        try:
            return base64.b64decode(encoded[8:].encode()).decode("utf-8")
        except Exception:
            return ""
    return encoded

def _load_config() -> dict:
    if education_mode():
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        secured, changed = migrate_config(cfg)
        if changed:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(secured, f, ensure_ascii=False, indent=2)
        for key in KEY_NAMES:
            cfg[key] = unprotect_key(cfg.get(key, ""))
        return cfg
    except Exception:
        return {}

PROCESS_INFO_FIELDS = [
    ("process_name", "공정명"),
    ("line", "라인/호기"),
    ("equipment", "설비명"),
    ("product", "제품명"),
    ("part_no", "품번"),
    ("lot_batch", "Lot/Batch"),
    ("operator_shift", "작업자/교대"),
    ("note", "비고"),
]


def _normalize_process_info(raw) -> dict[str, str]:
    """공정 식별 메타데이터를 고정된 라벨 딕셔너리로 정리한다."""
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, str] = {}
    for key, label in PROCESS_INFO_FIELDS:
        val = raw.get(label, raw.get(key, ""))
        out[label] = str(val or "").strip()
    return out


def _process_info_from_state() -> dict[str, str]:
    base = _normalize_process_info(st.session_state.get("process_info", {}))
    out: dict[str, str] = {}
    for key, label in PROCESS_INFO_FIELDS:
        state_key = f"process_info_{key}"
        out[label] = str(st.session_state.get(state_key, base.get(label, "")) or "").strip()
    return out


def _process_info_items(raw=None) -> list[tuple[str, str]]:
    info = _normalize_process_info(raw if raw is not None else _process_info_from_state())
    return [(label, value) for label, value in info.items() if str(value).strip()]


def _chart_mode_label(mode) -> str:
    return {
        "phase1": "해석용 관리도",
        "phase2": "관리용 관리도",
        EXTENDED_MODE: "확장 슈하르트 관리도",
    }.get(str(mode or "").strip(), str(mode or ""))


def _save_config():
    if education_mode():
        return
    startup_chart_mode = st.session_state.get("chart_usage_mode", "phase1")
    if startup_chart_mode == EXTENDED_MODE:
        startup_chart_mode = st.session_state.get("_standard_chart_usage_mode", "phase1")
    # SMTP 비밀번호는 저장하지 않는다. AI API 키는 사용자가 한 번 입력하면 재사용되도록 저장한다.
    cfg = {
        "n": st.session_state.get("n", 5),
        "include_incomplete": st.session_state.get("include_incomplete", False),
        "data_layout_mode": st.session_state.get("data_layout_mode", "long"),
        "subgroup_columns": st.session_state.get("subgroup_columns", []),
        "subgroup_var_name": st.session_state.get("subgroup_var_name", "측정치"),
        "measurement_date_column": st.session_state.get("measurement_date_column", ""),
        "chart_usage_mode": startup_chart_mode,
        "baseline_subgroups": st.session_state.get("baseline_subgroups", 25),
        "hist_bins": st.session_state.get("hist_bins", 30),
        "palette": st.session_state.get("palette", "trust"),
        "ksq_toggles": st.session_state.get("ksq_toggles", {}),
        "show_zones": st.session_state.get("show_zones", {}),
        "email": {
            "smtp_host": st.session_state.get("sb_smtp_host", "smtp.gmail.com"),
            "smtp_port": st.session_state.get("sb_smtp_port", 465),
            "smtp_user": st.session_state.get("sb_smtp_user", ""),
            "smtp_pass": "",
            "to_addrs": st.session_state.get("sb_to_addrs", ""),
            "use_ssl": st.session_state.get("sb_use_ssl", True),
            "use_starttls": st.session_state.get("sb_starttls", False),
            "only_recent": st.session_state.get("sb_only_recent", True),
        },
        "favorites": st.session_state.get("favorites", []),
        "last_analysis_env": _normalize_last_analysis_env(st.session_state.get("last_analysis_env", {})),
        "manual_target_enabled": bool(st.session_state.get("manual_target_enabled", False)),
        "manual_target_map": _normalize_manual_target_map(st.session_state.get("manual_target_map", {})),
        "auto_watch_enabled": st.session_state.get("auto_watch_enabled", False),
        "auto_watch_fav_idx": st.session_state.get("auto_watch_fav_idx", 0),
        "param_presets": st.session_state.get("param_presets", []),
        "email_history": st.session_state.get("email_history", [])[-10:],
        "beginner_mode": st.session_state.get("beginner_mode", False),
        "selected_columns": st.session_state.get("selected_columns"),
        "process_info": _process_info_from_state(),
        "gemini_api_key": protect_key(st.session_state.get("gemini_api_key", "")),
        "groq_api_key": protect_key(st.session_state.get("groq_api_key", "")),
        "openrouter_api_key": protect_key(st.session_state.get("openrouter_api_key", "")),
        "preprocess_outliers": st.session_state.get("preprocess_outliers", False),
        "outlier_method": st.session_state.get("outlier_method", "iqr"),
        "distribution_settings": st.session_state.get("distribution_settings", {}),
        "assessment_settings": st.session_state.get("assessment_settings", {}),
        "box_cox_transform": st.session_state.get("box_cox_transform", False),
    }
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _auto_detect_subgroup_columns(col_labels: list[str]) -> list[str]:
    """다열 부분군 후보 열을 자동 탐색한다."""
    if len(col_labels) < 2:
        return col_labels

    id_tokens = ("id", "index", "part", "lot", "no", "num", "번호", "부품", "순번")
    buckets: dict[str, list[tuple[int, str]]] = {}

    for col in col_labels:
        s = str(col).strip()
        m = re.match(r"^(.*?)[\s_\-]*([0-9]+)$", s)
        if not m:
            continue
        base_raw = (m.group(1) or "").strip()
        if not base_raw:
            continue
        base_norm = re.sub(r"[\s_\-]+", "", base_raw).lower()
        if any(tok in base_norm for tok in id_tokens):
            continue
        idx = int(m.group(2))
        buckets.setdefault(base_norm, []).append((idx, col))

    best: list[str] = []
    for _, pairs in buckets.items():
        if len(pairs) >= 2:
            cand = [name for _, name in sorted(pairs, key=lambda x: x[0])]
            if len(cand) > len(best):
                best = cand
    if len(best) >= 2:
        return best

    measure_tokens = ("측정", "measure", "meas", "value", "val", "spec")
    token_cols = [c for c in col_labels if any(tok in str(c).lower() for tok in measure_tokens)]
    if len(token_cols) >= 2:
        return token_cols

    fallback = [c for c in col_labels if not any(tok in str(c).lower() for tok in id_tokens)]
    if len(fallback) >= 2:
        return fallback[: min(8, len(fallback))]

    return col_labels[: min(5, len(col_labels))]


def _auto_detect_date_column(df: pd.DataFrame) -> str:
    """측정일자 후보 컬럼을 컬럼명과 datetime 파싱 성공률로 추천한다."""
    if df is None or df.empty:
        return ""
    name_tokens = ("측정일자", "측정일", "일자", "날짜", "date", "time")
    cols = list(df.columns)
    for col in cols:
        name = str(col).strip().lower()
        if any(tok in name for tok in name_tokens):
            return str(col)
    best_col = ""
    best_rate = 0.0
    for col in cols:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            continue
        if not (
            pd.api.types.is_datetime64_any_dtype(s)
            or pd.api.types.is_object_dtype(s)
            or pd.api.types.is_string_dtype(s)
        ):
            continue
        non_empty = s.dropna()
        if non_empty.empty:
            continue
        sample = non_empty.head(200)
        try:
            parsed = pd.to_datetime(sample, errors="coerce")
            rate = float(parsed.notna().mean())
        except Exception:
            rate = 0.0
        if rate > best_rate:
            best_rate = rate
            best_col = str(col)
    return best_col if best_rate >= 0.8 else ""


def _norm_key_for_match(v) -> str:
    return re.sub(r'\s+', '', str(v or "")).lower()


def _current_analysis_meta(preview: dict | None = None) -> dict:
    """리포트/화면에 공통으로 보여줄 분석 조건 요약."""
    ksq_on = [
        k for k, v in (st.session_state.get('ksq_toggles', {}) or {}).items()
        if bool(v)
    ]
    return {
        "data_name": st.session_state.get("data_file_name", "") or st.session_state.get("_loaded_fav_name", ""),
        "spec_name": st.session_state.get("spec_file_name", ""),
        "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "data_layout_mode": st.session_state.get("data_layout_mode", "long"),
        "n": int(st.session_state.get("n", 1)),
        "chart_usage_mode": st.session_state.get("chart_usage_mode", "phase1"),
        "chart_usage_mode_label": _chart_mode_label(st.session_state.get("chart_usage_mode", "phase1")),
        "baseline_subgroups": int(st.session_state.get("baseline_subgroups", 25)),
        "include_incomplete": bool(st.session_state.get("include_incomplete", False)),
        "preprocess_outliers": bool(st.session_state.get("preprocess_outliers", False)),
        "outlier_method": st.session_state.get("outlier_method", "iqr"),
        "distribution_settings": st.session_state.get("distribution_settings", {}),
        "box_cox_transform": bool(st.session_state.get("box_cox_transform", False)),
        "ksq_rules": ", ".join(ksq_on) if ksq_on else "T1",
        "subgroup_columns": list(st.session_state.get("subgroup_columns", []) or []),
        "measurement_date_column": st.session_state.get("measurement_date_column", ""),
        "process_info": _process_info_from_state(),
        "preview": preview or st.session_state.get("analysis_preview", {}),
    }


def _inspect_current_inputs() -> dict:
    """분석 전 데이터/규격 상태를 가볍게 점검한다."""
    out = {
        "ok": False,
        "data_loaded": bool(st.session_state.get("data_bytes")),
        "spec_loaded": bool(st.session_state.get("spec_bytes")),
        "rows": 0,
        "cols": 0,
        "numeric_cols": [],
        "numeric_count": 0,
        "missing_total": 0,
        "spec_count": 0,
        "matched_specs": [],
        "unmatched_specs": [],
        "missing_spec_cols": [],
        "match_rate": None,
        "wide_valid_groups": None,
        "wide_skipped_groups": None,
        "messages": [],
    }
    if not out["data_loaded"]:
        out["messages"].append("데이터 파일을 업로드하면 분석 전 점검이 표시됩니다.")
        return out

    try:
        df = load_data_df(
            io.BytesIO(st.session_state["data_bytes"]),
            sheet_name=st.session_state.get("data_sheet"),
        )
        out["rows"] = int(len(df))
        out["cols"] = int(len(df.columns))
        numeric_keys = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        num_cols = [str(c) for c in numeric_keys]
        out["numeric_cols"] = num_cols
        out["numeric_count"] = int(len(num_cols))
        out["missing_total"] = int(df[numeric_keys].isna().sum().sum()) if numeric_keys else 0
        out["ok"] = bool(num_cols)

        if st.session_state.get("data_layout_mode") == "wide_subgroup_cols":
            label_to_key = {str(c): c for c in numeric_keys}
            selected = [label_to_key[str(c)] for c in st.session_state.get("subgroup_columns", [])
                        if str(c) in label_to_key]
            if len(selected) >= 2:
                wide_df = df[selected].apply(pd.to_numeric, errors='coerce')
                valid_mask = wide_df.notna().all(axis=1)
                out["wide_valid_groups"] = int(valid_mask.sum())
                out["wide_skipped_groups"] = int((~valid_mask).sum())
            else:
                out["messages"].append("부분군 다열 모드에서는 측정 열을 2개 이상 선택해야 합니다.")

        spec_map = {}
        if out["spec_loaded"]:
            try:
                spec_map = load_spec_df(
                    io.BytesIO(st.session_state["spec_bytes"]),
                    sheet_name=st.session_state.get("spec_sheet"),
                )
            except Exception as e:
                out["messages"].append(f"규격 파일 읽기 실패: {e}")

        if spec_map:
            spec_keys = [str(k) for k in spec_map.keys()]
            spec_norm = {_norm_key_for_match(k): k for k in spec_keys}
            data_norm = {_norm_key_for_match(c): c for c in num_cols}
            matched_norm = sorted(set(spec_norm.keys()) & set(data_norm.keys()))
            out["spec_count"] = int(len(spec_keys))
            out["matched_specs"] = [str(data_norm[k]) for k in matched_norm]
            out["unmatched_specs"] = [str(spec_norm[k]) for k in spec_norm.keys() if k not in data_norm]
            out["missing_spec_cols"] = [str(data_norm[k]) for k in data_norm.keys() if k not in spec_norm]
            out["match_rate"] = (len(matched_norm) / len(num_cols)) if num_cols else None
        elif out["spec_loaded"]:
            out["messages"].append("규격 파일에서 유효한 규격 항목을 찾지 못했습니다.")
        else:
            out["missing_spec_cols"] = num_cols
    except Exception as e:
        out["messages"].append(f"데이터 점검 실패: {e}")
    return out


def _render_preflight_check(preview: dict):
    """사이드바 분석 시작 전 체크리스트/요약."""
    data_ok = bool(preview.get("data_loaded") and preview.get("numeric_count", 0) > 0)
    spec_ok = bool(preview.get("spec_loaded"))
    settings_ok = bool(
        st.session_state.get("data_layout_mode") != "wide_subgroup_cols"
        or len(st.session_state.get("subgroup_columns", [])) >= 2
    )
    if st.session_state.get('beginner_mode'):
        if data_ok:
            st.caption(f"측정 데이터 {preview.get('rows', 0):,}행 · 규격 {'선택됨' if spec_ok else '없음 (관리도만 확인)'}")
        if not settings_ok:
            st.warning('한 행에 함께 측정한 값이 들어 있는 열을 2개 이상 선택하세요.')
        if preview.get('unmatched_specs'):
            st.warning('데이터와 연결되지 않은 규격 항목: ' + ', '.join(preview['unmatched_specs'][:8]))
        if preview.get('missing_spec_cols'):
            st.caption('규격이 없는 항목은 공정능력 지수가 표시되지 않습니다.')
        for message in preview.get('messages', [])[:3]:
            st.caption(str(message))
        return
    steps = [
        ("1. 데이터 업로드", data_ok),
        ("2. 규격 확인", spec_ok),
        ("3. 분석 설정", settings_ok),
        ("4. 분석 시작", data_ok and settings_ok),
    ]
    st.markdown("### ✅ 분석 전 확인")
    for label, ok in steps:
        st.caption(("✅ " if ok else "⚪ ") + label)

    if data_ok:
        match = preview.get("match_rate")
        match_txt = f"{match * 100:.0f}%" if isinstance(match, float) else "규격 없음"
        st.caption(
            f"데이터: {preview.get('rows', 0):,}행 / 수치형 {preview.get('numeric_count', 0)}개 / "
            f"결측 {preview.get('missing_total', 0):,}개 / 규격 매칭 {match_txt}"
        )
        if preview.get("wide_valid_groups") is not None:
            st.caption(
                f"부분군 다열: 유효 부분군 {preview.get('wide_valid_groups', 0):,}개 / "
                f"제외 {preview.get('wide_skipped_groups', 0):,}개 / n={st.session_state.get('n', 0)}"
            )
        if preview.get("unmatched_specs"):
            st.warning("규격 파일 미매칭 항목: " + ", ".join(preview["unmatched_specs"][:8]))
        if preview.get("missing_spec_cols"):
            _miss = preview["missing_spec_cols"]
            st.info("규격 없는 변수: " + ", ".join(_miss[:8]) + (" ..." if len(_miss) > 8 else ""))
            st.caption("규격이 없는 변수는 Cpk/Ppk/PPM 계산이 제한될 수 있습니다.")
    for msg in preview.get("messages", [])[:3]:
        st.caption("• " + str(msg))

    mode_txt = "세로형" if st.session_state.get("data_layout_mode") == "long" else "부분군 다열"
    phase_txt = _chart_mode_label(st.session_state.get("chart_usage_mode"))
    spec_txt = "있음" if preview.get("spec_loaded") else "없음"
    prep = []
    if st.session_state.get("preprocess_outliers"):
        _method = "분위수 범위" if st.session_state.get("outlier_method") == "quantile" else "IQR"
        prep.append(f"이상치 제거({_method})")
    if st.session_state.get("box_cox_transform"):
        prep.append("Box-Cox")
    st.caption(
        f"현재 설정: {mode_txt}, n={st.session_state.get('n')}, {phase_txt}, "
        f"규격 {spec_txt}, 전처리 {', '.join(prep) if prep else '없음'}"
    )


def _render_first_look(results: list):
    """분석 후 가장 먼저 봐야 할 요약 박스."""
    if not results:
        return
    finite_cpk = [it for it in results if np.isfinite(it.get("cpk", np.nan))]
    worst = min(finite_cpk, key=lambda it: it.get("cpk", np.inf)) if finite_cpk else None
    spec_total = sum(int(it.get("spec_violation_count", 0)) for it in results)
    ctrl_total = sum(_get_ctrl_total(it) for it in results)
    rel_counts = {}
    for it in results:
        s = str(it.get("reliability_status", "정상") or "정상")
        rel_counts[s] = rel_counts.get(s, 0) + 1
    baseline_bad = [it for it in results if it.get("baseline_status") == "부적합"]

    st.markdown("#### 먼저 볼 것")
    summary_items = [
        ("신뢰도", " / ".join(f"{k} {v}" for k, v in rel_counts.items())),
        ("Cpk 최저", f"{worst.get('column')}: {fmt_value(worst.get('cpk'))}" if worst else "적용 조건 미충족/계산불가"),
        ("규격 이탈", f"{spec_total:,}건"),
        ("관리 이탈", f"{ctrl_total:,}건"),
        ("기준구간", f"부적합 {len(baseline_bad)}개" if baseline_bad else "문제 없음"),
    ]
    cards = "".join(
        '<div class="first-look-card">'
        f'<div class="first-look-label">{html.escape(str(label))}</div>'
        f'<div class="first-look-value">{html.escape(str(value))}</div>'
        '</div>'
        for label, value in summary_items
    )
    st.markdown(f'<div class="first-look-grid">{cards}</div>', unsafe_allow_html=True)
    risky = [
        it for it in results
        if it.get("reliability_status") in {"검토필요", "계산불가"}
    ]
    if risky:
        names = ", ".join(str(it.get("column")) for it in risky[:6])
        st.warning(f"신뢰도 검토가 필요한 변수: {names}" + (" ..." if len(risky) > 6 else ""))
    distribution_review = [str(it['column']) for it in results if it.get('distribution_warning')]
    if distribution_review:
        names = ', '.join(distribution_review[:6]) + (' …' if len(distribution_review) > 6 else '')
        st.warning(f'분포 검토 안내가 있는 변수: {names}. 항목별 경고와 현재 모형의 결과를 확인하세요.')


def _render_process_info_summary(meta: dict | None):
    """분석 결과에 묶인 공정 식별 정보를 화면 상단에 표시한다."""
    items = _process_info_items((meta or {}).get("process_info", {}))
    if not items:
        return
    chips = "".join(
        f'<span class="proc-chip"><b>{html.escape(label)}</b> {html.escape(value)}</span>'
        for label, value in items
    )
    st.markdown(
        f'<div class="proc-info-strip">{chips}</div>',
        unsafe_allow_html=True,
    )


SUMMARY_HIDDEN_COLUMNS = {
    "원본N",
    "분석N",
    "신뢰도 상태",
    "신뢰도 사유",
    "원본평균",
    "원본σ(전체)",
    "한계기준",
    "기준구간상태",
    "기준구간사유",
    "능력기준",
    "비고",
}


def _summary_visible_df(df):
    """선별요약 화면에서는 상세 추적용 컬럼을 숨긴다."""
    if df is None:
        return df
    df = df.copy()
    # Arrow metadata is parsed as strict JSON in the browser; analysis attrs may contain NaN.
    # Strip metadata only from the display copy, preserving the original for exports.
    df.attrs.clear()
    if df.empty:
        return df
    if "신뢰도 상태" in df.columns or "신뢰도 사유" in df.columns:
        status = df.get("신뢰도 상태", pd.Series([""] * len(df), index=df.index)).fillna("").astype(str).str.strip()
        reason = df.get("신뢰도 사유", pd.Series([""] * len(df), index=df.index)).fillna("").astype(str).str.strip()
        combined = [
            f"{s} · {r}" if s and r else (s or r)
            for s, r in zip(status, reason)
        ]
        insert_at = df.columns.get_loc("신뢰도 상태") if "신뢰도 상태" in df.columns else min(3, len(df.columns))
        if "신뢰도" in df.columns:
            df["신뢰도"] = combined
        else:
            df.insert(insert_at, "신뢰도", combined)
    if "군내/전체 지수 비율(Cwk/Ppk)" in df.columns:
        df = df.rename(columns={"군내/전체 지수 비율(Cwk/Ppk)": "군내/전체 지수 비율"})
    from spc_normality import shape_summary_display
    df = shape_summary_display(df)
    drop_cols = [c for c in SUMMARY_HIDDEN_COLUMNS if c in df.columns]
    return df.drop(columns=drop_cols) if drop_cols else df


SUMMARY_VIEWS = {
    '핵심 요약': ['변수', '평가 상태', 'N', '폭 지수', '치우침 반영 지수', '규격이탈', '관리이탈'],
    '능력 지표': ['변수', '판정', 'Cp', 'Cpk', 'Cw', 'Cwk', 'Pp', 'Ppk', 'Cpm', 'Cpmk'],
    '통계·정규성': ['변수', '평균', 'σ(전체)', 'σ(군내)', '정규성 검정법', '정규성 p값', '정규성 판정'],
    '전체 정보': None,
}


def _summary_view_df(df, view):
    """Project screen columns without altering analysis values or row positions."""
    columns = SUMMARY_VIEWS[view]
    if columns is None:
        leading = [col for col in ('변수', '평가 상태', '판정') if col in df.columns]
        columns = leading + [col for col in df.columns if col not in leading]
    return df.loc[:, [col for col in columns if col in df.columns]].copy()

import streamlit as st
from spc_policy import limits as _assessment_limits, band as _assessment_band


def _required_limit(it=None, metric='cpk'):
    return _assessment_limits(it if it is not None else {'assessment_settings': st.session_state.get('assessment_settings', {})}, metric)[1]


def _warning_limit(it=None, metric='cpk'):
    return _assessment_limits(it if it is not None else {'assessment_settings': st.session_state.get('assessment_settings', {})}, metric)[0]


def _item_band(it, metric='cpk'):
    return _assessment_band(it.get(metric), it, metric)


def _threshold_text(text):
    from spc_policy import format_criteria
    if st.session_state.get('assessment_settings', {}).get('criteria_mode') == 'aiag_vda':
        if '초보자 모드 활성화' in text:
            return '🔰 **초보자 모드** · AIAG·VDA 자동 평가: 표본 수에 따른 조정 목표값으로 비교합니다. 공정 상태 경고와 계산 조건은 별도로 확인하세요.'
        return re.sub(r'1\.00\s*[~–-]\s*1\.33', '항목별 기준 구간',
                      re.sub(r'1\.33|1\.00', '항목별 기준', text))
    return format_criteria(text, _warning_limit(), _required_limit())
import matplotlib.pyplot as plt
from scipy import stats

def _refresh_analysis_modules():
    """Recover stale imports after updating a running Streamlit app."""
    import inspect
    import spc_core
    import spc_fast

    required = {'selected_columns', 'detail_level', 'progress_callback'}
    if not required.issubset(inspect.signature(spc_core.run_full_analysis).parameters):
        importlib.invalidate_caches()
        importlib.reload(spc_core)
    # A from-import in spc_fast may still point to the previous core function.
    if spc_fast.run_full_analysis is not spc_core.run_full_analysis:
        importlib.reload(spc_fast)


_refresh_analysis_modules()

from spc_core import (
    CALCULATION_VERSION,
    WITHIN_CAPABILITY_NOTICE,
    analysis_options,
    assess_process,
    run_full_analysis,
    get_sheet_names,
    load_data_df,
    load_spec_df,
    fmt_value,
    mean_confidence_interval,
    screening_index_pair,
    screening_index_band,
)
from spc_charts import (
    build_extended_figure,
    build_interactive_control_chart,
    build_control_chart,
    build_histogram,
    build_qq_plot,
    build_stability_scatter,
    build_interactive_stability_scatter,
    stability_assessment,
    build_overview_2x2,
    build_correlation_heatmap,
    correlation_matrix,
    build_scatter_matrix,
    PALETTES,
)
from spc_fast import BoundedCache, cached_analysis, cached_frame, analysis_key, ui_analysis
from spc_mode_ui import (preserve_settings, beginner_inputs, beginner_conditions, measurement_column_picker,
                         quick_results, paged_window, show_scrollable_interactive_chart)
from spc_metric_ui import process_metric_cards_html, STATUS_CELL_STYLES
from spc_distribution_ui import render_distribution_panel
from spc_extended_ui import render_extended, with_extended_results
from spc_extended import EXTENDED_MODE, TITLE as EXTENDED_TITLE, display_panels, monitor_signals, unsupported_reason
from spc_chart_guide import chart_selection_error, chart_selection_label, render_chart_guide, render_studio_header
from spc_education import render_ocap_exercise, render_samples

from spc_export import (
    export_summary_excel, export_all_pdf, export_analysis_html, publish_analysis_html, html_bytes_to_pdf, fig_to_bytes,
)

_OUTLIER_METHOD_HELP = (
    "IQR\n"
    "Q1 = 25백분위, Q3 = 75백분위\n"
    "IQR = Q3 − Q1\n"
    "하한 = Q1 − 1.5 × IQR\n"
    "상한 = Q3 + 1.5 × IQR\n"
    "\n"
    "분위수 범위 (꼬리 0.1, Q = 3)\n"
    "P10 = 10백분위, P90 = 90백분위\n"
    "W = P90 − P10\n"
    "하한 = P10 − 3 × W\n"
    "상한 = P90 + 3 × W\n"
    "\n"
    "하한보다 작거나 상한보다 큰 값이 이상치입니다. "
    "분위수 범위는 기준 데이터가 11개 이상일 때만 적용합니다."
)

def _manual_target_map_from_editor(target_vars: list[str], edited_df=None) -> dict[str, float]:
    """Return the latest manual targets, including pending data_editor edits."""
    target_vars = [str(v).strip() for v in (target_vars or []) if str(v).strip()]
    existing_map = _normalize_manual_target_map(st.session_state.get('manual_target_map', {}))
    next_map = dict(existing_map)
    for v in target_vars:
        next_map.pop(v, None)

    rows = [{"변수": v, "목표치(T)": existing_map.get(v, np.nan)} for v in target_vars]

    if edited_df is not None:
        try:
            rows = edited_df.to_dict("records")
        except Exception:
            pass
    else:
        editor_state = st.session_state.get("manual_target_editor")
        if isinstance(editor_state, pd.DataFrame):
            rows = editor_state.to_dict("records")
        elif isinstance(editor_state, dict):
            for idx, changes in editor_state.get("edited_rows", {}).items():
                try:
                    row_idx = int(idx)
                except Exception:
                    continue
                if 0 <= row_idx < len(rows) and isinstance(changes, dict):
                    rows[row_idx].update(changes)

    for row in rows:
        var = str(row.get("변수", "")).strip()
        tv = pd.to_numeric(row.get("목표치(T)"), errors='coerce')
        if var and np.isfinite(tv):
            next_map[var] = float(tv)
    return next_map
from spc_email import smtp_send, send_alert_if_needed, split_emails
try:
    from spc_ai import build_var_context, generate_ai_interpretation, chat_with_spc_data
except Exception:
    # 일부 실행 환경에서 현재 작업 폴더가 달라질 수 있어 파일 경로로 재로딩
    try:
        _SPC_AI_PATH = os.path.join(_APP_DIR, "spc_ai.py")
        _spc_ai_spec = importlib.util.spec_from_file_location("spc_ai", _SPC_AI_PATH)
        if _spc_ai_spec is None or _spc_ai_spec.loader is None:
            raise ModuleNotFoundError("spc_ai spec load failed")
        _spc_ai_mod = importlib.util.module_from_spec(_spc_ai_spec)
        _spc_ai_spec.loader.exec_module(_spc_ai_mod)
        build_var_context = _spc_ai_mod.build_var_context
        generate_ai_interpretation = _spc_ai_mod.generate_ai_interpretation
        chat_with_spc_data = _spc_ai_mod.chat_with_spc_data
    except Exception:
        # 최종 안전장치: AI 모듈이 없어도 앱 본 기능은 동작하도록 유지
        def build_var_context(it, beginner_mode=False):
            return {"var_name": it.get("var_name", "-"), "beginner_mode": bool(beginner_mode)}

        def generate_ai_interpretation(*args, **kwargs):
            return {"summary": "AI 해석 모듈 로드 실패", "mode": "fallback"}

        def chat_with_spc_data(*args, **kwargs):
            return "AI 챗봇 모듈을 불러오지 못했습니다."
import datetime

# ─────────────────────────── 페이지 설정 ───────────────────────────
st.set_page_config(
    page_title="AIAG - VDA SPC Studio",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.caption(f"SPC Studio · 계산 버전 {CALCULATION_VERSION}")
render_access_gate()

# ─────────────────────────── CSS 커스텀 ───────────────────────────
st.markdown("""
<style>
/* 전체 폰트 */
html, body, [class*="css"] { font-family: "Malgun Gothic", "Nanum Gothic", sans-serif; }
[data-testid="stCaptionContainer"] { color:var(--text-color,#475569); opacity:1; font-size:0.85rem; line-height:1.5; }

/* 헤더 */
.main-header {
    background: linear-gradient(135deg, #1E3A8A, #0E7490);
    color: white;
    padding: 1rem 1.5rem;
    border-radius: 0.6rem;
    margin-bottom: 1.2rem;
}
.main-header h1 { margin: 0; font-size: 1.6rem; }

/* 메트릭 카드 */
.metric-card {
    background: white;
    color: #0f172a;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 0.7rem 1rem;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
}
.metric-label { font-size: 0.85rem; color: #475569; margin-bottom: 0.2rem; }
.metric-value { font-size: 1.15rem; font-weight: 700; }
.first-look-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 1rem;
    margin: 0.35rem 0 0.85rem;
}
.first-look-card {
    min-width: 0;
    min-height: 76px;
    padding: 0.45rem 0.15rem 0.55rem;
}
.first-look-label {
    color: #1e3a8a;
    font-size: 0.86rem;
    line-height: 1.2;
    margin-bottom: 0.22rem;
    overflow-wrap: anywhere;
}
.first-look-value {
    color: #0f172a;
    font-size: clamp(1.45rem, 1.75vw, 2.0rem);
    line-height: 1.18;
    font-weight: 500;
    overflow-wrap: anywhere;
    word-break: keep-all;
}
@media (max-width: 1100px) {
    .first-look-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
}
@media (max-width: 640px) {
    .first-look-grid {
        grid-template-columns: 1fr;
    }
}
.proc-info-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin: 0.25rem 0 0.9rem;
}
.proc-chip {
    display: inline-flex;
    gap: 0.35rem;
    align-items: baseline;
    max-width: 100%;
    padding: 0.28rem 0.55rem;
    border: 1px solid #dbe3ef;
    border-radius: 0.45rem;
    background: #f8fafc;
    color: #0f172a;
    font-size: 0.84rem;
    line-height: 1.25;
    overflow-wrap: anywhere;
}
.proc-chip b {
    color: #475569;
    white-space: nowrap;
}
/* Streamlit 기본 metric 가독성/반응형 개선 */
[data-testid="stMetricLabel"] > div {
    white-space: normal !important;
    overflow-wrap: anywhere !important;
    line-height: 1.15 !important;
}
[data-testid="stMetricLabel"] {
    min-height: 2.2em;
}
[data-testid="stMetricValue"] > div {
    font-size: clamp(1.40rem, 1.55vw, 2.05rem) !important;
    line-height: 1.08 !important;
}
@media (max-width: 1280px) {
    [data-testid="stMetricValue"] > div {
        font-size: clamp(1.25rem, 1.45vw, 1.70rem) !important;
    }
}
.good  { color: #0f766e; }
.warn  { color: #b45309; }
.bad   { color: #b42318; }
.muted { color: #475569; }
/* 선별 요약 테이블 강조 */
.stDataFrame td { font-size: 0.83rem; }

/* 상세 통계(통계형 디자인) */
.stats-kpi {
    border: 1px solid #dbe5ef;
    border-radius: 0.55rem;
    padding: 0.55rem 0.65rem;
    background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    min-height: 98px;
    transition: all 0.16s ease;
}
.stats-kpi-label {
    font-size: 0.84rem;
    color: #475569;
    margin-bottom: 0.16rem;
}
.stats-kpi-value {
    font-size: 1.25rem;
    font-weight: 700;
    color: #0f172a;
    font-family: "Consolas", "Roboto Mono", monospace;
    font-variant-numeric: tabular-nums;
    line-height: 1.15;
}
.stats-kpi-value.good { color: #065f46; }
.stats-kpi-value.warn { color: #92400e; }
.stats-kpi-value.bad { color: #991b1b; }
.stats-kpi-value.muted { color: #334155; }
.stats-kpi-sub {
    font-size: 0.81rem;
    color: #475569;
    line-height: 1.5;
    margin-top: 0.14rem;
}
.stats-kpi-good {
    border-color: rgba(16, 185, 129, 0.45);
    background: linear-gradient(180deg, rgba(16,185,129,0.13) 0%, rgba(240,253,250,0.96) 100%);
}
.stats-kpi-warn {
    border-color: rgba(245, 158, 11, 0.50);
    background: linear-gradient(180deg, rgba(245,158,11,0.14) 0%, rgba(255,251,235,0.96) 100%);
}
.stats-kpi-bad {
    border-color: rgba(220, 38, 38, 0.52);
    background: linear-gradient(180deg, rgba(220,38,38,0.15) 0%, rgba(254,242,242,0.96) 100%);
}
.stats-kpi-muted {
    border-color: rgba(100, 116, 139, 0.34);
    background: linear-gradient(180deg, rgba(100,116,139,0.12) 0%, rgba(248,250,252,0.96) 100%);
}
.stats-block {
    color: #0f172a;
    border: 1px solid #dbe5ef;
    border-radius: 0.55rem;
    padding: 0.55rem 0.7rem;
    background: #f8fafc;
    min-height: 210px;
}
.stats-block-compact {
    min-height: 126px;
}
.stats-block-compact .stats-title {
    font-size: 0.80rem;
    margin-bottom: 0.26rem;
}
.stats-block-compact .stats-kv {
    font-size: 0.79rem;
}
.stats-block-compact .stats-kv td {
    padding: 0.14rem 0;
}
.stats-block-compact .stats-kv td:first-child {
    width: 56%;
    line-height: 1.2;
}
.stats-title {
    font-size: 0.84rem;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 0.3rem;
}
.stats-kv {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.81rem;
}
.stats-kv td {
    padding: 0.18rem 0;
    border-bottom: 1px dashed #e2e8f0;
}
.stats-kv td:first-child {
    color: #475569;
    width: 46%;
}
.stats-kv td:last-child {
    text-align: right;
    color: #0f172a;
    font-family: "Consolas", "Roboto Mono", monospace;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.stats-limit-reference {
    margin: 0.35rem 0 0.55rem;
    color: #475569;
    font-size: 0.78rem;
    word-break: keep-all;
}
.stats-limits .stats-kv {
    table-layout: fixed;
}
.stats-limits .stats-kv td:first-child {
    white-space: nowrap;
}
.stats-val {
    display: inline-block;
    min-width: 70px;
    text-align: right;
    border-radius: 0.34rem;
    padding: 0.02rem 0.30rem;
    font-weight: 700;
}
.stats-val-good {
    background: rgba(16, 185, 129, 0.14);
    color: #065f46;
}
.stats-val-warn {
    background: rgba(245, 158, 11, 0.16);
    color: #92400e;
}
.stats-val-bad {
    background: rgba(220, 38, 38, 0.15);
    color: #991b1b;
}
.stats-val-muted {
    background: rgba(100, 116, 139, 0.12);
    color: #334155;
}
.stats-note {
    border-radius: 0.5rem;
    padding: 0.5rem 0.65rem;
    border: 1px solid transparent;
    font-size: 0.82rem;
    margin-top: 0.5rem;
}
.stats-note.good {
    background: rgba(16, 185, 129, 0.12);
    border-color: rgba(16, 185, 129, 0.42);
    color: #065f46;
}
.stats-note.warn {
    background: rgba(245, 158, 11, 0.14);
    border-color: rgba(245, 158, 11, 0.44);
    color: #92400e;
}
.stats-note.bad {
    background: rgba(220, 38, 38, 0.13);
    border-color: rgba(220, 38, 38, 0.42);
    color: #991b1b;
}

/* 사이드바 */
section[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
    padding-top: 0.75rem;
    padding-bottom: 1rem;
}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.65rem;
}
/* Tighten section separators while keeping controls inside open panels spaced. */
section[data-testid="stSidebar"] hr {
    margin: 0.4rem 0;
}
section[data-testid="stSidebar"] [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"] {
    gap: 1rem;
}

/* 비밀번호 표시/숨김 토글 버튼 제거 */
button[data-testid="stTextInputVisibilityToggle"],
button[data-testid="textInputPasswordToggle"],
div[data-testid="stTextInput"]:has(input[type="password"]) button { display: none !important; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────── 세션 상태 초기화 ───────────────────────────
def _new_chat_usage() -> dict:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "last_input_tokens": 0,
        "last_output_tokens": 0,
        "last_total_tokens": 0,
        "model_name": "",
        "model_input_limit": 0,   # per-request context window (if available)
        "model_output_limit": 0,  # per-request context window (if available)
        "token_source": "",
        "last_error": "",
    }


def _init_state():
    saved = _load_config()
    em = saved.get("email", {})
    saved_process_info = _normalize_process_info(saved.get("process_info", {}))
    startup_chart_mode = "phase2" if saved.get("chart_usage_mode") == "phase2" else "phase1"
    if (st.session_state.get('chart_usage_mode') == EXTENDED_MODE
            and '_standard_chart_usage_mode' not in st.session_state
            and st.session_state.get('selected_control_chart') != EXTENDED_MODE
            and not st.session_state.get('analysis_done')):
        st.session_state['chart_usage_mode'] = startup_chart_mode
    defaults = {
        'results': [],
        'summary_df': None,
        'analysis_done': False,
        'data_bytes': None,
        'data_sheet': None,
        'spec_bytes': None,
        'spec_sheet': None,
        'active_data_path': '',
        'active_spec_path': '',
        'data_file_name': '',
        'spec_file_name': '',
        'n': saved.get('n', 1),
        'include_incomplete': saved.get('include_incomplete', False),
        'data_layout_mode': saved.get('data_layout_mode', 'long'),
        'selected_columns': saved.get('selected_columns'),
        'subgroup_columns': saved.get('subgroup_columns', []),
        # A new browser session starts with the normal model. Presets and the
        # distribution panel can still apply a model explicitly in this session.
        'distribution_settings': {},
        'assessment_settings': saved.get('assessment_settings', {}),
        'subgroup_var_name': saved.get('subgroup_var_name', '측정치'),
        'measurement_date_column': saved.get('measurement_date_column', ''),
        'chart_usage_mode': startup_chart_mode,
        '_standard_chart_usage_mode': startup_chart_mode,
        'baseline_subgroups': saved.get('baseline_subgroups', 25),
        'palette': saved.get('palette', 'trust'),
        'hist_bins': saved.get('hist_bins', 30),
        'ksq_toggles': saved.get('ksq_toggles', {f'T{i}': True for i in range(1, 9)}),
        'show_zones': saved.get('show_zones', {'C': True, 'B': True, 'A': True, 'Z3': True}),
        # 이메일 설정 (비밀번호는 저장하지 않음: 실행 중 입력 또는 환경변수 사용)
        'sb_smtp_host': em.get('smtp_host', 'smtp.gmail.com'),
        'sb_smtp_port': em.get('smtp_port', 465),
        'sb_smtp_user': em.get('smtp_user', ''),
        'sb_smtp_pass': os.environ.get('SPC_SMTP_PASS', ''),
        'sb_from_addr': em.get('from_addr', ''),
        'sb_to_addrs': em.get('to_addrs', ''),
        'sb_use_ssl': em.get('use_ssl', True),
        'sb_starttls': em.get('use_starttls', False),
        'sb_only_recent': em.get('only_recent', True),
        # 즐겨찾기
        'favorites': saved.get('favorites', []),
        # 마지막 분석 환경
        'last_analysis_env': _normalize_last_analysis_env(saved.get('last_analysis_env', {})),
        # 수동 목표치(T) 오버라이드
        'manual_target_enabled': bool(saved.get('manual_target_enabled', False)),
        'manual_target_map': _normalize_manual_target_map(saved.get('manual_target_map', {})),
        # 자동 분석 감시
        'auto_watch_enabled': saved.get('auto_watch_enabled', False),
        'auto_watch_fav_idx': saved.get('auto_watch_fav_idx', 0),
        '_watch_mtime': 0.0,
        '_watch_path': '',
        '_watch_last_checked_at': '',
        '_watch_last_change_at': '',
        '_watch_last_status': '',
        # 분석 이력 (최근 5회)
        'analysis_history': [],
        # 이메일 발송 이력 (최근 10건)
        'email_history': saved.get('email_history', []),
        # 파라미터 프리셋
        'param_presets': saved.get('param_presets', []),
        # UI 상태
        'summary_filter': 'all',
        'detail_var_idx': 0,
        'summary_search': '',
        # 초보자 모드
        'beginner_mode': saved.get('beginner_mode', True),
        # 공정 식별 정보
        'process_info': saved_process_info,
        # AI API 키: 저장된 값 우선, 환경변수로도 대체 가능
        'gemini_api_key': ('' if education_mode() else (saved.get('gemini_api_key', '') or os.environ.get('GEMINI_API_KEY', ''))),
        'groq_api_key': ('' if education_mode() else (saved.get('groq_api_key', '') or os.environ.get('GROQ_API_KEY', ''))),
        'openrouter_api_key': ('' if education_mode() else (saved.get('openrouter_api_key', '') or os.environ.get('OPENROUTER_API_KEY', ''))),
        # 데이터 전처리 및 변환 옵션
        'preprocess_outliers': saved.get('preprocess_outliers', False),
        'outlier_method': saved.get('outlier_method', 'iqr') if saved.get('outlier_method') in ('iqr', 'quantile') else 'iqr',
        'box_cox_transform': False,
        # AI 결과 캐시(세션 전용)
        'ai_var_cache': {},
        'chat_history': [],
        'chat_usage': _new_chat_usage(),
        # 마지막 분석 환경 자동복원(세션 1회)
        '_last_env_restore_done': False,
        '_last_env_auto_trigger_done': False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if not st.session_state.get('_normal_start_initialized'):
        # Refresh sessions that were already open when this startup behavior changed.
        had_nondefault_model = bool(st.session_state['distribution_settings'] or st.session_state['box_cox_transform'])
        st.session_state['distribution_settings'] = {}
        st.session_state['box_cox_transform'] = False
        if had_nondefault_model and st.session_state.get('data_bytes') and st.session_state.get('analysis_done'):
            st.session_state['_refresh_normal_start'] = True
        st.session_state['_normal_start_initialized'] = True
    for key, label in PROCESS_INFO_FIELDS:
        state_key = f"process_info_{key}"
        if state_key not in st.session_state:
            st.session_state[state_key] = saved_process_info.get(label, "")

_init_state()

preserve_settings()
if st.session_state.get("_spc_analysis_kind") == "attribute":
    from spc_attribute_ui import render_attribute_studio

    render_attribute_studio()
    st.stop()

if '_bounded_cache' not in st.session_state:
    st.session_state['_bounded_cache'] = BoundedCache()

def load_data_df(path_or_bytes, sheet_name=None):
    return cached_frame(st.session_state['_bounded_cache'], path_or_bytes, sheet_name)

_inspect_uncached = _inspect_current_inputs
def _inspect_current_inputs():
    key = analysis_key(dict(preflight_version=2, data_bytes=st.session_state.get('data_bytes'),
        spec_bytes=st.session_state.get('spec_bytes'),
        data_sheet=st.session_state.get('data_sheet'), spec_sheet=st.session_state.get('spec_sheet'),
        layout=st.session_state.get('data_layout_mode'), columns=st.session_state.get('subgroup_columns')))
    previous = st.session_state.get('_preflight_cached')
    if previous and previous[0] == key:
        return previous[1]
    result = _inspect_uncached()
    st.session_state['_preflight_cached'] = (key, result)
    return result

_restore_last_analysis_env()
_last_env_notice = st.session_state.pop("_last_env_notice", "")
_last_env_notice_icon = st.session_state.pop("_last_env_notice_icon", "♻️")
if _last_env_notice:
    st.toast(_last_env_notice, icon=_last_env_notice_icon)

# ─────────────────────────── 파일 감시 (자동 분석) ───────────────────────────
@st.fragment(run_every=10)
def _file_watcher():
    if education_mode():
        return
    if not st.session_state.get('auto_watch_enabled'):
        return
    favs = st.session_state.get('favorites', [])
    idx  = st.session_state.get('auto_watch_fav_idx', 0)
    if not favs or idx >= len(favs):
        st.session_state['_watch_last_status'] = '감시할 즐겨찾기 없음'
        return
    fav = favs[idx]
    path = fav.get('data_path', '')
    if not path or not os.path.isfile(path):
        st.session_state['_watch_last_status'] = '감시 파일 없음'
        return
    mtime = os.path.getmtime(path)
    norm_path = _norm_path(path)
    prev_path = st.session_state.get('_watch_path', '')
    if prev_path and prev_path != norm_path:
        st.session_state['_watch_mtime'] = 0.0
    st.session_state['_watch_path'] = norm_path
    st.session_state['_watch_last_checked_at'] = datetime.datetime.now().strftime("%H:%M:%S")
    prev_mtime = st.session_state.get('_watch_mtime', 0.0)

    if prev_mtime == 0.0:
        # 최초 실행: 경로/파일만 기억, 분석은 하지 않음
        st.session_state['_watch_mtime'] = mtime
        with open(path, 'rb') as f:
            st.session_state['data_bytes'] = f.read()
        st.session_state['data_sheet'] = fav.get('data_sheet') or None
        st.session_state['active_data_path'] = _norm_path(path)
        st.session_state['data_file_name'] = os.path.basename(path)
        sp = fav.get('spec_path', '')
        if sp and os.path.isfile(sp):
            with open(sp, 'rb') as f:
                st.session_state['spec_bytes'] = f.read()
            st.session_state['spec_sheet'] = fav.get('spec_sheet') or None
            st.session_state['active_spec_path'] = _norm_path(sp)
            st.session_state['spec_file_name'] = os.path.basename(sp)
        else:
            st.session_state['spec_bytes'] = None
            st.session_state['spec_sheet'] = None
            st.session_state['active_spec_path'] = ''
            st.session_state['spec_file_name'] = ''
        st.session_state['_loaded_fav_name'] = fav.get('name', '')
        st.session_state.pop('_input_identity', None)
        st.session_state['_watch_last_status'] = '감시 기준 저장'
        _save_config()
        return

    if mtime != prev_mtime:
        # 파일 변경 감지: 분석 트리거
        st.session_state['_watch_mtime'] = mtime
        st.session_state['_watch_last_change_at'] = datetime.datetime.now().strftime("%H:%M:%S")
        with open(path, 'rb') as f:
            st.session_state['data_bytes'] = f.read()
        st.session_state['data_sheet'] = fav.get('data_sheet') or None
        st.session_state['active_data_path'] = _norm_path(path)
        st.session_state['data_file_name'] = os.path.basename(path)
        sp = fav.get('spec_path', '')
        if sp and os.path.isfile(sp):
            with open(sp, 'rb') as f:
                st.session_state['spec_bytes'] = f.read()
            st.session_state['spec_sheet'] = fav.get('spec_sheet') or None
            st.session_state['active_spec_path'] = _norm_path(sp)
            st.session_state['spec_file_name'] = os.path.basename(sp)
        else:
            st.session_state['spec_bytes'] = None
            st.session_state['spec_sheet'] = None
            st.session_state['active_spec_path'] = ''
            st.session_state['spec_file_name'] = ''
        st.session_state['_loaded_fav_name'] = fav.get('name', '')
        st.session_state.pop('_input_identity', None)
        st.session_state['_watch_file_changed'] = True
        st.session_state['_watch_last_status'] = '변경 감지'
        _save_config()
        st.rerun()
    else:
        st.session_state['_watch_last_status'] = '변경 없음'

_file_watcher()

# ─────────────────────────── 모든 즐겨찾기 일괄 분석 ───────────────────────────
def _run_all_favorites() -> list:
    """모든 즐겨찾기를 분석해 [(fav_name, results)] 반환."""
    favs = st.session_state.get('favorites', [])
    all_results = []
    failed = []
    for fav in favs:
        path = fav.get('data_path', '')
        if not path or not os.path.isfile(path):
            failed.append(f"{fav.get('name', '(이름없음)')}: 데이터 파일 없음")
            continue
        try:
            with open(path, 'rb') as f:
                data_bytes = io.BytesIO(f.read())
            sp = fav.get('spec_path', '')
            spec_bytes = None
            if sp and not os.path.isfile(sp):
                raise ValueError("등록한 규격 파일을 찾을 수 없습니다.")
            if sp and os.path.isfile(sp):
                with open(sp, 'rb') as f:
                    spec_bytes = io.BytesIO(f.read())
            results, fav_summary = run_full_analysis(
                data_bytes=data_bytes,
                data_sheet=fav.get('data_sheet') or None,
                spec_bytes=spec_bytes,
                spec_sheet=fav.get('spec_sheet') or None,
                **analysis_options(st.session_state),
            )
            if '오류' in fav_summary.columns:
                failed.extend(f"{fav['name']}: {row['변수']} — {row['오류']}"
                              for _, row in fav_summary[fav_summary['오류'].notna()].iterrows())
            if results:
                all_results.append((fav['name'], results))
        except Exception as e:
            failed.append(f"{fav.get('name', '(이름없음)')}: {e.__class__.__name__}")
            continue
    st.session_state['_fav_batch_errors'] = failed
    return all_results


def _requires_capability_review(item):
    return bool(item.get('distribution_warning') or
                (item.get('capability_status') and item['capability_status'] != '적용 가능'))


def _build_all_favs_email(all_results: list) -> tuple[str, str]:
    """모든 즐겨찾기 분석 결과로 통합 이메일 본문 생성."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append("=" * 60)
    lines.append("  SPC 전체 즐겨찾기 이상 알림 보고서")
    lines.append(f"  분석 일시: {now}")
    lines.append("=" * 60)

    total_bad, total_warn, total_spec, total_ctrl, total_review = 0, 0, 0, 0, 0
    detail_lines = []

    for fav_name, results in all_results:
        bad  = [it for it in results if not _requires_capability_review(it) and _item_band(it) == 'bad']
        warn = [it for it in results if not _requires_capability_review(it) and _item_band(it) == 'warn']
        svio = sum(it.get('spec_violation_count', 0) for it in results)
        cvio = sum(_get_ctrl_total(it) for it in results)
        total_bad  += len(bad)
        total_warn += len(warn)
        total_spec += svio
        total_ctrl += cvio
        total_review += sum(_requires_capability_review(it) for it in results)

        needs_review = [it for it in results if _requires_capability_review(it) or assess_process(it)['level'] != 'low']
        if not needs_review:
            detail_lines.append(f"\n▷ [{fav_name}] — 이상 없음 ✓")
            continue

        detail_lines.append(f"\n▷ [{fav_name}]")
        detail_lines.append(f"  변수 {len(results)}개 | 불량 {len(bad)}개 | 주의 {len(warn)}개 | "
                            f"규격이탈 {svio}건 | 관리이탈 {cvio}건")

        problem_items = needs_review
        if problem_items:
            detail_lines.append(f"  {'변수':<18} {'Cpk':>7} {'Ppk':>7} {'PPM(내)':>12} {'규격이탈':>8}")
            detail_lines.append("  " + "-" * 58)
            for it in problem_items:
                ppm_w = f"{it.get('ppm_within', np.nan):,.0f}" if np.isfinite(it.get('ppm_within', np.nan)) else "—"
                sv = it.get('spec_violation_count', 0)
                detail_lines.append(f"  {it['column']:<18} {fmt_value(it['cpk']):>7} "
                                    f"{fmt_value(it['ppk']):>7} {ppm_w:>12} {sv:>8}")
                detail_lines.append(f"    판정: {assess_process(it)['text']}")
                detail_lines.append("    Cp·Cpk: " + it.get('capability_status', '재분석 필요') + " · " + "; ".join(it.get('capability_reasons', [])))
                if it.get('distribution_warning'):
                    detail_lines.append('    분포 검토: ' + it['distribution_warning'])
                detail_lines.append("    Pp·Ppk/예상 PPM은 선택 분포의 모형 참고값입니다.")
                detail_lines.append(f"    계산법: {it.get('capability_method', 'normal')} / 분포: {(it.get('distribution_model') or {}).get('name', '정규')}")

    # 전체 요약
    lines.append("")
    lines.append("▶ 전체 요약")
    lines.append(f"  - 분석 대상 즐겨찾기 : {len(all_results)}개")
    lines.append(_threshold_text(f"  - Cpk < 1.00 (불량)  : {total_bad}개"))
    lines.append(_threshold_text(f"  - Cpk < 1.33 (주의)  : {total_warn}개"))
    lines.append(f"  - 규격 이탈 (누적)   : {total_spec}건")
    lines.append(f"  - 관리 이탈 (누적)   : {total_ctrl}건")
    lines.append(f"  - Cp·Cpk 검토 대상  : {total_review}개 (기준 충족/미달 집계 제외)")
    lines.extend(detail_lines)
    lines.append("")
    lines.append("=" * 60)
    lines.append("※ 본 메일은 SPC 웹앱 자동 발송입니다.")
    lines.append("=" * 60)

    subject = f"[SPC 전체 알림] 불량 {total_bad}건 / 주의 {total_warn}건 / 능력 검토 {total_review}개 — {now}"
    return subject, "\n".join(lines)


# ─────────────────────────── 이메일 상세 본문 생성 ───────────────────────────
def _build_detail_email(results: list, only_recent: bool, source_name: str = "") -> tuple[str, str]:
    """상세 이메일 제목과 본문 반환. 이탈 없으면 (None, None)."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    bad, warn, spec_vio, ctrl_vio = [], [], [], []
    for it in results:
        col = it['column']
        cpk = it.get('cpk', float('nan'))
        ppk = it.get('ppk', float('nan'))
        sv  = it.get('recent_spec_violation', 0) if only_recent else it.get('spec_violation_count', 0)
        cv  = (it.get('recent_ctrl_violation', 0) if only_recent
               else _get_ctrl_total(it))
        band = 'na' if _requires_capability_review(it) else _item_band(it)
        if band == 'bad':
            bad.append(it)
        elif band == 'warn':
            warn.append(it)
        if sv > 0:
            spec_vio.append((col, sv))
        if cv > 0:
            ctrl_vio.append((col, cv))

    rule_items = [it for it in results if (
        any(len(it['means']) - 1 in indices for indices in it.get('ksq_idx', {}).values())
        if only_recent else any(it.get('ksq_counts', {}).values()))]
    review_items = [it for it in results if _requires_capability_review(it)]
    if not bad and not warn and not spec_vio and not ctrl_vio and not rule_items and not review_items:
        return None, None

    lines = []
    lines.append("=" * 60)
    lines.append("  SPC 공정능력 이상 알림 보고서")
    lines.append(f"  분석 일시: {now}")
    if source_name:
        lines.append(f"  데이터 출처: {source_name}")
    lines.append("=" * 60)

    # 요약
    lines.append("")
    lines.append("▶ 요약")
    for item in results:
        lines.append(f"  {item['column']}: {assess_process(item)['text']}")
        if item.get('capability_status') != '적용 가능':
            lines.append("    Cp·Cpk: " + item.get('capability_status', '재분석 필요') + " — " + "; ".join(item.get('capability_reasons', [])))
        if item.get('distribution_warning'):
            lines.append('    분포 검토: ' + item['distribution_warning'])
    lines.append("  Pp·Ppk/예상 PPM은 선택 분포 모형의 참고값이며, 정규식 Cw·Cwk는 군내 진단용입니다.")
    for item in results:
        if item.get('distribution_mode') == 'nonnormal':
            lines.append(f"  {item['column']}: 분포 {(item.get('distribution_model') or {}).get('name', '미확인')} / 방법 {item.get('capability_method')} / Pearson T1만 적용")
    if rule_items:
        lines.append("  KSQ 규칙 확인: " + ", ".join(str(item['column']) for item in rule_items))
    lines.append(f"  - 분석 변수 수       : {len(results)}개")
    lines.append(_threshold_text(f"  - Cpk < 1.00 (불량)  : {len(bad)}개"))
    lines.append(_threshold_text(f"  - Cpk < 1.33 (주의)  : {len(warn)}개"))
    lines.append(f"  - 규격 이탈 변수     : {len(spec_vio)}개")
    lines.append(f"  - 관리한계 이탈 변수 : {len(ctrl_vio)}개")
    lines.append(f"  - Cp·Cpk 검토 대상  : {len(review_items)}개 (기준 충족/미달 집계 제외)")

    # 불량 변수 상세
    if bad:
        lines.append("")
        lines.append(_threshold_text("▶ [불량] Cpk < 1.00 — 즉시 조치 필요"))
        lines.append(f"  {'변수':<18} {'Cpk':>7} {'Ppk':>7} {'PPM(내)':>12} {'PPM(외)':>12} {'규격이탈':>8} {'관리이탈':>8}")
        lines.append("  " + "-" * 78)
        for it in bad:
            ppm_w = f"{it.get('ppm_within', np.nan):,.0f}" if np.isfinite(it.get('ppm_within', float('nan'))) else "—"
            ppm_o = f"{it['ppm_ppk']:,.0f}" if np.isfinite(it.get('ppm_ppk', float('nan'))) else "—"
            sv = it.get('spec_violation_count', 0)
            cv = _get_ctrl_total(it)
            lines.append(f"  {it['column']:<18} {fmt_value(it['cpk']):>7} {fmt_value(it['ppk']):>7} "
                         f"{ppm_w:>12} {ppm_o:>12} {sv:>8} {cv:>8}")

    # 주의 변수 상세
    if warn:
        lines.append("")
        lines.append(_threshold_text("▶ [주의] Cpk 1.00~1.33 — 모니터링 강화 필요"))
        lines.append(f"  {'변수':<18} {'Cpk':>7} {'Ppk':>7} {'PPM(내)':>12} {'PPM(외)':>12} {'규격이탈':>8} {'관리이탈':>8}")
        lines.append("  " + "-" * 78)
        for it in warn:
            ppm_w = f"{it.get('ppm_within', np.nan):,.0f}" if np.isfinite(it.get('ppm_within', float('nan'))) else "—"
            ppm_o = f"{it['ppm_ppk']:,.0f}" if np.isfinite(it.get('ppm_ppk', float('nan'))) else "—"
            sv = it.get('spec_violation_count', 0)
            cv = _get_ctrl_total(it)
            lines.append(f"  {it['column']:<18} {fmt_value(it['cpk']):>7} {fmt_value(it['ppk']):>7} "
                         f"{ppm_w:>12} {ppm_o:>12} {sv:>8} {cv:>8}")

    # 규격 이탈
    if spec_vio:
        lines.append("")
        lines.append("▶ 규격 이탈 변수")
        for col, cnt in spec_vio:
            lines.append(f"  - {col} : {cnt}건")

    # 관리한계 이탈
    if ctrl_vio:
        lines.append("")
        lines.append("▶ 관리한계 이탈 변수")
        for col, cnt in ctrl_vio:
            lines.append(f"  - {col} : {cnt}건")

    lines.append("")
    lines.append("=" * 60)
    lines.append("※ 본 메일은 SPC 웹앱 자동 발송입니다.")
    lines.append("=" * 60)

    n_bad, n_warn = len(bad), len(warn)
    src = f" [{source_name}]" if source_name else ""
    subject = f"[SPC 이상 알림]{src} 불량 {n_bad}건 / 주의 {n_warn}건 / 능력 검토 {len(review_items)}개 — {now}"
    return subject, "\n".join(lines)


# ─────────────────────────── 이력 헬퍼 ───────────────────────────
def _push_analysis_history(results: list, source: str = ""):
    entry = {
        'calculation_version': CALCULATION_VERSION,
        'model_signature': json.dumps({str(it['column']): {
            'mode': it.get('distribution_mode', 'normal'), 'method': it.get('capability_method', 'normal'),
            'family': (it.get('distribution_settings') or {}).get('family'),
            'lower': (it.get('distribution_settings') or {}).get('natural_lower')}
            for it in results}, sort_keys=True),
        'time': datetime.datetime.now().strftime("%m-%d %H:%M"),
        'source': source or st.session_state.get('_loaded_fav_name', ''),
        'n_vars': len(results),
        'n_bad':  sum(1 for it in results if _item_band(it) == 'bad'),
        'n_warn': sum(1 for it in results if _item_band(it) == 'warn'),
        'n_spec': sum(it.get('spec_violation_count', 0) for it in results),
        'cpk_list': {it['column']: it.get('cpk', float('nan')) for it in results},
    }
    hist = st.session_state.get('analysis_history', [])
    hist.append(entry)
    st.session_state['analysis_history'] = hist[-5:]


def _get_ctrl_counts(it: dict, scope: str = "display") -> tuple[int, int]:
    """관리이탈 건수를 운영 모드에 맞게 반환한다."""
    actual_scope = scope
    if scope == "display":
        actual_scope = "monitor" if it.get('control_phase') == 'phase2' else "total"

    if actual_scope == "baseline":
        return (
            int(it.get('x_ctrl_violation_count_baseline', it.get('x_ctrl_violation_count', 0))),
            int(it.get('r_ctrl_violation_count_baseline', it.get('r_ctrl_violation_count', 0))),
        )
    if actual_scope == "monitor":
        return (
            int(it.get('x_ctrl_violation_count_monitor', it.get('x_ctrl_violation_count', 0))),
            int(it.get('r_ctrl_violation_count_monitor', it.get('r_ctrl_violation_count', 0))),
        )
    return (
        int(it.get('x_ctrl_violation_count', 0)),
        int(it.get('r_ctrl_violation_count', 0)),
    )


def _get_ctrl_total(it: dict, scope: str = "display") -> int:
    x_cnt, r_cnt = _get_ctrl_counts(it, scope=scope)
    return int(x_cnt + r_cnt)


def _auto_select_corr_columns(results: list, num_cols: list, limit: int = 10) -> tuple[list, list]:
    """공정상태가 좋지 않은 항목을 상관분석 기본 선택값으로 추천한다."""
    if not num_cols:
        return [], []

    col_by_name = {str(c): c for c in num_cols}
    candidates = []
    for order, it in enumerate(results or []):
        col_name = str(it.get("column", ""))
        if col_name not in col_by_name:
            continue
        cpk = it.get("cpk", np.nan)
        cpk_val = float(cpk) if np.isfinite(cpk) else np.inf
        spec_cnt = int(it.get("spec_violation_count", 0) or 0)
        ctrl_cnt = _get_ctrl_total(it)
        is_risky = (
            (_item_band(it) in ('bad', 'warn'))
            or spec_cnt > 0
            or ctrl_cnt > 0
        )
        candidates.append({
            "col": col_by_name[col_name],
            "name": col_name,
            "cpk": cpk_val,
            "spec": spec_cnt,
            "ctrl": ctrl_cnt,
            "risky": is_risky, "band": _item_band(it),
            "order": order,
        })

    risky = [x for x in candidates if x["risky"]]
    risky.sort(key=lambda x: (
        0 if x["band"] == "bad" else 1,
        x["cpk"],
        -(x["spec"] + x["ctrl"]),
        x["order"],
    ))

    selected = []
    selected_names = set()
    for x in risky:
        if x["name"] not in selected_names:
            selected.append(x["col"])
            selected_names.add(x["name"])
        if len(selected) >= limit:
            return selected, [str(c) for c in selected]

    if len(selected) < 2:
        fallback = sorted(candidates, key=lambda x: (x["cpk"], x["order"]))
        for x in fallback:
            if x["name"] in selected_names:
                continue
            selected.append(x["col"])
            selected_names.add(x["name"])
            if len(selected) >= min(limit, 5) or len(selected) >= 2:
                break

    if len(selected) < 2:
        for col in num_cols:
            if str(col) in selected_names:
                continue
            selected.append(col)
            selected_names.add(str(col))
            if len(selected) >= min(limit, 5) or len(selected) >= 2:
                break

    return selected[:limit], [str(c) for c in selected[:limit]]


def _push_email_history(subject: str, to_list: list, ok: bool):
    entry = {
        'time': datetime.datetime.now().strftime("%m-%d %H:%M"),
        'subject': subject[:60],
        'to': ", ".join(to_list[:2]) + ("..." if len(to_list) > 2 else ""),
        'ok': ok,
    }
    hist = st.session_state.get('email_history', [])
    hist.append(entry)
    st.session_state['email_history'] = hist[-10:]
    _save_config()


# ─────────────────────────── 헬퍼 ───────────────────────────
def _cpk_color(val_str: str) -> str:
    try:
        v = float(val_str)
        if v < _warning_limit():   return "bad"
        if v < _required_limit():   return "warn"
        return "good"
    except Exception:
        return "muted"


def _judgment_text(cpk) -> str:
    """Cpk 값 → 한글 판정 배지 텍스트"""
    if isinstance(cpk, dict):
        return assess_process(cpk)['text']
    try:
        v = float(cpk)
    except Exception:
        return "⬜ 판정불가"
    if not np.isfinite(v): return "⬜ 판정불가"
    if v < _warning_limit():  return "🔴 능력 지수 부족"
    if v < _required_limit():  return "🟡 능력 지수 주의"
    return "능력 지수 양호 · 안정성 별도 확인"


def _action_guide_md(cpk) -> str:
    """Cpk 값 → 권장 행동 마크다운"""
    if isinstance(cpk, dict):
        assessment = assess_process(cpk)
        return f"**{assessment['text']}**\n\n{assessment['action']}"
    try:
        v = float(cpk)
    except Exception:
        return ""
    if not np.isfinite(v): return ""
    if v < _warning_limit():
        return (
            "**🔴 즉시 조치 필요**\n\n"
            "능력 지수가 낮습니다. 실제 규격이탈 여부는 원본 이탈 건수를 확인하세요.\n\n"
            "- **확인 항목**: 설비 마모 여부, 원자재 변경, 작업 조건 변동\n"
            "- **권장 행동**: 담당자에게 즉시 보고 후 이메일 알림 발송"
        )
    if v < _required_limit():
        return (
            "**🟡 모니터링 강화 필요**\n\n"
            "공정능력이 다소 부족합니다. 지속적인 모니터링이 필요합니다.\n\n"
            "- **확인 항목**: 관리한계 이탈 점 여부, 추세(단조증가/감소) 여부\n"
            "- **권장 행동**: 주기적 데이터 확인, 이상 발생 시 즉시 보고"
        )
    return (
        "**능력 지수 양호**\n\n"
        "공정 안정성·정규성·원본 규격이탈을 함께 확인하세요."
    )


def _render_ai_interpretation(ai_res: dict, assessment=None):
    """AI 해석 결과 렌더링 (하이라이트 기능 강화)."""
    if not ai_res:
        return
    
    risk = str(ai_res.get('risk_level', '')).lower()
    if assessment:
        risk = assessment['level'] if assessment['level'] != 'unknown' else 'medium'
        st.caption(f"계산 기준 판정: {assessment['text']} · AI 설명과 원인 가설은 참고용입니다.")
    
    # ── 1. 위험 등급별 하이라이트 박스 설정 ──
    risk_config = {
        'high': ('🔴 HIGH (고위험)', '#fef2f2', '#991b1b'),
        'medium': ('🟡 MEDIUM (주의)', '#fffbeb', '#92400e'),
        'low': ('🟢 LOW (정상)', '#f0fdf4', '#166534')
    }
    badge, bg_color, border_color = risk_config.get(risk, ('⬜ UNKNOWN', '#f8fafc', '#64748b'))
    
    # 요약문 하이라이트 박스 출력
    st.markdown(f"""
        <div style="background-color:{bg_color}; border: 1px solid {border_color}; 
                    padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <div style="color:{border_color}; font-weight: bold; font-size: 1.1rem; margin-bottom: 5px;">{badge}</div>
            <div style="color: #1e293b; font-size: 1rem; line-height: 1.5;">{ai_res.get('summary', '')}</div>
        </div>
    """, unsafe_allow_html=True)

    source = "AI 해석" if ai_res.get('source') == 'ai' else "규칙기반 해석"
    note = ai_res.get('note', '')
    st.caption(f"{source} · {note}" if note else source)

    # ── 2. 근거 데이터 표 하이라이트 ──
    ev = ai_res.get('evidence', [])
    if isinstance(ev, list) and ev:
        st.markdown("**📊 AI가 포착한 이상 근거**")
        df_ev = pd.DataFrame(ev)
        
        # 표 내부의 특정 키워드(Cpk, 이탈 등) 하이라이트 스타일 함수
        def _style_evidence(row):
            return [STATUS_CELL_STYLES['warn'] if risk != 'low' else '' for _ in row]
            
        try:
            st.dataframe(df_ev.style.apply(_style_evidence, axis=1), use_container_width=True, hide_index=True)
        except Exception:
            st.dataframe(df_ev, use_container_width=True, hide_index=True)

    # ── 3. 원인 가설 및 조치 사항 (기존 유지) ──
    hy = ai_res.get('hypotheses', [])
    if isinstance(hy, list) and hy:
        st.markdown("**🔍 AI 추정 원인 가설**")
        for h in hy:
            title = h.get('title', '가설')
            conf = h.get('confidence', '')
            checks = h.get('checks', [])
            st.write(f"- **{title}**" + (f" <span style='color:#64748b; font-size:0.8rem;'>(신뢰도: {conf})</span>" if conf else ""), unsafe_allow_html=True)
            if isinstance(checks, list):
                for c in checks[:3]:
                    st.write(f"  &nbsp;&nbsp;· {c}")

    actions = ai_res.get('actions', {})
    if isinstance(actions, dict):
        c1, c2, c3 = st.columns(3)
        for col, key, label in [
            (c1, 'immediate', '즉시'),
            (c2, 'within_24h', '24시간'),
            (c3, 'within_1w', '1주'),
        ]:
            items = actions.get(key, [])
            with col:
                st.markdown(f"**{label} 조치**")
                if isinstance(items, list) and items:
                    for x in items[:5]:
                        st.write(f"- {x}")
                else:
                    st.write("- 없음")

    more = ai_res.get('need_more_data', [])
    if isinstance(more, list) and more:
        with st.expander("추가 확인 데이터", expanded=False):
            for x in more[:8]:
                st.write(f"- {x}")


def _ctrl_violation_guide_md(it: dict, beginner_mode: bool) -> str:
    """관리이탈(T1) + KSQ(T2~T8) 설명 텍스트."""
    is_xmr = bool(it.get('is_xmr', False))
    disp_kind = str(it.get('dispersion_kind', "MR" if is_xmr else "R"))
    x_lab = "X(개별값)" if is_xmr else "Xbar(부분군 평균)"
    if disp_kind == "MR":
        r_lab = "MR(이동범위)"
    elif disp_kind == "S":
        r_lab = "S(부분군 표준편차)"
    else:
        r_lab = "R(부분군 범위)"
    x_u = int(it.get('xbar_ucl_cnt', 0))
    x_l = int(it.get('xbar_lcl_cnt', 0))
    r_u = int(it.get('r_ucl_cnt', 0))
    r_l = int(it.get('r_lcl_cnt', 0))
    ksq_counts = it.get('ksq_counts', {}) or {}
    hit_rules = []
    for i in range(1, 9):
        rk = f"T{i}"
        c = int(ksq_counts.get(rk, 0))
        if c > 0:
            hit_rules.append((rk, c))

    desc_beginner = {
        "T1": "한 점이 관리한계(UCL/LCL) 밖",
        "T2": "중심선 한쪽으로 9점 연속",
        "T3": "연속 6점이 계속 증가/감소",
        "T4": "연속 14점이 위아래 번갈아 발생",
        "T5": "3점 중 2점이 2σ 밖(같은 쪽)",
        "T6": "5점 중 4점이 1σ 밖(같은 쪽)",
        "T7": "연속 15점이 중심선 근처(1σ 안)",
        "T8": "연속 8점이 중심선에서 멀리 분포(1σ 밖)",
    }
    desc_admin = {
        "T1": ("점 1개가 3σ 관리한계(UCL/LCL) 밖", "특수원인 즉시 조사 대상"),
        "T2": ("중심선 한쪽에 연속 9점", "평균의 지속적 편향/세팅 shift 가능성"),
        "T3": ("연속 6점 단조 증가 또는 단조 감소", "드리프트(공구마모·온도변화) 가능성"),
        "T4": ("연속 14점 교번(상하 번갈이)", "과조정(탬퍼링)·보정 과민 가능성"),
        "T5": ("3점 중 2점이 2σ 밖(같은 쪽)", "큰 편차가 짧은 구간에 집중"),
        "T6": ("5점 중 4점이 1σ 밖(같은 쪽)", "중간 강도의 편향이 누적"),
        "T7": ("연속 15점이 ±1σ 내부", "산포 비정상 축소/층별 데이터 혼합 가능성"),
        "T8": ("연속 8점이 ±1σ 외부(양쪽 분포)", "혼합분포·조건 전환(군집) 가능성"),
    }

    if beginner_mode:
        lines = [
            "**초보자 모드: 관리이탈/KSQ 규칙 쉽게 보기**",
            "",
            "- T1은 점이 **UCL 위** 또는 **LCL 아래**로 나가면 이상 신호로 봅니다.",
            f"- 이번 T1 결과: {x_lab} UCL {x_u}건, LCL {x_l}건 / {r_lab} UCL {r_u}건, LCL {r_l}건",
        ]
        if hit_rules:
            lines += ["", "**이번에 감지된 KSQ 패턴**"]
            for rk, c in hit_rules:
                lines.append(f"- {rk} ({c}건): {desc_beginner.get(rk, '')}")
            lines += [
                "",
                "- 실무 팁: 발생 시점 전후 변경사항(작업자/설비/자재/조건)을 먼저 비교하세요.",
            ]
        else:
            lines += ["", "- KSQ T1~T8 위반은 감지되지 않았습니다."]
        return "\n".join(lines)

    lines = [
        "**중급자 이상 모드: 관리이탈/KSQ 규칙 상세 (KS Q ISO 7870-2)**",
        "",
        "- T1 정의: 점 1개가 3σ 관리한계(UCL/LCL) 밖이면 특수원인 신호로 판정.",
        "- T1 통계적 의미: 정규·안정 상태 가정 시 양측 3σ 이탈 확률 약 0.27%(편측 약 0.135%).",
        f"- 이번 T1 결과: {x_lab} UCL {x_u}건, LCL {x_l}건 / {r_lab} UCL {r_u}건, LCL {r_l}건",
        f"- 해석 포인트: {x_lab} 이탈은 중심 이동, {r_lab} 이탈은 산포/측정계 이슈 우선 점검.",
    ]
    if hit_rules:
        lines += ["", "**감지된 KSQ 규칙(T1~T8) 상세**"]
        for rk, c in hit_rules:
            d = desc_admin.get(rk, ("", ""))
            lines.append(f"- {rk} ({c}건): {d[0]} / 해석: {d[1]}")
    else:
        lines += ["", "- KSQ T1~T8 위반 없음 (현재 데이터 기준)."]

    lines += [
        "",
        "- 권장 대응: 1) 이탈 시점 Lot/시간대 격리  2) 4M1E 변경점 추적  3) 원인 제거 후 재발 확인  4) 필요 시 한계 재산정",
    ]
    return "\n".join(lines)


def _lbl(expert: str, beginner: str) -> str:
    """초보자 모드 시 쉬운 용어 반환"""
    return beginner if st.session_state.get('beginner_mode') else expert


def _fig_to_st(fig, *, vector=False):
    """Figure를 Streamlit에 표시하고 닫기."""
    try:
        if vector:
            st.image(_figure_to_svg(fig), width='stretch')
        else:
            st.pyplot(fig)
    finally:
        plt.close(fig)


def _render_date_label_status(it):
    selected = str(st.session_state.get('measurement_date_column', '') or '').strip()
    if not selected:
        return
    if it.get('x_labels') or it.get('r_labels'):
        st.caption(f"관리도 x축: {selected}")
    else:
        st.warning(
            f"x축 컬럼 '{selected}'에서 라벨을 만들지 못해 x축 번호로 표시됩니다. "
            "DATA 값이 날짜 또는 숫자 형식인지 확인한 뒤 다시 분석하세요."
        )


_CONTROL_SCROLL_THRESHOLD = 50
# 군이 많을 때 가로 스크롤 차트의 화면 폭(px/inch). 그림 자체는 SVG라 배율과 무관하게 선명하다.
_CONTROL_SCROLL_PX_PER_INCH = 150


def _safe_len(value) -> int:
    try:
        return len(value)
    except Exception:
        return 0


def _control_group_count(it) -> int:
    return max(_safe_len(it.get('means', [])), _safe_len(it.get('ranges', [])))


def _figure_to_svg(fig) -> str:
    """글자를 외곽선이 아닌 실제 글자로 저장한다. 브라우저가 화면 배율에 맞춰 그린다."""
    buf = io.StringIO()
    with plt.rc_context({"svg.fonttype": "none"}):
        fig.savefig(
            buf,
            format="svg",
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            edgecolor="none",
        )
    return buf.getvalue()


def _inline_svg(svg: str, css_width: str) -> str:
    """SVG를 이미지로 넣지 않고 문서에 직접 넣어 글자가 다시 늘어나지 않게 한다."""
    start = svg.find("<svg")
    end = svg.rfind("</svg>")
    if start >= 0 and end > start:
        svg = svg[start:end + len("</svg>")]
    def _open(match: re.Match) -> str:
        attrs = re.sub(r'\s(?:width|height)="[^"]*"', "", match.group(1))
        style = (
            f"width:{css_width};height:auto;display:block;"
            "text-rendering:optimizeLegibility;"
        )
        return f'<svg{attrs} role="img" aria-label="관리도" style="{style}">'
    return re.sub(r"<svg\b([^>]*)>", _open, svg, count=1)


def _show_control_chart_figure(fig, *, scroll_css_width: int | None = None) -> None:
    """화면 글꼴로 관리도를 그려 글자와 선이 배율과 관계없이 선명하게 보이게 한다."""
    import streamlit.components.v1 as components

    css_width = "100%" if scroll_css_width is None else f"{int(scroll_css_width)}px"
    svg = _inline_svg(_figure_to_svg(fig), css_width)
    plt.close(fig)
    scroll = "" if scroll_css_width is None else "overflow-x:auto;overflow-y:hidden;"
    html = f"""
    <div role="region" aria-label="관리도 가로 스크롤" tabindex="0" style="margin:0;max-width:100%;{scroll}">
      {svg}
    </div>
    <script>
    function fitChart() {{
      var svg = document.querySelector('svg');
      if (!svg || !window.frameElement) return;
      var height = Math.ceil(svg.parentElement.getBoundingClientRect().height) + 8;
      var frame = window.frameElement;
      frame.style.height = height + 'px';
      if (frame.parentElement) frame.parentElement.style.height = height + 'px';
    }}
    window.addEventListener('load', fitChart);
    window.addEventListener('resize', fitChart);
    </script>
    """
    components.html(html, height=860, scrolling=False)


def _render_control_chart_scrollable(it, palette, ksq_toggles, show_zones, display_mode: str = "compact", view_key="main"):
    """군 개수가 많은 관리도는 넓은 차트를 가로 스크롤 컨테이너로 표시."""
    it, _signals = paged_window(it, view_key)
    group_count = _control_group_count(it)
    scroll_column, presentation_column = st.columns(2)
    presentation = presentation_column.toggle("관리도 한 화면에 맞춤", key=f"presentation_control_chart_{view_key}",
                                              help="그래프 높이를 줄여 위치 관리도와 산포 관리도를 한 화면에서 함께 볼 수 있게 합니다.")
    scroll_enabled = scroll_column.toggle("관리도 가로 스크롤", value=group_count > _CONTROL_SCROLL_THRESHOLD,
                                          key=f"scroll_control_chart_{view_key}", disabled=presentation,
                                          help="측정점 간격을 넓혀 표시합니다. 관리도 아래 가로 스크롤 막대로 구간을 이동하세요. 끄면 전체 구간을 화면 폭에 맞춥니다.")
    scroll_enabled = scroll_enabled and not presentation
    width_in = min(max(12.0, group_count * 0.20), 200.0)
    scroll_width = int(width_in * _CONTROL_SCROLL_PX_PER_INCH)
    if scroll_enabled:
        st.caption(f"관리도 아래 가로 스크롤 막대로 좌우 이동하세요. 현재 표시: {group_count:,}점 · 전체 보기: ‘관리도 가로 스크롤’ 끄기")
    fig = build_interactive_control_chart(it, palette, ksq_toggles, show_zones=show_zones)
    config = {"displaylogo": False, "scrollZoom": False, "doubleClick": "reset"}
    show_scrollable_interactive_chart(fig, scroll_width if scroll_enabled else None, config, presentation=presentation)
    if it.get('dispersion_limit_method'):
        st.caption(it['dispersion_limit_method'])
    if it.get('mr_gap_count'):
        st.caption(f"결측·제외로 순서가 끊긴 구간 {it['mr_gap_count']:,}개는 이동범위 계산에서 제외했습니다.")
    if it.get('control_phase') == 'phase2' and it.get('distribution_mode') != 'nonnormal':
        st.caption('연속 규칙은 조건이 완성된 시점에 표시합니다.')
    return it


class _ResultPanel:
    def __init__(self, opened):
        self.open = opened
        self.container = st.container()

    def __enter__(self):
        return self.container.__enter__()

    def __exit__(self, *args):
        return self.container.__exit__(*args)


def _result_tabs(labels):
    """Use a normal input event for reliable result navigation and reruns."""
    if st.session_state.get('advanced_tabs') not in labels:
        st.session_state['advanced_tabs'] = labels[0]
    st.markdown('''<style>
    .st-key-advanced_tabs div[role="radiogroup"] {
      flex-wrap:nowrap; overflow-x:auto; gap:12px; border-bottom:1px solid #d8e1eb;
    }
    .st-key-advanced_tabs label[data-baseweb="radio"] {
      flex:0 0 auto; margin:0; padding:7px 1px; border-bottom:2px solid transparent;
    }
    .st-key-advanced_tabs label[data-baseweb="radio"] > div:first-child {display:none;}
    .st-key-advanced_tabs label:has(input:checked) {border-bottom-color:#ff4b4b;color:#ff4b4b;}
    .st-key-advanced_tabs label:hover {color:#ff4b4b;}
    </style>''', unsafe_allow_html=True)
    selected = st.radio('결과 보기', labels, horizontal=True, key='advanced_tabs', label_visibility='collapsed')
    return tuple(_ResultPanel(label == selected) for label in labels)


def _select_detail_item(results):
    nav_c1, nav_c2, nav_c3 = st.columns([1, 6, 1])
    n_cols = len(results)
    col_names = [x['column'] for x in results]
    _vi = st.session_state.get('detail_var_idx', 0)
    if not isinstance(_vi, int):
        _vi = 0
    _vi = max(0, min(_vi, n_cols - 1))
    _sel = st.session_state.get('detail_var_sel', _vi)
    if not isinstance(_sel, int):
        _sel = _vi
    _sel = max(0, min(_sel, n_cols - 1))
    st.session_state['detail_var_idx'] = _vi
    st.session_state['detail_var_sel'] = _sel
    with nav_c1:
        if st.button("◀ 이전", use_container_width=True, disabled=(_vi == 0)):
            _new = _vi - 1
            st.session_state['detail_var_idx'] = _new
            st.session_state['detail_var_sel'] = _new
            st.rerun()
    with nav_c3:
        if st.button("다음 ▶", use_container_width=True, disabled=(_vi == n_cols - 1)):
            _new = _vi + 1
            st.session_state['detail_var_idx'] = _new
            st.session_state['detail_var_sel'] = _new
            st.rerun()
    with nav_c2:
        sel_var = st.selectbox("변수 선택", options=range(n_cols),
                               index=_sel, format_func=lambda i: str(col_names[i]),
                               key="detail_var_sel", label_visibility="collapsed")
        if not isinstance(sel_var, int):
            sel_var = _sel
        sel_var = max(0, min(sel_var, n_cols - 1))
        if sel_var != _vi:
            st.session_state['detail_var_idx'] = sel_var
            _vi = sel_var

    if not results or _vi < 0 or _vi >= len(results):
        st.warning("표시할 분석 결과가 없습니다. 분석을 다시 실행해 주세요.")
        st.stop()

    it = results[_vi]
    return it, _vi


def _render_distribution_detail(it, _vi, palette):
    distribution_range = st.radio("히스토그램 표시 범위", ["분포 확대", "규격 전체"], horizontal=True, key=f"distribution_range_{_vi}")
    h_col, q_col = st.columns(2)
    with h_col:
        try:
            fig_hist = build_histogram(it, palette=palette, bins=st.session_state['hist_bins'],
                                       range_mode='data' if distribution_range == '분포 확대' else 'spec')
            _fig_to_st(fig_hist, vector=True)
            if st.button("🔍 확대", key=f"zoom_hist_{_vi}", use_container_width=True):
                _show_chart_modal(it, "hist", range_mode="data" if distribution_range == "분포 확대" else "spec")
        except Exception as e:
            st.error(f"히스토그램 오류: {e}")
    with q_col:
        try:
            fig_qq = build_qq_plot(it, palette=palette)
            _fig_to_st(fig_qq, vector=True)
            if st.button("🔍 확대", key=f"zoom_qq_{_vi}", use_container_width=True):
                _show_chart_modal(it, "qq")
        except Exception as e:
            st.error(f"QQ 플롯 오류: {e}")


def _render_extended_diagnostics(it):
    st.markdown("**확장 슈하르트 관리한계 진단**")
    result = it.get("extended_shewhart")
    if result is None:
        reason = it.get("extended_shewhart_error") or unsupported_reason(it)
        st.info(reason or "확장 슈하르트 관리도 화면에서 확장 관리도 설정을 적용하면 진단을 확인할 수 있습니다.")
        return

    table = result["table"]
    rows = []
    for phase, label in (("감시", "감시구간"), ("기준", "초기 기준구간")):
        period = table[table["구간"] == phase]
        if period.empty:
            continue
        for name, _ in reversed(display_panels()):
            decisions = period[name + " 판정"]
            above = int(decisions.eq("상한 초과").sum())
            below = int(decisions.eq("하한 미달").sum())
            rows.append({"구간": label, "관리도": name, "부분군 수": len(period),
                         "UCL 초과": above, "LCL 미달": below, "이탈 합계": above + below})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("차트와 같은 관리한계로 판정합니다. 감시구간에서는 S의 산포 이탈을 먼저 확인하세요.")
    if not table["구간"].eq("감시").any():
        st.info("감시구간 데이터가 없습니다.")


def _render_statistics_detail(it):
    ppm_w, ppm_o, ppm_e = it.get('ppm_within', np.nan), it['ppm_ppk'], it['emp_ppm']
    _is_xmr = bool(it.get('is_xmr', False))
    _x_lab = "X" if _is_xmr else "Xbar"
    _disp_kind = str(it.get('dispersion_kind', "MR" if _is_xmr else "R"))
    _disp_kind_label = {
        "R": "R(범위)",
        "S": "S(표준편차)",
        "MR": "MR(이동범위)",
    }.get(_disp_kind, _disp_kind)
    _disp_lcl_applicable = not (_disp_kind == "R" and int(it.get('n', 0)) <= 6)
    if it.get('distribution_mode') == 'nonnormal':
        _disp_lcl_applicable = int(it.get('n', 0)) > 1
    extended = it.get('chart_usage_mode') == EXTENDED_MODE
    extended_result = it.get('extended_shewhart') if extended else None
    _norm_msg = str(it.get('norm_msg', '정규성 검정 결과 없음'))
    _n_valid = int(np.isfinite(np.asarray(it.get('series', []), dtype=float)).sum())
    _series_vals = np.asarray(it.get('series', []), dtype=float)
    _series_vals = _series_vals[np.isfinite(_series_vals)]
    _std_v = np.nan
    if len(_series_vals):
        _min_v = float(np.min(_series_vals))
        _max_v = float(np.max(_series_vals))
        _avg_v = float(np.mean(_series_vals))
        _rng_v = _max_v - _min_v
    else:
        _min_v = np.nan
        _max_v = np.nan
        _avg_v = np.nan
        _rng_v = np.nan
    if len(_series_vals) >= 2:
        _std_v = float(np.std(_series_vals, ddof=1))
    _ci95_lo, _ci95_hi = mean_confidence_interval(_avg_v, _std_v, len(_series_vals))

    _med_v = np.nan
    _q1_v = np.nan
    _q3_v = np.nan
    _iqr_v = np.nan
    if len(_series_vals):
        _med_v = float(np.median(_series_vals))
        _q1_v = float(np.percentile(_series_vals, 25))
        _q3_v = float(np.percentile(_series_vals, 75))
        _iqr_v = _q3_v - _q1_v

    _cv_pct = np.nan
    if np.isfinite(_std_v) and np.isfinite(_avg_v) and _avg_v != 0:
        _cv_pct = abs(_std_v / _avg_v) * 100.0

    _target_v = float(it.get('target', np.nan))
    _target_delta = np.nan
    _target_delta_pct = np.nan
    if np.isfinite(_avg_v) and np.isfinite(_target_v):
        _target_delta = _avg_v - _target_v
        if _target_v != 0:
            _target_delta_pct = (_target_delta / _target_v) * 100.0

    _usl_v = float(it.get('usl', np.nan))
    _lsl_v = float(it.get('lsl', np.nan))
    _margin_usl = np.nan
    _margin_lsl = np.nan
    _margin_min = np.nan
    if np.isfinite(_usl_v) and np.isfinite(_avg_v):
        _margin_usl = _usl_v - _avg_v
    if np.isfinite(_lsl_v) and np.isfinite(_avg_v):
        _margin_lsl = _avg_v - _lsl_v
    _margin_cands = [v for v in (_margin_usl, _margin_lsl) if np.isfinite(v)]
    if _margin_cands:
        _margin_min = float(min(_margin_cands))

    _skew_v = np.nan
    _kurt_v = np.nan
    if len(_series_vals) >= 3:
        try:
            _skew_v = float(stats.skew(_series_vals, nan_policy='omit', bias=False))
        except Exception:
            _skew_v = np.nan
    if len(_series_vals) >= 4:
        try:
            _kurt_v = float(stats.kurtosis(_series_vals, nan_policy='omit', fisher=False, bias=False))
        except Exception:
            _kurt_v = np.nan

    def _cap_ci_approx(v, n_obs: int):
        try:
            fv = float(v)
        except Exception:
            return np.nan, np.nan
        if (not np.isfinite(fv)) or n_obs < 2:
            return np.nan, np.nan
        se = math.sqrt(max(0.0, (1.0 / (9.0 * n_obs)) + ((fv * fv) / (2.0 * max(1, n_obs - 1)))))
        z = 1.96
        return fv - z * se, fv + z * se

    def _margin_level(v) -> str:
        try:
            fv = float(v)
        except Exception:
            return "muted"
        if not np.isfinite(fv):
            return "muted"
        return "bad" if fv < 0 else "good"

    def _num(v, d: int = 3) -> str:
        try:
            fv = float(v)
        except Exception:
            return "—"
        return f"{fv:.{d}f}" if np.isfinite(fv) else "—"

    def _ppm(v) -> str:
        try:
            fv = float(v)
        except Exception:
            return "—"
        return f"{fv:,.0f}" if np.isfinite(fv) else "—"

    def _cp_level(v, metric='cpk') -> str:
        try:
            fv = float(v)
            if not np.isfinite(fv):
                return "muted"
            state = _assessment_band(fv, it, metric)
            return 'muted' if state == 'na' else state
        except Exception:
            return "muted"

    def _ppm_level(v) -> str:
        try:
            fv = float(v)
        except Exception:
            return "muted"
        if not np.isfinite(fv):
            return "muted"
        if fv > 50000:
            return "bad"
        if fv > 10000:
            return "warn"
        return "good"

    def _kv_block(title: str, rows, extra_class: str = "", *, reference=None) -> str:
        reference_html = (f"<div class='stats-limit-reference'><strong>한계 기준</strong> · {html.escape(reference)}</div>"
                          if reference else "")
        _rows_html = []
        for _row in rows:
            if len(_row) >= 3:
                k, v, cls = _row[0], _row[1], str(_row[2] or "")
            else:
                k, v = _row[0], _row[1]
                cls = ""
            if cls in {"good", "warn", "bad", "muted"}:
                v_html = f"<span class='stats-val stats-val-{cls}'>{v}</span>"
            else:
                v_html = str(v)
            _rows_html.append(f"<tr><td>{k}</td><td>{v_html}</td></tr>")
        _rows = "".join(_rows_html)
        _block_class = "stats-block"
        if str(extra_class).strip():
            _block_class += f" {str(extra_class).strip()}"
        return (
            f"<div class='{_block_class}'>"
            f"<div class='stats-title'>{title}</div>"
            f"{reference_html}<table class='stats-kv'>{_rows}</table>"
            f"</div>"
        )

    from spc_policy import REFERENCE_COMPARISON_LABEL, reference_only_decision

    if it.get('distribution_warning'):
        st.warning(it['distribution_warning'])
    _pair = screening_index_pair(it)
    st.caption(_pair['state'])
    _selected_metrics = [key for key in (_pair['width_metric'], _pair['shifted_metric']) if key]
    _modeled = it.get('distribution_mode') == 'nonnormal' or it.get('box_cox_applied')
    _suffix = '.' + it.get('capability_method', 'G') if _modeled else ''
    _selected_rows = []
    _kpis = []
    for _key in _selected_metrics:
        _value = it.get(_key, np.nan)
        _label = _key.capitalize() + _suffix
        _decision = it.get('capability_criteria', {}).get('indices', {}).get(_key, {})
        _sub = ('모형 기반' if _modeled else '전체산포 고려') if _num(_value) != '—' else '규격·계산 조건 확인'
        if _num(_value) == '—' and _key in ('cp', 'pp') and any(
                not np.isfinite(it.get(side, np.nan)) for side in ('usl', 'lsl')):
            _sub = '양측 규격 필요'
        if reference_only_decision(_decision):
            _sub = REFERENCE_COMPARISON_LABEL
        _cls = _cp_level(_value, _key)
        if _key == _pair['shifted_metric']:
            _state = screening_index_band(it)
            _cls = 'muted' if _state == 'na' else _state
        _kpis.append((_label, _num(_value), _sub, _cls))
        _selected_rows.append((_label, _num(_value), _cls))

    _within_label = 'MR 참고' if it.get('within_estimator') == 'moving_range' and not it.get('box_cox_applied') else '군내산포 고려'
    _within_rows = []
    for _key in ('cw', 'cwk'):
        _value = _num(it.get(_key, np.nan))
        _label = f'{_key.capitalize()} ({_within_label})'
        _sub = '현재 모형에서 미적용' if _value == '—' and _modeled else '계산 조건 확인' if _value == '—' else ''
        _kpis.append((_label, _value, _sub, 'muted'))
        _within_rows.append((_label, _value, 'muted'))
    _kpis.extend([
        ("PPM(Overall)", _ppm(ppm_o), "100만개 기준", _ppm_level(ppm_o)),
        ("유효 표본수", f"{_n_valid:,}", "결측 제외", "muted"),
    ])
    for _col, (_lbl, _val, _sub, _cls) in zip(st.columns(len(_kpis)), _kpis):
        _col.markdown(
            f"""
            <div class="stats-kpi stats-kpi-{_cls}">
                <div class="stats-kpi-label">{_lbl}</div>
                <div class="stats-kpi-value {_cls}">{_val}</div>
                <div class="stats-kpi-sub">{_sub}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    d1, d2, d3, d4 = st.columns([1.0, 1.0, 1.0, 1.22])
    _spec_rows = [
        ("USL (원단위)" if it.get('box_cox_applied') else "USL", _num(it.get('original_usl' if it.get('box_cox_applied') else 'usl', np.nan)), "muted"),
        ("LSL (원단위)" if it.get('box_cox_applied') else "LSL", _num(it.get('original_lsl' if it.get('box_cox_applied') else 'lsl', np.nan)), "muted"),
        ("목표치(T)", _num(it.get('target', np.nan)), "muted"),
        ("운영 모드", "확장 슈하르트" if extended else str(it.get('control_phase_label', '해석용')), "muted"),
        *_selected_rows,
        *_within_rows,
        ("Cpm (목표치 고려)", _num(it.get('cpm', np.nan)), _cp_level(it.get('cpm', np.nan), 'cpm')),
        ("Cpmk (목표치·치우침 고려)", _num(it.get('cpmk', np.nan)), _cp_level(it.get('cpmk', np.nan), 'cpmk')),
    ]
    _spec_v = int(it.get('spec_violation_count', 0))
    _ppm_rows = [
        ("PPM(Within)", _ppm(ppm_w), _ppm_level(ppm_w)),
        ("PPM(Overall)", _ppm(ppm_o), _ppm_level(ppm_o)),
        ("PPM(실측)", _ppm(ppm_e), _ppm_level(ppm_e)),
        ("규격 이탈", f"{_spec_v:,} 건", "bad" if _spec_v > 0 else "good"),
    ]
    if extended:
        _ctrl_rows = []
        _reference = "확장 관리도 설정 필요"
        if extended_result is not None:
            _reference = str(extended_result["reference"] or f"초기 기준 부분군 {extended_result['count']:,}개")
            signals = monitor_signals(extended_result)
            for name, _ in reversed(display_panels()):
                count = int(signals[name].sum())
                tally = f"{count:,} / {len(signals):,}개" if not signals.empty else "—"
                _ppm_rows.append((f"{name} 이탈", tally, "muted" if signals.empty else "bad" if count else "good"))
                lower, center, upper = extended_result["limits"][name]
                _ctrl_rows.extend((f"{name} {label}", f"{value:.8g}", "muted")
                                  for label, value in (("CL", center), ("UCL", upper), ("LCL", lower)))
    else:
        _x_v, _r_v = _get_ctrl_counts(it)
        _ppm_rows.extend([
            (f"{_x_lab} 이탈", f"{_x_v:,} 건", "bad" if _x_v > 0 else "good"),
            (f"{_disp_kind_label} 이탈", f"{_r_v:,} 건", "bad" if _r_v > 0 else "good"),
        ])
        _reference = str(it.get('limit_reference_label', '전체 데이터'))
        _ctrl_rows = [
            (f"{_x_lab} CL", _num(it.get('xbarbar', np.nan)), "muted"),
            (f"{_x_lab} UCL", _num(it.get('ucl', np.nan)), "muted"),
            (f"{_x_lab} LCL", _num(it.get('lcl', np.nan)), "muted"),
            (f"{_disp_kind} CL", _num(it.get('r_bar', np.nan)), "muted"),
            (f"{_disp_kind} UCL", _num(it.get('r_ucl', np.nan)), "muted"),
        ]
        if _disp_lcl_applicable:
            _ctrl_rows.append((f"{_disp_kind} LCL", _num(it.get('r_lcl', np.nan)), "muted"))
    _basic_rows_core = [
        ("최소", _num(_min_v), "muted"),
        ("최대", _num(_max_v), "muted"),
        ("평균(μ)", _num(_avg_v), "muted"),
        ("중앙값(Median)", _num(_med_v), "muted"),
        ("Q1 / Q3", f"{_num(_q1_v)} / {_num(_q3_v)}", "muted"),
        ("IQR(Q3-Q1)", _num(_iqr_v), "muted"),
        ("범위", _num(_rng_v), "muted"),
        ("95% CI 하한(μ)", _num(_ci95_lo), "muted"),
        ("95% CI 상한(μ)", _num(_ci95_hi), "muted"),
    ]
    _aux_rows_var_target = [
        ("변동계수 CV(%)", _num(_cv_pct, 2), "muted"),
        ("목표치 편차(μ-T)", _num(_target_delta), "muted"),
        ("편차율(%)", _num(_target_delta_pct, 2), "muted"),
    ]
    _aux_rows_margin = [
        ("규격여유 USL-μ", _num(_margin_usl), _margin_level(_margin_usl)),
        ("규격여유 μ-LSL", _num(_margin_lsl), _margin_level(_margin_lsl)),
        ("최소 규격여유", _num(_margin_min), _margin_level(_margin_min)),
    ]
    _aux_rows_shape_ci = [
        ("왜도", _num(abs(_skew_v)), "muted"),
        ("첨도", _num(abs(_kurt_v)), "muted"),
    ]
    _ci_key = _pair['shifted_metric']
    if _ci_key and not _modeled and '만족' in str(it.get('norm_msg', '')):
        _ci_lo, _ci_hi = _cap_ci_approx(it.get(_ci_key, np.nan), len(_series_vals))
        if np.isfinite([_ci_lo, _ci_hi]).all():
            _aux_rows_shape_ci.append((f"{_ci_key.capitalize()} 95% CI(근사)",
                                       f"{_num(_ci_lo)} ~ {_num(_ci_hi)}", "muted"))
    with d1:
        st.markdown(_kv_block("규격 및 능력 지수", _spec_rows), unsafe_allow_html=True)
    with d2:
        st.markdown(_kv_block("불량률 및 이탈 집계", _ppm_rows), unsafe_allow_html=True)
    with d3:
        st.markdown(_kv_block("관리한계선 파라미터", _ctrl_rows, "stats-limits", reference=_reference),
                    unsafe_allow_html=True)
    with d4:
        st.markdown(_kv_block("기초 통계량", _basic_rows_core), unsafe_allow_html=True)

    a1, a2, a3 = st.columns([1.0, 1.0, 1.20])
    with a1:
        st.markdown(_kv_block("변동/목표 편차", _aux_rows_var_target, "stats-block-compact"), unsafe_allow_html=True)
    with a2:
        st.markdown(_kv_block("규격 여유", _aux_rows_margin, "stats-block-compact"), unsafe_allow_html=True)
    with a3:
        st.markdown(_kv_block("분포 형상/능력 CI", _aux_rows_shape_ci, "stats-block-compact"), unsafe_allow_html=True)

    if extended:
        _render_extended_diagnostics(it)
    else:
        _ksq_c = it.get('ksq_counts', {})
        _ksq_total = sum(int(_ksq_c.get(f'T{j}', 0)) for j in range(1, 9))
        _ksq_df = pd.DataFrame([{f'T{j}': int(_ksq_c.get(f'T{j}', 0)) for j in range(1, 9)}], index=["위반 건수"])
        if it.get('distribution_mode') == 'nonnormal':
            st.caption(it.get('pearson_rules_note', ''))
            _ksq_df = _ksq_df[['T1']]
        st.markdown("**관리한계 이탈 진단 (T1)**" if it.get('distribution_mode') == 'nonnormal' else "**KSQ 규칙 진단 매트릭스 (T1~T8)**")
        try:
            _ksq_style = lambda v: STATUS_CELL_STYLES['bad' if int(v) > 0 else 'good']
            if hasattr(_ksq_df.style, 'map'):
                _ksq_styled = _ksq_df.style.map(_ksq_style)
            else:
                _ksq_styled = _ksq_df.style.applymap(_ksq_style)
            st.dataframe(_ksq_styled, use_container_width=True)
        except Exception:
            st.dataframe(_ksq_df, use_container_width=True)

        if _ksq_total == 0:
            _ksq_cls = "good"
            _ksq_txt = "KSQ 규칙 위반 없음"
        else:
            _ksq_cls = "bad"
            _ksq_txt = f"KSQ 규칙 위반 {int(_ksq_total)}건 감지"
        st.markdown(
            f"<div class='stats-note {_ksq_cls}'><b>규칙 판정:</b> {_ksq_txt}</div>",
            unsafe_allow_html=True,
        )

    if "만족" in _norm_msg:
        _norm_cls = "good"
        _norm_txt = "정규성 가정에 대체로 부합"
    elif "의심" in _norm_msg:
        _norm_cls = "bad"
        _norm_txt = "정규성 이탈 가능성 높음"
    else:
        _norm_cls = "warn"
        _norm_txt = "정규성 판정 정보 확인 필요"
    st.markdown(
        f"<div class='stats-note {_norm_cls}'><b>정규성 검정:</b> {_norm_txt} · {_norm_msg}</div>",
        unsafe_allow_html=True,
    )


def _mode_changed():
    if not st.session_state.get('beginner_mode'):
        st.session_state['advanced_tabs'] = '📉 관리도'
    _save_config()


def _open_intermediate():
    st.session_state['beginner_mode'] = False
    _mode_changed()


def _render_baseline_input():
    extended = st.session_state['chart_usage_mode'] == EXTENDED_MODE
    baseline_unit = "샘플" if st.session_state['n'] == 1 else "부분군"
    baseline_subgroups = st.number_input(
        "초기 기준 부분군 수" if extended else f"기준구간 수 (초기 {baseline_unit})",
        min_value=3,
        max_value=200,
        value=int(st.session_state.get('baseline_subgroups', 25)),
        help="초기 기준구간으로 관리한계를 계산하고 이후 데이터는 같은 한계로 감시합니다. 실무에서는 20~25개 이상 권장입니다.",
    )
    st.session_state['baseline_subgroups'] = int(baseline_subgroups)
    if extended:
        st.caption("입력한 개수만큼 초기 부분군으로 관리한계를 계산하고, 다음 부분군부터 감시합니다. 자동 산정에는 유효 기준 부분군이 6개 이상 필요합니다.")
    else:
        st.caption("관리용은 안정하다고 판단되는 초기 구간으로 한계선을 고정하고, 이후 구간만 감시용으로 판정합니다.")


# ─────────────────────────── 사이드바 ───────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ 분석 설정")

    _bm = st.radio('사용 모드', [True, False], key='beginner_mode', horizontal=True,
        format_func=lambda value: '🔰 초보자' if value else '중급자 이상', on_change=_mode_changed)
    st.caption('핵심 공정능력 지수와 관리도를 한 화면에서 확인합니다.' if _bm else '상세 통계와 고급 분석 기능을 사용할 수 있습니다.')
    st.caption(f"관리도 선택 · {chart_selection_label(st.session_state)}")
    _chart_guide_slot = st.empty()

    st.divider()

    if not _bm and not education_mode():
        # ── 즐겨찾기 ──
        with st.expander("⭐ 즐겨찾기"):
            favs: list = st.session_state.get("favorites", [])

            # 불러오기 완료 토스트 메시지 처리
            _toast_msg = st.session_state.pop("_fav_toast_msg", None)
            if st.session_state.pop("_fav_toast", False) and _toast_msg:
                st.toast(_toast_msg, icon="✅")

            if favs:
                fav_names = [f["name"] for f in favs]
                # sel_idx는 session_state에서 직접 읽어 안전하게 처리
                saved_idx = st.session_state.get("fav_sel_idx", 0)
                if not isinstance(saved_idx, int) or saved_idx >= len(fav_names):
                    saved_idx = 0
                sel_idx = st.selectbox(
                    "저장된 즐겨찾기",
                    options=range(len(fav_names)),
                    index=saved_idx,
                    format_func=lambda i: fav_names[i],
                    key="fav_sel_idx",
                )
                btn_c1, btn_c2 = st.columns(2)
                with btn_c1:
                    if st.button("불러오기", key="fav_load_btn", use_container_width=True):
                        fav = favs[sel_idx]
                        try:
                            data_path = fav.get("data_path", "")
                            if not data_path or not os.path.isfile(data_path):
                                st.error(f"파일을 찾을 수 없습니다:\n{data_path}")
                            else:
                                with open(data_path, "rb") as fh:
                                    st.session_state["data_bytes"] = fh.read()
                                st.session_state["data_sheet"] = fav.get("data_sheet") or None
                                st.session_state["active_data_path"] = _norm_path(data_path)
                                st.session_state["data_file_name"] = os.path.basename(data_path)
                                sp = fav.get("spec_path", "")
                                if sp and os.path.isfile(sp):
                                    with open(sp, "rb") as fh:
                                        st.session_state["spec_bytes"] = fh.read()
                                    st.session_state["spec_sheet"] = fav.get("spec_sheet") or None
                                    st.session_state["active_spec_path"] = _norm_path(sp)
                                    st.session_state["spec_file_name"] = os.path.basename(sp)
                                else:
                                    st.session_state["spec_bytes"] = None
                                    st.session_state["spec_sheet"] = None
                                    st.session_state["active_spec_path"] = ""
                                    st.session_state["spec_file_name"] = ""
                                # rerun 후 toast 표시를 위해 플래그 사용
                                st.session_state["_loaded_fav_name"] = fav["name"]
                                st.session_state.pop('_input_identity', None)
                                st.session_state.pop('data_uploader', None)
                                st.session_state.pop('spec_uploader', None)
                                from spc_file_ui import invalidate_analysis
                                invalidate_analysis(st.session_state)
                                st.session_state["_fav_toast"] = True
                                st.session_state["_fav_toast_msg"] = f"'{fav['name']}' 불러오기 완료 — ▶ 분석 시작을 눌러주세요"
                                _save_config()
                                st.rerun()
                        except Exception as e:
                            st.error(f"불러오기 실패: {e}")
                with btn_c2:
                    if st.button("삭제", key="fav_del_btn", use_container_width=True):
                        st.session_state["favorites"] = [f for i, f in enumerate(favs) if i != sel_idx]
                        _save_config()
                        st.rerun()

                # 로드 상태 표시
                if st.session_state.get("data_bytes"):
                    st.caption("✅ 데이터 로드됨 — ▶ 분석 시작을 눌러주세요")

                st.divider()
                # ── 자동 분석 감시 ──
                auto_on = st.checkbox(
                    "🔄 파일 변경 시 자동 분석",
                    key='auto_watch_enabled',
                )
                if auto_on:
                    st.selectbox(
                        "감시할 즐겨찾기",
                        options=range(len(fav_names)),
                        index=min(st.session_state.get('auto_watch_fav_idx', 0), len(fav_names) - 1),
                        format_func=lambda i: fav_names[i],
                        key='auto_watch_fav_idx',
                    )
                    st.caption("⏱ 10초마다 파일 변경을 감지합니다")
                st.divider()

            st.markdown("**즐겨찾기 추가**")
            fav_name_in = st.text_input("이름", key="fav_name_in", placeholder="예) 월간품질데이터")

            # ── 버퍼 → 위젯 key 복사 (위젯 렌더 전에 수행해야 함) ──
            if '_buf_fav_data' in st.session_state:
                st.session_state['fav_data_in'] = st.session_state.pop('_buf_fav_data')
            if '_buf_fav_spec' in st.session_state:
                st.session_state['fav_spec_in'] = st.session_state.pop('_buf_fav_spec')

            # 데이터 파일 경로 + 찾아보기
            dp_col, dp_btn = st.columns([3, 1])
            with dp_col:
                fav_data_in = st.text_input("데이터 파일 경로", key="fav_data_in")
            with dp_btn:
                st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
                if st.button("탐색", key="fav_browse_data"):
                    _browse_file("_buf_fav_data")
                    st.rerun()

            fav_data_sheets = []
            fav_data_path = _norm_path(st.session_state.get("fav_data_in", ""))
            if fav_data_path and os.path.isfile(fav_data_path) and not fav_data_path.lower().endswith(".csv"):
                try:
                    fav_data_sheets = get_sheet_names(fav_data_path)
                except Exception:
                    fav_data_sheets = []
            if fav_data_sheets:
                _saved_dsheet = str(st.session_state.get("fav_dsheet_in", "") or "")
                _dsheet_options = ["(첫 시트)"] + fav_data_sheets
                _dsheet_index = _dsheet_options.index(_saved_dsheet) if _saved_dsheet in _dsheet_options else 0
                fav_dsheet_sel = st.selectbox(
                    "데이터 시트 (선택)",
                    options=_dsheet_options,
                    index=_dsheet_index,
                    key="fav_dsheet_select",
                )
                fav_dsheet = "" if fav_dsheet_sel == "(첫 시트)" else fav_dsheet_sel
                st.session_state["fav_dsheet_in"] = fav_dsheet
            else:
                fav_dsheet = st.text_input(
                    "데이터 시트 (선택)",
                    key="fav_dsheet_in",
                    placeholder="CSV 또는 첫 시트 사용 시 비워두세요",
                )

            # 규격 파일 경로 + 찾아보기
            sp_col, sp_btn = st.columns([3, 1])
            with sp_col:
                fav_spec_in = st.text_input("규격 파일 경로 (선택)", key="fav_spec_in")
            with sp_btn:
                st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
                if st.button("탐색", key="fav_browse_spec"):
                    _browse_file("_buf_fav_spec")
                    st.rerun()

            fav_spec_sheets = []
            fav_spec_path = _norm_path(st.session_state.get("fav_spec_in", ""))
            if fav_spec_path and os.path.isfile(fav_spec_path) and not fav_spec_path.lower().endswith(".csv"):
                try:
                    fav_spec_sheets = get_sheet_names(fav_spec_path)
                except Exception:
                    fav_spec_sheets = []
            if fav_spec_sheets:
                _saved_ssheet = str(st.session_state.get("fav_ssheet_in", "") or "")
                _ssheet_options = ["(첫 시트)"] + fav_spec_sheets
                _ssheet_index = _ssheet_options.index(_saved_ssheet) if _saved_ssheet in _ssheet_options else 0
                fav_ssheet_sel = st.selectbox(
                    "규격 시트 (선택)",
                    options=_ssheet_options,
                    index=_ssheet_index,
                    key="fav_ssheet_select",
                )
                fav_ssheet = "" if fav_ssheet_sel == "(첫 시트)" else fav_ssheet_sel
                st.session_state["fav_ssheet_in"] = fav_ssheet
            else:
                fav_ssheet = st.text_input(
                    "규격 시트 (선택)",
                    key="fav_ssheet_in",
                    placeholder="CSV 또는 첫 시트 사용 시 비워두세요",
                )
            if st.button("즐겨찾기 저장", key="fav_save_btn", use_container_width=True):
                if not fav_name_in.strip():
                    st.error("이름을 입력하세요.")
                elif not fav_data_in.strip():
                    st.error("데이터 파일 경로를 입력하세요.")
                elif not os.path.isfile(fav_data_in.strip()):
                    st.error("데이터 파일 경로를 찾을 수 없습니다.")
                else:
                    new_fav = {
                        "name": fav_name_in.strip(),
                        "data_path": _norm_path(fav_data_in.strip()),
                        "data_sheet": fav_dsheet.strip() or None,
                        "spec_path": _norm_path(fav_spec_in.strip()),
                        "spec_sheet": fav_ssheet.strip() or None,
                    }
                    existing = st.session_state.get("favorites", [])
                    # 같은 이름이면 덮어쓰기
                    existing = [f for f in existing if f["name"] != new_fav["name"]]
                    existing.append(new_fav)
                    st.session_state["favorites"] = existing
                    _save_config()
                    st.success(f"'{new_fav['name']}' 저장됨")
                    st.rerun()

        st.divider()

    render_samples(expanded=education_mode())

    from spc_file_ui import render_file_inputs
    render_file_inputs()
    from spc_review_ui import render_assessment_settings
    if _bm:
        beginner_conditions()
        st.button('중급자 모드에서 확인', key='beginner_open_intermediate', on_click=_open_intermediate,
                  help='평가 기준 변경과 상세 분석을 확인합니다. 현재 파일과 분석 결과는 유지됩니다.')

    if not _bm:
        with st.expander("공정·평가"):
            render_assessment_settings(_save_config)
            st.markdown("#### 공정 정보")
            st.caption("분석 계산에는 사용하지 않고, 결과 화면과 다운로드 리포트의 식별 정보로 저장합니다.")
            proc_c1, proc_c2 = st.columns(2)
            for idx, (key, label) in enumerate(PROCESS_INFO_FIELDS):
                target_col = proc_c1 if idx % 2 == 0 else proc_c2
                with target_col:
                    if key == "note":
                        st.text_area(label, key=f"process_info_{key}", height=70, on_change=_save_config)
                    else:
                        st.text_input(label, key=f"process_info_{key}", on_change=_save_config)
            st.session_state["process_info"] = _process_info_from_state()
            if st.button("공정 정보 초기화", key="process_info_clear_btn", use_container_width=True):
                st.session_state["process_info"] = _normalize_process_info({})
                for key, _label in PROCESS_INFO_FIELDS:
                    st.session_state[f"process_info_{key}"] = ""
                _save_config()
                st.rerun()

    _df_layout = None
    _all_col_labels = []
    _numeric_col_labels = []
    if st.session_state.get('data_bytes'):
        try:
            _df_layout = load_data_df(
                io.BytesIO(st.session_state['data_bytes']),
                sheet_name=st.session_state.get('data_sheet'),
            )
            _all_col_labels = [str(c) for c in _df_layout.columns]
            _numeric_col_labels = [str(c) for c in _df_layout.columns if pd.api.types.is_numeric_dtype(_df_layout[c])]
        except Exception:
            _df_layout = None
            _all_col_labels = []
            _numeric_col_labels = []

    if not _bm:
        with st.expander("분석·관리도"):
            st.markdown("#### 분석 파라미터")
            # 프리셋 불러오기
            presets = st.session_state.get('param_presets', [])
            if presets:
                preset_names = ["(직접 설정)"] + [p['name'] for p in presets]
                sel_preset = st.selectbox("프리셋", preset_names, key="sel_preset")
                p_col1, p_col2 = st.columns(2)
                with p_col1:
                    if st.button("적용", key="apply_preset", use_container_width=True):
                        if sel_preset != "(직접 설정)":
                            p = next(p for p in presets if p['name'] == sel_preset)
                            st.session_state['n'] = p['n']
                            st.session_state['include_incomplete'] = p['include_incomplete']
                            st.session_state['data_layout_mode'] = p.get('data_layout_mode', 'long')
                            st.session_state['subgroup_columns'] = p.get('subgroup_columns', [])
                            st.session_state['subgroup_var_name'] = p.get('subgroup_var_name', '측정치')
                            st.session_state['measurement_date_column'] = p.get('measurement_date_column', '')
                            st.session_state['chart_usage_mode'] = p.get('chart_usage_mode', 'phase1')
                            st.session_state['baseline_subgroups'] = p.get('baseline_subgroups', 25)
                            st.session_state['distribution_settings'] = p.get('distribution_settings', {})
                            st.session_state['hist_bins'] = p['hist_bins']
                            st.session_state['palette'] = p['palette']
                            st.session_state['ksq_toggles'] = p['ksq_toggles']
                            st.session_state['show_zones'] = p['show_zones']
                            st.rerun()
                with p_col2:
                    if st.button("삭제", key="del_preset", use_container_width=True):
                        if sel_preset != "(직접 설정)":
                            st.session_state['param_presets'] = [p for p in presets if p['name'] != sel_preset]
                            _save_config()
                            st.rerun()
                st.divider()

            layout_labels = {
                'long': "세로형 (기존, 한 열 시계열)",
                'wide_subgroup_cols': "부분군 다열 (한 행=1부분군, 여러 열=반복 측정)",
            }
            data_layout_mode = st.radio(
                "데이터 구조",
                options=list(layout_labels.keys()),
                index=list(layout_labels.keys()).index(st.session_state.get('data_layout_mode', 'long')),
                format_func=lambda x: layout_labels.get(x, x),
            )
            st.session_state['data_layout_mode'] = data_layout_mode

            if _all_col_labels:
                _saved_date_col = str(st.session_state.get('measurement_date_column', '') or '')
                _auto_date_col = _auto_detect_date_column(_df_layout) if _df_layout is not None else ""
                _default_date_col = _saved_date_col if _saved_date_col in _all_col_labels else _auto_date_col
                _date_options = ["사용 안 함"] + _all_col_labels
                _date_index = _date_options.index(_default_date_col) if _default_date_col in _date_options else 0
                _date_sel = st.selectbox(
                    "측정일자 컬럼",
                    options=_date_options,
                    index=_date_index,
                    help="선택한 컬럼의 날짜를 관리도 x축 라벨로 표시합니다. 계산값에는 영향을 주지 않습니다.",
                )
                _next_date_col = "" if _date_sel == "사용 안 함" else _date_sel
                _prev_date_col = str(st.session_state.get('measurement_date_column', '') or '')
                st.session_state['measurement_date_column'] = _next_date_col
                if (
                    _next_date_col != _prev_date_col
                    and st.session_state.get('analysis_done')
                    and st.session_state.get('data_bytes')
                ):
                    st.session_state['_trigger_analysis'] = True
                    st.info("측정일자 컬럼이 변경되어 현재 설정으로 다시 분석합니다.")
                if st.session_state['measurement_date_column']:
                    _numeric_col_labels = [c for c in _numeric_col_labels if c != st.session_state['measurement_date_column']]
                    if _auto_date_col and _date_sel == _auto_date_col:
                        st.caption(f"측정일자 후보 자동 선택: {_auto_date_col}")
            else:
                st.session_state['measurement_date_column'] = ""

            if data_layout_mode == 'long':
                n_val = st.number_input(
                    "시료 크기 (n)",
                    min_value=1, max_value=25, value=st.session_state['n'],
                    help="n=1 → I-MR (X-MR), n=2~9 → Xbar-R, n≥10 → Xbar-S(자동 전환)",
                )
                st.session_state['n'] = int(n_val)

                st.caption("불완전 부분군은 관리도·능력 계산에서 제외합니다. 원본 규격이탈 집계에는 포함됩니다.")
                st.session_state['include_incomplete'] = False
            else:
                if _numeric_col_labels:
                    _auto_subgroup = _auto_detect_subgroup_columns(_numeric_col_labels)
                    _saved_subgroup = [c for c in st.session_state.get('subgroup_columns', []) if c in _numeric_col_labels]
                    if len(_saved_subgroup) < 2:
                        _saved_subgroup = _auto_subgroup

                    auto_c1, auto_c2 = st.columns([1, 2])
                    with auto_c1:
                        if st.button("측정열 자동 선택", key="auto_pick_subgroup_cols", use_container_width=True):
                            st.session_state['subgroup_columns'] = list(_auto_subgroup)
                            st.rerun()
                    with auto_c2:
                        st.caption(f"자동 후보: {', '.join(_auto_subgroup[:8])}" if _auto_subgroup else "자동 후보를 찾지 못했습니다.")

                    subgroup_cols = st.multiselect(
                        "부분군 측정 열 선택",
                        options=_numeric_col_labels,
                        default=_saved_subgroup,
                        help="한 행의 반복 측정값이 들어있는 열들을 선택하세요. 선택한 열 수가 시료 크기 n으로 자동 적용됩니다.",
                    )
                    st.session_state['subgroup_columns'] = subgroup_cols
                    if len(subgroup_cols) >= 2:
                        st.session_state['n'] = int(len(subgroup_cols))
                        st.caption(f"선택 열 {len(subgroup_cols)}개 기준으로 시료 크기 n={len(subgroup_cols)} 자동 적용")
                    else:
                        st.warning("부분군 다열 모드에서는 측정 열을 2개 이상 선택해야 합니다.")
                else:
                    st.warning("데이터 파일 업로드 후 수치형 측정 열을 선택할 수 있습니다.")

                subgroup_var_name = st.text_input(
                    "부분군 변수명",
                    value=str(st.session_state.get('subgroup_var_name', '측정치')),
                    help="차트와 요약표에 표시할 변수명입니다. 비워두면 '측정치'로 표시됩니다.",
                )
                st.session_state['subgroup_var_name'] = subgroup_var_name.strip() or "측정치"
                st.session_state['include_incomplete'] = False

            st.markdown("##### 🎯 목표치(T) 수동 입력 (선택)")
            _mt_enabled = st.checkbox(
                "수동 목표치 사용 (규격 파일 Target보다 우선 적용)",
                key='manual_target_enabled',
                help="입력한 목표치(T)는 동일 변수명에 대해 규격 파일의 Target 값을 덮어씁니다.",
            )
            if _mt_enabled:
                if data_layout_mode == 'wide_subgroup_cols':
                    _target_vars = [str(st.session_state.get('subgroup_var_name', '측정치')).strip() or "측정치"]
                else:
                    _target_vars = [str(c) for c in _numeric_col_labels]

                if _target_vars:
                    _existing_mt = _normalize_manual_target_map(st.session_state.get('manual_target_map', {}))
                    _rows = [{"변수": v, "목표치(T)": _existing_mt.get(v, np.nan)} for v in _target_vars]
                    _df_mt = pd.DataFrame(_rows)
                    _edited_mt = st.data_editor(
                        _df_mt,
                        hide_index=True,
                        use_container_width=True,
                        disabled=["변수"],
                        column_config={
                            "목표치(T)": st.column_config.NumberColumn(
                                "목표치(T)",
                                format="%.6g",
                            ),
                        },
                        key="manual_target_editor",
                    )

                    _next_mt = _manual_target_map_from_editor(_target_vars, _edited_mt)
                    st.session_state['manual_target_map'] = _next_mt

                    _active_cnt = sum(1 for _k in _target_vars if _k in _next_mt)
                    st.caption(f"수동 목표치 적용 변수: {_active_cnt}개 (빈칸은 자동 규칙: Target 미입력 시 규격 중앙값 사용)")
                    if st.button("수동 목표치 입력값 초기화", key="clear_manual_target_btn", use_container_width=True):
                        st.session_state['manual_target_map'] = {}
                        if 'manual_target_editor' in st.session_state:
                            del st.session_state['manual_target_editor']
                        st.rerun()
                else:
                    st.info("데이터 파일 업로드 후 변수별 목표치를 입력할 수 있습니다.")

            mode_labels = {
                'phase1': "해석용 관리도",
                'phase2': "관리용 관리도",
                EXTENDED_MODE: "확장 슈하르트 관리도",
            }
            saved_mode = st.session_state.get('chart_usage_mode', 'phase1')
            chart_usage_mode = st.radio(
                "관리도 운영 모드",
                options=list(mode_labels.keys()),
                index=list(mode_labels).index(saved_mode) if saved_mode in mode_labels else 0,
                format_func=lambda x: mode_labels.get(x, x),
            )
            st.session_state['chart_usage_mode'] = chart_usage_mode
            if chart_usage_mode != EXTENDED_MODE:
                st.session_state['_standard_chart_usage_mode'] = chart_usage_mode

            if chart_usage_mode == 'phase1':
                st.caption("해석용은 현재 데이터 전체로 시험 관리한계를 계산해 공정 안정 상태를 먼저 확인합니다.")
            else:
                _render_baseline_input()

            hist_bins = st.slider("히스토그램 구간 수", 5, 100, st.session_state['hist_bins'])
            st.session_state['hist_bins'] = hist_bins

            palette = st.selectbox(
                "색상 테마",
                options=list(PALETTES.keys()),
                index=list(PALETTES.keys()).index(st.session_state['palette']),
                format_func=lambda x: {'trust': '신뢰(파랑)', 'analytics': '분석(보라)', 'dark': '다크'}.get(x, x),
            )
            st.session_state['palette'] = palette

            st.divider()
            preset_name_in = st.text_input("프리셋 이름으로 저장", placeholder="예) 자동차부품용", key="preset_name_in")
            if st.button("현재 설정 저장", key="save_preset", use_container_width=True):
                if preset_name_in.strip():
                    new_p = {
                        'name': preset_name_in.strip(),
                        'n': st.session_state['n'],
                        'include_incomplete': st.session_state['include_incomplete'],
                        'data_layout_mode': st.session_state.get('data_layout_mode', 'long'),
                        'subgroup_columns': st.session_state.get('subgroup_columns', []),
                        'subgroup_var_name': st.session_state.get('subgroup_var_name', '측정치'),
                        'measurement_date_column': st.session_state.get('measurement_date_column', ''),
                        'chart_usage_mode': st.session_state['chart_usage_mode'],
                        'baseline_subgroups': st.session_state['baseline_subgroups'],
                        'hist_bins': st.session_state['hist_bins'],
                        'palette': st.session_state['palette'],
                        'ksq_toggles': st.session_state['ksq_toggles'],
                        'show_zones': st.session_state['show_zones'],
                        'distribution_settings': st.session_state.get('distribution_settings', {}),
                    }
                    existing = [p for p in st.session_state.get('param_presets', []) if p['name'] != new_p['name']]
                    existing.append(new_p)
                    st.session_state['param_presets'] = existing
                    _save_config()
                    st.success(f"'{new_p['name']}' 저장됨")

            st.markdown("#### 데이터 전처리 설정")
            st.caption("데이터의 신뢰성을 높이기 위한 전처리 옵션입니다.")
            st.checkbox(
                "결측 포함 부분군 제외 (기본)",
                value=True, disabled=True,
                help="n>1은 결측 포함 부분군 전체를 제외하여 원래 묶음을 유지합니다. n=1은 유효 측정값을 사용합니다."
            )
            st.checkbox(
                "이상치 후보 검출 (검토 승인 후 제외)",
                key='preprocess_outliers',
                on_change=_save_config,
                help="통계적 후보를 표시합니다. 변수 상세에서 사유·확인자와 함께 승인한 행의 부분군만 제외하며 원자료는 보존합니다."
            )
            if st.session_state.get('preprocess_outliers'):
                st.radio(
                    "이상치 기준",
                    options=["iqr", "quantile"],
                    format_func=lambda key: "IQR" if key == "iqr" else "분위수 범위 (꼬리 0.1, Q = 3)",
                    horizontal=True,
                    key='outlier_method',
                    on_change=_save_config,
                    help=_OUTLIER_METHOD_HELP,
                )
            st.checkbox(
                "비정규 데이터 Box-Cox 변환",
                value=st.session_state.get('box_cox_transform', False),
                key='box_cox_transform',
                on_change=_save_config,
                help="Box-Cox 변환 후 능력을 추정합니다. 관리용 모드는 원본 기준구간에서 추정한 λ와 이동량을 감시 데이터에도 적용합니다."
            )

            st.markdown("#### KSQ 규칙 / 존 설정")
            ksq_options = [
                'T2: 중심선 한쪽 9점',
                'T3: 연속 증가/감소 6점',
                'T4: 상하 교대 14점',
                'T5: 3점중 2점 A존(2σ)',
                'T6: 5점중 4점 B존(1σ)',
                'T7: C존(±1σ) 내부 15점',
                'T8: C존(±1σ) 외부 8점',
            ]
            ksq_keys    = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
            ksq_default = [ksq_options[i] for i, k in enumerate(ksq_keys)
                           if st.session_state['ksq_toggles'].get(k, True)]
            ksq_sel = st.multiselect("KSQ 규칙 선택 (T1 자동 적용)", ksq_options, default=ksq_default)
            new_ksq = {'T1': True}
            for k, label in zip(ksq_keys, ksq_options):
                new_ksq[k] = label in ksq_sel
            st.session_state['ksq_toggles'] = new_ksq

            zone_options = ['±1σ (C)', '±2σ (B)', '±3σ (A)', '3σ 초과']
            zone_keys    = ['C', 'B', 'A', 'Z3']
            zone_default = [zone_options[i] for i, k in enumerate(zone_keys)
                            if st.session_state['show_zones'].get(k, True)]
            zone_sel = st.multiselect("시그마 존 표시", zone_options, default=zone_default, key="zone_multi")
            new_zones = {k: zone_options[i] in zone_sel for i, k in enumerate(zone_keys)}
            st.session_state['show_zones'] = new_zones

            _bm_on = bool(st.session_state.get('beginner_mode', False))
            with st.expander("📘 KSQ 규칙 가이드", expanded=_bm_on):
                if _bm_on:
                    st.markdown("""
    - **T1(항상 적용)**: 점 1개가 UCL/LCL 밖이면 이상 신호
    - **T2**: 점이 한쪽으로 9개 연속이면 중심이 치우친 신호
    - **T3**: 6개 연속 증가/감소면 공정이 서서히 이동 중
    - **T4**: 14개 상하 교대면 주기적 흔들림(교호 영향) 의심
    - **T5**: 3개 중 2개가 2σ 바깥이면 강한 치우침 신호
    - **T6**: 5개 중 4개가 1σ 바깥이면 지속적 편향 신호
    - **T7**: 15개가 중심 근처(±1σ)면 데이터 층화/해상도 점검
    - **T8**: 8개가 중심에서 멀면 혼합 공정/조건 차이 의심
    """)
                else:
                    st.markdown("""
    - **T1 (3σ 이탈)**: 특수원인 1차 트리거. Lot 격리 및 4M1E 변경점 우선 확인
    - **T2 (9 run one side)**: 평균 shift 가능성. 오프셋/셋업값/교정 상태 점검
    - **T3 (6 trend)**: 드리프트 가능성. 공구 마모, 온도, 배치 경과시간 점검
    - **T4 (14 alternation)**: 과제어/교대 영향 가능성. 자동보정 로직, 교대 인수인계 확인
    - **T5 (2 of 3 in Zone A)**: 단기 강편향 신호. 최근 조건 변경, 레시피/원재료 변경 추적
    - **T6 (4 of 5 in Zone B)**: 지속 편향 신호. 중심 재설정 및 조정 후 재발 여부 확인
    - **T7 (15 in Zone C)**: 분산 과소/층화 가능성. 측정 분해능, 반올림, 샘플링 방식 점검
    - **T8 (8 outside Zone C)**: 혼합군 가능성. 라인/금형/공급처/작업자 분리 분석 권장
    """)

        if not education_mode():
            with st.expander("이메일 알림"):
                st.caption("규격/관리한계 이탈 시 SMTP 메일 발송")
                smtp_host_sb = st.text_input("SMTP 호스트", value="smtp.gmail.com", key="sb_smtp_host")
                smtp_port_sb = st.number_input("포트", min_value=1, max_value=65535, value=465, key="sb_smtp_port")
                smtp_user_sb = st.text_input("발신자 이메일", key="sb_smtp_user")
                smtp_pass_sb = st.text_input("비밀번호 / 앱 비밀번호", type="password", key="sb_smtp_pass")
                to_addrs_sb  = st.text_area("수신자 (쉼표/줄바꿈 구분)", height=70, key="sb_to_addrs",
                                            on_change=_save_config)
                use_ssl_sb      = st.checkbox("SSL (포트 465)", value=True, key="sb_use_ssl")
                use_starttls_sb = st.checkbox("STARTTLS (포트 587)", value=False, key="sb_starttls")
                only_recent_sb  = st.checkbox("최근 점만 확인", value=True, key="sb_only_recent")

                send_email_btn = st.button("📨 이탈 알림 발송", use_container_width=True, key="sb_send_btn")
                test_email_btn = st.button("🧪 테스트 발송", use_container_width=True, key="sb_test_btn")

                if send_email_btn:
                    if st.session_state.get('analysis_done'):
                        to_list = split_emails(to_addrs_sb)
                        if not to_list:
                            st.error("유효한 수신자가 없습니다.")
                        elif not smtp_user_sb or not smtp_pass_sb:
                            st.error("이메일/비밀번호를 입력하세요.")
                        else:
                            cfg = {
                                'host': smtp_host_sb, 'port': smtp_port_sb,
                                'user': smtp_user_sb, 'password': smtp_pass_sb,
                                'from': smtp_user_sb,
                                'use_ssl': use_ssl_sb,
                                'use_starttls': use_starttls_sb and not use_ssl_sb,
                            }
                            with st.spinner("발송 중..."):
                                subject, body = _build_detail_email(
                                    st.session_state['results'], only_recent_sb,
                                    source_name=st.session_state.get('_loaded_fav_name', ''))
                                if subject is None:
                                    st.info("이탈 없음 — 이메일 발송하지 않음")
                                else:
                                    msg = smtp_send(
                                        smtp_host=cfg['host'], smtp_port=cfg['port'],
                                        smtp_user=cfg['user'], smtp_pass=cfg['password'],
                                        from_addr=cfg['from'], to_addrs=to_list,
                                        subject=subject, body=body,
                                        use_ssl=cfg['use_ssl'], use_starttls=cfg['use_starttls'],
                                    )
                                    if msg == "OK":
                                        st.success("발송 완료!")
                                        _push_email_history(subject, to_list, True)
                                    else:
                                        st.error(f"실패: {msg}")
                                        _push_email_history(subject, to_list, False)
                    else:
                        st.warning("먼저 분석을 실행하세요.")

                if test_email_btn:
                    test_to = to_addrs_sb.strip().split('\n')[0].split(',')[0].strip()
                    if smtp_user_sb and smtp_pass_sb and test_to:
                        with st.spinner("테스트 발송 중..."):
                            res = smtp_send(
                                smtp_host=smtp_host_sb, smtp_port=smtp_port_sb,
                                smtp_user=smtp_user_sb, smtp_pass=smtp_pass_sb,
                                from_addr=smtp_user_sb,
                                to_addrs=[test_to],
                                subject="[SPC 웹] 테스트 메일",
                                body="SPC 웹앱 SMTP 테스트 메일입니다.",
                                use_ssl=use_ssl_sb, use_starttls=use_starttls_sb and not use_ssl_sb,
                            )
                        if res == "OK":
                            st.success("테스트 성공!")
                            _push_email_history("[테스트]", [test_to], True)
                        else:
                            st.error(f"실패: {res}")
                    else:
                        st.warning("SMTP 설정과 수신자를 입력하세요.")

                # 발송 이력
                email_hist = st.session_state.get('email_history', [])
                if email_hist:
                    st.divider()
                    st.caption("📋 최근 발송 이력")
                    for eh in reversed(email_hist[-5:]):
                        icon = "✅" if eh['ok'] else "❌"
                        st.caption(f"{icon} {eh['time']} | {eh['subject']}")

            st.divider()

        # ── 용어 사전 ──
        with st.expander("📚 용어 사전"):
            st.markdown(_threshold_text("""
    | 용어 | 쉬운 설명 |
    |------|-----------|
    | **Cp** | 전체 산포 기준 공정능력. 적용 조건 충족 시 표시 |
    | **Cpk** | 공정능력 지수. 규격 대비 공정 위치와 퍼짐을 반영. ≥1.33 권장 |
    | **Pp** | 전체 산포 기준 공정성능 참고값. 중심 치우침은 반영하지 않음 |
    | **Ppk** | 전체 변동 기준 성능 지수. 분포 모델과 공정 조건을 함께 확인 |
    | **Cpm** | 목표치(T) 반영 공정능력 지수. 평균이 목표치에서 멀수록 낮아짐 |
    | **Cpmk** | 목표치(T) + 규격 한쪽 치우침을 함께 반영한 공정능력 지수 |
    | **Cw / Cwk** | 군내 변동 진단 지수. n=1은 MR̄/1.128 기반 앱 참고값, n≥2는 부분군 산포로 계산. 이 지수만으로 공정능력 충족을 판정하지 않음 |
    | **UCL/LCL** | 관리 상한/하한선 (평균 ± 3σ). 이 선을 벗어나면 이상 신호 |
    | **USL/LSL** | 규격 상한/하한선 (고객·도면 기준) |
    | **PPM** | 100만 개 생산 시 불량 예상 개수 |
    | **σ (시그마)** | 표준편차 — 데이터가 평균에서 얼마나 떨어져 있는지 |
    | **σ(군내)** | 단기 공정 변동 (같은 조건에서의 흔들림) |
    | **σ(전체)** | 장기 공정 변동 (시간에 따른 전체 흔들림) |
    | **군내/전체 지수 비율** | abs(Cwk/Ppk). 변동 차이 진단용이며 안정성 검정이 아님 |
    | **KSQ 규칙** | 관리도에서 이상 패턴을 찾는 8가지 통계 규칙 |
    | **해석용 관리도** | 현재 데이터로 시험 관리한계를 계산해 공정 안정 상태를 확인하는 단계 |
    | **관리용 관리도** | 안정 구간으로 한계선을 고정한 뒤 이후 데이터만 감시하는 운영 단계 |
    | **Xbar-R** | n=2~9일 때: 부분군 평균-범위 관리도 |
    | **Xbar-S** | n≥10일 때: 부분군 평균-표준편차 관리도 (자동 전환) |
    | **I-MR (X-MR)** | n=1일 때: 개별값-이동범위 관리도 |

    ### 지수 공식
    - `Cp = (USL - LSL) / (6s_overall), 적용 조건 충족 시`
    - `Cpk = min((USL - μ)/(3s_overall), (μ - LSL)/(3s_overall)), 적용 조건 충족 시`
    - `Pp = (USL - LSL) / (6σ_overall)`
    - `Ppk = min((USL - μ)/(3σ_overall), (μ - LSL)/(3σ_overall))`
    - `Cw = (USL - LSL)/(6σ_estimate), n=1 MR 참고 / n≥2 군내 진단`
    - `Cwk = min((USL - μ)/(3σ_estimate), (μ - LSL)/(3σ_estimate)), n=1 MR 참고 / n≥2 군내 진단`
    - `Cpm = (USL - LSL) / (6√(σ_overall² + (μ - T)²))`
    - `Cpmk = min((USL - μ)/(3√(σ_overall² + (μ - T)²)), (μ - LSL)/(3√(σ_overall² + (μ - T)²)))`

    ※ 이 앱의 Cpm/Cpmk는 요청사항에 맞춰 `σ_overall(전체 표준편차)` 기준입니다.
    """))

        # ── AI 설정 (Gemini / Groq / OpenRouter) ──
        if education_mode():
            st.caption("교육용 AI 해석은 관리자 인증 후 서버에 설정한 API 키로 사용합니다.")
        else:
            with st.expander("🤖 AI 설정 (Gemini / Groq / OpenRouter)"):
                st.caption("Gemini → Groq → OpenRouter 순서로 전환합니다. Windows에서는 API 키를 현재 사용자 계정으로 암호화해 저장합니다. 다른 OS에서는 세션 또는 환경변수를 사용합니다.")
                _g_key = st.text_input(
                    "Gemini API Key",
                    type="password",
                    help="Google AI Studio에서 발급받은 API 키를 입력하세요.",
                    on_change=_save_config,
                    key="gemini_api_key"
                )
                _groq_key = st.text_input(
                    "Groq API Key",
                    type="password",
                    help="https://console.groq.com/keys 에서 발급받은 API 키를 입력하세요.",
                    on_change=_save_config,
                    key="groq_api_key"
                )
                _or_key = st.text_input(
                    "OpenRouter API Key",
                    type="password",
                    help="https://openrouter.ai 에서 발급받은 API 키를 입력하세요.",
                    on_change=_save_config,
                    key="openrouter_api_key"
                )

                if _g_key:
                    st.success("Gemini API 키가 설정되었습니다.")
                if _groq_key:
                    st.success("Groq API 키가 설정되었습니다. Gemini 실패/한도초과 시 우선 전환됩니다.")
                if _or_key:
                    st.success("OpenRouter API 키가 설정되었습니다. Groq도 실패하면 자동 전환됩니다.")
                if not _g_key and not _groq_key and not _or_key:
                    st.info("API 키 미설정 시 앱 내부 요약 기반 해석이 제공됩니다.")

            st.divider()

    if _bm:
        beginner_inputs(_df_layout)
        if st.session_state.get('chart_usage_mode') in ('phase2', EXTENDED_MODE):
            _render_baseline_input()
    elif _df_layout is not None and st.session_state.get('data_layout_mode') == 'long':
        _choices = [c for c in _numeric_col_labels if c != st.session_state.get('measurement_date_column')]
        measurement_column_picker(_choices)

    _preflight_preview = _inspect_current_inputs()
    st.session_state["analysis_preview"] = _preflight_preview
    _render_preflight_check(_preflight_preview)
    st.divider()

    # ── 분석 실행 버튼 ──
    _chart_selection_error = chart_selection_error(st.session_state)
    if _chart_selection_error:
        st.warning(_chart_selection_error)
    run_btn = st.button("▶ 분석 시작", type="primary", use_container_width=True,
                        disabled=bool(_chart_selection_error))


# ─────────────────────────── 분석 실행 ───────────────────────────
_auto_triggered = st.session_state.pop('_trigger_analysis', False)
_watch_file_changed = st.session_state.pop('_watch_file_changed', False)
_refresh_normal_start = st.session_state.pop('_refresh_normal_start', False)
if run_btn or _auto_triggered or _watch_file_changed or _refresh_normal_start:
    if _chart_selection_error:
        st.error(_chart_selection_error)
    elif not st.session_state.get('data_bytes'):
        st.error("데이터 파일을 먼저 업로드하세요.")
    elif (
        st.session_state.get('data_layout_mode') == 'wide_subgroup_cols'
        and len(st.session_state.get('subgroup_columns', [])) < 2
    ):
        st.error("부분군 다열 모드에서는 측정 열을 2개 이상 선택하세요. (분석·관리도 → 분석 파라미터 → 측정열 자동 선택)")
    else:
        with st.spinner("분석 중..."):
            try:
                _manual_target_map_for_run = None
                if not st.session_state.get('beginner_mode') and st.session_state.get('manual_target_enabled', False):
                    if st.session_state.get('data_layout_mode') == 'wide_subgroup_cols':
                        _run_target_vars = [str(st.session_state.get('subgroup_var_name', '측정치')).strip() or "측정치"]
                    else:
                        _run_target_vars = [str(c) for c in _numeric_col_labels]
                    st.session_state['manual_target_map'] = _manual_target_map_from_editor(_run_target_vars)
                    _manual_target_map_for_run = st.session_state.get('manual_target_map', {})

                request = dict(
                    data_bytes=st.session_state['data_bytes'],
                    data_sheet=st.session_state.get('data_sheet'),
                    spec_bytes=st.session_state.get('spec_bytes'),
                    spec_sheet=st.session_state.get('spec_sheet'),
                    selected_columns=st.session_state.get('selected_columns'),
                    **analysis_options(st.session_state),
                )
                request['detail_level'] = 'full'
                st.session_state['_analysis_request'] = request
                progress = st.progress(0, text='데이터 읽는 중...')
                def update_progress(index, total, column):
                    progress.progress(index / max(total, 1), text=f'{index + 1}/{total} · {column} 분석 중')
                results, summary_df = ui_analysis(st.session_state,
                    **request, progress_callback=update_progress)
                progress.empty()
                if not results:
                    errors = summary_df.get('오류', pd.Series(dtype=str)).dropna().unique()
                    raise ValueError(" / ".join(errors[:3]) or "분석 가능한 측정 항목이 없습니다.")
                st.session_state['results'] = results
                st.session_state['summary_df'] = summary_df
                st.session_state['analysis_done'] = True
                st.session_state['analysis_meta'] = _current_analysis_meta(st.session_state.get("analysis_preview", {}))
                st.session_state['analysis_meta'].update(summary_df.attrs.get('audit', {}))
                if request['chart_usage_mode'] == EXTENDED_MODE and not _watch_file_changed:
                    st.session_state['_extended_result_view'] = 'chart'
                _push_analysis_history(results)
                _save_last_analysis_env()
                _save_config()
                st.success(f"분석 완료: 변수 {len(results)}개")

                # 자동 감시 트리거 + 이탈 발생 시 전체 즐겨찾기 분석 후 통합 이메일 발송
                if _watch_file_changed and not _refresh_normal_start and st.session_state.get('auto_watch_enabled'):
                    _has_violation = (
                        any(assess_process(it)['level'] != 'low' for it in results)
                        or
                        any(_item_band(it) in ('bad', 'warn') for it in results)
                        or any(it.get('spec_violation_count', 0) > 0 for it in results)
                    )
                    if _has_violation:
                        _user    = st.session_state.get('sb_smtp_user', '')
                        _pass    = st.session_state.get('sb_smtp_pass', '')
                        _to_list = split_emails(st.session_state.get('sb_to_addrs', ''))
                        if _user and _pass and _to_list:
                            with st.spinner("전체 즐겨찾기 분석 중..."):
                                all_fav_results = _run_all_favorites()
                            _fav_errs = st.session_state.pop('_fav_batch_errors', [])
                            if _fav_errs:
                                _preview = "; ".join(_fav_errs[:3])
                                if len(_fav_errs) > 3:
                                    _preview += f" 외 {len(_fav_errs) - 3}건"
                                st.warning(f"일부 즐겨찾기 분석 실패: {_preview}")
                            if all_fav_results:
                                _subj, _body = _build_all_favs_email(all_fav_results)
                            else:
                                _subj, _body = _build_detail_email(
                                    results,
                                    only_recent=st.session_state.get('sb_only_recent', True),
                                    source_name=st.session_state.get('_loaded_fav_name', ''),
                                )
                            if _subj:
                                _res = smtp_send(
                                    smtp_host=st.session_state.get('sb_smtp_host', 'smtp.gmail.com'),
                                    smtp_port=st.session_state.get('sb_smtp_port', 465),
                                    smtp_user=_user, smtp_pass=_pass,
                                    from_addr=_user, to_addrs=_to_list,
                                    subject=_subj, body=_body,
                                    use_ssl=st.session_state.get('sb_use_ssl', True),
                                    use_starttls=(st.session_state.get('sb_starttls', False)
                                                  and not st.session_state.get('sb_use_ssl', True)),
                                )
                                if _res == "OK":
                                    st.toast(f"전체 즐겨찾기 {len(all_fav_results)}개 분석 후 이메일 발송 완료", icon="📧")
                                else:
                                    st.warning(f"자동 이메일 발송 실패: {_res}")
            except Exception as e:
                st.error(f"분석 실패: {e}")
                st.session_state['analysis_done'] = False


# Both modes share complete statistics; upgrade results retained by older sessions.
if st.session_state.get('analysis_done'):
    _current_results = st.session_state.get('results', [])
    if any(it.get('detail_level') == 'core' or it.get('calculation_version') != CALCULATION_VERSION for it in _current_results):
        if not st.session_state.get('_analysis_request'):
            st.warning('계산 기준이 변경되었습니다. 파일을 확인하고 재분석해 주세요. 이전 결과는 표시하지 않습니다.')
            st.stop()
        with st.spinner('공정능력과 상세 통계 계산 중...'):
            _request = dict(st.session_state['_analysis_request'], detail_level='full')
            _full, _summary = ui_analysis(st.session_state, **_request)
            st.session_state['_analysis_request'] = _request
            st.session_state['results'], st.session_state['summary_df'] = _full, _summary
            st.session_state['analysis_meta'].update(_summary.attrs.get('audit', {}))

# ─────────────────────────── 메인 영역 ───────────────────────────
_selected_date_col = str(st.session_state.get('measurement_date_column', '') or '').strip()
if (
    st.session_state.get('analysis_done')
    and _selected_date_col
    and st.session_state.get('results')
    and not any((it.get('x_labels') or it.get('r_labels')) for it in st.session_state.get('results', []))
    and st.session_state.get('_date_label_retry_col') != _selected_date_col
):
    st.session_state['_date_label_retry_col'] = _selected_date_col
    st.session_state['_trigger_analysis'] = True
    st.rerun()

render_studio_header()

if not st.session_state['analysis_done'] or _chart_guide_slot.toggle("관리도 선택 가이드 열기", key="_show_chart_guide"):
    render_chart_guide(_chart_selection_error, frame=_df_layout)
    st.divider()

# ── 상태바 (분석 완료 시) ──
_extended_result = any(it.get('chart_usage_mode') == EXTENDED_MODE for it in st.session_state.get('results', []))
if not _extended_result and not st.session_state.get('beginner_mode') and st.session_state.get('analysis_done') and all(it.get('detail_level') != 'core' for it in st.session_state.get('results', [])):
    _r = st.session_state.get('results', [])
    _nb  = sum(1 for it in _r if _item_band(it) == 'bad')
    _nw  = sum(1 for it in _r if _item_band(it) == 'warn')
    _ns  = sum(it.get('spec_violation_count', 0) for it in _r)
    _hist = st.session_state.get('analysis_history', [])
    _last_t = _hist[-1]['time'] if _hist else "—"
    _src = st.session_state.get('_loaded_fav_name', '')
    _watch_on = st.session_state.get('auto_watch_enabled', False)
    _watch_fav = ""
    if _watch_on:
        _favs = st.session_state.get('favorites', [])
        _wi   = st.session_state.get('auto_watch_fav_idx', 0)
        _watch_fav = _favs[_wi]['name'] if _favs and _wi < len(_favs) else ""

    _badge_bad  = f'<span style="background:#b42318;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.85rem">불량 {_nb}개</span>' if _nb else ''
    _badge_warn = f'<span style="background:#92400e;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.85rem">주의 {_nw}개</span>' if _nw else ''
    _badge_spec = f'<span style="background:#9a3412;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.85rem">이탈 {_ns}건</span>' if _ns else ''
    _watch_checked = st.session_state.get('_watch_last_checked_at', '')
    _watch_changed = st.session_state.get('_watch_last_change_at', '')
    _watch_status = st.session_state.get('_watch_last_status', '')
    _watch_detail = f"{_watch_fav}"
    if _watch_checked:
        _watch_detail += f" · 확인 {_watch_checked}"
    if _watch_changed:
        _watch_detail += f" · 변경 {_watch_changed}"
    elif _watch_status:
        _watch_detail += f" · {_watch_status}"
    _watch_badge = f'<span style="background:#047857;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.85rem">🔄 감시중: {_watch_detail}</span>' if _watch_on else ''
    _ok_badge    = '<span style="background:#047857;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.85rem">✓ 관측 구간 양호</span>' if _r and all(assess_process(it)['level'] == 'low' for it in _r) else ''

    _src_txt = f" | {_src}" if _src else ""
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;'
        f'padding:6px 12px;border-radius:6px;background:rgba(128,128,128,0.08);margin-bottom:8px;font-size:0.85rem">'
        f'<span>🕐 {_last_t}{_src_txt}</span>'
        f'{_badge_bad}{_badge_warn}{_badge_spec}{_ok_badge}{_watch_badge}'
        f'</div>',
        unsafe_allow_html=True,
    )

if not st.session_state['analysis_done']:
    if st.session_state.get('chart_usage_mode') == EXTENDED_MODE:
        st.stop()
    if st.session_state.get('beginner_mode'):
        st.subheader('관리도와 공정능력을 간단하게 확인하세요')
        st.markdown('1. 왼쪽에서 측정 데이터를 올리고 필요하면 규격 파일을 선택하세요.\n'
                    '2. 측정 항목과 한 번에 함께 측정한 개수를 확인하세요.\n'
                    '3. **분석 시작**을 누르면 핵심 지수와 관리도를 확인할 수 있습니다.')
        st.caption('규격이 없으면 관리도만 확인할 수 있습니다. 상세 분석과 평가 기준 변경은 중급자 모드에서 제공합니다.')
        st.stop()
    # ── 온보딩 가이드 ──
    st.markdown("## 처음 사용하시나요? 4단계로 시작하세요")
    _main_preview = st.session_state.get("analysis_preview") or _inspect_current_inputs()
    st.markdown("### 분석 전 상태")
    _p1, _p2, _p3, _p4 = st.columns(4)
    _p1.metric("데이터", "업로드됨" if _main_preview.get("data_loaded") else "대기")
    _p2.metric("수치형 변수", f"{_main_preview.get('numeric_count', 0):,}개")
    _p3.metric("결측", f"{_main_preview.get('missing_total', 0):,}개")
    _match = _main_preview.get("match_rate")
    _p4.metric("규격 매칭", f"{_match * 100:.0f}%" if isinstance(_match, float) else ("규격 없음" if not _main_preview.get("spec_loaded") else "확인 필요"))
    if _main_preview.get("unmatched_specs"):
        st.warning("규격 파일에 있지만 데이터와 매칭되지 않은 항목: " + ", ".join(_main_preview["unmatched_specs"][:10]))
    if _main_preview.get("missing_spec_cols"):
        _miss = _main_preview["missing_spec_cols"]
        st.info("규격이 없는 데이터 변수: " + ", ".join(_miss[:10]) + (" ..." if len(_miss) > 10 else ""))
    for _msg in _main_preview.get("messages", [])[:3]:
        st.caption("• " + str(_msg))

    ob1, ob2, ob3, ob4 = st.columns(4)
    with ob1:
        st.markdown("""<div style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);
            border-radius:8px;padding:14px;text-align:center;height:150px">
            <div style="font-size:2rem">📂</div>
            <div style="font-weight:700;margin:6px 0">1단계</div>
            <div style="font-size:0.88rem">왼쪽 사이드바에서<br>Excel/CSV 파일 업로드</div>
        </div>""", unsafe_allow_html=True)
    with ob2:
        st.markdown("""<div style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);
            border-radius:8px;padding:14px;text-align:center;height:150px">
            <div style="font-size:2rem">▶️</div>
            <div style="font-weight:700;margin:6px 0">2단계</div>
            <div style="font-size:0.88rem">사이드바 하단<br>▶ 분석 시작 클릭</div>
        </div>""", unsafe_allow_html=True)
    with ob3:
        st.markdown("""<div style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);
            border-radius:8px;padding:14px;text-align:center;height:150px">
            <div style="font-size:2rem">🚦</div>
            <div style="font-weight:700;margin:6px 0">3단계</div>
            <div style="font-size:0.88rem">대시보드에서<br>🔴🟡🟢 신호등 확인</div>
        </div>""", unsafe_allow_html=True)
    with ob4:
        st.markdown("""<div style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.35);
            border-radius:8px;padding:14px;text-align:center;height:150px">
            <div style="font-size:2rem">📧</div>
            <div style="font-weight:700;margin:6px 0">4단계</div>
            <div style="font-size:0.88rem">🔴 빨강 항목 클릭 →<br>관리도 확인 후 알림 발송</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(_threshold_text("""<div style="background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.3);
        border-radius:8px;padding:12px 18px;margin:16px 0;font-size:0.9rem">
        💡 <b>능력 지수 참고 기준</b> &nbsp;|&nbsp;
        🔴 <b>능력 부족</b>: Cpk &lt; 1.00 — 개선 필요 &nbsp;|&nbsp;
        🟡 <b>주의</b>: Cpk 1.00~1.33 — 모니터링 강화 &nbsp;|&nbsp;
        🟢 <b>능력 기준 충족</b>: Cpk ≥ 1.33<br>
        종합 판정은 규격이탈·공정 안정성·분석 조건을 함께 확인하세요.
    </div>"""), unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("""
### 규격 파일 형식 (선택)
규격 파일은 다음 형식의 Excel/CSV를 사용하세요:

| 변수 (또는 Column/Item) | USL | LSL | Target(선택) |
|---|---|---|---|
| 항목1 | 10.5 | 9.5 | 10.0 |
| 항목2 | 200 | 180 | 190 |

헤더 이름은 자동으로 인식됩니다 (column/item/변수/항목 등).
목표치(Target)를 비우면 USL/LSL의 중간값을 자동 사용합니다.
필요 시 `분석·관리도 → 분석 파라미터 → 수동 목표치 사용`에서 변수별 목표치를 직접 입력해 덮어쓸 수 있습니다.
""")

    st.markdown("---")
    st.markdown(_threshold_text("""
### 주요 기능
- **공정능력 지수**: Cp, Cpk, Pp, Ppk, Cpm, Cpmk (단측 규격 지원, 목표치 반영), Cw·Cwk (n=1 MR 기반 참고 / n≥2 군내 진단용)
- **PPM 계산**: Within/Overall/실측 불량률
- **KSQ 규칙 검출**: KS Q ISO 7870-2 T1~T8 규칙 위반점 표시
- **정규성 검정**: Shapiro-Wilk (n≤50) / Anderson-Darling (n>50)
- **Excel 내보내기**: 선별 요약 + 변수별 상세 (Cpk/Cpm/Cpmk < 1.33 분홍 강조)
- **PDF 내보내기**: 전체 관리도/히스토그램/QQ 플롯
- **HTML 내보내기**: 공유용 단일 리포트(요약표 + 변수별 요약 + 차트)
- **이메일 알림**: SMTP SSL/STARTTLS 지원
"""))
    st.stop()


# ─────────────────────────── 결과 표시 ───────────────────────────
results    = with_extended_results(st.session_state['results'])
summary_df = st.session_state['summary_df']
palette    = st.session_state['palette']

if not results:
    st.warning("분석 결과가 없습니다. 데이터를 확인하세요.")
    if summary_df is not None and '오류' in summary_df.columns:
        st.dataframe(summary_df[['변수', '오류']], hide_index=True, use_container_width=True)
    st.stop()

if summary_df is not None and '오류' in summary_df.columns:
    calculation_errors = summary_df[summary_df['오류'].notna()]
    if not calculation_errors.empty:
        st.warning(f"{len(calculation_errors)}개 변수는 계산하지 못했습니다. 아래 사유를 확인하세요.")
        st.dataframe(calculation_errors[['변수', '오류']], hide_index=True, use_container_width=True)

if _extended_result:
    beginner = st.session_state.get('beginner_mode')
    view = st.radio("확장 슈하르트 결과 보기", ['chart', 'reports'], horizontal=True,
                    format_func=lambda v: EXTENDED_TITLE if v == 'chart' else ("공정능력" if beginner else "공정능력·보고서"),
                    key='_extended_result_view', label_visibility='collapsed')
    if view == 'chart':
        item, _ = _select_detail_item(results)
        render_extended(item, results, palette)
        st.stop()
    st.caption("공정능력 지수는 공정능력 분석 기준으로 표시합니다. 확장 X̄와 S의 한계와 판정은 확장 관리도와 상세 통계에서 확인하세요.")
    if beginner:
        quick_results(results, render_chart=None)
        st.stop()

if st.session_state.get('beginner_mode'):
    def render_beginner_chart(item):
        return _render_control_chart_scrollable(
            item, palette, st.session_state['ksq_toggles'], st.session_state['show_zones'], view_key='beginner')
    quick_results(results, render_chart=render_beginner_chart)
    render_ocap_exercise(results)
    st.stop()


render_ocap_exercise(results)
col_names  = [it['column'] for it in results]
n_cols     = len(results)
control_phase_global = str(results[0].get('control_phase', 'phase1')) if results else 'phase1'
n_cpk_bad  = sum(1 for it in results if _item_band(it) == 'bad')
n_cpk_warn = sum(1 for it in results if _item_band(it) == 'warn')
n_spec_vio = sum(it.get('spec_violation_count', 0) for it in results)
n_ctrl_vio = sum(_get_ctrl_total(it) for it in results)
ctrl_metric_label = "관리한계 이탈 (감시구간)" if control_phase_global == 'phase2' else "관리한계 이탈 (누적)"
ctrl_metric_help = (
    "기준구간으로 고정한 관리한계를 기준으로 감시구간에서 발생한 이상 신호의 총 횟수입니다."
    if control_phase_global == 'phase2'
    else "관리 상한(UCL) 또는 하한(LCL)을 벗어난 이상 신호의 총 누적 횟수입니다."
)

# 상단 메트릭
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("분석 변수 수", f"{n_cols}개",
          help="분석한 측정 항목의 총 개수입니다.")
_auto_summary = any(it.get('capability_criteria', {}).get('mode') == 'aiag_vda' for it in results)
_adjusted_cpk = sum(it.get('capability_criteria', {}).get('indices', {}).get('cpk', {}).get('status') == 'met' and len(it['series']) < 125 for it in results)
m2.metric('Cpk 조정 목표값 충족' if _auto_summary else _threshold_text("Cpk < 1.33 (주의)"), f"{_adjusted_cpk if _auto_summary else n_cpk_warn}개" if results[0].get('detail_level') != 'core' else '미계산',
          delta=f"-{n_cpk_warn}" if n_cpk_warn else None, delta_color="inverse",
          help='N<125에서 첨부 식으로 계산한 조정 목표값을 충족한 항목 수입니다.' if _auto_summary else _threshold_text("공정능력 지수(Cpk)가 1.33 미만인 항목 수. 지속적인 모니터링이 필요합니다."))
m3.metric(_threshold_text("Cpk < 1.00 (불량)"), f"{n_cpk_bad}개" if results[0].get('detail_level') != 'core' else '미계산',
          delta=f"-{n_cpk_bad}" if n_cpk_bad else None, delta_color="inverse",
          help=_threshold_text("공정능력 지수(Cpk)가 1.00 미만인 항목 수. 규격 이탈이 발생할 수 있어 즉시 조치가 필요합니다."))
m4.metric("규격 이탈 (누적)", f"{n_spec_vio}건",
          help="고객·도면 규격(USL/LSL)을 벗어난 데이터의 총 누적 건수입니다.")
if _extended_result:
    extended_monitors = [monitor_signals(it["extended_shewhart"]) for it in results if it.get("extended_shewhart")]
    if any(not monitor.empty for monitor in extended_monitors):
        n_extended = sum(int(monitor[["확장 X̄", "S"]].sum().sum()) for monitor in extended_monitors)
        extended_total = f"{n_extended:,}건"
    else:
        extended_total = "감시 데이터 없음" if extended_monitors else "미계산"
    m5.metric("확장 X̄·S 이탈 (감시구간)", extended_total,
              help="확장 관리도 설정을 완료한 변수의 감시구간에서 확장 X̄와 S의 한계 이탈을 각각 집계해 더한 값입니다.")
else:
    m5.metric(ctrl_metric_label, f"{n_ctrl_vio}건", help=ctrl_metric_help)
_render_process_info_summary(st.session_state.get('analysis_meta', {}))
st.markdown("---")

# ── 초보자 모드 배너 ──
st.caption(WITHIN_CAPABILITY_NOTICE)
st.caption(f"Cp·Cpk 적용 조건 미충족/계산불가: {sum(it.get('capability_status') != '적용 가능' for it in results)}개 · 기준 충족/미달 집계에서 제외")
if st.session_state.get('beginner_mode'):
    st.info(
        _threshold_text("🔰 **초보자 모드 활성화 중** — 지표 이름이 쉬운 표현으로 표시되고, "
        "차트 읽는 법과 권장 행동 가이드가 자동으로 펼쳐집니다.  \n"
        "능력 지수 참고 기준: 🔴 **능력 부족** Cpk < 1.00 개선 필요 &nbsp;|&nbsp; "
        "🟡 **주의** Cpk 1.00~1.33 모니터링 &nbsp;|&nbsp; "
        "🟢 **능력 기준 충족** Cpk ≥ 1.33. "
        "종합 판정은 규격이탈·공정 안정성·분석 조건을 함께 확인하세요.")
    )

if control_phase_global == 'phase2' and not _extended_result:
    st.info(
        "📌 **관리용(Phase 2) 모드** — 초기 기준구간으로 관리한계를 고정했고, "
        "상단 관리이탈 집계와 상세 경고는 감시구간 기준으로 표시됩니다."
    )

# ── 행 하이라이트 함수 ──
def _highlight_row(df_row):
    item = next((r for r in results if str(r['column']) == str(df_row.get('변수', ''))), None)
    metric_columns = {name: name.lower() for name in ('Cp', 'Cpk', 'Pp', 'Ppk', 'Cpm', 'Cpmk')}
    if '폭 지수' in df_row.index:
        pair = screening_index_pair(item)
        metric_columns.update({'폭 지수': pair['width_metric'], '치우침 반영 지수': pair['shifted_metric']})
    # Each index keeps its own existing decision; colors never copy the worst index.
    styles = [''] * len(df_row)
    for column, metric in metric_columns.items():
        if column in df_row.index:
            if column == '치우침 반영 지수':
                state = screening_index_band(item)
            else:
                state = _item_band(item, metric) if item is not None and metric else 'na'
            styles[df_row.index.get_loc(column)] = STATUS_CELL_STYLES.get(state, STATUS_CELL_STYLES['na'])
    for cn in ('규격이탈', '관리이탈', 'X관리이탈', 'R관리이탈', '최근규격이탈', '최근관리이탈'):
        if cn in df_row.index:
            try:
                if int(df_row[cn]) > 0:
                    styles[df_row.index.get_loc(cn)] = STATUS_CELL_STYLES['bad']
            except Exception: pass
    return styles

# ── 차트 확대 다이얼로그 ──
@st.dialog("차트 확대 보기", width="large")
def _show_chart_modal(it, chart_type, range_mode='spec'):
    if chart_type == "overview":
        fig = build_overview_2x2(it, palette=palette, hist_bins=st.session_state['hist_bins'],
                                  ksq_toggles=st.session_state['ksq_toggles'], show_zones=st.session_state['show_zones'])
    elif chart_type == "control":
        _render_control_chart_scrollable(
            it,
            palette=palette,
            ksq_toggles=st.session_state['ksq_toggles'],
            show_zones=st.session_state['show_zones'],
            display_mode="detail",
            view_key="modal",
        )
        return
    elif chart_type == "hist":
        fig = build_histogram(it, palette=palette, bins=st.session_state['hist_bins'], range_mode=range_mode)
    else:
        fig = build_qq_plot(it, palette=palette)
    _fig_to_st(fig, vector=chart_type in ('hist', 'qq'))

# ═══════════════════════════════════════════════════════
# 결과 탭 — 선택한 화면만 계산
_result_labels = ["📉 관리도", "📊 대시보드", "📋 선별 요약", "📈 안정성", "🔗 상관분석",
                  "📈 히스토그램 / Q-Q", "📊 상세 통계", "💬 AI 챗봇", "💾 내보내기"]
tab_ctrl, tab_dash, tab_summ, tab_stab, tab_corr, tab_dist, tab_stats, tab_chat, tab_export = (
    (_ResultPanel(False), *_result_tabs(_result_labels[1:])) if _extended_result else _result_tabs(_result_labels)
)

with tab_dist:
    if tab_dist.open:
        st.subheader('히스토그램 / Q-Q 플롯')
        it, _vi = _select_detail_item(results)
        _render_distribution_detail(it, _vi, palette)

with tab_stats:
    if tab_stats.open:
        st.subheader('상세 통계')
        it, _vi = _select_detail_item(results)
        _render_statistics_detail(it)

# ══════════════════════════════════════════════
# TAB: 상관분석
# ══════════════════════════════════════════════
with tab_corr:
    if tab_corr.open:
        st.subheader("다변량 상관관계 분석")
        st.caption("여러 변수를 동시에 선택하여 항목 간의 연관성(피어슨 상관계수)을 분석합니다.")

        if st.session_state.get('data_bytes'):
            df_full = load_data_df(io.BytesIO(st.session_state['data_bytes']), 
                                   sheet_name=st.session_state.get('data_sheet'))
            num_cols = [c for c in df_full.columns if pd.api.types.is_numeric_dtype(df_full[c])]

            if len(num_cols) < 2:
                st.warning("상관 분석을 위해 최소 2개 이상의 수치형 변수가 필요합니다.")
            else:
                valid_corr_names = {str(c) for c in num_cols}
                if "corr_multiselect" in st.session_state:
                    cleaned_corr_cols = [
                        c for c in st.session_state.get("corr_multiselect", [])
                        if str(c) in valid_corr_names
                    ]
                    if len(cleaned_corr_cols) != len(st.session_state.get("corr_multiselect", [])):
                        st.session_state["corr_multiselect"] = cleaned_corr_cols

                auto_corr_cols, auto_corr_labels = _auto_select_corr_columns(
                    st.session_state.get("results", []),
                    num_cols,
                    limit=10,
                )
                corr_sig = tuple(str(c) for c in auto_corr_cols)
                prev_corr_sig = st.session_state.get("_corr_auto_sig")
                if (
                    auto_corr_cols
                    and (
                        "corr_multiselect" not in st.session_state
                        or prev_corr_sig != corr_sig
                        or not st.session_state.get("corr_multiselect")
                    )
                ):
                    st.session_state["corr_multiselect"] = auto_corr_cols
                    st.session_state["_corr_auto_sig"] = corr_sig

                if auto_corr_labels:
                    st.caption("자동 선택: 공정상태 위험 항목 우선 - " + ", ".join(auto_corr_labels[:10]))

                corr_select_kwargs = {}
                if "corr_multiselect" not in st.session_state:
                    corr_select_kwargs["default"] = auto_corr_cols or num_cols[:min(5, len(num_cols))]
                sel_corr_cols = st.multiselect(
                    "분석 대상 변수 선택 (최대 10개 권장)",
                    options=num_cols,
                    key="corr_multiselect",
                    **corr_select_kwargs,
                )

                if len(sel_corr_cols) >= 2:
                    c1, c2 = st.columns([1.3, 1])
                    with c1:
                        st.markdown("**1. 상관관계 히트맵**")
                        with st.spinner("히트맵 생성 중..."):
                            fig_corr = build_correlation_heatmap(df_full, sel_corr_cols, palette=palette)
                            _fig_to_st(fig_corr, vector=True)
                    with c2:
                        st.markdown("**2. 상관계수 표 (Pearson)**")
                        st.dataframe(correlation_matrix(df_full, sel_corr_cols).style
                                     .background_gradient(cmap='RdBu_r', axis=None, vmin=-1, vmax=1)
                                     .format('{:.3f}', na_rep='—'),
                                     use_container_width=True)
                        st.caption('색상 범위는 −1~+1로 고정합니다. 히트맵과 표는 선택 변수의 측정값이 모두 있는 행을 공통 사용하며, 결측·상수 열로 계산할 수 없는 값은 —로 표시합니다.')

                    st.divider()
                    st.markdown("**3. 다변량 산점도 및 분포 행렬**")
                    if st.checkbox("산점도 행렬 보기", key='show_scatter_matrix'):
                        _scatter_scope = st.radio('산점도 표시 범위', ['최대 5,000행', '전체 행'], horizontal=True)
                        _scatter_data = df_full
                        if _scatter_scope == '최대 5,000행' and len(df_full) > 5000:
                            _scatter_data = df_full.sample(n=5000, random_state=20260927)
                            st.caption('산점도만 5,000행을 추출해 표시합니다. 위의 상관계수와 관리도 판정은 전체 데이터를 사용합니다.')
                        with st.spinner("산점도 행렬 생성 중..."):
                            fig_matrix = build_scatter_matrix(_scatter_data, sel_corr_cols, palette=palette)
                            _fig_to_st(fig_matrix)
                else:
                    st.info("왼쪽에서 변수를 2개 이상 선택해 주세요.")
        else:
            st.info("데이터 파일 업로드 후 분석 가능합니다.")

    # ══════════════════════════════════════════════
    # TAB: AI 챗봇
    # ══════════════════════════════════════════════
with tab_chat:
    if tab_chat.open and not ai_available():
        st.info("AI 챗봇은 관리자 인증 후 사용할 수 있습니다.")
    if tab_chat.open and ai_available():
        st.subheader("💬 SPC 데이터 전문 챗봇")
        st.caption("업로드된 전체 분석 결과에 대해 궁금한 점을 물어보세요.")
        _cu = st.session_state.get("chat_usage", _new_chat_usage())

        cu1, cu2, cu3, cu4 = st.columns(4)
        cu1.metric("요청 수", f"{int(_cu.get('requests', 0))}회")
        cu2.metric("입력 토큰(누적)", f"{int(_cu.get('input_tokens', 0)):,}")
        cu3.metric("출력 토큰(누적)", f"{int(_cu.get('output_tokens', 0)):,}")
        cu4.metric("총 토큰(누적)", f"{int(_cu.get('total_tokens', 0)):,}")

        _model_name = str(_cu.get("model_name", "") or "-")
        _last_in = int(_cu.get("last_input_tokens", 0) or 0)
        _last_out = int(_cu.get("last_output_tokens", 0) or 0)
        _last_total = int(_cu.get("last_total_tokens", 0) or 0)
        _token_source = str(_cu.get("token_source", "") or "estimate")
        st.caption(
            f"마지막 모델: `{_model_name}` | 최근 1회 토큰: 입력 {_last_in:,}, 출력 {_last_out:,}, 합계 {_last_total:,} ({_token_source})"
        )

        _in_limit = int(_cu.get("model_input_limit", 0) or 0)
        if _in_limit > 0:
            _remain_ctx = max(_in_limit - _last_in, 0)
            lc1, lc2 = st.columns(2)
            lc1.metric("모델 1회 입력 한도", f"{_in_limit:,}")
            lc2.metric("최근 요청 대비 입력 여유", f"{_remain_ctx:,}")

        st.caption("참고: Google API에서 무료 한도의 실시간 잔여량은 직접 제공되지 않아, 위 값은 세션 기준 사용량/추정치입니다.")
        _last_err = str(_cu.get("last_error", "") or "").strip()
        if _last_err:
            st.caption(f"최근 API 오류: {_last_err}")

        # 대화 이력 초기화 버튼
        if st.button("🔄 대화 초기화", key="clear_chat_btn"):
            st.session_state['chat_history'] = []
            st.session_state['chat_usage'] = _new_chat_usage()
            st.rerun()

        # 채팅 메시지 출력
        for msg in st.session_state['chat_history']:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        # 채팅 입력
        if prompt := st.chat_input("질문을 입력하세요 (예: 가장 불량이 심한 변수는 뭐야?"):
            # 1. 사용자 메시지 추가
            st.session_state['chat_history'].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # 2. AI 응답 생성
            with st.chat_message("assistant"):
                with st.spinner("생각 중..."):
                    # 현재 분석 요약 데이터를 JSON string으로 변환해 컨텍스트로 전달
                    if summary_df is not None:
                        summary_json = summary_df.to_json(orient='records', force_ascii=False)
                    else:
                        summary_json = "현재 분석된 데이터 요약 정보가 없습니다."

                    response = chat_with_spc_data(
                        query=prompt,
                        history=st.session_state['chat_history'][:-1], # 현재 질문 제외한 히스토리
                        full_summary=summary_json
                    )
                    st.markdown(response)
                    st.session_state['chat_history'].append({"role": "assistant", "content": response})

    # ══════════════════════════════════════════════
    # TAB: 대시보드
    # ══════════════════════════════════════════════
with tab_dash:
    if tab_dash.open:
        st.subheader("능력·성능 대시보드")
        from spc_dashboard import render_dashboard
        render_dashboard(results, palette=palette)

        # 분석 이력
        hist_list = st.session_state.get('analysis_history', [])
        if len(hist_list) > 1:
            st.markdown("**분석 이력 (최근 5회)**")
            _df_hist = pd.DataFrame([
                {'분석시각': h['time'], '출처': h.get('source',''), '변수수': h['n_vars'],
                 '불량': h['n_bad'], '주의': h['n_warn'], '규격이탈': h['n_spec']}
                for h in reversed(hist_list)
            ])
            st.dataframe(_df_hist, use_container_width=True, hide_index=True)

            # Cpk 추이 비교 (공통 변수)
            if (len(hist_list) >= 2 and all(h.get('calculation_version') == CALCULATION_VERSION for h in hist_list[-2:])
                    and hist_list[-2].get('model_signature') == hist_list[-1].get('model_signature')):
                st.markdown("**Cpk 변화 추이 (현재 vs 이전)**")
                prev = hist_list[-2]['cpk_list']
                curr = hist_list[-1]['cpk_list']
                common = [c for c in curr if c in prev]
                if common:
                    trend_rows = []
                    for c in common:
                        pv, cv = prev[c], curr[c]
                        diff = (cv - pv) if np.isfinite(cv) and np.isfinite(pv) else float('nan')
                        trend_rows.append({'변수': c,
                                           '이전 Cpk': f"{pv:.3f}" if np.isfinite(pv) else "—",
                                           '현재 Cpk': f"{cv:.3f}" if np.isfinite(cv) else "—",
                                           '변화': f"{diff:+.3f}" if np.isfinite(diff) else "—"})
                    st.dataframe(pd.DataFrame(trend_rows), use_container_width=True, hide_index=True)
            elif len(hist_list) >= 2:
                st.caption('계산 버전·분포·계산 방법이 다른 이력은 Cpk를 직접 비교하지 않습니다. 같은 기준으로 재분석해 주세요.')

    # ══════════════════════════════════════════════
    # TAB: 선별 요약
    # ══════════════════════════════════════════════
with tab_summ:
    if tab_summ.open:
        st.subheader("선별 요약 — 전체 변수")

        if st.session_state.get('beginner_mode'):
            st.info(
                "💡 **이 표 보는 법** — **판정** 컬럼을 먼저 확인하세요.  \n"
                "🔴 빨강 행은 즉시 조치가 필요합니다. 행을 클릭하면 해당 변수의 관리도로 이동합니다."
            )

        # 대시보드 신호등 버튼에서 넘어온 경우 안내 배너
        _tl_hint = st.session_state.pop('_tl_nav_hint', None)
        if _tl_hint:
            st.success(f"**{_tl_hint}** 항목이 필터링되었습니다. 아래 표에서 확인하세요.")

        if summary_df is not None and not summary_df.empty:
            # 필터 버튼
            fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
            _flt = st.session_state.get('summary_filter', 'all')
            if fc1.button("전체 보기",        type="primary" if _flt=='all'  else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'all';  st.rerun()
            if fc2.button("표시 지표 미달", type="primary" if _flt=='bad'  else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'bad';  st.rerun()
            if fc3.button("주의 이상",        type="primary" if _flt=='warn' else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'warn'; st.rerun()
            if fc4.button("규격 이탈",        type="primary" if _flt=='spec' else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'spec'; st.rerun()
            if fc5.button("관리 이탈",        type="primary" if _flt=='ctrl' else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'ctrl'; st.rerun()
            if fc6.button("표시 지표 기준 충족", type="primary" if _flt=='good' else "secondary", use_container_width=True): st.session_state['summary_filter'] = 'good'; st.rerun()

            # 검색
            search_q = st.text_input("변수명 검색", placeholder="변수명 입력...", key="summ_search", label_visibility="collapsed")

            # 필터링
            _flt = st.session_state.get('summary_filter', 'all')
            show_results = results
            if _flt == 'bad':
                show_results = [it for it in results if screening_index_band(it) == 'bad']
            elif _flt == 'warn':
                show_results = [it for it in results if screening_index_band(it) in ('bad', 'warn')]
            elif _flt == 'good':
                show_results = [it for it in results if screening_index_band(it) == 'good']
            elif _flt == 'spec':
                show_results = [it for it in results if it.get('spec_violation_count', 0) > 0]
            elif _flt == 'ctrl':
                show_results = [it for it in results if _get_ctrl_total(it) > 0]
            if search_q.strip():
                show_results = [it for it in show_results if search_q.strip().lower() in str(it['column']).lower()]

            show_cols = [str(it['column']) for it in show_results]
            _df_show = summary_df[summary_df['변수'].astype(str).isin(show_cols)] if '변수' in summary_df.columns else summary_df.iloc[[i for i, it in enumerate(results) if it in show_results]]

            # 표시 지수(Cpk 또는 Ppk) 기준 오름차순 정렬
            if '변수' in _df_show.columns:
                displayed = {str(it['column']): it.get(screening_index_pair(it)['shifted_metric'], np.nan)
                             for it in show_results}
                _df_show = _df_show.sort_values('변수', key=lambda col: col.astype(str).map(displayed),
                                                kind='stable')

            _df_show = _summary_visible_df(_df_show)
            _summary_items = {str(it['column']): it for it in results}
            _row_items = [_summary_items.get(str(name)) for name in _df_show['변수']]
            _pairs = [screening_index_pair(it) for it in _row_items]
            for _column, _key in (('평가 상태', 'state'), ('폭 지수', 'width'), ('치우침 반영 지수', 'shifted')):
                _df_show[_column] = [pair[_key] for pair in _pairs]
            _df_show['판정'] = [assess_process(it)['text'] if it is not None else '계산 불가' for it in _row_items]
            _df_show['관리이탈'] = [_get_ctrl_total(it) if it is not None else None for it in _row_items]
            for _metric in ('Cw', 'Cwk'):
                if _metric not in _df_show.columns:
                    _df_show[_metric] = [fmt_value(it.get(_metric.lower(), np.nan)) if it is not None else '—' for it in _row_items]
            _summary_view = st.radio('표시 항목', list(SUMMARY_VIEWS), horizontal=True, key='summary_view')
            _df_table = _summary_view_df(_df_show, _summary_view)
            _summary_config = {
                '변수': st.column_config.Column('변수', pinned=True, width='small'),
                '판정': st.column_config.Column('판정', pinned=True, width='medium', help='규격 이탈, 관리 상태와 분석 조건을 함께 확인한 결과입니다.'),
                '평가 상태': st.column_config.Column('평가 상태', pinned=True, width='medium', help='능력 적용 가능이면 Cp·Cpk, 그 외에는 Pp·Ppk를 참고값으로 표시합니다. 관리 상태와 분포 조건을 함께 확인합니다.'),
                '폭 지수': st.column_config.Column('폭 지수', help='적용 조건 충족 시 Cp, 그 외에는 Pp. 양측 규격이 필요합니다.'),
                '치우침 반영 지수': st.column_config.Column('치우침 반영 지수', help='적용 조건 충족 시 Cpk, 그 외에는 Ppk. 셀에 실제 지표명을 함께 표시합니다.'),
                'Cwk': st.column_config.Column('Cwk', help='군내산포 고려. n=1은 MR 기반 참고값입니다.'),
                'Cw': st.column_config.Column('Cw', help='군내산포 고려. n=1은 MR 기반 참고값입니다.'),
                '관리이탈': st.column_config.Column('관리이탈', help='X·산포 관리도 이탈 건수의 합계. 관리용은 감시 구간 기준입니다.'),
            }
            # Reset selection when the visible row set/order or view changes.
            _summary_table_key = f"summary_table_{_summary_view}_{hash(tuple(_df_show['변수'].astype(str)))}"
            _row_detail = "상세 정보" if _extended_result else "상세 정보와 관리도"
            st.caption(f"표시: {len(_df_table)}개 / 전체 {len(summary_df)}개 · {_summary_view} · 행을 선택하면 {_row_detail}가 표시됩니다.")
            try:
                styled = _df_table.style.apply(_highlight_row, axis=1)
                _summ_event = st.dataframe(styled, use_container_width=True, height=420,
                                           hide_index=True, column_config=_summary_config, key=_summary_table_key,
                                           on_select="rerun", selection_mode="single-row")
            except Exception:
                _summ_event = st.dataframe(_df_table, use_container_width=True, height=420,
                                           hide_index=True, column_config=_summary_config, key=_summary_table_key,
                                           on_select="rerun", selection_mode="single-row")

            # 행 클릭 → 관리도 표시
            _sel_rows = _summ_event.selection.rows if hasattr(_summ_event, 'selection') else []
            if _sel_rows:
                try:
                    _sel_var_name = _df_show.iloc[_sel_rows[0]]['변수']
                    _sel_it = next((x for x in results if str(x['column']) == str(_sel_var_name)), None)
                    if _sel_it:
                        with st.expander(f"선택 항목 상세 — {_sel_var_name}"):
                            _selected_detail = _df_show.iloc[_sel_rows[0]].rename_axis('항목').reset_index(name='값')
                            _selected_detail['값'] = _selected_detail['값'].map(str)
                            st.dataframe(_selected_detail, hide_index=True, use_container_width=True)
                        if not _extended_result:
                            st.markdown(f"---\n#### 📉 관리도 — **{_sel_var_name}**")
                            _m1, _m2, _m3, _m4, _m5, _m6 = st.columns(6)
                            _m1.metric("Cpk", fmt_value(_sel_it['cpk']))
                            _m2.metric("Ppk", fmt_value(_sel_it['ppk']))
                            _m3.metric("Cpm", fmt_value(_sel_it.get('cpm', np.nan)))
                            _m4.metric("Cpmk", fmt_value(_sel_it.get('cpmk', np.nan)))
                            _m5.metric("규격 이탈", f"{_sel_it['spec_violation_count']}건")
                            _m6.metric("관리 이탈", f"{_get_ctrl_total(_sel_it)}건")
                            _render_date_label_status(_sel_it)
                            _render_control_chart_scrollable(
                                _sel_it,
                                view_key="summary",
                                palette=palette,
                                ksq_toggles=st.session_state['ksq_toggles'],
                                show_zones=st.session_state['show_zones'],
                            )
                            if st.button("🔍 확대 보기", key="summ_zoom_ctrl"):
                                _show_chart_modal(_sel_it, "control")
                except Exception:
                    pass

            try:
                excel_bytes = export_summary_excel(results, summary_df, analysis_meta=st.session_state.get('analysis_meta', {}))
                st.download_button("📥 Excel 다운로드 (선별 요약)", data=excel_bytes,
                                   file_name="spc_선별요약.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception as e:
                st.warning(f"Excel 생성 실패: {e}")
        else:
            st.info("분석 결과가 없습니다.")

    # ══════════════════════════════════════════════
    # TAB: 안정성 산점도
    # ══════════════════════════════════════════════
with tab_stab:
    if tab_stab.open:
        st.subheader("공정 능력 · 변동 평가")
        st.caption("전체 성능 참고값과 군내·전체 변동 차이를 함께 확인합니다. 공정 안정성은 관리도 결과와 함께 판단하세요.")
        limit_col, ratio_col, view_col = st.columns([1, 1, 2])
        auto_stability = any(it.get('capability_criteria', {}).get('mode') == 'aiag_vda' for it in results)
        with limit_col:
            if auto_stability:
                st.caption('Ppk 기준: 항목별 자동 요구값 적용')
                stab_ppk = None
            else:
                stab_ppk = st.number_input("Ppk 충족 기준", min_value=0.1, value=_required_limit(),
                                           step=0.01, key="stab_ppk_limit")
        with ratio_col:
            stab_ratio = st.number_input("변동 비율 구분 기준", min_value=1.01, value=1.25,
                                         step=0.01, key="stab_ratio_limit")
        with view_col:
            stab_view = st.radio("표시 범위", ["주요 구간 확대", "전체 보기"],
                                 horizontal=True, key="stab_view")
        opt1, opt2 = st.columns(2)
        stab_labels = opt1.checkbox("항목명 표시", value=False, key="stab_labels")
        stab_contour = opt2.checkbox("Cwk = 1 등고선 표시", value=False, key="stab_contour", disabled=auto_stability)
        st.caption(_threshold_text("기본 구분값: Ppk 1.33 / 비율 1.25 · 사내·고객 기준에 맞게 조정하세요. 비율 기준은 안정성 검정의 임계값이 아닙니다."))
        try:
            stab_rows = stability_assessment(results, stab_ppk, stab_ratio)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("평가 항목", f"{len(stab_rows)}개")
            m2.metric("Ppk 참고 기준 미달", f"{sum(r['ppk'] < r['required'] for r in stab_rows)}개")
            m3.metric("변동 차이 확인 필요", f"{sum(r['ratio'] > stab_ratio for r in stab_rows)}개")
            m4.metric("평가 제외", f"{len(results) - len(stab_rows)}개")
            if stab_rows:
                fig_stab = build_interactive_stability_scatter(
                    results, palette=palette, ppk_limit=stab_ppk, ratio_limit=stab_ratio,
                    focus=stab_view == "주요 구간 확대", show_contour=stab_contour,
                    show_labels=stab_labels)
                st.plotly_chart(fig_stab, use_container_width=True, key="stability_regions",
                                config={"displaylogo": False})
            else:
                st.info("평가 가능한 항목이 없습니다. 규격과 Cwk·Ppk 계산 결과를 확인하세요.")
            if len(stab_rows) < len(results):
                st.caption("평가 제외: Ppk 또는 Cwk/Ppk 비율을 계산할 수 없는 항목입니다. Ppk=0이나 유효 산포가 없을 때는 비율이 정의되지 않습니다. n=1의 비율은 MR 진단 참고입니다.")
        except Exception as e:
            st.error(f"안정성 산점도 생성 실패: {e}")
        st.markdown("""
**해석 가이드**
- **녹색:** 능력 기준 충족 · 변동 차이 작음 / **노랑:** 능력 기준 충족 · 변동 차이 큼
- **주황:** 능력 개선 필요 · 변동 차이 작음 / **빨강:** 능력 개선 필요 · 변동 차이 큼
- 점에 마우스를 올리면 항목명과 Cwk·Ppk·비율을 확인할 수 있습니다. 화면 밖 항목은 **전체 보기**에서 확인하세요.
- 비율 1은 군내·전체 산포 추정값이 유사함을 뜻하며, 안정성의 증거가 아닙니다. 1보다 크게 낮은 값은 부분군 구성과 변동 추정 방식을 확인하세요.
- 영역은 선택한 기준에 따른 보조 분류이며, 관리도 이상 신호의 유무를 대신하지 않습니다.
""")

# ══════════════════════════════════════════════
# TAB: 관리도 (메인)
# ══════════════════════════════════════════════
with tab_ctrl:
    if tab_ctrl.open:
        _render_first_look(results)
        st.divider()

        # 변수 선택 UI
        it, _vi = _select_detail_item(results)
        col_name = it['column']
        if it.get('data_layout_mode') == 'wide_subgroup_cols':
            _sg_cols = ", ".join(it.get('subgroup_columns', []))
            _sg_used = int(it.get('subgroup_groups_used', 0))
            _sg_skip = int(it.get('subgroup_groups_skipped', 0))
            st.caption(f"부분군 다열 모드: 측정 열 [{_sg_cols}] / 유효 부분군 {_sg_used}개 (결측 포함으로 제외 {_sg_skip}개)")

        # 판정 배지 (3-1)
        _jt = _judgment_text(it)
        _jcolor = {'🔴': 'rgba(220,38,38,0.15)', '🟡': 'rgba(217,119,6,0.15)', '🟢': 'rgba(34,197,94,0.12)'}.get(_jt[0], 'rgba(128,128,128,0.1)')
        st.markdown(
            f'<div style="background:{_jcolor};border-radius:8px;padding:8px 16px;'
            f'font-size:1.05rem;font-weight:700;margin-bottom:8px">{_jt}</div>',
            unsafe_allow_html=True
        )
        _rel_status = str(it.get('reliability_status', '정상') or '정상')
        _rel_reasons = list(it.get('reliability_reasons', []) or [])
        _rel_color = {
            '정상': 'rgba(34,197,94,0.12)',
            '주의': 'rgba(217,119,6,0.15)',
            '검토필요': 'rgba(234,88,12,0.18)',
            '계산불가': 'rgba(220,38,38,0.18)',
        }.get(_rel_status, 'rgba(100,116,139,0.12)')
        _rel_text = ", ".join(_rel_reasons) if _rel_reasons else "특이 사유 없음"
        if st.checkbox("측정점·제외 데이터 확인", key="show_point_audit"):
            raw_values = np.asarray(it.get('raw_series', it.get('original_series', [])), dtype=float)
            kept = set(np.asarray(it.get('source_indices', []), dtype=int).tolist())
            _out_info_early = it.get('outlier_info') or {}
            excluded_outliers = {int(v['index']) - 1 for v in _out_info_early.get('items', [])}
            _outlier_status = '분위수 범위 이상치 제외' if str(_out_info_early.get('method', '')).lower() == 'quantile' else 'IQR 이상치 제외'
            point_rows = []
            for idx, value in enumerate(raw_values):
                status = '분석 포함' if idx in kept else '부분군/불완전군 제외'
                if not np.isfinite(value):
                    status = '결측/비유한 값'
                elif idx in excluded_outliers:
                    status = _outlier_status
                bad = np.isfinite(value) and (
                    (np.isfinite(it.get('original_usl', np.nan)) and value > it['original_usl']) or
                    (np.isfinite(it.get('original_lsl', np.nan)) and value < it['original_lsl']))
                point_rows.append({'원본 순서': idx + 1, '원본 부분군': idx // it['n'] + 1,
                                   '원본 측정값': value, '처리': status, '원본 규격이탈': bool(bad)})
            point_frame = pd.DataFrame(point_rows)
            only_attention = st.checkbox("제외·규격이탈만 보기", key="audit_points_filter")
            visible_points = point_frame
            if only_attention and not point_frame.empty:
                visible_points = point_frame[(point_frame['처리'] != '분석 포함') | point_frame['원본 규격이탈']]
            st.dataframe(visible_points, use_container_width=True, hide_index=True, height=280)
            st.download_button("원본 처리 내역 CSV", point_frame.to_csv(index=False).encode('utf-8-sig'),
                                file_name="spc_point_audit.csv", mime="text/csv")
        st.caption("실측 PPM·규격이탈·최근 규격이탈은 전처리 전 원본 기준입니다. 관리도·능력 지수는 분석 대상 데이터 기준입니다.")
        if it.get('pre_msg'):
            st.caption(it['pre_msg'])
        st.markdown(
            f'<div style="background:{_rel_color};border-radius:8px;padding:7px 14px;'
            f'font-size:0.95rem;font-weight:650;margin-bottom:8px">'
            f'신뢰도 상태: {_rel_status} · {_rel_text}</div>',
            unsafe_allow_html=True,
        )
        if it.get('preprocess_applied'):
            _out_info = it.get('outlier_info', {}) or {}
            _out_cnt = int(_out_info.get('count', 0) or 0)
            _bc_txt = ""
            if it.get('box_cox_applied'):
                _bc_txt = f" / Box-Cox λ={fmt_value(it.get('box_cox_lambda', np.nan))}, shift={fmt_value(it.get('box_cox_shift', np.nan))}"
            st.caption(
                f"전처리 적용: 원본 N {it.get('original_n', 0)} → 분석 N {it.get('analysis_n', 0)} "
                f"(이상치 제거 {_out_cnt}개{_bc_txt})"
            )
            if _out_cnt > 0:
                with st.expander("🧹 제거된 이상치 목록", expanded=False):
                    _out_rows = _out_info.get('items', [])
                    if _out_rows:
                        if str(_out_info.get('method', '')).lower() == 'quantile':
                            _fence_txt = (
                                f"분위수 범위 기준: 하한 {fmt_value(_out_info.get('lower', np.nan))}, "
                                f"상한 {fmt_value(_out_info.get('upper', np.nan))} "
                                f"(P10 {fmt_value(_out_info.get('p10', np.nan))}, "
                                f"P90 {fmt_value(_out_info.get('p90', np.nan))}, "
                                f"W {fmt_value(_out_info.get('width', np.nan))})"
                            )
                        else:
                            _fence_txt = (
                                f"IQR 기준: 하한 {fmt_value(_out_info.get('lower', np.nan))}, "
                                f"상한 {fmt_value(_out_info.get('upper', np.nan))} "
                                f"(Q1 {fmt_value(_out_info.get('q1', np.nan))}, "
                                f"Q3 {fmt_value(_out_info.get('q3', np.nan))}, "
                                f"IQR {fmt_value(_out_info.get('iqr', np.nan))})"
                            )
                        st.caption(_fence_txt)
                        st.dataframe(pd.DataFrame(_out_rows), use_container_width=True, hide_index=True)
        _is_xmr = bool(it.get('is_xmr', False))
        _disp_kind = str(it.get('dispersion_kind', "MR" if _is_xmr else "R"))
        _disp_kind_label = {
            "R": "R(범위)",
            "S": "S(표준편차)",
            "MR": "MR(이동범위)",
        }.get(_disp_kind, _disp_kind)
        try:
            _n_subgroup = int(it.get('n', 0))
        except Exception:
            _n_subgroup = 0
        _r_lcl_applicable = not (_disp_kind == "R" and _n_subgroup <= 6)
        if it.get('distribution_mode') == 'nonnormal':
            _r_lcl_applicable = _n_subgroup > 1
        if it.get('distribution_warning'):
            st.warning(it['distribution_warning'])
        st.markdown(process_metric_cards_html(it), unsafe_allow_html=True)
        from spc_review_ui import render_result_review
        render_result_review(it, _save_config)
        render_distribution_panel(it, save_config=_save_config)
        with st.expander("계산 기준·분석 조건 안내", expanded=False):
            st.caption(WITHIN_CAPABILITY_NOTICE)
            st.caption(it.get('within_capability_note', '이전 분석 결과입니다. 재분석하면 Cw·Cwk를 확인할 수 있습니다.'))
            st.caption("Cp·Cpk 적용 상태: " + it.get('capability_status', '재분석 필요') + " · " + "; ".join(it.get('capability_reasons', [])))
            st.caption(it.get('performance_note', ''))

        _phase_label = str(it.get('control_phase_label', '해석용'))
        _phase_desc = str(it.get('limit_reference_desc', ''))
        _phase_base = _get_ctrl_total(it, scope='baseline')
        _phase_monitor = _get_ctrl_total(it, scope='monitor')
        if it.get('control_phase') == 'phase2':
            st.caption(
                f"현재 모드: **{_phase_label}**  |  한계 기준: **{it.get('limit_reference_label', '기준구간')}**  |  "
                f"기준구간 이탈 {_phase_base}건 / 감시구간 이탈 {_phase_monitor}건"
            )
            if _phase_base > 0:
                st.warning("⚠️ 기준구간 자체에 관리이탈이 있어 관리용 기준선으로는 부적합할 수 있습니다. 기준구간 재선정 또는 해석용 재검토를 권장합니다.")
            elif it.get('baseline_status') == '부적합':
                st.warning("⚠️ 기준구간 검증 결과 부적합합니다: " + "; ".join(it.get('baseline_reasons', [])))
            else:
                st.info("ℹ️ 기준구간에서 한계선을 고정했고, 이후 구간만 관리용으로 감시합니다.")
        else:
            st.caption(f"현재 모드: **{_phase_label}**  |  {_phase_desc}")

        # AI 해석 (Phase 1: 변수별)
        _bm_now = bool(st.session_state.get('beginner_mode', False))
        _admin_mode = not _bm_now
        _ai_ctx = build_var_context(it, beginner_mode=_bm_now)
        _ai_ctx_txt = json.dumps(_ai_ctx, ensure_ascii=False, sort_keys=True)
        _ai_ctx_hash = hashlib.sha1(_ai_ctx_txt.encode("utf-8")).hexdigest()[:16]
        _ai_cache_key = f"{it.get('column','')}:{_ai_ctx_hash}:{'admin' if _admin_mode else 'beginner'}"

        _ai_cache = st.session_state.get('ai_var_cache', {})
        ai_b1, ai_b2 = st.columns([1, 4])
        with ai_b1:
            if st.button("🤖 AI 해석 생성", key=f"ai_interp_btn_{_vi}", use_container_width=True,
                         disabled=not ai_available()):
                with st.spinner("AI 해석 생성 중..."):
                    _ai_result = generate_ai_interpretation(
                        context=_ai_ctx,
                        admin_mode=_admin_mode,
                    )
                    _ai_cache[_ai_cache_key] = _ai_result
                    st.session_state['ai_var_cache'] = _ai_cache
                    _save_config()
        with ai_b2:
            st.caption(
                "초보자 모드: 쉬운 해석 / 중급자 이상 모드: 근거·조치 상세 해석. "
                + ("AI 해석은 관리자 인증 후 사용할 수 있습니다." if education_mode() else "API 키 미설정 시 규칙기반 해석으로 동작합니다.")
            )

        if ai_available() and _ai_cache_key in _ai_cache:
            with st.expander("🤖 AI 해석 결과", expanded=_bm_now):
                _render_ai_interpretation(_ai_cache[_ai_cache_key], assess_process(it))

        # 관리도 (메인 — 바로 표시)
        try:
            _render_date_label_status(it)
            _render_control_chart_scrollable(
                it,
                palette=palette,
                ksq_toggles=st.session_state['ksq_toggles'],
                show_zones=st.session_state['show_zones'],
            )

            # 이탈 요약 문구 (3-2)
            _is_phase2 = (it.get('control_phase') == 'phase2')
            _n_ctrl = _get_ctrl_total(it)
            _n_ctrl_base = _get_ctrl_total(it, scope='baseline')
            _n_ctrl_all = _get_ctrl_total(it, scope='total')
            _n_spec = it.get('spec_violation_count', 0)
            if _n_ctrl > 0 or _n_spec > 0 or (_is_phase2 and _n_ctrl_base > 0):
                _msgs = []
                if _n_ctrl > 0:
                    if _is_phase2:
                        _msgs.append(f"감시구간에서 관리한계를 벗어난 점 **{_n_ctrl}개** — 고정 한계 기준으로 특수원인 조사가 필요합니다.")
                    else:
                        _msgs.append(f"관리한계를 벗어난 점 **{_n_ctrl}개** — 공정에 이상 원인이 존재할 수 있습니다.")
                if _is_phase2 and _n_ctrl_base > 0:
                    _msgs.append(f"기준구간에서도 관리이탈 **{_n_ctrl_base}개**가 확인되었습니다. 기준구간 재선정이 필요할 수 있습니다.")
                if _n_spec > 0:
                    _msgs.append(f"규격을 벗어난 데이터 **{_n_spec}건** — 불량 발생 가능성이 있습니다.")
                st.warning("⚠️ " + "  \n".join(_msgs))

                # 관리한계선 이탈 상세 정보 표시
                if _n_ctrl_all > 0:
                    _bm_on = bool(st.session_state.get('beginner_mode', False))
                    with st.expander("📘 관리이탈/KSQ 규칙 설명", expanded=_bm_on):
                        if _is_phase2:
                            st.markdown(
                                f"- 현재는 **관리용(Phase 2)** 입니다. 한계선은 **{it.get('limit_reference_label', '기준구간')}** 기준으로 고정되어 있습니다.\n"
                                f"- 감시구간 이탈: **{_n_ctrl}건**, 기준구간 이탈: **{_n_ctrl_base}건**"
                            )
                        else:
                            st.markdown("- 현재는 **해석용(Phase 1)** 입니다. 표시된 한계선은 현재 데이터 기준의 시험 관리한계입니다.")
                        st.markdown(_ctrl_violation_guide_md(it, beginner_mode=_bm_on))

                    _ctrl_rows = []
                    _is_xmr = it.get('is_xmr', False)
                    _x_name = "X(Individuals)" if _is_xmr else "Xbar"
                    _disp_kind = str(it.get('dispersion_kind', "MR" if _is_xmr else "R"))
                    _r_name = _disp_kind
                    _x_seq = "샘플" if _is_xmr else "부분군"
                    _r_seq = "구간" if _is_xmr else "부분군"
                    _x_vals = np.asarray(it.get('means', []), dtype=float)
                    _r_vals = np.asarray(it.get('ranges', []), dtype=float)
                    _ucl = it.get('ucl', np.nan)
                    _lcl = it.get('lcl', np.nan)
                    _r_ucl = it.get('r_ucl', np.nan)
                    _r_lcl = it.get('r_lcl', np.nan)
                    _x_base_end = int(it.get('baseline_display_count', len(_x_vals)))
                    _r_base_end = int(it.get('baseline_r_display_count', len(_r_vals)))

                    def _push_violation(chart_name, side, seq_label, idx, value, limit, zone):
                        if not (np.isfinite(value) and np.isfinite(limit)):
                            return
                        delta = (value - limit) if side == "UCL 초과" else (limit - value)
                        _ctrl_rows.append({
                            '구간': zone,
                            '차트': chart_name,
                            '이탈유형': side,
                            '번호': f"{seq_label} {idx + 1}",
                            '값': fmt_value(value),
                            '한계선': fmt_value(limit),
                            '초과량': fmt_value(delta),
                        })

                    for _idx, _v in enumerate(_x_vals):
                        _zone = "기준구간" if (_is_phase2 and _idx < _x_base_end) else ("감시구간" if _is_phase2 else "전체")
                        if np.isfinite(_ucl) and _v > _ucl:
                            _push_violation(_x_name, "UCL 초과", _x_seq, _idx, _v, _ucl, _zone)
                        if np.isfinite(_lcl) and _v < _lcl:
                            _push_violation(_x_name, "LCL 미만", _x_seq, _idx, _v, _lcl, _zone)

                    for _idx, _v in enumerate(_r_vals):
                        _zone = "기준구간" if (_is_phase2 and _idx < _r_base_end) else ("감시구간" if _is_phase2 else "전체")
                        if np.isfinite(_r_ucl) and _v > _r_ucl:
                            _push_violation(_r_name, "UCL 초과", _r_seq, _idx, _v, _r_ucl, _zone)
                        if np.isfinite(_r_lcl) and _v < _r_lcl:
                            _push_violation(_r_name, "LCL 미만", _r_seq, _idx, _v, _r_lcl, _zone)

                    if _ctrl_rows:
                        with st.expander("📌 관리한계선 이탈 상세", expanded=True):
                            st.dataframe(pd.DataFrame(_ctrl_rows), use_container_width=True, hide_index=True)
            else:
                if _is_phase2:
                    st.success("✅ 감시구간 데이터가 고정 관리한계·규격 내에 있습니다.")
                else:
                    st.success("✅ 모든 점이 관리한계·규격 내에 있습니다.")

            ctrl_z1, ctrl_z2 = st.columns(2)
            with ctrl_z1:
                if st.button("🔍 관리도 확대", key=f"zoom_ctrl_{_vi}", use_container_width=True):
                    _show_chart_modal(it, "control")
            with ctrl_z2:
                if st.button("🔍 개요(2×2) 확대", key=f"zoom_ov_{_vi}", use_container_width=True):
                    _show_chart_modal(it, "overview")
        except Exception as e:
            st.error(f"관리도 오류: {e}")

        # 권장 행동 가이드 (3-6)
        _guide = _action_guide_md(it)
        if _guide:
            with st.expander("💡 이 결과, 어떻게 해야 할까요?", expanded=st.session_state.get('beginner_mode', False)):
                st.markdown(_guide)

        # 차트 읽는 법 (3-7)
        with st.expander("📖 관리도 읽는 법", expanded=st.session_state.get('beginner_mode', False)):
            st.markdown("""
- **중심선 (CL)**: 데이터의 평균값입니다.
- **위/아래 점선 (UCL/LCL)**: 관리 상한/하한선. 이 선을 벗어나면 공정에 이상 신호입니다.
- **해석용 (Phase 1)**: 현재 데이터 전체로 시험 관리한계를 계산해 공정이 안정한지 먼저 봅니다.
- **관리용 (Phase 2)**: 초기 기준구간으로 한계선을 고정하고, 이후 감시구간만 운영 관리용으로 봅니다.
- **빨간 점**: 관리한계를 벗어난 이상 데이터입니다. 원인을 찾아 조치하세요.
- **점들이 한쪽으로 몰리거나 계속 올라가면**: 공정이 서서히 변화하고 있다는 신호입니다.
- **USL/LSL 선 (있을 경우)**: 고객·도면 기준 규격 상한/하한선입니다.
- **목표치(T) 선 (있을 경우)**: 공정이 맞추고자 하는 목표 중심값입니다.
""")

        with st.expander("📋 원시 데이터 미리보기 (최근 500행)", expanded=False):
            _raw_series = pd.Series(it['series'], name=col_name).reset_index(drop=True)
            _raw_series.index = _raw_series.index + 1  # 1-based
            _df_raw = _raw_series.tail(500).to_frame()

            _usl = it.get('usl', float('nan'))
            _lsl = it.get('lsl', float('nan'))
            _ucl = it.get('ucl', float('nan'))
            _lcl = it.get('lcl', float('nan'))
            _is_xmr = it.get('is_xmr', False)

            def _style_raw_val(val):
                try:
                    v = float(val)
                except Exception:
                    return ''
                if (np.isfinite(_usl) and v > _usl) or (np.isfinite(_lsl) and v < _lsl):
                    return STATUS_CELL_STYLES['bad']
                if (np.isfinite(_ucl) and v > _ucl) or (np.isfinite(_lcl) and v < _lcl):
                    return STATUS_CELL_STYLES['warn']
                return ''

            try:
                _styled_raw = _df_raw.style.map(_style_raw_val)
            except AttributeError:  # pandas < 2.1 fallback
                _styled_raw = _df_raw.style.applymap(_style_raw_val)

            st.caption("🔴 규격 이탈 (USL/LSL 초과)  🟠 관리한계 이탈 (UCL/LCL 초과)")
            st.dataframe(_styled_raw, use_container_width=True)

# ══════════════════════════════════════════════
# TAB: 내보내기
# ══════════════════════════════════════════════
with tab_export:
    if tab_export.open:
        st.subheader("💾 결과 내보내기")
        audit_meta = st.session_state.get('analysis_meta', {})
        st.caption(f"분석 ID: {audit_meta.get('analysis_id', '재분석 후 생성')} · 계산 버전: {audit_meta.get('calculation_version', '—')}")
        st.download_button("분석 조건·검증 정보 JSON", json.dumps(audit_meta, ensure_ascii=False, indent=2, default=str),
                           file_name="spc_analysis_audit.json", mime="application/json")
        ex1, ex2, ex3 = st.columns(3)

        with ex1:
            st.markdown("### 📊 Excel 다운로드")
            st.write("선별 요약 + 변수별 상세 정보 포함")
            try:
                excel_bytes = export_summary_excel(results, summary_df, analysis_meta=st.session_state.get('analysis_meta', {}))
                st.download_button("📥 Excel 다운로드", data=excel_bytes, file_name="spc_분석결과.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception as e:
                st.error(f"Excel 생성 실패: {e}")

        with ex2:
            st.markdown("### 📄 PDF 다운로드")
            st.write("HTML 리포트를 생성한 뒤 Chromium으로 PDF 변환 (A4 가로 인쇄 최적화)")
            st.caption("HTML 인쇄: Ctrl+P → 용지 A4, 가로, 배경 그래픽 켜기 | PDF 최초 1회: py -m playwright install chromium")
            pdf_charts = st.checkbox("관리도 차트 포함", value=True, key="pdf_include_charts")
            pdf_histogram = st.checkbox("히스토그램 포함", value=True, key="pdf_include_histogram")
            pdf_qq = st.checkbox("Q-Q 플롯 포함", value=False, key="pdf_include_qq")
            if st.button("📄 PDF 생성"):
                with st.spinner("HTML 생성 후 PDF 변환 중..."):
                    try:
                        pdf_bytes = export_all_pdf(
                            results=results,
                            summary_df=summary_df,
                            analysis_meta=st.session_state.get('analysis_meta', {}),
                            palette=palette,
                            hist_bins=st.session_state['hist_bins'],
                            ksq_toggles=st.session_state['ksq_toggles'],
                            show_zones=st.session_state['show_zones'],
                            include_charts=pdf_charts,
                            include_histogram=pdf_histogram,
                            include_qq=pdf_qq,
                        )
                        st.download_button("📥 PDF 다운로드", data=pdf_bytes, file_name="spc_분석결과.pdf",
                                           mime="application/pdf", key="dl_pdf_final")
                        st.success("PDF 생성 완료!")
                    except Exception as e:
                        msg = str(e).strip() or repr(e)
                        st.error(f"PDF 생성 실패: {msg}")
            st.markdown("**HTML 파일 → PDF**")
            html_for_pdf = st.file_uploader("저장한 HTML 파일 업로드", type=["html", "htm"], key="html_to_pdf_upload")
            if html_for_pdf is not None:
                if st.button("📄 HTML을 PDF로 변환", key="btn_html_to_pdf"):
                    with st.spinner("HTML 파일을 PDF로 변환 중..."):
                        try:
                            pdf_from_html = html_bytes_to_pdf(html_for_pdf.getvalue())
                            st.download_button(
                                "📥 변환된 PDF 다운로드",
                                data=pdf_from_html,
                                file_name="spc_분석결과_from_html.pdf",
                                mime="application/pdf",
                                key="dl_pdf_from_html",
                            )
                            st.success("HTML → PDF 변환 완료!")
                        except Exception as e:
                            msg = str(e).strip() or repr(e)
                            st.error(f"HTML → PDF 변환 실패: {msg}")

        with ex3:
            st.markdown("### 🌐 HTML 다운로드")
            st.write("공유용 리포트 (요약표 + 변수별 상세, A4 가로 인쇄 최적화)")
            st.caption("인쇄 시: Ctrl+P → 용지 A4, 가로, 배경 그래픽 켜기")
            html_charts = st.checkbox("관리도 차트 포함", value=True, key="html_include_charts")
            html_histogram = st.checkbox("히스토그램 포함", value=True, key="html_include_histogram")
            html_qq = st.checkbox("Q-Q 플롯 포함", value=False, key="html_include_qq")
            html_qr_url = st.text_input("QR 공유 주소 (선택)", key="html_qr_url",
                                        help="공유 주소를 입력하면 QR을 스캔해 해당 주소를 열 수 있습니다. 비우면 보고서 식별 정보가 표시됩니다.")
            public_report_dir = os.getenv('SPC_PUBLIC_REPORT_DIR', '').strip()
            public_base_url = os.getenv('SPC_PUBLIC_REPORT_BASE_URL', '').strip()
            create_html = st.button("🌐 HTML 생성")
            publish_html = False
            if public_report_dir and public_base_url:
                publish_html = st.button("HTML 생성 및 게시", help="게시한 보고서는 링크를 가진 누구나 열 수 있습니다.")
            if create_html or publish_html:
                with st.spinner("HTML 생성 중..."):
                    try:
                        html_options = dict(
                            results=results,
                            summary_df=summary_df,
                            analysis_meta=st.session_state.get('analysis_meta', {}),
                            palette=palette,
                            hist_bins=st.session_state['hist_bins'],
                            ksq_toggles=st.session_state['ksq_toggles'],
                            show_zones=st.session_state['show_zones'],
                            include_charts=html_charts,
                            include_histogram=html_histogram,
                            include_qq=html_qq,
                        )
                        if publish_html:
                            public_url, html_bytes = publish_analysis_html(public_report_dir, public_base_url, **html_options)
                            st.link_button("공개 보고서 열기", public_url)
                            st.code(public_url, language=None)
                        else:
                            html_bytes = export_analysis_html(**html_options, qr_share_url=html_qr_url)
                        st.download_button(
                            "📥 HTML 다운로드",
                            data=html_bytes,
                            file_name="spc_분석결과.html",
                            mime="text/html",
                            key="dl_html_final", on_click="ignore",
                        )
                        st.success("HTML 게시 완료!" if publish_html else "HTML 생성 완료!")
                    except Exception as e:
                        st.error(f"HTML 생성 실패: {e}")

        st.divider()
        st.markdown("### 📸 개별 차트 PNG 다운로드")
        sel_col   = st.selectbox("변수 선택", col_names, key="png_col_sel")
        _chart_options = [EXTENDED_TITLE] if _extended_result else ["개요(2×2)", "관리도(상세)"]
        sel_chart = st.selectbox("차트 종류", [*_chart_options, "히스토그램", "Q-Q 플롯"], key="png_chart_sel")
        if st.button("PNG 생성"):
            it_sel = next((x for x in results if x['column'] == sel_col), None)
            if it_sel:
                try:
                    if sel_chart == EXTENDED_TITLE:
                        extended = it_sel.get('extended_shewhart')
                        if extended is None:
                            raise ValueError(it_sel.get('extended_shewhart_error', '확장 관리도 설정을 확인하세요.'))
                        fig_dl = build_extended_figure(extended, palette=palette)
                    elif sel_chart == "개요(2×2)":
                        fig_dl = build_overview_2x2(it_sel, palette=palette, hist_bins=st.session_state['hist_bins'], ksq_toggles=st.session_state['ksq_toggles'], show_zones=st.session_state['show_zones'])
                    elif sel_chart == "관리도(상세)":
                        fig_dl = build_control_chart(it_sel, palette=palette, ksq_toggles=st.session_state['ksq_toggles'], show_zones=st.session_state['show_zones'])
                    elif sel_chart == "히스토그램":
                        fig_dl = build_histogram(it_sel, palette=palette, bins=st.session_state['hist_bins'])
                    else:
                        fig_dl = build_qq_plot(it_sel, palette=palette)
                    png_b = fig_to_bytes(fig_dl, fmt='png', dpi=150)
                    plt.close(fig_dl)
                    st.download_button(f"📥 {sel_col}_{sel_chart}.png 다운로드", data=png_b,
                                       file_name=f"{sel_col}_{sel_chart}.png", mime="image/png", key="dl_png_single")
                except Exception as e:
                    st.error(f"PNG 생성 실패: {e}")


    # ─────────────────────────── 푸터 ───────────────────────────
st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:var(--text-color,#475569); font-size:0.85rem;'>"
    "SPC Studio · 계량형 | Streamlit 기반 | 원본: 공정능력_관리도_Rev26.py"
    "</div>",
    unsafe_allow_html=True,
)


