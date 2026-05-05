# Grooming Style

강아지 사진을 업로드하면 견종별 미용 스타일로 AI가 변환해주는 웹 서비스.

비회원 1회성 · 웹 우선 · 포트폴리오 + 상업 서비스 전환 고려

---

## 실행

```bash
# Frontend (http://localhost:3000)
cd frontend && npm install && npm run dev

# Backend (http://localhost:8000)
cd backend && source venv/bin/activate && uvicorn main:app --reload
```

---

## API

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /api/breeds` | 견종 + 스타일 목록 반환 |
| `POST /api/generate` | `{ image_url, breed_id, style_id }` → `{ result_url, processing_time }` |

---

## 주요 문서

| 문서 | 용도 |
|------|------|
| `CLAUDE.md` | AI 에이전트 공통 규칙 |
| `backend/CLAUDE.md` | 백엔드·AI 파이프라인 규칙 (단일 진실 소스) |
| `frontend/CLAUDE.md` | 프론트엔드 구현 규칙 |
| `backend/CHANGELOG.md` | 변경·실험 이력 (Phase별) |
| `DECISIONS.md` | 폐기된 접근·재시도 금지 목록 |
| `TEST_RESULTS.md` | 수동 테스트 수치 기록 |
