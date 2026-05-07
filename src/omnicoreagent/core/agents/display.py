def show_tool_response(agent_name, tool_name, tool_args, observation):
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.pretty import Pretty
    from rich.text import Text

    content = Group(
        Text(agent_name.upper(), style="bold magenta"),
        Text(f"-> Calling tool: {tool_name}", style="bold blue"),
        Text("-> Tool input:", style="bold yellow"),
        Pretty(tool_args),
        Text("-> Tool response:", style="bold green"),
        Pretty(observation),
    )

    panel = Panel.fit(content, title="TOOL CALL LOG", border_style="bright_black")
    Console().print(panel)


def show_sub_agent_call_result(agent_call_result):
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.pretty import Pretty
    from rich.text import Text

    blocks = []
    parent_agent = agent_call_result.get("agent_name", "unknown_agent")
    agent_calls = agent_call_result.get("agent_calls", [])
    outputs = agent_call_result.get("output", [])

    blocks.append(Text(f"PARENT AGENT: {parent_agent.upper()}", style="bold magenta"))
    output_map = {output["agent_name"]: output for output in outputs}

    for call in agent_calls:
        agent_name = call.get("agent", "unknown_agent")
        params = call.get("parameters", {})
        result = output_map.get(agent_name, {})
        status = result.get("status", "unknown")
        output = result.get("output")

        blocks.append(Text(""))
        blocks.append(Text(f"-> Sub-agent: {agent_name}", style="bold blue"))
        blocks.append(Text("-> Parameters:", style="bold yellow"))
        blocks.append(Pretty(params))
        blocks.append(
            Text(
                f"-> Status: {status}",
                style="bold green" if status == "success" else "bold red",
            )
        )
        blocks.append(Text("-> Output:", style="bold cyan"))
        blocks.append(Pretty(output))

    panel = Panel.fit(
        Group(*blocks),
        title="SUB-AGENT EXECUTION TRACE",
        border_style="bright_black",
    )
    Console().print(panel)
