import { App } from "@/components/App";
import { ReviewQueue } from "@/components/ReviewQueue";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const { view } = await searchParams;
  return view === "contacts" ? <App /> : <ReviewQueue />;
}
