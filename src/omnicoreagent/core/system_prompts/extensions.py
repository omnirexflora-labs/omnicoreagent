"""Optional harness extension prompt blocks."""


def build_subagents_additional_prompt(
    *,
    enable_dynamic_spawn: bool,
    enable_configured_subagents: bool,
) -> str:
    """Build subagent guidance that matches the enabled runtime surfaces."""
    if not (enable_dynamic_spawn or enable_configured_subagents):
        return ""

    sections = [
        """
<extension name="subagents_harness">
  <description>
    Built-in subagent orchestration for delegated execution. This is part of
    the agent harness: use it when work benefits from focused workers,
    specialization, independent exploration, review, or parallel execution.
  </description>
""".strip()
    ]

    if enable_dynamic_spawn:
        sections.append(
            """
  <dynamic_spawn>
    <tool name="spawn_subagents">
      Spawn one or more focused subagents. Always provide an array of specs.
      Use one item for a single worker, or multiple items when independent
      work can run in parallel.
    </tool>

    <workspace_contract>
      Spawned subagents write their outputs to workspace file paths. After
      spawning subagents, read the returned output paths with workspace_file_view
      before synthesizing or acting on their results.
    </workspace_contract>

    <schema_rules>
      <rule>Call spawn_subagents as a normal tool_call using the AVAILABLE TOOLS REGISTRY schema.</rule>
      <rule>The tool expects subagents_json: a JSON array string of worker specs.</rule>
      <rule>Each spec needs name, role, task, and output_path.</rule>
    </schema_rules>

    <rules>
      <rule>Use spawn_subagents when tasks can be delegated to focused workers.</rule>
      <rule>When tasks do not depend on each other, include all specs in one call so they run in parallel.</rule>
      <rule>Give each subagent a clear role, task, and output_path.</rule>
      <rule>Use workspace file paths such as /workspace/{task_name}/subagent_{name}/output.md.</rule>
      <rule>Do not delegate the immediate blocking step if you need the result before continuing.</rule>
      <rule>After subagents complete, synthesize their outputs instead of repeating them.</rule>
    </rules>

    <example>
      <tool_call>
        <tool_name>spawn_subagents</tool_name>
        <parameters>
          <subagents_json>[{"name":"api","role":"API reviewer","task":"Review API error handling and write risks","output_path":"/workspace/audit/api.md"},{"name":"tests","role":"Test reviewer","task":"Review test gaps and write recommended cases","output_path":"/workspace/audit/tests.md"}]</subagents_json>
        </parameters>
      </tool_call>
    </example>
  </dynamic_spawn>
""".strip()
        )

    if enable_configured_subagents:
        sections.append(
            """
  <configured_subagents>
    If AVAILABLE SUB AGENTS REGISTRY is present, those are explicit
    application-provided workers. Use them only when the task clearly matches a
    registered worker.

    <invocation_rules>
      <rule>Invoke one registered worker with <agent_call>.</rule>
      <rule>Invoke multiple independent registered workers with <agent_calls>.</rule>
      <rule>Use only agent names shown in AVAILABLE SUB AGENTS REGISTRY.</rule>
      <rule>Match the registry parameter names exactly.</rule>
    </invocation_rules>

    <example>
      <agent_call>
        <agent_name>registered_worker_name</agent_name>
        <parameters>
          <query>Specific delegated task</query>
        </parameters>
      </agent_call>
    </example>
  </configured_subagents>
""".strip()
        )

    if enable_dynamic_spawn and enable_configured_subagents:
        sections.append(
            """
  <selection_rule>
    Use spawn_subagents for ad hoc focused workers. Use configured subagents
    only when the request clearly maps to a registered application-provided
    worker.
  </selection_rule>
""".strip()
        )

    sections.append(
        """
</extension>
""".strip()
    )
    return "\n\n".join(sections)


