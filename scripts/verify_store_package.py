#!/usr/bin/env python3
"""Verify Google's signed public CRX3 against an immutable upload ZIP.

Uses the existing OpenSSL CLI; no credentials, browser profile or third-party
Python package is needed. Never extracts the archive or changes an installation.
Format and signature algorithms follow Chromium's crx3.proto and crx_verifier.cc:
https://raw.githubusercontent.com/chromium/chromium/main/components/crx_file/crx3.proto
https://raw.githubusercontent.com/chromium/chromium/main/components/crx_file/crx_verifier.cc
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

ITEM_ID = "dbfkmbhkefkppdbbiifihekopelgcdgb"
VERIFIER_POLICY_VERSION = "google-crx3-publisher-v1"
TRUSTED_PUBLISHER_KEY_SHA256 = "61f7f2a6bfcf74cd0bc1fe2497cc9b04254c658f79f2145392867ea8366367cf"
UPDATE_URL = "https://clients2.google.com/service/update2/crx"
OPENSSL = "/usr/bin/openssl"
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_HEADER_BYTES = 1024 * 1024
MAX_PROOFS = 4
DOWNLOAD_SECONDS = 20
SOCKET_TIMEOUT = 4


class VerificationError(RuntimeError):
    """A public package could not be proven to match the reviewed upload."""


def _download_url_allowed(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username is not None or
                parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment):
            return False
        return ((parsed.hostname == "clients2.google.com" and parsed.path == "/service/update2/crx") or
                (parsed.hostname == "clients2.googleusercontent.com" and parsed.path.startswith("/crx/")))
    except ValueError:
        return False


class _GoogleRedirects(HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 2

    def redirect_request(self, request, fp, code, message, headers, newurl):
        if not _download_url_allowed(newurl):
            raise VerificationError("Store download redirected outside the permitted Google origins")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download_public_crx(item_id: str) -> bytes:
    if not re.fullmatch(r"[a-p]{32}", item_id):
        raise VerificationError("Invalid expected extension identity")
    query = urlencode({"response": "redirect", "prodversion": "156.0.0.0",
                       "acceptformat": "crx3", "x": f"id={item_id}&uc"})
    request = Request(UPDATE_URL + "?" + query, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/x-chrome-extension, application/octet-stream"})
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    try:
        with build_opener(_GoogleRedirects()).open(request, timeout=SOCKET_TIMEOUT) as response:
            if not _download_url_allowed(response.url):
                raise VerificationError("Store download returned an unexpected Google origin")
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_PACKAGE_BYTES):
                raise VerificationError("Store package exceeds the download limit")
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise VerificationError("Store package download exceeded its time limit")
                chunk = response.read(min(65536, MAX_PACKAGE_BYTES + 1 - size))
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_PACKAGE_BYTES:
                    raise VerificationError("Store package exceeds the download limit")
    except (HTTPError, URLError, HTTPException, TimeoutError, OSError):
        raise VerificationError("Public store package download failed") from None


def _fields(data: bytes) -> dict[int, list[bytes]]:
    def varint(offset):
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise VerificationError("Truncated CRX3 header")
            octet = data[offset]
            offset += 1
            value |= (octet & 127) << shift
            if octet < 128:
                return value, offset
        raise VerificationError("Invalid CRX3 header integer")
    fields, offset = {}, 0
    while offset < len(data):
        tag, offset = varint(offset)
        if not 1 <= tag >> 3 < (1 << 29) or tag & 7 != 2:
            raise VerificationError("Unsupported CRX3 header field")
        length, offset = varint(offset)
        if length > len(data) - offset:
            raise VerificationError("Truncated CRX3 header field")
        fields.setdefault(tag >> 3, []).append(data[offset:offset + length])
        offset += length
    return fields


def _single(fields, name):
    values = fields.get(name, [])
    if len(values) != 1:
        raise VerificationError("Missing or duplicate CRX3 identity/signature field")
    return values[0]


def _openssl(args):
    try:
        result = subprocess.run([OPENSSL, *args], capture_output=True, timeout=3,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationError("OpenSSL proof verification failed or timed out") from None
    if result.returncode:
        raise VerificationError("Invalid CRX3 proof or signature")
    return result.stdout


def verify_crx3(crx: bytes, expected_item_id: str) -> tuple[bytes, list[dict]]:
    if not re.fullmatch(r"[a-p]{32}", expected_item_id):
        raise VerificationError("Invalid expected extension identity")
    if len(crx) > MAX_PACKAGE_BYTES or len(crx) < 12 or crx[:4] != b"Cr24":
        raise VerificationError("Invalid or oversized CRX3 package")
    version, length = struct.unpack("<II", crx[4:12])
    if version != 3 or length > MAX_HEADER_BYTES or length > len(crx) - 12:
        raise VerificationError("Invalid CRX3 format or header size")
    header_bytes, archive = crx[12:12 + length], crx[12 + length:]
    if any(marker in header_bytes for marker in (b"PK\x05\x06", b"PK\x06\x07", b"PK\x06\x06")):
        raise VerificationError("Ambiguous CRX3 header archive marker")
    header = _fields(header_bytes)
    signed = _single(header, 10000)
    identity = _single(_fields(signed), 1)
    if len(identity) != 16:
        raise VerificationError("Invalid signed CRX3 identity")
    actual_id = "".join(chr(97 + nibble) for byte in identity for nibble in (byte >> 4, byte & 15))
    if actual_id != expected_item_id:
        raise VerificationError("Signed CRX3 identity differs from the expected extension")
    proofs = [(kind, proof) for kind in (2, 3) for proof in header.get(kind, [])]
    if not 1 <= len(proofs) <= MAX_PROOFS:
        raise VerificationError("Invalid CRX3 proof count")
    message = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed)) + signed + archive
    developer, publisher, evidence = False, False, []
    with tempfile.TemporaryDirectory(prefix="dtg-crx-proof-") as temporary:
        root = Path(temporary)
        (root / "signed.bin").write_bytes(message)
        for kind, encoded in proofs:
            proof = _fields(encoded)
            key, signature = _single(proof, 1), _single(proof, 2)
            if len(key) > 4096 or len(signature) > 1024:
                raise VerificationError("Oversized CRX3 public proof")
            pem = b"-----BEGIN PUBLIC KEY-----\n" + base64.encodebytes(key) + b"-----END PUBLIC KEY-----\n"
            (root / "public.pem").write_bytes(pem)
            (root / "signature.bin").write_bytes(signature)
            if kind == 2:
                _openssl(["rsa", "-pubin", "-in", str(root / "public.pem"), "-noout"])
            else:
                info = _openssl(["ec", "-pubin", "-in", str(root / "public.pem"), "-text", "-noout"])
                if b"ASN1 OID: prime256v1" not in info:
                    raise VerificationError("CRX3 ECDSA proof must use the P-256 curve")
            rsa_mode = ["-sigopt", "rsa_padding_mode:pkcs1"] if kind == 2 else []
            _openssl(["dgst", "-sha256", "-verify", str(root / "public.pem"),
                      "-signature", str(root / "signature.bin"), *rsa_mode, str(root / "signed.bin")])
            digest = hashlib.sha256(key).hexdigest()
            developer |= bytes.fromhex(digest)[:16] == identity
            publisher |= kind == 3 and digest == TRUSTED_PUBLISHER_KEY_SHA256
            evidence.append({"algorithm": "RSA_PKCS1_SHA256" if kind == 2 else "ECDSA_SHA256",
                             "key_sha256": digest})
    if not developer or not publisher:
        raise VerificationError("Required developer identity or trusted Google publisher proof is missing")
    return archive, evidence


def _zip_files(data: bytes) -> tuple[dict[str, bytes], set[str]]:
    if len(data) > MAX_PACKAGE_BYTES:
        raise VerificationError("ZIP package exceeds the size limit")
    files, directories, names, total = {}, set(), set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 512:
                raise VerificationError("ZIP package contains too many entries")
            for entry in archive.infolist():
                name = entry.filename
                canonical = name[:-1] if entry.is_dir() else name
                path = PurePosixPath(canonical)
                if (not canonical or len(name) > 512 or name in names or "\x00" in name or entry.orig_filename != name or
                        "\\" in name or ":" in name or path.is_absolute() or
                        any(part in {"", ".", ".."} for part in canonical.split("/"))):
                    raise VerificationError("ZIP contains a duplicate or unsafe entry")
                names.add(name)
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.flag_bits & 1 or
                        kind not in ((0, stat.S_IFDIR) if entry.is_dir() else (0, stat.S_IFREG))):
                    raise VerificationError("ZIP contains an encrypted or nonregular entry")
                if entry.file_size > MAX_PACKAGE_BYTES or total + entry.file_size > MAX_EXPANDED_BYTES:
                    raise VerificationError("Expanded ZIP exceeds the size limit")
                total += entry.file_size
                if entry.is_dir():
                    directories.add(canonical)
                else:
                    files[name] = archive.read(entry)
    except VerificationError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, ValueError):
        raise VerificationError("Invalid ZIP package") from None
    return files, directories


def verify_store_contents(store_zip: bytes, upload_zip: bytes, expected_version: str) -> int:
    actual, directories = _zip_files(store_zip)
    expected, _ = _zip_files(upload_zip)
    if "manifest.json" not in expected or "manifest.json" not in actual:
        raise VerificationError("Package manifest is missing")
    extra = set(actual) - set(expected)
    if extra - {"_metadata/verified_contents.json"} or set(expected) - set(actual):
        raise VerificationError("Store package runtime file inventory differs from the upload")
    expected_dirs = {str(parent) for name in expected for parent in PurePosixPath(name).parents
                     if str(parent) != "."} | {"_metadata"}
    if directories - expected_dirs:
        raise VerificationError("Store package contains unexpected directories")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        before = json.loads(expected["manifest.json"], object_pairs_hook=unique_object)
        after = json.loads(actual["manifest.json"], object_pairs_hook=unique_object)
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError()
    except (UnicodeDecodeError, ValueError):
        raise VerificationError("Invalid package manifest") from None
    if before.get("version") != expected_version or after.get("version") != expected_version:
        raise VerificationError("Package version differs from the published revision")
    if "update_url" in before:
        raise VerificationError("Upload package must not prescribe a store update URL")
    update_url = after.pop("update_url", None)
    if update_url not in (None, UPDATE_URL) or before != after:
        raise VerificationError("Store manifest differs beyond Google's expected update URL")
    if any(actual[name] != data for name, data in expected.items() if name != "manifest.json"):
        raise VerificationError("Store runtime bytes differ from the immutable upload")
    return len(expected)


def verify_published_package(upload_zip: Path, expected_version: str,
                             expected_item_id: str = ITEM_ID) -> dict:
    try:
        fd = os.open(upload_zip, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise VerificationError("Upload artifact is not a regular file")
            uploaded = source.read(MAX_PACKAGE_BYTES + 1)
    except OSError:
        raise VerificationError("Cannot read the immutable upload artifact") from None
    if len(uploaded) > MAX_PACKAGE_BYTES:
        raise VerificationError("Upload artifact exceeds the size limit")
    crx = download_public_crx(expected_item_id)
    archive, proofs = verify_crx3(crx, expected_item_id)
    count = verify_store_contents(archive, uploaded, expected_version)
    return {"store_payload_verified": True, "policy_version": VERIFIER_POLICY_VERSION,
            "item_id": expected_item_id, "version": expected_version,
            "upload_sha256": hashlib.sha256(uploaded).hexdigest(),
            "store_crx_sha256": hashlib.sha256(crx).hexdigest(),
            "store_zip_sha256": hashlib.sha256(archive).hexdigest(),
            "runtime_files_verified": count, "proofs": proofs,
            "verified_at": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("upload_zip", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--item-id", default=ITEM_ID)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_published_package(args.upload_zip, args.expected_version, args.item_id),
                         sort_keys=True))
    except VerificationError as exc:
        parser.exit(1, f"Store verification failed: {exc}\n")


if __name__ == "__main__":
    main()
