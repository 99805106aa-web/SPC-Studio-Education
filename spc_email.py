# -*- coding: utf-8 -*-
"""
spc_email.py
============
SMTP 이메일 알림 모듈
원본: 공정능력_관리도_Rev26.py _smtp_send / _send_email_if_needed 에서 추출
"""

import smtplib
import ssl
import re
import logging
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SPC-WEB-EMAIL")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def validate_email(addr: str) -> bool:
    return bool(EMAIL_RE.match(addr.strip()))


def split_emails(text: str) -> List[str]:
    """쉼표/세미콜론/줄바꿈으로 구분된 이메일 목록 분할 + 유효성 검사."""
    raw = re.split(r"[,;\n\r]+", text or "")
    return [e.strip() for e in raw if e.strip() and validate_email(e.strip())]


def smtp_send(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_addr: str,
    to_addrs: List[str],
    subject: str,
    body: str,
    use_ssl: bool = True,
    use_starttls: bool = False,
    attachment_bytes: Optional[bytes] = None,
    attachment_name: Optional[str] = None,
) -> str:
    """
    SMTP 메일 발송.
    반환: 성공 시 "OK", 실패 시 오류 메시지
    """
    if not to_addrs:
        return "수신자가 없습니다."

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_addr or smtp_user
    msg['To'] = ", ".join(to_addrs)
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid()
    msg.set_content(body, charset='utf-8')

    if attachment_bytes and attachment_name:
        msg.add_attachment(
            attachment_bytes,
            maintype='application',
            subtype='octet-stream',
            filename=attachment_name,
        )

    try:
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=20) as s:
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        elif use_starttls:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.ehlo()
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        return "OK"
    except smtplib.SMTPAuthenticationError as e:
        return f"인증 실패: {e}"
    except smtplib.SMTPException as e:
        return f"SMTP 오류: {e}"
    except Exception as e:
        return f"발송 실패: {e}"


def send_alert_if_needed(
    results: List[Dict[str, Any]],
    smtp_cfg: Dict[str, Any],
    to_addrs: List[str],
    only_recent: bool = True,
) -> str:
    """
    규격 또는 관리한계 이탈이 있을 때만 이메일 발송.
    only_recent=True: 마지막 점만 확인 (최근 이탈).
    반환: 발송 결과 문자열
    """
    if not to_addrs:
        return "수신자 없음"

    spec_violations = []
    ctrl_violations = []

    for it in results:
        col = it['column']
        if only_recent:
            if it.get('recent_spec_violation', 0):
                spec_violations.append(col)
            if it.get('recent_ctrl_violation', 0):
                ctrl_violations.append(col)
        else:
            if it.get('spec_violation_count', 0) > 0:
                spec_violations.append(f"{col}({it['spec_violation_count']}건)")
            if it.get('x_ctrl_violation_count', 0) + it.get('r_ctrl_violation_count', 0) > 0:
                ctrl_violations.append(col)

    if not spec_violations and not ctrl_violations:
        return "이탈 없음 — 이메일 발송하지 않음"

    lines = ["[SPC 웹 자동 알림]\n"]
    if spec_violations:
        lines.append(f"■ 규격 이탈 변수: {', '.join(spec_violations)}")
    if ctrl_violations:
        lines.append(f"■ 관리한계 이탈 변수: {', '.join(ctrl_violations)}")
    lines.append("\n※ 본 메일은 자동 발송입니다.")
    body = "\n".join(lines)

    subject_prefix = "최근 이탈 알림" if only_recent else "전체 이탈 알림"
    subject = f"[SPC] {subject_prefix} - 규격{len(spec_violations)}건 관리한계{len(ctrl_violations)}건"

    return smtp_send(
        smtp_host=smtp_cfg.get('host', 'smtp.gmail.com'),
        smtp_port=int(smtp_cfg.get('port', 465)),
        smtp_user=smtp_cfg.get('user', ''),
        smtp_pass=smtp_cfg.get('password', ''),
        from_addr=smtp_cfg.get('from', smtp_cfg.get('user', '')),
        to_addrs=to_addrs,
        subject=subject,
        body=body,
        use_ssl=smtp_cfg.get('use_ssl', True),
        use_starttls=smtp_cfg.get('use_starttls', False),
    )
