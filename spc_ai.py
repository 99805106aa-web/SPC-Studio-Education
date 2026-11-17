# -*- coding: utf-8 -*-
"""
spc_ai.py
=========
SPC 결과를 AI로 해석하는 보조 모듈.

1차 구현 범위:
- 변수별 컨텍스트 생성
- 규칙기반(로컬) 해석
"""

from __future__ import annotations
from typing import Any, Dict

import os
from spc_access import ai_guard, ai_key
import json
import numpy as np
import google.generativeai as genai
import urllib.request
import urllib.error

GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _to_float(v: Any) -> float:
    try:
        f = float(v)
        return f if np.isfinite(f) else np.nan
    except Exception:
        return np.nan


def _fmt(v: Any, decimals: int = 3) -> str:
    f = _to_float(v)
    if not np.isfinite(f):
        return "—"
    return f"{f:.{decimals}f}"


from spc_core import assess_process


def _risk_level(context: Dict[str, Any]) -> str:
    assessment = context.get("assessment", {})
    if assessment.get("level"):
        return "medium" if assessment["level"] == "unknown" else assessment["level"]
    cpk = _to_float(context.get("cpk"))
    ctrl = int(context.get("ctrl_violation_count", 0))
    spec = int(context.get("spec_violation_count", 0))
    t1 = int(context.get("ksq_counts", {}).get("T1", 0))

    if spec > 0 or ctrl > 0 or t1 > 0:
        return "high"
    from spc_policy import limits
    warning, required = limits(context)
    if np.isfinite(cpk) and cpk < warning:
        return "high"
    if np.isfinite(cpk) and cpk < required:
        return "medium"
    return "low"


def build_var_context(it: Dict[str, Any], beginner_mode: bool) -> Dict[str, Any]:
    """LLM 입력용 변수별 요약 컨텍스트."""
    ksq_counts = it.get("ksq_counts", {}) or {}
    if it.get("control_phase") == "phase2":
        ctrl_violation_count = int(it.get("x_ctrl_violation_count_monitor", it.get("x_ctrl_violation_count", 0))) + int(it.get("r_ctrl_violation_count_monitor", it.get("r_ctrl_violation_count", 0)))
    else:
        ctrl_violation_count = int(it.get("x_ctrl_violation_count", 0)) + int(it.get("r_ctrl_violation_count", 0))

    return {
        "mode": "beginner" if beginner_mode else "admin",
        "assessment": assess_process(it),
        "column": str(it.get("column", "")),
        "chart_mode": str(it.get("chart_mode", "")),
        "control_phase": str(it.get("control_phase_label", "해석용")),
        "limit_reference": str(it.get("limit_reference_label", "")),
        "reliability_status": str(it.get("reliability_status", "")),
        "reliability_reasons": list(it.get("reliability_reasons", []) or []),
        "baseline_status": str(it.get("baseline_status", "")),
        "baseline_reasons": list(it.get("baseline_reasons", []) or []),
        "preprocess_applied": bool(it.get("preprocess_applied", False)),
        "empirical_basis": "규격이탈/실측 PPM은 전처리 전 원본 유효 측정값 기준",
        "original_n": int(it.get("original_n", 0)),
        "analysis_n": int(it.get("analysis_n", 0)),
        "outlier_count": int((it.get("outlier_info") or {}).get("count", 0)),
        "box_cox_applied": bool(it.get("box_cox_applied", False)),
        "dispersion_kind": str(it.get("dispersion_kind", "")),
        "n": int(it.get("n", 0)),
        "mu": _to_float(it.get("mu")),
        "std_within": _to_float(it.get("std_within")),
        "std_overall": _to_float(it.get("std_all")),
        "cp": _to_float(it.get("cp")),
        "cpk": _to_float(it.get("cpk")),
        "cw": _to_float(it.get("cw")),
        "cwk": _to_float(it.get("cwk")),
        "within_overall_ratio": _to_float(it.get("within_overall_ratio")),
        "capability_status": str(it.get("capability_status", "재분석 필요")),
        "capability_reasons": list(it.get("capability_reasons", [])),
        "performance_note": str(it.get("performance_note", "")),
        "distribution_mode": it.get('distribution_mode', 'normal'),
        "distribution_model": it.get('distribution_model'),
        "chart_model": it.get('chart_model'),
        "chart_limit_method": it.get('chart_limit_method'),
        "dispersion_limit_method": it.get('dispersion_limit_method'),
        "capability_method": it.get('capability_method', 'normal'),
        "assessment_settings": it.get('assessment_settings', {}),
        "sequence_review": it.get('sequence_review', {}),
        "study_conclusion": it.get('study_conclusion', ''),
        "capability_methods": it.get('capability_methods', {}),
        "pearson_rules_note": it.get('pearson_rules_note', ''),
        "pp": _to_float(it.get("pp")),
        "ppk": _to_float(it.get("ppk")),
        "cpm": _to_float(it.get("cpm")),
        "cpmk": _to_float(it.get("cpmk")),
        "usl": _to_float(it.get("usl")),
        "lsl": _to_float(it.get("lsl")),
        "target": _to_float(it.get("target")),
        "ucl_x": _to_float(it.get("ucl")),
        "lcl_x": _to_float(it.get("lcl")),
        "ucl_disp": _to_float(it.get("r_ucl")),
        "lcl_disp": _to_float(it.get("r_lcl")),
        "x_ucl_count": int(it.get("xbar_ucl_cnt", 0)),
        "x_lcl_count": int(it.get("xbar_lcl_cnt", 0)),
        "disp_ucl_count": int(it.get("r_ucl_cnt", 0)),
        "disp_lcl_count": int(it.get("r_lcl_cnt", 0)),
        "ctrl_violation_count": ctrl_violation_count,
        "spec_violation_count": int(it.get("spec_violation_count", 0)),
        "recent_ctrl_violation": int(it.get("recent_ctrl_violation", 0)),
        "recent_spec_violation": int(it.get("recent_spec_violation", 0)),
        "ksq_counts": {f"T{i}": int(ksq_counts.get(f"T{i}", 0)) for i in range(1, 9)},
        "normality": str(it.get("norm_msg", "")),
    }


