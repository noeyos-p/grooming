# Grooming Style

강아지 사진을 업로드하면 견종별 미용 스타일로 털 질감에 맞게 AI가 변환해주는 웹 서비스.
비회원 1회성 · 웹 우선 · 포트폴리오 + 상업 서비스 전환 고려

---

## 커뮤니케이션 규칙

- **사용자에게 항상 존댓말 사용** — 반말 금지

---

## 절대 규칙

- **로그인/인증 코드 추가 금지** — 비회원 1회성 서비스로 설계됨
- **견종·스타일 데이터 하드코딩 금지** — `style_prompts.py`에서만 관리
- **Replicate API 직접 호출 금지** — 반드시 `ai_pipeline.py`를 통할 것
- **Gemini API 직접 호출 금지** — 반드시 `gemini_pipeline.py`를 통할 것
- **OpenAI DALL-E 사용 금지** — Replicate API 또는 Google Gemini API만 허용
- **GPU 서버 직접 연동 금지** — API 과금 방식만 허용

---

## 아키텍처

### 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Next.js 14+ (App Router) + TypeScript + Tailwind CSS + shadcn/ui |
| Mobile (추후) | React Native + Expo |
| Backend | FastAPI (Python 3.11+) |
| AI Pipeline | Replicate API — SDXL + ControlNet (lucataco/sdxl-controlnet) + SAM |
| DB | PostgreSQL (견종/스타일 메타데이터) |
| Storage | Cloudinary (이미지 저장 + 다운로드) |
| Infra | Vercel (Frontend) + Railway 또는 Render (Backend) |

### 폴더 구조

```
Grooming/
├── frontend/
│   ├── app/
│   │   ├── page.tsx              # 메인 (업로드 + 견종/스타일 선택)
│   │   ├── result/page.tsx       # 결과 페이지
│   │   └── api/                  # BFF API routes
│   ├── components/
│   │   ├── ImageUploader.tsx
│   │   ├── BreedSelector.tsx
│   │   ├── StyleSelector.tsx
│   │   └── ResultDisplay.tsx
│   └── public/styles/            # 스타일 예시 썸네일
│
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── generate.py           # POST /api/generate
│   │   └── breeds.py             # GET /api/breeds
│   ├── services/
│   │   ├── ai_pipeline.py        # Replicate API 오케스트레이션
│   │   ├── segmentation.py       # SAM 배경 분리
│   │   └── style_prompts.py      # 견종+스타일별 프롬프트 (단일 진실 소스)
│   └── models/
│       └── breed.py              # Pydantic 모델
│
└── CLAUDE.md
```

### AI 파이프라인

```
업로드
  → LoRA 학습된 모델로 img2img 스타일 변환 (견종+스타일별 LoRA)
  → Cloudinary 저장 → URL 반환
```

**유일하게 허용된 추론 경로: LoRA img2img**
- 학습된 LoRA가 없는 견종+스타일 조합은 서비스 불가 (에러 반환)
- LoRA 학습: `stability-ai/sdxl` Training API (베이스 모델 전용, 추론에는 사용 금지)

**사용 금지 모델 (얼굴 보존 불가 — 재창작 문제):**
- ~~`schananas/grounded_sam`~~ — ControlNet+compositing 경로와 함께 폐기
- ~~`lucataco/sdxl-controlnet`~~ — 전체 이미지 재창작, 얼굴 보존 불가
- ~~`lucataco/sdxl-inpainting`~~ — mask를 힌트로만 사용, true inpainting 아님
- ~~`stability-ai/sdxl`~~ (추론) — inpainting 미지원, mask를 힌트로만 사용
- ~~`stability-ai/stable-diffusion-inpainting`~~ — SD 1.5 품질 열위
- ~~`meta/segment-anything`~~ — Replicate 404
- ~~`lucataco/ip-adapter-sdxl`~~ — Replicate 404
- ~~`jagilley/controlnet-canny`~~ — 입력 스키마 불일치

---

## 빌드/테스트

> 상세 명령어는 `frontend/CLAUDE.md`, `backend/CLAUDE.md` 참고

```bash
# Frontend
cd frontend && npm install && npm run dev     # http://localhost:3000

# Backend
cd backend && source venv/bin/activate && uvicorn main:app --reload  # http://localhost:8000
```

**배포:** Frontend → Vercel (main 브랜치 자동 배포) · Backend → Railway/Render

---

## 도메인 컨텍스트

### 핵심 용어

