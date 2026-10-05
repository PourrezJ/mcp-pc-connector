"""Passerelle vers des serveurs MCP locaux (stdio) déclarés dans upstreams.json.

Format (même forme que Claude Desktop) :
{
  "blender": {"command": ".venv/Scripts/blender-mcp.exe", "args": [], "env": {}, "enabled": true}
}
Leurs outils sont exposés sous le nom  <serveur>__<outil>.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool

SEP = "__"
CONFIG = Path(__file__).with_name("upstreams.json")


class Upstreams:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="upstreams").start()
        self.sessions: dict[str, ClientSession] = {}
        self.stacks: dict[str, AsyncExitStack] = {}
        self.tools: dict[str, list[Tool]] = {}
        self.errors: dict[str, str] = {}
        self.lock = asyncio.Lock()

    # -- exécution dans la boucle dédiée (les sessions stdio y vivent en permanence)
    async def _run(self, coro, timeout=None):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)

    def _config(self) -> dict:
        if not CONFIG.exists():
            return {}
        return {k: v for k, v in json.loads(CONFIG.read_text(encoding="utf-8")).items()
                if not k.startswith("_") and v.get("enabled", True)}

    async def _connect(self, name: str, cfg: dict):
        cmd = cfg["command"]
        if not os.path.isabs(cmd) and ("/" in cmd or "\\" in cmd):
            cmd = str(CONFIG.parent / cmd)
        params = StdioServerParameters(command=cmd, args=cfg.get("args", []),
                                       env={**os.environ, **cfg.get("env", {})}, cwd=cfg.get("cwd"))
        stack = AsyncExitStack()
        try:
            r, w = await stack.enter_async_context(stdio_client(params))
            s = await stack.enter_async_context(ClientSession(r, w))
            await asyncio.wait_for(s.initialize(), cfg.get("connect_timeout", 60))
            self.tools[name] = (await s.list_tools()).tools
            self.sessions[name], self.stacks[name] = s, stack
            self.errors.pop(name, None)
        except BaseException as e:  # noqa: BLE001
            await stack.aclose()
            self.errors[name] = f"{type(e).__name__}: {e}"

    async def _close(self, name: str):
        self.sessions.pop(name, None); self.tools.pop(name, None)
        st = self.stacks.pop(name, None)
        if st:
            try:
                await st.aclose()
            except BaseException:  # noqa: BLE001
                pass

    async def _ensure(self):
        async with self.lock:
            cfg = self._config()
            for name in list(self.sessions):
                if name not in cfg:
                    await self._close(name)
            for name, c in cfg.items():
                if name not in self.sessions:
                    await self._connect(name, c)

    async def _reload(self):
        for name in list(self.sessions):
            await self._close(name)
        self.errors.clear()
        await self._ensure()

    async def _call(self, server: str, tool: str, args: dict):
        if server not in self.sessions:
            await self._ensure()
        s = self.sessions.get(server)
        if not s:
            raise RuntimeError(f"Serveur '{server}' indisponible : {self.errors.get(server, 'non configuré')}")
        try:
            return await s.call_tool(tool, args)
        except Exception:
            # processus mort ? une reconnexion puis un nouvel essai
            await self._close(server); await self._ensure()
            s = self.sessions.get(server)
            if not s:
                raise
            return await s.call_tool(tool, args)

    # -- API publique (appelée depuis la boucle uvicorn)
    async def list_tools(self) -> list[Tool]:
        await self._run(self._ensure(), 120)
        out = []
        for server, tools in self.tools.items():
            for t in tools:
                out.append(t.model_copy(update={"name": f"{server}{SEP}{t.name}", "outputSchema": None,
                                                "description": f"[{server}] {t.description or ''}"}))
        return out

    def owns(self, name: str) -> bool:
        return SEP in name

    async def call(self, name: str, args: dict, timeout: float = 600):
        server, tool = name.split(SEP, 1)
        return await self._run(self._call(server, tool, args), timeout)

    async def reload(self) -> dict:
        await self._run(self._reload(), 180)
        return self.status()

    def status(self) -> dict:
        return {"connectés": {k: [t.name for t in v] for k, v in self.tools.items()},
                "erreurs": dict(self.errors), "config": str(CONFIG)}
