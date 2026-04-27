"""
🏠 부동산 추천 Agent — CLI 진입점

LangGraph 기반 멀티턴 대화형 부동산 추천 Agent.
국토부 실거래가 API + Tavily 웹 검색을 도구로 사용하며,
조건이 부족하면 되묻고(clarify) 검증 실패 시 재검색(verify retry)합니다.

추가 인터페이스:
  - FastAPI:  uvicorn api:app --reload
  - Streamlit: streamlit run streamlit.py
  - 동시 실행: ./run.sh
"""

import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from agent.graph import build_graph

console = Console()


def _print_header() -> None:
    console.print(Panel(
        "[bold cyan]🏠 부동산 추천 Agent[/bold cyan]\n"
        "[dim]LangGraph + 국토부 실거래가 API · 자연어로 매물 검색하기[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print(
        "\n[dim]예: 마포구에서 월세 보증금 3000에 월 80 이하 투룸 / "
        "강남구 매매 20억 이하 아파트 / 종료하려면 'q' 입력[/dim]\n"
    )


def _check_env() -> None:
    """필수/선택 환경변수를 점검하고 안내."""
    missing_required = []
    if not os.getenv("OPENAI_API_KEY"):
        missing_required.append("OPENAI_API_KEY (필수)")

    if missing_required:
        console.print(
            f"[red]환경변수 누락: {', '.join(missing_required)}[/red]\n"
            "[yellow].env 파일을 생성하고 키를 설정해 주세요. .env.example 참고.[/yellow]"
        )
        sys.exit(1)

    if not os.getenv("MOLIT_API_KEY"):
        console.print(
            "[yellow]⚠ MOLIT_API_KEY 미설정 — 실거래가 조회가 빈 결과를 반환합니다.[/yellow]\n"
            "[dim]   data.go.kr 에서 무료 발급 후 .env에 추가하면 실데이터 추천이 가능합니다.[/dim]\n"
        )
    if not os.getenv("TAVILY_API_KEY"):
        console.print(
            "[dim]ℹ TAVILY_API_KEY 미설정 — 동네 정보 웹 검색은 건너뜁니다.[/dim]\n"
        )


def _print_condition(condition: dict, lifestyle: dict) -> None:
    if not condition and not (lifestyle or {}).get("raw_keywords"):
        return

    table = Table(title="파싱된 조건", show_header=True, header_style="bold magenta")
    table.add_column("항목", style="cyan")
    table.add_column("값", style="white")

    labels = {
        "region": "위치",
        "deal_type": "거래유형",
        "max_deposit": "최대 보증금(만)",
        "max_monthly": "최대 월세(만)",
        "max_price": "최대 매매가(만)",
        "min_area": "최소 면적(m²)",
        "property_type": "방종류",
        "min_households": "최소 세대수",
        "parking_required": "주차 필수",
        "building_structure": "구조",
        "max_subway_minutes": "역까지(분)",
        "min_rooms": "최소 방수",
        "min_bathrooms": "최소 욕실수",
        "preferred_floor": "선호 층",
        "direction": "방향",
        "max_building_age": "최대 연식(년)",
    }
    for key, label in labels.items():
        val = condition.get(key)
        if val is not None:
            table.add_row(label, str(val))

    if lifestyle:
        for key, label in [
            ("activities", "라이프 활동"),
            ("atmosphere", "동네 분위기"),
            ("amenities", "편의시설"),
            ("raw_keywords", "생활권 원문"),
        ]:
            val = lifestyle.get(key)
            if val:
                table.add_row(label, str(val))

    console.print(table)


def _print_results(state: dict) -> None:
    condition = state.get("condition", {})
    lifestyle = state.get("lifestyle", {}) or {}
    filtered = state.get("filtered_results", []) or []
    search_count = len(state.get("search_results", []) or [])

    console.print(Rule("[bold green]🔍 검색 결과[/bold green]", style="green"))
    _print_condition(condition, lifestyle)

    console.print(
        f"\n[yellow]📊 실거래 수집: {search_count}건 → 필터·검증 통과: {len(filtered)}건[/yellow]\n"
    )

    if filtered:
        table = Table(title="추천 매물", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("제목", style="cyan")
        table.add_column("위치", style="white")
        table.add_column("거래", style="white")
        table.add_column("가격", style="green")
        table.add_column("면적", style="white")
        table.add_column("층", style="white")
        table.add_column("점수", style="yellow")

        for p in filtered:
            price = p.get("price", {}) or {}
            deal_type = p.get("deal_type", "")
            deposit = price.get("deposit", 0)
            monthly = price.get("monthly", 0)
            if deal_type == "월세":
                price_str = f"보 {deposit:,} / 월 {monthly:,}"
            elif deal_type == "전세":
                price_str = f"전세 {deposit:,}"
            elif deal_type == "매매":
                price_str = f"매매 {deposit:,}"
            else:
                price_str = f"{deposit:,}/{monthly:,}"

            table.add_row(
                str(p.get("id", "-")),
                str(p.get("title", "-"))[:30],
                str(p.get("region", "-"))[:20],
                deal_type or "-",
                price_str,
                f"{p.get('area_m2', '-')}m²",
                f"{p.get('floor', '-')}/{p.get('total_floors', '-')}",
                str(p.get("score", 0)),
            )
        console.print(table)
        console.print()

    console.print(Rule("[bold green]✨ 추천[/bold green]", style="green"))
    console.print(Panel(
        state.get("recommendations", "추천 결과가 없습니다."),
        border_style="green",
        padding=(1, 2),
    ))


def _read_input(prompt: str = "🏡 ") -> str:
    """EOF/Ctrl+C를 안전하게 처리하는 입력 헬퍼."""
    console.print(f"[bold cyan]{prompt}[/bold cyan] ", end="")
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def run_session(graph) -> None:
    """한 번의 검색 세션 — clarify가 필요하면 재질문 루프를 돈다."""
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    user_input = _read_input("🏡 어떤 매물을 찾으시나요?")
    if user_input.lower() in ("q", "quit", "exit", "종료"):
        return

    if not user_input:
        console.print("[red]입력값이 없습니다.[/red]")
        return

    while True:
        console.print("\n[dim]🔄 Agent 실행 중...[/dim]")

        try:
            state = graph.invoke({"user_input": user_input}, config=config)
        except Exception as e:
            console.print(f"[red]❌ 실행 오류: {e}[/red]")
            console.print_exception()
            return

        # clarify 단계: 추가 정보 요청 → 재질문 루프
        if state.get("clarify_question"):
            console.print(Rule("[bold yellow]🤔 추가 정보 필요[/bold yellow]", style="yellow"))
            console.print(Panel(
                state["clarify_question"],
                border_style="yellow",
                padding=(1, 2),
            ))
            answer = _read_input("👉")
            if answer.lower() in ("q", "quit", "exit", "종료"):
                return
            if not answer:
                console.print("[red]답변이 없어 종료합니다.[/red]")
                return
            user_input = answer
            continue

        _print_results(state)
        return


def main() -> None:
    _print_header()
    _check_env()

    graph = build_graph()

    while True:
        run_session(graph)
        console.print(
            "\n[bold white]🔄 다른 매물을 더 찾으시겠어요? (y/n):[/bold white] ", end=""
        )
        try:
            again = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            again = "n"
        if again != "y":
            console.print("[yellow]👋 이용해 주셔서 감사합니다![/yellow]")
            break
        console.print()


if __name__ == "__main__":
    main()
