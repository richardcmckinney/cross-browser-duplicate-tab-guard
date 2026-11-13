"""Pinned data-only Web Store packaging policy.

Vendored from reviewed companion packaging code at b7efc76582fbeac8833ae0c47659514b6a345655.
Candidate Python and JavaScript are treated only as data and never imported.
Policy changes require deliberately updating the installed publisher bundle.
"""
from __future__ import annotations
import io
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
import zipfile

class PackageError(RuntimeError):
    pass
GuardError = PackageError

POLICY_VERSION = 1


APP_SLUG = "cross-browser-duplicate-tab-guard"

WEBSTORE_DESCRIPTION = (
    "Stops exact-URL duplicate tabs across Chrome, Brave and Chromium, "
    "with editable exceptions. Needs the free local companion app."
)

WEBSTORE_PERMISSIONS = frozenset(
    {"alarms", "nativeMessaging", "storage", "tabs", "webNavigation"}
)
# Project policy: optional capabilities require a separate release-policy review.
WEBSTORE_OPTIONAL_PERMISSIONS = frozenset()

WEBSTORE_FILE_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".png"})

WEBSTORE_TEXT_SUFFIXES = frozenset({".css", ".html", ".js", ".json"})

WEBSTORE_IGNORED_NAMES = frozenset(
    {".DS_Store", "Thumbs.db", ".gitignore", "__pycache__"}
)

WEBSTORE_SOURCE_ONLY_FILES = frozenset({"icons/icon.svg"})

WEBSTORE_EPOCH = 1767225600

WEBSTORE_FALLBACK_LINE = re.compile(
    r'^const FALLBACK_WS_URL = "[^"\n]*";$',
    re.MULTILINE,
)

WEBSTORE_GUARD_ANCHOR = "async function connectWebSocket() {\n"

WEBSTORE_GUARD = (
    "  // Chrome Web Store build: per-computer loopback credentials are never "
    "shipped in a published package.\n"
    "  if (!FALLBACK_WS_URL) {\n"
    "    if (!lastNativeError) {\n"
    "      lastNativeError = \"The local companion app is not installed or not running.\";\n"
    "    }\n"
    "    scheduleReconnect();\n"
    "    return;\n"
    "  }\n"
)

WEBSTORE_FORBIDDEN_TEXT = (
    "__AUTH_TOKEN__",
    "__WS_PORT__",
    "ws://",
    "127.0.0.1",
    "localhost",
    "/Users/",
    "/home/",
)

def _webstore_error(message: str) -> GuardError:
    """Construct one consistent validation error for package callers."""
    return GuardError(f"Chrome Web Store package rejected: {message}")

def _safe_webstore_path(value: str) -> bool:
    """Return whether a manifest path is a relative, normalized package path."""
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    # Chrome resolves percent-encoded separators before loading package paths.
    # Reject encoded dot segments and separators before normalizing the archive.
    if re.search(r"%(?:2e|2f|5c)", value, flags=re.IGNORECASE):
        return False
    parts = value.split("/")
    return not value.startswith("/") and all(part not in {"", ".", ".."} for part in parts)

