# Backend Changelog

## 2026-04-14

### 수정
- `services/gemini_pipeline.py` — `ThinkingConfig` import 및 `GenerateContentConfig`의 `thinking_config=ThinkingConfig(thinking_budget=5000)` 완전 제거: `gemini-3.1-flash-image-preview`는 thinking이 내장되어 있어 별도 설정 불필요, `response_modalities=["IMAGE"]`와 동시에 설정하면 `ValueError` 발생
- `routers/generate.py` — `from services.style_prompts import get_prompt` import 추가; 파이프라인 진입 전 `breed_id`/`style_id` 사전 검증 블록 추가(유효하지 않으면 422 반환); `except ValueError as exc` + `except RuntimeError as exc` 분리 핸들러를 `except (ValueError, RuntimeError) as exc` 통합 핸들러로 교체 후 500 반환 — SDK 파라미터 오류가 422로 노출되는 경로 차단

### 트러블슈팅
#### ThinkingConfig + IMAGE 모달리티 충돌로 HTTP 422 반환
- **증상**: `POST /api/generate` 호출 시 422 에러 반환
- **원인**: `_run_gemini()`에서 `ThinkingConfig(thinking_budget=5000)`과 `response_modalities=["IMAGE"]`를 동시에 설정하면 Google Gemini SDK가 `ValueError`를 throw; `generate.py`의 `except ValueError` 핸들러가 이를 422로 그대로 노출
- **해결**: `gemini_pipeline.py`에서 `ThinkingConfig` 관련 코드 전체 제거; `generate.py`에서 `ValueError`를 파이프라인 레벨에서는 500으로 처리, 422는 breed/style 사전 검증 단계에서만 발생하도록 분리

---

## 2026-04-14

### 수정
- `services/gemini_pipeline.py` — 이미지 생성 품질 향상을 위한 두 가지 업그레이드 적용
  - `asyncio` import 추가
  - `from google.genai.types import GenerateContentConfig, Part, ThinkingConfig` import 추가
  - 이미지 생성 모델 `gemini-2.5-flash-image` → `gemini-3.1-flash-image-preview` 변경
  - `_MODEL_ANALYSIS = "gemini-2.5-flash"` 상수 추가
  - `_analyze_dog_features()` async 함수 추가: `gemini-2.5-flash` 텍스트 모델로 강아지 눈/코/입/포즈/털색/배경 분석, 실패 시 빈 문자열 반환하여 파이프라인 중단 없음
  - `_run_gemini()` — `GenerateContentConfig`에 `ThinkingConfig(thinking_budget=5000)` 추가
  - `run_gemini_pipeline()` — 단계 5를 5-1(특징 분석), 5-2(털 색상 추출), 5-3(분석 결과 프롬프트 주입)으로 세분화; `enhanced_prompt`를 `_run_gemini()`에 전달

---

### 추가
- `config/imagen_registry.json` — Vertex AI Imagen 스타일 튜닝 상태 레지스트리 (breed+style → tuning_job_name / endpoint_id / status)
  - status 값: `pending` → `tuning` → `ready` / `failed`
- `services/vertex_imagen_training.py` — Vertex AI Imagen 3 Style Tuning 잡 관리 모듈
  - `start_tuning()`: style_prompts.BREEDS의 reference_images_gcs를 읽어 튜닝 잡 시작, 레지스트리에 status="tuning" 기록
  - `get_tuning_status()`: TuningJobServiceClient로 잡 상태 폴링, 완료 시 endpoint_id + status="ready" + tuned_at 기록
  - `load_registry()` / `save_registry()` / `get_imagen_entry()`: 레지스트리 관리
- `services/vertex_imagen_pipeline.py` — Vertex AI Imagen 3 추론 파이프라인
  - `run_vertex_imagen_pipeline()`: 프롬프트 조회 → endpoint_id 확인 → 이미지 다운로드 → HEIC 변환 → Vertex AI 추론 (asyncio.to_thread) → Cloudinary 업로드 → URL 반환
  - `_detect_mime_type()`, `_convert_to_jpeg_if_needed()`: gemini_pipeline.py와 동일한 헬퍼 로직

