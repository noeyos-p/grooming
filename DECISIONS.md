# DECISIONS — 재시도 금지 목록

프로젝트 진행 중 실패·폐기된 접근의 압축 목록.
새 접근 제안 전 반드시 이 목록과 충돌 여부를 점검할 것.

---

## AI 모델 — 사용 금지

| 모델 | 금지 이유 |
|------|-----------|
| `lucataco/sdxl-controlnet` | 전체 이미지 재창작, 얼굴 보존 불가 |
| `lucataco/sdxl-inpainting` | mask를 힌트로만 사용, true inpainting 아님 |
| `stability-ai/sdxl` (추론) | inpainting 미지원, mask를 힌트로만 사용 |
| `stability-ai/stable-diffusion-inpainting` | SD 1.5 품질 열위 |
| `schananas/grounded_sam` | ControlNet+compositing 경로와 함께 폐기 |
| `meta/segment-anything` | Replicate 404 |
| `lucataco/ip-adapter-sdxl` | Replicate 404 |
| `jagilley/controlnet-canny` | 입력 스키마 불일치 |
| `fofr/sdxl-inpainting` | Replicate 404 |
| `diffusers/stable-diffusion-xl-1.0-inpainting-0.1` | Replicate 404 |
| `andreasjansson/stable-diffusion-inpainting` | CUDA OOM |

---

## 폐기된 인프라

| 인프라 | 상태 | 비고 |
|--------|------|------|
| Replicate 기반 LoRA 학습 | 완전 폐기 | `backend/archive/`에 보관, 실행 경로에서 제외 |
| Admin 관리 API (`routers/admin.py`) | 완전 폐기 | `backend/archive/`에 보관 |

---

## 아키텍처 패턴 — 폐기된 접근

| 접근 | 금지 이유 |
|------|-----------|
| ControlNet + SAM compositing 파이프라인 | 얼굴 보존 불가, 재창작 문제 |
| `stability-ai/sdxl` 추론 경로 직접 사용 | inpainting 미지원 |

---

## 추가 규칙 (프로젝트 진행 중 갱신)

_새로운 실패 사례가 생기면 이 파일에 추가할 것._