def _fallback_interpretation(context: Dict[str, Any], admin_mode: bool, reason: str = "") -> Dict[str, Any]:
    risk = _risk_level(context)
    col = context.get("column", "변수")
    cpk = _to_float(context.get("cpk"))
    ppk = _to_float(context.get("ppk"))
    cpm = _to_float(context.get("cpm"))
    cpmk = _to_float(context.get("cpmk"))
    ctrl = int(context.get("ctrl_violation_count", 0))
    spec = int(context.get("spec_violation_count", 0))
    t1 = int(context.get("ksq_counts", {}).get("T1", 0))

    if risk == "high":
        summary = f"{col}: 고위험 신호입니다. 즉시 원인점검이 필요합니다."
    elif risk == "medium":
        summary = f"{col}: 주의 상태입니다. 중심/산포 추세를 강화 모니터링하세요."
    else:
        summary = f"{col}: 현재는 안정 상태로 보입니다."

    evidence = [
        {"metric": "Cpk", "value": _fmt(cpk), "reason": str(context.get("capability_status", "재분석 필요")) + ": " + "; ".join(context.get("capability_reasons", []))},
        {"metric": "Ppk", "value": _fmt(ppk), "reason": context.get('performance_note', '선택 분포 모형의 참고값')},
        {"metric": "Cwk", "value": _fmt(context.get("cwk")), "reason": "군내 변동에 기반한 공정 진단 지수; 이 지수만으로 공정 전체의 능력 충족을 판정하거나 입증하지 않음"},
        {"metric": "Cpm/Cpmk", "value": f"{_fmt(cpm)} / {_fmt(cpmk)}", "reason": "목표치 반영 능력 확인"},
        {"metric": "관리한계 이탈", "value": str(ctrl), "reason": "X/산포 관리도 UCL/LCL 이탈 건수"},
        {"metric": "규격 이탈", "value": str(spec), "reason": "USL/LSL 벗어난 건수"},
        {"metric": "KSQ T1", "value": str(t1), "reason": "3σ 한계 밖 점 개수"},
    ]

    if admin_mode:
        hypotheses = [
            {
                "title": "공정 중심 이동(Mean shift)",
                "confidence": "중",
                "checks": ["셋업값/오프셋 변경 이력", "교정 시점", "교대/작업자 전환 시점"],
            },
            {
                "title": "산포 증가(Variation increase)",
                "confidence": "중",
                "checks": ["공구 마모", "원재료 lot 변경", "환경(온도/습도) 변동"],
            },
            {
                "title": "측정계 영향",
                "confidence": "중",
                "checks": ["게이지 점검", "반복측정 편차", "측정 분해능/반올림"],
            },
        ]
        actions = {
            "immediate": [
                "이탈 시점 전후 lot/시간대 데이터를 분리해 확인",
                "관리한계 이탈 항목은 생산/품질 담당자에게 즉시 공유",
            ],
            "within_24h": [
                "4M1E 변경점(인원/설비/방법/자재/환경) 체크리스트 점검",
                "원인 후보별 재현 또는 반증 데이터 수집",
            ],
            "within_1w": [
                "원인 확정 후 표준조건 재설정",
                "재발 방지 관리항목(관리도 모니터링 규칙) 업데이트",
            ],
        }
    else:
        hypotheses = [
            {
                "title": "공정 조건이 평소와 달라졌을 가능성",
                "confidence": "중",
                "checks": ["작업조건 변경 여부", "자재 변경 여부"],
            }
        ]
        actions = {
            "immediate": ["이탈 구간을 먼저 확인하고 담당자에게 공유"],
            "within_24h": ["설비/자재/작업조건 변경 내역 점검"],
            "within_1w": ["원인 정리 후 재발 방지 항목 반영"],
        }

    note = "규칙기반 해석 결과입니다."
    if reason:
        note = f"{note} ({reason})"

    return {
        "summary": summary,
        "risk_level": risk,
        "evidence": evidence,
        "hypotheses": hypotheses,
        "actions": actions,
        "need_more_data": [
            "이탈 발생 시점 전후 20점 데이터",
            "설비/자재/교정 이력",
            "교대/작업자 변경 이력",
        ],
        "note": note,
        "source": "fallback",
    }