def _manifest_resource_paths(manifest: dict[str, Any]) -> list[tuple[str, bool]]:
    """Collect manifest paths and whether wildcard matching is permitted."""
    paths: list[tuple[str, bool]] = []

    def add(value: Any, wildcard_allowed: bool = False) -> None:
        if isinstance(value, str):
            paths.append((value, wildcard_allowed))

    icons = manifest.get("icons", {})
    if isinstance(icons, dict):
        for value in icons.values():
            add(value)
    action = manifest.get("action", {})
    if isinstance(action, dict):
        add(action.get("default_popup"))
        default_icon = action.get("default_icon", {})
        if isinstance(default_icon, dict):
            for value in default_icon.values():
                add(value)
    add(manifest.get("options_page"))
    options_ui = manifest.get("options_ui", {})
    if isinstance(options_ui, dict):
        add(options_ui.get("page"))
    add(manifest.get("devtools_page"))
    overrides = manifest.get("chrome_url_overrides", {})
    if isinstance(overrides, dict):
        for value in overrides.values():
            add(value)
    side_panel = manifest.get("side_panel", {})
    if isinstance(side_panel, dict):
        add(side_panel.get("default_path"))
    offscreen = manifest.get("offscreen", {})
    if isinstance(offscreen, dict):
        documents = offscreen.get("documents", [])
        if isinstance(documents, list):
            for value in documents:
                add(value)
    sandbox = manifest.get("sandbox", {})
    if isinstance(sandbox, dict):
        pages = sandbox.get("pages", [])
        if isinstance(pages, list):
            for value in pages:
                add(value)
    background = manifest.get("background", {})
    if isinstance(background, dict):
        add(background.get("service_worker"))
    content_scripts = manifest.get("content_scripts", [])
    if isinstance(content_scripts, list):
        for content_script in content_scripts:
            if not isinstance(content_script, dict):
                continue
            for key in ("js", "css"):
                values = content_script.get(key, [])
                if isinstance(values, list):
                    for value in values:
                        add(value)
    resource_groups = manifest.get("web_accessible_resources", [])
    if isinstance(resource_groups, list):
        for resource_group in resource_groups:
            if not isinstance(resource_group, dict):
                continue
            values = resource_group.get("resources", [])
            if isinstance(values, list):
                for value in values:
                    add(value, True)
    dnr = manifest.get("declarative_net_request", {})
    if isinstance(dnr, dict):
        rule_resources = dnr.get("rule_resources", [])
        if isinstance(rule_resources, list):
            for rule_resource in rule_resources:
                if isinstance(rule_resource, dict):
                    add(rule_resource.get("path"))
    nacl_modules = manifest.get("nacl_modules", [])
    if isinstance(nacl_modules, list):
        for module in nacl_modules:
            if isinstance(module, dict):
                add(module.get("pnacl") or module.get("nacl"))
    return paths

def _manifest_optional_shape_problems(manifest: dict[str, Any]) -> list[str]:
    """Reject malformed optional MV3 objects before they reach the store."""
    problems: list[str] = []

    def require_dict(field: str) -> dict[str, Any] | None:
        if field not in manifest:
            return {}
        value = manifest[field]
        if not isinstance(value, dict):
            problems.append(f"manifest {field} must be an object")
            return None
        return value

    action = require_dict("action")
    if action is not None and "default_popup" in action and not isinstance(
        action["default_popup"], str
    ):
        problems.append("manifest action.default_popup must be a path")
    options_ui = require_dict("options_ui")
    if options_ui is not None and "page" in options_ui and not isinstance(
        options_ui["page"], str
    ):
        problems.append("manifest options_ui.page must be a path")
    side_panel = require_dict("side_panel")
    if side_panel is not None and "default_path" in side_panel and not isinstance(
        side_panel["default_path"], str
    ):
        problems.append("manifest side_panel.default_path must be a path")
    overrides = require_dict("chrome_url_overrides")
    if overrides is not None and any(
        not isinstance(value, str) for value in overrides.values()
    ):
        problems.append("manifest chrome_url_overrides values must be paths")
    offscreen = require_dict("offscreen")
    if offscreen is not None and "documents" in offscreen:
        documents = offscreen["documents"]
        if not isinstance(documents, list) or not documents or any(
            not isinstance(value, str) for value in documents
        ):
            problems.append("manifest offscreen.documents must be a non-empty list of paths")
    sandbox = require_dict("sandbox")
    if sandbox is not None and "pages" in sandbox:
        pages = sandbox["pages"]
        if not isinstance(pages, list) or not pages or any(
            not isinstance(value, str) for value in pages
        ):
            problems.append("manifest sandbox.pages must be a non-empty list of paths")
    dnr = require_dict("declarative_net_request")
    if dnr is not None and "rule_resources" in dnr:
        rule_resources = dnr["rule_resources"]
        if not isinstance(rule_resources, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("path"), str)
            for item in rule_resources
        ):
            problems.append(
                "manifest declarative_net_request.rule_resources must contain path objects"
            )
    if "nacl_modules" in manifest:
        modules = manifest["nacl_modules"]
        if not isinstance(modules, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("pnacl") or item.get("nacl"), str)
            for item in modules
        ):
            problems.append("manifest nacl_modules must contain path objects")
    return problems

