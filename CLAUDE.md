# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 구조

```
search-agent/
├── backend/
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── search_agent.py   # Search 서브에이전트 (전용 LLM + search_tool + 서브그래프)
│   │   └── summary_agent.py  # Summary 서브에이전트 (전용 LLM + summary_tool + 서브그래프)
│   ├── agent.py              # Main 오케스트레이터 (전용 LLM + MainState 그래프)
│   ├── server.py             # FastAPI 서버 (엔드포인트)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html            # 검색 UI (Vanilla HTML/CSS/JS)
│   ├── style.css
│   └── Dockerfile
├── docker-compose.yml
├── .env                      # GOOGLE_API_KEY 설정
├── .venv/                    # Python 가상환경
└── pyproject.toml            # 의존성 명세
```

## 환경 설정

```bash
# 가상환경 활성화 (Windows)
.venv/Scripts/activate
```

`.env` 파일에 API 키 설정:
```
GOOGLE_API_KEY=...
```

## 실행

**Docker (권장):**
```bash
docker compose up --build
```
- 프론트엔드: http://localhost:3000
- 백엔드 API: http://localhost:8000

**로컬 실행:**
```bash
# 백엔드 (backend/ 디렉토리에서)
PYTHONIOENCODING=utf-8 ../.venv/Scripts/uvicorn server:app --port 8000 --reload

# 프론트엔드
../.venv/Scripts/python -m http.server 3000 --directory frontend
```

## API 엔드포인트

| Method | Path             | 설명                        |
|--------|------------------|-----------------------------|
| GET    | /health          | 서버 상태 확인              |
| POST   | /search/stream   | SSE 스트리밍 검색+요약      |

`/search/stream` SSE 이벤트 타입:
- `status` — 에이전트 진행 상황 (`node`: `"search"` \| `"summarize"`)
- `chunk` — Gemini 토큰 단위 출력 (`content`: string)
- `done` — 최종 결과 (`keyword`, `summary`, `search_results`)

## 아키텍처

### 멀티 에이전트 구조

Main 오케스트레이터가 LLM 툴 호출로 두 서브에이전트에 명령을 내리는 구조:

```
[사용자 키워드]
      ↓
orchestrator_node (Main LLM)
      ↓ tool_call: search_tool
tools node → search_agent 서브그래프 실행
      ↓
extract_results_node → search_results 상태 저장
      ↓
orchestrator_node (Main LLM)
      ↓ tool_call: summary_tool(search_results)
tools node → summary_agent 서브그래프 실행
      ↓
extract_results_node → summary 상태 저장
      ↓
orchestrator_node (Main LLM) → 최종 답변 생성 (스트리밍)
      ↓
     END
```

### backend/agents/search_agent.py

- **전용 LLM**: `gemini-2.5-flash` (temperature 0.1)
- **`search_tool`** (`@tool`): Main 오케스트레이터가 호출하는 진입점
- **`SearchState`**: `keyword`, `search_results`
- **서브그래프**: `search_node` → END (DuckDuckGo 5건 검색 후 포맷)

### backend/agents/summary_agent.py

- **전용 LLM**: `gemini-2.5-flash` (temperature 0.3)
- **`summary_tool`** (`@tool`): Main 오케스트레이터가 호출하는 진입점. `keyword`와 `search_results`를 인자로 받음
- **`SummaryState`**: `keyword`, `search_results`, `summary`
- **서브그래프**: `summarize_node` → END (핵심 요약 / 주요 포인트 / 참고 출처 형식)

### backend/agent.py

- **전용 LLM**: `gemini-2.5-flash` (temperature 0.2), `search_tool` + `summary_tool` 바인딩
- **`MainState`** (`TypedDict`):
  - `keyword` — 입력 키워드
  - `messages` — `add_messages`로 누적되는 대화 이력 (Annotated)
  - `search_results` — `extract_results_node`가 채우는 검색 원문
  - `summary` — `extract_results_node`가 채우는 요약 텍스트
- **노드 구성**:
  - `orchestrator_node` — Main LLM이 서브에이전트에 툴 호출로 명령
  - `tools` — `ToolNode`로 search_tool / summary_tool 실행
  - `extract_results_node` — ToolMessage에서 search_results/summary 추출해 상태 저장
- **라우팅**: 마지막 메시지에 tool_calls 있으면 `tools`, 없으면 `END`
- **주의**: 첫 호출 시 `HumanMessage`를 state에 함께 저장해야 Gemini의 메시지 순서 제약(user→model 교번) 충족

### backend/server.py

- `/search/stream` — `graph.astream_events(version="v2")`로 SSE 스트리밍
  - `on_chain_start "LangGraph"` 첫 이벤트에서 `top_run_id` 캡처 (서브그래프 이벤트 구분용)
  - `on_tool_start` 이벤트로 에이전트 진행 상태 전송
  - `on_chat_model_stream` + `langgraph_node == "orchestrator"` 필터로 최종 답변 토큰만 스트리밍
    - `isinstance(token, str)` 체크로 Gemini 2.5 thinking block(list 형식) 제외
  - `on_chain_end "LangGraph"` + `run_id == top_run_id` 조건으로 최상위 그래프 종료만 감지해 `done` 전송

### frontend/index.html

빌드 도구 없는 단일 HTML 파일. `fetch` + `ReadableStream`으로 SSE를 직접 파싱한다 (`EventSource`는 POST 미지원). 검색 진행 단계(웹 검색 → AI 요약)를 시각적으로 표시하고, LLM 토큰이 올 때마다 실시간 타이핑 효과를 구현한다.

## 주요 의존성

| 패키지 | 용도 |
|--------|------|
| `langgraph` | StateGraph 기반 멀티 에이전트 구성 |
| `langchain-google-genai` | Gemini LLM 연동 |
| `langchain-community` | DuckDuckGoSearchResults 도구 |
| `fastapi` + `uvicorn` | HTTP 서버 |
| `python-dotenv` | `.env` 로드 |
