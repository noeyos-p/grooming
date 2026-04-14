# Backend — 개발 환경 설정

> **코드 작업 전 필독**: `CHANGELOG.md`를 먼저 읽어 과거 트러블슈팅·시도 이력을 파악할 것.

## 기술 스택
- FastAPI (Python 3.11+)
- PostgreSQL
- Google Gemini 2.5 Flash Image Generation (현재 사용 중인 AI 파이프라인)
- Cloudinary (이미지 저장)

## 설치 및 실행

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload  # http://localhost:8000
```

## 환경변수 (`backend/.env`)

```
GOOGLE_API_KEY=
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
DATABASE_URL=postgresql://...
```

## 디렉토리 구조

```
backend/
├── main.py                   # FastAPI 앱 진입점
├── config/
│   └── lora_registry.json    # LoRA 학습 상태 레지스트리 (breed+style → 버전/상태)
├── routers/
│   ├── generate.py           # POST /api/generate
│   ├── breeds.py             # GET /api/breeds
│   └── admin.py              # POST/GET/DELETE /api/admin/... (LoRA 관리)
├── services/
│   ├── ai_pipeline.py        # Replicate API 오케스트레이션
│   ├── lora_training.py      # LoRA 학습 관리 — Replicate Training API 래퍼
│   ├── segmentation.py       # SAM 배경 분리
│   └── style_prompts.py      # 견종+스타일별 프롬프트 딕셔너리 (trigger_word 포함)
├── models/
│   └── breed.py              # Pydantic 모델 (BreedInfo, StyleInfo, GenerateRequest)
└── requirements.txt
```

---

## LoRA 학습 인프라 (사용 중단)

Replicate 기반 LoRA 학습 및 admin 관리 API는 현재 비활성화 상태.
관련 파일(`services/lora_training.py`, `routers/admin.py`, `services/ai_pipeline.py`, `services/segmentation.py`)은
참조용으로 보존하나 실행 경로에서 제외됨.

---

## Replicate 모델 이력 (참조용 — 현재 파이프라인에서 미사용)

Replicate 기반 파이프라인은 Google Gemini로 교체되어 현재 사용하지 않음.
아래는 과거 시도 이력으로, 향후 Replicate 재도입 시 참조할 것.

| 모델 | 결과 |
|------|------|
| `lucataco/sdxl-controlnet` | 얼굴 보존 불가 — 전체 이미지 재창작 |
| `schananas/grounded_sam` | ControlNet+compositing 경로와 함께 폐기 |
| `lucataco/sdxl-inpainting` | true inpainting 아님 — 마스크 밖도 재창작 |
| `stability-ai/sdxl` | mask가 힌트 전용 — inpainting 불가 |
| `stability-ai/stable-diffusion-inpainting` | SD 1.5 기반 — 품질 열위 |
| `jagilley/controlnet-canny` | 입력 스키마 불일치 |
| `lucataco/ip-adapter-sdxl` | Replicate 404 |
| `meta/segment-anything` | Replicate 404 |
| `fofr/sdxl-inpainting` | Replicate 404 |
| `diffusers/stable-diffusion-xl-1.0-inpainting-0.1` | Replicate 404 |
| `andreasjansson/stable-diffusion-inpainting` | CUDA OOM |
