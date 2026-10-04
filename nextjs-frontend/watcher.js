/* eslint-disable @typescript-eslint/no-require-imports */
const chokidar = require("chokidar");
const { exec } = require("child_process");
const { config } = require("dotenv");

config({ path: ".env.local" });

const openapiFile = process.env.OPENAPI_OUTPUT_FILE || "openapi.json";
// One generation at a time: each one empties app/openapi-client before writing it, so two at once (the schema written
// twice in quick succession) delete each other's files and leave the client importing modules that are gone. Changes
// during a run queue one more run.
let running = false;
let pending = false;

function generate() {
  if (running) {
    pending = true;
    return;
  }
  running = true;
  exec("pnpm run generate-client", (error, stdout, stderr) => {
    if (error) {
      console.error(`Error: ${error.message}`);
    } else if (stderr) {
      console.error(`stderr: ${stderr}`);
    } else {
      console.log(`stdout: ${stdout}`);
    }
    running = false;
    if (pending) {
      pending = false;
      generate();
    }
  });
}

// Watch the specific file for changes
chokidar.watch(openapiFile).on("change", (path) => {
  console.log(`File ${path} has been modified. Running generate-client...`);
  generate();
});
