"""Windows 사용자 계정에 묶인 DPAPI 암호화. 다른 OS에서는 세션/환경변수를 사용한다."""
import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"
CACHE_PREFIX = b"spc-dpapi-v1:"
KEY_NAMES = ("gemini_api_key", "groq_api_key", "openrouter_api_key")


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI는 Windows에서만 사용할 수 있습니다.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def protect_key(value: str) -> str:
    if not value or os.name != "nt":
        return ""
    return PREFIX + base64.b64encode(_crypt(value.encode("utf-8"))).decode("ascii")


def unprotect_key(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value  # 기존 평문 설정을 읽은 뒤 migrate_config에서 암호화한다.
    try:
        return _crypt(base64.b64decode(value[len(PREFIX):]), decrypt=True).decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return ""  # 다른 사용자/PC의 설정은 환경변수 또는 재입력으로 복원한다.


def is_protected_cache(value: bytes) -> bool:
    """마지막 분석 캐시가 DPAPI 보호 형식인지 확인한다."""
    return bool(value and bytes(value).startswith(CACHE_PREFIX))


def protect_cache_bytes(value: bytes) -> bytes:
    """업로드 원본을 현재 Windows 사용자만 복호화할 수 있게 보호한다."""
    if not value:
        return b""
    if os.name != "nt":
        raise OSError("업로드 캐시 암호화는 Windows DPAPI가 필요합니다.")
    encrypted = _crypt(bytes(value))
    return CACHE_PREFIX + base64.b64encode(encrypted)


def unprotect_cache_bytes(value: bytes) -> bytes:
    """보호 캐시를 복호화한다. 기존 평문 캐시는 호환을 위해 그대로 반환한다."""
    payload = bytes(value or b"")
    if not is_protected_cache(payload):
        return payload
    try:
        encrypted = base64.b64decode(payload[len(CACHE_PREFIX):], validate=True)
        return _crypt(encrypted, decrypt=True)
    except (OSError, ValueError) as exc:
        raise ValueError("업로드 캐시를 복호화할 수 없습니다.") from exc


def migrate_config(config: dict) -> tuple[dict, bool]:
    """평문 키만 변환한다. 이미 암호화된 값은 그대로 유지한다."""
    secured = dict(config)
    changed = False
    for key in KEY_NAMES:
        value = secured.get(key, "")
        if value and not value.startswith(PREFIX):
            secured[key] = protect_key(value)
            changed = True
    return secured, changed
