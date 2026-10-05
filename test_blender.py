"""Test passerelle Blender : python test_blender.py (serveur + Blender ouverts)."""
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import server

URL = f"http://127.0.0.1:{server.PORT}/mcp/{server.TOKEN}"


async def main():
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("nb outils:", len(names))
            print("blender:", [n for n in names if n.startswith("blender__")][:40])
            st = await s.call_tool("upstreams", {"action": "status"})
            print("status:", st.content[0].text[:600])
            for n in ("blender__get_scene_info",):
                if n in names:
                    res = await s.call_tool(n, {})
                    print(n, "isError=", res.isError, (getattr(res.content[0], "text", "") or "")[:600])
            code = "import bpy\nbpy.ops.mesh.primitive_monkey_add(location=(0,0,2))\nprint([o.name for o in bpy.data.objects])"
            if "blender__execute_blender_code" in names:
                res = await s.call_tool("blender__execute_blender_code", {"code": code})
                print("exec isError=", res.isError, (getattr(res.content[0], "text", "") or "")[:400])

asyncio.run(main())
