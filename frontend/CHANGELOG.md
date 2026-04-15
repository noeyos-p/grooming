# Frontend Changelog

<!--
## 작성 가이드

**정렬**: 최신 날짜가 맨 위 · 같은 날짜 안에서도 최신 Phase가 위 (번호 높을수록 위)

**Phase 템플릿**

### N. 제목 — 한 줄로 무슨 작업인지 명시

배경이나 동기가 있으면 제목 바로 아래 한두 문장으로. (선택)

**추가**
- `경로/파일명` — 한 줄 설명

**수정**
- `경로/파일명` — 변경 내용 한 줄 요약

**삭제**
- `경로/파일명` — 삭제 이유

**트러블슈팅**
> **문제 제목**
> - 증상:
> - 원인:
> - 해결:

**시도한 접근**
> **시도 제목 — 실패/보류**
> - 방법:
> - 결과:
> - 대안:

**규칙**: 같은 작업 흐름은 같은 Phase에 묶을 것 · 트러블슈팅/시도는 반드시 blockquote 형식
-->

## 2026-04-15

### 2. 리팩토링 — 비활성 LoRA 관련 파일 archive 이동

LoRA 파이프라인 완전 폐기에 맞춰 프론트엔드 비활성 파일을 정리.

**이동 (frontend/archive/)**
- `app/admin/page.tsx` → `archive/app/admin/` — LoRA 학습 관리 대시보드 (비활성)
- `components/LoraTrainingPanel.tsx` → `archive/components/` — LoRA 학습 시작 모달 (비활성)

---

## 2026-04-15

### 1. CLAUDE.md 절대 규칙 역전파 — useRef guard 규칙 추가

CHANGELOG에 기록된 React StrictMode 이중 호출 실패 패턴을 CLAUDE.md 절대 규칙으로 역전파.

**수정**
- `frontend/CLAUDE.md` — ## 절대 규칙에 useEffect guard useRef 규칙 추가 (StrictMode 재마운트 시 useState guard 무력화 방지)

---

## 2026-04-14

### 2. StrictMode 이중 API 호출 수정 — useRef로 교체 + useCallback 추가

React StrictMode가 effect를 두 번 실행할 때 `useState`로 구현된 `started` 플래그가 리셋되어
`generate()` API 호출이 두 번 발생하는 문제 수정. 첫 번째 결과(INPAINT)가 잠시 보였다가
두 번째 결과(Gemini fallback)로 교체되던 현상 해결.

**수정**
- `app/result/page.tsx` — `const [started, setStarted] = useState(false)` → `const startedRef = useRef(false)` 교체. `useRef`는 StrictMode 언마운트/재마운트 사이클에서 값을 유지해 이중 호출 차단
- `hooks/useGenerate.ts` — `generate` 함수를 `useCallback(fn, [])` 으로 래핑. 렌더마다 함수 레퍼런스가 바뀌어 `useEffect` 의존성 배열이 불필요하게 트리거되던 문제 해결

**트러블슈팅**

> **결과 페이지에서 Image 1이 잠시 보였다가 Image 2로 교체되는 현상**
> - 증상: 결과 페이지 로딩 후 한 결과가 표시됐다가 시간이 지나면 다른 결과로 바뀜
> - 원인: React StrictMode가 개발 모드에서 effect를 마운트→언마운트→재마운트 순으로 두 번 실행. `useState(false)`는 재마운트 시 `false`로 리셋되어 `generate()` 두 번 호출 → API call 2개 발생 → 결과 1(INPAINT) 표시 후 결과 2(Gemini fallback)로 덮어쓰기
> - 해결: `useRef`로 교체 — ref 값은 StrictMode 사이클에서도 유지되므로 재마운트 후 `startedRef.current = true` 상태 보존 → 두 번째 effect 즉시 종료

---

### 1. Admin 대시보드 + LoRA 학습 패널 추가

**추가**
- `app/admin/page.tsx` — LoRA 학습 관리 대시보드
  - 11개 견종 × 3개 스타일 = 33개 카드 그리드 (반응형: 1/2/3열)
  - 학습 상태 배지: 미학습 / 학습중(애니메이션) / 완료 / 실패
  - 학습 완료 후 30초 간격 자동 폴링 (`GET /api/admin/train/{id}/status`)
  - "전체 새로고침" 버튼으로 레지스트리 수동 갱신
- `components/LoraTrainingPanel.tsx` — ZIP 업로드 학습 시작 모달
  - 클릭 및 드래그앤드롭 양방향 지원 (.zip 전용, 200MB 제한)
  - `POST /api/admin/train` multipart 요청 (breed_id, style_id, images)
  - 업로드 중 로딩 상태, 에러 인라인 표시

**수정**
- `app/result/page.tsx` — 결과 페이지 개선
- `hooks/useImageUpload.ts` — 이미지 업로드 훅 개선

**트러블슈팅**

> **결과 페이지에서 이전 이미지가 먼저 보였다가 새 이미지로 교체되는 현상**
> - 증상: 같은 견종+스타일 조합을 재실행하면 결과 페이지에서 이전 결과가 먼저 표시되다가 새 결과로 바뀜
> - 원인: 백엔드에서 Cloudinary `public_id`를 `{breed_id}_{style_id}` 고정값으로 사용 → `overwrite=True`로 덮어써도 CDN 엣지 캐시가 만료 전까지 구버전을 서빙
> - 해결: 백엔드 `ai_pipeline.py` Cloudinary 업로드 호출에 `invalidate=True` 추가 → 업로드 즉시 CDN 캐시 무효화

---

## 2026-04-09

### 1. 초기 세팅 — Next.js 14 App Router 기반 프로젝트 초기화

**추가**
- `app/page.tsx` — 메인 페이지 (업로드 + 견종/스타일 선택)
- `app/result/page.tsx` — 결과 페이지 초기 버전
- `app/api/breeds/` — 견종 목록 BFF API route
- `app/api/generate/` — 변환 요청 BFF API route
- `components/BreedSelector.tsx` — 견종 선택 컴포넌트
- `components/StyleSelector.tsx` — 스타일 선택 컴포넌트
- `components/ImageUploader.tsx` — 이미지 업로드 컴포넌트
- `components/ResultDisplay.tsx` — 결과 표시 컴포넌트
- `hooks/useBreedStyles.ts` — 견종/스타일 데이터 훅
- `hooks/useGenerate.ts` — 변환 요청 훅
- Next.js 14 App Router + TypeScript + Tailwind CSS + shadcn/ui 기반 프로젝트 초기화
