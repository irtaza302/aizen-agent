import os

from setuptools import setup


def parse_requirements(filename):
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name="aizen-ai-cli",
    version="2.3.0",
    description="Aizen AI Agent — A professional-grade AI coding assistant for your terminal.",
    packages=["aizen"],
    install_requires=parse_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "aizen=aizen.main:main",
        ],
    },
)
