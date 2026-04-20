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
---

## Face Parts Detection (run_face_parts.py)

- 출력: 탐지된 파트 수(0~3), bbox 좌표 퍼센트
- 기준: left_eye·right_eye·nose 3개 모두 탐지되면 PASS

| 날짜 | 탐지 파트 수 | left_eye bbox (%) | right_eye bbox (%) | nose bbox (%) | 판정 | 비고 |
|------|-------------|-------------------|--------------------|---------------|------|------|

---

## Inpainting Model Comparison (run_inpainting_models.py)

- 출력: 모델별 결과 URL 및 마스크 외부 영역 보존 여부
- 기준: 마스크 외부 픽셀이 원본과 동일하면 PASS

| 날짜 | 모델 | 마스크 외부 보존 | 처리 시간(s) | 판정 | 비고 |
|------|------|----------------|-------------|------|------|

---

## 기타 / 메모

수치로 정량화하기 어려운 결과나 추가 관찰 사항을 자유롭게 기록한다.