def _select_gemini_model(available_models: list[str]) -> str:
    """
    Prefer lightweight flash models (free-tier friendly) and
    avoid deprecated gemini-pro defaults.
    """
    priority = [
        "models/gemini-2.5-flash",
        "gemini-2.5-flash",
        "models/gemini-2.0-flash",
        "gemini-2.0-flash",
        "models/gemini-1.5-flash",
        "gemini-1.5-flash",
        "gemini-flash-latest",
    ]

    for p in priority:
        if p in available_models:
            return p

    flash_models = [m for m in available_models if "gemini" in m.lower() and "flash" in m.lower()]
    if flash_models:
        return flash_models[0]

    gemini_models = [m for m in available_models if "gemini" in m.lower()]
    if gemini_models:
        return gemini_models[0]

    return "models/gemini-2.5-flash"


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _estimate_tokens(text: Any) -> int:
    """Very rough fallback estimator when token metadata is unavailable."""
    s = "" if text is None else str(text)
    if not s:
        return 0
    return max(1, len(s) // 4)


def _count_tokens_safe(model: Any, text: Any) -> int:
    s = "" if text is None else str(text)
    if not s:
        return 0
    try:
        counted = model.count_tokens(s)
        # google.generativeai result can vary by version
        for attr in ("total_tokens", "token_count"):
            if hasattr(counted, attr):
                return _to_int(getattr(counted, attr), _estimate_tokens(s))
        if isinstance(counted, dict):
            for key in ("total_tokens", "token_count"):
                if key in counted:
                    return _to_int(counted.get(key), _estimate_tokens(s))
        return _estimate_tokens(s)
    except Exception:
        return _estimate_tokens(s)


def _resolve_model_limits(target_model: str, model_meta: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """Return (input_limit, output_limit). 0 means unknown."""
    if target_model in model_meta:
        meta = model_meta[target_model]
    else:
        alt = target_model.replace("models/", "") if target_model.startswith("models/") else f"models/{target_model}"
        meta = model_meta.get(alt, {})
    return (
        _to_int(meta.get("input_token_limit"), 0),
        _to_int(meta.get("output_token_limit"), 0),
    )


def _is_rate_or_quota_error(err_text: str) -> bool:
    e = (err_text or "").lower()
    keywords = [
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
        "too many requests",
        "exceeded your current quota",
        "rpd",
        "rpm",
        "tpm",
    ]
    return any(k in e for k in keywords)


def _extract_json_block(text: str) -> str:
    s = (text or "").strip()
    if "```json" in s:
        s = s.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in s:
        s = s.split("```", 1)[1].split("```", 1)[0]
    return s.strip()


def _build_interpretation_prompt(context: Dict[str, Any], admin_mode: bool) -> str:
    return f"""
당신은 20년 경력의 베테랑 품질 관리(SPC) 전문가입니다. 한국어로 답변하세요.
아래 분석 데이터(JSON)를 바탕으로 현재 공정 상태를 진단하고 전문가적인 조언을 제공하세요.
결과는 반드시 JSON 형식으로만 응답하세요. (마크다운 없이 JSON만)

[분석 데이터]
{json.dumps(context, ensure_ascii=False)}

[요청 사항]
- assessment의 계산 판정을 우선하고, 안정성 검토가 필요한 공정을 Cpk만으로 정상이라고 설명하지 마세요.
- Cp/Cpk는 전체 산포 기준이며 capability_status가 적용 가능일 때만 해석합니다. 적용 조건을 충족하지 못한 값을 추정하거나 Cwk로 대체하지 마세요.
- Cw/Cwk는 군내 산포 진단용이고 Pp/Ppk와 예상 PPM은 정규모델 가정 참고값입니다. 비정규 의심 시 공식 능력 판정을 하지 마세요.
- distribution_mode가 nonnormal이면 검증된 선택 분포와 capability_method(G/Z)를 설명하고 원자료 정규성 기각만으로 비정규 지수를 부정하지 마세요. Pearson은 T1만 적용하며 Cw/Cwk/Cpm/Cpmk는 미적용입니다.
- 예상 능력 부족과 원본 규격이탈을 구분하고, 원인은 확인이 필요한 가설로만 제시하세요.
- {'초보자도 이해하기 쉽게 설명하세요.' if not admin_mode else '전문적인 통계 용어와 분석을 제공하세요.'}

[응답 형식]
{{
  "summary": "전체 상태 요약",
  "risk_level": "high, medium, low 중 하나",
  "evidence": [{{"metric": "지표명", "value": "값", "reason": "설명"}}],
  "hypotheses": [{{"title": "가설", "confidence": "상/중/하", "checks": ["확인사항"]}}],
  "actions": {{"immediate": [], "within_24h": [], "within_1w": []}},
  "need_more_data": [],
  "note": ""
}}
"""


def _openrouter_chat_completion(
    api_key: str,
    messages: list[dict[str, Any]],
    model: str = "openrouter/free",
    timeout_sec: int = 60,
) -> tuple[str, dict[str, Any], str]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url="https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-OpenRouter-Title": "web_spc",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"OpenRouter HTTP {he.code}: {body[:400]}")
    except Exception as e:
        raise RuntimeError(f"OpenRouter request failed: {e}")

    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise RuntimeError(f"OpenRouter empty choices: {data}")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "")
    if isinstance(content, list):
        # OpenAI-compatible multimodal format can return list blocks.
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        text = "\n".join(parts).strip()
    else:
        text = str(content or "").strip()

    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    used_model = str(data.get("model", model)) if isinstance(data, dict) else model
    return text, usage if isinstance(usage, dict) else {}, used_model


