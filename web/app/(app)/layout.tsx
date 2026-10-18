import { MeProvider } from "@/components/me";
import { Shell } from "@/components/shell";
import { Spinner } from "@/components/ui";

export default function SignedInLayout({ children }: LayoutProps<"/">) {
  return (
    <MeProvider
      fallback={
        <div className="flex flex-1 items-center justify-center text-muted">
          <Spinner />
        </div>
      }
    >
      <Shell>{children}</Shell>
    </MeProvider>
  );
}
