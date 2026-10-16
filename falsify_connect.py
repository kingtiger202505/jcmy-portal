"""Falsification test: do connections that SHOULD fail also 'succeed'?
If yes, something is accepting all SYNs and open-port results are meaningless.
"""
import socket
import time

TARGETS = [
    ("43.128.144.27", 3389, "real RDP (verified by handshake)"),
    ("43.128.144.27", 12345, "almost certainly closed"),
    ("43.128.144.27", 9999, "almost certainly closed"),
    ("43.128.144.27", 45678, "almost certainly closed"),
    ("1.2.3.4", 3389, "non-existent host"),
    ("192.0.2.1", 80, "TEST-NET-1, non-routable"),
    ("10.255.255.1", 80, "private, unroutable"),
]


def probe(host, port):
    s = socket.socket()
    s.settimeout(6)
    t0 = time.time()
    try:
        s.connect((host, port))
        dt = (time.time() - t0) * 1000
        s.settimeout(3)
        try:
            b = s.recv(64)
        except Exception:
            b = b"<no data>"
        return f"CONNECTED in {dt:.0f}ms, recv={b[:40]!r}"
    except Exception as e:
        dt = (time.time() - t0) * 1000
        return f"FAILED in {dt:.0f}ms: {type(e).__name__}: {e}"
    finally:
        s.close()


for host, port, note in TARGETS:
    print(f"{host}:{port:<6} ({note})")
    print(f"    {probe(host, port)}")