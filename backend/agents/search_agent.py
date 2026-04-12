"""
Search 서브에이전트

DuckDuckGo로 웹 검색을 수행하고 결과를 정형화하여 반환한다.
LLM 없이 검색 도구만 사용하므로 빠르고 비용이 들지 않는다.

외부 인터페이스:
    search_tool — Main 오케스트레이터가 호출하는 @tool 함수
"""
from dotenv import load_dotenv
load_dotenv()

import logging
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_duckduckgo = DuckDuckGoSearchResults(num_results=5, output_format="list")


class SearchState(TypedDict):
    keyword: str
    search_results: str


def _search_node(state: SearchState) -> SearchState:
    keyword = state["keyword"]
    logger.info("[SearchAgent] 검색 시작 | keyword='%s'", keyword)

    results = _duckduckgo.invoke(keyword)

    if isinstance(results, list):
        formatted = ""
        for i, r in enumerate(results, 1):
            title   = r.get("title", "제목 없음")
            snippet = r.get("snippet", r.get("body", "내용 없음"))
            link    = r.get("link", r.get("href", ""))
            formatted += f"[{i}] {title}\n{snippet}\n출처: {link}\n\n"
        logger.info("[SearchAgent] 검색 완료 | keyword='%s' | %d건 수집 | %d자",
                    keyword, len(results), len(formatted))
    else:
        formatted = str(results)
        logger.info("[SearchAgent] 검색 완료 | keyword='%s' | raw 결과 %d자",
                    keyword, len(formatted))

    return {"search_results": formatted}


_graph = (
    StateGraph(SearchState)
    .add_node("search", _search_node)
    .set_entry_point("search")
    .add_edge("search", END)
    .compile()
)


@tool
def search_tool(keyword: str) -> str:
    """웹에서 키워드를 검색하여 최신 정보를 수집합니다."""
    result = _graph.invoke({"keyword": keyword, "search_results": ""})
    return result["search_results"]