def _groq_chat_completion(
    api_key: str,
    messages: list[dict[str, Any]],
    model: str = GROQ_DEFAULT_MODEL,
    timeout_sec: int = 60,
) -> tuple[str, dict[str, Any], str]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url="https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except urllib.error.HTTPError as he:
        body = ""
        try:
            body = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Groq HTTP {he.code}: {body[:400]}")
    except Exception as e:
        raise RuntimeError(f"Groq request failed: {e}")

    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        raise RuntimeError(f"Groq empty choices: {data}")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    text = str(message.get("content", "") or "").strip()
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    used_model = str(data.get("model", model)) if isinstance(data, dict) else model
    return text, usage if isinstance(usage, dict) else {}, used_model


def _update_chat_usage(
    st_mod: Any,
    *,
    in_tokens: int,
    out_tokens: int,
    total_tokens: int,
    model_name: str,
    token_source: str,
    model_input_limit: int = 0,
    model_output_limit: int = 0,
    error_text: str = "",
) -> None:
    chat_usage = st_mod.session_state.get("chat_usage", {})
    chat_usage["requests"] = _to_int(chat_usage.get("requests", 0), 0) + (0 if error_text else 1)
    chat_usage["input_tokens"] = _to_int(chat_usage.get("input_tokens", 0), 0) + max(in_tokens, 0)
    chat_usage["output_tokens"] = _to_int(chat_usage.get("output_tokens", 0), 0) + max(out_tokens, 0)
    chat_usage["total_tokens"] = _to_int(chat_usage.get("total_tokens", 0), 0) + max(total_tokens, 0)
    chat_usage["last_input_tokens"] = max(in_tokens, 0)
    chat_usage["last_output_tokens"] = max(out_tokens, 0)
    chat_usage["last_total_tokens"] = max(total_tokens, 0)
    chat_usage["model_name"] = model_name
    chat_usage["model_input_limit"] = max(model_input_limit, 0)
    chat_usage["model_output_limit"] = max(model_output_limit, 0)
    chat_usage["token_source"] = token_source
    chat_usage["last_error"] = error_text
    st_mod.session_state["chat_usage"] = chat_usage


def _generate_ai_interpretation_openrouter(
    context: Dict[str, Any],
    admin_mode: bool,
    openrouter_api_key: str,
) -> Dict[str, Any]:
    prompt = _build_interpretation_prompt(context, admin_mode)
    text, _, used_model = _openrouter_chat_completion(
        api_key=openrouter_api_key,
        messages=[{"role": "user", "content": prompt}],
        model="openrouter/free",
    )
    parsed = json.loads(_extract_json_block(text))
    parsed["source"] = "ai"
    parsed["note"] = parsed.get("note", "") + f" (OpenRouter:{used_model} 분석)"
    return parsed


def _generate_ai_interpretation_groq(
    context: Dict[str, Any],
    admin_mode: bool,
    groq_api_key: str,
) -> Dict[str, Any]:
    prompt = _build_interpretation_prompt(context, admin_mode)
    text, _, used_model = _groq_chat_completion(
        api_key=groq_api_key,
        messages=[
            {"role": "system", "content": "Return only valid JSON. Do not wrap it in markdown."},
            {"role": "user", "content": prompt},
        ],
        model=GROQ_DEFAULT_MODEL,
    )
    parsed = json.loads(_extract_json_block(text))
    parsed["source"] = "ai"
    parsed["note"] = parsed.get("note", "") + f" (Groq:{used_model} 분석)"
    return parsed


def _numeric_from_summary(v: Any) -> float:
    if v is None:
        return np.nan
    s = str(v).strip().replace(",", "")
    if not s or s in {"—", "-", "nan", "None"}:
        return np.nan
    try:
        f = float(s)
        return f if np.isfinite(f) else np.nan
    except Exception:
        return np.nan


def _int_from_summary(v: Any) -> int:
    f = _numeric_from_summary(v)
    return int(f) if np.isfinite(f) else 0


