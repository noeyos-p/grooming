"""
견종+스타일별 프롬프트 단일 진실 소스 (Single Source of Truth).
견종·스타일 데이터는 반드시 이 파일에서만 관리한다.
"""

BREEDS: dict[str, dict] = {
    "maltese": {
        "name": "말티즈",
        "styles": {
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "maltese dog with teddy bear cut grooming style, fluffy round face, fur trimmed evenly into round shape, professional grooming, cute teddy bear appearance",
                "negative_prompt": "messy, dirty, ungroomed, long flowing hair",
                "trigger_word": "GRMDMALTTEDD",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "maltese dog with puppy cut, short even trim all over body, clean white fluffy coat, youthful appearance, professional dog grooming",
                "negative_prompt": "long hair, matted, uneven cut",
                "trigger_word": "GRMDMALTPUPP",
                "reference_images_gcs": None,
            },
            "lion_cut": {
                "name": "라이언컷",
                "prompt": "maltese dog with lion cut grooming, full mane around head and neck, body trimmed short, fluffy tail tip, professional grooming",
                "negative_prompt": "messy, ungroomed",
                "trigger_word": "GRMDMALTLION",
                "reference_images_gcs": None,
            },
        },
    },
    "poodle": {
        "name": "푸들",
        "styles": {
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "poodle with teddy bear cut, round fluffy head, even curly coat trim, adorable teddy bear face shape, professional grooming",
                "negative_prompt": "show cut, continental clip, uneven",
                "trigger_word": "GRMDPOODTEDD",
                "reference_images_gcs": None,
            },
            "continental_clip": {
                "name": "콘티넨탈클립",
                "prompt": "poodle with continental clip, pompons on hips and ankles, full mane, classic show poodle grooming style",
                "negative_prompt": "puppy cut, short all over, messy",
                "trigger_word": "GRMDPOODCONT",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "poodle with puppy cut grooming, uniform short curly coat, round face, fluffy ears, clean professional trim",
                "negative_prompt": "continental clip, show style, long",
                "trigger_word": "GRMDPOODPUPP",
                "reference_images_gcs": None,
            },
        },
    },
    "bichon": {
        "name": "비숑",
        "styles": {
            "round_cut": {
                "name": "라운드컷",
                "prompt": "bichon frise with round cut, perfectly round fluffy white head, cloud-like appearance, professional grooming, pristine white coat",
                "negative_prompt": "flat, uneven, dirty, yellow",
                "trigger_word": "GRMDBICHROND",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "bichon frise with puppy cut, short fluffy white coat evenly trimmed, sweet puppy face, professional grooming",
                "negative_prompt": "round poof, show style, matted",
                "trigger_word": "GRMDBICHPUPP",
                "reference_images_gcs": None,
            },
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "bichon frise with teddy bear cut grooming, soft round face, even white fluffy coat, adorable teddy bear look",
                "negative_prompt": "show style, uneven, dirty",
                "trigger_word": "GRMDBICHTEDD",
                "reference_images_gcs": None,
            },
        },
    },
    "maltipoo": {
        "name": "말티푸",
        "styles": {
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "maltipoo with teddy bear cut, soft fluffy mixed coat trimmed evenly, round cute face, professional grooming",
                "negative_prompt": "matted, uneven, overgrown",
                "trigger_word": "GRMDMTPOTEDD",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "maltipoo with puppy cut, short uniform trim, fluffy face, wavy soft coat, professional dog grooming",
                "negative_prompt": "long flowing, matted, dirty",
                "trigger_word": "GRMDMTPOPUPP",
                "reference_images_gcs": None,
            },
            "fluffy_cut": {
                "name": "플러피컷",
                "prompt": "maltipoo with fluffy cut, voluminous soft coat kept medium length, full fluffy appearance, professional grooming",
                "negative_prompt": "short cut, shaved, messy",
                "trigger_word": "GRMDMTPOFLUF",
                "reference_images_gcs": None,
            },
        },
    },
    "pomeranian": {
        "name": "포메라니안",
        "styles": {
            "bear_cut": {
                "name": "곰돌이컷",
                "prompt": "pomeranian with bear cut grooming, round teddy bear face, body fur trimmed to even length, fluffy tail, adorable bear appearance",
                "negative_prompt": "fox face, show coat, uneven",
                "trigger_word": "GRMDPOMEBEAR",
                "reference_images_gcs": None,
            },
            "fox_cut": {
                "name": "여우컷",
                "prompt": "pomeranian with fox cut, pointed fox-like face, full double coat, fluffy tail, natural breed appearance enhanced",
                "negative_prompt": "rounded, shaved, uneven",
                "trigger_word": "GRMDPOMEFOX_",
                "reference_images_gcs": None,
            },
            "round_cut": {
                "name": "라운드컷",
                "prompt": "pomeranian with round cut, perfectly rounded fluffy silhouette, even coat length all around, professional grooming",
                "negative_prompt": "pointed, fox cut, uneven",
                "trigger_word": "GRMDPOMEROND",
                "reference_images_gcs": None,
            },
        },
    },
    "yorkshire": {
        "name": "요크셔테리어",
        "styles": {
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "yorkshire terrier with puppy cut, short even trim, silky fine coat, cute young appearance, professional grooming",
                "negative_prompt": "show coat, long floor-length, bow",
                "trigger_word": "GRMDYORKPUPP",
                "reference_images_gcs": None,
            },
            "show_cut": {
                "name": "쇼컷",
                "prompt": "yorkshire terrier with show cut, long silky floor-length coat, perfectly parted, bow on head, competition grooming style",
                "negative_prompt": "short, uneven, matted",
                "trigger_word": "GRMDYORKSHOW",
                "reference_images_gcs": None,
            },
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "yorkshire terrier with teddy bear cut, short fluffy face, rounded head, even body trim, adorable bear look",
                "negative_prompt": "long show coat, silky straight, uneven",
                "trigger_word": "GRMDYORKTEDD",
                "reference_images_gcs": None,
            },
        },
    },
    "shih_tzu": {
        "name": "시츄",
        "styles": {
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "shih tzu with puppy cut, short even fluffy coat, sweet round face, professional grooming, clean trim",
                "negative_prompt": "show coat, long, topknot",
                "trigger_word": "GRMDSHTZPUPP",
                "reference_images_gcs": None,
            },
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "shih tzu with teddy bear cut grooming, round fluffy face, even medium-length coat, adorable teddy bear appearance",
                "negative_prompt": "show style, long floor-length, uneven",
                "trigger_word": "GRMDSHTZTEDD",
                "reference_images_gcs": None,
            },
            "lion_cut": {
                "name": "라이언컷",
                "prompt": "shih tzu with lion cut, full mane around head, body shaved short, fluffy tail, professional grooming",
                "negative_prompt": "all over fluffy, uneven, messy",
                "trigger_word": "GRMDSHTZLION",
                "reference_images_gcs": None,
            },
        },
    },
    "papillon": {
        "name": "파피용",
        "styles": {
            "natural_cut": {
                "name": "자연컷",
                "prompt": "papillon with natural cut, butterfly ears fully feathered, natural flowing coat lightly trimmed for neatness, elegant appearance",
                "negative_prompt": "shaved, heavily trimmed, messy",
                "trigger_word": "GRMDPAPNATU",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "papillon with puppy cut, short even body trim, feathered ears kept, clean professional look",
                "negative_prompt": "long flowing, show style, matted",
                "trigger_word": "GRMDPAPPUPP",
                "reference_images_gcs": None,
            },
            "summer_cut": {
                "name": "썸머컷",
                "prompt": "papillon with summer cut, short cool trim all over body, butterfly ears lightly trimmed, fresh summer grooming",
                "negative_prompt": "full coat, heavy fur, winter",
                "trigger_word": "GRMDPAPSUM_",
                "reference_images_gcs": None,
            },
        },
    },
    "spitz": {
        "name": "스피츠",
        "styles": {
            "natural_cut": {
                "name": "자연컷",
                "prompt": "japanese spitz with natural cut, full white double coat lightly groomed, fluffy fox-like face, elegant natural appearance",
                "negative_prompt": "shaved, heavily trimmed, dirty",
                "trigger_word": "GRMDSPITZNATU",
                "reference_images_gcs": None,
            },
            "round_cut": {
                "name": "라운드컷",
                "prompt": "japanese spitz with round cut, rounded fluffy silhouette, white coat trimmed to even round shape, professional grooming",
                "negative_prompt": "natural spiky, uneven, dirty",
                "trigger_word": "GRMDSPITZROND",
                "reference_images_gcs": None,
            },
            "bear_cut": {
                "name": "곰돌이컷",
                "prompt": "japanese spitz with bear cut, round teddy bear face shape, fluffy even white coat, adorable bear appearance",
                "negative_prompt": "fox cut, pointed face, uneven",
                "trigger_word": "GRMDSPITZBEAR",
                "reference_images_gcs": None,
            },
        },
    },
    "mini_bichon": {
        "name": "미니비숑",
        "styles": {
            "round_cut": {
                "name": "라운드컷",
                "prompt": "miniature bichon with round cut, perfectly spherical fluffy white head, cloud-like grooming, professional clean white coat",
                "negative_prompt": "flat, yellow, uneven, dirty",
                "trigger_word": "GRMDMNBCROND",
                "reference_images_gcs": None,
            },
            "teddy_cut": {
                "name": "테디베어컷",
                "prompt": "miniature bichon with teddy bear cut, soft round face, even fluffy white coat, adorable teddy bear look",
                "negative_prompt": "show style, uneven, dirty",
                "trigger_word": "GRMDMNBCTEDD",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "miniature bichon with puppy cut, short fluffy white coat, sweet puppy appearance, professional grooming",
                "negative_prompt": "round poof show style, matted",
                "trigger_word": "GRMDMNBCPUPP",
                "reference_images_gcs": None,
            },
        },
    },
    "bedlington": {
        "name": "베들링턴",
        "styles": {
            "traditional_cut": {
                "name": "전통컷",
                "prompt": "bedlington terrier with traditional cut, lamb-like pear-shaped head, arched back silhouette, soft linty coat, classic breed grooming",
                "negative_prompt": "puppy cut, shaved, messy",
                "trigger_word": "GRMDBEDLTRAD",
                "reference_images_gcs": None,
            },
            "puppy_cut": {
                "name": "퍼피컷",
                "prompt": "bedlington terrier with puppy cut, short even trim, soft coat, youthful appearance, professional grooming",
                "negative_prompt": "show style, traditional arch, long",
                "trigger_word": "GRMDBEDLPUPP",
                "reference_images_gcs": None,
            },
            "lamb_cut": {
                "name": "램컷",
                "prompt": "bedlington terrier with lamb cut, soft woolly even coat, gentle lamb-like appearance, professional grooming",
                "negative_prompt": "traditional arch, show cut, uneven",
                "trigger_word": "GRMDBEDLLAMB",
                "reference_images_gcs": None,
            },
        },
    },
}


def get_all_breeds() -> list[dict]:
    """모든 견종 + 스타일 목록 반환 (API 응답용)."""
    result = []
    for breed_id, breed_data in BREEDS.items():
        styles = [
            {"id": style_id, "name": style_data["name"], "thumbnail_url": None}
            for style_id, style_data in breed_data["styles"].items()
        ]
        result.append({"id": breed_id, "name": breed_data["name"], "styles": styles})
    return result


def get_prompt(breed_id: str, style_id: str) -> dict | None:
    """특정 견종+스타일의 프롬프트 딕셔너리 반환. 존재하지 않으면 None."""
    breed = BREEDS.get(breed_id)
    if breed is None:
        return None
    style = breed["styles"].get(style_id)
    if style is None:
        return None
    return {
        "prompt": style["prompt"],
        "negative_prompt": style["negative_prompt"],
        "trigger_word": style.get("trigger_word", ""),
    }
