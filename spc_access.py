"""MSA-style access: one administrator-selected day, public student sessions."""
from __future__ import annotations
from contextlib import contextmanager
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

KST = timezone(timedelta(hours=9), 'Asia/Seoul')
_SIGNING_KEY = secrets.token_bytes(32)
_HASH_ROUNDS = 600_000
# Same initial administrator password as the MSA training app.
DEFAULT_ADMIN_PASSWORD = '0017080'


def setting(name):
    value = os.environ.get(name, '').strip()
    if not value:
        import streamlit as st
        try:
            value = str(st.secrets.get(name, '')).strip()
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            value = ''
    return value


def education_mode():
    return setting('SPC_EDUCATION_MODE').lower() in {'1', 'true', 'yes', 'on'}


def config_path():
    override = setting('SPC_ACCESS_CONFIG_PATH')
    return Path(override) if override else Path(__file__).parent / 'education_runtime' / 'access_day.json'


def _valid_hash(record):
    try:
        return (isinstance(record, dict) and record['algorithm'] == 'pbkdf2_sha256'
                and record['iterations'] == _HASH_ROUNDS
                and len(bytes.fromhex(record['salt'])) == 16
                and len(bytes.fromhex(record['digest'])) == 32)
    except (KeyError, TypeError, ValueError):
        return False


def _hash_password(password):
    """Retained to verify/migrate existing locally registered admin passwords."""
    if not isinstance(password, str) or len(password) < 7:
        raise ValueError('비밀번호는 7자 이상 입력하세요.')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _HASH_ROUNDS)
    return dict(algorithm='pbkdf2_sha256', iterations=_HASH_ROUNDS, salt=salt.hex(), digest=digest.hex())


def _verify_password(password, record):
    if not _valid_hash(record):
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations'])
    return hmac.compare_digest(actual.hex(), record['digest'])


def _read_config():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError()
        if data.get('version') == 2:
            start, end = date.fromisoformat(data['start_date']), date.fromisoformat(data['end_date'])
            if end < start or (data.get('admin_source') == 'local' and not _valid_hash(data.get('admin_hash'))):
                raise ValueError()
        else:
            date.fromisoformat(data['allowed_date'])
            if data.get('admin_hash') is not None and not _valid_hash(data['admin_hash']):
                raise ValueError()
        return data
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError('사용일 설정 파일을 확인해 주세요.') from exc


