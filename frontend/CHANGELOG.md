# Frontend Changelog

## 2026-04-14

### 추가
- `app/admin/page.tsx` — LoRA 학습 관리 대시보드
  - 11개 견종 × 3개 스타일 = 33개 카드 그리드 (반응형: 1/2/3열)
  - 학습 상태 배지: 미학습 / 학습중(애니메이션) / 완료 / 실패
  - 학습 완료 후 30초 간격 자동 폴링 (`GET /api/admin/train/{id}/status`)
  - "전체 새로고침" 버튼으로 레지스트리 수동 갱신
- `components/LoraTrainingPanel.tsx` — ZIP 업로드 학습 시작 모달
  - 클릭 및 드래그앤드롭 양방향 지원 (.zip 전용, 200MB 제한)
  - `POST /api/admin/train` multipart 요청 (breed_id, style_id, images)
  - 업로드 중 로딩 상태, 에러 인라인 표시

### 수정
- `app/result/page.tsx` — 결과 페이지 개선 (11:36)
- `hooks/useImageUpload.ts` — 이미지 업로드 훅 개선 (11:38)

### 트러블슈팅
#### 결과 페이지에서 이전 이미지가 먼저 보였다가 새 이미지로 교체되는 현상
- **증상**: 같은 견종+스타일 조합을 재실행하면 결과 페이지에서 이전 결과가 먼저 표시되다가 새 결과로 바뀜
- **원인**: 백엔드에서 Cloudinary `public_id`를 `{breed_id}_{style_id}` 고정값으로 사용 → `overwrite=True`로 덮어써도 CDN 엣지 캐시가 만료 전까지 구버전을 서빙
- **해결**: 백엔드 `ai_pipeline.py` Cloudinary 업로드 호출에 `invalidate=True` 추가 → 업로드 즉시 CDN 캐시 무효화

---

## 2026-04-09

### 추가 (초기 세팅)
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
