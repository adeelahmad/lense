import { configure } from "@testing-library/react";

// waitFor and findBy* give up after 1 s by default; a busy self-hosted runner can take longer to render
configure({ asyncUtilTimeout: 10000 });
