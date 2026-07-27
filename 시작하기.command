#!/bin/bash
# 기억력 게임 원클릭 실행 (macOS)
# 1) TMDB_API_KEY가 설정돼 있으면 문제 크롤링 → questions.json 갱신
# 2) 로컬 서버 실행 후 브라우저 자동 오픈
cd "$(dirname "$0")"

# ── TMDB 키: 아래 따옴표 안에 발급받은 키를 넣으면 영화 스틸컷이 나옵니다.
#    무료 발급: https://www.themoviedb.org/settings/api
TMDB_API_KEY="${TMDB_API_KEY:-c684218ca3959a5d7c7c20e3332e571f}"

echo "── 문제 데이터 갱신 시도 ──"
if command -v python3 >/dev/null; then
  python3 -m pip install -q requests beautifulsoup4 2>/dev/null
  TMDB_API_KEY="$TMDB_API_KEY" python3 crawler/crawl_questions.py || echo "크롤링 실패 → 기존/내장 문제 사용"
fi

echo ""
echo "── 게임 서버 시작: http://localhost:8000 (종료: Ctrl+C) ──"
( sleep 1 && open "http://localhost:8000" ) &
python3 -m http.server 8000
