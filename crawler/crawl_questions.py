#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기억력 게임 문제 생성 크롤러 v2
====================================
index.html이 읽는 questions.json을 생성합니다.

수집 소스
- 배우(actor)   : 네이버 연예뉴스 랭킹 기사 제목 + 썸네일에서 배우 이름 매칭
- 가수(singer)  : 멜론 차트 TOP100 → 곡명/가사 힌트, 정답은 "가수"
- 예능(variety) : 한국어 위키피디아 API에서 프로그램별 출연진 추출 (3명 이상)
- 영화(movie)   : TMDB API로 한국 인기 영화 스틸컷 자동 수집 (무료 API 키 필요)
- 수도(capital) : 내장 데이터 (크롤링 불필요)

사용법
    pip install requests beautifulsoup4
    export TMDB_API_KEY=발급받은키        # 영화 스틸컷용 (선택)
    python crawl_questions.py [--out ../questions.json]

TMDB 키 발급(무료): https://www.themoviedb.org/settings/api
실패한 카테고리는 자동으로 내장 풀 사용. 수집물은 개인 용도로만 사용하세요.
"""
import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("pip install requests beautifulsoup4 후 다시 실행하세요.")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

KNOWN_ACTORS = [
    "송강호", "마동석", "전지현", "이병헌", "김혜수", "최민식", "이정재", "정우성",
    "하정우", "황정민", "손예진", "현빈", "공유", "박서준", "박보검", "김수현",
    "김태리", "수지", "아이유", "유아인", "조인성", "김고은", "박은빈", "남주혁",
    "한소희", "고윤정", "차은우", "김지원", "송중기", "송혜교", "전도연", "라미란",
    "유해진", "조진웅", "이성민", "설경구", "김윤석", "박해일", "탕웨이", "김다미",
]

# 위키피디아에서 출연진을 가져올 예능 목록 (문서 제목)
VARIETY_SHOWS = {
    "런닝맨": "런닝맨 (텔레비전 프로그램)",
    "나 혼자 산다": "나 혼자 산다",
    "1박 2일": "1박 2일",
    "아는 형님": "아는 형님",
    "놀면 뭐하니": "놀면 뭐하니?",
    "신서유기": "신서유기",
    "무한도전": "무한도전",
    "라디오스타": "라디오 스타 (텔레비전 프로그램)",
}

# 난이도 기준(객관 지표):
#   영화  = TMDB 투표수(vote_count) 상위 1/3 easy, 중간 normal, 하위 hard
#   가수  = 멜론 차트 순위 1~10위 easy, 11~50위 normal, 51위~ hard
#   예능  = 위키 문서 조회는 지표가 없어 목록에 수동 태그
VARIETY_DIFFICULTY = {
    "무한도전": "easy", "런닝맨": "easy", "1박 2일": "easy", "나 혼자 산다": "easy",
    "신서유기": "normal", "아는 형님": "normal", "놀면 뭐하니": "normal", "라디오스타": "normal",
}


def fetch(url, retries=3, **kw):
    """요청 제한(429)·일시 오류(5xx)면 잠시 쉬고 재시도."""
    import time
    for i in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=10, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (i + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


# ---------------------------------------------------------------
# 1) 배우: 네이버 연예뉴스 랭킹
# ---------------------------------------------------------------
def crawl_actors(limit=8):
    out, seen = [], set()
    try:
        html = fetch("https://entertain.naver.com/ranking").text
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li"):
            a = li.find("a")
            img = li.find("img")
            if not a:
                continue
            title = a.get_text(" ", strip=True)
            src = (img.get("src") or img.get("data-src")) if img else None
            for name in KNOWN_ACTORS:
                if name in title and name not in seen:
                    seen.add(name)
                    q = {"answer": name, "alt": [],
                         "prompt": f"오늘 연예뉴스에 등장한 이 배우는? (기사: {title.replace(name, '○'*len(name))})"}
                    if src:
                        q["img"] = src
                    out.append(q)
                    break
            if len(out) >= limit:
                break
    except Exception as e:
        print(f"  [actor] 실패: {e}")
    return out


# ---------------------------------------------------------------
# 2) 가수: 멜론 차트 → 정답은 가수명
# ---------------------------------------------------------------
def crawl_singers(limit=12):
    """멜론 차트에서 수집. 난이도 = 차트 순위 (1~10 easy / 11~50 normal / 51~ hard)."""
    out, seen = [], set()
    try:
        html = fetch("https://www.melon.com/chart/index.htm").text
        soup = BeautifulSoup(html, "html.parser")
        rows = list(enumerate(soup.select("tr.lst50, tr.lst100"), start=1))  # (순위, tr)
        random.shuffle(rows)
        per_diff = {"easy": 0, "normal": 0, "hard": 0}
        quota = limit // 3 + 1  # 난이도별 고르게
        for rank, tr in rows:
            t = tr.select_one("div.rank01 a")
            a = tr.select_one("div.rank02 a")
            if not (t and a):
                continue
            title, artist = t.get_text(strip=True), a.get_text(strip=True)
            answer, alt = clean_artist(artist)
            if answer in seen:
                continue
            diff = "easy" if rank <= 10 else "normal" if rank <= 50 else "hard"
            if per_diff[diff] >= quota:
                continue
            seen.add(answer)
            per_diff[diff] += 1
            m = re.search(r"goSongDetail\('?(\d+)'?\)", t.get("href", ""))
            lyric = get_melon_lyric_snippet(m.group(1)) if m else None
            prompt = (f"🎵 '{title}' — \"{lyric}\"" if lyric
                      else f"🎵 '{title}' — 현재 멜론 차트인 중인 이 곡의 가수는?")
            out.append({"answer": answer, "alt": alt, "prompt": prompt,
                        "lyric": True, "difficulty": diff})
            if len(out) >= limit:
                break
    except Exception as e:
        print(f"  [singer] 실패: {e}")
    return out


def clean_artist(name):
    """'LE SSERAFIM (르세라핌)' → 정답 '르세라핌', 별칭에 나머지 표기 등록.
    한글 표기를 우선 정답으로 사용."""
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", name)
    if not m:
        return name, []
    outer, inner = m.group(1).strip(), m.group(2).strip()
    def has_ko(s): return bool(re.search(r"[가-힣]", s))
    if has_ko(inner) and not has_ko(outer):
        return inner, [outer, name]
    return outer, [inner, name]


def get_melon_lyric_snippet(song_id, lines=1):
    """곡 상세 페이지에서 가사 첫 소절만 (저작권상 짧게)."""
    try:
        html = fetch(f"https://www.melon.com/song/detail.htm?songId={song_id}").text
        soup = BeautifulSoup(html, "html.parser")
        box = soup.select_one("div.lyric")
        if not box:
            return None
        first = [l for l in box.get_text("\n", strip=True).split("\n") if l.strip()][:lines]
        return " / ".join(first) if first else None
    except Exception:
        return None


# ---------------------------------------------------------------
# 3) 예능: 위키피디아 API에서 출연진 추출
# ---------------------------------------------------------------
def wiki_cast(page_title):
    """문서 인포박스/본문 위키텍스트에서 사람 링크를 뽑아 출연진 후보로."""
    api = "https://ko.wikipedia.org/w/api.php"
    params = {
        "action": "query", "format": "json", "prop": "revisions",
        "rvprop": "content", "rvslots": "main", "rvsection": "0",
        "titles": page_title,
    }
    r = fetch(api, params=params).json()
    pages = r.get("query", {}).get("pages", {})
    text = ""
    for p in pages.values():
        revs = p.get("revisions") or []
        if revs:
            text = revs[0].get("slots", {}).get("main", {}).get("*", "") or revs[0].get("*", "")
    if not text:
        return []
    # |출연자 = ... 또는 |출연 = ... 필드에서만 [[이름]] 링크 추출
    # (필드가 없으면 빈 리스트 반환 — 본문 전체에서 긁으면 '일요일', '돌비 디지털' 같은
    #  엉뚱한 링크가 출연진으로 들어가는 오류가 생김)
    m = re.search(r"\|\s*(?:출연자|출연|진행자?)\s*=\s*(.*?)(?=\n\s*\|)", text, re.S)
    if not m:
        return []
    NOT_PERSON = {"대한민국", "일요일", "토요일", "문화방송", "한국방송공사", "돌비 디지털",
                  "서울특별시", "리얼리티 방송", "버라이어티"}
    cast = []
    for link in re.findall(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", m.group(1)):
        name = re.sub(r"\s*\(.*\)$", "", link.strip())
        if (re.fullmatch(r"[가-힣]{2,4}(?:\s?[가-힣0-9]{0,3})?", name)
                and name not in NOT_PERSON and name not in cast):
            cast.append(name)
    return cast[:6]


def crawl_variety(limit=8):
    out = []
    for answer, page in VARIETY_SHOWS.items():
        try:
            cast = wiki_cast(page)
            if len(cast) >= 3:
                alt = [answer.replace(" ", "")] if " " in answer else []
                out.append({"answer": answer, "alt": alt, "cast": cast[:5],
                            "difficulty": VARIETY_DIFFICULTY.get(answer, "normal")})
                print(f"  [variety] {answer}: {', '.join(cast[:5])}")
        except Exception as e:
            print(f"  [variety] {answer} 실패: {e}")
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------
# 4) 영화/배우: TMDB (무료 API 키 필요)
# ---------------------------------------------------------------
TMDB = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w780"
PROFILE_BASE = "https://image.tmdb.org/t/p/w500"


def crawl_actors_tmdb(api_key, per_tier=8, movie_pages=3, cast_per_movie=8):
    """TMDB 한국 인기 영화들의 출연진에서 배우 풀을 동적으로 구성 (수백 명 규모).
    난이도 = 수집된 전체 풀 안에서 popularity 백분위 3등분.
    풀이 크고 무명~톱스타가 섞여 있어 백분위 방식이 자연스럽게 작동한다.
    영화 목록이 바뀌고 매일 랜덤 추출하므로 문제가 계속 달라진다."""
    # 1) 한국 영화 수집 (페이지 단위 실패는 건너뜀)
    movies = []
    for page in range(1, movie_pages + 1):
        try:
            r = fetch(f"{TMDB}/discover/movie",
                      params={"api_key": api_key, "language": "ko-KR",
                              "with_origin_country": "KR",
                              "sort_by": "vote_count.desc",
                              "vote_count.gte": 100,
                              "without_genres": "99,10402,16",
                              "page": page}).json()
            movies += [m for m in (r.get("results") or [])
                       if m.get("original_language") == "ko"]
        except Exception as e:
            print(f"  [actor/tmdb] discover p{page} 실패(건너뜀): {e}")
            continue

    # 2) 각 영화의 출연진 수집 (주연~조연 상위 cast_per_movie명)
    people = {}
    for mv in movies:
        try:
            r = fetch(f"{TMDB}/movie/{mv['id']}/credits",
                      params={"api_key": api_key, "language": "ko-KR"}).json()
            for c in (r.get("cast") or [])[:cast_per_movie]:
                if not c.get("profile_path"):
                    continue
                # 원어 이름이 한글인 배우만 (에드 해리스처럼 한국어 표기된 외국 배우 제외)
                if not re.search(r"[가-힣]", c.get("original_name", "")):
                    continue
                pid = c["id"]
                if pid not in people:
                    people[pid] = c
                people[pid]["_count"] = people[pid].get("_count", 0) + 1
        except Exception:
            continue

    # 3) 너무 무명(사실상 못 맞추는) 배우 제외: 출연 2회 이상 또는 인기도 일정 이상
    pool = [p for p in people.values()
            if p.get("_count", 0) >= 2 or p.get("popularity", 0) >= 2.0]
    if len(pool) < 30:
        pool = list(people.values())
    if not pool:
        return []
    print(f"  [actor/tmdb] 배우 풀 {len(pool)}명 구성")

    # 4) popularity 백분위로 3등분 → 각 티어에서 랜덤 추출
    pool.sort(key=lambda p: p.get("popularity", 0), reverse=True)
    n = len(pool)
    tiers = {"easy": pool[:n // 3], "normal": pool[n // 3:2 * n // 3], "hard": pool[2 * n // 3:]}
    out = []
    for diff, tier in tiers.items():
        random.shuffle(tier)
        for p in tier[:per_tier]:
            out.append({"answer": p["name"], "alt": [],
                        "img": PROFILE_BASE + p["profile_path"],
                        "prompt": "이 배우의 이름은?",
                        "difficulty": diff})
    return out


def crawl_movies(api_key, limit=24, pages=8):
    """TMDB discover로 한국 영화 ~160편 풀 구성, 스틸컷(backdrop) 첨부.
    난이도 = 풀 내 투표수(vote_count) 백분위 3등분, 티어별 매일 랜덤 추출.
    (배우와 같은 논리: 큰 풀 + 객관 지표 + 랜덤 추출로 재탕 방지)"""
    movies = []
    for page in range(1, pages + 1):
        try:
            r = fetch(f"{TMDB}/discover/movie",
                      params={"api_key": api_key, "language": "ko-KR",
                              "with_origin_country": "KR",
                              "sort_by": "vote_count.desc",
                              "vote_count.gte": 50,  # 너무 무명작 제외
                              "without_genres": "99,10402,16",  # 다큐/공연실황/애니 제외
                              "page": page}).json()
            movies += [mv for mv in (r.get("results") or [])
                       if mv.get("original_language") == "ko"]
        except Exception as e:
            print(f"  [movie] discover p{page} 실패(건너뜀): {e}")
            continue  # 한 페이지 실패해도 나머지로 계속
    if not movies:
        return []

    movies.sort(key=lambda m: m.get("vote_count", 0), reverse=True)
    n = len(movies)
    tiers = {"easy": movies[:n//3], "normal": movies[n//3:2*n//3], "hard": movies[2*n//3:]}
    out = []
    quota = limit // 3 + 1
    for diff, tier in tiers.items():
        random.shuffle(tier)
        got = 0
        for movie in tier:
            if got >= quota:
                break
            try:
                imgs = fetch(f"{TMDB}/movie/{movie['id']}/images",
                             params={"api_key": api_key}).json()
                backdrops = imgs.get("backdrops") or []
                if not backdrops:
                    continue
                still = random.choice(backdrops[:5])  # 포스터 대신 스틸컷(제목 미노출)
                out.append({
                    "answer": movie.get("title"),
                    "alt": [],
                    "img": IMG_BASE + still["file_path"],
                    "prompt": f"힌트: {movie.get('release_date', '')[:4]}년 개봉",
                    "difficulty": diff,
                })
                got += 1
                print(f"  [movie/{diff}] {movie.get('title')} "
                      f"(투표 {movie.get('vote_count')}) 스틸컷 OK")
            except Exception as e:
                print(f"  [movie] {movie.get('title')} 실패: {e}")
    return out


# ---------------------------------------------------------------
# 내장 풀 (크롤링 실패 시 대체)
# ---------------------------------------------------------------
FALLBACK = {
    "movie": [
        {"answer": "기생충", "difficulty": "easy", "alt": [], "prompt": "반지하 가족이 부잣집에 하나둘 취업하며 벌어지는 이야기. 칸 황금종려상·아카데미 작품상."},
        {"answer": "극한직업", "difficulty": "easy", "alt": [], "prompt": "잠복하려고 차린 치킨집이 대박 난 마약반 형사들."},
        {"answer": "부산행", "difficulty": "easy", "alt": [], "prompt": "좀비가 퍼진 대한민국, KTX 안에서의 사투."},
        {"answer": "명량", "difficulty": "easy", "alt": [], "prompt": "12척으로 330척에 맞선 해전. 역대 최다 관객."},
        {"answer": "베테랑", "difficulty": "easy", "alt": [], "prompt": "\"어이가 없네~\" 재벌 3세를 쫓는 형사."},
        {"answer": "올드보이", "difficulty": "normal", "alt": [], "prompt": "15년간 감금됐던 남자의 복수극. 장도리 롱테이크 액션."},
        {"answer": "타짜", "difficulty": "normal", "alt": [], "prompt": "\"묻고 더블로 가!\" 화투판 인생."},
        {"answer": "아저씨", "difficulty": "normal", "alt": [], "prompt": "\"난 오늘만 살아.\" 옆집 소녀를 구하러 나선 전직 특수요원."},
        {"answer": "곡성", "difficulty": "normal", "alt": [], "prompt": "\"뭣이 중헌디!\" 외지인이 나타난 후 마을의 기이한 사건들."},
        {"answer": "살인의 추억", "difficulty": "normal", "alt": ["살인의추억"], "prompt": "\"밥은 먹고 다니냐?\" 화성 연쇄살인사건을 쫓는 두 형사."},
        {"answer": "버닝", "difficulty": "hard", "alt": [], "prompt": "하루키 단편 원작, 유아인·스티븐 연 주연, 이창동 감독."},
        {"answer": "벌새", "difficulty": "hard", "alt": [], "prompt": "1994년 서울, 중2 은희의 성장담. 김보라 감독."},
        {"answer": "파수꾼", "difficulty": "hard", "alt": [], "prompt": "세 고교생의 우정과 파국. 이제훈의 출세작."},
        {"answer": "지구를 지켜라!", "difficulty": "hard", "alt": ["지구를 지켜라", "지구를지켜라"], "prompt": "외계인이라 믿는 남자를 납치한 청년. 장준환 감독의 컬트작."},
    ],
    "singer": [
        {"answer": "아이유", "difficulty": "easy", "alt": ["IU"], "prompt": "🎵 '밤편지' — \"이 밤 그날의 반딧불을...\"", "lyric": True},
        {"answer": "싸이", "difficulty": "easy", "alt": ["PSY"], "prompt": "🎵 '강남스타일' — 말춤 신드롬", "lyric": True},
        {"answer": "임영웅", "difficulty": "easy", "alt": [], "prompt": "🎵 '이제 나만 믿어요' — 미스터트롯 진(眞)", "lyric": True},
        {"answer": "방탄소년단", "difficulty": "easy", "alt": ["BTS"], "prompt": "🎵 'Dynamite' — 빌보드 1위 7인조", "lyric": True},
        {"answer": "윤하", "difficulty": "normal", "alt": [], "prompt": "🎵 '사건의 지평선' — 역주행 신화", "lyric": True},
        {"answer": "버스커버스커", "difficulty": "normal", "alt": ["버스커 버스커"], "prompt": "🎵 '벚꽃 엔딩' — 벚꽃연금", "lyric": True},
        {"answer": "김광석", "difficulty": "normal", "alt": [], "prompt": "🎵 '서른 즈음에' — 영원한 가객", "lyric": True},
        {"answer": "이무진", "difficulty": "normal", "alt": [], "prompt": "🎵 '신호등' — 싱어게인 출신", "lyric": True},
        {"answer": "검정치마", "difficulty": "hard", "alt": [], "prompt": "🎵 'EVERYTHING' — 조휴일의 1인 밴드", "lyric": True},
        {"answer": "델리스파이스", "difficulty": "hard", "alt": ["델리 스파이스"], "prompt": "🎵 '챠우챠우' — 한국 모던록의 시작점", "lyric": True},
        {"answer": "브로콜리너마저", "difficulty": "hard", "alt": ["브로콜리 너마저"], "prompt": "🎵 '앵콜요청금지'", "lyric": True},
        {"answer": "넬", "difficulty": "hard", "alt": ["NELL"], "prompt": "🎵 '기억을 걷는 시간' — 김종완이 이끄는 밴드", "lyric": True},
    ],
    "actor": [
        {"answer": "송강호", "difficulty": "easy", "alt": [], "prompt": "기생충·변호인·괴물의 주연. 칸 남우주연상."},
        {"answer": "마동석", "difficulty": "easy", "alt": [], "prompt": "범죄도시 마석도 형사. 이터널스 출연."},
        {"answer": "전지현", "difficulty": "easy", "alt": [], "prompt": "엽기적인 그녀, 별에서 온 그대의 천송이."},
        {"answer": "이정재", "difficulty": "easy", "alt": [], "prompt": "오징어 게임의 성기훈. 에미상 수상."},
        {"answer": "이병헌", "difficulty": "normal", "alt": [], "prompt": "내부자들·오징어 게임의 프론트맨."},
        {"answer": "김혜수", "difficulty": "normal", "alt": [], "prompt": "타짜의 정마담. \"나 이대 나온 여자야.\""},
        {"answer": "최민식", "difficulty": "normal", "alt": [], "prompt": "올드보이·명량·파묘의 주연."},
        {"answer": "박은빈", "difficulty": "normal", "alt": [], "prompt": "'이상한 변호사 우영우'의 주인공."},
        {"answer": "박정민", "difficulty": "hard", "alt": [], "prompt": "동주·다만 악에서 구하소서. 파수꾼으로 데뷔한 연기파."},
        {"answer": "구교환", "difficulty": "hard", "alt": [], "prompt": "반도·D.P.·모가디슈. 독립영화 감독 겸 배우."},
        {"answer": "전여빈", "difficulty": "hard", "alt": [], "prompt": "빈센조의 홍차영 변호사, 낙원의 밤."},
        {"answer": "김신록", "difficulty": "hard", "alt": [], "prompt": "지옥의 박정자, 재벌집 막내아들. 연극 무대 출신."},
    ],
    "variety": [
        {"answer": "무한도전", "difficulty": "easy", "alt": [], "cast": ["유재석", "박명수", "정준하", "정형돈", "하하", "노홍철"]},
        {"answer": "런닝맨", "difficulty": "easy", "alt": [], "cast": ["유재석", "김종국", "하하", "지석진", "송지효", "전소민"]},
        {"answer": "1박 2일", "difficulty": "easy", "alt": ["1박2일"], "cast": ["강호동", "이수근", "은지원", "김종민"]},
        {"answer": "나 혼자 산다", "difficulty": "easy", "alt": ["나혼자산다"], "cast": ["전현무", "박나래", "기안84", "코드 쿤스트"]},
        {"answer": "신서유기", "difficulty": "normal", "alt": [], "cast": ["강호동", "이수근", "은지원", "규현", "송민호", "피오"]},
        {"answer": "아는 형님", "difficulty": "normal", "alt": ["아는형님"], "cast": ["강호동", "서장훈", "김영철", "이수근", "김희철", "민경훈"]},
        {"answer": "삼시세끼", "difficulty": "normal", "alt": [], "cast": ["차승원", "유해진", "손호준"]},
        {"answer": "놀면 뭐하니", "difficulty": "normal", "alt": ["놀면뭐하니"], "cast": ["유재석", "하하", "주우재", "이이경"]},
        {"answer": "뿅뿅 지구오락실", "difficulty": "hard", "alt": ["지구오락실"], "cast": ["이은지", "미미", "이영지", "안유진"]},
        {"answer": "출장 십오야", "difficulty": "hard", "alt": ["출장십오야", "십오야"], "cast": ["나영석", "김대주", "이우정"]},
        {"answer": "강식당", "difficulty": "hard", "alt": [], "cast": ["강호동", "이수근", "은지원", "송민호"]},
        {"answer": "어쩌다 사장", "difficulty": "hard", "alt": ["어쩌다사장"], "cast": ["차태현", "조인성", "김우빈"]},
    ],
    "capital": [
        {"answer": "서울", "difficulty": "easy", "alt": ["서울특별시"], "prompt": "🌍 대한민국의 수도는?"},
        {"answer": "도쿄", "difficulty": "easy", "alt": ["동경"], "prompt": "🌍 일본의 수도는?"},
        {"answer": "파리", "difficulty": "easy", "alt": [], "prompt": "🌍 프랑스의 수도는?"},
        {"answer": "카이로", "difficulty": "easy", "alt": [], "prompt": "🌍 이집트의 수도는?"},
        {"answer": "하노이", "difficulty": "easy", "alt": [], "prompt": "🌍 베트남의 수도는? (호치민 아님!)"},
        {"answer": "캔버라", "difficulty": "normal", "alt": [], "prompt": "🌍 호주의 수도는? (시드니 아님!)"},
        {"answer": "오타와", "difficulty": "normal", "alt": [], "prompt": "🌍 캐나다의 수도는? (토론토 아님!)"},
        {"answer": "브라질리아", "difficulty": "normal", "alt": [], "prompt": "🌍 브라질의 수도는? (리우 아님!)"},
        {"answer": "앙카라", "difficulty": "normal", "alt": [], "prompt": "🌍 튀르키예의 수도는? (이스탄불 아님!)"},
        {"answer": "베른", "difficulty": "normal", "alt": [], "prompt": "🌍 스위스의 수도는? (취리히 아님!)"},
        {"answer": "헬싱키", "difficulty": "normal", "alt": [], "prompt": "🌍 핀란드의 수도는?"},
        {"answer": "웰링턴", "difficulty": "hard", "alt": [], "prompt": "🌍 뉴질랜드의 수도는? (오클랜드 아님!)"},
        {"answer": "아디스아바바", "difficulty": "hard", "alt": ["아디스 아바바"], "prompt": "🌍 에티오피아의 수도는?"},
        {"answer": "아스타나", "difficulty": "hard", "alt": [], "prompt": "🌍 카자흐스탄의 수도는?"},
        {"answer": "네피도", "difficulty": "hard", "alt": [], "prompt": "🌍 미얀마의 수도는? (양곤 아님!)"},
        {"answer": "아부자", "difficulty": "hard", "alt": [], "prompt": "🌍 나이지리아의 수도는? (라고스 아님!)"},
    ],
}


def pick(crawled, fallback, min_n=3):
    return crawled if len(crawled) >= min_n else fallback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent.parent / "questions.json"))
    args = ap.parse_args()
    data = {}

    key = os.environ.get("TMDB_API_KEY")

    print("배우 크롤링 (네이버 연예뉴스)...")
    actors = crawl_actors()
    if len(actors) < 3 and key:
        print("  네이버 실패 → TMDB 인물 검색으로 대체...")
        actors = crawl_actors_tmdb(key)
    data["actor"] = pick(actors, FALLBACK["actor"])
    print(f"  → {len(data['actor'])}개 ({'크롤링' if len(actors) >= 3 else '내장 풀'})")

    print("가수 크롤링 (멜론 차트)...")
    singers = crawl_singers()
    data["singer"] = pick(singers, FALLBACK["singer"])
    print(f"  → {len(data['singer'])}개 ({'크롤링' if len(singers) >= 3 else '내장 풀'})")

    print("예능 출연진 크롤링 (위키피디아)...")
    variety = crawl_variety()
    data["variety"] = pick(variety, FALLBACK["variety"])
    print(f"  → {len(data['variety'])}개 ({'크롤링' if len(variety) >= 3 else '내장 풀'})")

    if key:
        print("영화 스틸컷 크롤링 (TMDB)...")
        movies = crawl_movies(key)
        data["movie"] = pick(movies, FALLBACK["movie"])
        print(f"  → {len(data['movie'])}개 ({'크롤링' if len(movies) >= 3 else '내장 풀'})")
    else:
        print("영화: TMDB_API_KEY 미설정 → 내장 풀 사용")
        print("  (무료 키 발급: https://www.themoviedb.org/settings/api)")
        data["movie"] = FALLBACK["movie"]

    data["capital"] = FALLBACK["capital"]
    print("수도: 내장 데이터 사용")

    out = Path(args.out)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n완료: {out.resolve()}")
    print("로컬 서버로 실행: python -m http.server 8000 → http://localhost:8000")


if __name__ == "__main__":
    main()
