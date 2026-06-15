"""
Smart project context detection for Aizen.

Auto-detects project type, language, framework, git state, and directory
structure to inject into the system prompt — so Aizen "just knows" your project.
"""

import os
import subprocess

from .logging_config import logger

# Config file → (language, framework) mapping
PROJECT_SIGNATURES: dict[str, tuple[str, str | None]] = {
    "pyproject.toml": ("Python", None),
    "setup.py": ("Python", None),
    "setup.cfg": ("Python", None),
    "requirements.txt": ("Python", None),
    "Pipfile": ("Python", None),
    "package.json": ("JavaScript/TypeScript", None),
    "tsconfig.json": ("TypeScript", None),
    "Cargo.toml": ("Rust", None),
    "go.mod": ("Go", None),
    "Gemfile": ("Ruby", None),
    "build.gradle": ("Java/Kotlin", "Gradle"),
    "build.gradle.kts": ("Kotlin", "Gradle"),
    "pom.xml": ("Java", "Maven"),
    "composer.json": ("PHP", None),
    "mix.exs": ("Elixir", None),
    "pubspec.yaml": ("Dart", "Flutter"),
    "CMakeLists.txt": ("C/C++", "CMake"),
    "Makefile": (None, "Make"),
    "Dockerfile": (None, "Docker"),
    "docker-compose.yml": (None, "Docker Compose"),
    "docker-compose.yaml": (None, "Docker Compose"),
    ".terraform": (None, "Terraform"),
    "serverless.yml": (None, "Serverless"),
}

# package.json dependency → framework detection
JS_FRAMEWORK_SIGNATURES: dict[str, str] = {
    "next": "Next.js",
    "react": "React",
    "vue": "Vue.js",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "angular": "Angular",
    "@angular/core": "Angular",
    "express": "Express.js",
    "fastify": "Fastify",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "vite": "Vite",
    "gatsby": "Gatsby",
    "remix": "Remix",
    "astro": "Astro",
    "electron": "Electron",
    "tailwindcss": "Tailwind CSS",
}

# pyproject.toml / requirements.txt → framework detection
PY_FRAMEWORK_SIGNATURES: dict[str, str] = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "streamlit": "Streamlit",
    "pytest": "pytest",
    "celery": "Celery",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "typer": "Typer",
    "click": "Click",
    "rich": "Rich",
}


