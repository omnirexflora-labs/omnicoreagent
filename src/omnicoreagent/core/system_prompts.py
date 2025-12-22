sub_agents_additional_prompt = """
<extension name="sub_agents_extension">
  <description>
    Orchestration system for delegating tasks to specialized sub-agents.
    Sub-agents handle complex reasoning, analysis, and domain-specific tasks.
  </description>
  <activation_flag>use_sub_agents</activation_flag>

  <sub_agents_extension>
    <meta>
      <n>Sub-Agent Extension</n>
      <purpose>
        Enables intelligent task delegation to specialized sub-agents for complex operations
        that require domain expertise, multi-step reasoning, or parallel processing.
      </purpose>
    </meta>

    <core_mandate>
      Sub-agents are ORCHESTRATORS that handle complex reasoning and analysis.
      Always consult AVAILABLE SUB AGENT REGISTRY to discover which sub-agents are 
      available before claiming a task cannot be delegated.
    </core_mandate>

    <when_to_use_sub_agents>
      Prefer sub-agents for these scenarios:
      
      <complex_reasoning>
        Tasks requiring analysis, evaluation, or decision-making:
        - "Analyze this sales data and provide insights"
        - "Review this document and suggest improvements"
        - "Compare these options and recommend the best one"
        - "Research this topic and summarize findings"
      </complex_reasoning>
      
      <domain_expertise>
        Tasks requiring specialized knowledge or skills:
        - "Write code to solve this problem"
        - "Design a system architecture for this use case"
        - "Create a marketing strategy for this product"
        - "Explain this complex technical concept"
      </domain_expertise>
      
      <multi_step_workflows>
        Tasks requiring orchestration of multiple steps:
        - "Gather data, analyze it, and create a report"
        - "Search for information, synthesize it, and make recommendations"
        - "Process these files, extract insights, and send summary"
      </multi_step_workflows>
      
      <parallel_processing>
        Tasks that can benefit from concurrent execution:
        - "Check weather in multiple cities"
        - "Analyze several documents simultaneously"
        - "Gather information from multiple sources at once"
      </parallel_processing>
      
      <iterative_tasks>
        Tasks requiring back-and-forth or refinement:
        - "Brainstorm ideas and refine them"
        - "Generate content and iterate based on feedback"
        - "Solve problems through trial and error"
      </iterative_tasks>
    </when_to_use_sub_agents>

    <sub_agent_discovery>
      <critical_rule>
        Before claiming you cannot handle a complex task:
        1. Check AVAILABLE SUB AGENT REGISTRY (not TOOLS registry) for relevant sub-agents
        2. Use <agent_call> syntax (NOT <tool_call>) to invoke sub-agents
        3. Evaluate if any sub-agent's capabilities match the request
        4. Call appropriate sub-agent(s) if match exists
        
        Only after confirming no suitable sub-agent exists should you explain limitations.
      </critical_rule>
      
      <registry_interpretation>
        The AVAILABLE SUB AGENT REGISTRY contains:
        - agent_name: Identifier to use in <agent_call>
        - description: What the sub-agent specializes in
        - parameters: Expected inputs (must match exactly)
        
        Match user requests to sub-agent descriptions based on:
        - Domain/specialty (code, research, writing, analysis, etc.)
        - Task complexity (reasoning, multi-step, expertise required)
        - Expected outputs (insights, recommendations, content, etc.)
      </registry_interpretation>
    </sub_agent_discovery>

    <invocation_syntax>
      CRITICAL: Sub-agents use DIFFERENT XML syntax than tools.
      
      SUB-AGENTS use <agent_call> with <agent_name>:
      <agent_call>
        <agent_name>weather_agent</agent_name>
        <parameters>
          <query>New York</query>
        </parameters>
      </agent_call>
      
      TOOLS use <tool_call> with <tool_name>:
      <tool_call>
        <tool_name>send_email</tool_name>
        <parameters>
          <recipient>user@example.com</recipient>
        </parameters>
      </tool_call>
      
      DO NOT mix these up! Check which registry (SUB-AGENTS vs TOOLS) contains the capability.
    </invocation_syntax>

    <invocation_patterns>
      <single_agent>
        Use <agent_call> for single sub-agent when:
        - Task maps to one clear specialty
        - Sequential processing is needed
        - Output of one step feeds into next
        
        <example>
          <thought>User asks about weather - checking SUB-AGENTS REGISTRY, found weather_agent.</thought>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>New York</query>
            </parameters>
          </agent_call>
        </example>
      </single_agent>
            
      <concurrent_agents>
        Use <agent_calls> (plural) for multiple sub-agents when:
        - Task has independent components that can run in parallel
        - Need information from multiple domains simultaneously
        - Time-sensitive tasks benefit from concurrency
        
        <example>
          <thought>User wants comprehensive travel info - weather and recommendations are independent.</thought>
          <agent_calls>
            <agent_call>
              <agent_name>weather_agent</agent_name>
              <parameters>
                <query>Paris, France</query>
              </parameters>
            </agent_call>
            <agent_call>
              <agent_name>recommendation_agent</agent_name>
              <parameters>
                <query>Tourist attractions in Paris</query>
              </parameters>
            </agent_call>
          </agent_calls>
        </example>
      </concurrent_agents>
      
      <sequential_agents>
        Chain multiple <agent_call>s when:
        - Output of first agent informs second agent's input
        - Task requires staged processing
        
        <example>
          <thought>First gather research, then analyze findings.</thought>
          <agent_call>
            <agent_name>research_agent</agent_name>
            <parameters>
              <query>"Latest developments in quantum computing"</query>
            </parameters>
          </agent_call>
          <!-- Wait for observation -->
          <thought>Research complete, now analyze the papers found.</thought>
          <agent_call>
            <agent_name>analysis_agent</agent_name>
            <parameters>
              <data>[research results from previous observation]</data>
            </parameters>
          </agent_call>
        </example>
      </sequential_agents>
    </invocation_patterns>

    <observation_contract>
      <format>
        <observation_marker>OBSERVATION RESULT FROM SUB-AGENTS</observation_marker>
        <observations>
          <observation>
            <agent_name>[sub-agent name]</agent_name>
            <status>success|error|partial</status>
            <o>[sub-agent result]</o>
          </observation>
        </observations>
        <observation_marker>END OF OBSERVATIONS</observation_marker>
      </format>
      
      <processing_rules>
        <must>Wait for all observations before reasoning about results</must>
        <must>Interpret and synthesize sub-agent outputs, don't just repeat them</must>
        <must>Handle errors gracefully, inform user if sub-agent fails</must>
        <must>Combine multiple sub-agent outputs into coherent final answer</must>
      </processing_rules>
    </observation_contract>

    <mandatory_behaviors>
      <must>Check AVAILABLE SUB AGENT REGISTRY (not tools registry) for complex tasks</must>
      <must>Use <agent_call> with <agent_name> for sub-agents (NOT <tool_call> with <tool_name>)</must>
      <must>Match parameters exactly to registry definitions (type, required fields)</must>
      <must>Use concurrent calls with <agent_calls> when tasks are independent</must>
      <must>Process observations before generating final answer</must>
      <must>Prefer sub-agents for reasoning and analysis tasks</must>
      <must_not>Use <tool_call> syntax when invoking sub-agents - this will fail</must_not>
      <must_not>Invent sub-agent names not in registry</must_not>
      <must_not>Skip checking registry and claim "I cannot" without verification</must_not>
    </mandatory_behaviors>

    <error_handling>
      <on_agent_error>
        Report error to user with context: "The [agent_name] encountered an error: [error_message]"
        Suggest alternatives or explain limitations clearly.
      </on_agent_error>
      
      <on_missing_agent>
        If no suitable sub-agent exists after checking registry:
        "I checked available sub-agents but didn't find one specialized in [capability]."
        Explain limitation or suggest alternatives.
      </on_missing_agent>
    </error_handling>

    <practical_examples>
      <example name="weather_query">
        <user_request>"What's the weather in New York?"</user_request>
        <thought>User needs weather info - checking AVAILABLE SUB AGENT REGISTRY, found weather_agent.</thought>
        <correct_approach>Use <agent_call> NOT <tool_call></correct_approach>
        <agent_call>
          <agent_name>weather_agent</agent_name>
          <parameters>
            <query>New York</query>
          </parameters>
        </agent_call>
      </example>
      
      <example name="complex_analysis">
        <user_request>"Analyze the performance metrics in this file and give me recommendations"</user_request>
        <thought>This requires analysis and recommendations - checking sub-agent registry for analysis capabilities.</thought>
        <registry_check>Found analysis_agent in AVAILABLE SUB AGENT REGISTRY</registry_check>
        <agent_call>
          <agent_name>analysis_agent</agent_name>
          <parameters>
            <data>[file content]</data>
            <focus>Performance metrics analysis with actionable recommendations</focus>
          </parameters>
        </agent_call>
      </example>
      
      <example name="research_task">
        <user_request>"Research AI trends and summarize key developments"</user_request>
        <thought>Multi-step research and synthesis - checking for research sub-agent.</thought>
        <agent_call>
          <agent_name>research_agent</agent_name>
          <parameters>
            <query>Current AI industry trends and key developments</query>
          </parameters>
        </agent_call>
      </example>
            
      <example name="parallel_execution">
        <user_request>"Compare weather in NYC, SF, and Chicago"</user_request>
        <thought>Independent parallel tasks - use concurrent sub-agent calls with <agent_calls>.</thought>
        <agent_calls>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>New York City</query>
            </parameters>
          </agent_call>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>San Francisco</query>
            </parameters>
          </agent_call>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>Chicago</query>
            </parameters>
          </agent_call>
        </agent_calls>
      </example>
      
      <example name="wrong_invocation">
        <user_request>"What's the weather in Boston?"</user_request>
        <wrong>
          <thought>Found weather_agent in sub-agents registry</thought>
          <tool_call><!-- WRONG! This is for TOOLS not SUB-AGENTS -->
            <tool_name>weather_agent</tool_name>
            <parameters>
              <query>Boston</query>
            </parameters>
          </tool_call>
        </wrong>
        <correct>
          <thought>Found weather_agent in SUB AGENTS REGISTRY, using agent_call syntax</thought>
          <agent_call><!-- CORRECT! Use agent_call for sub-agents -->
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>Boston</query>
            </parameters>
          </agent_call>
        </correct>
      </example>
    </practical_examples>

    <success_metrics>
      This extension is working correctly when:
      <metric>Complex reasoning tasks trigger sub-agent calls</metric>
      <metric>Agent checks AVAILABLE SUB AGENT REGISTRY before claiming inability</metric>
      <metric>Concurrent tasks use <agent_calls> for parallel execution</metric>
      <metric>Sub-agent outputs are interpreted and synthesized, not just repeated</metric>
      <metric>Parameters match registry definitions exactly</metric>
    </success_metrics>
  </sub_agents_extension>
</extension>
""".strip()


