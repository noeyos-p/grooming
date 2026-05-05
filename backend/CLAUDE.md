# Backend — 개발 환경 설정

## 작업 전 확인 규칙

- 모든 작업 전에 `CHANGELOG.md` 전체를 읽지 말 것.
- 현재 작업이 AI 파이프라인, bbox 계약, 색상 보존, 얼굴 보존과 직접 관련될 때만
  `CHANGELOG.md`의 관련 Phase 또는 실패 패턴 요약을 참고할 것.
- 현재 유효한 규칙의 1차 진실 소스는 이 `CLAUDE.md`다.

---

## 절대 규칙

- **Gemini API 직접 호출 금지** — 반드시 `gemini_pipeline.py`를 통할 것
- **Replicate API 직접 호출 금지** — 반드시 `ai_pipeline.py`를 통할 것
- **로그인/인증 코드 추가 금지** — 비회원 1회성 서비스로 설계됨
- **견종·스타일 데이터 하드코딩 금지** — `style_prompts.py`에서만 관리

### 현재 파이프라인 알려진 동작

- **눈·코 위치(bbox)는 gating 통과 시 합성으로 보존됨** — mask/drift 기준 미달 샘플은 합성 스킵, Gemini 결과 유지
- **입(mouth)은 기본 OFF** — `FACE_PRESERVE_MOUTH=False`. 원형 얼굴견에서 입안 검정/혀 분홍으로 마스크 번짐(Phase 30)
- **털 색상 및 눈 색깔 변경 발생 중** — 스타일 변환 범위가 색상까지 영향을 줌 (미해결)

### AI 파이프라인 — 반복 실패에서 확인된 구조적 금지 규칙

- **AFFINE·좌표 정렬 기반 원본-생성 이미지 합성 재도입 금지** — ghost 아티팩트의 구조적 원인으로 확인됨 (Phase 14)
- **모델 출력 bbox를 픽셀 좌표로 직접 취급 금지** — Gemini/SAM 결과는 정수 퍼센트 좌표 0–100 계약으로 받고, 실제 픽셀 변환은 단일 경계에서만 수행. float·픽셀·0.0–1.0 좌표를 Gemini 프롬프트에 섞어 요청 금지 (Phase 21)
- **후처리 색상 보정 코드 재추가 금지** — `_color_correct_result()` 류 LUT·히스토그램·채널 스케일 보정은 반복 실험에서 역효과 확인. 색상 보존은 프롬프트(ABSOLUTE COLOR RULE)와 입력 제약으로 해결. 색상 측정·로그는 허용 (Phase 18–21)
- **`style_prompts.py` 스타일 설명에 털 색상어 추가 금지** — "white fur", "black coat" 같은 색상 수식어는 Gemini가 원본 털 색을 바꾸는 원인. 색상 보존은 공통 프롬프트의 ABSOLUTE COLOR RULE에만 둔다 (Phase 16)
- **얼굴 보존 기준: `scripts/test_face_preservation.py` 기준 MAE ≤ 25.0** — 파이프라인 수정 후 반드시 통과 확인. 기준은 눈/코 개별 파트 MAE 평균. anchor-inpaint v1.0에서는 hard_preserve 영역 MAE 기준 ~2.1로 매우 낮음(Phase 34 실측). gemini 경로는 평균 55로 기준 위반 상태이므로 anchor가 기본 라우팅인 한 Face MAE 기준은 anchor 한정으로 계속 유효
- **얼굴 파트 합성은 gating 우선** — ellipse fallback / mask 면적 비율 > 0.65 / drift 비율 > 0.6 / active pixels < 50 중 하나라도 해당하면 해당 파트 합성 스킵. drift 임계는 dst_parts 탐지로 paste 위치가 보정되므로 0.25보다 완화(Phase 30.1) (Phase 30, 30.1)
- **mouth(입) 기본 보존 금지** — `FACE_PRESERVE_MOUTH=False` 가 기본. 원형 얼굴견에서 입안 검정/혀 분홍으로 마스크가 번지는 구조적 실패. 재활성화는 실측 데이터 동반 시만 (Phase 30)
- **gating 임계값은 상수로 중앙화** — `_MIN_MASK_PIXELS`, `_MAX_MASK_AREA_RATIO`, `_MAX_DRIFT_RATIO`, `_EYE_PADDING_RATIO`, `_DEFAULT_PADDING_RATIO`, `_EYE_MASK_BLUR_COEF`, `_DEFAULT_MASK_BLUR_COEF`, `_EYE_MASK_BLUR_MAX` 는 `gemini_pipeline.py` 상단에만 둔다. 함수 내부 매직넘버 금지 (Phase 30, 30.2)
- **drift 계산은 비율 스케일만** — 원본→결과 bbox 중심 매핑은 `(orig_w, orig_h)→(res_w, res_h)` 선형 비율만 사용. affine/회전/perspective 금지 (Phase 14, Phase 30)

