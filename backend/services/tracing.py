"""LangSmith tracing 헬퍼.

환경변수 설정 예시:
  LANGCHAIN_TRACING_V2=true
  LANGCHAIN_API_KEY=<LangSmith API 키>
  LANGCHAIN_PROJECT=grooming-style

위 변수가 없거나 langsmith 패키지가 미설치면 데코레이터는 no-op으로 동작한다.
(즉, 프로덕션 배포 환경에서도 추가 지연 없이 무시된다.)

파이프라인 입력/출력에 raw 이미지 bytes가 섞이면 LangSmith payload가 폭주하므로,
bytes 및 bytearray는 길이만 기록하도록 sanitize 한다.
"""

from __future__ import annotations

from typing import Any, Callable

try:
    from langsmith import traceable as _lc_traceable

    _LANGSMITH_AVAILABLE = True
except ImportError:  # 패키지 미설치 — 데코레이터 자리만 채운다
    _LANGSMITH_AVAILABLE = False


def traceable(*dargs: Any, **dkwargs: Any) -> Callable:
    """langsmith.traceable 의 pass-through 래퍼.

    langsmith 미설치면 원 함수를 그대로 반환한다.
    """
    if _LANGSMITH_AVAILABLE:
        return _lc_traceable(*dargs, **dkwargs)

    # @traceable 단독 사용
    if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
        return dargs[0]

    # @traceable(run_type=..., name=...) 형태
    def _decorator(func: Callable) -> Callable:
        return func

    return _decorator


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"_bytes_len": len(value)}
    return value


def sanitize_inputs(inputs: dict) -> dict:
    """image_bytes 등 bytes 인자를 길이 메타로 치환하고, 직렬화 불가한 client 객체는 제외."""
    safe: dict = {}
    for key, value in inputs.items():
        if key in {"gemini_client", "client"}:
            continue
        safe[key] = _sanitize_value(value)
    return safe


def sanitize_bytes_output(output: Any) -> Any:
    """bytes 반환값은 길이만 기록. tuple 반환이면 각 요소를 sanitize."""
    if isinstance(output, (bytes, bytearray)):
        return {"_bytes_len": len(output)}
    if isinstance(output, tuple):
        return [_sanitize_value(v) for v in output]
    return output
