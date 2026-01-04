#!/usr/bin/env python3
"""
AGENT #2: CONTENT MODERATION AGENT
Real-world production agent for monitoring and moderating user-generated content
"""

import os
import asyncio
import hashlib
from datetime import datetime
from omnicoreagent import (
    MemoryRouter,
    EventRouter,
    BackgroundAgentManager,
    ToolRegistry,
)

# Initialize tool registry
tool_registry = ToolRegistry()

# ============================================================================
# REAL-WORLD TOOLS FOR CONTENT MODERATION
# ============================================================================


@tool_registry.register_tool("scan_directory_for_new_content")
def scan_directory_for_new_content(directory: str) -> str:
    """Scan directory for new or modified content files."""
    import json

    try:
        if not os.path.exists(directory):
            return f"Directory not found: {directory}"

        # State file to track what we've seen
        state_file = os.path.expanduser("~/.omni_core_agent/moderation_state.json")
        os.makedirs(os.path.dirname(state_file), exist_ok=True)

        # Load previous state
        previous_state = {}
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                all_states = json.load(f)
                previous_state = all_states.get(directory, {})

        # Scan current files
        current_files = []
        new_files = []
        modified_files = []

        for root, dirs, files in os.walk(directory):
            for filename in files:
                # Only check text-based content files
                if filename.endswith(
                    (
                        ".txt",
                        ".md",
                        ".json",
                        ".csv",
                        ".log",
                        ".py",
                        ".js",
                        ".html",
                        ".xml",
                    )
                ):
                    filepath = os.path.join(root, filename)

                    try:
                        file_stat = os.stat(filepath)
                        file_hash = hashlib.md5(open(filepath, "rb").read()).hexdigest()

                        file_info = {
                            "path": filepath,
                            "size": file_stat.st_size,
                            "modified": file_stat.st_mtime,
                            "hash": file_hash,
                        }

                        current_files.append(file_info)

                        # Check if new or modified
                        if filepath not in previous_state:
                            new_files.append(file_info)
                        elif previous_state[filepath]["hash"] != file_hash:
                            modified_files.append(file_info)

                    except Exception:
                        continue

        # Save current state
        new_state = {
            f["path"]: {"hash": f["hash"], "size": f["size"]} for f in current_files
        }
        all_states = {}
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                all_states = json.load(f)
        all_states[directory] = new_state

        with open(state_file, "w") as f:
            json.dump(all_states, f, indent=2)

        # Build report
        if not new_files and not modified_files:
            return f"No new or modified content in {directory}"

        report = f"Content scan results for {directory}:\n\n"

        if new_files:
            report += f"NEW FILES ({len(new_files)}):\n"
            for f in new_files[:10]:
                report += f"  • {os.path.basename(f['path'])} ({f['size']} bytes)\n"
            if len(new_files) > 10:
                report += f"  ... and {len(new_files) - 10} more\n"
            report += "\n"

        if modified_files:
            report += f"MODIFIED FILES ({len(modified_files)}):\n"
            for f in modified_files[:10]:
                report += f"  • {os.path.basename(f['path'])} ({f['size']} bytes)\n"
            if len(modified_files) > 10:
                report += f"  ... and {len(modified_files) - 10} more\n"

        return report

    except Exception as e:
        return f"Error scanning directory: {str(e)}"


@tool_registry.register_tool("analyze_content_file")
def analyze_content_file(filepath: str) -> str:
    """Analyze a content file for policy violations."""

    try:
        if not os.path.exists(filepath):
            return f"File not found: {filepath}"

        # Read file content
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Basic content analysis
        word_count = len(content.split())
        char_count = len(content)
        line_count = len(content.split("\n"))

        # Check for policy violations
        violations = []
        flags = []

        # Spam indicators
        spam_keywords = [
            "buy now",
            "click here",
            "limited time",
            "act now",
            "free money",
            "make money fast",
            "guaranteed",
            "no risk",
            "order now",
        ]
        spam_count = sum(1 for keyword in spam_keywords if keyword in content.lower())
        if spam_count >= 3:
            flags.append(f"SPAM: Contains {spam_count} spam indicators")

        # Inappropriate language (basic check)
        profanity_list = ["damn", "hell", "crap", "shit", "fuck", "bitch", "ass"]
        profanity_count = sum(1 for word in profanity_list if word in content.lower())
        if profanity_count > 0:
            flags.append(
                f"LANGUAGE: Contains {profanity_count} potentially inappropriate words"
            )

        # Suspicious patterns
        if content.count("http://") + content.count("https://") > 10:
            flags.append("SUSPICIOUS: Excessive URLs detected")

        if content.count("@") > 20:
            flags.append("SUSPICIOUS: Excessive email addresses")

        # All caps check (shouting)
        caps_words = [
            word for word in content.split() if word.isupper() and len(word) > 3
        ]
        if len(caps_words) > word_count * 0.3:
            flags.append("STYLE: Excessive use of ALL CAPS")

        # Repetition check
        words = content.lower().split()
        if len(words) > 0:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.3:
                flags.append("QUALITY: High repetition detected")

        # Build report
        report = f"Content Analysis: {os.path.basename(filepath)}\n\n"
        report += "Statistics:\n"
        report += f"  Words: {word_count}\n"
        report += f"  Characters: {char_count}\n"
        report += f"  Lines: {line_count}\n\n"

        if flags:
            report += f"⚠️  FLAGS DETECTED ({len(flags)}):\n"
            for flag in flags:
                report += f"  • {flag}\n"
            report += "\n"
            report += "Recommendation: REVIEW REQUIRED\n"
        else:
            report += "✓ No policy violations detected\n"
            report += "Recommendation: APPROVED\n"

        return report

    except Exception as e:
        return f"Error analyzing file: {str(e)}"


