"use strict";

(() => {
  const STORAGE_KEY = "duplicateTabGuardExceptionSettingsV1";
  const SCHEMA_VERSION = 1;
  const MAX_RULES = 500;
  const MAX_PATTERN_LENGTH = 4096;
  const MAX_NOTE_LENGTH = 160;

  const TYPES = Object.freeze({
    exact: "Exact URL",
    host: "Hostname",
    domain: "Domain + subdomains",
    wildcard: "Wildcard URL",
    regex: "Regular expression",
  });
  const BROWSER_SCOPES = new Set(["all", "brave", "chrome", "chromium"]);
  const CONTEXT_SCOPES = new Set(["all", "regular", "incognito"]);

  function makeId() {
    try {
      return crypto.randomUUID();
    } catch (_) {
      return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
    }
  }

  function defaultSettings() {
    return {
      schema: SCHEMA_VERSION,
      enabled: true,
      pausedUntil: 0,
      rules: [],
    };
  }

  function normalizeType(value) {
    const type = String(value || "exact").toLowerCase();
    return Object.prototype.hasOwnProperty.call(TYPES, type) ? type : "exact";
  }

  function normalizeBrowserScope(value) {
    const scope = String(value || "all").toLowerCase();
    return BROWSER_SCOPES.has(scope) ? scope : "all";
  }

  function normalizeContextScope(value) {
    const scope = String(value || "all").toLowerCase();
    return CONTEXT_SCOPES.has(scope) ? scope : "all";
  }

  function normalizeRule(value = {}) {
    const raw = value && typeof value === "object" ? value : {};
    return {
      id: String(raw.id || makeId()).slice(0, 128),
      enabled: raw.enabled !== false,
      type: normalizeType(raw.type),
      pattern: String(raw.pattern || "").trim().slice(0, MAX_PATTERN_LENGTH),
      browser: normalizeBrowserScope(raw.browser),
      context: normalizeContextScope(raw.context),
      note: String(raw.note || "").replace(/\s+/g, " ").trim().slice(0, MAX_NOTE_LENGTH),
      createdAt: Number.isFinite(Number(raw.createdAt)) ? Number(raw.createdAt) : Date.now(),
    };
  }

  function normalizeSettings(value) {
    const raw = value && typeof value === "object" ? value : {};
    const rules = Array.isArray(raw.rules)
      ? raw.rules.slice(0, MAX_RULES).map(normalizeRule).filter((rule) => rule.pattern)
      : [];
    return {
      schema: SCHEMA_VERSION,
      enabled: raw.enabled !== false,
      pausedUntil: Math.max(0, Number(raw.pausedUntil) || 0),
      rules,
    };
  }

  function parseUrl(value) {
    try {
      return new URL(String(value || ""));
    } catch (_) {
      return null;
    }
  }

  function hostFromPattern(pattern, includePort) {
    let value = String(pattern || "").trim().toLowerCase();
    if (!value) return "";
    value = value.replace(/^\*\./, "");
    const parsed = parseUrl(value.includes("://") ? value : `https://${value}`);
    if (parsed) return (includePort ? parsed.host : parsed.hostname).toLowerCase();
    value = value.split(/[/?#]/, 1)[0];
    return includePort ? value : value.replace(/:\d+$/, "");
  }

  function escapeRegex(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function wildcardRegex(pattern) {
    const source = String(pattern || "")
      .split("*").map((part) => part.split("?").map(escapeRegex).join(".")).join(".*");
    return new RegExp(`^${source}$`);
  }

    const regexCache = new Map();
  function getCachedRegex(key, factory) {
    let re = regexCache.get(key);
    if (!re) {
      re = factory();
      if (regexCache.size > 2000) regexCache.clear();
      regexCache.set(key, re);
    }
    return re;
  }

  function regexFromPattern(pattern) {
    const value = String(pattern || "");
    if (value.startsWith("/")) {
      const lastSlash = value.lastIndexOf("/");
      if (lastSlash > 0) {
        const body = value.slice(1, lastSlash);
        const flags = value.slice(lastSlash + 1).replace(/[gy]/g, "");
        if (!/^[dimsuv]*$/.test(flags) || new Set(flags).size !== flags.length) {
          throw new Error("Unsupported or duplicate regular-expression flags.");
        }
        return new RegExp(body, flags);
      }
    }
    return new RegExp(value);
  }

  function validateRule(value) {
    const rule = normalizeRule(value);
    if (!rule.pattern) return { ok: false, error: "Enter a URL, hostname, wildcard, or regular expression." };
    if (rule.pattern.length > MAX_PATTERN_LENGTH) return { ok: false, error: `Patterns are limited to ${MAX_PATTERN_LENGTH} characters.` };

    if (rule.type === "host" && !hostFromPattern(rule.pattern, true)) {
      return { ok: false, error: "Enter a valid hostname, optionally including a port." };
    }
    if (rule.type === "domain" && !hostFromPattern(rule.pattern, false)) {
      return { ok: false, error: "Enter a valid domain name." };
    }
    try {
      if (rule.type === "wildcard") wildcardRegex(rule.pattern);
      if (rule.type === "regex") regexFromPattern(rule.pattern);
    } catch (error) {
      return { ok: false, error: error && error.message ? error.message : "The pattern is invalid." };
    }
    return { ok: true, rule };
  }

  function ruleMatches(value, context = {}) {
    const rule = normalizeRule(value);
    if (!rule.enabled || !rule.pattern) return false;

    const browser = String(context.browser || "").toLowerCase();
    if (rule.browser !== "all" && rule.browser !== browser) return false;

    const incognito = Boolean(context.incognito);
    if (rule.context === "regular" && incognito) return false;
    if (rule.context === "incognito" && !incognito) return false;

    const url = String(context.url || "");
    if (!url) return false;

    if (rule.type === "exact") return url === rule.pattern;

    const parsed = parseUrl(url);
    if (rule.type === "host") {
      if (!parsed) return false;
      const target = hostFromPattern(rule.pattern, true);
      const actual = target.includes(":") ? parsed.host.toLowerCase() : parsed.hostname.toLowerCase();
      return actual === target;
    }
    if (rule.type === "domain") {
      if (!parsed) return false;
      const target = hostFromPattern(rule.pattern, false);
      const actual = parsed.hostname.toLowerCase();
      return actual === target || actual.endsWith(`.${target}`);
    }
    try {
      if (rule.type === "wildcard") {
        return getCachedRegex("w:" + rule.pattern, () => wildcardRegex(rule.pattern)).test(url);
      }
      if (rule.type === "regex") {
        return getCachedRegex("r:" + rule.pattern, () => regexFromPattern(rule.pattern)).test(url);
      }
    } catch (_) {
      return false;
    }
    return false;
  }

  function evaluate(value, context = {}, at = Date.now()) {
    const settings = normalizeSettings(value);
    if (!settings.enabled) return { excluded: true, reason: "guard_disabled", rule: null };
    if (settings.pausedUntil > at) return { excluded: true, reason: "guard_paused", rule: null };

    for (const rule of settings.rules) {
      if (ruleMatches(rule, context)) return { excluded: true, reason: "exception_rule", rule };
    }
    return { excluded: false, reason: "", rule: null };
  }

  function suggestPattern(url, type) {
    const normalizedType = normalizeType(type);
    const text = String(url || "");
    const parsed = parseUrl(text);
    if (normalizedType === "exact") return text;
    if (normalizedType === "host") return parsed ? parsed.host : "";
    if (normalizedType === "domain") return parsed ? parsed.hostname : "";
    if (normalizedType === "wildcard") {
      if (!parsed) return text;
      if (parsed.protocol === "file:") return `${parsed.protocol}//${parsed.pathname.replace(/[^/]*$/, "*")}`;
      return `${parsed.protocol}//${parsed.host}/*`;
    }
    return text ? `^${escapeRegex(text)}$` : "";
  }

  globalThis.CBDTGExceptions = Object.freeze({
    STORAGE_KEY,
    SCHEMA_VERSION,
    MAX_RULES,
    TYPES,
    defaultSettings,
    normalizeRule,
    normalizeSettings,
    validateRule,
    ruleMatches,
    evaluate,
    suggestPattern,
  });
})();
