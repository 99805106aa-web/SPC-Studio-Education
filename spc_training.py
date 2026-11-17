"""Public classroom address and locally generated QR; no external QR service."""
import io
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit

import qrcode
import streamlit as st

from spc_access import admin_authorized, config_path, setting


def canonical_public_url(value):
    value = str(value or '').strip()
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not host or parsed.username is not None
            or parsed.password is not None or len(value) > 2048):
        raise ValueError('공개 접속 주소는 https://로 시작하는 앱 주소를 입력하세요.')
    if host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local', '.internal')):
        raise ValueError('localhost·내부 주소는 교육생 공유 주소로 사용할 수 없습니다.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host:
            raise ValueError('공개 도메인을 입력하세요.')
    else:
        if not address.is_global:
            raise ValueError('공개 도메인을 입력하세요.')
    parsed.port  # Validate malformed port numbers.
    return urlunsplit(('https', parsed.netloc.lower(), parsed.path.rstrip('/'), '', ''))


def _url_config_path():
    return config_path().with_name('public_url.json')


def public_url():
    configured = setting('SPC_PUBLIC_URL')
    if configured:
        return canonical_public_url(configured)
    path = _url_config_path()
    if path.exists():
        try:
            return canonical_public_url(json.loads(path.read_text(encoding='utf-8'))['url'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('저장된 공개 접속 주소를 관리자 설정에서 확인하세요.') from exc
    detected = st.context.url or ''
    # A deployed Streamlit app knows its real browser origin. Never fabricate a
    # subdomain and never turn a localhost address into a classroom QR.
    if (urlsplit(detected).hostname or '').lower().endswith('.streamlit.app'):
        return canonical_public_url(detected)
    return ''


def qr_png(url):
    url = canonical_public_url(url)
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    code.add_data(url)
    code.make(fit=True)
    output = io.BytesIO()
    code.make_image(fill_color='black', back_color='white').save(output, format='PNG')
    return output.getvalue()


def save_public_url(value):
    from spc_access import _config_lock
    import os
    import tempfile
    from pathlib import Path
    if not admin_authorized():
        raise PermissionError('접속 주소 변경은 관리자 인증이 필요합니다.')
    if setting('SPC_PUBLIC_URL'):
        raise ValueError('접속 주소는 배포 설정에서 관리 중입니다.')
    url = canonical_public_url(value)
    path = _url_config_path()
    with _config_lock():
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                temp = Path(handle.name)
                json.dump({'url':url}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)


def render_training_access():
    with st.sidebar.expander('🎓 교육 접속 주소 · QR', expanded=False):
        try:
            url = public_url()
        except ValueError as exc:
            st.warning(str(exc))
            url = ''
        if url:
            st.markdown('**교육생 접속 주소**')
            st.code(url, language=None)
            st.link_button('교육용 앱 열기', url)
            png = qr_png(url)
            st.image(png, caption='휴대폰 카메라로 접속 QR을 스캔하세요.', width=220)
            st.download_button('접속 QR PNG 내려받기', png, file_name='SPC_교육_접속_QR.png',
                               mime='image/png', key='_spc_qr_download', on_click='ignore')
            st.download_button('접속 주소 TXT 내려받기', (url+'\n').encode('utf-8'),
                               file_name='SPC_교육_접속주소.txt', mime='text/plain',
                               key='_spc_url_download', on_click='ignore')
        else:
            st.info('Streamlit 배포 후 실제 앱 주소와 QR이 표시됩니다. 관리자가 공개 주소를 직접 등록할 수도 있습니다.')
        st.caption('지정일에는 교육생이 로그인 없이 접속합니다. 분석은 PC·노트북 사용을 권장합니다.')
        if admin_authorized():
            with st.form('_spc_public_url_settings'):
                value = st.text_input('공개 접속 주소', value=url, key='_spc_public_url_input',
                                      disabled=bool(setting('SPC_PUBLIC_URL')))
                submitted = st.form_submit_button('접속 주소 저장', disabled=bool(setting('SPC_PUBLIC_URL')))
            if submitted:
                try:
                    save_public_url(value)
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