@tool_registry.register_tool("flag_content")
def flag_content(filepath: str, reason: str, severity: str = "medium") -> str:
    """Flag content for human review."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create flagged_content table
        cursor.execute("""CREATE TABLE IF NOT EXISTS flagged_content
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          filepath TEXT NOT NULL,
                          reason TEXT NOT NULL,
                          severity TEXT NOT NULL,
                          status TEXT DEFAULT 'pending',
                          flagged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                          reviewed_at DATETIME,
                          reviewer_notes TEXT)""")

        # Check if already flagged
        cursor.execute(
            'SELECT id FROM flagged_content WHERE filepath = ? AND status = "pending"',
            (filepath,),
        )
        existing = cursor.fetchone()

        if existing:
            return f"Content already flagged: {filepath} (Flag ID: {existing[0]})"

        # Insert new flag
        cursor.execute(
            """INSERT INTO flagged_content (filepath, reason, severity)
                         VALUES (?, ?, ?)""",
            (filepath, reason, severity),
        )

        flag_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return f"Content flagged for review (Flag ID: {flag_id})\n  File: {filepath}\n  Reason: {reason}\n  Severity: {severity}"

    except Exception as e:
        return f"Error flagging content: {str(e)}"


@tool_registry.register_tool("approve_content")
def approve_content(filepath: str, notes: str = "") -> str:
    """Approve content as policy-compliant."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create approved_content table
        cursor.execute("""CREATE TABLE IF NOT EXISTS approved_content
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          filepath TEXT NOT NULL,
                          notes TEXT,
                          approved_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        cursor.execute(
            "INSERT INTO approved_content (filepath, notes) VALUES (?, ?)",
            (filepath, notes),
        )

        approval_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return f"Content approved (ID: {approval_id}): {os.path.basename(filepath)}"

    except Exception as e:
        return f"Error approving content: {str(e)}"


@tool_registry.register_tool("get_flagged_content")
def get_flagged_content(status: str = "pending", limit: int = 10) -> str:
    """Get flagged content items."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")

        if not os.path.exists(db_path):
            return "No moderation database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """SELECT id, filepath, reason, severity, flagged_at
                         FROM flagged_content
                         WHERE status = ?
                         ORDER BY 
                           CASE severity
                             WHEN 'critical' THEN 1
                             WHEN 'high' THEN 2
                             WHEN 'medium' THEN 3
                             WHEN 'low' THEN 4
                           END,
                           flagged_at DESC
                         LIMIT ?""",
            (status, limit),
        )

        items = cursor.fetchall()
        conn.close()

        if not items:
            return f"No {status} flagged content."

        report = f"Flagged Content ({status.upper()}) - {len(items)} items:\n\n"
        for item in items:
            report += f"Flag #{item[0]} [{item[3].upper()}]\n"
            report += f"  File: {os.path.basename(item[1])}\n"
            report += f"  Reason: {item[2]}\n"
            report += f"  Flagged: {item[4]}\n\n"

        return report

    except Exception as e:
        return f"Error retrieving flagged content: {str(e)}"


@tool_registry.register_tool("get_moderation_stats")
def get_moderation_stats() -> str:
    """Get content moderation statistics."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")

        if not os.path.exists(db_path):
            return "No moderation database found."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Total flagged
        cursor.execute("SELECT COUNT(*) FROM flagged_content")
        total_flagged = cursor.fetchone()[0]

        # Pending review
        cursor.execute('SELECT COUNT(*) FROM flagged_content WHERE status = "pending"')
        pending = cursor.fetchone()[0]

        # By severity
        cursor.execute(
            'SELECT severity, COUNT(*) FROM flagged_content WHERE status = "pending" GROUP BY severity'
        )
        severity_counts = cursor.fetchall()

        # Total approved
        cursor.execute("SELECT COUNT(*) FROM approved_content")
        total_approved = cursor.fetchone()[0]

        # Recent activity
        cursor.execute(
            'SELECT COUNT(*) FROM flagged_content WHERE date(flagged_at) = date("now")'
        )
        today_flags = cursor.fetchone()[0]

        conn.close()

        stats = f"""Content Moderation Statistics:
  
  Total Items Flagged: {total_flagged}
  Pending Review: {pending}
  Total Approved: {total_approved}
  Flagged Today: {today_flags}
  
  Pending by Severity:"""

        if severity_counts:
            for severity, count in severity_counts:
                stats += f"\n    {severity}: {count}"
        else:
            stats += "\n    None"

        return stats

    except Exception as e:
        return f"Error getting stats: {str(e)}"