@contextmanager
def _config_lock():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('다른 세션에서 저장 중입니다. 잠시 후 다시 시도하세요.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_config(data):
    path = config_path()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class DayStatus:
    allowed: bool
    allowed_date: date | None
    message: str = ''


def _deployment_day():
    day = setting('SPC_ALLOWED_DATE')
    if day:
        return day
    # Old equal start/end dates remain compatible. A multi-day range must be
    # explicitly replaced rather than silently opening additional days.
    start, end = setting('SPC_ALLOWED_START_DATE'), setting('SPC_ALLOWED_END_DATE')
    if start or end:
        if start != end or not start:
            raise ValueError('기존 기간 설정 대신 SPC_ALLOWED_DATE로 하루를 지정하세요.')
        return start
    return ''


def _fixed_day():
    return bool(setting('SPC_ALLOWED_DATE') or setting('SPC_ALLOWED_START_DATE') or setting('SPC_ALLOWED_END_DATE'))


def access_status(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('서버 시간에는 시간대가 필요합니다.')
    try:
        raw = _deployment_day()
        if not raw:
            data = _read_config()
            if not data:
                return DayStatus(False, None, '사용 가능일이 설정되지 않았습니다.')
            if data.get('version') == 2:
                if data['start_date'] != data['end_date']:
                    return DayStatus(False, None, '기존 사용기간을 사용 가능일 하루로 다시 지정해 주세요.')
                raw = data['start_date']
            else:
                raw = data['allowed_date']
        selected = date.fromisoformat(raw)
    except (OSError, ValueError, TypeError, KeyError):
        return DayStatus(False, None, '사용 가능일 설정을 확인해 주세요.')
    today = current.astimezone(KST).date()
    if today < selected:
        return DayStatus(False, selected, '사용 가능일이 아닙니다.')
    if today > selected:
        return DayStatus(False, selected, '사용 기간이 종료되었습니다.')
    return DayStatus(True, selected)


def ensure_active_day():
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    import streamlit as st
    context = get_script_run_ctx(suppress_warning=True)
    browser_gate = context is not None and st.session_state.get('_spc_access_enforced', False)
    if education_mode() or config_path().exists() or _fixed_day() or browser_gate:
        status = access_status()
        if not status.allowed:
            raise PermissionError(status.message)


def day_guard(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        ensure_active_day()
        result = func(*args, **kwargs)
        ensure_active_day()
        return result
    return guarded


def _admin_credential():
    server = setting('SPC_ADMIN_PASSWORD')
    if server:
        return server, None
    record = _read_config().get('admin_hash')
    return ('', record) if record else (DEFAULT_ADMIN_PASSWORD, None)


def _admin_signature():
    password, record = _admin_credential()
    material = json.dumps(record, sort_keys=True) if record else hashlib.sha256(password.encode()).hexdigest()
    return hmac.new(_SIGNING_KEY, ('admin:'+material).encode(), hashlib.sha256).hexdigest()


def admin_authorized():
    import streamlit as st
    try:
        return hmac.compare_digest(str(st.session_state.get('_spc_admin_token', '')), _admin_signature())
    except ValueError:
        return False


def authenticate_admin(password):
    import streamlit as st
    if time.monotonic() < st.session_state.get('_spc_admin_wait', 0):
        return False
    try:
        expected, record = _admin_credential()
        valid = (_verify_password(password, record) if record else
                 bool(expected) and hmac.compare_digest(password.encode(), expected.encode()))
    except ValueError:
        valid = False
    if valid:
        st.session_state['_spc_admin_token'] = _admin_signature()
        st.session_state['_spc_admin_failures'] = 0
        return True
    failures = st.session_state.get('_spc_admin_failures', 0)+1
    st.session_state['_spc_admin_failures'] = failures
    if failures >= 5:
        st.session_state['_spc_admin_wait'] = time.monotonic()+60
        st.session_state['_spc_admin_failures'] = 0
    return False


def save_allowed_day(selected):
    if not admin_authorized():
        raise PermissionError('관리자 인증이 필요합니다.')
    if _fixed_day():
        raise ValueError('사용일은 배포 설정에서 관리 중입니다.')
    if not isinstance(selected, date) or isinstance(selected, datetime):
        raise ValueError('사용 가능일을 선택하세요.')
    with _config_lock():
        if not admin_authorized():
            raise PermissionError('관리자 인증이 변경되었습니다. 다시 인증하세요.')
        try:
            previous = _read_config()
        except ValueError:
            if not setting('SPC_ADMIN_PASSWORD'):
                raise
            previous = {}
        data = dict(version=3, allowed_date=selected.isoformat())
        if previous.get('admin_hash'):
            data['admin_hash'] = previous['admin_hash']
        _write_config(data)


def ensure_ai_access():
    ensure_active_day()
    if education_mode() and not admin_authorized():
        raise PermissionError('AI 해석은 관리자 인증 후 사용할 수 있습니다.')


def ai_guard(func):
    @wraps(func)
    def guarded(*args, **kwargs):
        ensure_ai_access()
        result = func(*args, **kwargs)
        ensure_ai_access()
        return result
    return guarded


def ai_available():
    return not education_mode() or admin_authorized()


def ai_key(name, state_key):
    if education_mode():
        ensure_ai_access()
        return setting(name)
    import streamlit as st
    return st.session_state.get(state_key) or os.environ.get(name, '')


def _clear_analysis_state():
    import streamlit as st
    for job in st.session_state.get('_distribution_jobs', {}).values():
        if job.get('cancel') is not None:
            job['cancel'].set()
    for key in list(st.session_state):
        if not key.startswith(('_spc_', 'FormSubmitter:_spc_')):
            del st.session_state[key]
    st.session_state['analysis_done'] = False


def _render_admin_day_settings(status):
    import streamlit as st
    if not admin_authorized():
        st.info('관리자 비밀번호 입력 후 사용일을 설정할 수 있습니다.')
        with st.form('_spc_admin_login', clear_on_submit=True):
            password = st.text_input('관리자 비밀번호', type='password', key='_spc_admin_input')
            submitted = st.form_submit_button('입장')
        if submitted:
            if authenticate_admin(password):
                st.session_state['_spc_clear_admin_input'] = True
                st.rerun()
            st.error('비밀번호가 맞지 않거나 재시도 대기 중입니다. 5회 실패 시 1분 후 다시 시도하세요.')
        return
    if st.button('관리자 잠금', key='_spc_admin_lock'):
        st.session_state.pop('_spc_admin_token', None)
        st.session_state.pop('ai_var_cache', None)
        st.session_state.pop('chat_history', None)
        st.rerun()
    fixed = _fixed_day()
    if fixed:
        st.caption('사용일은 배포 설정에서 관리 중입니다.')
    with st.form('_spc_access_day'):
        selected = st.date_input('사용 가능일', value=status.allowed_date or datetime.now(KST).date(),
                                 disabled=fixed, key='_spc_selected_day_'+str(status.allowed_date))
        st.caption('한국시간 기준 선택한 날짜의 00:00부터 다음 날 00:00 전까지 사용할 수 있습니다.')
        submitted = st.form_submit_button('사용일 저장', disabled=fixed)
    if submitted:
        try:
            save_allowed_day(selected)
        except (OSError, ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.rerun()


def render_access_gate():
    """A public classroom app opens on the selected day; only settings need auth."""
    import streamlit as st
    st.session_state['_spc_access_enforced'] = True
    st.session_state.pop('_spc_user_token', None)
    if st.session_state.pop('_spc_clear_admin_input', False):
        st.session_state.pop('_spc_admin_input', None)
    status = access_status()

    def signature():
        current = access_status()
        return current.allowed, str(current.allowed_date), admin_authorized()

    st.session_state['_spc_watch_signature'] = signature()
    @st.fragment(run_every='1s')
    def watchdog():
        if signature() != st.session_state.get('_spc_watch_signature'):
            st.rerun()
    watchdog()
    with st.sidebar.expander('사용일 설정', expanded=False):
        _render_admin_day_settings(status)
    from spc_training import render_training_access
    render_training_access()
    if status.allowed_date:
        st.sidebar.caption(f'사용 가능일: {status.allowed_date.isoformat()} · 한국시간 23:59까지')
    if status.allowed:
        return
    _clear_analysis_state()
    st.title('SPC Studio · 교육 접속 안내')
    st.info(status.message)
    if status.allowed_date:
        st.caption(f'사용 가능일: {status.allowed_date.isoformat()} (한국시간)')
    st.caption('관리자는 왼쪽 사이드바의 사용일 설정에서 인증한 뒤 교육일을 지정하세요.')
    st.stop()


def render_education_gate():
    render_access_gate()
