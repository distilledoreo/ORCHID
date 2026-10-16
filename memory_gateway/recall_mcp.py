"""Thin stdio MCP adapter bound to one ORCHID project and thread."""

import argparse
import asyncio
import json
import sys

import httpx

VERSION = "2025-06-18"
TOOL = {"name": "recall", "description": "Search original ORCHID messages; query event:<id> for a source.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 512},
                                                          "k": {"type": "integer", "minimum": 1, "maximum": 50}},
                        "required": ["query"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True}}


class RecallMCP:
    def __init__(self, client, project, thread):
        self.client = client
        self.headers = {"x-memory-project": project, "x-memory-thread": thread}
        self.initialized = False
        self.ready = False

    async def handle(self, message):
        request_id = message.get("id") if isinstance(message, dict) else None
        def error(code, text):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": text}}
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return error(-32600, "Invalid request")
        method = message["method"]
        if method == "notifications/initialized":
            self.ready = self.initialized
            return None
        if "id" not in message:
            return None
        if method == "initialize":
            self.initialized = True
            result = {"protocolVersion": VERSION, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "orchid-raw-recall", "version": "1.0"}}
        elif method == "ping":
            result = {}
        elif not self.ready:
            return error(-32002, "Server is not initialized")
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or params.get("name") != "recall":
                return error(-32602, "Unknown tool")
            args = params.get("arguments")
            if not isinstance(args, dict) or set(args) - {"query", "k"}:
                return error(-32602, "Invalid recall arguments")
            query, k = args.get("query"), args.get("k", 5)
            if not isinstance(query, str) or not query.strip() or len(query) > 512 or not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 50:
                return error(-32602, "Invalid recall arguments")
            try:
                response = await self.client.post("/v1/memory/recall", headers=self.headers, json={"query": query, "k": k})
                response.raise_for_status()
                payload = response.json()
                result = {"content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload}
            except Exception:
                result = {"content": [{"type": "text", "text": "Scoped recall is unavailable; retry later."}], "isError": True}
        else:
            return error(-32601, "Method not found")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def serve(args):
    async with httpx.AsyncClient(base_url=args.url, timeout=15) as client:
        server = RecallMCP(client, args.project, args.thread)
        for line in sys.stdin:
            try:
                message = json.loads(line)
                response = await server.handle(message)
            except json.JSONDecodeError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                print(json.dumps(response, separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7331")
    parser.add_argument("--project", required=True)
    parser.add_argument("--thread", required=True)
    asyncio.run(serve(parser.parse_args()))


if __name__ == "__main__":
    main()
