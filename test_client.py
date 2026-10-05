"""Test de bout en bout : python test_client.py (serveur lancé, .env présent)."""
import asyncio, os, tempfile
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import server  # charge .env

URL = f"http://127.0.0.1:{server.PORT}/mcp/{server.TOKEN}"


async def main():
    d = server.ROOTS[0] / "mcp-pc-connector" if (server.ROOTS[0] / "mcp-pc-connector").exists() else server.ROOTS[0]
    d = Path(__file__).parent
    t = d / "_test.txt"
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("tools:", [x.name for x in (await s.list_tools()).tools])
            call = lambda n, **a: s.call_tool(n, a)
            res = await call("apply_patch", patch_text=f"*** Begin Patch\n*** Add File: {t}\n+hello\n+world\n*** End Patch")
            print("add:", res.content[0].text)
            res = await call("apply_patch", patch_text=f"*** Begin Patch\n*** Update File: {t}\n@@\n hello\n-world\n+MCP\n*** End Patch")
            print("update:", res.content[0].text)
            print("read:", (await call("read", path=str(t))).content[0].text)
            print("exec:", (await call("exec_command", command="echo salut && ver", cwd=str(d))).content[0].text)
            res = await call("exec_command", command="python -i -q -u", interactive=True, cwd=str(d))
            txt = res.content[0].text; print("interactive:", txt)
            sid = txt.split("[session ")[1].split()[0]
            print("stdin:", (await call("write_stdin", session_id=sid, data="print(6*7)\n", timeout_s=2)).content[0].text)
            print("kill:", (await call("write_stdin", session_id=sid, data="\x03", timeout_s=1)).content[0].text)
            png = next(Path(server.ROOTS[0]).rglob("*.png"), None)
            if png:
                res = await call("view_image", path=str(png))
                print("image:", png.name, res.content[0].type, res.content[0].mimeType)
            res = await call("read", path="C:/Windows/win.ini")
            print("hors racine refusé:", res.isError)
            t.unlink()

asyncio.run(main())
