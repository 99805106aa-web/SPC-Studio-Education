"""Education deployment policy. Desktop execution remains unrestricted."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

KST = timezone(timedelta(hours=9), "Asia/Seoul")
_SIGNING_KEY = secrets.token_bytes(32)


def setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        import streamlit as st
        try:
            value = str(st.secrets.get(name, "")).strip()
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            value = ""
    return value


def education_mode() -> bool:
    return setting("SPC_EDUCATION_MODE").lower() in {"1", "true", "yes", "on"}


def config_path() -> Path:
    override = setting("SPC_ACCESS_CONFIG_PATH")
    return Path(override) if override else Path(__file__).parent / "education_runtime" / "access_day.json"


@dataclass(frozen=True)
class DayStatus:
    allowed: bool
    allowed_date: date | None
    message: str = ""


def access_status(now: datetime | None = None) -> DayStatus:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("서버 시간에는 시간대가 필요합니다.")
    try:
        raw = setting("SPC_ALLOWED_DATE")
        if not raw:
            path = config_path()
            if not path.exists():
                return DayStatus(False, None, "사용 가능일이 설정되지 않았습니다.")
            raw = json.loads(path.read_text(encoding="utf-8"))["allowed_date"]
        selected = date.fromisoformat(raw)
    except (OSError, ValueError, TypeError, KeyError):
        return DayStatus(False, None, "사용 가능일 설정을 확인해 주세요.")
    today = current.astimezone(KST).date()
    if today != selected:
        return DayStatus(False, selected, "사용 가능일이 아닙니다." if today < selected else "사용 기간이 종료되었습니다.")
    return DayStatus(True, selected)


def ensure_active_day():
    if education_mode():
        status = access_status()
        if not status.allowed:
            raise PermissionError(status.message)


def day_guard(func):
    """Recheck before work/cache reads and before returning newly generated results."""
    @wraps(func)
    def guarded(*args, **kwargs):
        ensure_active_day()
        result = func(*args, **kwargs)
        ensure_active_day()
        return result
    return guarded


def _admin_signature() -> str:
    return hmac.new(_SIGNING_KEY, setting("SPC_ADMIN_PASSWORD").encode(), hashlib.sha256).hexdigest()


def admin_authorized() -> bool:
    import streamlit as st
    return bool(setting("SPC_ADMIN_PASSWORD")) and hmac.compare_digest(
        str(st.session_state.get("_spc_admin_token", "")), _admin_signature())


def authenticate_admin(password: str) -> bool:
    import streamlit as st
    if time.monotonic() < st.session_state.get("_spc_admin_wait", 0):
        return False
    expected = setting("SPC_ADMIN_PASSWORD")
    if expected and hmac.compare_digest(password.encode(), expected.encode()):
        st.session_state["_spc_admin_token"] = _admin_signature()
        st.session_state["_spc_admin_failures"] = 0
        return True
    failures = st.session_state.get("_spc_admin_failures", 0) + 1
    st.session_state["_spc_admin_failures"] = failures
    if failures >= 5:
        st.session_state["_spc_admin_wait"] = time.monotonic() + 60
        st.session_state["_spc_admin_failures"] = 0
    return False


def ensure_ai_access():
    ensure_active_day()
    if education_mode() and not admin_authorized():
        raise PermissionError("AI 해석은 관리자 인증 후 사용할 수 있습니다.")


def ai_guard(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        ensure_ai_access()
        result = func(*args, **kwargs)
        ensure_ai_access()
        return result
    return guarded


def ai_available() -> bool:
    return not education_mode() or admin_authorized()


def ai_key(name: str, state_key: str) -> str:
    if education_mode():
        ensure_ai_access()
        return setting(name)
    import streamlit as st
    return st.session_state.get(state_key) or os.environ.get(name, "")


def save_allowed_day(selected: date):
    if not admin_authorized():
        raise PermissionError("관리자 인증이 필요합니다.")
    if setting("SPC_ALLOWED_DATE"):
        raise ValueError("사용일은 배포 설정에서 관리 중입니다.")
    if not isinstance(selected, date) or isinstance(selected, datetime):
        raise ValueError("사용 가능일을 선택하세요.")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump({"allowed_date": selected.isoformat()}, handle)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def render_education_gate():
    """Run before loading desktop configuration or rendering analysis UI."""
    if not education_mode():
        return
    import streamlit as st

    @st.fragment(run_every="1s")
    def watchdog():
        if access_status().allowed != st.session_state.get("_spc_day_open"):
            st.rerun()

    status = access_status()
    st.session_state["_spc_day_open"] = status.allowed
    watchdog()
    with st.sidebar.expander("🔐 사용일 설정 / 관리자"):
        if not setting("SPC_ADMIN_PASSWORD"):
            st.info("배포 설정에 SPC_ADMIN_PASSWORD를 지정하세요.")
        elif not admin_authorized():
            with st.form("spc_admin_login"):
                password = st.text_input("관리자 비밀번호", type="password")
                submitted = st.form_submit_button("인증")
            if submitted:
                if authenticate_admin(password):
                    st.rerun()
                st.error("인증 실패 또는 재시도 대기 중입니다. 5회 실패 시 1분 후 재시도하세요.")
        else:
            if st.button("관리자 잠금"):
                st.session_state.pop("_spc_admin_token", None)
                st.session_state.pop("ai_var_cache", None)
                st.session_state.pop("chat_history", None)
                st.rerun()
            fixed = bool(setting("SPC_ALLOWED_DATE"))
            with st.form("spc_access_day"):
                selected = st.date_input("사용 가능일", value=status.allowed_date or datetime.now(KST).date(), disabled=fixed)
                st.caption("한국시간 00:00부터 다음 날 00:00 전까지 사용합니다.")
                if fixed:
                    st.caption("사용일은 환경변수 또는 Cloud Secrets에서 관리 중입니다.")
                submitted = st.form_submit_button("사용일 저장", disabled=fixed)
            if submitted:
                try:
                    save_allowed_day(selected)
                except (OSError, ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
    with st.sidebar.expander("🎓 교육 접속 안내"):
        st.markdown("공유받은 URL을 Chrome 또는 Edge로 여세요. 설치·회원가입은 필요 없습니다.\n\n"
                    "예제 또는 Excel/CSV 업로드로 실습합니다. 기밀·개인정보는 업로드하지 마세요.\n\n"
                    "입력과 결과는 각 접속 세션에만 보관됩니다. 종료 전 보고서를 내려받으세요. AI는 강사만 사용합니다.")
    if status.allowed_date:
        st.sidebar.caption(f"사용 가능일: {status.allowed_date.isoformat()} · 한국시간")
    if not status.allowed:
        # Remove old results on expiry; uploaded data stays in this session only.
        for key in ("results", "summary_df", "_bounded_cache", "ai_var_cache", "chat_history", "_preflight_cached"):
            st.session_state.pop(key, None)
        st.session_state["analysis_done"] = False
        st.title("SPC Studio · 교육용")
        st.info(status.message)
        if status.allowed_date:
            st.caption(f"사용 가능일: {status.allowed_date.isoformat()} (한국시간)")
        st.stop()
