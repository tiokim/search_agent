"""
Main 오케스트레이터

Gemini LLM이 search_tool / summary_tool을 순서대로 호출하여
검색 → 요약 → 최종 답변을 생성한다.

그래프 구조:
    orchestrator ──(tool_calls?)──▶ tools ──▶ extract_results ──▶ orchestrator
                └──(없음)──▶ END

외부 인터페이스:
    graph — server.py 에서 astream_events()로 사용하는 컴파일된 그래프
"""
from dotenv import load_dotenv
load_dotenv()

import logging
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage

from agents.search_agent import search_tool
from agents.summary_agent import summary_tool

logger = logging.getLogger(__name__)

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
llm_with_tools = llm.bind_tools([search_tool, summary_tool])

_SYSTEM_PROMPT = SystemMessage(content=(
    "당신은 검색 및 요약 에이전트를 지휘하는 오케스트레이터입니다.\n"
    "사용자의 키워드에 대해 반드시 아래 순서로 작업을 수행하세요:\n"
    "1. search_tool을 호출하여 웹 검색을 수행하세요.\n"
    "2. search_tool의 결과를 summary_tool에 전달하여 요약을 생성하세요.\n"
    "3. 요약 결과를 바탕으로 사용자에게 최종 답변을 한국어로 제공하세요.\n"
    "도구 호출 없이 직접 답변하지 마세요."
))


class MainState(TypedDict):
    keyword: str
    messages: Annotated[list, add_messages]  # add_messages: 호출마다 누적
    search_results: str                       # extract_results_node가 채움
    summary: str                              # extract_results_node가 채움


def orchestrator_node(state: MainState) -> dict:
    new_msgs = []
    history = state["messages"]

    if not history:
        # 첫 호출: HumanMessage를 state에도 저장해야 Gemini의
        # user→model 교번 규칙을 만족할 수 있다.
        logger.info("[Orchestrator] 요청 수신 | keyword='%s'", state["keyword"])
        initial = HumanMessage(content=f"'{state['keyword']}' 키워드를 검색하고 요약해주세요.")
        new_msgs.append(initial)
        history = [initial]

    response = llm_with_tools.invoke([_SYSTEM_PROMPT] + history)
    new_msgs.append(response)

    if isinstance(response, AIMessage) and response.tool_calls:
        tool_names = [tc["name"] for tc in response.tool_calls]
        logger.info("[Orchestrator] 툴 호출 결정 | tools=%s", tool_names)
    else:
        answer = response.content if isinstance(response.content, str) else ""
        logger.info("[Orchestrator] 최종 답변 생성 완료 | keyword='%s' | %d자",
                    state["keyword"], len(answer))

    return {"messages": new_msgs}


def extract_results_node(state: MainState) -> dict:
    """ToolMessage 결과를 MainState의 개별 필드로 추출한다."""
    updates: dict = {}
    for msg in state["messages"]:
        if isinstance(msg, ToolMessage):
            if msg.name == "search_tool":
                updates["search_results"] = msg.content
                logger.info("[Orchestrator] search_tool 결과 수신 | %d자", len(msg.content))
            elif msg.name == "summary_tool":
                updates["summary"] = msg.content
                logger.info("[Orchestrator] summary_tool 결과 수신 | %d자", len(msg.content))
    return updates


def _route(state: MainState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


graph = (
    StateGraph(MainState)
    .add_node("orchestrator", orchestrator_node)
    .add_node("tools", ToolNode([search_tool, summary_tool]))
    .add_node("extract_results", extract_results_node)
    .set_entry_point("orchestrator")
    .add_conditional_edges("orchestrator", _route, {"tools": "tools", END: END})
    .add_edge("tools", "extract_results")
    .add_edge("extract_results", "orchestrator")
    .compile()
)
