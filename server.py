"""MCP distant pour coder sur le PC : read, view_image, apply_patch, exec_command, write_stdin."""
from __future__ import annotations

import hmac
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP, Image

from upstreams import Upstreams


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env(Path(__file__).with_name(".env"))

TOKEN = os.environ.get("MCP_TOKEN", "")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9931"))
ROOTS = [Path(r).resolve() for r in re.split(r"[;,]", os.environ.get("ALLOWED_ROOTS", "D:/Repos")) if r.strip()]
MAX_READ = 1_000_000
MAX_IMAGE = 8_000_000
MAX_OUTPUT = 200_000

UP = Upstreams()


class GatewayMCP(FastMCP):
    """FastMCP + outils des serveurs MCP locaux (upstreams.json) préfixés <serveur>__."""

    async def list_tools(self):
        own = await super().list_tools()
        try:
            return own + await UP.list_tools()
        except Exception as e:  # noqa: BLE001
            print(f"[upstreams] list_tools: {e}")
            return own

    async def call_tool(self, name, arguments):
        if not UP.owns(name):
            return await super().call_tool(name, arguments)
        res = await UP.call(name, arguments or {})
        if res.isError:
            raise ValueError("\n".join(getattr(c, "text", str(c)) for c in res.content))
        return res.content


mcp = GatewayMCP("pc-coder", host=HOST, port=PORT, streamable_http_path="/mcp")


@mcp.tool()
async def upstreams(action: str = "status") -> dict:
    """Serveurs MCP locaux branchés (upstreams.json). action='status' ou 'reload' (relit le fichier et relance)."""
    return await UP.reload() if action == "reload" else UP.status()


def _safe(path: str, cwd: str | None = None) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd or ROOTS[0]) / p
    p = p.resolve()
    if not any(p == r or r in p.parents for r in ROOTS):
        raise ValueError(f"Chemin hors des racines autorisées {[str(r) for r in ROOTS]}: {p}")
    return p


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…[tronqué, {len(s)} caractères]"


# ---------------------------------------------------------------- read
@mcp.tool()
def read(path: str, offset: int = 1, limit: int = 2000) -> str:
    """Lit un fichier texte avec numéros de ligne (offset 1-indexé) ou liste un dossier."""
    p = _safe(path)
    if p.is_dir():
        return "\n".join(sorted((c.name + ("/" if c.is_dir() else "")) for c in p.iterdir()))
    if p.stat().st_size > MAX_READ * 20:
        raise ValueError("Fichier trop gros")
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        raise ValueError("Fichier binaire (utilisez view_image pour les images)")
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(offset, 1) - 1
    chunk = lines[start:start + limit]
    out = "\n".join(f"{i}|{l}" for i, l in enumerate(chunk, start + 1))
    if len(out) > MAX_READ:
        out = out[:MAX_READ] + "\n…[tronqué]"
    if start + limit < len(lines):
        out += f"\n…[{len(lines)} lignes au total, suite avec offset={start + limit + 1}]"
    return out


# ---------------------------------------------------------------- view_image
@mcp.tool()
def view_image(path: str) -> Image:
    """Renvoie une image locale (png, jpg, gif, webp) pour que le modèle la voie."""
    p = _safe(path)
    fmt = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}.get(p.suffix.lower())
    if not fmt:
        raise ValueError("Format non supporté")
    if p.stat().st_size > MAX_IMAGE:
        raise ValueError("Image trop grosse (>8 Mo)")
    return Image(data=p.read_bytes(), format=fmt)


