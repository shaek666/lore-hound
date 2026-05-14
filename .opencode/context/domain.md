# Domain: Codebase Research Agent (lore-hound)

## Project Purpose
An AI agent that answers technical questions about GitHub repositories by exploring the code itself. Built as a Senior Backend Developer take-home task for CodeFusion AI.

## Key Problem
Given a GitHub repo URL + natural language question, the agent must:
1. Clone the repo locally (shallow clone, --depth 1)
2. Explore the codebase using tool-calling (list_files, read_file, search_code, get_file_summary)
3. Log findings to a PostgreSQL database mid-research
4. Produce a cited answer with specific file paths, function names, and line numbers
5. Persist everything so sessions can be retrieved and built upon

## Target Users
- CodeFusion AI hiring team evaluating the submission
- The agent is REST API-only (no frontend, no CLI)

## Success Criteria
- Agent completes research sessions with substantive, cited answers
- Database shows the full reasoning chain (tool calls in order)
- API returns session data correctly
- Clean repo with README, DECISIONS.md, .env.example