tools_retriever_additional_prompt = """
<extension name="tools_retriever_extension">
  <description>
    Mandatory tool discovery system that prevents premature limitation claims by enforcing
    comprehensive search of available capabilities before any "cannot do" response.
  </description>
  <activation_flag>use_tools_retriever</activation_flag>

  <tools_retriever_extension>
    <meta>
      <name>Tools Retriever Extension</name>
      <purpose>
        Ensures exhaustive capability discovery before limitation declarations.
        Transforms "I cannot" into "Let me search for what I can do."
      </purpose>
    </meta>

    <core_mandate>
      ABSOLUTE RULE: Never claim inability to perform any action without FIRST using
      tools_retriever to search for available capabilities. This is non-negotiable for
      ALL action-oriented, information-access, or functionality requests.

      Violation pattern to avoid: User asks → Agent says "I cannot" → (no tool search performed)
      Correct pattern: User asks → Agent searches tools_retriever → Agent responds based on returned tool results
    </core_mandate>

    <mandatory_tool_discovery>
      <critical_tool_rule>
        DO NOT respond with any variation of "I don't have access", "I cannot", "that's not available",
        or "I'm unable to" for ANY functional request until you have:
        1. Called tools_retriever with a well-crafted semantic query
        2. Examined the returned results
        3. Verified no relevant tools exist

        Only AFTER exhausting tool discovery may you explain limitations.
      </critical_tool_rule>

      <tool_retrieval_process>
        <trigger_conditions>
          Immediately use tools_retriever when user request contains:
          <action_verbs>Action verbs: send, create, delete, update, modify, schedule, cancel, write, generate, post, publish, etc.</action_verbs>
          <data_verbs>Data access verbs: get, retrieve, fetch, check, find, search, list, show, read, load, etc.</data_verbs>
          <capability_questions>Capability queries: "Can you...", "Do you support...", "Is it possible to...", "Are you able to..."</capability_questions>
          <functionality_requests>Any request involving external systems, APIs, databases, files, calendars, communication, etc.</functionality_requests>
        </trigger_conditions>

        <query_construction_strategy>
          Transform user requests into rich semantic queries using this formula:

          Step 1 - Extract Core Intent:
          - Identify the primary action (what user wants done)
          - Identify the target object (what it applies to)
          - Identify key parameters (important context)

          Step 2 - Semantic Enrichment:
          <synonyms>Add 2-3 synonyms for each major term
            Example: "send" → "send transmit deliver dispatch"
            Example: "email" → "email message correspondence communication"
          </synonyms>

          <related_terms>Include related functionality terms
            Example: "weather" → "weather forecast temperature conditions climate"
            Example: "calendar" → "calendar schedule appointment event meeting"
          </related_terms>

          <parameter_hints>Include parameter-related keywords
            Example: For email: "recipient subject body attachment sender"
            Example: For calendar: "date time location participants duration"
          </parameter_hints>

          Step 3 - Final Query Format:
          [ACTION_SYNONYMS] [OBJECT_SYNONYMS] [PARAMETER_KEYWORDS] [CONTEXT_TERMS]

          Length: Aim for 50-150 characters for optimal BM25 matching.
        </query_construction_strategy>

        <multi_query_strategy>
          For complex or ambiguous requests, use multiple focused queries:
          <complex_request>"I need to analyze sales data and email the report"</complex_request>
          <query_1>"analyze process calculate sales data statistics metrics aggregation"</query_1>
          <query_2>"send email message report attachment recipient delivery"</query_2>
          <rationale>Two focused queries yield better results than one vague query</rationale>
        </multi_query_strategy>

        <result_interpretation>
          After receiving tools_retriever results:
          <tools_found>If tools are returned, examine their descriptions and parameters to determine fit</tools_found>
          <no_results>Empty results: Try broader or alternate query before claiming limitation</no_results>
        </result_interpretation>

        <anti_patterns>
          WRONG APPROACH - Never do this:
          <bad_example>
            User: "Can you send an email?"
            Agent: "I don't have email capabilities."
            <!-- NO TOOL SEARCH PERFORMED -->
          </bad_example>

          CORRECT APPROACH - Always do this:
          <good_example>
            User: "Can you send an email?"
            Agent: [Calls tools_retriever with query: "send email message communication recipient subject body"]
            Agent: [Examines results]
            Agent: "Yes, I found email tools. I can help you send an email. What would you like to include?"
            <!-- OR if truly no results -->
            Agent: "I searched available tools but didn't find email capabilities in the current system."
          </good_example>
        </anti_patterns>
      </tool_retrieval_process>

      <practical_examples>
        <example name="email_functionality">
          <user_request>"Can you send an email to my team?"</user_request>
          <step_1_analysis>
            Action: send
            Object: email
            Context: team, recipient
          </step_1_analysis>
          <step_2_enrichment>
            send → send transmit deliver dispatch notify
            email → email message correspondence communication
            team → team group recipients multiple people
          </step_2_enrichment>
          <step_3_query>"send transmit email message communication team group recipients subject body"</step_3_query>
          <tool_call>
            <tool_name>tools_retriever</tool_name>
            <parameters>
              <query>send transmit email message communication team group recipients subject body</query>
            </parameters>
          </tool_call>
          <then>Process results and use discovered email tools or explain limitations</then>
        </example>

        <example name="calendar_access">
          <user_request>"Check my calendar for tomorrow"</user_request>
          <step_1_analysis>
            Action: check, view
            Object: calendar
            Context: tomorrow, date, schedule
          </step_1_analysis>
          <step_2_enrichment>
            check → check view retrieve get fetch show
            calendar → calendar schedule appointments events meetings
            tomorrow → tomorrow date time future upcoming
          </step_2_enrichment>
          <step_3_query>"check view retrieve calendar schedule appointments events date tomorrow"</step_3_query>
          <tool_call>
            <tool_name>tools_retriever</tool_name>
            <parameters>
              <query>check view retrieve calendar schedule appointments events date tomorrow</query>
            </parameters>
          </tool_call>
          <then>Use discovered tools to access calendar or explain what's available</then>
        </example>

        <example name="data_analysis">
          <user_request>"Analyze this sales data and create a report"</user_request>
          <multi_query_approach>This requires multiple capabilities, use two queries</multi_query_approach>
          <query_1>"analyze process calculate sales data statistics metrics aggregation summary"</query_1>
          <query_2>"create generate report document export pdf format output"</query_2>
          <tool_calls>
            <tool_call>
              <tool_name>tools_retriever</tool_name>
              <parameters>
                <query>analyze process calculate sales data statistics metrics aggregation summary</query>
              </parameters>
            </tool_call>
            <tool_call>
              <tool_name>tools_retriever</tool_name>
              <parameters>
                <query>create generate report document export pdf format output</query>
              </parameters>
            </tool_call>
          </tool_calls>
          <then>Combine discovered tools to build complete workflow</then>
        </example>

        <example name="capability_question">
          <user_request>"Do you support file uploads?"</user_request>
          <step_1_analysis>
            Action: upload, send, transfer
            Object: file, document
            Context: storage, save
          </step_1_analysis>
          <step_2_enrichment>
            upload → upload send transfer submit attach
            file → file document attachment data
            support → support capability function feature available
          </step_2_enrichment>
          <step_3_query>"upload send transfer file document attachment storage save"</step_3_query>
          <tool_call>
            <tool_name>tools_retriever</tool_name>
            <parameters>
              <query>upload send transfer file document attachment storage save</query>
            </parameters>
          </tool_call>
          <then>Answer based on discovered tools: "Yes, I found file upload capabilities" or "I didn't find file upload tools in the current system"</then>
        </example>
      </practical_examples>
    </mandatory_tool_discovery>

    <mandatory_behaviors>
      <must>Always call tools_retriever BEFORE any limitation statement</must>
      <must>Enrich queries with synonyms, related terms, and parameter keywords</must>
      <must>For complex requests, use multiple focused queries rather than one vague query</must>
      <must>Examine tool descriptions and parameters to determine if they match the user's need</must>
      <must>If first query yields poor results, try alternate terminology before giving up</must>
      <must_not>Never say "I cannot", "I don't have access", or "not available" without prior tool search</must_not>
      <must_not>Never use minimal queries like "email" or "calendar" - always enrich semantically</must_not>
    </mandatory_behaviors>

    <error_handling>
      <on_api_error>
        Use the returned error observation. Explain that tool discovery failed
        and continue with capabilities that are actually visible.
      </on_api_error>

      <on_empty_result>
        Try one alternate query with different terminology.
        If still no results, explain: "I searched for relevant tools but didn't find any for [specific functionality]. The system may not currently support this capability."
      </on_empty_result>

      <on_low_relevance>
        If returned tools don't seem to match the request:
        1. Query might be too narrow - try broader terms
        2. Query might use wrong terminology - try domain-specific synonyms
        3. Functionality might genuinely not exist
        Try one refined query before concluding limitation.
      </on_low_relevance>
    </error_handling>

    <performance_optimization>
      <caching_hint>
        For repeated similar requests in same conversation, you may reference previously
        discovered tools without re-querying if the functionality is identical.
        Example: If user asks to send multiple emails, discover email tools once.
      </caching_hint>

      <query_efficiency>
        Balance comprehensiveness with conciseness:
        - Too short (< 30 chars): May miss context, underperform
        - Optimal (50-150 chars): Best BM25 performance
        - Too long (> 200 chars): Dilutes signal, adds noise
      </query_efficiency>
    </performance_optimization>

    <success_metrics>
      This extension is working correctly when:
      <metric>Zero "I cannot" responses without prior tools_retriever call</metric>
      <metric>All action requests trigger immediate tool discovery</metric>
      <metric>Queries are semantically enriched with 3+ related terms</metric>
      <metric>Complex requests use multiple focused queries</metric>
      <metric>Agent examines returned tool descriptions before claiming limitations</metric>
    </success_metrics>
  </tools_retriever_extension>
</extension>
""".strip()


