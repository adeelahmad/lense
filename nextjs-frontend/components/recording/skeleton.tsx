import { Skeleton } from "@/components/ui/states";

/** Loading state: the page's shape (header, chapters, player over transcript, side panel) in skeletons. */
export function RecordingSkeleton() {
  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col overflow-hidden" aria-busy="true" aria-label="Loading the recording">
      <div className="flex flex-col gap-3 border-b border-border px-4 py-4 md:px-6">
        <Skeleton className="h-3 w-40" />
        <div className="flex items-center gap-4">
          <Skeleton className="h-7 w-[46%] min-w-[200px]" />
          <span className="flex-1" />
          <Skeleton className="hidden h-8 w-[340px] md:block" />
        </div>
        <Skeleton className="h-3 w-[60%]" />
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px] xl:grid-cols-[200px_minmax(0,1fr)_440px]">
        <div className="hidden flex-col gap-4 border-r border-border bg-surface p-4 xl:flex">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="flex flex-col gap-1.5">
              <Skeleton className="h-3" style={{ width: `${55 + ((i * 17) % 35)}%` }} />
              <Skeleton className="h-2.5 w-10" />
            </div>
          ))}
        </div>
        <div className="flex min-w-0 flex-col">
          <div className="flex flex-col gap-3 border-b border-border px-6 py-4">
            <div className="flex items-center gap-3">
              <Skeleton className="size-[38px] !rounded-full" />
              <Skeleton className="h-3.5 w-28" />
            </div>
            <Skeleton className="h-16 w-full" />
          </div>
          <div className="flex flex-col gap-6 px-6 py-5">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="grid grid-cols-[56px_1fr] gap-4">
                <Skeleton className="h-3 w-10 justify-self-end" />
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="h-4" style={{ width: `${70 + ((i * 13) % 25)}%` }} />
                  <Skeleton className="h-4" style={{ width: `${40 + ((i * 29) % 40)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="hidden flex-col gap-4 border-l border-border p-5 lg:flex">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-3 w-3/4" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-3 w-4/5" />
        </div>
      </div>
    </div>
  );
}
