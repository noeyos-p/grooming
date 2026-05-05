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

## CV 도구 — 사용 금지

| 도구 | 금지 이유 |
|------|-----------|
| mediapipe / dlib (사람 얼굴 landmark) | 사람 얼굴 기반, 강아지 도메인 부적합 (구조적) |

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

## CV Deterministic Geometry 후보 — 현재 채택 보류

| 후보 | 상태 | 이유 | 재검토 조건 |
|------|------|------|-----------|
| DogFLW (martvelge/DogFLW) | 보류 | 데이터셋만 공개, pretrained weight 없음, CC-BY-NC 4.0으로 상업 사용 불가 | CC-BY-NC 4.0 해제 또는 상업 라이선스 협의 시 |
| Ultralytics Dog-Pose + YOLOv8-pose fine-tune | 보류 | 데이터셋만 공개, pretrained는 human COCO로 학습됨, fine-tune 필수, AGPL-3.0 (상업 별도 라이선스) | AGPL 상업 라이선스 확보 또는 MIT 대안 모델 등장 시 |
| SAM2 point prompts | 보류 | 현재 홀드. 2-stage crop Gemini 스파이크 결과 이후 재평가 | v1.1 Step 2 다중 이미지 재산정 결과 이후 |

2-stage crop Gemini 스파이크는 2026-04-21 실측에서 불통 확정 (`max_std_ratio = 0.151`, baseline 대비 2.2× 악화, `run_2stage_crop_probe.py` · `TEST_RESULTS.md` 참조). Gemini VLM 내부 개선만으로 geometry 안정화는 불가능. 다음 트랙은 SAM2 point prompt 또는 YOLOv8 Dog-Pose fine-tune 중 별도 결정.

---

## 추가 규칙 (프로젝트 진행 중 갱신)

_새로운 실패 사례가 생기면 이 파일에 추가할 것._