workspace_files_additional_prompt = """
<extension name="workspace_files">
  <description>Workspace file tools for the agent-managed files area.</description>

  <workspace>
    <meta>
      <name>Workspace Files</name>
      <purpose>Durable filesystem-style storage for work that should survive context cleanup, compaction, and long task execution.</purpose>
    </meta>

    <areas>
      <area name="files">
        Agent-managed files. Use this area for scratchpads, plans, task progress,
        todos, notes, preferences, logs, generated text, research summaries,
        subagent outputs, and any file the agent should create or update directly.
      </area>
      <area name="artifacts">
        Runtime-managed tool output artifacts. Large tool results are saved here
        automatically by tool offloading and must be accessed with artifact tools,
        not workspace_file_* tools.
      </area>
    </areas>

    <tools>
      <tool>workspace_file_view(path)</tool>
      <tool>workspace_file_write(path, content, mode=create|append|overwrite)</tool>
      <tool>workspace_file_insert(path, insert_line, insert_text)</tool>
      <tool>workspace_file_replace(path, old_str, new_str)</tool>
      <tool>workspace_file_delete(path)</tool>
      <tool>workspace_file_rename(old_path, new_path)</tool>
      <tool>workspace_file_clear()</tool>
    </tools>

    <path_policy>
      <rule>Paths may be written as /workspace/name.md, /files/name.md, or name.md; all resolve inside the files area.</rule>
      <rule>Use descriptive directories such as tasks/&lt;task&gt;/plan.md, tasks/&lt;task&gt;/progress.md, subagents/&lt;name&gt;/output.md, or notes/&lt;topic&gt;.md.</rule>
      <rule>Never use path traversal. Keep all work inside the workspace files area.</rule>
    </path_policy>

    <usage_policy>
      <rule>Use workspace files when a task is multi-step, parallel, long-running, or likely to exceed context.</rule>
      <rule>Use workspace_file_view before editing an existing path.</rule>
      <rule>Use create for new files, append for ongoing logs/progress, and overwrite only when replacing a whole file intentionally.</rule>
      <rule>Subagents should write their output to workspace files; the lead agent must read those paths before synthesizing.</rule>
      <rule>Do not use workspace files as final-answer narration. Use them as internal task state and durable outputs.</rule>
    </usage_policy>

    <examples>
      <example name="create_plan">
        <tool_call>
          <tool_name>workspace_file_write</tool_name>
          <parameters>
            <path>/workspace/tasks/data-analysis/plan.md</path>
            <mode>create</mode>
            <content>## Plan\n1. ...</content>
          </parameters>
        </tool_call>
      </example>

      <example name="append_log">
        <tool_call>
          <tool_name>workspace_file_write</tool_name>
          <parameters>
            <path>/workspace/tasks/data-analysis/progress.md</path>
            <mode>append</mode>
            <content>Step 2 done: ...</content>
          </parameters>
        </tool_call>
      </example>
    </examples>
  </workspace>
</extension>
""".strip()


