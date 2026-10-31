// Summarises pytest's JUnit XML as Markdown for the Tests section of the PR
// description (see pr-section.js).

const fs = require("fs");

const MAX_LISTED = 20;

const unescape = (text) =>
  text
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");

const attr = (attrs, name) => {
  const match = new RegExp(`\\b${name}="([^"]*)"`).exec(attrs);
  return match ? unescape(match[1]) : "";
};

module.exports = (path, footer) => {
  let xml;
  try {
    xml = fs.readFileSync(path, "utf8");
  } catch (e) {
    return ["### ❌ Tests didn't run", "", footer].join("\n");
  }

  const suite = /<testsuite\b([^>]*)>/.exec(xml);
  const attrs = suite ? suite[1] : "";
  const count = (name) => Number(attr(attrs, name)) || 0;
  const tests = count("tests");
  const failures = count("failures");
  const errors = count("errors");
  const skipped = count("skipped");
  const passed = tests - failures - errors - skipped;
  const seconds = Math.round(Number(attr(attrs, "time")) || 0);
  const ok = suite && failures === 0 && errors === 0;

  const failed = [];
  const testcase = /<testcase\b([^>]*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g;
  let match;
  while ((match = testcase.exec(xml))) {
    if (match[2] && /<(failure|error)\b/.test(match[2])) {
      failed.push(`${attr(match[1], "classname")}::${attr(match[1], "name")}`);
    }
  }

  const lines = [
    `### ${ok ? "✅" : "❌"} Tests ${ok ? "passed" : "failed"}`,
    "",
    "| Tests | Passed | Failed | Errors | Skipped | Time |",
    "| --- | --- | --- | --- | --- | --- |",
    `| ${tests} | ${passed} | ${failures} | ${errors} | ${skipped} | ${seconds}s |`,
  ];
  if (failed.length) {
    lines.push("", "Failed:", "");
    failed.slice(0, MAX_LISTED).forEach((name) => lines.push(`- \`${name}\``));
    if (failed.length > MAX_LISTED) {
      lines.push(`- and ${failed.length - MAX_LISTED} more`);
    }
  }
  lines.push("", footer);
  return lines.join("\n");
};
