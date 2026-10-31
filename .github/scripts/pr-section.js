// Writes a workflow's results into its own section at the bottom of the PR
// description, replacing that section from the last run. A new PR comment
// emails everyone on the PR, but editing the description doesn't.
//
// Used from actions/github-script after actions/checkout:
//   await require("./.github/scripts/pr-section.js")({ github, context, core }, "fbc2-docker-build", body);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const normalize = (text) => (text || "").replace(/\r\n/g, "\n");

module.exports = async ({ github, context, core }, name, content, waitMs = 3000) => {
  const start = `<!-- ${name}:start -->`;
  const end = `<!-- ${name}:end -->`;
  const section = `${start}\n${content}\n${end}`;
  const request = { ...context.repo, pull_number: context.payload.pull_request.number };

  // Each workflow reads the description, changes its own section and writes
  // it back, so two finishing at once can drop each other's section. Check
  // ours is still there after a moment, and write it again if it isn't.
  for (let attempt = 1; attempt <= 5; attempt++) {
    const body = normalize((await github.rest.pulls.get(request)).data.body);
    const from = body.indexOf(start);
    const to = body.indexOf(end, from);
    const updated =
      from !== -1 && to !== -1
        ? body.slice(0, from) + section + body.slice(to + end.length)
        : [body.trimEnd(), section].filter(Boolean).join("\n\n");
    if (updated !== body) {
      await github.rest.pulls.update({ ...request, body: updated });
    }
    await sleep(waitMs + Math.random() * waitMs);
    if (normalize((await github.rest.pulls.get(request)).data.body).includes(section)) {
      return;
    }
    core.info(`The ${name} section was overwritten; writing it again (attempt ${attempt}).`);
  }
  core.warning(`Couldn't write the ${name} section into the PR description.`);
};