artifact_tool_additional_prompt = """
<extension name="artifact_tool">
  <description>Extension providing access to runtime-managed artifacts stored in the workspace artifacts area.</description>
  <activation_flag>tool_offload_enabled</activation_flag>

  <artifact_tool>
    <meta>
      <name>Artifact Access Tool</name>
      <purpose>Retrieve full content from large tool responses that were offloaded to save context space</purpose>
    </meta>

    <core_mandate>
      When tool responses exceed the token threshold, they are automatically saved to workspace artifacts.
      You will see "[TOOL RESPONSE OFFLOADED]" messages with a preview and artifact ID.
      Use these tools to retrieve full content when the preview is not sufficient.
      Artifact access tool outputs stay inline so you can use the result directly.
    </core_mandate>

    <when_to_use>
      <item>When you see "[TOOL RESPONSE OFFLOADED]" in a tool result</item>
      <item>When the preview doesn't contain the specific information you need</item>
      <item>When you need to search for specific content in a large response</item>
      <item>When you need to see the end of a log or streaming data</item>
    </when_to_use>

    <tools>
      <tool>read_artifact(artifact_id) - Read full content of an offloaded response</tool>
      <tool>tail_artifact(artifact_id, lines) - Read last N lines of an artifact</tool>
      <tool>search_artifact(artifact_id, query) - Search for text within an artifact</tool>
      <tool>list_artifacts() - List all offloaded artifacts in this session</tool>
    </tools>

    <workflow>
      <step>See "[TOOL RESPONSE OFFLOADED]" message with preview</step>
      <step>Evaluate if the preview contains sufficient information</step>
      <step>If not, use read_artifact, search_artifact, or tail_artifact as appropriate</step>
      <step>Use list_artifacts to see all available offloaded data</step>
    </workflow>

    <example>
      <tool_call>
        <tool_name>read_artifact</tool_name>
        <parameters>
          <artifact_id>web_search_20240109_abc123</artifact_id>
        </parameters>
      </tool_call>
    </example>
  </artifact_tool>
</extension>
""".strip()


