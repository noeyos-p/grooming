# Backend — 개발 환경 설정

## 작업 전 확인 규칙

- 모든 작업 전에 `CHANGELOG.md` 전체를 읽지 말 것.
- 현재 작업이 AI 파이프라인, bbox 계약, 색상 보존, 얼굴 보존과 직접 관련될 때만
  `CHANGELOG.md`의 관련 Phase 또는 실패 패턴 요약을 참고할 것.
- 현재 유효한 규칙의 1차 진실 소스는 이 `CLAUDE.md`다.

---

## 절대 규칙

- **Gemini API 직접 호출 금지** — 반드시 `gemini_pipeline.py`를 통할 것
- **Replicate API 직접 호출 금지** — 반드시 `ai_pipeline.py`를 통할 것
- **로그인/인증 코드 추가 금지** — 비회원 1회성 서비스로 설계됨
- **견종·스타일 데이터 하드코딩 금지** — `style_prompts.py`에서만 관리

### 현재 파이프라인 알려진 동작

- **눈·코·입 위치(bbox)는 정확하게 보존됨** — 합성 파이프라인 정상 동작 확인
- **털 색상 및 눈 색깔 변경 발생 중** — 스타일 변환 범위가 색상까지 영향을 줌 (미해결)

### AI 파이프라인 — 반복 실패에서 확인된 구조적 금지 규칙

- **AFFINE·좌표 정렬 기반 원본-생성 이미지 합성 재도입 금지** — ghost 아티팩트의 구조적 원인으로 확인됨 (Phase 14)
- **모델 출력 bbox를 픽셀 좌표로 직접 취급 금지** — Gemini/SAM 결과는 정수 퍼센트 좌표 0–100 계약으로 받고, 실제 픽셀 변환은 단일 경계에서만 수행. float·픽셀·0.0–1.0 좌표를 Gemini 프롬프트에 섞어 요청 금지 (Phase 21)
- **후처리 색상 보정 코드 재추가 금지** — `_color_correct_result()` 류 LUT·히스토그램·채널 스케일 보정은 반복 실험에서 역효과 확인. 색상 보존은 프롬프트(ABSOLUTE COLOR RULE)와 입력 제약으로 해결. 색상 측정·로그는 허용 (Phase 18–21)
- **`style_prompts.py` 스타일 설명에 털 색상어 추가 금지** — "white fur", "black coat" 같은 색상 수식어는 Gemini가 원본 털 색을 바꾸는 원인. 색상 보존은 공통 프롬프트의 ABSOLUTE COLOR RULE에만 둔다 (Phase 16)
- **얼굴 보존 기준: `scripts/test_face_preservation.py` 기준 MAE ≤ 25.0** — 파이프라인 수정 후 반드시 통과 확인. 기준은 눈/코 개별 파트 MAE 평균

---

## 기술 스택
- FastAPI (Python 3.11+)
- PostgreSQL
- Google Gemini 2.5 Flash Image Generation (현재 사용 중인 AI 파이프라인)
- Cloudinary (이미지 저장)

## 현재 라우팅

- `generate.py`: Imagen-first → Gemini fallback
  - Vertex AI Imagen 튜닝 모델이 `ready` 상태이면 Imagen 파이프라인 사용
  - 미준비 시 Gemini 파이프라인으로 폴백
- `gemini_pipeline.py`: 현재 기본 생성 파이프라인 (폴백 포함 대부분의 요청 처리)
- `inpaint_pipeline.py`: 실험/대체 경로 (fal.ai FLUX.1-fill)
- `vertex_imagen_pipeline.py`: Imagen 튜닝 모델 준비된 경우만 진입

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
├── main.py                          # FastAPI 앱 진입점
├── config/
│   └── imagen_registry.json         # Vertex AI Imagen 튜닝 상태 레지스트리
├── routers/
│   ├── generate.py                  # POST /api/generate (Imagen-first → Gemini fallback)
│   └── breeds.py                    # GET /api/breeds
├── services/
│   ├── gemini_pipeline.py           # 현재 메인 파이프라인 — Gemini 이미지 생성
│   ├── inpaint_pipeline.py          # fal.ai FLUX.1-fill 기반 인페인팅 파이프라인
│   ├── vertex_imagen_pipeline.py    # Vertex AI Imagen 3 추론 파이프라인
│   ├── vertex_imagen_training.py    # Vertex AI Imagen 3 Style Tuning 잡 관리
│   ├── image_utils.py               # 공통 유틸 — MIME 감지·HEIC 변환
│   └── style_prompts.py             # 견종+스타일별 프롬프트 딕셔너리 (단일 진실 소스)
├── models/
│   └── breed.py                     # Pydantic 모델 (BreedInfo, StyleInfo, GenerateRequest)
├── tests/
│   ├── test_gemini_pipeline.py      # pytest 자동화 테스트
│   ├── test_style_prompts.py
│   ├── test_generate_router.py
│   ├── run_face_parts.py            # 수동 실행 스크립트 — 얼굴 파트 bbox 검증
│   ├── run_face_preservation.py     # 수동 실행 스크립트 — MAE 기반 얼굴 보존 평가
│   └── run_inpainting_models.py     # 수동 실행 스크립트 — inpainting 모델 비교
├── archive/                         # 폐기된 파이프라인 참조용 보관 (실행 경로에서 제외)
│   ├── services/ (ai_pipeline.py, lora_training.py, segmentation.py)
│   ├── routers/ (admin.py)
│   └── config/ (lora_registry.json)
└── requirements.txt
```

---

## 폴더 규칙

| 폴더 | 역할 | 금지 사항 |
|------|------|-----------|
| `routers/` | FastAPI 엔드포인트 정의 — 요청 파싱·응답 직렬화만 | 비즈니스 로직 직접 작성 금지 — 반드시 `services/`에 위임 |
| `services/` | 비즈니스 로직·AI 파이프라인 — 핵심 처리 담당 | HTTP 요청/응답 객체 직접 참조 금지 |
| `models/` | Pydantic 모델 — 요청·응답·DB 스키마 정의 | 로직 포함 금지 — 순수 데이터 구조만 |
| `config/` | JSON 설정 파일 — 레지스트리·환경 무관 설정 | 코드 파일 배치 금지 |
| `tests/` | 자동화 테스트 + 수동 검증 스크립트 — pytest 자동 수집 대상은 `test_*.py`, 수동 실행 스크립트는 `run_*.py` 접두사 사용 | - |

---

## CHANGELOG 규칙

- 변경, 트러블슈팅, 시도 이력은 `backend/CHANGELOG.md`에 기록할 것
- CHANGELOG는 기록 전용이며, 현재 유효한 규칙만 이 CLAUDE.md에 반영할 것
- 상세 형식과 템플릿은 `backend/CHANGELOG.md` 상단 가이드를 따른다