@tool_registry.register_tool("remove_violating_content")
def remove_violating_content(filepath: str, reason: str) -> str:
    """Move violating content to quarantine directory."""
    import shutil

    try:
        if not os.path.exists(filepath):
            return f"File not found: {filepath}"

        # Create quarantine directory
        quarantine_dir = os.path.expanduser("~/.OmniCoreAgent/quarantine")
        os.makedirs(quarantine_dir, exist_ok=True)

        # Move file to quarantine
        filename = os.path.basename(filepath)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_path = os.path.join(quarantine_dir, f"{timestamp}_{filename}")

        shutil.move(filepath, quarantine_path)

        # Log the action
        import sqlite3

        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""CREATE TABLE IF NOT EXISTS removed_content
                         (id INTEGER PRIMARY KEY AUTOINCREMENT,
                          original_path TEXT NOT NULL,
                          quarantine_path TEXT NOT NULL,
                          reason TEXT NOT NULL,
                          removed_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        cursor.execute(
            """INSERT INTO removed_content (original_path, quarantine_path, reason)
                         VALUES (?, ?, ?)""",
            (filepath, quarantine_path, reason),
        )

        conn.commit()
        conn.close()

        return f"Content removed to quarantine:\n  From: {filepath}\n  To: {quarantine_path}\n  Reason: {reason}"

    except Exception as e:
        return f"Error removing content: {str(e)}"


@tool_registry.register_tool("create_moderation_report")
def create_moderation_report() -> str:
    """Create a comprehensive moderation report."""
    import sqlite3

    try:
        db_path = os.path.expanduser("~/.OmniCoreAgent/moderation.db")

        if not os.path.exists(db_path):
            return "No moderation data available."

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all stats
        cursor.execute("SELECT COUNT(*) FROM flagged_content")
        total_flagged = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM approved_content")
        total_approved = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM removed_content")
        total_removed = cursor.fetchone()[0]

        # Recent flags
        cursor.execute("""SELECT filepath, reason, severity, flagged_at
                         FROM flagged_content
                         ORDER BY flagged_at DESC
                         LIMIT 5""")
        recent_flags = cursor.fetchall()

        conn.close()

        report = f"""
╔════════════════════════════════════════════════════════════════╗
║           CONTENT MODERATION REPORT                            ║
║           Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}                      ║
╚════════════════════════════════════════════════════════════════╝

SUMMARY METRICS:
  Total Content Flagged:    {total_flagged}
  Total Content Approved:   {total_approved}
  Total Content Removed:    {total_removed}
  
  Current Status:
    Pending Review:         {total_flagged - total_removed}
    Action Rate:            {(total_removed / max(total_flagged, 1) * 100):.1f}%

RECENT FLAGGED CONTENT:"""

        if recent_flags:
            for flag in recent_flags:
                report += f"\n  • {os.path.basename(flag[0])} [{flag[2]}]"
                report += f"\n    Reason: {flag[1]}"
                report += f"\n    Time: {flag[3]}\n"
        else:
            report += "\n  No recent flags\n"

        report += "\n" + "=" * 64

        return report

    except Exception as e:
        return f"Error creating report: {str(e)}"


# ============================================================================
# CONTENT MODERATION AGENT CONFIGURATION
# ============================================================================

CONTENT_MODERATION_AGENT = {
    "agent_id": "content_moderation_agent",
    "system_instruction": """You are an AI Content Moderation Agent responsible for monitoring user-generated content.

Your responsibilities:
1. Scan monitored directories for new/modified content files
2. Analyze each file for policy violations
3. Make moderation decisions (approve, flag, or remove)
4. Maintain detailed logs of all actions
5. Generate regular moderation reports

Content Policy Guidelines:

SPAM DETECTION:
- Multiple promotional keywords (buy now, click here, etc.)
- Excessive URLs or email addresses
- Repetitive content

INAPPROPRIATE CONTENT:
- Profanity or offensive language
- Harassment or bullying indicators
- Misleading or false information markers

QUALITY STANDARDS:
- Excessive use of ALL CAPS
- Poor grammar/readability (when severe)
- Low-effort or duplicate content

SEVERITY LEVELS:
- CRITICAL: Immediate removal required (severe violations)
- HIGH: Flag for priority human review (significant issues)
- MEDIUM: Flag for standard review (moderate concerns)
- LOW: Note for monitoring (minor issues)

DECISION PROCESS:
1. For content with NO violations: Approve immediately
2. For content with MINOR issues (1-2 low severity flags): Approve with notes
3. For content with MODERATE issues (multiple flags or 1+ medium): Flag for human review
4. For content with SEVERE issues (critical violations): Flag as critical and recommend removal

Always document your reasoning. Be consistent but context-aware. When in doubt, flag for human review rather than auto-approving or removing.""",
    "model_config": {
        "provider": "openai",
        "model": "gpt-4.1",
        "temperature": 0.2,  # Low temperature for consistent policy application
        "max_context_length": 10000,
    },
    "mcp_tools": [
        {
            "name": "filesystem",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                os.path.expanduser("~/Desktop"),
                os.path.expanduser("~/Documents"),
            ],
        }
    ],
    "local_tools": tool_registry,
    "agent_config": {
        "max_steps": 20,  # May need to check many files
        "tool_call_timeout": 60,
        "request_limit": 0,
        "memory_config": {"mode": "sliding_window", "value": 100},
    },
    "interval": 300,  # Check every 5 minutes
    "max_retries": 3,
    "debug": True,
    "task_config": {
        "query": """Perform content moderation scan:

1. Scan monitored directories (Desktop and Documents) for new/modified content
2. For EACH new or modified file:
   a. Analyze the content thoroughly
   b. Apply policy guidelines to identify violations
   c. Make a moderation decision:
      - If clean: Approve with brief notes
      - If minor issues: Approve with notes about concerns
      - If moderate issues: Flag for human review with detailed reason
      - If severe issues: Flag as critical
   d. Log your decision and reasoning

3. After processing all files:
   a. Get current moderation statistics
   b. Review any pending flagged items
   c. Create a summary report

Be thorough but efficient. Document all decisions clearly.""",
        "description": "Automated content moderation and policy enforcement",
    },
}