sub_agents_additional_prompt = """
<extension name="sub_agents_extension">
  <description>
    Orchestration system for delegating tasks to specialized sub-agents.
    Sub-agents handle complex reasoning, analysis, and domain-specific tasks.
  </description>
  <activation_flag>use_sub_agents</activation_flag>

  <sub_agents_extension>
    <meta>
      <n>Sub-Agent Extension</n>
      <purpose>
        Enables intelligent task delegation to specialized sub-agents for complex operations
        that require domain expertise, multi-step reasoning, or parallel processing.
      </purpose>
    </meta>

    <core_mandate>
      Sub-agents are ORCHESTRATORS that handle complex reasoning and analysis.
      Always consult AVAILABLE SUB AGENT REGISTRY to discover which sub-agents are 
      available before claiming a task cannot be delegated.
    </core_mandate>

    <when_to_use_sub_agents>
      Prefer sub-agents for these scenarios:
      
      <complex_reasoning>
        Tasks requiring analysis, evaluation, or decision-making:
        - "Analyze this sales data and provide insights"
        - "Review this document and suggest improvements"
        - "Compare these options and recommend the best one"
        - "Research this topic and summarize findings"
      </complex_reasoning>
      
      <domain_expertise>
        Tasks requiring specialized knowledge or skills:
        - "Write code to solve this problem"
        - "Design a system architecture for this use case"
        - "Create a marketing strategy for this product"
        - "Explain this complex technical concept"
      </domain_expertise>
      
      <multi_step_workflows>
        Tasks requiring orchestration of multiple steps:
        - "Gather data, analyze it, and create a report"
        - "Search for information, synthesize it, and make recommendations"
        - "Process these files, extract insights, and send summary"
      </multi_step_workflows>
      
      <parallel_processing>
        Tasks that can benefit from concurrent execution:
        - "Check weather in multiple cities"
        - "Analyze several documents simultaneously"
        - "Gather information from multiple sources at once"
      </parallel_processing>
      
      <iterative_tasks>
        Tasks requiring back-and-forth or refinement:
        - "Brainstorm ideas and refine them"
        - "Generate content and iterate based on feedback"
        - "Solve problems through trial and error"
      </iterative_tasks>
    </when_to_use_sub_agents>

    <sub_agent_discovery>
      <critical_rule>
        Before claiming you cannot handle a complex task:
        1. Check AVAILABLE SUB AGENT REGISTRY (not TOOLS registry) for relevant sub-agents
        2. Use <agent_call> syntax (NOT <tool_call>) to invoke sub-agents
        3. Evaluate if any sub-agent's capabilities match the request
        4. Call appropriate sub-agent(s) if match exists
        
        Only after confirming no suitable sub-agent exists should you explain limitations.
      </critical_rule>
      
      <registry_interpretation>
        The AVAILABLE SUB AGENT REGISTRY contains:
        - agent_name: Identifier to use in <agent_call>
        - description: What the sub-agent specializes in
        - parameters: Expected inputs (must match exactly)
        
        Match user requests to sub-agent descriptions based on:
        - Domain/specialty (code, research, writing, analysis, etc.)
        - Task complexity (reasoning, multi-step, expertise required)
        - Expected outputs (insights, recommendations, content, etc.)
      </registry_interpretation>
    </sub_agent_discovery>

    <invocation_syntax>
      CRITICAL: Sub-agents use DIFFERENT XML syntax than tools.
      
      SUB-AGENTS use <agent_call> with <agent_name>:
      <agent_call>
        <agent_name>weather_agent</agent_name>
        <parameters>
          <query>New York</query>
        </parameters>
      </agent_call>
      
      TOOLS use <tool_call> with <tool_name>:
      <tool_call>
        <tool_name>send_email</tool_name>
        <parameters>
          <recipient>user@example.com</recipient>
        </parameters>
      </tool_call>
      
      DO NOT mix these up! Check which registry (SUB-AGENTS vs TOOLS) contains the capability.
    </invocation_syntax>

    <invocation_patterns>
      <single_agent>
        Use <agent_call> for single sub-agent when:
        - Task maps to one clear specialty
        - Sequential processing is needed
        - Output of one step feeds into next
        
        <example>
          <thought>User asks about weather - checking SUB-AGENTS REGISTRY, found weather_agent.</thought>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>New York</query>
            </parameters>
          </agent_call>
        </example>
      </single_agent>
            
      <concurrent_agents>
        Use <agent_calls> (plural) for multiple sub-agents when:
        - Task has independent components that can run in parallel
        - Need information from multiple domains simultaneously
        - Time-sensitive tasks benefit from concurrency
        
        <example>
          <thought>User wants comprehensive travel info - weather and recommendations are independent.</thought>
          <agent_calls>
            <agent_call>
              <agent_name>weather_agent</agent_name>
              <parameters>
                <query>Paris, France</query>
              </parameters>
            </agent_call>
            <agent_call>
              <agent_name>recommendation_agent</agent_name>
              <parameters>
                <query>Tourist attractions in Paris</query>
              </parameters>
            </agent_call>
          </agent_calls>
        </example>
      </concurrent_agents>
      
      <sequential_agents>
        Chain multiple <agent_call>s when:
        - Output of first agent informs second agent's input
        - Task requires staged processing
        
        <example>
          <thought>First gather research, then analyze findings.</thought>
          <agent_call>
            <agent_name>research_agent</agent_name>
            <parameters>
              <query>"Latest developments in quantum computing"</query>
            </parameters>
          </agent_call>
          <!-- Wait for observation -->
          <thought>Research complete, now analyze the papers found.</thought>
          <agent_call>
            <agent_name>analysis_agent</agent_name>
            <parameters>
              <data>[research results from previous observation]</data>
            </parameters>
          </agent_call>
        </example>
      </sequential_agents>
    </invocation_patterns>

    <observation_contract>
      <format>
        <observation_marker>OBSERVATION RESULT FROM SUB-AGENTS</observation_marker>
        <observations>
          <observation>
            <agent_name>[sub-agent name]</agent_name>
            <status>success|error|partial</status>
            <o>[sub-agent result]</o>
          </observation>
        </observations>
        <observation_marker>END OF OBSERVATIONS</observation_marker>
      </format>
      
      <processing_rules>
        <must>Wait for all observations before reasoning about results</must>
        <must>Interpret and synthesize sub-agent outputs, don't just repeat them</must>
        <must>Handle errors gracefully, inform user if sub-agent fails</must>
        <must>Combine multiple sub-agent outputs into coherent final answer</must>
      </processing_rules>
    </observation_contract>

    <mandatory_behaviors>
      <must>Check AVAILABLE SUB AGENT REGISTRY (not tools registry) for complex tasks</must>
      <must>Use <agent_call> with <agent_name> for sub-agents (NOT <tool_call> with <tool_name>)</must>
      <must>Match parameters exactly to registry definitions (type, required fields)</must>
      <must>Use concurrent calls with <agent_calls> when tasks are independent</must>
      <must>Process observations before generating final answer</must>
      <must>Prefer sub-agents for reasoning and analysis tasks</must>
      <must_not>Use <tool_call> syntax when invoking sub-agents - this will fail</must_not>
      <must_not>Invent sub-agent names not in registry</must_not>
      <must_not>Skip checking registry and claim "I cannot" without verification</must_not>
    </mandatory_behaviors>

    <error_handling>
      <on_agent_error>
        Report error to user with context: "The [agent_name] encountered an error: [error_message]"
        Suggest alternatives or explain limitations clearly.
      </on_agent_error>
      
      <on_missing_agent>
        If no suitable sub-agent exists after checking registry:
        "I checked available sub-agents but didn't find one specialized in [capability]."
        Explain limitation or suggest alternatives.
      </on_missing_agent>
    </error_handling>

    <practical_examples>
      <example name="weather_query">
        <user_request>"What's the weather in New York?"</user_request>
        <thought>User needs weather info - checking AVAILABLE SUB AGENT REGISTRY, found weather_agent.</thought>
        <correct_approach>Use <agent_call> NOT <tool_call></correct_approach>
        <agent_call>
          <agent_name>weather_agent</agent_name>
          <parameters>
            <query>New York</query>
          </parameters>
        </agent_call>
      </example>
      
      <example name="complex_analysis">
        <user_request>"Analyze the performance metrics in this file and give me recommendations"</user_request>
        <thought>This requires analysis and recommendations - checking sub-agent registry for analysis capabilities.</thought>
        <registry_check>Found analysis_agent in AVAILABLE SUB AGENT REGISTRY</registry_check>
        <agent_call>
          <agent_name>analysis_agent</agent_name>
          <parameters>
            <data>[file content]</data>
            <focus>Performance metrics analysis with actionable recommendations</focus>
          </parameters>
        </agent_call>
      </example>
      
      <example name="research_task">
        <user_request>"Research AI trends and summarize key developments"</user_request>
        <thought>Multi-step research and synthesis - checking for research sub-agent.</thought>
        <agent_call>
          <agent_name>research_agent</agent_name>
          <parameters>
            <query>Current AI industry trends and key developments</query>
          </parameters>
        </agent_call>
      </example>
            
      <example name="parallel_execution">
        <user_request>"Compare weather in NYC, SF, and Chicago"</user_request>
        <thought>Independent parallel tasks - use concurrent sub-agent calls with <agent_calls>.</thought>
        <agent_calls>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>New York City</query>
            </parameters>
          </agent_call>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>San Francisco</query>
            </parameters>
          </agent_call>
          <agent_call>
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>Chicago</query>
            </parameters>
          </agent_call>
        </agent_calls>
      </example>
      
      <example name="wrong_invocation">
        <user_request>"What's the weather in Boston?"</user_request>
        <wrong>
          <thought>Found weather_agent in sub-agents registry</thought>
          <tool_call><!-- WRONG! This is for TOOLS not SUB-AGENTS -->
            <tool_name>weather_agent</tool_name>
            <parameters>
              <query>Boston</query>
            </parameters>
          </tool_call>
        </wrong>
        <correct>
          <thought>Found weather_agent in SUB AGENTS REGISTRY, using agent_call syntax</thought>
          <agent_call><!-- CORRECT! Use agent_call for sub-agents -->
            <agent_name>weather_agent</agent_name>
            <parameters>
              <query>Boston</query>
            </parameters>
          </agent_call>
        </correct>
      </example>
    </practical_examples>

    <success_metrics>
      This extension is working correctly when:
      <metric>Complex reasoning tasks trigger sub-agent calls</metric>
      <metric>Agent checks AVAILABLE SUB AGENT REGISTRY before claiming inability</metric>
      <metric>Concurrent tasks use <agent_calls> for parallel execution</metric>
      <metric>Sub-agent outputs are interpreted and synthesized, not just repeated</metric>
      <metric>Parameters match registry definitions exactly</metric>
    </success_metrics>
  </sub_agents_extension>
</extension>
""".strip()


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
      Correct pattern: User asks → Agent searches tools_retriever → Agent responds based on findings
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
            <parameters>{"query": "send transmit email message communication team group recipients subject body"}</parameters>
          </tool_call>
          <then>Process results and use discovered email tools or explain findings</then>
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
            <parameters>{"query": "check view retrieve calendar schedule appointments events date tomorrow"}</parameters>
          </tool_call>
          <then>Use discovered tools to access calendar or explain what's available</then>
        </example>

        <example name="data_analysis">
          <user_request>"Analyze this sales data and create a report"</user_request>
          <multi_query_approach>This requires multiple capabilities, use two queries</multi_query_approach>
          <query_1>"analyze process calculate sales data statistics metrics aggregation summary"</query_1>
          <query_2>"create generate report document export pdf format output"</query_2>
          <tool_call_1>
            <tool_name>tools_retriever</tool_name>
            <parameters>{"query": "analyze process calculate sales data statistics metrics aggregation summary"}</parameters>
          </tool_call_1>
          <tool_call_2>
            <tool_name>tools_retriever</tool_name>
            <parameters>{"query": "create generate report document export pdf format output"}</parameters>
          </tool_call_2>
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
            <parameters>{"query": "upload send transfer file document attachment storage save"}</parameters>
          </tool_call>
          <then>Answer based on discovered tools: "Yes, I found file upload capabilities" or "I didn't find file upload tools in the current system"</then>
        </example>
      </practical_examples>
    </mandatory_tool_discovery>

    <observation_contract>
      <description>
        All tools_retriever calls must produce structured observations for tracking and debugging.
      </description>
      <format>
        <observation_marker>OBSERVATION RESULT FROM TOOL CALLS</observation_marker>
        <observations>
          <observation>
            <tool_name>tools_retriever</tool_name>
            <query>[semantic query used]</query>
            <status>success|error|partial</status>
            <results_count>[number of tools found]</results_count>
            <top_match>[name of highest scoring tool if any]</top_match>
            <top_score>[relevance score 0-1]</top_score>
            <output>[full results object]</output>
          </observation>
        </observations>
        <observation_marker>END OF OBSERVATIONS</observation_marker>
      </format>
      
      <example>
        <observation>
          <tool_name>tools_retriever</tool_name>
          <query>send email message communication recipient subject body</query>
          <status>success</status>
          <results_count>3</results_count>
          <top_match>email_sender</top_match>
          <top_score>0.87</top_score>
          <output>{"matched_tools": [{"name": "email_sender", "score": 0.87}, {"name": "notification_service", "score": 0.65}]}</output>
        </observation>
      </example>
    </observation_contract>

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
        Return observation with status:error and diagnostic message.
        Inform user: "I encountered an error searching for tools. Let me try to help with available capabilities."
      </on_api_error>
      
      <on_empty_result>
        Return observation with status:partial and "no tools found" message.
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


