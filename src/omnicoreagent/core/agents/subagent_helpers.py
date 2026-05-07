import inspect
from html import escape


def resolve_agent(agent_name: str, sub_agents: list):
    for agent in sub_agents:
        if agent.name == agent_name:
            return agent
    raise ValueError(f"Sub-agent '{agent_name}' not found")


def build_kwargs(agent, provided_params: dict):
    sig = inspect.signature(agent.run)
    kwargs = {}

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        if name in provided_params:
            kwargs[name] = provided_params[name]
            continue

        if param.default is inspect.Parameter.empty:
            raise ValueError(
                f"Missing required parameter '{name}' for agent '{agent.name}'"
            )

    return kwargs


def build_sub_agents_observation_xml(observations: list[dict]) -> str:
    xml_lines = [
        "OBSERVATION RESULT FROM SUB-AGENTS",
        "<observations>",
    ]

    for obs in observations:
        agent_name = escape(str(obs.get("agent_name", "unknown")), quote=False)
        status = escape(str(obs.get("status", "unknown")), quote=False)
        output = escape(str(obs.get("output", "")), quote=False)

        xml_lines.append("  <observation>")
        xml_lines.append(f"    <agent_name>{agent_name}</agent_name>")
        xml_lines.append(f"    <status>{status}</status>")

        if status == "error":
            xml_lines.append(f"    <e>{output}</e>")
        else:
            xml_lines.append(f"    <o>{output}</o>")

        xml_lines.append("  </observation>")

    xml_lines.append("</observations>")
    xml_lines.append("END OF OBSERVATIONS")
    return "\n".join(xml_lines)
