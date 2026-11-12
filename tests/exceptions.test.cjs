"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "extension", "exceptions.js"), "utf8");

function loadRules(overrides = {}) {
  const context = vm.createContext({ URL, ...overrides });
  vm.runInContext(source, context);
  return context.CBDTGExceptions;
}

test("normalization preserves oversized patterns for validation", () => {
  const rules = loadRules();
  for (const type of ["exact", "host", "domain", "wildcard", "regex"]) {
    const pattern = "a".repeat(4097);
    const normalized = rules.normalizeSettings({ rules: [{ type, pattern }] }).rules[0];
    assert.equal(normalized.pattern, pattern, type);
    const validation = rules.validateRule(normalized);
    assert.equal(validation.ok, false, type);
    assert.match(validation.error, /4096/);
  }
});

test("oversized rules are inert and never compiled as regular expressions", () => {
  let compiled = 0;
  const rules = loadRules({ RegExp: function (...args) { compiled += 1; return new RegExp(...args); } });
  for (const type of ["exact", "host", "domain", "wildcard", "regex"]) {
    const pattern = "a".repeat(4097);
    const candidate = { type, pattern };
    assert.equal(rules.ruleMatches(candidate, { url: pattern }), false, type);
    assert.equal(rules.ruleMatches(candidate, { url: pattern.slice(0, 4096) }), false, type);
    assert.equal(rules.evaluate({ rules: [candidate] }, { url: pattern }).excluded, false, type);
  }
  assert.equal(compiled, 0);
});

test("exact rules at the length boundary retain their full matching behavior", () => {
  const rules = loadRules();
  const pattern = "https://example.test/".padEnd(4096, "a");
  const validation = rules.validateRule({ type: "exact", pattern });
  assert.equal(validation.ok, true);
  assert.equal(validation.rule.pattern, pattern);
  assert.equal(rules.ruleMatches(validation.rule, { url: pattern }), true);
  assert.equal(rules.ruleMatches(validation.rule, { url: pattern + "b" }), false);
});

test("valid normalized rules preserve existing matching and scope behavior", () => {
  const rules = loadRules();
  const cases = [
    ["exact", "https://example.test/page"],
    ["host", "example.test"],
    ["domain", "example.test"],
    ["wildcard", "https://example.test/*"],
    ["regex", "/^https:\\/\\/example\\.test\\//i"],
  ];
  for (const [type, pattern] of cases) {
    const validation = rules.validateRule({ type, pattern: ` ${pattern} `, browser: "chrome", context: "regular" });
    assert.equal(validation.ok, true, type);
    assert.equal(validation.rule.pattern, pattern, type);
    assert.equal(rules.ruleMatches(validation.rule, { url: "https://example.test/page", browser: "chrome", incognito: false }), true, type);
    assert.equal(rules.ruleMatches(validation.rule, { url: "https://example.test/page", browser: "brave", incognito: false }), false, type);
    assert.equal(rules.ruleMatches(validation.rule, { url: "https://example.test/page", browser: "chrome", incognito: true }), false, type);
  }
});
