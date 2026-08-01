# Changelog

이 프로젝트의 주요 변경 사항을 버전별로 기록합니다.

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 따르고, 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [Unreleased]

### Fixed

- Codex/Claude Code 플러그인 업데이트 안내 명령어 오류 수정 — 마켓플레이스 갱신 시 `org/repo` 형태가 아니라 등록된 마켓플레이스 이름(`yourssu`)을 써야 함
- Claude Code 업데이트 안내를 공식 CLI 명령(`claude plugin update`) 기준으로 정정

## [0.1.1] - 2026-08-01

### Added

- MCP 2.x 마이그레이션 (FastMCP → MCPServer) (SPR-43)
- Codex CLI 호환 플러그인 지원 (SPR-44)
- 시간표 파싱 도구 — `schedule_room` 파싱 + 시간 충돌 검사 (SPR-50)
- 교양필수 과목명 목록 조회 도구 (SPR-51)
- 시간표 후보 생성(composer) 스킬 — 후보 영속화 도구 3종 + builder 5단계 연결 (SPR-52)
- 시간표 시각화 스킬 — 조립 중/최종 시간표를 정적 HTML로 렌더 + 충돌 강조 (SPR-53)
- 프로필 단과대(college) 자동 매칭 — USAINT collage 추출 (SPR-55)
- 복수/부전공/연계융합/교직 카테고리 조회 활성화 (SPR-48)

### Fixed

- 만료 세션에서 자동 재로그인이 동작하지 않는 문제 (SPR-45)
- 로그인 페이지 디자인 미적용 — CSP style-src 허용 (SPR-54)

## [0.1.0] - 2026-07-31

플러그인으로 정식 등록된 첫 버전. 그 이전까지 쌓인 MVP 기능을 포함합니다.

### Added

- Claude Code 정식 플러그인 등록 (`plugin.json` + `mcp.json`) (SPR-42)
- soongpt-mcp 플러그인 사용 가이드 스킬 (SPR-32)
- 시간표 완성 오케스트레이터 스킬 (SPR-39)
- 유세인트 로그인 페이지 UI 개선 (SPR-41)
- 학과-단과대 매핑 자동 빌드 + 캐시 (SPR-37)
- 교직이수 / 복수·연계융합·부전공 정보 프로필 스키마 (SPR-35, SPR-36)
- 들을 수 있는 과목 통합 조회 스킬 + 강의 캐시 (SPR-34)
- 시간표 인터뷰 스킬 뼈대 (SPR-33)
- 사용자 프로필 영속화 스키마 + MCP 도구 3종 (SPR-30)
- 온디맨드 localhost 브라우저 로그인 플로우 (SPR-28)
- `find_lectures` 과목 검색 도구, USAINT 졸업요건 조회 등 초기 MVP
