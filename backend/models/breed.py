from pydantic import BaseModel


class StyleInfo(BaseModel):
    id: str
    name: str
    thumbnail_url: str | None = None


class BreedInfo(BaseModel):
    id: str
    name: str
    styles: list[StyleInfo]


class GenerateRequest(BaseModel):
    image_url: str
    breed_id: str
    style_id: str


class GenerateResponse(BaseModel):
    result_url: str
    processing_time: float
