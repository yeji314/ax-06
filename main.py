import os
from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.table import Table

from agent.graph import build_graph
from agent.state import AgentState

console = Console()


def print_header():
    console.print(Panel(
        "[bold cyan]🏠 부동산 추천 Agent[/bold cyan]\n"
        "[dim]LangGraph 기반 State Machine으로 최적의 매물을 찾아드립니다[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))


def print_results(state: AgentState):
    console.print(Rule("[bold green]🔍 검색 결과[/bold green]", style="green"))

    condition = state.get("condition", {})
    if any(v for v in condition.values()):
        table = Table(title="파싱된 조건", show_header=True, header_style="bold magenta")
        table.add_column("항목", style="cyan")
        table.add_column("값", style="white")

        labels = {
            "region": "위치",
            "deal_type": "거래유형",
            "max_deposit": "최대보증금(만원)",
            "max_monthly": "최대월세(만원)",
            "max_price": "최대가격(만원)",
            "min_area": "최소면적(m²)",
            "property_type": "방종류",
            "min_households": "최소 세대수",
            "parking_required": "주차 필수",
            "building_structure": "계단식/복도식",
            "max_subway_minutes": "역까지 최대(분)",
            "min_rooms": "최소 방 개수",
            "min_bathrooms": "최소 욕실 개수",
            "preferred_floor": "선호 층",
            "direction": "선호 방향",
            "max_building_age": "최대 연식(년)",
        }
        for key, label in labels.items():
            val = condition.get(key)
            if val is not None:
                table.add_row(label, str(val))
        console.print(table)
        console.print()

    search_count = len(state.get("search_results", []))
    filtered_count = len(state.get("filtered_results", []))
    console.print(f"[yellow]📊 1차 검색: {search_count}개 → 필터링 후: {filtered_count}개[/yellow]")
    console.print()

    filtered = state.get("filtered_results", [])
    if filtered:
        detail_table = Table(
            title="추천 매물 상세", show_header=True, header_style="bold magenta"
        )
        detail_table.add_column("매물", style="cyan", no_wrap=False)
        detail_table.add_column("위치", style="white")
        detail_table.add_column("금액대", style="white")
        detail_table.add_column("면적", style="white")
        detail_table.add_column("세대수", style="white")
        detail_table.add_column("주차", style="white")
        detail_table.add_column("구조", style="white")
        detail_table.add_column("역까지", style="white")
        detail_table.add_column("방/욕실", style="white")
        detail_table.add_column("층/방향", style="white")
        detail_table.add_column("연식", style="white")

        for p in filtered:
            price = p.get("price", {})
            dt = p.get("deal_type", "")
            if dt == "월세":
                price_str = f"보{price.get('deposit', 0)}/월{price.get('monthly', 0)}"
            elif dt == "전세":
                price_str = f"전세 {price.get('deposit', 0)}"
            else:
                price_str = f"매매 {price.get('deposit', 0)}"

            detail_table.add_row(
                f"{p.get('id', '')} {p.get('title', '')}",
                f"{p.get('region', '')} {p.get('district', '')}",
                price_str,
                f"{p.get('area_m2', '-')}m²",
                f"{p.get('households', '-')}세대",
                "가능" if p.get("parking") else "불가",
                str(p.get("building_structure", "-")),
                f"{p.get('subway_minutes', '-')}분",
                f"{p.get('rooms', '-')}/{p.get('bathrooms', '-')}",
                f"{p.get('floor', '-')}층/{p.get('direction', '-')}",
                f"{p.get('built_year', '-')}",
            )
        console.print(detail_table)
        console.print()

    console.print(Rule("[bold green]✨ 추천 매물[/bold green]", style="green"))
    console.print(Panel(
        state.get("recommendations", "추천 결과가 없습니다."),
        border_style="green",
        padding=(1, 2),
    ))


def run_agent(user_input: str):
    console.print(f"\n[bold white]📝 입력:[/bold white] {user_input}\n")

    graph = build_graph()

    initial_state: AgentState = {
        "user_input": user_input,
        "condition": {},
        "is_valid": False,
        "error_message": None,
        "search_results": [],
        "filtered_results": [],
        "recommendations": "",
        "retry_count": 0,
        "messages": [],
    }

    console.print("[bold cyan]🔄 Agent 실행 중...[/bold cyan]")
    final_state = graph.invoke(initial_state)

    print_results(final_state)
    return final_state


def main():
    print_header()
    console.print(
        "\n[dim]부동산 조건을 자연어로 입력하세요. "
        "(예: 마포구에서 월세 보증금 3000에 월 80 이하 투룸)[/dim]\n"
    )

    while True:
        try:
            console.print("[bold cyan]🏡 어떤 매물을 찾으시나요?[/bold cyan] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]👋 종료합니다.[/yellow]")
            break

        if not user_input:
            console.print("[red]입력값이 없습니다. 다시 입력해주세요.[/red]")
            continue

        if user_input.lower() in ("q", "quit", "exit", "종료"):
            console.print("[yellow]👋 이용해 주셔서 감사합니다![/yellow]")
            break

        try:
            run_agent(user_input)
        except Exception as e:
            console.print(f"[red]❌ 오류가 발생했습니다: {e}[/red]")
            import traceback
            console.print_exception()

        console.print()
        console.print("[bold white]🔄 다시 검색하시겠습니까? (y/n):[/bold white] ", end="")
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