def webstore_package_problems(
    manifest: dict[str, Any],
    files: dict[str, bytes],
    secrets_to_exclude: Iterable[str] = (),
) -> list[str]:
    """Return every reason a derived store build must not be uploaded."""
    problems: list[str] = []
    if manifest.get("manifest_version") != 3:
        problems.append("manifest_version must be 3")
    if "key" in manifest:
        problems.append("manifest.json still carries the developer key")
    if "update_url" in manifest:
        problems.append("manifest.json still carries update_url")
    version = manifest.get("version")
    version_parts = version.split(".") if isinstance(version, str) else []
    if (
        not 1 <= len(version_parts) <= 4
        or any(
            re.fullmatch(r"[0-9]{1,5}", part) is None or int(part) > 65535
            for part in version_parts
        )
    ):
        problems.append("manifest version must contain 1 to 4 integers from 0 through 65535")
    name = manifest.get("name", "")
    description = manifest.get("description", "")
    if not isinstance(name, str) or not 0 < len(name) <= 75:
        problems.append(f"manifest name must be 1 to 75 characters (it is {len(name) if isinstance(name, str) else 'not text'})")
    if not isinstance(description, str) or not 0 < len(description) <= 132:
        problems.append(f"manifest description must be 1 to 132 characters (it is {len(description) if isinstance(description, str) else 'not text'})")
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list):
        problems.append("manifest permissions must be a list")
    else:
        unexpected = [
            item for item in permissions
            if not isinstance(item, str) or item not in WEBSTORE_PERMISSIONS
        ]
        if unexpected:
            problems.append(f"unexpected permissions: {sorted(map(str, unexpected))}")
    optional = manifest.get("optional_permissions", [])
    if not isinstance(optional, list):
        problems.append("manifest optional_permissions must be a list")
    elif any(not isinstance(item, str) or item not in WEBSTORE_OPTIONAL_PERMISSIONS for item in optional):
        problems.append("manifest optional_permissions exceeds the pinned capability policy")
    problems.extend(_manifest_optional_shape_problems(manifest))
    background = manifest.get("background", {})
    if not isinstance(background, dict) or not isinstance(
        background.get("service_worker"), str
    ):
        problems.append("manifest background.service_worker must be a path")
    content_scripts = manifest.get("content_scripts", [])
    if not isinstance(content_scripts, list):
        problems.append("manifest content_scripts must be a list")
        content_scripts = []
    for index, content_script in enumerate(content_scripts):
        if not isinstance(content_script, dict):
            problems.append(f"manifest content_scripts[{index}] must be an object")
            continue
        for key in ("js", "css"):
            values = content_script.get(key, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                problems.append(f"manifest content_scripts[{index}].{key} must be a list of paths")
    resource_groups = manifest.get("web_accessible_resources", [])
    if not isinstance(resource_groups, list):
        problems.append("manifest web_accessible_resources must be a list")
        resource_groups = []
    for index, resource_group in enumerate(resource_groups):
        if not isinstance(resource_group, dict):
            problems.append(f"manifest web_accessible_resources[{index}] must be an object")
            continue
        if "resources" not in resource_group:
            problems.append(f"manifest web_accessible_resources[{index}].resources is required")
            resources = []
        else:
            resources = resource_group.get("resources")
        if not isinstance(resources, list) or any(
            not isinstance(value, str) for value in resources
        ):
            problems.append(f"manifest web_accessible_resources[{index}].resources must be a list of paths")
        if "matches" not in resource_group and "extension_ids" not in resource_group:
            problems.append(f"manifest web_accessible_resources[{index}] needs matches or extension_ids")
        matches = resource_group.get("matches", [])
        if "matches" in resource_group and (
            not isinstance(matches, list) or not matches or any(
                not isinstance(value, str) for value in matches
            )
        ):
            problems.append(f"manifest web_accessible_resources[{index}].matches must be a non-empty list")
        extension_ids = resource_group.get("extension_ids", [])
        if "extension_ids" in resource_group and (
            not isinstance(extension_ids, list)
            or not extension_ids
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[a-p]{32}", value) is None
                for value in extension_ids
            )
        ):
            problems.append(
                f"manifest web_accessible_resources[{index}].extension_ids must be non-empty extension IDs"
            )
    icons = manifest.get("icons", {})
    if not isinstance(icons, dict):
        problems.append("manifest icons must be an object")
        icons = {}
    for size in ("16", "32", "48", "128"):
        icon = icons.get(size)
        if not isinstance(icon, str) or icon not in files:
            problems.append(f"icon {size} is not declared or not packaged")
    csp = manifest.get("content_security_policy")
    if isinstance(csp, dict):
        csp_value = csp.get("extension_pages", "")
    else:
        csp_value = csp or ""
    if any(marker in str(csp_value) for marker in ("connect-src", "ws://", "127.0.0.1", "localhost")):
        problems.append("manifest CSP still permits loopback connections")
    for resource, wildcard_allowed in _manifest_resource_paths(manifest):
        has_wildcard = any(char in resource for char in "*?[")
        if not _safe_webstore_path(resource) or (has_wildcard and not wildcard_allowed):
            problems.append(f"manifest resource path is unsafe: {resource!r}")
        elif not has_wildcard and resource not in files:
            problems.append(f"manifest resource is missing: {resource}")
    secrets_list = [str(item) for item in secrets_to_exclude if item]
    secret_bytes = [(secret, secret.encode("utf-8")) for secret in secrets_list]
    for rel, data in sorted(files.items()):
        findings: set[str] = set()
        for _, encoded_secret in secret_bytes:
            if encoded_secret in data:
                findings.add(f"{rel}: contains this computer's loopback token")
        for marker in WEBSTORE_FORBIDDEN_TEXT:
            if marker.encode("utf-8") in data:
                findings.add(f"{rel}: contains computer-specific text {marker!r}")
        problems.extend(sorted(findings))
        if Path(rel).suffix.lower() in WEBSTORE_TEXT_SUFFIXES:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{rel}: text asset is not valid UTF-8")
    worker = files.get("service_worker.js", b"")
    try:
        worker_text = worker.decode("utf-8")
    except UnicodeDecodeError:
        worker_text = ""
    if 'const FALLBACK_WS_URL = "";' not in worker_text or WEBSTORE_GUARD not in worker_text:
        problems.append("service_worker.js: loopback fallback was not disabled")
    return problems

