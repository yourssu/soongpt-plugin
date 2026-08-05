# soongpt-plugin 프로젝트 지침

> 이 파일은 이 저장소를 직접 개발할 때(로컬에서 이 폴더를 열었을 때)의 컨텍스트입니다. 플러그인을 설치한 최종 사용자에게는 전달되지 않습니다(`claude plugin tag` 실행 시 "CLAUDE.md at the plugin root is not loaded as project context" 경고 있음 — 사용자에게 컨텍스트를 배포하려면 skill로 만들어야 함).

## 버전 관리 & 릴리즈

이 플러그인은 `.claude-plugin/plugin.json`의 `version` 필드를 실제 배포 버전으로 씁니다(커밋 SHA 기반 자동 업데이트 아님 — 자세한 배경은 README "업데이트" 섹션 참고).

### 평소 작업 vs 배포

- 평소 기능/수정 커밋은 버전을 올리지 않고 자유롭게 쌓는다.
- "이제 사용자에게 배포해도 된다"고 판단되는 시점에만 **버전 bump 전용 커밋**을 별도로 만든다.
- 사용자는 이 bump 커밋이 main에 올라온 뒤에야 `/plugin update` 또는 `claude plugin update soongpt@yourssu`로 업데이트를 받는다.

### 버전을 올릴 때 반드시 같이 할 일

1. 아래 3개 파일의 버전 문자열을 **동일하게** 맞춘다. 이 중 `claude plugin tag`가 자동으로 검증해주는 건 `plugin.json`과 마켓플레이스 엔트리의 일치 여부뿐이고, `pyproject.toml`/`__init__.py`는 도구가 봐주지 않으니 수동으로 맞춰야 한다:
   - `.claude-plugin/plugin.json` → `version`
   - `pyproject.toml` → `[project].version`
   - `src/soongpt_mcp/__init__.py` → `__version__`
2. `CHANGELOG.md`를 갱신한다 (기준은 이슈 번호가 아니라 **PR 번호**):
   - `CHANGELOG.md`에서 직전 버전 섹션에 마지막으로 기록된 PR 번호(예: `#28`)를 확인한다.
   - `gh pr list --state merged --limit 30 --json number,title`로 그 이후 머지된 PR을 조회한다.
   - `[Unreleased]` 아래 쌓여 있던 항목들을 포함해, 새로 머지된 PR들을 새 버전 섹션(`## [x.y.z] - YYYY-MM-DD`)으로 옮기고 날짜를 채운다.
   - 각 PR을 Added/Changed/Fixed로 분류해 사람이 읽을 수 있는 한 줄 요약으로 정리하고, 항목 끝에 PR 번호(`(#N)`)를 표기한다. PR 없이 main에 바로 커밋한 경우(문서 오타 수정 등)는 PR 번호 없이 적어도 된다.
   - `[Unreleased]`는 빈 섹션으로 남겨서 다음 변경사항을 계속 쌓을 수 있게 한다.
3. (선택) `claude plugin tag --push` 로 `soongpt--v{version}` 태그를 생성해 릴리즈 지점을 남긴다. 이 명령은 `plugin.json`과 마켓플레이스 엔트리의 버전 일치 여부를 자동 검증해준다.

### 커밋 스타일

버전 bump는 기능 변경과 섞지 않고 별도 커밋으로 분리한다 (예: `chore: 플러그인 버전 0.1.1 → 0.1.2`).

## 문서 작성 규칙 (SKILL.md / CHANGELOG.md)

**SKILL.md와 CHANGELOG.md에는 이슈번호(SPR-N) 참조를 넣지 않는다** (SPR-118로 전면 제거된 저장소 컨벤션 — 회귀 금지).

- 규칙을 만들 때 "왜"가 필요하면 번호 대신 **설명 텍스트**로 적는다. 예:
  - ❌ `load_lectures_cache(..., include_lectures=False) 호출 (SPR-76)`
  - ✅ `load_lectures_cache(..., include_lectures=False) 호출 — 캐시 히트 여부만 확인하면 되므로 그룹 메타만 받는다`
- 교차 참조도 번호 대신 의미 표현으로 (예: `채플 satisfied 인터뷰 예외와 동일 패턴`).
- `CHANGELOG.md` 항목 끝의 **PR 번호(`(#N)`)는 유지**한다 — 형식 규약이므로. 이슈번호(SPR-N)만 금지.
- **이유**: SKILL.md는 스킬 호출마다 LLM 컨텍스트로 로드되어 이슈번호가 매번 토큰으로 소비된다. CHANGELOG도 마찬가지. 커밋 메시지/PR 본문/Linear에는 SPR-N을 자유롭게 써도 된다 (저장소 내부 기록이라 컨텍스트 비용 없음).
- 코드 주석(.py)의 SPR-N은 컨텍스트에 로드되지 않아 금지 대상은 아니지만, 새로 추가할 때는 번호 없이 설명만 적는 것을 권장한다.
