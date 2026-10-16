"""Grab the certificate the RDP server presents and decode it fully."""
import socket
import ssl
import struct
import sys

from cryptography import x509

X224_CR = bytes([0x0E, 0xE0, 0x00, 0x00, 0x00, 0x00, 0x00])
NEG_REQ = struct.pack("<BBHI", 0x01, 0x00, 0x08, 0x0B)

host = sys.argv[1] if len(sys.argv) > 1 else "43.128.144.27"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 3389

payload = X224_CR + NEG_REQ
s = socket.create_connection((host, port), timeout=10)
s.sendall(bytes([0x03, 0x00]) + struct.pack(">H", len(payload) + 4) + payload)
cc = s.recv(4096)
selected = struct.unpack("<I", cc[15:19])[0]
print(f"selectedProtocol = {selected}")

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
tls = ctx.wrap_socket(s, server_hostname=host)
der = tls.getpeercert(binary_form=True)
tls.close()

cert = x509.load_der_x509_certificate(der)
print(f"subject   : {cert.subject.rfc4514_string()}")
print(f"issuer    : {cert.issuer.rfc4514_string()}")
print(f"serial    : {cert.serial_number}")
print(f"not before: {cert.not_valid_before_utc}")
print(f"not after : {cert.not_valid_after_utc}")
print(f"signature : {cert.signature_hash_algorithm.name}")
try:
    print(f"SAN       : {cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value}")
except x509.ExtensionNotFound:
    print("SAN       : none")
print(f"is CA     : {cert.extensions.get_extension_for_class(x509.BasicConstraints).value}")
print(f"key type  : {cert.public_key().__class__.__name__}")