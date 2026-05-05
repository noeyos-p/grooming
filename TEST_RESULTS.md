# Test Results

수동 실행 스크립트(`run_*.py`) 및 기타 테스트를 실행할 때마다 수치 결과를 여기에 기록한다.
테스트 이미지는 항상 `~/Downloads/IMG_7641.jpg` 사용.

---

## Face Preservation (run_face_preservation.py)

- 기준(THRESHOLD_MAE): **25.0** — 이 이하면 PASS
- MAE 종류: `mae_dark` (어두운 픽셀 한정 평균 절대오차, 0~255)

| 날짜 | breed/style | left_eye | right_eye | nose | 전체 MAE | 판정 | 비고 |
|------|-------------|----------|-----------|------|----------|------|------|
| 2026-04-15 | maltese/teddy_cut | 11.45 | 22.88 | 18.90 | 17.7 | PASS | 눈 paste 방식 변경 후 첫 측정 |
| 2026-04-20 | maltese/teddy_cut | 12.40 | 12.26 | 17.22 | 14.8 | PASS | pyramid blend 롤백 + ellipse fallback 시 합성 스킵 |
| 2026-04-20 | maltese/teddy_cut | skip:drift_too_large | skip:drift_too_large | ok | 14.0 | PASS | gemini_calls=2 |


### Detail (part-level meta)

| 날짜 | part | ellipse_fb | mask_area | drift | active_px | skip |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-04-20 | left_eye | False | 0.12 | 0.35 | 385 | drift_too_large |
| 2026-04-20 | right_eye | False | 0.40 | 0.49 | 1256 | drift_too_large |
| 2026-04-20 | nose | False | 0.21 | 0.11 | 749 |   |
| 2026-04-20 | mouth | — | — | — | — | disabled |
| 2026-04-20 | maltese/teddy_cut | skip:active_pixels_low | skip:active_pixels_low | skip:active_pixels_low | 88.5 | FAIL | gemini_calls=2 |
| 2026-04-20 | left_eye | True | 0.00 | 3.75 | 0 | active_pixels_low |
| 2026-04-20 | right_eye | True | 0.00 | 4.52 | 0 | active_pixels_low |
| 2026-04-20 | nose | True | 0.00 | 3.19 | 0 | active_pixels_low |
| 2026-04-20 | mouth | — | — | — | — | disabled |
| 2026-04-20 | maltese/teddy_cut | ok | ok | ok | 53.1 | FAIL | gemini_calls=1 |
| 2026-04-20 | left_eye | False | 0.55 | 0.38 | 1110 |   |
| 2026-04-20 | right_eye | False | 0.52 | 0.40 | 1049 |   |
| 2026-04-20 | nose | False | 0.29 | 0.04 | 1519 |   |
| 2026-04-20 | mouth | — | — | — | — | disabled |
| 2026-04-20 | maltese/teddy_cut | ok | ok | ok | 78.1 | FAIL | gemini_calls=1 |
| 2026-04-20 | left_eye | False | 0.29 | 0.60 | 1921 |   |
| 2026-04-20 | right_eye | False | 0.48 | 0.53 | 3139 |   |
| 2026-04-20 | nose | False | 0.48 | 0.15 | 1801 |   |
| 2026-04-20 | mouth | — | — | — | — | disabled |

### 결과 이미지 링크

