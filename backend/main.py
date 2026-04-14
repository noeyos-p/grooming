import logging
import os

from dotenv import load_dotenv

# load_dotenv()를 라우터 import 전에 호출해야 한다.
# ai_pipeline.py의 cloudinary.config()가 모듈 로드 시점에 실행되므로
# 그 전에 환경변수가 설정되어 있어야 함.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import breeds, generate, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

app = FastAPI(
    title="Grooming Style API",
    description="강아지 사진을 견종별 미용 스타일로 AI 변환해주는 서비스",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# CORS — 프론트엔드 개발 서버 허용
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------
app.include_router(breeds.router)
app.include_router(generate.router)
app.include_router(admin.router)


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