def _load_summary_records(full_summary: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(full_summary)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def _compact_summary_for_prompt(full_summary: str, max_rows: int = 80) -> str:
    """LLM 입력이 너무 커지지 않도록 핵심 컬럼/위험 항목 위주로 줄인다."""
    records = _load_summary_records(full_summary)
    if not records:
        return str(full_summary)[:12000]

    keep_cols = [
        "변수", "N", "원본N", "분석N", "신뢰도 상태", "신뢰도 사유", "능력 적용 상태", "능력 적용 사유", "성능 해석 안내", "관리도", "적용 분포", "능력 계산법", "기준분포 상태", "성능분포 상태", "Cpk", "Ppk", "Cw", "Cwk", "군내/전체 지수 비율(Cwk/Ppk)", "Cpm", "Cpmk",
        "규격이탈", "X관리이탈", "R관리이탈", "최근규격이탈", "최근관리이탈",
        "운영모드", "한계기준", "기준구간상태", "기준구간사유", "능력기준", "비고",
    ]

    def risk_key(row: dict[str, Any]) -> tuple[int, float]:
        cpk = _numeric_from_summary(row.get("Cpk"))
        spec = _int_from_summary(row.get("규격이탈"))
        x_ctrl = _int_from_summary(row.get("X관리이탈"))
        r_ctrl = _int_from_summary(row.get("R관리이탈"))
        risk = (1000 if spec > 0 else 0) + (500 if (x_ctrl + r_ctrl) > 0 else 0)
        cpk_sort = cpk if np.isfinite(cpk) else 999.0
        return (-risk, cpk_sort)

    compact = []
    for row in sorted(records, key=risk_key)[:max_rows]:
        compact.append({k: row.get(k) for k in keep_cols if k in row})

    omitted = max(len(records) - len(compact), 0)
    payload: dict[str, Any] = {"records": compact}
    if omitted:
        payload["omitted_low_risk_rows"] = omitted
    return json.dumps(payload, ensure_ascii=False)


def _fallback_chat_response(query: str, full_summary: str, reason: str = "") -> str:
    """API 키가 없거나 외부 AI 호출이 실패해도 기본 질문에는 답한다."""
    records = _load_summary_records(full_summary)
    if not records:
        base = "현재 분석 요약 데이터가 없어 챗봇이 판단할 근거가 부족합니다. 먼저 분석을 실행한 뒤 다시 질문해 주세요."
        return f"{base}\n\n참고: {reason}" if reason else base

    enriched = []
    for row in records:
        cpk = _numeric_from_summary(row.get("Cpk"))
        spec = _int_from_summary(row.get("규격이탈"))
        x_ctrl = _int_from_summary(row.get("X관리이탈"))
        r_ctrl = _int_from_summary(row.get("R관리이탈"))
        enriched.append({
            "name": str(row.get("변수", "")).strip() or "(이름 없음)",
            "cpk": cpk,
            "ppk": _numeric_from_summary(row.get("Ppk")),
            "spec": spec,
            "ctrl": x_ctrl + r_ctrl,
            "reliability": str(row.get("신뢰도 상태", "") or ""),
            "reliability_reason": str(row.get("신뢰도 사유", "") or ""),
            "capability_status": str(row.get("능력 적용 상태", "") or ""),
            "capability_reason": str(row.get("능력 적용 사유", "") or ""),
            "note": str(row.get("비고", "") or "").strip(),
        })

    cpk_items = [x for x in enriched if np.isfinite(x["cpk"])]
    worst = sorted(cpk_items, key=lambda x: x["cpk"])[:5]
    required = _numeric_from_summary(records[0].get('고객 요구값')) if records else 1.33
    warning = _numeric_from_summary(records[0].get('개선 경계')) if records else 1.
    required = required if np.isfinite(required) else 1.33
    warning = warning if np.isfinite(warning) else 1.
    bad = [x for x in cpk_items if x["cpk"] < warning]
    warn = [x for x in cpk_items if warning <= x["cpk"] < required]
    spec_bad = [x for x in enriched if x["spec"] > 0]
    ctrl_bad = [x for x in enriched if x["ctrl"] > 0]

    lines = [
        "외부 AI 호출 없이 앱 내부 요약값으로 답변합니다.",
        "",
        f"- 전체 변수: {len(records)}개",
        f"- Cpk < {warning:.2f}: {len(bad)}개",
        f"- Cpk {warning:.2f}~{required:.2f}: {len(warn)}개",
        f"- 규격 이탈 발생: {len(spec_bad)}개",
        f"- 관리한계 이탈 발생: {len(ctrl_bad)}개",
    ]

    if worst:
        lines.append("")
        lines.append("Cpk가 낮은 순서:")
        for x in worst:
            cpk_txt = _fmt(x["cpk"])
            ppk_txt = _fmt(x["ppk"])
            rel = f", 신뢰도 {x['reliability']}" if x.get("reliability") else ""
            lines.append(f"- {x['name']}: Cpk {cpk_txt}, Ppk {ppk_txt}, 규격이탈 {x['spec']}건, 관리이탈 {x['ctrl']}건{rel}")

    held = [x for x in enriched if x['capability_status'] and x['capability_status'] != '적용 가능']
    if held:
        lines.append("Cp·Cpk 검토 대상 (기준 충족/미달 집계에서 제외):")
        for x in held[:10]:
            lines.append(f"- {x['name']}: {x['capability_status']} · {x['capability_reason']}")
    lines.append("Pp·Ppk와 예상 PPM은 선택 분포 모형의 참고값입니다. 비정규 결과는 적용 분포와 G/Z 방법을 확인하세요. 정규식 Cw·Cwk는 군내 산포 진단용입니다.")

    if spec_bad or ctrl_bad:
        lines.append("")
        lines.append("우선 조치 후보:")
        for x in (spec_bad + [v for v in ctrl_bad if v not in spec_bad])[:5]:
            lines.append(f"- {x['name']}: 규격이탈 {x['spec']}건, 관리이탈 {x['ctrl']}건")

    lines.append("")
    lines.append("권장 조치: Cpk 최저 항목부터 최근 이탈 시점, 설비/자재/작업자/측정기 변경 이력을 확인하세요.")
    if reason:
        lines.append("")
        lines.append(f"참고: {reason}")
    return "\n".join(lines)

@ai_guard
def generate_ai_interpretation(
    context: Dict[str, Any],
    admin_mode: bool,
) -> Dict[str, Any]:
    """
    Gemini API를 사용한 AI 해석 생성. API 키가 없거나 오류 발생 시 규칙기반으로 대체.
    """
    import streamlit as st
    gemini_api_key = ai_key("GEMINI_API_KEY", "gemini_api_key")
    groq_api_key = ai_key("GROQ_API_KEY", "groq_api_key")
    openrouter_api_key = ai_key("OPENROUTER_API_KEY", "openrouter_api_key")

    if not gemini_api_key and not groq_api_key and not openrouter_api_key:
        return _fallback_interpretation(
            context=context,
            admin_mode=admin_mode,
            reason="Gemini/Groq/OpenRouter API 키 미설정",
        )

    prompt = _build_interpretation_prompt(context, admin_mode)

    if gemini_api_key:
        try:
            genai.configure(api_key=gemini_api_key)

            available_models = []
            try:
                for m in genai.list_models():
                    if 'generateContent' in m.supported_generation_methods:
                        available_models.append(m.name)
            except Exception:
                available_models = []

            target_model = _select_gemini_model(available_models)
            model = genai.GenerativeModel(target_model)
            response = model.generate_content(prompt)
            text = _extract_json_block(response.text or "")

            res = json.loads(text)
            res["source"] = "ai"
            res["note"] = res.get("note", "") + f" ({target_model} 분석)"
            return res

        except Exception as gemini_err:
            gemini_err_text = str(gemini_err)
            if groq_api_key:
                try:
                    res = _generate_ai_interpretation_groq(
                        context=context,
                        admin_mode=admin_mode,
                        groq_api_key=groq_api_key,
                    )
                    res["note"] = res.get("note", "") + (
                        " (Gemini 한도초과 자동전환)" if _is_rate_or_quota_error(gemini_err_text)
                        else " (Gemini 실패 자동전환)"
                    )
                    return res
                except Exception as groq_err:
                    if not openrouter_api_key:
                        return _fallback_interpretation(
                            context=context,
                            admin_mode=admin_mode,
                            reason=f"Gemini 실패 + Groq 실패: {groq_err}",
                        )

            if openrouter_api_key:
                try:
                    res = _generate_ai_interpretation_openrouter(
                        context=context,
                        admin_mode=admin_mode,
                        openrouter_api_key=openrouter_api_key,
                    )
                    res["note"] = res.get("note", "") + (
                        " (Gemini 한도초과 자동전환)" if _is_rate_or_quota_error(gemini_err_text)
                        else " (Gemini/Groq 실패 후 자동전환)"
                    )
                    return res
                except Exception as openrouter_err:
                    return _fallback_interpretation(
                        context=context,
                        admin_mode=admin_mode,
                        reason=f"Gemini 실패 + OpenRouter 실패: {openrouter_err}",
                    )
            return _fallback_interpretation(
                context=context,
                admin_mode=admin_mode,
                reason=f"Gemini 연동 실패: {gemini_err_text}",
            )

    if groq_api_key:
        try:
            res = _generate_ai_interpretation_groq(
                context=context,
                admin_mode=admin_mode,
                groq_api_key=groq_api_key,
            )
            res["note"] = res.get("note", "") + " (Groq 직접사용)"
            return res
        except Exception as groq_err:
            if not openrouter_api_key:
                return _fallback_interpretation(
                    context=context,
                    admin_mode=admin_mode,
                    reason=f"Groq 연동 실패: {groq_err}",
                )

    # Gemini/Groq 키가 없거나 실패하면 OpenRouter 사용
    try:
        res = _generate_ai_interpretation_openrouter(
            context=context,
            admin_mode=admin_mode,
            openrouter_api_key=openrouter_api_key,
        )
        res["note"] = res.get("note", "") + " (OpenRouter 직접사용)"
        return res
    except Exception as openrouter_err:
        return _fallback_interpretation(
            context=context,
            admin_mode=admin_mode,
            reason=f"OpenRouter 연동 실패: {openrouter_err}",
        )


@ai_guard
def chat_with_spc_data(
    query: str,
    history: list[dict[str, str]],
    full_summary: str,
) -> str:
    """
    사용자와 분석 데이터에 대해 대화하는 챗봇 기능.
    - query: 사용자 질문
    - history: 이전 대화 내역 (Streamlit format)
    - full_summary: 전체 변수 요약 데이터 (JSON string)
    """
    import streamlit as st
    gemini_api_key = ai_key("GEMINI_API_KEY", "gemini_api_key")
    groq_api_key = ai_key("GROQ_API_KEY", "groq_api_key")
    openrouter_api_key = ai_key("OPENROUTER_API_KEY", "openrouter_api_key")

    if not gemini_api_key and not groq_api_key and not openrouter_api_key:
        return _fallback_chat_response(
            query=query,
            full_summary=full_summary,
            reason="Gemini/Groq/OpenRouter API 키 미설정",
        )

    compact_summary = _compact_summary_for_prompt(full_summary)

    # 시스템 지침 (프롬프트 주입)
    system_instruction = f"""
당신은 품질 관리 데이터 분석 전문 챗봇입니다. 한국어로 친절하게 답변하세요.
아래는 현재 업로드된 공정 데이터의 분석 요약 결과입니다.

[전체 데이터 요약]
{compact_summary}

[답변 가이드]
1. 사용자가 특정 변수에 대해 물어보면 위 요약 데이터를 근거로 답변하세요.
2. Cp/Cpk는 전체 산포 기준이며 능력 적용 상태가 '적용 가능'일 때만 기준과 비교합니다. '관리상태 확인', '분포 검토 필요', '정규성 확인 필요'인 값을 추정하거나 Cwk로 대체하지 마세요. Pp/Ppk와 예상 PPM은 정규모델 가정 참고값이며 비정규 의심 시 능력을 확정하지 마세요. Cw/Cwk는 군내 산포 진단용입니다.
비정규 모드의 검증된 분포에는 선택한 G/Z 계산법을 적용하며 원자료의 정규성 기각만으로 지수를 부정하지 않습니다. Pearson은 T1만 적용하고 정규식 군내·목표치 지수는 미적용입니다.
3. 통계적 전문 지식을 바탕으로 원인 추정이나 조치 방안을 제안하세요.
4. 요약 데이터에 없는 내용은 추측하지 말고 모른다고 답변하거나 추가 데이터를 요청하세요.
5. 답변은 가급적 불필요한 서론 없이 핵심 위주로, 마크다운 형식을 사용하여 읽기 좋게 작성하세요.
"""

    def _chat_via_openrouter() -> str:
        if not openrouter_api_key:
            raise RuntimeError("OpenRouter API 키가 설정되지 않았습니다.")

        or_messages: list[dict[str, str]] = [{"role": "system", "content": system_instruction}]
        for h in history:
            role = str(h.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(h.get("content", "")).strip()
            if not content:
                continue
            or_messages.append({"role": role, "content": content})
        or_messages.append({"role": "user", "content": str(query)})

        response_text, usage, used_model = _openrouter_chat_completion(
            api_key=openrouter_api_key,
            messages=or_messages,
            model="openrouter/free",
        )
        response_text = response_text or ""

        in_tokens = _to_int(usage.get("prompt_tokens", 0), 0)
        out_tokens = _to_int(usage.get("completion_tokens", 0), 0)
        total_tokens = _to_int(usage.get("total_tokens", 0), 0)
        token_source = "estimate"

        if in_tokens > 0 or out_tokens > 0 or total_tokens > 0:
            token_source = "openrouter_usage"
        else:
            input_text = "\n".join(str(m.get("content", "")) for m in or_messages)
            in_tokens = _estimate_tokens(input_text)
            out_tokens = _estimate_tokens(response_text)
            total_tokens = in_tokens + out_tokens

        _update_chat_usage(
            st,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            total_tokens=total_tokens,
            model_name=used_model,
            token_source=token_source,
            model_input_limit=0,
            model_output_limit=0,
            error_text="",
        )
        return response_text

    def _chat_via_groq() -> str:
        if not groq_api_key:
            raise RuntimeError("Groq API 키가 설정되지 않았습니다.")

        groq_messages: list[dict[str, str]] = [{"role": "system", "content": system_instruction}]
        for h in history:
            role = str(h.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(h.get("content", "")).strip()
            if not content:
                continue
            groq_messages.append({"role": role, "content": content})
        groq_messages.append({"role": "user", "content": str(query)})

        response_text, usage, used_model = _groq_chat_completion(
            api_key=groq_api_key,
            messages=groq_messages,
            model=GROQ_DEFAULT_MODEL,
        )
        response_text = response_text or ""

        in_tokens = _to_int(usage.get("prompt_tokens", 0), 0)
        out_tokens = _to_int(usage.get("completion_tokens", 0), 0)
        total_tokens = _to_int(usage.get("total_tokens", 0), 0)
        token_source = "groq_usage" if (in_tokens > 0 or out_tokens > 0 or total_tokens > 0) else "estimate"
        if in_tokens <= 0:
            input_text = "\n".join(str(m.get("content", "")) for m in groq_messages)
            in_tokens = _estimate_tokens(input_text)
        if out_tokens <= 0:
            out_tokens = _estimate_tokens(response_text)
        if total_tokens <= 0:
            total_tokens = in_tokens + out_tokens

        _update_chat_usage(
            st,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            total_tokens=total_tokens,
            model_name=used_model,
            token_source=token_source,
            model_input_limit=0,
            model_output_limit=0,
            error_text="",
        )
        return response_text

    target_model = "gemini"
    model_input_limit = 0
    model_output_limit = 0

    if gemini_api_key:
        try:
            genai.configure(api_key=gemini_api_key)

            # 모델 선택 로직 (가장 빠른 flash 우선)
            available_models = []
            model_meta: dict[str, dict[str, Any]] = {}
            try:
                for m in genai.list_models():
                    if "generateContent" in m.supported_generation_methods:
                        available_models.append(m.name)
                        model_meta[m.name] = {
                            "input_token_limit": getattr(m, "input_token_limit", 0),
                            "output_token_limit": getattr(m, "output_token_limit", 0),
                        }
            except Exception:
                available_models = []
                model_meta = {}

            target_model = _select_gemini_model(available_models)
            model = genai.GenerativeModel(target_model)
            model_input_limit, model_output_limit = _resolve_model_limits(target_model, model_meta)

            # 대화 시작 (history 변환 필요: role 'user'/'assistant' -> 'user'/'model')
            gemini_history = []
            for h in history:
                gemini_history.append({
                    "role": "user" if h["role"] == "user" else "model",
                    "parts": [h["content"]],
                })

            chat = model.start_chat(history=gemini_history)

            # Streamlit rerun 뒤에도 매 질문마다 SPC 요약 컨텍스트를 함께 보낸다.
            actual_query = f"{system_instruction}\n\n사용자 질문: {query}"

            response = chat.send_message(actual_query)
            response_text = response.text or ""

            usage_md = getattr(response, "usage_metadata", None)
            in_tokens = 0
            out_tokens = 0
            total_tokens = 0
            token_source = "estimate"

            if usage_md is not None:
                in_tokens = _to_int(getattr(usage_md, "prompt_token_count", 0), 0)
                out_tokens = _to_int(getattr(usage_md, "candidates_token_count", 0), 0)
                total_tokens = _to_int(getattr(usage_md, "total_token_count", 0), 0)
                if in_tokens > 0 or out_tokens > 0 or total_tokens > 0:
                    token_source = "api_usage_metadata"

            if in_tokens <= 0:
                # Include prior conversation text for better approximation.
                hist_text = "\n".join(str(h.get("content", "")) for h in history)
                in_tokens = _count_tokens_safe(model, f"{hist_text}\n{actual_query}" if hist_text else actual_query)
            if out_tokens <= 0:
                out_tokens = _count_tokens_safe(model, response_text)
            if total_tokens <= 0:
                total_tokens = in_tokens + out_tokens

            _update_chat_usage(
                st,
                in_tokens=in_tokens,
                out_tokens=out_tokens,
                total_tokens=total_tokens,
                model_name=target_model,
                token_source=token_source,
                model_input_limit=model_input_limit,
                model_output_limit=model_output_limit,
                error_text="",
            )
            return response_text

        except Exception as gemini_err:
            gemini_err_text = str(gemini_err)
            if groq_api_key:
                try:
                    return _chat_via_groq()
                except Exception as groq_err:
                    if not openrouter_api_key:
                        error_text = f"Gemini 실패 + Groq 실패: {groq_err}"
                        _update_chat_usage(
                            st,
                            in_tokens=0,
                            out_tokens=0,
                            total_tokens=0,
                            model_name=GROQ_DEFAULT_MODEL,
                            token_source="error",
                            error_text=error_text,
                        )
                        return _fallback_chat_response(query, full_summary, error_text)

            if openrouter_api_key:
                try:
                    return _chat_via_openrouter()
                except Exception as openrouter_err:
                    error_text = f"Gemini 실패 + Groq/OpenRouter 실패: {openrouter_err}"
                    _update_chat_usage(
                        st,
                        in_tokens=0,
                        out_tokens=0,
                        total_tokens=0,
                        model_name="openrouter/free",
                        token_source="error",
                        error_text=error_text,
                    )
                    return _fallback_chat_response(query, full_summary, error_text)

            error_text = f"Gemini 연동 실패: {gemini_err_text}"
            _update_chat_usage(
                st,
                in_tokens=0,
                out_tokens=0,
                total_tokens=0,
                model_name=target_model,
                token_source="error",
                model_input_limit=model_input_limit,
                model_output_limit=model_output_limit,
                error_text=error_text,
            )
            return _fallback_chat_response(query, full_summary, error_text)

    if groq_api_key:
        try:
            return _chat_via_groq()
        except Exception as groq_err:
            if not openrouter_api_key:
                error_text = f"Groq 연동 실패: {groq_err}"
                _update_chat_usage(
                    st,
                    in_tokens=0,
                    out_tokens=0,
                    total_tokens=0,
                    model_name=GROQ_DEFAULT_MODEL,
                    token_source="error",
                    error_text=error_text,
                )
                return _fallback_chat_response(query, full_summary, error_text)

    # Gemini/Groq 키가 없거나 실패하면 OpenRouter 직접 사용
    try:
        return _chat_via_openrouter()
    except Exception as openrouter_err:
        error_text = f"OpenRouter 연동 실패: {openrouter_err}"
        _update_chat_usage(
            st,
            in_tokens=0,
            out_tokens=0,
            total_tokens=0,
            model_name="openrouter/free",
            token_source="error",
            error_text=error_text,
        )
        return _fallback_chat_response(query, full_summary, error_text)

