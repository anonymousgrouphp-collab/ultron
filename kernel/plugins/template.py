"""kernel/plugins/template.py — Phase S3: plugin ecosystem templates.

Provides a template and utilities for creating MCP server plugins that
can be mounted via the kernel's MCP client.

Plugin structure:
    my-plugin/
    ├── __init__.py
    ├── server.py       # FastMCP server implementation
    ├── tools.py         # Tool definitions
    ├── manifest.json    # Plugin metadata
    └── README.md        # Documentation

Usage::

    template = PluginTemplate("my-plugin", description="Does cool things")
    template.add_tool("do_thing", "Does a thing", {"type": "object", "properties": {...}})
    template.generate(Path("plugins/my-plugin"))
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["PluginTemplate"]


@dataclass
class ToolSpec:
    """Specification for a plugin tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    risk: str = "read"  # read, write, execute, destructive


@dataclass
class PluginTemplate:
    """Template for creating MCP server plugins.

    Parameters
    ----------
    name:
        Plugin name (snake_case).
    description:
        Brief description of what the plugin does.
    version:
        Plugin version (semver).
    author:
        Plugin author name.
    """

    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    tools: list[ToolSpec] = field(default_factory=list)

    def add_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        risk: str = "read",
    ) -> None:
        """Add a tool to the plugin."""
        self.tools.append(ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            risk=risk,
        ))

    def generate(self, target_dir: Path) -> None:
        """Generate the plugin scaffold in the target directory."""
        target_dir.mkdir(parents=True, exist_ok=True)

        # manifest.json
        manifest = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "risk": t.risk,
                }
                for t in self.tools
            ],
        }
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )

        # server.py
        server_code = self._generate_server()
        (target_dir / "server.py").write_text(server_code)

        # tools.py
        tools_code = self._generate_tools()
        (target_dir / "tools.py").write_text(tools_code)

        # __init__.py
        (target_dir / "__init__.py").write_text(f'"""Plugin: {self.name}"""\n')

        # README.md
        readme = self._generate_readme()
        (target_dir / "README.md").write_text(readme)

    def _generate_server(self) -> str:
        """Generate the server.py template."""
        tool_defs = []
        for tool in self.tools:
            tool_defs.append(f'''
@server.tool()
def {tool.name}({self._params_to_args(tool.parameters)}) -> str:
    """{tool.description}"""
    # TODO: Implement this tool
    return f"{tool.name} executed successfully"
''')

        return f'''"""MCP server for plugin: {self.name}"""

from fastmcp import FastMCP

server = FastMCP("{self.name}")
{"".join(tool_defs)}

if __name__ == "__main__":
    server.run()
'''

    def _generate_tools(self) -> str:
        """Generate the tools.py template."""
        tool_defs = []
        for tool in self.tools:
            tool_defs.append(f'''
class {tool.name.title().replace("_", "")}:
    """{tool.description}"""

    name = "{tool.name}"
    description = "{tool.description}"
    parameters = {json.dumps(tool.parameters, indent=8)}
    risk = "{tool.risk}"

    @staticmethod
    def execute(**kwargs) -> str:
        """Execute this tool."""
        # TODO: Implement
        return f"{{tool.name}} executed"
''')

        return f'''"""Tool definitions for plugin: {self.name}"""
{"".join(tool_defs)}
'''

    def _generate_readme(self) -> str:
        """Generate the README.md template."""
        tools_section = "\n".join(
            f"- **{t.name}**: {t.description} (risk: {t.risk})"
            for t in self.tools
        )
        return f'''# {self.name}

{self.description}

## Tools

{tools_section}

## Installation

1. Copy this plugin to your ULTRON plugins directory
2. Install any dependencies: `pip install -r requirements.txt`
3. Mount via MCP: add to your config as an MCP server

## Development

```bash
# Run the server locally
python server.py

# Test with the ULTRON kernel
# Mount via: kernel/mcp_client.py mount_stdio
```
'''

    def _params_to_args(self, params: dict[str, Any]) -> str:
        """Convert JSON schema parameters to Python function arguments."""
        props = params.get("properties", {})
        args = []
        for name, prop in props.items():
            type_map = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
            py_type = type_map.get(prop.get("type", "string"), "Any")
            args.append(f"{name}: {py_type} = None")
        return ", ".join(args) if args else ""
