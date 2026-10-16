"""Only report a port as open if it returns real bytes.

The sandbox TCP proxy accepts every SYN, so connect() success proves nothing.
Here each port must either send a banner unprompted or answer an HTTP request.
"""
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

HOST = sys.argv[1] if len(sys.argv) > 1 else "43.128.144.27"
PORTS = [22, 80, 443, 3000, 5000, 5601, 8000, 8001, 8080, 8081, 8443,
         8888, 8889, 9000, 9200, 11434, 20128, 20129, 30000]

HTTP_PROBE = (b"GET / HTTP/1.1\r\nHost: " + HOST.encode() +
              b"\r\nUser-Agent: curl/8\r\nConnection: close\r\n\r\n")


def probe(port: int):
    s = socket.socket()
    s.settimeout(8)
    try:
        s.connect((HOST, port))
        # Phase 1: does it speak first (SSH, MySQL, Redis...)?
        s.settimeout(3)
        try:
            first = s.recv(256)
        except Exception:
            first = b""
        if first:
            return port, True, "banner", first
        # Phase 2: poke it with HTTP.
        try:
            s.sendall(HTTP_PROBE)
        except Exception:
            return port, False, "send failed", b""
        s.settimeout(6)
        chunks = b""
        try:
            while len(chunks) < 512:
                b = s.recv(256)
                if not b:
                    break
                chunks += b
        except Exception:
            pass
        if chunks:
            return port, True, "http", chunks
        return port, False, "silent", b""
    except Exception as e:
        return port, False, f"{type(e).__name__}", b""
    finally:
        s.close()


with ThreadPoolExecutor(max_workers=16) as ex:
    results = sorted(ex.map(probe, PORTS))

print("=== ports that returned real data ===")
for port, ok, how, data in results:
    if ok:
        text = data.decode("utf-8", "replace")
        first_line = text.split("\n")[0].strip()[:100]
        print(f"OPEN {port:<6} [{how}] {first_line}")
print("\n=== silent / no real data (cannot be confirmed open) ===")
print(", ".join(f"{p}({how})" for p, ok, how, _ in results if not ok))