### 수정
- `requirements.txt` — `google-cloud-aiplatform>=1.70.0` 추가 (Vertex AI SDK)
- `.env` — Vertex AI 관련 환경변수 5개 추가 (placeholder 값): `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`, `VERTEX_IMAGEN_OUTPUT_GCS_BUCKET`, `VERTEX_IMAGEN_TRAINING_GCS_BUCKET`
- `services/style_prompts.py` — 33개 스타일 전체에 `"reference_images_gcs": None` 필드 추가 (실제 GCS 경로는 추후 개별 입력)
- `routers/admin.py` — Vertex AI Imagen 튜닝 관련 엔드포인트 4개 추가
  - `POST /api/admin/imagen/tune` — 스타일 튜닝 잡 시작 (Form: breed_id, style_id)
  - `GET /api/admin/imagen/tune/{tuning_job_name:path}/status` — 튜닝 상태 조회 (path 타입으로 '/' 포함 리소스명 처리)
  - `GET /api/admin/imagen` — 전체 Imagen 레지스트리 조회
  - `DELETE /api/admin/imagen/{breed_id}/{style_id}` — Imagen 레지스트리 항목 삭제
- `routers/generate.py` — Imagen-first + Gemini fallback 라우팅 추가
  - `get_imagen_entry()`로 해당 breed+style의 Imagen 준비 여부 확인
  - status="ready" + endpoint_id 존재 시 Imagen 파이프라인 선택, 미준비 시 Gemini 폴백
- `main.py` — admin 라우터 재등록 (`app.include_router(admin.router)`)

---

## 2026-04-14

### 수정
- `services/gemini_pipeline.py` — `_extract_dominant_fur_colors()` 함수 추가: PIL로 이미지를 100x100으로 리사이즈 후 `getcolors()`로 픽셀별 빈도 수집, 배경 추정 색상(RGB 각각 230 이상 또는 30 이하) 제외, 상위 5가지 색상을 `rgb(R, G, B)` 형태 문자열로 반환
- `services/gemini_pipeline.py` — `run_gemini_pipeline()`: 이미지 bytes 확보 후 Gemini 호출 전 `_extract_dominant_fur_colors()` 실행, 추출 성공 시 실측 RGB 값을 프롬프트에 포함 / 실패 시 일반 색상 보존 지시로 폴백
- `services/gemini_pipeline.py` — `gemini_prompt` 구성 로직 전면 개편: 5개 항목의 구조화된 CRITICAL REQUIREMENTS 형식으로 교체 (털 색상 실측값 명시, 눈·코·입 간격·비율·상대적 위치 보존 지시, 변경 대상을 fur cut/length/texture로 명시)
- `services/gemini_pipeline.py` — 파이프라인 단계 주석 번호 업데이트: 색상 추출·프롬프트 구성이 step 5로 추가되면서 Gemini 호출 → step 6, Cloudinary 업로드 → step 7로 순서 조정
- `services/gemini_pipeline.py` — docstring 파이프라인 순서 갱신 (7단계로 확장)

---

## 2026-04-14

### 수정
- `routers/generate.py` — 메인 파이프라인을 Replicate(ControlNet+SAM+LoRA)에서 Google Gemini로 교체: `run_pipeline` → `run_gemini_pipeline` import 및 호출 변경
- `main.py` — admin(LoRA 학습 관리) 라우터 비활성화: `from routers import admin` 및 `app.include_router(admin.router)` 제거
- `requirements.txt` — `replicate==0.34.1` 제거 (실행 경로에서 replicate 사용처 없음)
- `backend/CLAUDE.md` — 현재 AI 파이프라인을 Google Gemini 2.5 Flash로 명시, LoRA 인프라 섹션 사용 중단으로 표기, 환경변수에서 REPLICATE_API_TOKEN/REPLICATE_USERNAME 제거 후 GOOGLE_API_KEY로 교체

---

## 2026-04-14

### 삭제
- `services/ai_pipeline.py` — `_run_controlnet()`, `_composite_images()`, `_upload_bytes_to_cloudinary()` 삭제. ControlNet+SAM+PIL compositing 경로 완전 제거
- `services/segmentation.py` — 미사용 파일로 마킹 (SAM 경로 폐기)

### 수정
- `services/ai_pipeline.py` — `run_pipeline()`을 LoRA 전용으로 단순화. LoRA 미학습 시 ValueError 반환

