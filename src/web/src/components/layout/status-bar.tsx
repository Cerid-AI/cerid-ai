import { useQuery } from "@tanstack/react-query"
import { fetchHealth } from "@/lib/api"
import { cn } from "@/lib/utils"

export function StatusBar() {
  const { data: health, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: 1,
  })

  const status = isLoading ? "loading" : isError ? "error" : health?.status ?? "unknown"

  return (
    <div className="flex h-8 items-center gap-4 border-t bg-muted/40 px-4 text-xs text-muted-foreground">
      <div className="flex items-center gap-1.5">
        <div
          className={cn(
            "h-2 w-2 rounded-full",
            status === "healthy" && "bg-green-500",
            status === "degraded" && "bg-yellow-500",
            status === "loading" && "bg-muted-foreground/50",
            (status === "error" || status === "unknown") && "bg-red-500"
          )}
        />
        <span>
          {status === "healthy" && "All systems operational"}
          {status === "degraded" && "Some services degraded"}
          {status === "error" && "Connection error"}
          {status === "loading" && "Checking..."}
          {status === "unknown" && "Unknown status"}
        </span>
      </div>

      {health?.services && (
        <div className="flex items-center gap-3">
          {Object.entries(health.services).map(([name, state]) => (
            <span key={name} className={cn(state === "error" && "text-destructive")}>
              {name}: {state}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