# ============================================================================
# DEPLOYMENT AND TESTING
# ============================================================================


async def setup_and_run_agent():
    """Setup and run the content moderation agent."""

    print("=" * 80)
    print("CONTENT MODERATION AGENT - PRODUCTION DEPLOYMENT")
    print("=" * 80)
    print()

    # Step 1: Initialize agent components
    print("Step 1: Initializing agent components...")
    memory_router = MemoryRouter("in_memory")
    event_router = EventRouter("in_memory")
    background_manager = BackgroundAgentManager(
        memory_router=memory_router, event_router=event_router
    )
    print("✓ Components initialized")
    print()

    # Step 2: Create test content files
    print("Step 2: Creating test content for monitoring...")
    test_dir = os.path.expanduser("~/Desktop/test_content")
    os.makedirs(test_dir, exist_ok=True)

    # Create clean content
    with open(os.path.join(test_dir, "clean_article.txt"), "w") as f:
        f.write(
            "This is a well-written article about technology trends. "
            "It provides valuable insights into artificial intelligence and machine learning. "
            "The content is educational and professional."
        )

    # Create spam content
    with open(os.path.join(test_dir, "suspicious_post.txt"), "w") as f:
        f.write(
            "BUY NOW! LIMITED TIME OFFER! Click here to make money fast! "
            "Guaranteed results! Act now! Order today! No risk! Free money! "
            "Visit http://spam1.com http://spam2.com http://spam3.com"
        )

    # Create content with profanity
    with open(os.path.join(test_dir, "complaint.txt"), "w") as f:
        f.write(
            "This damn service is terrible. What the hell were they thinking? "
            "This is complete crap and shit. I'm so pissed off."
        )

    # Create all caps content
    with open(os.path.join(test_dir, "shouting.txt"), "w") as f:
        f.write(
            "THIS IS VERY IMPORTANT INFORMATION THAT EVERYONE NEEDS TO READ RIGHT NOW "
            "PLEASE SHARE THIS WITH EVERYONE YOU KNOW THIS IS URGENT"
        )

    print(f"✓ Created test content in {test_dir}")
    print()

    # Step 3: Create the agent
    print("Step 3: Creating Content Moderation Agent...")

    # Update config to use test directory
    test_config = CONTENT_MODERATION_AGENT.copy()
    test_config["mcp_tools"] = [
        {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", test_dir],
        }
    ]

    result = await background_manager.create_agent(test_config)
    print(f"✓ Agent created: {result['agent_id']}")
    print(f"  Session ID: {result['session_id']}")
    print()

    # Step 4: Start the background manager
    print("Step 4: Starting background agent manager...")
    background_manager.start()
    print("✓ Agent is now running automatically every 5 minutes")
    print()

    # Step 5: Show monitoring status
    print("Step 5: Monitoring configuration:")
    print(f"  Directory: {test_dir}")
    print("  Files to monitor: *.txt, *.md, *.json, *.py, *.js, *.html")
    print("  Database: ~/.OmniCoreAgent/moderation.db")
    print()

    print("=" * 80)
    print("AGENT IS NOW ACTIVE")
    print("=" * 80)
    print()
    print("The agent will:")
    print("  • Monitor directories for new/modified content every 5 minutes")
    print("  • Analyze all text-based files for policy violations")
    print("  • Automatically approve clean content")
    print("  • Flag suspicious content for human review")
    print("  • Track all moderation actions in database")
    print("  • Generate regular moderation reports")
    print()
    print("Monitored file types:")
    print("  .txt, .md, .json, .csv, .log, .py, .js, .html, .xml")
    print()
    print("To test the agent:")
    print(f"  • Add new files to {test_dir}")
    print("  • Modify existing files")
    print("  • Check moderation stats with get_moderation_stats()")
    print()
    print("Press Ctrl+C to stop the agent...")
    print()

    try:
        # Keep running and show status updates
        while True:
            await asyncio.sleep(60)

            # Show status every minute
            status = background_manager.get_agent_status("content_moderation_agent")
            if status:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Agent Status: "
                    f"Runs: {status['run_count']}, Errors: {status['error_count']}"
                )

                # Show moderation stats every 5 minutes
                if status["run_count"] % 5 == 0 and status["run_count"] > 0:
                    print("\n" + get_moderation_stats() + "\n")

    except KeyboardInterrupt:
        print("\n\nShutting down agent...")
        background_manager.shutdown()
        print("✓ Agent stopped successfully")
        print("\nFinal report:")
        print(create_moderation_report())