| 날짜/시각 | breed/style | MAE | 판정 | 업로드 | 결과 |
|-----------|-------------|----:|------|--------|------|
| 2026-04-20 13:44 | maltese/teddy_cut | 26.5 | FAIL | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776660185/grooming-style/uploads/k8a84nkds9jqafm6koam.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776660263/grooming-results/ittaiq8ekpyjxvvbieng.jpg) |
| 2026-04-20 13:48 | maltese/teddy_cut | 73.9 | FAIL | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776660433/grooming-style/uploads/iej1zyd0r3o3qwtk2yi9.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776660526/grooming-results/qohwixarzm6mhxzb0fvs.jpg) |
| 2026-04-20 14:00 | maltese/teddy_cut | 14.8 | PASS | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776661150/grooming-style/uploads/ursgozk2pdohr0zepv6s.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776661229/grooming-results/fjt6s5lhrb8qjhdninvf.jpg) |
| 2026-04-20 14:27 | maltese/teddy_cut | 14.0 | PASS | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776662763/grooming-style/uploads/ixglbspcr7llyrkszktj.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776662862/grooming-results/anq8lv6koseau5xxhtie.jpg) |
| 2026-04-20 14:43 | maltese/teddy_cut | 88.5 | FAIL | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776663712/grooming-style/uploads/sieru25gwbdwu693x6jd.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776663805/grooming-results/ukxxriwrmlfjgzhjlkzn.jpg) |
| 2026-04-20 14:45 | maltese/teddy_cut | 53.1 | FAIL | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776663881/grooming-style/uploads/xayu3k76xj0cb4xhnzfr.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776663954/grooming-results/sps7jennvi1j2r1qrbta.jpg) |
| 2026-04-20 15:21 | maltese/teddy_cut | 78.1 | FAIL | [업로드](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776665993/grooming-style/uploads/d1eq8bzvrw40y1vqayqo.jpg) | [결과](https://res.cloudinary.com/dubnzx8ew/image/upload/v1776666071/grooming-results/unos5juloi07ouaizs3a.jpg) |

---

## Anchor-Inpaint Pipeline (run_anchor_inpaint.py)

- 파이프라인: `anchor_inpaint` (EVF-SAM2 + FLUX.1-fill) vs `gemini` (baseline)
- 이미지: IMG_7641.jpg 고정
- 지표: face_mae(hard-preserve 영역), edit_zone_change(편집 영역 변화), **silhouette_iou_whole(구조 안정 — Phase 34.2 승인 지표)**, head_shape_iou(deprecated · 참고치), hard_preserve_area_ratio, mode, position_gate_ok, label_swapped

**확장 샘플 (N=16, 2026-04-23 재실측 — silhouette_iou_whole 통합)**

| 날짜 | 이미지 | pipeline | breed/style | mode | face_mae | edit_zone_change | silhouette_iou_whole | head_shape_iou (deprecated) | iou_detect_fail | hard_preserve_ratio |
|------|--------|----------|-------------|------|---:|---:|---:|---:|------|-:|
| 2026-04-23 | IMG_7641 | anchor | maltese/teddy_cut | full | 2.130 | 25.78 | 0.819 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | poodle/teddy_cut | full | 2.131 | 20.72 | 0.958 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | bichon/round_cut | full | 2.131 | 19.46 | 0.921 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | maltipoo/teddy_cut | full | 2.132 | 39.58 | 0.806 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | pomeranian/bear_cut | full | 2.130 | 39.80 | 0.805 | 0.439 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | yorkshire/puppy_cut | full | 2.130 | 25.72 | 0.783 | 0.074 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | shih_tzu/puppy_cut | full | 2.130 | 20.99 | 0.842 | 0.779 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | papillon/natural_cut | full | 2.130 | 21.91 | 0.776 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | spitz/natural_cut | full | 2.131 | 33.31 | 0.853 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | mini_bichon/round_cut | full | 2.130 | 30.86 | 0.800 | 0.000 | true | 0.00957 |
| 2026-04-23 | IMG_7641 | anchor | bedlington/traditional_cut | full | 2.131 | 41.11 | 0.785 | 0.667 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | gemini | maltese/teddy_cut | (N/A) | 48.454 | 53.02 | 0.890 | 0.613 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | gemini | poodle/teddy_cut | (N/A) | 48.218 | 35.42 | 0.855 | 0.556 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | gemini | bichon/round_cut | (N/A) | 35.005 | 19.13 | 0.924 | 0.593 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | gemini | pomeranian/bear_cut | (N/A) | 87.039 | 40.24 | 0.782 | 0.742 | false | 0.00957 |
| 2026-04-23 | IMG_7641 | gemini | shih_tzu/puppy_cut | (N/A) | 67.190 | 33.52 | 0.872 | 0.000 | true | 0.00957 |

**요약 통계**

**anchor-inpaint (N=11)**

| 지표 | mean | median | p25 | p75 | min | max |
|------|---:|---:|---:|---:|---:|---:|
| face_mae | 2.131 | 2.130 | 2.130 | 2.131 | 2.130 | 2.132 |
| edit_zone_change | 29.022 | 25.784 | 21.447 | 36.444 | 19.460 | 41.110 |
| silhouette_iou_whole | 0.832 | 0.806 | 0.793 | 0.847 | 0.776 | 0.958 |
| head_shape_iou (deprecated) | 0.178 | 0.000 | 0.000 | 0.256 | 0.000 | 0.779 |
| hard_preserve_area_ratio | 0.00957 | 0.00957 | 0.00957 | 0.00957 | 0.00957 | 0.00957 |

detect_fail 4/11 · silhouette_lt_0p70_count **0/11** · mode 전원 full

**gemini baseline (N=5)**

| 지표 | mean | median | p25 | p75 | min | max |
|------|---:|---:|---:|---:|---:|---:|
| face_mae | 57.181 | 48.454 | 48.218 | 67.190 | 35.005 | 87.039 |
| edit_zone_change | 36.266 | 35.421 | 33.522 | 40.238 | 19.127 | 53.022 |
| silhouette_iou_whole | 0.865 | 0.872 | 0.855 | 0.890 | 0.782 | 0.924 |
| head_shape_iou (deprecated) | 0.501 | 0.593 | 0.556 | 0.613 | 0.000 | 0.742 |

**임계 판정 (Phase 34.2 확정)**

- **Face MAE ≤ 25.0**: anchor **11/11 통과** (mean=2.131), gemini 0/5 미달 (mean=57.181)
- **silhouette_iou_whole ≥ 0.70** (v1.1 Step 1 승인 지표): anchor **11/11 통과** (min=0.776), gemini 5/5 통과 (min=0.782)
- **Edit-zone change**: anchor p25=21.447, gemini baseline p25 후보 **33.522** — 단일 이미지 분포이므로 Step 2에서 다중 이미지 재산정 예정
- **head_shape_iou (deprecated)**: detect_fail 5/16. 구조 안정 지표로는 사용하지 않음 (Phase 34.2에서 silhouette로 교체)

---

## Edit-zone Change Baseline — 다중 이미지 재산정 (Phase 34.2 Step 2)

- 스크립트: `backend/tests/run_edit_baseline_multi.py`
- JSONL: `/tmp/edit_baseline_multi.jsonl`
- 목적: 기존 baseline_p25(29.942 — IMG_7641 단일 이미지 × 5 gemini)가 이미지 분포에 과적합. 다중 이미지 × 대표 스타일로 재산정
- 설계: 5 이미지 × 3 스타일 = 15 gemini 샘플
- 이미지: IMG_7641, IMG_2749, IMG_9712, IMG_9827, IMG_9999
- 스타일: maltese/teddy_cut, bichon/round_cut, pomeranian/bear_cut

**샘플별 결과 (N=15, 2026-04-23)**

| 이미지 | breed/style | face_mae | edit_zone_change | silhouette_iou_whole |
|--------|-------------|---:|---:|---:|
| IMG_7641.jpg | maltese/teddy_cut | 16.824 | 11.45 | 0.970 |
| IMG_7641.jpg | bichon/round_cut | 41.274 | 26.45 | 0.912 |
| IMG_7641.jpg | pomeranian/bear_cut | 35.108 | 28.00 | 0.904 |
| IMG_2749.jpeg | maltese/teddy_cut | 21.056 | 13.46 | 0.886 |
| IMG_2749.jpeg | bichon/round_cut | 15.999 | 15.91 | 0.888 |
| IMG_2749.jpeg | pomeranian/bear_cut | 17.394 | 32.42 | 0.848 |
| IMG_9712.jpg | maltese/teddy_cut | 50.842 | 85.61 | 0.916 |
| IMG_9712.jpg | bichon/round_cut | 18.431 | 26.61 | 0.640 |
| IMG_9712.jpg | pomeranian/bear_cut | 18.134 | 19.70 | 0.894 |
| IMG_9827.jpg | maltese/teddy_cut | 22.199 | 20.61 | 0.785 |
| IMG_9827.jpg | bichon/round_cut | 37.646 | 130.98 | 0.830 |
| IMG_9827.jpg | pomeranian/bear_cut | 53.575 | 107.70 | 0.776 |
| IMG_9999.jpg | maltese/teddy_cut | 53.304 | 39.86 | 0.886 |
| IMG_9999.jpg | bichon/round_cut | 50.392 | 33.60 | 0.914 |
| IMG_9999.jpg | pomeranian/bear_cut | 33.662 | 28.02 | 0.825 |

**요약 통계 (edit_zone_change)**

| 지표 | mean | median | **p25** | p75 | min | max |
|------|---:|---:|---:|---:|---:|---:|
| 전체 N=15 | 41.358 | 28.001 | **20.155** | 36.733 | 11.449 | 130.979 |
| IMG_9827 제외 N=12 (참고) | 30.09 | 27.31 | **18.754** | 32.71 | 11.449 | 85.610 |
| IMG_9827 only N=3 (참고) | 86.43 | 107.70 | **64.153** | 119.34 | 20.610 | 130.979 |

**이미지별 p25**

| 이미지 | p25 |
|---|---:|
| IMG_7641.jpg | 18.951 |
| IMG_2749.jpeg | 14.683 |
| IMG_9712.jpg | 23.157 |
| **IMG_9827.jpg** | **64.153** ← gemini 전체재생성 outlier |
| IMG_9999.jpg | 30.810 |

**스타일별 p25**

| 스타일 | p25 |
|---|---:|
| maltese/teddy_cut | 13.459 |
| bichon/round_cut | 26.452 |
| pomeranian/bear_cut | 28.001 |

**기준 확정**

- **edit_zone_change baseline_p25 = 20.155** (N=15 전체 · 정식 기준)
- IMG_9827 제외 18.754는 **정식 기준이 아닌 민감도 참고값** — outlier(gemini 전체재생성)가 분포 상단을 올려 p25를 높이는 방향이어서 제외해도 기준이 오히려 관대해짐

**적용 — 2026-04-23 N=16 anchor 배치 평가**

- 통과: **10/11 (90.9%)**
- 경계 실패 1건: `bichon/round_cut` edit=19.46 (baseline 대비 -0.70)
- 통과 샘플 edit 범위: 20.72 (poodle/teddy_cut) ~ 41.11 (bedlington/traditional_cut)
- anchor edit p25 21.447 ≈ Step 2 baseline 20.155 — anchor가 gemini 다중 이미지 하한에 맞춰 편집 충분성 유지

**경계 사례 상세 — `bichon/round_cut` (IMG_7641, anchor)**

| 지표 | 값 | 해석 |
|---|---:|---|
| edit_zone_change | 19.46 | baseline 20.155 대비 -0.70 (경계 미달) |
| silhouette_iou_whole | 0.9213 | anchor N=11 중 **2위** (구조 상위권) |
| face_mae | 2.131 | 정상 (기준 25.0) |
| hard_preserve_area_ratio | 0.00957 | N=11 전원 동일 — mask 보수성 이슈 아님 |
| mode / position_gate_ok | full / true | geometry 정상 |

**교차검증**
- 같은 `bichon/round_cut` gemini 5이미지 edit (Step 2, IMG_9827 outlier 제외): 15.91 / 26.45 / 26.61 / 33.60 — gemini도 해당 스타일에서 편집 강도 하위권
- 같은 `round_cut` 다른 견종 anchor: `mini_bichon/round_cut` edit = 30.86 (bichon 대비 +11.4) — 같은 스타일이라도 견종 프롬프트에 따라 편차 큼

**판정**: 스타일 특성상 low-change 케이스. `bichon/round_cut` 프롬프트는 원본(말티즈) 털과 목표(둥근 털)가 가까워 변환 압력이 본래 낮음. silhouette 상위권 + face 정상 + hard_preserve 비율 동일 → 구조 안정 과잉 달성이지 회귀 아님.

**조치**: baseline_p25 = 20.155 유지, 본 건은 경계 사례 1건으로 기록.

결과 이미지: https://res.cloudinary.com/dubnzx8ew/image/upload/v1776918192/grooming-results/eswxkxzew7gcdknrf8kc.jpg

---

## Geometry Variance — Probe 이력

기하 안정성(좌표 편차) 측정 흐름 — Gemini VLM 단일/다회/crop 호출 비교

| 날짜 | 방식 | 스크립트 | basis_part | max_std_ratio | verdict | 비고 |
|------|------|----------|------------|---------------|---------|------|
| 2026-04-20 | 전체 이미지 단일 호출 N=5 | `run_geometry_variance.py` | nose.cy | 0.068 | unstable | baseline |
| 2026-04-20 | Median N=3 × 3 rep | `run_geometry_median_probe.py` | nose | 0.0372 | unstable | regime-switch 지속 |
| 2026-04-20 | Median N=5 × 3 rep | `run_geometry_median_probe.py` | right_eye | 0.0367 | unstable | N 증가 효과 없음 |
| 2026-04-21 | 2-stage head crop N=5 | `run_2stage_crop_probe.py` | nose.cy | 0.151 | unstable | crop으로 오히려 악화 (call 2 의미 수준 regime-switch) |

Gemini VLM은 공간 기반 crop, 다회 호출 집계 어느 쪽으로도 분산이 수렴하지 않음. 외부 결정론 CV 도입이 전제가 되는 단계에 진입.

---

## Face Parts Detection (run_face_parts.py)

- 출력: 탐지된 파트 수(0~3), bbox 좌표 퍼센트
- 기준: left_eye·right_eye·nose 3개 모두 탐지되면 PASS

데이터 없음 — `run_face_parts.py` 실행 후 기록

---

## Inpainting Model Comparison (run_inpainting_models.py)

- 출력: 모델별 결과 URL 및 마스크 외부 영역 보존 여부
- 기준: 마스크 외부 픽셀이 원본과 동일하면 PASS

데이터 없음 — `run_inpainting_models.py` 실행 후 기록

---

## 기타 / 메모

수치로 정량화하기 어려운 결과나 추가 관찰 사항을 자유롭게 기록한다.
