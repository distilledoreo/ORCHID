"""Idle admission tracks complete foreground responses, including streaming."""

import asyncio


class ActivityGate:
    def __init__(self):
        self.active = 0
        self.idle = asyncio.Event()
        self.idle.set()

    def begin(self):
        self.active += 1
        self.idle.clear()

    def end(self):
        self.active -= 1
        if self.active == 0:
            self.idle.set()


class ForegroundActivityMiddleware:
    def __init__(self, app, gate):
        self.app = app
        self.gate = gate

    async def __call__(self, scope, receive, send):
        track = scope["type"] == "http" and scope.get("path") == "/v1/chat/completions"
        if track:
            self.gate.begin()
        try:
            await self.app(scope, receive, send)
        finally:
            if track:
                self.gate.end()