### AI 파이프라인 — Anchor-Inpaint v1.0 (Phase 34)

- **품질 실패(QualityFailure) 시 Gemini 전체재생성 경로로 폴백 금지** — Phase 34. 폴백하는 순간 ghost 경로(Phase 14) 부활 위험. QualityFailure는 422 명시 실패로 반환.
- **앵커 geometry(좌표·bbox)는 EVF-SAM2 text-grounded 결과만 사용** — Phase 32~33에서 Gemini VLM geometry의 regime-switching이 확인되어 격리. 좌표 산출에 다른 CV 모델 혼용 금지. 단, 전신 실루엣 마스크는 rembg(u2net) — 평가 게이트(silhouette_iou_whole)와 동일 함수(`silhouette_iou.extract_foreground_mask`)를 edit 마스크 합성에서도 재사용한다 (Phase 34.3).
- **앵커는 bbox 사각형(v1.0), contour 앵커는 v1.1에서 도입** — Phase 34 v1.0은 구조 검증 단계로서 bbox 사각형만 사용. contour 정교화는 v1.1 승인 통과 후.
- **soft band는 Phase 32 Probe 불통으로 v1.0 미적용** — 2-layer binary 고정. FLUX.1-fill이 grayscale 중간값 gradient를 유도 신호로 사용하지 않음. soft band 재도입은 다른 모델 옵션 검토 후 v1.1에서.
- **edit 마스크는 전신 실루엣 ∩ ¬dilate(anchor_bbox, r2)** — Phase 34.3. 이전의 `head_bbox` 한정 방식은 미용이 머리 위쪽에만 적용되는 구조적 한계로 폐기. `_detect_full_head_bbox()` 호출은 anchor_inpaint 파이프라인에서 제거됨. (head_bbox 함수 자체는 `inpaint_pipeline.py`의 다른 경로·`tests/run_*_probe.py`·`test_head_bbox_parser.py`에서 계속 사용)
- **edit_zone_change baseline_p25 = 20.155** (Phase 34.2 Step 2 · N=15 · 5 이미지 × 3 스타일 gemini 샘플, `/tmp/edit_baseline_multi.jsonl`) — 2026-04-23 N=16 anchor 배치 기준 pass rate 10/11. 경계 실패 1건(`bichon/round_cut` edit=19.46)은 스타일 특성상 low-change로 확인되어 기록만 유지 (silhouette 0.921 상위권 + face MAE 정상 + gemini 동 스타일 min 15.91). 참고: IMG_9827(gemini 전체재생성 outlier) 제외 N=12 서브셋 p25=18.754 — **정식 기준이 아닌 민감도 분석값**. **Phase 34.3 변경(전신 실루엣 기반 edit 마스크)으로 edit 영역이 확대되었으므로 baseline 재산정 필요 — N=16 재배치 트리거 대기 중. 재산정 완료 전까지는 단건 단위로만 검증 가능.**
- **Head shape IoU (bbox 기반) deprecate — 구조 안정 지표는 silhouette_iou_whole** (Phase 34.2). `services/silhouette_iou.py` rembg(u2net) 기반 전경 마스크 IoU 사용. N=16 배치에서 bbox 유효 1/16(6.25%) vs silhouette 16/16(100%) 확인. **승인선 `silhouette_iou_whole ≥ 0.70` 확정** — 2026-04-23 N=16 재배치에서 anchor 11/11 전원 통과(min 0.776). head_crop IoU는 참고치로만 유지
- **`_detect_full_head_bbox()` 파서는 xyxy와 box_2d 두 스키마 모두 수용** — Gemini 2.5 Flash가 프롬프트 스키마를 무시하고 native `{"box_2d": [ymin, xmin, ymax, xmax]}` 를 반환하는 현상이 확인됨(Phase 34.1). 파서는 `_parse_head_bbox_payload()` 단일 진입점 사용, 스케일 자동 판정(max>100 → 1000)