---

## 2026-04-14

### 평가
#### Gemini 파이프라인 현재 완성도 (IMG_7641.jpg / maltese / teddy_cut 기준)
- **털 색상 유지**: 프롬프트로 지시하고 있으나 완전히 유지되지 않음 — 색조 변화가 일부 발생
- **눈/코/입 보존**: 프롬프트 지시만으로는 위치·형태 정밀 보존에 한계 있음
- **종합**: 현재까지 테스트한 결과물 중 완성도가 가장 높음 — SAM compositing ghost 현상 제거 후 개선됨
- **향후 과제**: 털 색상 정밀 보존 및 얼굴 특징 유지를 위한 추가 프롬프트 개선 또는 대안 접근 필요

### 수정
- `services/gemini_pipeline.py` — SAM 합성 로직 제거 후 프롬프트 강화 방식으로 대체: `_get_face_features_mask()`, `_composite_face_features()`, `asyncio.gather` SAM 병렬 실행 코드 전체 제거. 눈/코/입 보존을 프롬프트 CRITICAL REQUIREMENTS 4개 항목으로 Gemini에 지시
- `services/gemini_pipeline.py` — 불필요한 import 제거: `asyncio`, `replicate`, `ImageFilter`, `ImageOps` 제거 (HEIC 변환에 필요한 `io`, `Image`는 유지)

### 트러블슈팅
#### Gemini 결과에 ghost 현상 발생
- **증상**: Gemini 변환 결과 위에 원본 눈/코/입 픽셀을 좌표 그대로 붙여넣어 두 실루엣이 겹쳐 보이는 ghost 현상
- **원인**: ControlNet은 원본 구조를 그대로 유지해 픽셀 합성이 가능하지만, Gemini는 이미지를 새로 생성하면서 강아지 위치가 미묘하게 달라져 좌표 기반 compositing과 불일치
- **해결**: SAM 호출 및 PIL compositing 전체 제거. 프롬프트에 눈/코/입 보존 CRITICAL REQUIREMENTS 4개 항목 강화로 Gemini가 자연스럽게 처리하도록 변경

### 추가
- `requirements.txt` — `pillow-heif==0.18.0` 추가 (HEIC 변환 지원)
- `services/gemini_pipeline.py` — `_convert_to_jpeg_if_needed()` 함수 추가: magic number 기반 HEIC 감지 후 JPEG로 변환, 변환 여부 bool 함께 반환

### 수정
- `services/gemini_pipeline.py` — 파일 최상단에 `pillow_heif.register_heif_opener()` 등록 추가: Pillow가 HEIC 파일을 열 수 있도록 초기화
- `services/gemini_pipeline.py` — `run_gemini_pipeline()`: 이미지 다운로드 직후 HEIC → JPEG 변환 적용, 변환된 경우 Cloudinary 재업로드해 SAM에 전달할 public JPEG URL 확보
- `services/gemini_pipeline.py` — `run_gemini_pipeline()`: `prompt_data["prompt"]`를 그대로 사용하지 않고 색상 보존 지시문을 append한 `color_preserving_prompt`를 Gemini에 전달

### 트러블슈팅
#### HEIC 이미지 파이프라인 처리 검증
- **증상**: iPhone 촬영 HEIC 이미지(IMG_7641.jpg)가 Cloudinary에 .heic로 저장됨 — SAM과 Gemini 모두 HEIC 미지원
- **원인**: Cloudinary는 HEIC를 원본 포맷 그대로 저장 (format 미지정 시)
- **해결**: 다운로드한 bytes를 `_convert_to_jpeg_if_needed()`로 변환 후 Cloudinary 재업로드(`format="jpg"`) → SAM public JPEG URL 획득

## 2026-04-14

