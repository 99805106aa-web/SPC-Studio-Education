"""Extended Shewhart Xbar-S comparison for fixed reference subgroups."""

import hashlib

import numpy as np
import pandas as pd
from scipy.stats import chi2, norm


TITLE = "확장 슈하르트 X̄-S 관리도"
EXTENDED_MODE = "extended"
METHODS = {"extremes": "기준 평균의 하위·상위 3개", "stages": "교체 직후·직전 기준 평균",
           "manual": "예상 평균 범위 직접 입력"}
STAGES = ("미지정", "교체 직후", "사용 중간", "교체 직전", "보정 후")
ALIASES = {"새 공구 교체 직후": "교체 직후", "다음 교체 직전": "교체 직전",
           "보정 후 재측정": "보정 후"}
PANELS = (("일반 X̄", "평균"), ("확장 X̄", "평균"), ("S", "표준편차 S"))
GUIDE = ("AIAG–VDA SPC 2026 · 9.3.3.2 및 9.3.5.3 | S 관리도를 먼저 확인하세요. "
         "공구 상태는 확장 X̄ 이탈 해석에 사용하며 판정은 관리한계 기준입니다.")


def display_panels(compare=False):
    return PANELS[1:] + PANELS[:1] if compare else PANELS[1:]


def monitor_signals(result):
    """Return limit violations for each chart in the monitoring period."""
    columns = {name + " 판정": name for name, _ in display_panels(compare=True)}
    return result["table"].iloc[result["count"]:][list(columns)].ne("이내").rename(columns=columns)


def monitor_counts(monitor):
    """Summarize extended Xbar and S signals, excluding optional comparison charts."""
    if monitor is None or monitor.empty:
        return None, None, None, None
    signals = monitor[["확장 X̄", "S"]]
    x_count, s_count = map(int, signals.sum())
    return x_count, s_count, x_count + s_count, int(signals.iloc[-1].any())


def unsupported_reason(item):
    if not 2 <= item["n"] <= 25:
        return "부분군 크기가 2~25인 계량형 자료에서 계산할 수 있습니다."
    if item.get("box_cox_applied") or item.get("distribution_mode") != "normal":
        return "원단위의 일반 분석 결과에서 계산할 수 있습니다."
    if item.get("control_phase") != "phase2":
        return "분석 파라미터에서 관리용 관리도와 기준 부분군 수를 설정하고 다시 분석하세요."
    return ""


def subgroup_ids(item):
    n = item["n"]
    count = len(item["means"])
    return np.asarray(item["source_indices"], dtype=np.int64)[: count * n:n] // n + 1


def settings_key(item, analysis_id):
    ids = subgroup_ids(item)
    return (analysis_id, str(item["column"]), hashlib.sha256(ids.tobytes()).hexdigest())


def tool_states(ids, n, layout, source=None):
    """Keep the file label and canonical stage separate for interpretation."""
    if source is None:
        return [("미지정", "미지정")] * len(ids)
    states = []
    for number in ids:
        start = number - 1 if layout == "wide_subgroup_cols" else (number - 1) * n
        size = 1 if layout == "wide_subgroup_cols" else n
        values = ["" if pd.isna(v) else str(v).strip() for v in source.iloc[start:start + size]]
        canonical = {ALIASES.get(value, value) for value in values}
        if len(canonical) > 1:
            states.append(("미지정", "부분군 내 상태 불일치"))
        elif not values or not values[0]:
            states.append(("미지정", "미지정"))
        else:
            stage = canonical.pop()
            states.append((stage, values[0]) if stage in STAGES else ("미지정", "미인식: " + values[0]))
    return states


def calculate(item, config, source=None):
    reason = unsupported_reason(item)
    if reason:
        raise ValueError(reason)
    n = item["n"]
    ids = subgroup_ids(item)
    means = np.asarray(item["means"], dtype=float)
    raw = np.asarray(item["series"], dtype=float)
    if len(raw) != len(means) * n or len(ids) != len(means):
        raise ValueError("크기가 일정한 완전 부분군이 필요합니다.")
    s = raw.reshape(-1, n).std(axis=1, ddof=1)
    count = item["baseline_display_count"]
    reference = means[:count]
    sigma = float(np.sqrt(np.mean(s[:count] ** 2)))
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("기준구간 군내 표준편차가 0이거나 계산할 수 없습니다.")
    states = tool_states(ids, n, item.get("data_layout_mode"), source)
    overrides = config.get("overrides", {})
    states = [((overrides[int(i)], overrides[int(i)]) if int(i) in overrides else state)
              for i, state in zip(ids, states)]
    method = config.get("method", "stages")
    counts = {}
    if method == "extremes":
        if count < 6:
            raise ValueError("자동 산정에는 유효 기준 부분군이 6개 이상 필요합니다. 공구 상태별 평균이나 직접 입력도 사용할 수 있습니다.")
        ordered = np.sort(reference)
        lower, upper = float(ordered[:3].mean()), float(ordered[-3:].mean())
    elif method == "stages":
        bounds = []
        for stage in ("교체 직후", "교체 직전"):
            values = reference[np.array([code == stage for code, _ in states[:count]])]
            if not len(values):
                raise ValueError(f"기준구간에 {stage}로 지정한 부분군이 필요합니다.")
            bounds.append(float(values.mean()))
            counts[stage] = len(values)
        lower, upper = bounds
    else:
        try:
            lower, upper = float(config["lower"]), float(config["upper"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("예상 평균 하단과 상단에 숫자를 입력하세요.") from None
    center = float(reference.mean())
    if not np.isfinite([lower, upper]).all() or not lower <= center <= upper:
        raise ValueError("예상 평균 하단 ≤ 기준구간 전체 평균 ≤ 예상 평균 상단이어야 합니다.")
    confidence = config.get("confidence", 0.99)
    tail = (1 - confidence) / 2
    margin = float(norm.isf(tail) * sigma / np.sqrt(n))
    s_low, s_high = sigma * np.sqrt(chi2.ppf([tail, 1 - tail], n - 1) / (n - 1))
    limits = {"일반 X̄": (center - margin, center, center + margin),
              "확장 X̄": (lower - margin, center, upper + margin),
              "S": (float(s_low), float(s[:count].mean()), float(s_high))}
    table = pd.DataFrame({"부분군": ids, "구간": np.where(np.arange(len(ids)) < count, "기준", "감시"),
                          "공구 상태": [label for _, label in states], "평균": means, "표준편차 S": s})
    for name, value in PANELS:
        lcl, _, ucl = limits[name]
        table[name + " 판정"] = np.select([table[value] < lcl, table[value] > ucl],
                                         ["하한 미달", "상한 초과"], default="이내")
    return dict(limits=limits, table=table, sigma=sigma, margin=margin, lower=lower, upper=upper,
                method=METHODS[method], confidence=confidence, count=count, counts=counts,
                reference=item.get("limit_reference_label", ""), n=n,
                baseline_requested_count=item.get("baseline_requested_count"))


def calculation_note(result):
    return (f"{result['method']} · 비개입 확률 {result['confidence']:.2%} · n={result['n']} · "
            f"기준 {result['count']}개 · σ̂ᵢₙ {result['sigma']:.8g} · 관리 여유 {result['margin']:.8g} · "
            f"예상 평균 {result['lower']:.8g} – {result['upper']:.8g}")


def mean_axis_limits(result, table):
    lcl, _, ucl = result["limits"]["확장 X̄"]
    lo, hi = min(lcl, table["평균"].min()), max(ucl, table["평균"].max())
    pad = (hi - lo) * .12
    return lo - pad, hi + pad
