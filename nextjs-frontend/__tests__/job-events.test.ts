import { QueryClient } from "@tanstack/react-query";

import type { JobList } from "@/app/openapi-client/types.gen";
import { patchJobLists } from "@/components/activity/job-events";
import type { JobRecord } from "@/components/activity/job-model";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

const list = (jobs: Partial<JobRecord>[], counts: Record<string, number>): JobList =>
  ({ jobs, counts, running: false, log: "" }) as unknown as JobList;

describe("live changes to cached job lists", () => {
  it("keeps queue priority before limiting live rows", () => {
    const qc = new QueryClient();
    const key = ["jobs", { status: "queued", order: "queue", limit: 2 }];
    const first = { id: 1, status: "queued", priority: 2, created_at: "2026-01-01" } as JobRecord;
    const second = { id: 2, status: "queued", priority: 1, created_at: "2026-01-02" } as JobRecord;
    qc.setQueryData(key, list([first, second], { queued: 2 }));
    const ids = () => qc.getQueryData<JobList>(key)?.jobs.map((j) => j.id);

    // An arrival outside this page must not displace a run workers will take sooner.
    patchJobLists(qc, { id: 3, status: "queued", priority: 0, created_at: "2026-01-03" } as JobRecord);
    expect(ids()).toEqual([1, 2]);
    // Reprioritisation moves an existing row, and can bring an off-page run to the front.
    patchJobLists(qc, { ...second, priority: 3 });
    expect(ids()).toEqual([2, 1]);
    patchJobLists(qc, { id: 3, status: "queued", priority: 4 } as JobRecord);
    expect(ids()).toEqual([3, 2]);
    patchJobLists(qc, { ...second, status: "running" });
    expect(ids()).toEqual([3]);
  });

  it("keeps oldest queued runs first when priorities tie", () => {
    const qc = new QueryClient();
    const key = ["jobs", { status: "queued", order: "queue", limit: 2 }];
    qc.setQueryData(key, list([{ id: 1, status: "queued", created_at: "2026-01-01" }], { queued: 1 }));
    patchJobLists(qc, { id: 2, status: "queued", created_at: "2026-01-02" } as JobRecord);
    expect(qc.getQueryData<JobList>(key)?.jobs.map((j) => j.id)).toEqual([1, 2]);
  });

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