### 수정
- `backend/services/gemini_pipeline.py` — 눈/코/입 보존 합성 추가: Gemini 변환 + Grounded SAM 병렬 실행 후 PIL composite으로 원본 눈/코/입 픽셀 보존
- `backend/services/gemini_pipeline.py` — Gemini 모델명 수정: `gemini-2.0-flash-exp-image-generation` → `gemini-2.5-flash-image` (구 모델명 404로 존재하지 않음)
- `backend/services/gemini_pipeline.py` — MIME 타입 자동 감지 추가: `_detect_mime_type()` 함수로 magic number 기반 HEIC/JPEG/PNG/WEBP 자동 판별 (기존 `image/jpeg` 하드코딩 제거)
- `backend/services/gemini_pipeline.py` — Gemini content=None 방어 코드 추가: safety filter 등으로 content가 None인 경우 명확한 RuntimeError로 처리
- `backend/services/gemini_pipeline.py` — SAM AsyncIterator 수집 수정: `replicate.async_run` 반환값이 AsyncIterator인 경우 `async for`로 수집하도록 변경 (`list()` 사용 불가)

### 트러블슈팅
#### gemini-2.5-flash-image MIME 타입 하드코딩 오류
- **증상**: HEIC 이미지를 `mime_type="image/jpeg"`로 Gemini에 전달하면 `candidates[0].content`가 None으로 반환됨
- **원인**: HEIC 파일을 JPEG로 잘못 선언하면 Gemini가 이미지를 파싱하지 못하고 content=None으로 응답
- **해결**: `_detect_mime_type()` 함수 추가 — magic number(ftyp box, PNG signature, JPEG SOI)로 실제 포맷 감지

#### SAM AsyncIterator list() 오류
- **증상**: `Grounded SAM 실패: 'async_generator' object is not iterable`
- **원인**: `replicate.async_run()` 반환값이 AsyncIterator이므로 동기 `list()`로 변환 불가
- **해결**: `hasattr(output, '__aiter__')`로 AsyncIterator 감지 후 `async for`로 수집

### 트러블슈팅
#### gemini-2.0-flash-exp-image-generation 404
- **증상**: Gemini API 호출 시 `404 NOT_FOUND` — models/gemini-2.0-flash-exp-image-generation is not found for API version v1beta
- **원인**: 구 실험 모델명이 API에서 제거됨
- **해결**: `client.models.list()`로 현재 사용 가능 모델 목록 확인 후 `gemini-2.5-flash-image`로 교체

#### gemini-2.5-flash-image 무료 티어 quota 0
- **증상**: `429 RESOURCE_EXHAUSTED` — limit: 0 on GenerateRequestsPerDayPerProjectPerModel-FreeTier
- **원인**: `gemini-2.5-flash-image`(preview-image) 모델은 무료 티어에서 일일 허용 횟수가 0 — 유료 플랜 전용
- **해결**: Google AI API 유료 플랜 활성화 필요. 코드 자체는 정상 동작 확인됨

---

## 2026-04-14

### 추가
- `backend/services/gemini_pipeline.py` — Google Gemini 2.0 Flash 기반 이미지 생성 파이프라인
- `backend/tests/__init__.py` — pytest 테스트 패키지 초기화
- `backend/tests/test_gemini_pipeline.py` — gemini_pipeline 단위 테스트 (6개)
- `backend/tests/test_style_prompts.py` — style_prompts 단위 테스트 (5개)
- `backend/tests/test_generate_router.py` — FastAPI 라우터 통합 테스트 (5개)
- `backend/conftest.py` — pytest 공통 픽스처
- `backend/pytest.ini` — asyncio_mode = auto 설정

### 수정
- `backend/requirements.txt` — google-genai, pytest, pytest-asyncio 추가
- `backend/.env` — GOOGLE_API_KEY 항목 추가

---

## 2026-04-14

### 추가
- `scripts/test_inpainting_models.py` — SDXL inpainting 후보 모델 3종 비교 테스트 스크립트
  - `fofr/sdxl-inpainting`, `diffusers/sdxl-inpainting`, `andreasjansson/sd-inpainting` 순차 실행
  - `--image <url>` CLI 인자로 테스트 이미지 지정
  - PIL로 합성 마스크(중앙 상단 흰색 원) 생성 후 Cloudinary `grooming-style/test-masks` 업로드
  - 결과 테이블을 stdout에 출력하고 `scripts/results.json`에 전체 결과 저장

---

## 2026-04-14

