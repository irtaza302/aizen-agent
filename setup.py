import os
from setuptools import setup

def parse_requirements(filename):
    filepath = os.path.join(os.path.dirname(__file__), filename)
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

setup(
    name="aether-cli",
    version="1.1.0",
    description="Aether AI Coding Agent",
    py_modules=["aether"],
    install_requires=parse_requirements("requirements.txt"),
    entry_points={
        "console_scripts": [
            "aether=aether:main",
        ],
    },
)
