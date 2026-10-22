# Contributing to Aegist MetaSearch Engine

Thanks for your interest in contributing to Aegist MetaSearch Engine. This project is a private, self-hosted metasearch engine built with Python, TypeScript, and LLM-assisted search workflows. We appreciate contributions that improve privacy, relevance, performance, and overall usability.

## Code of Conduct

We aim to maintain a respectful, inclusive, and collaborative environment. Please:
- Be kind and constructive in discussions
- Respect different experience levels
- Focus on the problem, not the person
- Keep feedback actionable and professional

If you experience harassment or abusive behavior, please report it to the maintainers.

## How to Contribute

There are many ways to contribute:
- Report bugs
- Suggest features or enhancements
- Improve documentation
- Fix issues and add tests
- Improve search ranking, privacy, security, or performance
- Help with frontend, backend, crawler, indexer, or deployment work

We welcome both small and large contributions.

## Getting Started

### Prerequisites
Before contributing, make sure you have:
- Python 3.10+
- Node.js 18+
- npm or pnpm
- Git
- A working local environment for running the app

### Fork and Clone
1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/Aegist-MetaSearch-Engine.git
   cd Aegist-MetaSearch-Engine
   ```
3. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

### Install Dependencies
Backend:
```bash
pip install -r requirements.txt
```

Frontend:
```bash
npm install
```

If the project uses a virtual environment or custom setup, follow the project’s README exactly.

## Project Structure

This repository includes both backend and frontend components. In general:
- `backend/` or `server/`: API, crawlers, indexing logic, ranking, and retrieval systems
- `frontend/` or `web/`: UI and dashboard
- `core/` or `engine/`: domain logic, search models, query processing
- `scripts/`: setup, automation, and operational utilities
- `tests/`: automated tests

If your area is not obvious, check the README or ask in an issue before making major changes.

## Contribution Guidelines

### 1. Start with an Issue
For larger changes, please open an issue first to discuss:
- the proposed feature
- expected behavior
- design considerations
- compatibility concerns

This helps avoid duplicate work and keeps the project aligned.

### 2. Keep Changes Focused
Open pull requests for one concern at a time:
- bug fix
- feature
- refactor
- documentation update

Avoid combining unrelated changes into a single PR.

### 3. Follow the Existing Style
- Match the existing code style
- Prefer clear, readable code over clever solutions
- Preserve naming consistency
- Add comments only where they add value

### 4. Write Tests
Please add or update tests for behavior changes. We prefer:
- unit tests for logic-heavy components
- integration tests for API or indexing flows
- frontend tests for user-facing UI behavior where appropriate

If a bug is fixed, include a test that covers it.

### 5. Keep Performance in Mind
This project deals with search, indexing, retrieval, and potentially LLM-based enhancement. Please be mindful of:
- latency
- memory usage
- database/query efficiency
- API response times
- crawler behavior
- cost of LLM calls

### 6. Respect Privacy and Security
Because this project is positioned as a private search engine, security and privacy are critical. Please avoid introducing:
- insecure defaults
- log leakage of user queries or sensitive data
- unsafe handling of external URLs or crawled content
- unreviewed prompt injection or abuse risks
- insecure authentication or configuration patterns

If you are changing behavior related to privacy, crawling, user data, or external integrations, document the impact in your PR.

## Pull Request Process

1. Ensure your branch is up to date with the main branch.
2. Run the relevant tests locally.
3. Make sure your code passes linting and formatting checks.
4. Open a pull request with a clear title and description.
5. Include:
   - summary of the change
   - why the change is needed
   - testing performed
   - any limitations or follow-up items

### PR Template
Use a clear PR description like:

```md
## Summary
Briefly explain what this change does.

## Why
Why is this change needed? What problem does it solve?

## Changes
- item 1
- item 2
- item 3

## Testing
- command 1
- command 2

## Notes
Anything else maintainers should know.
```

## Issue Reporting

When reporting a bug, please include:
- a clear title
- steps to reproduce
- expected behavior
- actual behavior
- screenshots or logs if relevant
- environment details (OS, Python version, Node version, browser, etc.)

When suggesting a feature, include:
- motivation
- use case
- rough solution or idea
- any concerns about privacy, scale, or performance

## Coding Standards

### Python
- Use type hints where practical
- Prefer clear function names and small, focused functions
- Follow PEP 8
- Avoid unnecessary dependencies
- Keep async or concurrency patterns intentional and safe

### TypeScript
- Keep files readable and modular
- Prefer typed interfaces over `any`
- Follow the repo’s existing linting / formatting rules
- Avoid large unstructured components

### Search & AI Behavior
If your change affects ranking, retrieval, or LLM usage:
- document the intended behavior
- explain trade-offs
- include testing or validation where possible
- avoid silent changes to privacy-sensitive logic

## Documentation

If you add or change behavior, update the docs where needed:
- README
- setup instructions
- environment variables
- architecture notes
- API documentation or examples

Good documentation helps maintainers and users understand the project better.

## Questions?

If you are unsure whether your idea fits, feel free to open an issue and ask. We’d rather help early than reject a promising contribution late.

Thank you for helping improve Aegist MetaSearch Engine.