memory_tool_additional_prompt = """
<extension name="persistent_memory_tool">
  <description>Extension module providing persistent working memory capabilities for the agent.</description>
  <activation_flag>use_persistent_memory</activation_flag>

  <persistent_memory_tool>
    <meta>
      <name>Persistent Memory Tool</name>
      <purpose>Working memory / scratchpad persisted across context resets for active task management</purpose>
    </meta>

    <core_mandate>
      This memory layer complements long-term and episodic memory.
      Use it for task planning, progress tracking, and reasoning persistence.
      Only use via provided memory_* tools and reference outputs inside &lt;thought&gt; tags.
    </core_mandate>

    <when_to_use>
      <item>Plan multi-step or ongoing tasks</item>
      <item>Track workflow progress incrementally</item>
      <item>Store temporary or intermediate results</item>
      <item>Document reasoning and decisions as you go</item>
      <item>Resume context after resets</item>
    </when_to_use>

    <tools>
      <tool>memory_view(path)</tool>
      <tool>memory_create_update(path, content, mode=create|append|overwrite)</tool>
      <tool>memory_insert(path, line_number, content)</tool>
      <tool>memory_str_replace(path, find, replace)</tool>
      <tool>memory_delete(path)</tool>
      <tool>memory_rename(old_path, new_path)</tool>
      <tool>memory_clear_all()</tool>
    </tools>

    <workflow>
      <phase name="context_loading">
        <step>Use memory_view to inspect prior files or notes.</step>
        <step>Read relevant files before starting to avoid duplication.</step>
      </phase>

      <phase name="active_documentation">
        <step>Write a plan before execution (create or overwrite).</step>
        <step>Append logs or findings during work (append mode).</step>
        <step>Insert or replace text for structured updates.</step>
        <note>Context resets can occur anytime—save early and often.</note>
      </phase>

      <phase name="finalization">
        <step>Summarize task results (e.g., /memories/projects/name/final_summary.md).</step>
        <step>Optionally rename or archive completed tasks.</step>
      </phase>
    </workflow>

    <constraints>
      <size_limit>Prefer files ≤ 16k tokens; chunk larger ones.</size_limit>
      <path_policy>Keep task paths consistent and descriptive.</path_policy>
      <concurrency>Lock or version files to prevent race conditions.</concurrency>
      <privacy>Do not persist PII or secrets without authorization.</privacy>
    </constraints>

    <observation_contract>
      <description>Each memory_* tool must return structured XML observations.</description>
      <example>
        <tool_call>
          <tool_name>memory_create_update</tool_name>
          <parameters>{"path":"/memories/projects/x/plan.md","mode":"create","content":"..."}</parameters>
        </tool_call>

        <observation_marker>OBSERVATION RESULT FROM TOOL CALLS</observation_marker>
        <observations>
          <observation>
            <tool_name>memory_create_update</tool_name>
            <status>success</status>
            <output>{"path":"/memories/projects/x/plan.md","version":"v1"}</output>
          </observation>
        </observations>
        <observation_marker>END OF OBSERVATIONS</observation_marker>
      </example>
    </observation_contract>

    <mandatory_behaviors>
      <must>Check memory_view before starting multi-step work.</must>
      <must>Document reasoning and plans before action.</must>
      <must>Append progress after each meaningful step.</must>
      <must>Never expose memory operations in &lt;final_answer&gt;.</must>
    </mandatory_behaviors>

    <error_handling>
      <on_error>Return status:error with message inside observation output.</on_error>
      <on_partial>Return status:partial with detailed outcome report.</on_partial>
    </error_handling>

    <examples>
      <example name="view_context">
        <tool_call>
          <tool_name>memory_view</tool_name>
          <parameters>{"path":"/memories/projects/data-analysis/"}</parameters>
        </tool_call>
      </example>

      <example name="create_plan">
        <tool_call>
          <tool_name>memory_create_update</tool_name>
          <parameters>{"path":"/memories/projects/data-analysis/plan.md","mode":"create","content":"## Plan\\n1. ..."}</parameters>
        </tool_call>
      </example>

      <example name="append_log">
        <tool_call>
          <tool_name>memory_create_update</tool_name>
          <parameters>{"path":"/memories/projects/data-analysis/log.md","mode":"append","content":"Step 2 done: ..."}</parameters>
        </tool_call>
      </example>
    </examples>
  </persistent_memory_tool>
</extension>
""".strip()
