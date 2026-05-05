# Contributing to OmmiCoreAgent

First off, thank you for considering contributing to OmniCoreAgent! It's people like you that make OmniCoreAgent such a great tool. 👏

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Submitting Changes](#submitting-changes)
- [Style Guidelines](#style-guidelines)
- [Adding New Features](#adding-new-features)
- [Bug Reports](#bug-reports)
- [Project Links](#project-links)

## 📜 Code of Conduct

This project and everyone participating in it is governed by our Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to [abioladedayo1993@gmail.com].

## 🚀 Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git https://github.com/omnirexflora-labs/omnicoreagent.git
   cd ommicoreagent
   ```
3. Create a virtual environment:
   ```bash
   uv venv
   source .venv/bin/activate
   ```
4. Install dependencies:
   ```bash
   uv sync
   ```

## 💻 Development Setup

1. **Environment Configuration**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

2. **Install Development Dependencies**
   ```bash
   pip install -e ".[dev]"
   ```

3. **Pre-commit Hooks**
   ```bash
   pre-commit install
   ```

## 🔄 Making Changes

1. Create a new branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes:
   - Write meaningful commit messages
   - Keep commits atomic and focused
   - Add tests for new functionality
   - Update documentation as needed

3. Run tests:
   ```bash
   pytest
   ```

## 📝 Style Guidelines

### Python Code Style
- Follow PEP 8 guidelines
- Use type hints
- Maximum line length: 88 characters (Black formatter)
- Use docstrings for functions and classes

### Commit Messages
```
type(scope): Brief description

Detailed description of what changed and why.
```

Types:
- feat: New feature
- fix: Bug fix
- docs: Documentation changes
- style: Code style changes
- refactor: Code refactoring
- test: Adding/modifying tests
- chore: Maintenance tasks

## 🌟 Adding New Features

1. **Transport Layer**
   - Follow the existing transport interface
   - Add appropriate tests
   - Document new transport methods

2. **Server Integration**
   - Implement server configuration validation
   - Add error handling
   - Document server requirements

3. **Prompt Management**
   - Follow the prompt interface
   - Add validation for new prompt types
   - Update prompt documentation

## 🐛 Bug Reports

When filing an issue, please include:

1. **Description**
   - Clear and descriptive title
   - Detailed description of the issue

2. **Environment**
   - Python version
   - Operating system
   - Package versions

3. **Steps to Reproduce**
   - Detailed step-by-step guide
   - Example code if applicable
   - Expected vs actual behavior

4. **Additional Context**
   - Log files
   - Screenshots
   - Related issues

## 🔍 Pull Request Process

1. **Before Submitting**
   - Update documentation
   - Add/update tests
   - Run the test suite
   - Update CHANGELOG.md

2. **PR Template**
   ```markdown
   ## Description
   Brief description of changes

   ## Type of Change
   - [ ] Bug fix
   - [ ] New feature
   - [ ] Breaking change
   - [ ] Documentation update

   ## Testing
   Describe testing done

   ## Checklist
   - [ ] Tests added/updated
   - [ ] Documentation updated
   - [ ] CHANGELOG.md updated
   ```

## 👥 Project Links

- Follow us on [Twitter](https://x.com/omnirexflolabs)


## ❓ Questions?

Feel free to reach out:
- Open an issue
- Email: contact@omnirexfloralabs.com

---

Thank you for contributing to OmniCoreAgent! 🎉
