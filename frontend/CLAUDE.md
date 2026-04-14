# Frontend — 개발 환경 설정

> **코드 작업 전 필독**: `CHANGELOG.md`를 먼저 읽어 과거 트러블슈팅·시도 이력을 파악할 것.

## 기술 스택
- Next.js 14+ (App Router)
- TypeScript
- Tailwind CSS + shadcn/ui

## 설치 및 실행

```bash
npm install
npm run dev        # http://localhost:3000
npm run build
npm run lint
```

## 환경변수 (`frontend/.env.local`)

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_CLOUDINARY_CLOUD_NAME=
```

## 디렉토리 구조

```
frontend/
├── app/
│   ├── page.tsx              # 메인 페이지 (업로드 + 견종/스타일 선택)
│   ├── result/page.tsx       # 결과 페이지
│   └── api/                  # Next.js API routes (BFF)
├── components/
│   ├── ImageUploader.tsx     # 사진 업로드 컴포넌트
│   ├── BreedSelector.tsx     # 견종 선택 컴포넌트
│   ├── StyleSelector.tsx     # 스타일 선택 컴포넌트
│   └── ResultDisplay.tsx     # 결과 이미지 + 다운로드
└── public/
    └── styles/               # 스타일 예시 썸네일 이미지
```

---

## 절대 규칙

- **로그인/인증 코드 추가 금지** — 비회원 1회성 서비스로 설계됨
- **백엔드 직접 호출 금지** — 반드시 `app/api/` BFF 라우트를 통할 것 (CORS·보안 처리 일원화)
- **`any` 타입 사용 금지** — 명시적 TypeScript 타입 필수
- **견종·스타일 데이터 하드코딩 금지** — `GET /api/breeds`로 fetch해서 사용할 것

---

## 컴포넌트/훅 패턴

- **비즈니스 로직은 custom hook으로 분리** — UI 컴포넌트에 직접 작성 금지 (React Native 전환 대비)
  - 예: `hooks/useImageUpload.ts`, `hooks/useBreedStyles.ts`, `hooks/useGenerate.ts`
- 컴포넌트는 `components/` 아래, 훅은 `hooks/` 아래에 배치
- 페이지 컴포넌트(`app/*.tsx`)는 레이아웃·조합만 담당, 로직 없음
- Server Component 기본 사용 — 클라이언트 상태·이벤트 필요 시에만 `'use client'` 명시

---

## UX 구현 규칙

- **파일 검증 필수**: 업로드 시 클라이언트에서 크기(5MB 이하) 및 형식(JPG/PNG) 검사
- **로딩 상태 필수**: Replicate API 콜드 스타트 대비 변환 중 로딩 UI 표시 (15~30초 예상)
- **에러 메시지**: 기술적 오류 문자열 그대로 노출 금지 — 사용자 친화적 문구로 변환
- **결과 페이지**: 다운로드 버튼 + 다시 시도 버튼 반드시 제공

---

## 스타일링 규칙

- Tailwind CSS 유틸리티 클래스 우선 사용 — 커스텀 CSS 최소화
- UI 컴포넌트는 **shadcn/ui 우선** — 직접 구현 전 shadcn/ui 목록 먼저 확인
- 반응형: 모바일 우선 (`sm` → `md` → `lg`) 순서로 작성
- 인라인 스타일(`style={{}}`) 사용 금지

### 컬러 시스템

**기본 원칙: 흑백 + 포인트 컬러 1가지만 사용**

| 역할 | 값 |
|------|----|
| 배경·기본 텍스트 | 흑백 계열 (`white`, `black`, `neutral-*`) |
| 포인트 컬러 | `#00D3D7` (CSS 변수: `--color-accent`) |

**포인트 컬러 사용 방식** — opacity로 단계 표현:

```css
/* globals.css */
:root {
  --color-accent: #00D3D7;
}
```

```
100%  bg-[#00D3D7]                     — 주요 CTA 버튼, 강조 요소
 90%  bg-[#00D3D7]/90                  — hover 상태
 50%  bg-[#00D3D7]/50                  — 비활성 상태, 보조 배경
 20%  bg-[#00D3D7]/20                  — 연한 배경, 태그
 10%  bg-[#00D3D7]/10                  — 가장 연한 배경 틴트
```

- **포인트 컬러 외 유채색 추가 금지** — 다른 색상이 필요한 경우 opacity 단계로 해결
- 텍스트 색상도 동일 규칙: `text-[#00D3D7]`, `text-[#00D3D7]/70` 등으로 표현
- 그레이스케일은 Tailwind `neutral-*` 토큰 사용 (`gray-*` 혼용 금지)

