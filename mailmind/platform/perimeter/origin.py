"""The Origins this server's own pages send."""


def own_origins(port):
    return (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
