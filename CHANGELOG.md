# Changelog

## Unreleased — native tool runtime

- Remove executor/governance history callbacks and redundant redaction/JSON
  round-trips. The native runtime writes each guarded/offloaded result once.
  Decode native arguments once; preserve ordinary dictionaries with data/message
  keys instead of guessing they are result envelopes.

- Detect native tool loops across complete rounds with concrete provider/server
  identities. Ignore execution order and generated IDs, retain changing sibling
  results as progress, and halt repeated invalid or unavailable calls consistently.

- Remove the unused legacy tool resolver, obsolete control types and unused default
  config wrapper. Add a concurrency regression test and installed-MCP diagnostic;
  reopen integration gates for the SDK incompatibilities documented in the audit.

- Breaking: remove Cencori from supported providers and delete its dedicated SDK
  adapter. Existing Cencori configurations now fail validation; all supported
  providers use LiteLLM. Remove the unused direct OpenAI SDK dependency.

- Route production OpenAI async, sync and streaming calls through current LiteLLM;
  remove the temporary direct-SDK workaround and test-only routing shim. Pass API
  keys per connection and keep retry ownership at the runtime boundary.

- Refresh direct, optional, development and build dependencies to the September 14,
  2026 stable-release baseline; regenerate the complete dependency lock. OpenAI
  SDK stays on 2.54.0 because LiteLLM 1.100.1 requires `<3.0.0`.
- Breaking: require Python 3.12–3.14 to support the new dependency baseline,
  including NumPy 2.5.3. Development and CI continue to use Python 3.12.
- Install all project extras and development groups in CI, key environment caches
  by `uv.lock`, and make the existing Ruff rules explicit across default changes.

- Replace XML control with native function calls, strict JSON argument schemas,
  stable provider call IDs and correlated JSON tool results. XML task content is
  ordinary text and is never executable control syntax.
- Add live provider, Python agent and SSE text streaming using the shared loop;
  bounded queues, child actor identity and cancellation close upstream streams.
- Remove the obsolete XML parsers/observation executors, unused summary-memory
  constructor prompt/export, and global tool index.
  Discovery now unlocks schemas for the next turn in an isolated per-run catalog.
- Keep complete tool interactions together through context selection and history;
  retain historical XML sessions as data. History loading failures now surface.
- Return explicit success/error termination status through serving and background
  outcomes. Dynamic spawn accepts a `subagents` array, replacing `subagents_json`.
- Breaking: removed RouterAgent, ParallelAgent and SequentialAgent APIs, examples
  and documentation, with no fallback. Normal, deep and background runs remain.



All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed
- Conversation history now accepts tool, summary, delegation, and custom metadata
  without dropping prior turns. Reconstructed tool batches exclude orphan and
  duplicate results and retain their call identifiers.

### Changed
- Agent execution now uses native tool calls and plain final text. XML-looking text
  is task content. Tool call IDs and typed JSON arguments survive execution/history.
- Dynamic spawning accepts a `subagents` array, replacing `subagents_json`.

### Removed
- **Breaking change:** Deleted `RouterAgent`, `ParallelAgent`, and `SequentialAgent`,
  including their package exports, examples, and documentation. These APIs have no
  compatibility aliases or fallback implementations.

## [0.1.18] - 2025-06-19

### Changed
- **BREAKING CHANGE**: Removed local ChromaDB support for performance optimization
  - Local ChromaDB fallback system has been completely removed
  - Users must now explicitly configure a vector database provider
  - No more automatic fallback to local storage
  - This eliminates the 81+ second startup delay from ChromaDB Rust bindings
- **Performance Improvement**: Vector database modules now load only when explicitly needed
  - Lazy loading prevents unnecessary imports during package initialization
  - Faster startup when vector DB is disabled
  - Better resource management

### Added
- **Required Configuration**: Users must set `OMNI_MEMORY_PROVIDER` to one of:
  - `qdrant-remote` (recommended default)
  - `chroma-remote` 
  - `chroma-cloud`