| 용어 | 설명 |
|------|------|
| 견종 (breed) | 11개 지원: 말티즈·푸들·비숑·말티푸·포메라니안·요크셔테리어·시츄·파피용·스피츠·미니비숑·베들링턴 |
| 스타일 (style) | 견종별 미용 스타일, 견종당 3개 (이름 TBD → `style_prompts.py`에서 관리) |
| 스타일 프롬프트 | Replicate API에 전달되는 견종+스타일 특화 텍스트 |
| 털 질감 | CLIP으로 자동 감지 후 프롬프트에 반영 |

### 사용자 플로우

```
사진 업로드 → 견종 선택 → 스타일 선택 → 변환하기
→ 로딩 (15~30초) → 결과 확인 → 다운로드
```

### API 계약

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /api/breeds` | 견종 + 스타일 목록 반환 |
| `POST /api/generate` | `{ image_url, breed_id, style_id }` → `{ result_url, processing_time }` |

---

## 에이전트 사용 규칙

작업 성격에 따라 반드시 아래 서브에이전트를 사용할 것.

| 작업 유형 | 사용 에이전트 |
|-----------|--------------|
| FastAPI 라우터, DB 모델, AI 파이프라인, Cloudinary, backend/ 내 모든 파일 | `grooming-style-backend` |
| Next.js 페이지/컴포넌트, shadcn/ui, Tailwind CSS, TypeScript 타입, frontend/ 내 모든 파일 | `frontend-nextjs` |

- frontend + backend 동시에 수정하는 경우: 두 에이전트를 병렬 실행
- 에이전트를 거치지 않고 직접 구현 금지

---

## 코딩 컨벤션

### 네이밍

| 대상 | 규칙 | 예시 |
|------|------|------|
| 컴포넌트 | PascalCase | `BreedSelector.tsx` |
| 훅 | camelCase + use 접두사 | `useImageUpload.ts` |
| API 라우터 | snake_case | `generate.py` |
| 견종/스타일 ID | snake_case | `maltese`, `teddy_cut` |
| 환경변수 | UPPER_SNAKE_CASE | `REPLICATE_API_TOKEN` |

### 커밋 메시지

```
feat: 말티즈 테디베어컷 스타일 프롬프트 추가
fix: SAM 배경 분리 실패 시 폴백 처리
refactor: ai_pipeline 비동기 처리 개선
```

### 패턴 규칙

- 비즈니스 로직은 custom hook으로 분리 — UI 컴포넌트에 직접 작성 금지 (모바일 전환 대비)
- 이미지 업로드 시 클라이언트에서 파일 크기(10MB) 및 형식(JPG/PNG) 검증 필수
- Replicate API 콜드 스타트 지연 대비 로딩 UX 반드시 구현

---

## 문서 유지 규칙

### 규칙 변경 시 문서 즉시 반영

- 대화 중 스타일·아키텍처·규칙이 변경되면 **즉시 해당 CLAUDE.md 파일을 수정**할 것
- 변경 대상 예시: 컬러 시스템, 컴포넌트 구조, API 계약, 절대 규칙 등
- 구두로만 합의하고 파일을 업데이트하지 않는 것은 금지 — 다음 대화에서 규칙이 유실됨

### CHANGELOG 작성 규칙

- 파일 위치: `frontend/CHANGELOG.md`, `backend/CHANGELOG.md` (각 영역별 분리)
- **파일·기능 추가·수정·삭제, 트러블슈팅 해결, 시도한 접근법이 생길 때마다 반드시 해당 CHANGELOG를 업데이트**할 것
- CLAUDE.md에 트러블슈팅이나 시도 이력을 기록하지 말 것 — CHANGELOG가 단일 기록 장소
- 형식:
  ```
  ## YYYY-MM-DD

  ### 추가
  - `경로/파일명` — 한 줄 설명

  ### 수정
  - `경로/파일명` — 변경 내용 한 줄 요약

  ### 삭제
  - `경로/파일명` — 삭제 이유

  ### 트러블슈팅
  #### [문제 제목]
  - **증상**: 어떤 오류·현상이 발생했는지
  - **원인**: 왜 발생했는지
  - **해결**: 어떻게 고쳤는지

  ### 시도한 접근
  #### [시도 제목] — 실패/보류
  - **방법**: 어떻게 시도했는지
  - **결과**: 왜 안 됐는지
  - **대안**: 대신 무엇을 선택했는지
  ```
- 날짜는 항상 절대 날짜(YYYY-MM-DD)로 기록
- frontend 관련 → `frontend/CHANGELOG.md`, backend 관련 → `backend/CHANGELOG.md`
- 동일 날짜에 여러 변경이 있으면 하나의 날짜 섹션 아래 모아서 기록