# ---------------------------------------------------------------- apply_patch
def _apply_codex_patch(text: str, base: Path) -> list[str]:
    """Format Codex : *** Begin Patch / *** Add|Update|Delete File / @@ / +-␠ lignes."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("Le patch doit commencer par '*** Begin Patch'")
    done, i = [], 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "*** End Patch":
            break
        if line.startswith("*** Add File: "):
            p = _safe(line[14:].strip(), str(base)); i += 1
            body = []
            while i < len(lines) and not lines[i].startswith("***"):
                body.append(lines[i][1:] if lines[i].startswith("+") else lines[i]); i += 1
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(body) + "\n", encoding="utf-8")
            done.append(f"A {p}")
        elif line.startswith("*** Delete File: "):
            p = _safe(line[17:].strip(), str(base)); p.unlink(); done.append(f"D {p}"); i += 1
        elif line.startswith("*** Update File: "):
            p = _safe(line[17:].strip(), str(base)); i += 1
            move_to = None
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                move_to = _safe(lines[i][13:].strip(), str(base)); i += 1
            raw = p.read_text(encoding="utf-8")
            crlf = "\r\n" in raw
            src = raw.replace("\r\n", "\n").split("\n")
            pos = 0
            while i < len(lines) and not lines[i].startswith("*** "):
                if lines[i].startswith("@@"):
                    i += 1; continue
                old, new = [], []
                while i < len(lines) and not lines[i].startswith(("@@", "*** ")):
                    l = lines[i]; tag, body = (l[:1], l[1:]) if l else (" ", "")
                    if tag in (" ", "-"): old.append(body)
                    if tag in (" ", "+"): new.append(body)
                    i += 1
                idx = _find(src, old, pos)
                if idx < 0:
                    raise ValueError(f"Contexte introuvable dans {p}:\n" + "\n".join(old))
                src[idx:idx + len(old)] = new
                pos = idx + len(new)
            out = "\n".join(src)
            target = move_to or p
            target.write_text(out.replace("\n", "\r\n") if crlf else out, encoding="utf-8", newline="")
            if move_to:
                p.unlink()
            done.append(f"M {target}")
        else:
            i += 1
    return done


def _find(src: list[str], old: list[str], start: int) -> int:
    if not old:
        return start
    for norm in (lambda s: s, lambda s: s.rstrip(), lambda s: s.strip()):
        o = [norm(x) for x in old]
        for k in list(range(start, len(src) - len(old) + 1)) + list(range(0, start)):
            if [norm(x) for x in src[k:k + len(old)]] == o:
                return k
    return -1


@mcp.tool()
def apply_patch(patch_text: str, cwd: str | None = None, strip: int = 1) -> str:
    """Applique un patch. Format Codex ('*** Begin Patch' … '*** End Patch') ou diff unifié (git diff)."""
    base = _safe(cwd) if cwd else ROOTS[0]
    if patch_text.lstrip().startswith("*** Begin Patch"):
        return "\n".join(_apply_codex_patch(patch_text.strip(), base)) or "Rien à faire"
    for f in re.findall(r"^\+\+\+ (?:b/)?(\S+)", patch_text, re.M):
        if f != "/dev/null":
            _safe(f, str(base))
    r = subprocess.run(["git", "apply", f"-p{strip}", "--recount", "--whitespace=nowarn", "-"],
                       input=patch_text, text=True, capture_output=True, cwd=base)
    if r.returncode == 0:
        return "OK (git apply)\n" + r.stdout
    r2 = subprocess.run(["patch", f"-p{strip}", "--forward", "--batch"], input=patch_text,
                        text=True, capture_output=True, cwd=base)
    if r2.returncode == 0:
        return "OK (patch)\n" + r2.stdout
    raise ValueError(f"Échec\ngit apply: {r.stderr}\npatch: {r2.stdout}{r2.stderr}")


# ---------------------------------------------------------------- exec_command / write_stdin
class Session:
    def __init__(self, command: str, cwd: Path):
        self.proc = subprocess.Popen(command, shell=True, cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        self.buf = bytearray()
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1)
            if not chunk:
                break
            with self.lock:
                self.buf += chunk

    def drain(self, wait: float) -> str:
        deadline = time.time() + wait
        last = -1
        while time.time() < deadline:
            with self.lock:
                n = len(self.buf)
            if self.proc.poll() is not None and n == last:
                break
            last = n
            time.sleep(0.1)
        with self.lock:
            data, self.buf = bytes(self.buf), bytearray()
        return data.decode("utf-8", errors="replace")


SESSIONS: dict[str, Session] = {}


def _report(sid: str, s: Session, out: str) -> str:
    code = s.proc.poll()
    status = f"[session {sid} en cours]" if code is None else f"[session {sid} terminée, code {code}]"
    if code is not None:
        SESSIONS.pop(sid, None)
    return _clip(out) + "\n" + status


@mcp.tool()
def exec_command(command: str, cwd: str | None = None, timeout_s: float = 30,
                 session_id: str | None = None, interactive: bool = False) -> str:
    """Exécute une commande shell (cmd.exe). interactive=True garde le processus vivant et renvoie
    un session_id utilisable avec write_stdin. session_id existant = relit la sortie de cette session."""
    if session_id:
        s = SESSIONS.get(session_id)
        if not s:
            raise ValueError("Session inconnue")
        return _report(session_id, s, s.drain(timeout_s))
    wd = _safe(cwd) if cwd else ROOTS[0]
    if interactive:
        sid = uuid.uuid4().hex[:8]
        s = SESSIONS[sid] = Session(command, wd)
        return _report(sid, s, s.drain(min(timeout_s, 10)))
    try:
        r = subprocess.run(command, shell=True, cwd=wd, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        return _clip((e.stdout or b"").decode("utf-8", "replace")) + f"\n[timeout {timeout_s}s]"
    out = (r.stdout + (b"\n[stderr]\n" + r.stderr if r.stderr else b"")).decode("utf-8", "replace")
    return _clip(out) + f"\n[code {r.returncode}]"


@mcp.tool()
def write_stdin(session_id: str, data: str, timeout_s: float = 5.0) -> str:
    """Écrit dans le stdin d'une session interactive (ajoutez '\\n' pour valider) et renvoie la nouvelle sortie.
    data vide = simple lecture. data='\\u0003' tue la session."""
    s = SESSIONS.get(session_id)
    if not s:
        raise ValueError("Session inconnue")
    if data == "\x03":
        s.proc.kill()
    elif data:
        s.proc.stdin.write(data.encode("utf-8"))
        s.proc.stdin.flush()
    return _report(session_id, s, s.drain(timeout_s))


# ---------------------------------------------------------------- auth + run
class PathTokenAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope.get("path") == "/health":
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"ok"})
                return
            path = scope.get("path", "")
            # Token dans le chemin (reverse proxy nginx) : /mcp/<token> -> /mcp
            if hmac.compare_digest(path.rstrip("/"), f"/mcp/{TOKEN}"):
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
                await self.app(scope, receive, send)
                return
            if path.startswith("/.well-known/"):
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            # Pas de Bearer : seul /mcp/<token> est accepté, tout le reste = 404
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    if len(TOKEN) < 24:
        sys.exit("MCP_TOKEN manquant ou trop court (voir .env.example)")
    print(f"MCP pc-coder sur http://{HOST}:{PORT}/mcp  racines={[str(r) for r in ROOTS]}")
    uvicorn.run(PathTokenAuth(mcp.streamable_http_app()), host=HOST, port=PORT, log_level="info")
