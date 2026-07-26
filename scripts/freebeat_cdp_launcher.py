#!/usr/bin/env python3
"""Host-side temporary Chrome CDP launcher for Freebeat registration.

Run this on the Docker host, not inside the any-auto-register container.
The container calls /launch with the task proxy, uses the returned cdp_url,
then calls /release so the Chrome process and temporary profile are removed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest
from urllib.parse import quote, unquote, urlparse, urlunparse


DEFAULT_URL = "https://freebeat.ai/login?redirectTo=%2F"


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("content-length") or 0)
    if length <= 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8", "replace")
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(handler: BaseHTTPRequestHandler, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _find_free_port(host: str, start: int, end: int) -> int:
    for port in range(start, end + 1):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {host}:{start}-{end}")


def _parse_host_map(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(value or "").split(","):
        if "=" not in item:
            continue
        source, target = item.split("=", 1)
        source = source.strip().lower()
        target = target.strip()
        if source and target:
            result[source] = target
    return result


def _normalize_proxy(proxy: str, *, default_proxy: str, host_map: dict[str, str]) -> str:
    raw = str(proxy or default_proxy or "").strip()
    if not raw:
        return ""
    if raw.startswith("socks://"):
        raw = "socks5://" + raw[len("socks://") :]
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return raw
    hostname = parsed.hostname
    mapped = host_map.get(hostname.lower(), hostname)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    netloc = f"{auth}{mapped}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path or "", parsed.params or "", parsed.query or "", parsed.fragment or ""))


def _safe_proxy_for_log(proxy: str) -> str:
    parsed = urlparse(str(proxy or ""))
    if parsed.username and parsed.hostname:
        return f"{parsed.scheme}://***@{parsed.hostname}{':' + str(parsed.port) if parsed.port else ''}"
    return str(proxy or "")


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()


class _ForwardHandler(socketserver.BaseRequestHandler):
    target_host = "127.0.0.1"
    target_port = 0

    def handle(self) -> None:
        try:
            upstream = socket.create_connection((self.target_host, self.target_port), timeout=8)
        except OSError:
            return
        left = threading.Thread(target=_pump, args=(self.request, upstream), daemon=True)
        right = threading.Thread(target=_pump, args=(upstream, self.request), daemon=True)
        left.start()
        right.start()
        left.join()
        right.join()


class _ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], target_host: str, target_port: int):
        handler = type(
            "ForwardHandler",
            (_ForwardHandler,),
            {"target_host": target_host, "target_port": target_port},
        )
        super().__init__(server_address, handler)


@dataclass
class BrowserSession:
    session_id: str
    proc: subprocess.Popen
    profile_dir: Path
    chrome_port: int
    public_port: int
    cdp_url: str
    proxy: str
    created_at: float
    forwarder: _ForwardServer
    forward_thread: threading.Thread


class LauncherState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.sessions: dict[str, BrowserSession] = {}
        self.lock = threading.Lock()
        self.launch_lock = threading.Lock()
        self.host_map = _parse_host_map(args.proxy_host_map)
        self.profile_root = Path(args.profile_root).expanduser()
        self.profile_root.mkdir(parents=True, exist_ok=True)

    def cleanup_expired(self) -> None:
        deadline = time.time() - max(60, int(self.args.ttl_seconds))
        expired: list[str] = []
        with self.lock:
            for session_id, session in self.sessions.items():
                if session.created_at < deadline or session.proc.poll() is not None:
                    expired.append(session_id)
        for session_id in expired:
            self.release(session_id)

    def launch(self, payload: dict) -> dict:
        self.cleanup_expired()
        session_id = uuid.uuid4().hex
        profile_dir = Path(tempfile.mkdtemp(prefix=f"freebeat-{session_id[:10]}-", dir=str(self.profile_root)))
        chrome_port = _find_free_port(self.args.chrome_bind_host, self.args.chrome_port_start, self.args.chrome_port_end)
        public_port = _find_free_port(self.args.listen_host, self.args.public_port_start, self.args.public_port_end)
        proxy = _normalize_proxy(
            str(payload.get("proxy") or ""),
            default_proxy=self.args.default_proxy,
            host_map=self.host_map,
        )
        url = str(payload.get("url") or DEFAULT_URL).strip() or DEFAULT_URL
        locale = str(payload.get("locale") or self.args.lang or "en-US").strip() or "en-US"
        user_agent = str(payload.get("user_agent") or "").strip()
        headless = _truthy(payload.get("headless"), False)

        forwarder = _ForwardServer((self.args.listen_host, public_port), self.args.chrome_bind_host, chrome_port)
        forward_thread = threading.Thread(target=forwarder.serve_forever, daemon=True)
        forward_thread.start()

        cmd = [
            self.args.chrome_binary,
            f"--remote-debugging-address={self.args.chrome_bind_host}",
            f"--remote-debugging-port={chrome_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile_dir}",
            f"--lang={locale}",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        if headless:
            cmd.append("--headless=new")
        if proxy:
            cmd.append(f"--proxy-server={proxy}")
        if user_agent and user_agent.lower() != "native":
            cmd.append(f"--user-agent={user_agent}")
        cmd.append(url)

        env = os.environ.copy()
        for key, value in {
            "DISPLAY": self.args.display,
            "WAYLAND_DISPLAY": self.args.wayland_display,
            "XAUTHORITY": self.args.xauthority,
            "DBUS_SESSION_BUS_ADDRESS": self.args.dbus_session_bus_address,
            "LANG": self.args.locale_env,
        }.items():
            if value:
                env[key] = value

        log_file = (profile_dir / "chrome.log").open("ab")
        try:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, env=env, close_fds=True)
        except Exception:
            log_file.close()
            forwarder.shutdown()
            forwarder.server_close()
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise
        log_file.close()

        cdp_url = f"http://{self.args.public_host}:{public_port}"
        session = BrowserSession(
            session_id=session_id,
            proc=proc,
            profile_dir=profile_dir,
            chrome_port=chrome_port,
            public_port=public_port,
            cdp_url=cdp_url,
            proxy=proxy,
            created_at=time.time(),
            forwarder=forwarder,
            forward_thread=forward_thread,
        )
        try:
            self._wait_ready(session, float(payload.get("timeout_seconds") or self.args.launch_timeout_seconds))
        except Exception as exc:
            log_tail = ""
            try:
                log_tail = (profile_dir / "chrome.log").read_text(
                    encoding="utf-8",
                    errors="replace",
                )[-1200:].strip()
            except Exception:
                pass
            self._destroy(session)
            if log_tail:
                raise RuntimeError(f"{exc}; chrome_log={log_tail}") from exc
            raise

        with self.lock:
            self.sessions[session_id] = session
        return {
            "ok": True,
            "session_id": session_id,
            "cdp_url": cdp_url,
            "release_url": f"http://{self.args.public_host}:{self.args.port}/release",
            "proxy": proxy,
            "profile_dir": str(profile_dir),
            "chrome_port": chrome_port,
            "public_port": public_port,
        }

    def _wait_ready(self, session: BrowserSession, timeout_seconds: float) -> None:
        deadline = time.time() + max(8.0, min(timeout_seconds, 45.0))
        last_error = ""
        while time.time() < deadline:
            if session.proc.poll() is not None:
                raise RuntimeError(f"chrome exited with code {session.proc.returncode}")
            try:
                with urlrequest.urlopen(f"{session.cdp_url}/json/version", timeout=3) as response:
                    if response.status == 200:
                        return
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.4)
        raise RuntimeError(f"chrome CDP did not become ready: {last_error}")

    def release(self, session_id: str) -> dict:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if not session:
            return {"ok": True, "released": False, "session_id": session_id}
        self._destroy(session)
        return {"ok": True, "released": True, "session_id": session_id}

    def _destroy(self, session: BrowserSession) -> None:
        with contextlib.suppress(Exception):
            if session.proc.poll() is None:
                session.proc.terminate()
                try:
                    session.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    session.proc.kill()
                    session.proc.wait(timeout=5)
        with contextlib.suppress(Exception):
            session.forwarder.shutdown()
        with contextlib.suppress(Exception):
            session.forwarder.server_close()
        shutil.rmtree(session.profile_dir, ignore_errors=True)


class LauncherHandler(BaseHTTPRequestHandler):
    state: LauncherState

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self.state.cleanup_expired()
            _write_json(
                self,
                200,
                {
                    "ok": True,
                    "sessions": len(self.state.sessions),
                    "listen_host": self.state.args.listen_host,
                    "port": self.state.args.port,
                },
            )
            return
        _write_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path.startswith("/launch"):
            try:
                payload = _read_json(self)
                with self.state.launch_lock:
                    result = self.state.launch(payload)
                print(
                    "launched session={session_id} cdp={cdp_url} proxy={proxy}".format(
                        session_id=result.get("session_id"),
                        cdp_url=result.get("cdp_url"),
                        proxy=_safe_proxy_for_log(str(result.get("proxy") or "")),
                    ),
                    flush=True,
                )
                _write_json(self, 200, result)
            except Exception as exc:
                _write_json(self, 500, {"ok": False, "error": str(exc)})
            return
        if self.path.startswith("/release"):
            payload = _read_json(self)
            result = self.state.release(str(payload.get("session_id") or ""))
            print(f"released session={result.get('session_id')} released={result.get('released')}", flush=True)
            _write_json(self, 200, result)
            return
        _write_json(self, 404, {"ok": False, "error": "not found"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default=os.getenv("FREEBEAT_CDP_LAUNCHER_HOST", "172.18.0.1"))
    parser.add_argument("--public-host", default=os.getenv("FREEBEAT_CDP_LAUNCHER_PUBLIC_HOST", "172.18.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FREEBEAT_CDP_LAUNCHER_PORT", "9321")))
    parser.add_argument("--chrome-binary", default=os.getenv("FREEBEAT_CDP_CHROME_BINARY", "/usr/bin/google-chrome"))
    parser.add_argument("--chrome-bind-host", default=os.getenv("FREEBEAT_CDP_CHROME_BIND_HOST", "127.0.0.1"))
    parser.add_argument("--profile-root", default=os.getenv("FREEBEAT_CDP_PROFILE_ROOT", "~/.cache/any-auto-register/freebeat-cdp"))
    parser.add_argument("--default-proxy", default=os.getenv("FREEBEAT_CDP_DEFAULT_PROXY", ""))
    parser.add_argument("--proxy-host-map", default=os.getenv("FREEBEAT_CDP_PROXY_HOST_MAP", "xray=127.0.0.1"))
    parser.add_argument("--chrome-port-start", type=int, default=int(os.getenv("FREEBEAT_CDP_CHROME_PORT_START", "9420")))
    parser.add_argument("--chrome-port-end", type=int, default=int(os.getenv("FREEBEAT_CDP_CHROME_PORT_END", "9519")))
    parser.add_argument("--public-port-start", type=int, default=int(os.getenv("FREEBEAT_CDP_PUBLIC_PORT_START", "9520")))
    parser.add_argument("--public-port-end", type=int, default=int(os.getenv("FREEBEAT_CDP_PUBLIC_PORT_END", "9619")))
    parser.add_argument("--ttl-seconds", type=int, default=int(os.getenv("FREEBEAT_CDP_TTL_SECONDS", "600")))
    parser.add_argument("--launch-timeout-seconds", type=float, default=float(os.getenv("FREEBEAT_CDP_LAUNCH_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--display", default=os.getenv("DISPLAY", ":0"))
    parser.add_argument("--wayland-display", default=os.getenv("WAYLAND_DISPLAY", "wayland-0"))
    parser.add_argument("--xauthority", default=os.getenv("XAUTHORITY", ""))
    parser.add_argument("--dbus-session-bus-address", default=os.getenv("DBUS_SESSION_BUS_ADDRESS", ""))
    parser.add_argument("--lang", default=os.getenv("FREEBEAT_CDP_LANG", "en-US"))
    parser.add_argument("--locale-env", default=os.getenv("LANG", "en_US.UTF-8"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = LauncherState(args)
    LauncherHandler.state = state
    server = ThreadingHTTPServer((args.listen_host, args.port), LauncherHandler)
    print(f"freebeat CDP launcher listening on http://{args.listen_host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        for session_id in list(state.sessions):
            state.release(session_id)


if __name__ == "__main__":
    main()
