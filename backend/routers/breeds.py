from fastapi import APIRouter

from models.breed import BreedInfo, StyleInfo
from services.style_prompts import get_all_breeds

router = APIRouter()


@router.get("/api/breeds", response_model=list[BreedInfo])
async def list_breeds() -> list[BreedInfo]:
    """지원하는 모든 견종과 각 견종의 미용 스타일 목록을 반환한다."""
    raw_breeds = get_all_breeds()
    return [
        BreedInfo(
            id=breed["id"],
            name=breed["name"],
            styles=[
                StyleInfo(
                    id=style["id"],
                    name=style["name"],
                    thumbnail_url=style.get("thumbnail_url"),
                )
                for style in breed["styles"]
            ],
        )
        for breed in raw_breeds
    ]
