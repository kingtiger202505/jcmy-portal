"""RDP probe with correct X.224 parsing, then a TLS upgrade to read the
server's certificate (xrdp auto-generates one whose CN is the hostname).
"""
import socket
import ssl
import struct
import sys

PROTOCOL_NAMES = {
    0: "PROTOCOL_RDP (standard RDP security)",
    1: "PROTOCOL_SSL (TLS)",
    2: "PROTOCOL_HYBRID (CredSSP / NLA)",
    3: "PROTOCOL_RDSTLS",
    4: "PROTOCOL_HYBRID_EX (NLA + early user auth)",
}

X224_CR = bytes([0x0E, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00])
NEG_REQ = struct.pack("<BBHI", 0x01, 0x00, 0x08, 0x0B)  # RDP|SSL|HYBRID


def tpkt(payload: bytes) -> bytes:
    return bytes([0x03, 0x00]) + struct.pack(">H", len(payload) + 4) + payload


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "43.128.144.27"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 3389

    s = socket.create_connection((host, port), timeout=10)
    s.sendall(tpkt(X224_CR + NEG_REQ))
    data = s.recv(4096)
    print(f"CC raw ({len(data)} bytes): {data.hex()}")

    if data[0] != 0x03:
        print("not a TPKT reply — this is not an RDP listener")
        return 1
    # Header is a fixed 7 bytes: LI(0x0e) Type(0xd0) DST-REF(2) SRC-REF(2) CLASS(1)
    # The LI byte in RDP is always 0x0e, it is NOT the real X.224 length.
    neg = data[11:]
    selected = None
    if len(neg) >= 8 and neg[0] == 0x02:
        _, flags, nlen, selected = struct.unpack("<BBHI", neg[:8])
        print(f"RDP Negotiation Response: flags=0x{flags:02x} len={nlen} "
              f"selectedProtocol={selected} ({PROTOCOL_NAMES.get(selected)})")
    else:
        print("no negotiation response -> plain RDP security, no TLS")
        return 0

    if selected not in (1, 2, 3, 4):
        print("server selected standard RDP security; skipping TLS")
        return 0

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        tls = ctx.wrap_socket(s, server_hostname=host)
    except Exception as e:
        print(f"TLS upgrade failed: {e}")
        return 1

    print(f"TLS: {tls.version()} {tls.cipher()[0]}")
    der = tls.getpeercert(binary_form=True)
    print(f"cert DER length: {len(der) if der else 0}")
    name = tls.getpeercert() or {}
    print(f"cert dict: {name}")
    # Decode the DER by hand so we see the subject even without a CA bundle.
    print(f"cert hex head: {der[:64].hex() if der else ''}")
    tls.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())