- **Clear Error Messages**: Better feedback when vector DB configuration is missing

### Removed
- Local ChromaDB persistence and warmup system
- Automatic fallback mechanisms
- Module-level vector DB initialization
- ChromaDB local client type

### Migration Guide
- **Before**: `ENABLE_VECTOR_DB=true` would automatically use local ChromaDB
- **After**: Must set both `ENABLE_VECTOR_DB=true` AND `OMNI_MEMORY_PROVIDER=qdrant-remote` (or other provider)
- **Impact**: Faster startup, but requires explicit configuration

## [0.1.17] - 2025-05-28

### Added
- OAuth Authentication Support:
  - Added OAuth 2.0 authentication flow for MCP servers
  - Support for multiple authentication methods:
    - OAuth 2.0
    - Bearer token
    - Custom headers
  - Flexible authentication configuration in server settings
  - Secure credential management
- Enhanced Server Configuration:
  - Updated server configuration format to support OAuth
  - Added authentication method specification
  - Improved server connection security
  - Better error handling for authentication failures

### Changed
- Updated server configuration examples to include OAuth support
- Enhanced documentation for authentication methods
- Improved security section in README
- Updated server management commands documentation

### Fixed
- Improved authentication error handling
- Enhanced security documentation
- Updated configuration validation for authentication methods

## [0.1.16] - 2025-05-16

### Added
- New Streamable HTTP Transport:
  - Added support for streamable HTTP transport protocol
  - Efficient data streaming capabilities
  - Configurable timeout and read timeout settings
  - Header support for authentication and custom configurations
- Dynamic Server Management:
  - New `/add_servers:<config.json>` command to add one or more servers
  - New `/remove_server:<server_name>` command to remove servers
  - Support for adding multiple servers from a single configuration file
  - Real-time server capability updates after adding/removing servers
- Enhanced Server Configuration:
  - Added streamable HTTP server configuration examples
  - Updated documentation for new transport type
  - Improved server management commands documentation

### Changed
- Updated README with new server management commands
- Enhanced server configuration examples to include streamable HTTP
- Improved documentation for transport protocols
- Updated interactive commands section with new server management features

### Fixed
- Improved server connection handling
- Enhanced error messages for server management commands
- Updated documentation formatting for consistency

## [0.1.15] - 2025-05-05

### Added
- Token & Usage Management:
  - `/api_stats` command to view total tokens used, total requests, response tokens, and number of requests
  - Ability to set limits for total requests and total token usage; agent will automatically stop when limits are reached
  - Configurable tool call timeout and max steps; agent will terminate if these thresholds are exceeded
- Developer Integration Enhancements:
  - Expanded documentation and examples for using MCPOmni Connect as a backend Python library
  - FastAPI example for building custom API servers with support for both ReAct Agent and Orchestrator Agent modes
  - Minimal code snippets for custom MCP client integration in Python projects
- FastAPI API Documentation:
  - Documented `/chat/agent_chat` endpoint with request/response examples
  - Added web client usage instructions for `examples/index.html`
- Environment Variables Reference:
  - Added table of supported environment variables and their descriptions in the README
- Typos and Documentation Improvements:
  - Fixed typos and improved clarity throughout the README
  - Clarified configuration options and usage instructions

### Changed
- Updated server configuration examples to clarify usage of `tool_call_timeout`, `max_steps`, `request_limit`, and `total_tokens_limit`
- Improved README structure with new "Examples", "Developer Integration", "Token & Usage Management", and "FastAPI API Endpoints" sections
- Enhanced error handling and documentation for agent termination on reaching usage limits

### Fixed
- Corrected typos in documentation and configuration comments
- Improved consistency in code examples and documentation formatting

## [0.1.14] - 2025-04-18

### Added
- DeepSeek model integration with full support for tool execution
- New Orchestrator Agent Mode:
  - Advanced planning for complex multi-step tasks
  - Strategic delegation across multiple MCP servers
  - Intelligent agent coordination and communication
  - Parallel task execution capabilities
  - Dynamic resource allocation
  - Sophisticated workflow management
  - Real-time progress monitoring
  - Adaptive task prioritization
