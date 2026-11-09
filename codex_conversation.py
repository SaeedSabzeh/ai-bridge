"""Read-only MCP App and text view for the Codex AI Bridge plugin."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


VIEW = Path(__file__).resolve().parent / "codex-plugin" / "conversation.html"
NAMES = {"claude": "Claude", "chatgpt": "Codex", "user": "You"}
STATUSES = {"queued": "Waiting", "running": "Working", "answered": "Answered",
            "failed": "Failed", "cancelled": "Cancelled"}
CONTINUITY = {"new": "New session", "resumed": "Resumed session", "recovered": "Recovered in a new session",
              "starting": "New session requested", "resuming": "Resume requested", "recovering": "Recovery requested"}


def excerpt(value, limit):
    text = value or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


class ConversationView:
    names = ("show_conversation", "refresh_conversation")
    resource = {
        "uri": "ui://ai-bridge/conversation.html",
        "name": "Claude ↔ Codex",
        "description": "Recent exchanges from the local AI Bridge mailbox.",
        "mimeType": "text/html;profile=mcp-app",
    }

    def __init__(self, mailbox, lookup=None, checkpoint=None):
        self.mailbox = mailbox
        self.lookup = lookup
        self.checkpoint = checkpoint

    def tools(self):
        schema = {"type": "object", "properties": {
            "task_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$",
                        "description": "Optional task filter."},
        }, "additionalProperties": False}
        annotations = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        return [
            {"name": "show_conversation", "title": self.resource["name"],
             "description": "Show up to six recent Claude–Codex questions and replies from the local "
                            "mailbox. Use to open the conversation panel or check recent exchanges.",
             "inputSchema": schema, "annotations": annotations,
             "_meta": {"ui": {"resourceUri": self.resource["uri"]},
                       "openai/ui": {"entrypoints": [{"type": "thread"}]}}},
            {"name": "refresh_conversation", "title": "Refresh AI Bridge conversation",
             "description": "Refresh the local conversation panel without sending a message.",
             "inputSchema": {**schema, "properties": {**schema["properties"],
                 "request_id": {"type": "string", "description": "Open the details of one request."}}}, "annotations": annotations,
             "_meta": {"ui": {"visibility": ["app"]}}},
        ]

    def read_resource(self, uri):
        if uri != self.resource["uri"]:
            raise ValueError("Unknown conversation resource.")
        return {"contents": [{**self.resource, "text": VIEW.read_text(encoding="utf-8"),
                              "_meta": {"ui": {"prefersBorder": True}}}]}

    def call(self, name, args):
        if not isinstance(args, dict) or set(args) - {"task_id", "request_id"}:
            raise ValueError("Supply an optional task ID or request ID.")
        request_id = args.get("request_id")
        if request_id is not None:
            if (name != "refresh_conversation" or not self.lookup or not isinstance(request_id, str)
                    or not re.fullmatch(r"[a-f0-9]{32}", request_id) or "task_id" in args):
                raise ValueError("Invalid request ID.")
            item = self.lookup(request_id)
            return {"content": [{"type": "text", "text": "Loaded local request details."}],
                    "_meta": {"request": {key: item.get(key) for key in (
                        "id", "question", "context", "shared_history", "shared_checkpoint", "error_detail")}}}
        task = args.get("task_id")
        if task is not None and (not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task)):
            raise ValueError("Invalid task ID.")
        records = [item for item in self.mailbox() if not task or item["task_id"] == task]
        records = records[:24 if task and name == "refresh_conversation" else 6]
        exchanges = []
        for item in records:
            status = STATUSES.get(item["status"], item["status"])
            if item["status"] == "queued" and item["mode"] == "manual":
                status = "Waiting in inbox"
            exchanges.append({
                "id": item["id"], "task": item["task_id"], "time": item["created_at"],
                "topic": item.get("topic"), "mode": item["mode"], "events": item.get("events", []),
                "continuity": CONTINUITY.get(item.get("continuity"), "Unknown") if item["mode"] == "auto" else "Manual reply",
                "from": NAMES.get(item["sender"], item["sender"]),
                "to": NAMES.get(item["recipient"], item["recipient"]),
                "status": status, "question": excerpt(item["question"], 1200),
                "answer": excerpt(item["answer"], 2400), "error": excerpt(item["error"], 350),
            })
        text = "Refreshed the local conversation."
        if name == "show_conversation":
            lines = []
            for item in exchanges:
                lines.extend([
                    f"**{item['from']} → {item['to']}** · {item['status']} · task {item['task']}",
                    item["question"], item["answer"] or item["error"],
                    f"Request ID: {item['id']}", "",
                ])
            text = "\n".join(lines).strip() or "No exchanges found for this view."
        return {"content": [{"type": "text", "text": text}],
                "_meta": {"conversation": {"task_id": task, "exchanges": exchanges,
                    "checkpoint": self.checkpoint(task) if task and self.checkpoint else None}}}


def serve_local_panel():
    """Show the same read-only conversation in Codex's browser panel."""
    from ai_bridge import BASE, BridgeError, Mailbox

    mailbox = Mailbox(BASE, {}, external_workers=True)
    view = ConversationView(lambda: mailbox.listing(limit=None, expire=False),
                            lambda request_id: mailbox.get(request_id, expire=False), mailbox.checkpoint)
    key = secrets.token_urlsafe(32)
    last_access = time.monotonic()

    class PanelHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send_body(self, status, body, mime_type):
            self.send_response(status)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            nonlocal last_access
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
                self.send_error(403)
                return
            if self.path == "/":
                self.send_body(200, VIEW.read_bytes(), "text/html; charset=utf-8")
                return
            url = urlsplit(self.path)
            if url.path != "/api/conversation":
                self.send_error(404)
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {key}".encode("ascii")):
                self.send_error(403)
                return
            last_access = time.monotonic()
            query = parse_qs(url.query)
            args = {key: query[key][0] for key in ("task_id", "request_id") if key in query}
            try:
                result = view.call("refresh_conversation", args)
            except (BridgeError, OSError, ValueError):
                self.send_error(503, "The conversation is temporarily unavailable.")
                return
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")

    with ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler) as server:
        server.timeout = 1
        print(f"http://127.0.0.1:{server.server_port}/#{key}", flush=True)
        while time.monotonic() - last_access < 300:
            server.handle_request()


if __name__ == "__main__":
    serve_local_panel()