---

## 기술 스택
- FastAPI (Python 3.11+)
- PostgreSQL
- Google Gemini 2.5 Flash Image Generation (현재 사용 중인 AI 파이프라인)
- Cloudinary (이미지 저장)

## 현재 라우팅

- `generate.py`: Imagen-first → anchor_inpaint → (QualityFailure=422, RuntimeError=Gemini 폴백)
  - Vertex AI Imagen 튜닝 모델이 `ready` 상태이면 Imagen 파이프라인 사용
  - 미준비 시 anchor_inpaint (EVF-SAM2 + FLUX.1-fill) 기본 경로로 진입
  - QualityFailure (geometry 탐지 실패): 422 명시 실패, Gemini 폴백 금지 (Phase 34 — ghost 경로 부활 방지)
  - RuntimeError (FLUX/네트워크): Gemini 폴백만 허용 (시스템 오류 폴백)
- `anchor_inpaint_pipeline.py`: Phase 34 기본 생성 파이프라인 — EVF-SAM2 기하 + FLUX.1-fill inpainting
- `gemini_pipeline.py`: Imagen 미준비 시 anchor_inpaint 폴백 및 runtime 오류 폴백 대상
- `inpaint_pipeline.py`: 실험/대체 경로 (fal.ai FLUX.1-fill)
- `vertex_imagen_pipeline.py`: Imagen 튜닝 모델 준비된 경우만 진입

## 설치 및 실행

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload  # http://localhost:8000
```

## 환경변수 (`backend/.env`)

```
GOOGLE_API_KEY=
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
DATABASE_URL=postgresql://...