- Client-Side Sampling Support:
  - Dynamic sampling configuration from client
  - Flexible LLM response generation
  - Customizable sampling parameters
  - Real-time sampling adjustments
- Chat History File Storage:
  - Save complete chat conversations to files
  - Load previous conversations from saved files
  - Continue conversations from where you left off
  - File-based backup and restoration
  - Persistent chat history across sessions

### Changed
- Enhanced Mode System with three distinct modes:
  - Chat Mode (Default)
  - Autonomous Mode
  - Orchestrator Mode
- Updated AI model integration documentation
- Improved chat history management system
- Enhanced server configuration options for new features

### Fixed
- Improved mode switching reliability
- Enhanced chat history persistence
- Optimized orchestrator mode performance

## [0.1.13] - 2025-04-14

### Added
- Gemini model integration with full support for tool execution
- Redis-powered memory persistence:
  - Conversation history tracking
  - State management across sessions
  - Configurable memory retention
  - Efficient data serialization and retrieval
  - Multi-server memory synchronization
- Agentic Mode capabilities:
  - Autonomous task execution without human intervention
  - Advanced reasoning and decision-making
  - Complex task decomposition and handling
  - Self-guided tool selection and execution
- Advanced prompt features:
  - Dynamic prompt discovery across servers
  - JSON and key-value format support
  - Nested argument structures
  - Automatic type conversion and validation
- Comprehensive troubleshooting guide with:
  - Common issues and solutions
  - Debug mode instructions
  - Support workflow
- Detailed architecture documentation with component breakdown
- Advanced server configuration examples for:
  - Multiple transport protocols
  - Various LLM providers
  - Docker integration

### Changed
- Enhanced installation process with UV package manager
- Improved development quick start guide
- Updated server configuration format to support multiple LLM providers
- Expanded model support documentation for all providers
- Enhanced security documentation with explicit user control details
- Restructured README with clearer sections and examples

### Fixed
- Standardized command formatting in documentation
- Improved code block consistency
- Enhanced example clarity and completeness

## [0.1.1] - 2025-03-27

### Added
- Comprehensive Security & Privacy section with detailed subsections:
  - Explicit User Control
  - Data Protection
  - Privacy-First Approach
  - Secure Communication
- Detailed Model Support section covering:
  - OpenAI Models
  - OpenRouter Models
  - Groq Models
  - Universal Model Support through ReAct Agent
- Structured Testing section with:
  - Multiple test running options
  - Test directory structure
  - Coverage reporting instructions
- Support for additional LLM providers:
  - OpenRouter integration
  - Groq integration
  - Universal model support through ReAct Agent

### Changed
- Improved AI-Powered Intelligence section:
  - Added support for multiple LLM providers (OpenAI, OpenRouter, Groq)
  - Added detailed ReAct Agent capabilities for models without function calling
  - Fixed typos in "seamless"
- Enhanced Server Configuration Examples:
  - Added support for multiple LLM providers
  - Updated model examples
  - Added comments for supported providers
- Updated Prerequisites:
  - Changed Python version requirement from 3.12+ to 3.10+
  - Updated API key requirements to support multiple providers
- Improved environment variable setup:
  - Changed from OPENAI_API_KEY to LLM_API_KEY for broader provider support
  - Added support for multiple API keys in .env file

### Fixed
- Typos in model integration descriptions
- Formatting issues in various sections
- Inconsistent capitalization in headers
- Fixed typo in "client" command (was "cient")
- Improved code block formatting and consistency

### Removed
- Redundant security information
- Simplified test section
- Removed specific OpenAI model references in favor of provider-agnostic examples
- Removed redundant prompt examples in favor of more structured documentation

## [0.1.0] - 2025-03-21
- Initial release
- Basic MCP server integration
- OpenAI model support
- Core CLI functionality
