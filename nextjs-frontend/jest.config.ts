import type { Config } from "jest";
import nextJest from "next/jest.js";

// Loads next.config and .env files into the test environment.
const createJestConfig = nextJest({ dir: "./" });

const config: Config = {
  clearMocks: true,
  restoreMocks: true,
  coverageProvider: "v8",
  testEnvironment: "jsdom",
  // the longer UI tests take over 5 s on a busy self-hosted runner
  testTimeout: 20000,
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
};

export default createJestConfig(config);
