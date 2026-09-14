import inspect


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
