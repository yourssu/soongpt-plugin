"""
Rusaint 서비스에서 사용하는 상수.
"""

import rusaint

# 학기 타입 매핑 (rusaint.SemesterType → API 문자열)
SEMESTER_TYPE_MAP = {
    rusaint.SemesterType.ONE: "1",
    rusaint.SemesterType.TWO: "2",
    rusaint.SemesterType.SUMMER: "SUMMER",
    rusaint.SemesterType.WINTER: "WINTER",
}

# 채플 관련 과목 코드 (학점 과목이 아님, 졸업 요건은 별도 API에서 확인)
CHAPEL_CODES = frozenset({"21501015", "21500785"})  # 비전채플(2학년+), 소그룹채플(1학년)

# ============================================================
# 교양필수 재수강 매핑 (2022학년도 이전 입학자 구과목 → 신과목)
# ============================================================
# 폐강된 구과목의 과목명(rusaint class_name) → 대체 신과목의 baseCode(8자리)
# 구과목 코드로 현재 학기 DB 조회 시 매칭 불가 → 신과목 baseCode를 추가하여 대체 과목 추천
RETAKE_GENERAL_REQUIRED_MAPPING: dict[str, str] = {
    "독서와토론": "21501003",
    "대학글쓰기": "21501006",
    "기업가정신과행동": "21501009",
    "현대인과성서": "21501020",
    "컴퓨터사고": "21501028",
    "AI와데이터사회": "21501034",
}
