"""
FastAPI 서버

GET  /health        — 헬스체크
POST /search/stream — SSE 스트리밍 검색+요약

SSE 이벤트 스펙:
    {"type": "status", "node": "search"|"summarize", "message": str}
    {"type": "chunk",  "content": str}               ← 오케스트레이터 최종 답변 토큰
    {"type": "done",   "keyword": str, "search_results": str, "summary": str}
"""
import asyncio
import json
import logging
import logging.config
import os
import re
from fastapi import FastAPI, HTTPException, Security
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from agent import graph

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
bearer_scheme = HTTPBearer()

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"}
    },
    "root": {"level": "INFO", "handlers": ["console"]},
})

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Search Agent API",
    description="키워드로 웹 검색 후 AI가 요약 정리해주는 API",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    keyword: str


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_rate_limit(exc: Exception) -> bool:
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def _retry_delay(exc: Exception) -> float:
    """에러 메시지에서 권장 대기 시간(초)을 파싱한다. 없으면 35초."""
    m = re.search(r'retry[^0-9]*?(\d+(?:\.\d+)?)\s*s', str(exc), re.IGNORECASE)
    return float(m.group(1)) + 1.0 if m else 35.0


def _user_error_msg(exc: Exception) -> str:
    if _is_rate_limit(exc):
        return "API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
    return "검색 중 오류가 발생했습니다. 다시 시도해 주세요."


@app.get("/health")
def health():
    return {"status": "ok"}


def _verify_token(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)):
    if not ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="서버에 ACCESS_TOKEN이 설정되지 않았습니다.")
    if credentials.credentials != ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")


@app.post("/search/stream")
async def search_stream(req: SearchRequest, _: None = Security(_verify_token)):
    if not req.keyword.strip():
        raise HTTPException(status_code=400, detail="keyword는 비워둘 수 없습니다.")
    logger.info("[Server] /search/stream 요청 | keyword='%s'", req.keyword)

    async def event_generator():
        MAX_RETRIES = 2
        attempt = 0

        while True:
            answer_parts: list[str] = []
            top_run_id: str | None = None
            initial_state = {
                "keyword": req.keyword,
                "messages": [],
                "search_results": "",
                "summary": "",
            }

            try:
                async for event in graph.astream_events(initial_state, version="v2"):
                    kind = event["event"]
                    name = event.get("name", "")

                    if kind == "on_chain_start" and name == "LangGraph" and top_run_id is None:
                        top_run_id = event.get("run_id")

                    elif kind == "on_tool_start":
                        if name == "search_tool":
                            yield _sse({"type": "status", "node": "search", "message": "검색 중..."})
                        elif name == "summary_tool":
                            yield _sse({"type": "status", "node": "summarize", "message": "요약 중..."})

                    elif kind == "on_chat_model_stream":
                        # orchestrator 노드의 최종 답변 토큰만 스트리밍
                        # isinstance(str) 체크: Gemini 2.5의 thinking block은 list로 오므로 제외
                        if event.get("metadata", {}).get("langgraph_node") == "orchestrator":
                            token = event["data"]["chunk"].content
                            if isinstance(token, str) and token:
                                answer_parts.append(token)
                                yield _sse({"type": "chunk", "content": token})

                    elif kind == "on_chain_end" and name == "LangGraph" and event.get("run_id") == top_run_id:
                        output = event["data"].get("output", {})
                        logger.info("[Server] 스트리밍 완료 | keyword='%s' | summary %d자",
                                    req.keyword, len(output.get("summary", "")))
                        yield _sse({
                            "type": "done",
                            "keyword": output.get("keyword", req.keyword),
                            "search_results": output.get("search_results", ""),
                            # summary_tool 결과가 없으면 오케스트레이터 답변 전체를 사용
                            "summary": output.get("summary") or "".join(answer_parts),
                        })

                break  # 정상 완료

            except Exception as exc:
                if _is_rate_limit(exc) and attempt < MAX_RETRIES:
                    delay = _retry_delay(exc)
                    attempt += 1
                    logger.warning("[Server] Rate limit 발생, %ds 후 재시도 (%d/%d) | keyword='%s'",
                                   int(delay), attempt, MAX_RETRIES, req.keyword)
                    yield _sse({"type": "reset"})
                    yield _sse({
                        "type": "status",
                        "node": "retry",
                        "message": f"API 요청 한도 초과 — {int(delay)}초 후 재시도합니다... ({attempt}/{MAX_RETRIES})",
                    })
                    await asyncio.sleep(delay)
                else:
                    logger.error("[Server] 처리 실패 | keyword='%s' | %s", req.keyword, exc)
                    yield _sse({"type": "error", "message": _user_error_msg(exc)})
                    break

    return StreamingResponse(event_generator(), media_type="text/event-stream")
