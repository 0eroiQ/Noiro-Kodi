import json
import os
import socket
import socketserver
import threading


class RpcError(RuntimeError):
    pass


class JsonRpcClient(object):
    def __init__(self, socket_path, timeout=10):
        self.socket_path = socket_path
        self.timeout = timeout
        self._counter = 0

    def call(self, method, params=None):
        self._counter += 1
        request_id = self._counter
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self.timeout)
        try:
            client.connect(self.socket_path)
            client.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
            if not chunks:
                raise RpcError("Noiro service returned no response")
            response = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
        except (OSError, ValueError) as error:
            raise RpcError(str(error))
        finally:
            client.close()
        if response.get("id") != request_id:
            raise RpcError("Noiro service returned a mismatched response")
        if response.get("error"):
            raise RpcError(response["error"].get("message") or "Noiro request failed")
        return response.get("result")


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class JsonRpcServer(object):
    def __init__(self, socket_path, dispatcher):
        self.socket_path = socket_path
        self.dispatcher = dispatcher
        self.server = None
        self.thread = None

    def start(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        directory = os.path.dirname(self.socket_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        dispatcher = self.dispatcher

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline(1024 * 1024)
                request_id = None
                try:
                    request = json.loads(line.decode("utf-8"))
                    request_id = request.get("id")
                    if request.get("jsonrpc") != "2.0" or not request.get("method"):
                        raise RpcError("Invalid JSON-RPC request")
                    result = dispatcher(request["method"], request.get("params") or {})
                    response = {"jsonrpc": "2.0", "id": request_id, "result": result}
                except Exception as error:  # RPC boundary: errors must become JSON, not kill service
                    response = {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": str(error)},
                    }
                self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))

        self.server = _ThreadingUnixServer(self.socket_path, Handler)
        os.chmod(self.socket_path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, name="noiro-rpc", daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
