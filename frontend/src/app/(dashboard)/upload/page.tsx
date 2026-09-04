import { UploadPanel } from "@/components/documents/UploadPanel";

export const metadata = { title: "Upload · EnterpriseRAG" };

export default function UploadPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-on-background">Upload documents</h1>
        <p className="mt-1 text-on-surface-variant">
          Add files to the knowledge base. Each file is validated independently — one invalid file
          never blocks the rest.
        </p>
      </header>

      <UploadPanel />
    </div>
  );
}
