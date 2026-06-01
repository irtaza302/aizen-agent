import os
from setuptools import setup

def parse_requirements(filename):
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name="aether-ai-cli",
    version="2.0.1",
    description="Aether AI Agent — A professional-grade AI coding assistant for your terminal.",
    py_modules=["aether"],
    install_requires=parse_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "aether=aether:main",
        ],
    },
)