### 수정
- `services/ai_pipeline.py` — `_run_controlnet`: `num_inference_steps` 30→50, `condition_scale` 0.5→0.7 (출력 선명도 개선)
- `services/ai_pipeline.py` — `_composite_images`: 하드 바이너리 마스크 합성 → Gaussian blur(radius=8) 소프트 마스크 합성으로 교체 (얼굴/털 경계 하드 엣지 제거)
- `services/segmentation.py` — `_MASK_DILATION` 10→15 (얼굴 보존 안전 마진 확대)

### 트러블슈팅
#### 출력 이미지가 흐릿하고 입력 구조를 따르지 않는 문제
- **증상**: 변환 결과가 뭉개지거나 안개 낀 듯 흐릿하게 출력됨
- **원인**: `num_inference_steps=30`으로 디퓨전 스텝이 부족해 디테일 복원 불충분; `condition_scale=0.5`로 ControlNet이 입력 구조를 약하게 따름
- **해결**: `num_inference_steps=50`, `condition_scale=0.7`로 상향

#### 얼굴 경계에 합성 이음새(seam) 아티팩트 발생
- **증상**: 얼굴 영역과 털 영역 경계에 날카로운 선이 보임
- **원인**: `ImageOps.invert` 후 바이너리 마스크로 `Image.composite` 직접 적용 — 경계 픽셀이 0 또는 255의 하드 엣지로만 처리됨
- **해결**: `ImageFilter.GaussianBlur(radius=8)`로 마스크 경계를 먼저 블러 처리한 뒤 합성 (소프트 트랜지션)

### 추가
- `config/lora_registry.json` — 견종+스타일별 LoRA 학습 상태 영속 저장소
  - 키: `{breed_id}_{style_id}`, 값: replicate_model, version, trigger_word, status, training_id 등
  - status 값: `pending` / `training` / `ready` / `failed`
- `services/lora_training.py` — Replicate Training API 래퍼
  - `start_training()`: Replicate에 모델 레포 생성 후 SDXL LoRA 학습 시작
  - `get_training_status()`: 학습 상태 폴링, 완료 시 version 자동 기록
  - `load_registry()` / `save_registry()` / `get_lora_entry()`: 레지스트리 관리
- `routers/admin.py` — LoRA 학습 관리 API (`/api/admin`)
  - `POST /api/admin/train` — ZIP 업로드 후 학습 시작
  - `GET /api/admin/train/{training_id}/status` — 학습 상태 조회
  - `GET /api/admin/lora` — 전체 레지스트리 조회
  - `DELETE /api/admin/lora/{breed_id}/{style_id}` — 레지스트리 항목 삭제

### 수정
- `main.py` — admin 라우터 등록 추가
- `services/ai_pipeline.py` — LoRA 추론 분기 추가
  - `_run_with_lora()`: 학습된 LoRA 모델로 img2img 변환 (3회 재시도 포함)
  - `run_pipeline()`: lora_registry 확인 후 LoRA / 기존(ControlNet+SAM) 경로 자동 선택
- `services/style_prompts.py` — 33개 견종+스타일 전체에 `trigger_word` 필드 추가
  - 패턴: `GRMD` + 견종4자 + 스타일4자 (대문자)
  - `get_prompt()` 반환값에 `trigger_word` 포함

### 환경변수 추가
- `REPLICATE_USERNAME` — LoRA 학습 시 Replicate 모델 소유자 계정명

### 트러블슈팅
#### base64 dataURL을 Replicate에 직접 전달 시 500 에러
- **증상**: 프론트엔드가 `data:image/jpeg;base64,...` 형태로 `image_url` 전달 → 500 에러
- **원인**: Replicate API는 public URL 또는 파일 객체만 허용, base64 스킴 미지원
- **해결**: `ai_pipeline.py`에서 `image_url.startswith("data:")` 감지 시 Cloudinary에 먼저 업로드 후 public URL로 교체

#### Replicate 모델 404 에러
- **증상**: `ReplicateError: status 404, The requested resource could not be found`
- **원인**: 모델 자체가 Replicate에서 삭제/비공개 처리됨 (`lucataco/ip-adapter-sdxl`, `meta/segment-anything` 등)
- **해결**: `replicate.models.get()`으로 존재 여부 사전 검증 후 대체 모델 사용

#### Cloudinary overwrite=False로 동일 public_id 재업로드 시 에러
- **증상**: 동일 breed+style 조합 두 번째 요청에서 Cloudinary 업로드 실패
- **원인**: `overwrite=False` 설정 + 고정 `public_id`로 인해 충돌
- **해결**: `overwrite=True`로 설정