# LangSmith 트레이싱 (선택) — 미설정 시 추적 비활성화 · no-op
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=grooming-style
```

- LangSmith는 `services/tracing.py`의 pass-through 래퍼를 통해 주입됨. 환경변수 또는 패키지가 없으면 데코레이터는 원 함수를 그대로 호출한다.
- raw `image_bytes`와 `gemini_client`는 sanitize되어 payload에 포함되지 않는다.

## 디렉토리 구조

```
backend/
├── main.py                          # FastAPI 앱 진입점
├── config/
│   └── imagen_registry.json         # Vertex AI Imagen 튜닝 상태 레지스트리
├── routers/
│   ├── generate.py                  # POST /api/generate (Imagen-first → Gemini fallback)
│   └── breeds.py                    # GET /api/breeds
├── services/
│   ├── anchor_inpaint_pipeline.py   # Phase 34 기본 생성 파이프라인 — EVF-SAM2 + FLUX.1-fill
│   ├── evf_sam_geometry.py          # EVF-SAM2 text-grounded 기하 탐지
│   ├── gemini_pipeline.py           # Gemini 이미지 생성 파이프라인 (anchor 폴백 대상)
│   ├── image_utils.py               # 공통 유틸 — MIME 감지·HEIC 변환
│   ├── inpaint_pipeline.py          # fal.ai FLUX.1-fill 기반 인페인팅 파이프라인
│   ├── silhouette_iou.py            # rembg 기반 전경 마스크 IoU (Phase 34.2 구조 안정 지표)
│   ├── style_prompts.py             # 견종+스타일별 프롬프트 딕셔너리 (단일 진실 소스)
│   ├── tracing.py                   # LangSmith 패스스루 래퍼 — 미설정 시 no-op
│   ├── vertex_imagen_pipeline.py    # Vertex AI Imagen 3 추론 파이프라인
│   └── vertex_imagen_training.py    # Vertex AI Imagen 3 Style Tuning 잡 관리
├── models/
│   └── breed.py                     # Pydantic 모델 (BreedInfo, StyleInfo, GenerateRequest)
├── tests/
│   ├── test_anchor_inpaint_pipeline.py  # pytest — anchor-inpaint 파이프라인
│   ├── test_gemini_pipeline.py          # pytest — gemini 파이프라인
│   ├── test_generate_router.py          # pytest — generate 라우터
│   ├── test_head_bbox_parser.py         # pytest — head bbox 파서 스키마 수용
│   ├── test_silhouette_iou.py           # pytest — silhouette IoU 계산
│   ├── test_style_prompts.py            # pytest — 스타일 프롬프트 유효성
│   ├── run_anchor_inpaint.py            # 수동 — anchor-inpaint 단건 실행
│   ├── run_anchor_inpaint_batch.py      # 수동 — anchor-inpaint 배치 실행
│   ├── run_anchor_batch_smoke.py        # 수동 — 배치 스모크 테스트
│   ├── run_evf_sam_geometry.py          # 수동 — EVF-SAM 기하 검증
│   ├── run_evf_sam2_probe.py            # 수동 — EVF-SAM2 프로브
│   ├── run_face_parts.py                # 수동 — 얼굴 파트 bbox 검증
│   ├── run_face_preservation.py         # 수동 — MAE 기반 얼굴 보존 평가
│   ├── run_geometry_median_probe.py     # 수동 — 기하 중앙값 프로브
│   ├── run_geometry_variance.py         # 수동 — 기하 분산 측정
│   ├── run_head_bbox_diagnosis.py       # 수동 — head bbox 진단
│   ├── run_inpainting_models.py         # 수동 — inpainting 모델 비교
│   ├── run_silhouette_probe.py          # 수동 — silhouette IoU 프로브
│   ├── run_soft_mask_probe.py           # 수동 — soft mask 프로브
│   ├── run_2stage_crop_probe.py         # 수동 — 2-stage crop 프로브
│   └── make_evf_sam2_overlays.py        # 수동 — EVF-SAM2 오버레이 시각화
├── archive/                         # 폐기된 파이프라인 참조용 보관 (실행 경로에서 제외)
│   ├── services/ (ai_pipeline.py, lora_training.py, segmentation.py)
│   ├── routers/ (admin.py)
│   └── config/ (lora_registry.json)
└── requirements.txt
```

---

## 폴더 규칙

| 폴더 | 역할 | 금지 사항 |
|------|------|-----------|
| `routers/` | FastAPI 엔드포인트 정의 — 요청 파싱·응답 직렬화만 | 비즈니스 로직 직접 작성 금지 — 반드시 `services/`에 위임 |
| `services/` | 비즈니스 로직·AI 파이프라인 — 핵심 처리 담당 | HTTP 요청/응답 객체 직접 참조 금지 |
| `models/` | Pydantic 모델 — 요청·응답·DB 스키마 정의 | 로직 포함 금지 — 순수 데이터 구조만 |
| `config/` | JSON 설정 파일 — 레지스트리·환경 무관 설정 | 코드 파일 배치 금지 |
| `tests/` | 자동화 테스트 + 수동 검증 스크립트 — pytest 자동 수집 대상은 `test_*.py`, 수동 실행 스크립트는 `run_*.py` 접두사 사용 | - |

---

## CHANGELOG 규칙

- 변경, 트러블슈팅, 시도 이력은 `backend/CHANGELOG.md`에 기록할 것
- CHANGELOG는 기록 전용이며, 현재 유효한 규칙만 이 CLAUDE.md에 반영할 것
- 상세 형식과 템플릿은 `backend/CHANGELOG.md` 상단 가이드를 따른다