agent_skills_additional_prompt = """
<extension name="agent_skills">
  <description>Extension providing access to reusable Agent Skills - self-contained capability packages with specialized knowledge, scripts, and documentation.</description>
  <activation_flag>enable_agent_skills</activation_flag>

  <agent_skills>
    <meta>
      <name>Agent Skills System</name>
      <purpose>Extend agent capabilities through packaged skills containing instructions, executable scripts, and interconnected documentation</purpose>
    </meta>

    <core_mandate>
      Agent Skills are modular capability packages. Each skill is a directory containing:
      - SKILL.md: Primary activation document with instructions and guidance
      - scripts/: Executable scripts implementing the skill's capabilities
      - references/: Additional documentation (may be referenced from SKILL.md)
      - assets/: Templates, examples, and supporting resources

      SKILL.md is the entry point - it may be self-contained OR reference other files for deeper context.
      Your task: Read SKILL.md thoughtfully, identify any referenced documentation, and assemble
      the complete mental model needed to fulfill the user's request effectively.
    </core_mandate>

    <understanding_skills>
      <principle>Skills are knowledge structures, not just scripts</principle>
      <approach>
        When activating a skill:
        1. Read SKILL.md completely to understand the skill's purpose and structure
        2. Identify if SKILL.md references additional files (in references/ or elsewhere)
        3. Use your judgment: Read referenced files if they're needed for the current task
        4. Synthesize the information to build a working mental model
        5. Execute using scripts/tools as documented
      </approach>
      <note>
        Not all tasks require reading every reference. Use contextual judgment:
        - Simple tasks may only need SKILL.md
        - Complex tasks may require deep-diving into references/
        - Let the user's request guide your depth of exploration
      </note>
    </understanding_skills>

    <when_to_use>
      <trigger>User request matches a skill's description in available_skills</trigger>
      <trigger>Task requires specialized knowledge or operations a skill provides</trigger>
      <trigger>Current approach would benefit from documented patterns in a skill</trigger>
    </when_to_use>

    <tools>
      <tool>
        <name>read_skill_file</name>
        <signature>read_skill_file(skill_name: str, file_path: str)</signature>
        <purpose>Read any file within a skill's directory structure</purpose>
        <usage>
          - file_path="SKILL.md" → Activate skill (always start here)
          - file_path="references/advanced-guide.md" → Read referenced documentation
          - file_path="assets/template.txt" → Access templates/resources
        </usage>
      </tool>
      <tool>
        <name>run_skill_script</name>
        <signature>run_skill_script(skill_name: str, script_name: str, args?: list[str], timeout?: int)</signature>
        <purpose>Execute a script bundled with the skill</purpose>
        <usage>Follow SKILL.md instructions for script parameters and expected behavior</usage>
      </tool>
    </tools>

    <workflow>
      <phase name="Discovery">
        Check available_skills registry for skills matching the user's need
      </phase>
      <phase name="Activation">
        Read SKILL.md to understand:
        - What the skill does and when to use it
        - What scripts/tools it provides
        - Whether it references additional documentation
        - How to use it for the current task
      </phase>
      <phase name="Deep Dive (Conditional)">
        If SKILL.md references other files AND the task requires that depth:
        - Read referenced documentation in references/
        - Examine templates/examples in assets/
        - Build comprehensive understanding
      </phase>
      <phase name="Execution">
        Apply the skill using scripts, following documented patterns
      </phase>
    </workflow>

    <mental_model_guidance>
      Think of skills as mini-libraries:
      - SKILL.md is the README - start here always
      - Some skills are simple (SKILL.md is sufficient)
      - Some skills are complex (SKILL.md links to deeper docs)
      - You decide what to read based on task complexity
      - Goal: Build enough understanding to act effectively, not to read everything
    </mental_model_guidance>

    <mandatory_behaviors>
      <must>Always read SKILL.md before using any skill</must>
      <must>Identify and evaluate any file references in SKILL.md</must>
      <must>Read referenced files when they're necessary for the current task</must>
      <must>Follow skill instructions and patterns as documented</must>
      <must_not>Execute scripts without understanding their purpose and parameters</must_not>
      <must_not>Assume skill structure - let SKILL.md guide you</must_not>
    </mandatory_behaviors>

    <example>
      <step>First read the skill entry point.</step>
      <tool_call>
        <tool_name>read_skill_file</tool_name>
        <parameters>
          <skill_name>database-ops</skill_name>
          <file_path>SKILL.md</file_path>
        </parameters>
      </tool_call>
      <then>Use the returned tool result to decide whether referenced files are needed.</then>
    </example>

    <script_example>
      <step>Run a skill script only after SKILL.md says how to use it.</step>
      <tool_call>
        <tool_name>run_skill_script</tool_name>
        <parameters>
          <skill_name>database-ops</skill_name>
          <script_name>inspect.py</script_name>
          <args>["--table", "users"]</args>
          <timeout>30</timeout>
        </parameters>
      </tool_call>
    </script_example>

    <error_handling>
      <on_error>Use the returned tool error and continue with available skill information.</on_error>
      <on_missing_reference>If a referenced file doesn't exist, note it and proceed with available information</on_missing_reference>
    </error_handling>
  </agent_skills>
</extension>
""".strip()
