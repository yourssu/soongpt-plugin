# Changelog

이 프로젝트의 주요 변경 사항을 버전별로 기록합니다.

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 따르고, 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.
각 항목 끝의 `(#N)`은 해당 변경을 반영한 PR 번호입니다.

## [Unreleased]

### Added

- 대용량 MCP 도구 소형 응답 옵션 — `find_lectures(summary=True)`, `load_lectures_cache(include_lectures=False)`(그룹 메타·codes), `parse_lectures_cache(summary=True)`. 기본값은 기존 동작 유지. 통합 조회·캐시 확인이 컨텍스트를 크게 아낌 (SPR-76) (#44)

## [1.1.0] - 2026-08-05

강의 fetch→캐시 파이프라인을 서버 측 자동 저장으로 개선하고, 채플 라우팅을 실제 학년 기준으로 수정.

### Added

- find_lectures 서버 측 캐시 자동 저장 — fetch 시점에 결과를 즉시 그룹 병합 저장. LLM이 거대 응답을 컨텍스트로 통과시켜 취합/저장할 필요가 없어짐 (SPR-75) (#40)
- 강의 캐시 그룹 키 규칙 고정 — `major_<collage>_<department>`, `optional_elective_all` 등 서버가 자동 생성 (SPR-75) (#40)

### Changed

- `save_lectures_cache` 도구 제거 — find_lectures 자동 저장으로 대체. 캐시 저장을 덮어쓰기에서 그룹 병합으로 전환 (SPR-75) (#40)

### Fixed

- 채플 요건 충족 시 인터뷰 채플 질문 스킵 (SPR-73) (#38)
- 채플 1학년 2학기 오라우팅 — `profile.grade`(PT-87 +1학기 보정)와 무관하게 실제 학년(`actual_grade`) 기준으로 분기 (SPR-71) (#39)

## [1.0.0] - 2026-08-05

안정화 릴리즈 — 강의 조회 속도/안정성 개선과 1학년 소그룹채플 정책 반영.

### Added

- 채플 grade별 분기 — 1학년 소그룹채플 정책 추가. fetch를 grade 기반 단일 호출(1학년→소그룹채플, 2학년+→비전채플)로 바꾸고, composer ④ 채플과 interview 채플 질문을 grade로 분기. 2학년+ 비전채플 로직은 기존 그대로 보존 (#34)
- 교양선택 '전체' 1회 호출 전환 — 학번별 분산 조회(5~25회) 제거 + field 줄바꿈 학번 태그 파싱 (#37)

### Changed

- get_usaint_snapshot 최적화 — 사용하지 않는 졸업사정표(grad) 세션 제거로 조회 속도 개선 (#33)
- find_lectures 동시성 안정화 — 세마포어 상한으로 포털 순차 처리에 따른 타임아웃/에러 방지 (#36)
- timetable-builder에 시각화(visualize) 연결 명시 (#32)
- 단독 MCP 서버 기준 문서(MCP 시절 잔재) 정리 (#35)

### Fixed

- timetable-composer 수강제한 두 케이스("대상외" vs "대상 학년 다름") 용어 구분 (#30)
- timetable-visualize 빈 요일/시간 그리드 누락 수정 (#31)
- `constants.py` `CHAPEL_CODES` 인라인 주석 정정 — `21501015`가 "채플"이 아니라 "비전채플" (#34)

## [0.1.2] - 2026-08-01

### Added

- soongpt-timetable-composer 채플(chapel) 처리 추가 (#29)

### Fixed

- Codex/Claude Code 플러그인 업데이트 안내 명령어 오류 수정 — 마켓플레이스 갱신 시 `org/repo` 형태가 아니라 등록된 마켓플레이스 이름(`yourssu`)을 써야 함
- Claude Code 업데이트 안내를 공식 CLI 명령(`claude plugin update`) 기준으로 정정

## [0.1.1] - 2026-08-01

### Added

- MCP 2.x 마이그레이션 (FastMCP → MCPServer) (#17)
- Codex CLI 호환 플러그인 지원 (#16)
- 시간표 파싱 도구 — `schedule_room` 파싱 + 시간 충돌 검사 (#23)
- 교양필수 과목명 목록 조회 도구 (#24)
- 시간표 후보 생성(composer) 스킬 — 후보 영속화 도구 3종 + builder 5단계 연결 (#25)
- 시간표 시각화 스킬 — 조립 중/최종 시간표를 정적 HTML로 렌더 + 충돌 강조 (#26)
- 프로필 단과대(college) 자동 매칭 — USAINT collage 추출 (#28)
- 복수/부전공/연계융합/교직 카테고리 조회 활성화 (#21)

### Fixed

- 만료 세션에서 자동 재로그인이 동작하지 않는 문제 (#18)
- 로그인 페이지 디자인 미적용 — CSP style-src 허용 (#27)

## [0.1.0] - 2026-07-31

플러그인으로 정식 등록된 첫 버전. 그 이전까지 쌓인 MVP 기능을 포함합니다.

### Added

- Claude Code 정식 플러그인 등록 (`plugin.json` + `mcp.json`) (#15)
- soongpt-mcp 플러그인 사용 가이드 스킬 (#13)
- 시간표 완성 오케스트레이터 스킬 (#12)
- 유세인트 로그인 페이지 UI 개선 (#14)
- 학과-단과대 매핑 자동 빌드 + 캐시 (#9)
- 교직이수 / 복수·연계융합·부전공 정보 프로필 스키마 (#6, #7)
- 들을 수 있는 과목 통합 조회 스킬 + 강의 캐시 (#8)
- 시간표 인터뷰 스킬 뼈대 (#5)
- 사용자 프로필 영속화 스키마 + MCP 도구 3종 (#3)
- 온디맨드 localhost 브라우저 로그인 플로우 (#2)
- `find_lectures` 과목 검색 도구, USAINT 졸업요건 조회 등 초기 MVP (#1)
