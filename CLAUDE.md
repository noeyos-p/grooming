# Grooming Style

강아지 사진을 업로드하면 견종별 미용 스타일로 털 질감에 맞게 AI가 변환해주는 웹 서비스.
비회원 1회성 · 웹 우선 · 포트폴리오 + 상업 서비스 전환 고려

---

## 커뮤니케이션 규칙

- **사용자에게 항상 존댓말 사용** — 반말 금지

---

## 절대 규칙

- **로그인/인증 코드 추가 금지**
- **견종·스타일 데이터 하드코딩 금지**
- **환경설정 파일 임의 변경 금지**
- **사용하지 않는 파일 대량 생성 금지**
- **테스트 없이 완료 선언 금지**

---

## 문서 우선순위

- 루트 CLAUDE.md는 프로젝트 공통 원칙만 정의한다
- frontend 구현 규칙 (컴포넌트 구조, 컬러 시스템, UX 패턴) → `frontend/CLAUDE.md`
- backend 구현 규칙 (AI 파이프라인, 모델 선택, 금지 접근, 디렉토리 구조) → `backend/CLAUDE.md`
- AI 파이프라인·모델·bbox·색상 보존·금지 접근의 단일 진실 소스는 `backend/CLAUDE.md`
- 루트 CLAUDE.md는 공통 원칙만 정의하며, 구현 세부사항은 해당 영역의 CLAUDE.md를 따른다
- 더 구체적인 범위를 다루는 CLAUDE.md가 우선한다

---

## 아키텍처

### 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Next.js 14+ (App Router) + TypeScript + Tailwind CSS + shadcn/ui |
| Mobile (추후) | React Native + Expo |
| Backend | FastAPI (Python 3.11+) |
| AI Pipeline | `backend/CLAUDE.md` 참조 |
| DB | PostgreSQL (견종/스타일 메타데이터) |
| Storage | Cloudinary (이미지 저장 + 다운로드) |
| Infra | Vercel (Frontend) + Railway 또는 Render (Backend) |

---

## 빌드/테스트

> 상세 명령어는 `frontend/CLAUDE.md`, `backend/CLAUDE.md` 참고

```bash
# Frontend
cd frontend && npm install && npm run dev     # http://localhost:3000

# Backend
cd backend && source venv/bin/activate && uvicorn main:app --reload  # http://localhost:8000
```

**테스트 이미지:** 모든 테스트는 항상 `~/Downloads/IMG_7641.jpg` 를 사용할 것

**테스트 결과 기록:** 수동 테스트 스크립트(`run_*.py`) 실행 후 수치 결과를 반드시 루트 `TEST_RESULTS.md`의 해당 섹션 표에 추가할 것

**배포:** Frontend → Vercel (main 브랜치 자동 배포) · Backend → Railway/Render

---

## 도메인 컨텍스트

### 핵심 용어

| 용어 | 설명 |
|------|------|
| 견종 (breed) | 11개 지원: 말티즈·푸들·비숑·말티푸·포메라니안·요크셔테리어·시츄·파피용·스피츠·미니비숑·베들링턴 |
| 스타일 (style) | 견종별 미용 스타일, 견종당 3개 (이름 TBD → `style_prompts.py`에서 관리) |
| 스타일 프롬프트 | AI 파이프라인에 전달되는 견종+스타일 특화 텍스트 |

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

## 작업 규칙

- **폐기된 접근을 부활시키지 말 것**
- **새 접근 제안 시 기존 실패 패턴과 충돌 여부 먼저 점검할 것**
- **변경은 최소 범위로 하고, 테스트 기준을 함께 제시할 것**

---

## 에이전트 사용 규칙

작업 성격에 따라 반드시 아래 서브에이전트를 사용할 것.

| 작업 유형 | 사용 에이전트 |
|-----------|--------------|
| FastAPI 라우터, DB 모델, AI 파이프라인, Cloudinary, backend/ 내 모든 파일 | `grooming-style-backend` |
| Next.js 페이지/컴포넌트, shadcn/ui, Tailwind CSS, TypeScript 타입, frontend/ 내 모든 파일 | `frontend-nextjs` |
| CLAUDE.md, CHANGELOG.md, README.md, DECISIONS.md 등 모든 .md 문서 생성·수정 | `md-doc-writer` |

- **독립적으로 실행 가능한 에이전트는 항상 단일 메시지에서 병렬로 호출할 것** — 순차 호출 금지
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
| 환경변수 | UPPER_SNAKE_CASE | `GOOGLE_API_KEY` |

### 커밋 메시지

```
feat: 말티즈 테디베어컷 스타일 프롬프트 추가
fix: 얼굴 보존 MAE 기준 초과 시 폴백 처리
refactor: gemini_pipeline 비동기 처리 개선
```

### 작업 규칙

- **작업 전 변경 계획을 3단계 이하로 제시** — 큰 작업도 단계를 먼저 요약한 뒤 진행
- **한 번에 큰 리팩터링 금지** — 기능 추가와 구조 변경을 한 PR에 섞지 않음
- **새 라이브러리 추가 전 이유 설명** — 기존 스택으로 해결 불가한 이유를 먼저 제시

### 패턴 규칙

- 구현 패턴 규칙은 `frontend/CLAUDE.md`, `backend/CLAUDE.md` 참고

---

## 문서 유지 규칙

- 대화 중 규칙·아키텍처·계약이 변경되면 **즉시 해당 영역의 CLAUDE.md를 수정**할 것 — 구두 합의만으로는 안 됨
- **프로젝트 진행 중 발견한 절대 규칙 및 실패로 인한 금지 사항은 해당 영역의 CLAUDE.md에 추가**할 것
- 프로젝트 공통 원칙으로 승격할 필요가 있을 때만 루트 CLAUDE.md에 반영할 것

## CHANGELOG 규칙

- 변경, 트러블슈팅, 시도 이력은 해당 영역의 CHANGELOG.md에 기록할 것
- CHANGELOG는 기록 전용이며, 현재 유효한 규칙만 CLAUDE.md에 반영할 것
- 상세 형식과 템플릿은 각 CHANGELOG.md 상단 가이드를 따른다