def package_bytes(source_files: dict[str, bytes]) -> tuple[str, bytes]:
    """Apply the pinned native-only policy and return deterministic archive bytes."""
    files = dict(source_files)
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate manifest JSON key")
            result[key] = value
        return result
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _webstore_error(f"manifest.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise _webstore_error("manifest.json must contain an object")
    manifest.pop("key", None)
    manifest.pop("update_url", None)
    manifest["description"] = WEBSTORE_DESCRIPTION
    csp = manifest.get("content_security_policy")
    if isinstance(csp, dict):
        csp["extension_pages"] = "script-src 'self'; object-src 'self';"
    elif isinstance(csp, str):
        manifest["content_security_policy"] = "script-src 'self'; object-src 'self';"
    worker_bytes = files.get("service_worker.js")
    if worker_bytes is None:
        raise _webstore_error("service_worker.js is missing")
    try:
        worker = worker_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _webstore_error(f"service_worker.js is not valid UTF-8: {exc}") from exc
    worker, replaced = WEBSTORE_FALLBACK_LINE.subn('const FALLBACK_WS_URL = "";', worker)
    if replaced != 1:
        raise _webstore_error(
            f"service_worker.js: expected one FALLBACK_WS_URL declaration, found {replaced}"
        )
    if worker.count(WEBSTORE_GUARD_ANCHOR) != 1:
        raise _webstore_error("service_worker.js: connectWebSocket anchor not found exactly once")
    worker = worker.replace(WEBSTORE_GUARD_ANCHOR, WEBSTORE_GUARD_ANCHOR + WEBSTORE_GUARD, 1)
    files["service_worker.js"] = worker.encode("utf-8")
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    problems = webstore_package_problems(manifest, files)
    if problems:
        raise _webstore_error("\n  - " + "\n  - ".join(problems))
    buffer = io.BytesIO()
    stamp = time.gmtime(WEBSTORE_EPOCH)[:6]
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[rel])
    return manifest["version"], buffer.getvalue()
