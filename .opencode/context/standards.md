# Coding Standards: lore-hound

## Django Conventions
- Use function-based views with @api_view decorator (not ViewSets)
- Use ModelSerializer for model serialization, Serializer for input validation
- Services go in research/services/ package (not in models.py or views.py)
- Management commands in research/management/commands/
- All models registered in admin.py

## Python Style
- Type hints on all function signatures
- Docstrings on all public functions and classes
- No print() in production code (use import logging)
- No commented-out code
- No bare except: clauses
- Use pathlib for filesystem paths, not os.path
- Use f-strings, not .format() or %

## Error Handling
- Tool errors return descriptive error messages to the LLM (let it recover)
- LLM API errors: retry 2x with exponential backoff, then fail the session
- Path traversal: reject paths with .. segments
- Binary files: detect via first 512 bytes, return [Binary file: ...]

## Git Conventions
- Atomic commits per task
- No API keys committed (use .env.example + .gitignore)
- No large files in repo (Postgres data in docker volume, repos in data/)

## AI Tool Usage
- This project was built with AI coding tools. DECISIONS.md must honestly document:
  - Which tools were used
  - What was AI-generated vs. hand-written vs. AI-assisted then edited
  - Where AI helped most and where it failed
  - How AI output was reviewed and verified
