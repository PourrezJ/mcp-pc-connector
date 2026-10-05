# mcp-pc-connector

Serveur MCP (Streamable HTTP) pour coder sur ce PC à distance.

Outils : `read`, `view_image`, `apply_patch` (format Codex `*** Begin Patch` ou diff unifié), `exec_command` (one-shot ou `interactive=true` → session), `write_stdin`.

## Lancer
```
run.cmd            # crée la venv au 1er lancement
```
Config dans `.env` (voir `.env.example`) : `MCP_TOKEN`, `PORT` (9931), `ALLOWED_ROOTS` (`D:/Repos`, séparateur `;`), `HOST` (0.0.0.0).

- Endpoint : `http://<ip-du-pc>:9931/mcp/<MCP_TOKEN>` (token dans l URL, pas de Bearer ; tout autre chemin = 404)
- Public : `https://mcp.djoe.ovh/mcp/<MCP_TOKEN>` (nginx -> 192.168.1.138:9931)
- `GET /health` → `ok` (sans auth)
- Test : `.venv\Scripts\python.exe test_client.py`

## Client Hermes (`config.yaml`)
```yaml
mcp_servers:
  pc:
    url: "http://127.0.0.1:9931/mcp/<MCP_TOKEN>"
    timeout: 300
```

## Accès depuis l'extérieur
Le serveur exécute des commandes arbitraires : ne l'exposez **jamais** en HTTP clair sur Internet.
Passez par Tailscale/WireGuard, ou un reverse proxy HTTPS (Caddy/nginx), ou `cloudflared tunnel`.
Les clients type ChatGPT/Claude.ai « connecteur personnalisé » exigent une URL HTTPS publique.

## Serveurs MCP locaux (passerelle)
Déclarez des serveurs MCP stdio dans `upstreams.json` (même forme que Claude Desktop : `command`, `args`, `env`, `enabled`).
Leurs outils apparaissent sous `<nom>__<outil>`, par exemple `blender__execute_blender_code`.
Après une modification : appelez l'outil `upstreams` avec `action="reload"`. Le statut et les erreurs sont visibles avec `action="status"`.

### Blender
- Serveur : `blender-mcp` (installé dans la venv)
- Add-on : `blender/addon.py` (copie de [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp), MIT, port par défaut changé en 9877), installé dans Blender 5.2, **port 9877**. Le port 9876 reste pour l'extension officielle Blender Lab.
- Le serveur de l'add-on démarre tout seul à l'ouverture de Blender. Il suffit que Blender soit ouvert.
- Test : `.venv\Scripts\python.exe test_blender.py`
