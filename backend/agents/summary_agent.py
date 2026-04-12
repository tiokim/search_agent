"""
Summary 서브에이전트

검색 결과를 Gemini LLM으로 분석하여 핵심 요약 / 주요 포인트 / 참고 출처 형식으로 반환한다.

외부 인터페이스:
    summary_tool — Main 오케스트레이터가 호출하는 @tool 함수
"""
from dotenv import load_dotenv
load_dotenv()

import logging
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3)

_SYSTEM = SystemMessage(content=(
    "당신은 검색 결과를 분석해서 핵심을 잘 정리하는 전문가입니다. "
    "주어진 검색 결과를 바탕으로 한국어로 명확하고 구조적인 요약을 작성하세요."
))

_USER_TEMPLATE = (
    "다음은 '{keyword}' 키워드로 검색한 결과입니다.\n\n"
    "{search_results}\n\n"
    "아래 형식으로 요약해주세요:\n"
    "## 핵심 요약\n"
    "3~5문장으로 핵심 내용 정리\n\n"
    "## 주요 포인트\n"
    "- 중요한 내용을 불릿 포인트로 나열\n\n"
    "## 참고 출처\n"
    "검색 결과에 포함된 주요 출처 링크 나열"
)


class SummaryState(TypedDict):
    keyword: str
    search_results: str
    summary: str


def _summarize_node(state: SummaryState) -> SummaryState:
    keyword = state["keyword"]
    logger.info("[SummaryAgent] 요약 시작 | keyword='%s' | 입력 %d자",
                keyword, len(state["search_results"]))

    user = HumanMessage(content=_USER_TEMPLATE.format(
        keyword=keyword,
        search_results=state["search_results"],
    ))
    response = llm.invoke([_SYSTEM, user])

    summary = response.content if isinstance(response.content, str) else ""
    logger.info("[SummaryAgent] 요약 완료 | keyword='%s' | 출력 %d자", keyword, len(summary))

    return {"summary": summary}


_graph = (
    StateGraph(SummaryState)
    .add_node("summarize", _summarize_node)
    .set_entry_point("summarize")
    .add_edge("summarize", END)
    .compile()
)


@tool
def summary_tool(keyword: str, search_results: str) -> str:
    """검색 결과를 분석하여 구조화된 한국어 요약을 생성합니다."""
    result = _graph.invoke({
        "keyword": keyword,
        "search_results": search_results,
        "summary": "",
    })
    return result["summary"]
