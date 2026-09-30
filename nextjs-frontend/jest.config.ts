import type { Config } from "jest";
import nextJest from "next/jest.js";

// Loads next.config and .env files into the test environment.
const createJestConfig = nextJest({ dir: "./" });

const config: Config = {
  clearMocks: true,
  restoreMocks: true,
  coverageProvider: "v8",
  testEnvironment: "jsdom",
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
};

export default createJestConfig(config);