#### main.py에서 load_dotenv() 위치가 잘못되면 Cloudinary "Must supply api_key" 에러
- **증상**: 백엔드 재시작 후 `{"detail":"Must supply api_key"}` 422 에러
- **원인**: `load_dotenv()`를 라우터 import 이후에 호출 → `ai_pipeline.py`의 `cloudinary.config()`가 모듈 로드 시점에 실행될 때 환경변수가 비어있음
- **해결**: `load_dotenv()`를 `from routers import ...` 보다 반드시 먼저 호출

#### HEIC 파일을 Replicate에 전달하면 "cannot identify image file" 에러
- **증상**: `ModelError: cannot identify image file '/tmp/image.png'`
- **원인**: iPhone 사진(.jpg 확장자라도 실제로는 HEIC) → Cloudinary가 .heic로 저장 → Replicate HEIC 미지원
- **해결**: Cloudinary 업로드 시 `format="jpg"` 지정해 JPEG로 강제 변환

#### meta/segment-anything Replicate 404
- **증상**: `meta/segment-anything` 호출 시 404
- **원인**: 모델이 Replicate에서 삭제/비공개 처리됨
- **해결**: `schananas/grounded_sam:ee871c19...` 으로 대체. 텍스트 프롬프트 기반 마스크 생성 가능

### 시도한 접근
#### ControlNet 단독으로 얼굴·배경 보존 시도 — 실패
- **방법**: `lucataco/sdxl-controlnet` 전체 이미지 변환
- **결과**: 얼굴·배경 모두 변환됨. ControlNet은 마스크 기능 없이 전체 이미지를 대상으로 함
- **대안**: SAM으로 마스크 생성 후 PIL 합성으로 보존

#### Grounded SAM + stability-ai/sdxl inpainting — 실패
- **방법**: SAM으로 마스크 생성 → `stability-ai/sdxl`의 `mask` 파라미터로 inpainting
- **결과**: 오히려 더 나빠짐. `stability-ai/sdxl`의 `mask`는 진짜 inpainting이 아니라 마스크를 힌트로만 사용하고 전체 이미지를 변환함
- **대안**: ControlNet + SAM 병렬 실행 후 PIL 픽셀 합성으로 전환

#### Grounded SAM(털 마스크) + PIL 합성 — 실패
- **방법**: SAM으로 "dog fur" 마스크 생성 → PIL composite(transformed, orig, fur_mask)
- **결과**: 얼굴·배경 보존 개선 안 됨. SAM이 "털" 영역 경계를 불명확하게 인식, 마스크 품질 불량
- **대안**: 털 탐지 대신 얼굴 탐지 후 반전하는 방식으로 전환

#### Grounded SAM(얼굴 마스크 반전) + PIL 합성 — 현재 적용 중
- **방법**: SAM으로 "dog face" 마스크 생성 → 반전 → PIL composite
- **결과**: 얼굴 탐지가 털 탐지보다 정확. 반전하면 얼굴=보존, 털=변환
- `adjustment_factor=-15` 침식으로 얼굴 경계에서 안전 마진 확보
- 합성 규칙: `Image.composite(transformed, orig, mask)` — 흰색=변환, 검은색=원본

---

## 2026-04-09

### 추가 (초기 세팅)
- `main.py` — FastAPI 앱 진입점, CORS 설정
- `routers/generate.py` — `POST /api/generate` (이미지 변환 요청)
- `routers/breeds.py` — `GET /api/breeds` (견종+스타일 목록)
- `services/ai_pipeline.py` — Replicate API 오케스트레이션
  - lucataco/sdxl-controlnet + schananas/grounded_sam 병렬 실행
  - PIL 픽셀 합성으로 얼굴·배경 보존
  - base64 dataURL → Cloudinary 사전 변환 처리
- `services/segmentation.py` — Grounded SAM 배경 분리
- `services/style_prompts.py` — 11개 견종 × 3개 스타일 프롬프트 정의
- `models/breed.py` — Pydantic 모델 (BreedInfo, GenerateRequest 등)
- FastAPI + Replicate + Cloudinary 기반 프로젝트 초기화
