import { QueryClient } from "@tanstack/react-query";

import type { JobList } from "@/app/openapi-client/types.gen";
import { patchJobLists } from "@/components/activity/job-events";
import type { JobRecord } from "@/components/activity/job-model";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

const list = (jobs: Partial<JobRecord>[], counts: Record<string, number>): JobList =>
  ({ jobs, counts, running: false, log: "" }) as unknown as JobList;

describe("live changes to cached job lists", () => {
  it("keeps a namespace's or a batch's list to its own jobs", () => {
    const qc = new QueryClient();
    const all = ["jobs", { limit: 200 }];
    const pods = ["jobs", { limit: 200, namespace: "pods" }];
    const batch = ["jobs", { limit: 2000, batch: 7 }];
    for (const key of [all, pods, batch]) qc.setQueryData(key, list([], {}));
    const ids = { pods: 1, calls: 2 };

    patchJobLists(qc, { id: 10, status: "queued", space: 2, batch: 7 } as JobRecord, ids);
    patchJobLists(qc, { id: 11, status: "running", space: 1 } as JobRecord, ids);
    const idsOf = (key: unknown[]) => (qc.getQueryData<JobList>(key)?.jobs ?? []).map((j) => j.id);
    expect(idsOf(all)).toEqual([11, 10]);
    expect(idsOf(pods)).toEqual([11]);
    expect(qc.getQueryData<JobList>(pods)?.counts).toEqual({ running: 1 });
    expect(idsOf(batch)).toEqual([10]);

    // without the namespace ids, a namespace's list waits for its refetch rather than guess
    patchJobLists(qc, { id: 12, status: "queued", space: 1 } as JobRecord);
    expect(idsOf(pods)).toEqual([11]);
    expect(idsOf(all)).toEqual([12, 11, 10]);
  });
});
