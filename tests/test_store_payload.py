"""Adversarial signed-store-payload checks; generated test keys are never credentials."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request
import warnings
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "verify_store_package", Path(__file__).resolve().parents[1] / "scripts/verify_store_package.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def varint(value):
    encoded = bytearray()
    while value >= 128:
        encoded.append((value & 127) | 128)
        value >>= 7
    return bytes(encoded + bytes([value]))


def field(number, value):
    return varint((number << 3) | 2) + varint(len(value)) + value


def make_zip(files):
    stream = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, data in files:
            archive.writestr(name, data)
    return stream.getvalue()


@unittest.skipUnless(Path(verify.OPENSSL).is_file(), "OpenSSL is required for CRX proof verification")
class SignedPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="dtg-crx-fixture-")
        cls.root = Path(cls.temporary.name)
        cls.command("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(cls.root / "developer.key"))
        cls.command("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-out", str(cls.root / "publisher.key"))
        cls.der = {}
        for role in ("developer", "publisher"):
            cls.der[role] = cls.command("pkey", "-in", str(cls.root / f"{role}.key"),
                                        "-pubout", "-outform", "DER")
        cls.identity = hashlib.sha256(cls.der["developer"]).digest()[:16]
        cls.item_id = "".join(chr(97 + n) for b in cls.identity for n in (b >> 4, b & 15))
        cls.manifest = {"manifest_version": 3, "name": "Signed test extension", "version": "1.2.3"}
        cls.files = {"manifest.json": json.dumps(cls.manifest).encode(),
                     "service_worker.js": b'const FALLBACK_WS_URL = "";',
                     "content.js": b'"use strict";'}
        cls.uploaded = make_zip(cls.files.items())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def command(cls, *args):
        result = subprocess.run([verify.OPENSSL, *args], capture_output=True, timeout=10,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        if result.returncode:
            raise AssertionError("Test fixture OpenSSL operation failed")
        return result.stdout

    def setUp(self):
        self.pin = patch.object(verify, "TRUSTED_PUBLISHER_KEY_SHA256",
                                hashlib.sha256(self.der["publisher"]).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def archive(self, *, changed=None, entries=None):
        files = dict(self.files)
        files["manifest.json"] = json.dumps(self.manifest | {"update_url": verify.UPDATE_URL}).encode()
        files["_metadata/verified_contents.json"] = b'{"signed_content":{}}'
        files.update(changed or {})
        return make_zip(entries if entries is not None else files.items())

    def crx(self, archive=None, *, identity=None, publisher=True):
        signed = field(1, identity if identity is not None else self.identity)
        payload = self.archive() if archive is None else archive
        message = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed)) + signed + payload
        (self.root / "signed.bin").write_bytes(message)
        header = b""
        for role, number in (("developer", 2), ("publisher", 3)):
            if role == "publisher" and not publisher:
                continue
            signature = self.command("dgst", "-sha256", "-sign", str(self.root / f"{role}.key"),
                                     str(self.root / "signed.bin"))
            header += field(number, field(1, self.der[role]) + field(2, signature))
        header += field(10000, signed)
        return b"Cr24" + struct.pack("<II", 3, len(header)) + header + payload

    def check(self, crx):
        archive, proofs = verify.verify_crx3(crx, self.item_id)
        count = verify.verify_store_contents(archive, self.uploaded, "1.2.3")
        return count, proofs

    def test_valid_signed_package_and_documented_google_changes(self):
        count, proofs = self.check(self.crx())
        self.assertEqual(count, 3)
        self.assertEqual(len(proofs), 2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "upload.zip"
            path.write_bytes(self.uploaded)
            with patch.object(verify, "download_public_crx", return_value=self.crx()):
                evidence = verify.verify_published_package(path, "1.2.3", self.item_id)
        self.assertTrue(evidence["store_payload_verified"])
        self.assertEqual(evidence["upload_sha256"], hashlib.sha256(self.uploaded).hexdigest())
        self.assertEqual(evidence["policy_version"], verify.VERIFIER_POLICY_VERSION)

    def test_tampered_archive_cannot_pass_signatures(self):
        package = bytearray(self.crx())
        package[-1] ^= 1
        with self.assertRaises(verify.VerificationError):
            self.check(bytes(package))

    def test_forged_declared_identity_requires_matching_developer_key(self):
        forged = b"\xff" * 16
        with self.assertRaisesRegex(verify.VerificationError, "developer identity"):
            verify.verify_crx3(self.crx(identity=forged), "p" * 32)

    def test_rsa_pss_signature_is_not_accepted_as_pkcs1_proof(self):
        signed = field(1, self.identity)
        archive = self.archive()
        message = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed)) + signed + archive
        (self.root / "signed.bin").write_bytes(message)
        wrong_rsa = self.command("dgst", "-sha256", "-sign", str(self.root / "developer.key"),
                                 "-sigopt", "rsa_padding_mode:pss", str(self.root / "signed.bin"))
        publisher = self.command("dgst", "-sha256", "-sign", str(self.root / "publisher.key"),
                                 str(self.root / "signed.bin"))
        header = field(2, field(1, self.der["developer"]) + field(2, wrong_rsa))
        header += field(3, field(1, self.der["publisher"]) + field(2, publisher))
        header += field(10000, signed)
        package = b"Cr24" + struct.pack("<II", 3, len(header)) + header + archive
        with self.assertRaisesRegex(verify.VerificationError, "signature"):
            self.check(package)

    def test_missing_or_untrusted_publisher_proof_is_rejected(self):
        with self.assertRaisesRegex(verify.VerificationError, "publisher proof"):
            self.check(self.crx(publisher=False))
        with patch.object(verify, "TRUSTED_PUBLISHER_KEY_SHA256", "0" * 64):
            with self.assertRaisesRegex(verify.VerificationError, "publisher proof"):
                self.check(self.crx())

    def test_signed_runtime_substitution_is_rejected(self):
        with self.assertRaisesRegex(verify.VerificationError, "runtime bytes"):
            self.check(self.crx(self.archive(changed={"service_worker.js": b"malicious replacement"})))

    def test_truncated_or_oversized_crx_headers_fail_closed(self):
        packages = [b"Cr24", b"Cr24" + struct.pack("<II", 3, 500) + b"\x00",
                    b"Cr24" + struct.pack("<II", 3, verify.MAX_HEADER_BYTES + 1)]
        for package in packages:
            with self.subTest(length=len(package)), self.assertRaises(verify.VerificationError):
                verify.verify_crx3(package, self.item_id)

    def test_signed_non_zip_or_missing_runtime_is_rejected(self):
        with self.assertRaises(verify.VerificationError):
            self.check(self.crx(b"not a zip"))
        entries = [(name, data) for name, data in self.files.items() if name != "content.js"]
        with self.assertRaisesRegex(verify.VerificationError, "inventory"):
            self.check(self.crx(self.archive(entries=entries)))

    def test_duplicate_traversal_and_extra_runtime_entries_are_rejected(self):
        base = list(self.files.items())
        for addition in [("content.js", b"second copy"), ("../outside.js", b"escape"),
                         ("unexpected.js", b"unexpected code")]:
            with self.subTest(name=addition[0]), self.assertRaises(verify.VerificationError):
                self.check(self.crx(self.archive(entries=base + [addition])))

    def test_undocumented_manifest_or_version_changes_are_rejected(self):
        for manifest in [self.manifest | {"permissions": ["debugger"]},
                         self.manifest | {"update_url": "https://evil.example/update"},
                         self.manifest | {"version": "1.2.4"}]:
            with self.subTest(manifest=manifest), self.assertRaises(verify.VerificationError):
                self.check(self.crx(self.archive(changed={"manifest.json": json.dumps(manifest).encode()})))

    def test_expansion_limit_applies_before_reading_entries(self):
        with patch.object(verify, "MAX_EXPANDED_BYTES", 2):
            with self.assertRaisesRegex(verify.VerificationError, "Expanded ZIP"):
                self.check(self.crx())

    def test_null_filename_and_directory_type_aliases_are_rejected(self):
        entries = [("content.jsXXXXX" if name == "content.js" else name, data)
                   for name, data in self.files.items()]
        nul_archive = make_zip(entries).replace(b"content.jsXXXXX", b"content.js\x00XXXX")
        with self.assertRaisesRegex(verify.VerificationError, "unsafe entry"):
            self.check(self.crx(nul_archive))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in self.files.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = ((stat.S_IFDIR | 0o755) if name == "content.js"
                                      else (stat.S_IFREG | 0o644)) << 16
                archive.writestr(info, data)
        with self.assertRaisesRegex(verify.VerificationError, "nonregular"):
            self.check(self.crx(stream.getvalue()))

    def test_duplicate_manifest_keys_are_rejected(self):
        ambiguous = (b'{"manifest_version":3,"name":"Signed test extension",'
                     b'"version":"9.9.9","version":"1.2.3"}')
        with self.assertRaisesRegex(verify.VerificationError, "manifest"):
            self.check(self.crx(self.archive(changed={"manifest.json": ambiguous})))

    def test_upload_symlink_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "upload.zip"
            target = Path(temporary) / "target.zip"
            target.write_bytes(self.uploaded)
            path.symlink_to(target)
            with patch.object(verify, "download_public_crx") as download:
                with self.assertRaises(verify.VerificationError):
                    verify.verify_published_package(path, "1.2.3", self.item_id)
                download.assert_not_called()


class DownloadBoundaryTests(unittest.TestCase):
    class Response:
        url = verify.UPDATE_URL
        def __init__(self, data=b"", headers=None):
            self.data, self.headers, self.reads = data, headers or {}, 0
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, size):
            self.reads += 1
            value, self.data = self.data[:size], self.data[size:]
            return value

    def test_redirect_to_metadata_http_or_other_google_path_is_rejected(self):
        for url in ("http://clients2.googleusercontent.com/crx/package",
                    "https://169.254.169.254/latest/meta-data/",
                    "https://clients2.google.com/unexpected",
                    "https://user@clients2.googleusercontent.com/crx/package",
                    "https://clients2.googleusercontent.com:8443/crx/package"):
            with self.subTest(url=url), self.assertRaises(verify.VerificationError):
                verify._GoogleRedirects().redirect_request(Request(verify.UPDATE_URL), None, 302, "", {}, url)

    def test_declared_and_streamed_size_limits_are_enforced(self):
        for response in (self.Response(headers={"Content-Length": "9"}), self.Response(b"x" * 9)):
            with self.subTest(headers=response.headers), patch.object(verify, "MAX_PACKAGE_BYTES", 8), \
                    patch.object(verify, "build_opener") as opener:
                opener.return_value.open.return_value = response
                with self.assertRaisesRegex(verify.VerificationError, "download limit"):
                    verify.download_public_crx(verify.ITEM_ID)

    def test_download_deadline_and_final_origin_fail_closed(self):
        response = self.Response(b"package")
        with patch.object(verify, "build_opener") as opener, \
                patch.object(verify.time, "monotonic", side_effect=[0, 21]):
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(verify.VerificationError, "time limit"):
                verify.download_public_crx(verify.ITEM_ID)
            self.assertEqual(response.reads, 0)
        response.url = "https://evil.example/package"
        with patch.object(verify, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(verify.VerificationError, "unexpected"):
                verify.download_public_crx(verify.ITEM_ID)

    def test_invalid_identity_never_starts_network(self):
        with patch.object(verify, "build_opener") as opener:
            with self.assertRaises(verify.VerificationError):
                verify.download_public_crx("../invalid")
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