class ProjectDetector:
    """Auto-detects project metadata from the current working directory."""

    def __init__(self, root: str | None = None):
        self.root = root or os.getcwd()
        self._cache: dict | None = None

    def detect(self) -> dict:
        """Detect project metadata. Returns a dict with detected info."""
        if self._cache is not None:
            return self._cache

        result = {
            "languages": set(),
            "frameworks": set(),
            "config_files": [],
            "git_branch": None,
            "git_recent_commits": [],
            "directory_structure": "",
            "project_name": os.path.basename(self.root),
        }

        self._detect_from_config_files(result)
        self._detect_js_frameworks(result)
        self._detect_py_frameworks(result)
        self._detect_git_state(result)
        self._generate_structure(result)

        # Convert sets to sorted lists for serialization
        result["languages"] = sorted(result["languages"])
        result["frameworks"] = sorted(result["frameworks"])

        self._cache = result
        return result

    def _detect_from_config_files(self, result: dict) -> None:
        """Detect language/framework from project config files."""
        for filename, (lang, framework) in PROJECT_SIGNATURES.items():
            path = os.path.join(self.root, filename)
            if os.path.exists(path):
                result["config_files"].append(filename)
                if lang:
                    result["languages"].add(lang)
                if framework:
                    result["frameworks"].add(framework)

    def _detect_js_frameworks(self, result: dict) -> None:
        """Detect JS/TS frameworks from package.json dependencies."""
        pkg_path = os.path.join(self.root, "package.json")
        if not os.path.exists(pkg_path):
            return

        try:
            import json

            with open(pkg_path, encoding="utf-8") as f:
                pkg = json.load(f)

            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))

            for dep_name, framework_name in JS_FRAMEWORK_SIGNATURES.items():
                if dep_name in all_deps:
                    result["frameworks"].add(framework_name)

        except Exception as e:
            logger.debug("Failed to parse package.json: %s", e)

    def _detect_py_frameworks(self, result: dict) -> None:
        """Detect Python frameworks from pyproject.toml or requirements.txt."""
        # Try pyproject.toml first
        pyproject_path = os.path.join(self.root, "pyproject.toml")
        if os.path.exists(pyproject_path):
            try:
                with open(pyproject_path, encoding="utf-8") as f:
                    content = f.read().lower()
                for dep_name, framework_name in PY_FRAMEWORK_SIGNATURES.items():
                    if dep_name in content:
                        result["frameworks"].add(framework_name)
            except Exception as e:
                logger.debug("Failed to parse pyproject.toml: %s", e)

        # Also check requirements.txt
        req_path = os.path.join(self.root, "requirements.txt")
        if os.path.exists(req_path):
            try:
                with open(req_path, encoding="utf-8") as f:
                    content = f.read().lower()
                for dep_name, framework_name in PY_FRAMEWORK_SIGNATURES.items():
                    if dep_name in content:
                        result["frameworks"].add(framework_name)
            except Exception as e:
                logger.debug("Failed to parse requirements.txt: %s", e)

    def _detect_git_state(self, result: dict) -> None:
        """Detect git branch and recent commits."""
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if branch.returncode == 0:
                result["git_branch"] = branch.stdout.strip()

            log = subprocess.run(
                ["git", "log", "--oneline", "-5", "--no-decorate"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if log.returncode == 0 and log.stdout.strip():
                result["git_recent_commits"] = log.stdout.strip().splitlines()

        except Exception as e:
            logger.debug("Git detection failed: %s", e)

    def _generate_structure(self, result: dict, max_depth: int = 2) -> None:
        """Generate a compact directory tree (top N levels)."""
        lines = []
        ignore_dirs = {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            ".tox",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            "dist",
            "build",
            ".next",
            ".nuxt",
            "target",
            ".idea",
            ".vscode",
            ".eggs",
            "*.egg-info",
        }

        def _walk(path: str, prefix: str, depth: int):
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(path))
            except PermissionError:
                return

            dirs = [
                e
                for e in entries
                if os.path.isdir(os.path.join(path, e))
                and e not in ignore_dirs
                and not e.startswith(".")
            ]
            files = [
                e
                for e in entries
                if os.path.isfile(os.path.join(path, e)) and not e.startswith(".")
            ]

            # Show dirs first, then top-level files
            for d in dirs:
                lines.append(f"{prefix}📁 {d}/")
                _walk(os.path.join(path, d), prefix + "  ", depth + 1)

            # Only show files at first level to keep it compact
            if depth <= 1:
                for f_name in files[:15]:
                    lines.append(f"{prefix}📄 {f_name}")
                if len(files) > 15:
                    lines.append(f"{prefix}... and {len(files) - 15} more files")

        _walk(self.root, "", 0)
        result["directory_structure"] = "\n".join(lines[:50])  # Cap at 50 lines

    def to_system_context(self) -> str:
        """Format detection results as a string for system prompt injection."""
        info = self.detect()

        parts = []
        parts.append(f"Project: {info['project_name']}")

        if info["languages"]:
            parts.append(f"Languages: {', '.join(info['languages'])}")

        if info["frameworks"]:
            parts.append(f"Frameworks: {', '.join(info['frameworks'])}")

        if info["git_branch"]:
            parts.append(f"Git branch: {info['git_branch']}")

        if info["git_recent_commits"]:
            parts.append("Recent commits:")
            for commit in info["git_recent_commits"][:3]:
                parts.append(f"  - {commit}")

        if info["directory_structure"]:
            parts.append(f"\nProject structure:\n{info['directory_structure']}")

        if not parts:
            return ""

        return "\n<project_context>\n" + "\n".join(parts) + "\n</project_context>"
