"""Offline discovery probes; no provider calls or production-source mutations."""
import asyncio
import json
from types import SimpleNamespace
from omnicoreagent.core.agents.xml_parser import parse_action_or_answer
from omnicoreagent.core.agents.llm_response import extract_response_content
from omnicoreagent.core.tools.arguments import normalize_tool_args
from omnicoreagent.core.tools.observations import build_xml_observations_block
from omnicoreagent.core.tools.tool_executor import ToolExecutor
from omnicoreagent.core.agents.message_history import AgentMessageHistoryLoader
from omnicoreagent.core.agents.session_state import AgentSessionStateStore
from omnicoreagent.core.agents.base import BaseReactAgent
from omnicoreagent.core.memory_store.memory_router import MemoryRouter
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.subagents import build_subagent_tools
from omnicoreagent.core.context_manager import AgentLoopContextManager


def emit(name, value):
    print(json.dumps({'probe': name, 'result': value}, default=str))

call = '<tool_call><tool_name>ping</tool_name><parameters><x>1</x></parameters></tool_call>'
agent_call = '<agent_call><agent_name>worker</agent_name><parameters><query>task</query></parameters></agent_call>'
for name, response in {
    'plain': 'hello',
    'thought_only': '<thought>thinking</thought>',
    'answer_then_tool': '<final_answer>done</final_answer>' + call,
    'tool_inside_answer': '<final_answer>Example: ' + call + '</final_answer>',
    'all_three': agent_call + '<final_answer>done</final_answer>' + call,
    'malformed_tool_and_answer': '<tool_call><tool_name>ping</tool_name></tool_call><final_answer>done</final_answer>',
    'two_unwrapped_calls': call + call.replace('ping', 'pong'),
    'empty_collection': '<tool_calls></tool_calls>' + call,
    'fenced_call': '```xml\n' + call + '\n```',
    'aliases': '<tool_call><name>ping</name><args><x>1</x></args></tool_call>',
    'parameter_text': '<tool_call><name>ping</name><args>plain</args></tool_call>',
    'entities': '<tool_call><name>ping</name><args><x>&lt;tag&gt;</x></args></tool_call>',
}.items():
    result = parse_action_or_answer(response).model_dump(exclude_none=True)
    if 'error' in result:
        result['error'] = result['error'].splitlines()[0]
    emit(name, result)

emit('normalization', normalize_tool_args({'text': 'hello, world', 'numeric_string': '001', 'specs': '[{"name":"one"}]', 'false_string': 'false'}))
emit('observation_false_and_error_status', build_xml_observations_block([{'tool_name': 'ping', 'status': 'error', 'data': False, 'message': None}]))
try:
    extract_response_content(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[{'id': 'native'}]))]))
except Exception as e:
    emit('native_only_response', type(e).__name__ + ': ' + str(e))
emit('mcp_multi_content', ToolExecutor(None)._normalize_result('mcp', {}, SimpleNamespace(content=[SimpleNamespace(text='first'), SimpleNamespace(text='second')], isError=True)))

async def main():
    async def history_writer(**kwargs):
        pass
    for name, metadata in [('tool', {'agent_name': 'probe', 'tool': 'ping', 'args': {}, 'tool_call_id': 'a'}), ('subagent', {'agent_name': 'probe', 'sub_agent_results': True}), ('summary', {'agent_name': 'probe', 'type': 'history_summary', 'summarizes': ['a']})]:
        async def records(**kwargs):
            return [{'role': 'user' if name != 'tool' else 'tool', 'content': 'payload', 'metadata': metadata}]
        state = AgentSessionStateStore('probe').get('s', False)
        try:
            await AgentMessageHistoryLoader('probe').load(message_history=records, session_id='s', session_state=state)
            emit('load_' + name + '_metadata', len(state.messages))
        except Exception as e:
            emit('load_' + name + '_metadata', type(e).__name__ + ': ' + str(e))

    class StubModel:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.calls = []
        async def llm_call(self, messages):
            self.calls.append([{'role': m.role if hasattr(m, 'role') else m['role'], 'content': m.content if hasattr(m, 'content') else m.get('content', '')} for m in messages])
            return next(self.responses)

    agent = BaseReactAgent('probe', 5, 2, tool_offload_config={'enabled': False})
    async def empty_history(**kwargs):
        return []
    model = StubModel(['plain'] * 5)
    result = await agent.run(system_prompt='probe', query='q', llm_connection=model, add_message_to_history=history_writer, message_history=empty_history, session_id='errors')
    emit('five_parse_errors', {'answer': result['answer'], 'calls': len(model.calls), 'last_context_roles': [m['role'] for m in model.calls[-1]], 'state_after_run': agent._get_session_state('errors', False).state})

    registry = ToolRegistry()
    @registry.register_tool(name='ping')
    async def ping(x):
        return {'status': 'success', 'data': {'x': x}}
    memory = MemoryRouter('in_memory')
    model = StubModel([call, '<final_answer>done</final_answer>'])
    result = await agent.run(system_prompt='probe', query='q', llm_connection=model, add_message_to_history=memory.store_message, message_history=memory.get_messages, local_tools=registry, session_id='continued')
    emit('single_tool_active_context', model.calls[1])
    records = await memory.get_messages('continued', 'probe')
    emit('single_tool_stored_shapes', [{'role': m['role'], 'content': m['content'], 'metadata_keys': list(m['metadata'])} for m in records])
    model2 = StubModel(['<final_answer>second</final_answer>'])
    await agent.run(system_prompt='probe', query='again', llm_connection=model2, add_message_to_history=memory.store_message, message_history=memory.get_messages, local_tools=registry, session_id='continued')
    emit('continued_tool_session_context', model2.calls[0])

    class Factory:
        async def run_parallel_subagents(self, specs):
            return {'status': 'success', 'data': specs}
    spawn_registry = ToolRegistry()
    build_subagent_tools(Factory(), spawn_registry)
    args = normalize_tool_args({'subagents_json': '[{"name":"one","role":"r","task":"t","output_path":"out"}]'})
    try:
        emit('single_spawn_normalized', await spawn_registry.execute_tool('spawn_subagents', args))
    except Exception as e:
        emit('single_spawn_normalized', type(e).__name__ + ': ' + str(e))

    messages = [{'role': 'system', 'content': 'system'}, {'role': 'assistant', 'content': 'call', 'tool_calls': [{'id': str(i)} for i in range(5)]}] + [{'role': 'tool', 'content': 'result', 'tool_call_id': str(i)} for i in range(5)]
    managed = await AgentLoopContextManager({'enabled': True, 'preserve_recent': 4}).manage_context(messages)
    emit('context_splits_native_batch', [m['role'] for m in managed])

asyncio.run(main())