# ============================================================================
# MANUAL TESTING FUNCTIONS
# ============================================================================


def test_moderation_tools():
    """Test all content moderation tools manually."""

    print("Testing Content Moderation Tools")
    print("=" * 80)
    print()

    # Create test directory
    test_dir = os.path.expanduser("~/Desktop/moderation_test")
    os.makedirs(test_dir, exist_ok=True)

    # Test 1: Create test files
    print("Test 1: Creating test content files...")
    test_file = os.path.join(test_dir, "test_article.txt")
    with open(test_file, "w") as f:
        f.write("This is a test article with some content.")
    print(f"✓ Created: {test_file}")
    print()

    # Test 2: Scan directory
    print("Test 2: Scanning directory for new content...")
    print(scan_directory_for_new_content(test_dir))
    print()

    # Test 3: Analyze content
    print("Test 3: Analyzing content file...")
    print(analyze_content_file(test_file))
    print()

    # Test 4: Flag content
    print("Test 4: Flagging content...")
    print(flag_content(test_file, "Test flag for demonstration", "medium"))
    print()

    # Test 5: Approve content
    print("Test 5: Approving content...")
    print(approve_content(test_file, "Approved after review"))
    print()

    # Test 6: Get flagged items
    print("Test 6: Getting flagged content...")
    print(get_flagged_content())
    print()

    # Test 7: Get statistics
    print("Test 7: Getting moderation statistics...")
    print(get_moderation_stats())
    print()

    # Test 8: Create report
    print("Test 8: Creating moderation report...")
    print(create_moderation_report())
    print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Run manual tests
        test_moderation_tools()
    else:
        # Run the full agent
        asyncio.run(setup_and_run_agent())
