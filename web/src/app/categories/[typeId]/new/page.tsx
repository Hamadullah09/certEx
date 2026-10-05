"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, ChevronLeft, FolderPlus, Info } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import {
  ColumnBuilder,
  type ColumnDraft,
  keyFromLabel,
  newColumn,
  problemsWith,
} from "@/components/batches/column-builder";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateBatch } from "@/hooks/use-batches";
import { useCertificateTypes, useSchemaVersion } from "@/hooks/use-register";
import { useDefaultSchemaVersion } from "@/hooks/use-registry";
import { ApiError } from "@/lib/api";
import { isRtlText as isUrdu } from "@/lib/text-direction";

/**
 * Creating a batch, and deciding once what every document in it will be read for.
 *
 * The columns are started from the category's standard ones rather than from nothing.
 * An office whose birth register looks like everybody else's should be able to press
 * save; an office with its own form edits the list first. Starting empty would make
 * the common case the most work, and would invite a batch missing the columns the
 * register needs.
 */
export default function NewBatchInCategoryPage() {
  const params = useParams<{ typeId: string }>();
  const typeId = params.typeId;
  const router = useRouter();

  const categories = useCertificateTypes();
  const category = (categories.data ?? []).find((item) => item.id === typeId);
  const defaultVersion = useDefaultSchemaVersion(typeId);
  const starting = useSchemaVersion(defaultVersion.data?.latest_version_id);

  const [name, setName] = React.useState("");
  const [columns, setColumns] = React.useState<ColumnDraft[]>([]);
  const seeded = React.useRef(false);

  // Seeded once from the category's standard columns, then it is the operator's list.
  React.useEffect(() => {
    if (seeded.current || !starting.data) return;
    seeded.current = true;
    setColumns(
      starting.data.fields.map((field) =>
        newColumn({
          name: field.name,
          label: field.label,
          kind: field.kind,
          isIdentifier: field.role === "identifier",
          required: field.required,
          keyFollowsLabel: false,
          // Carried through, not dropped. The standard columns match a printed form
          // through these; without them "Certificate number" fails to match a form
          // that prints "Certificate No." and the record cannot be filed.
          otherWordings: [...field.labels_en, ...field.labels_ur],
        }),
      ),
    );
  }, [starting.data]);

  // Nothing to start from: a category with no published schema, which is the normal
  // state of one an administrator has just added.
  React.useEffect(() => {
    if (seeded.current) return;
    if (defaultVersion.isFetched && !defaultVersion.data?.latest_version_id) {
      seeded.current = true;
      setColumns([
        newColumn({
          label: "Certificate No",
          name: keyFromLabel("Certificate No"),
          kind: "reference",
          isIdentifier: true,
          required: true,
          keyFollowsLabel: false,
        }),
      ]);
    }
  }, [defaultVersion.isFetched, defaultVersion.data]);

  const create = useCreateBatch();
  const problems = problemsWith(columns);
  const canSave = name.trim().length > 0 && problems.length === 0 && !create.isPending;

  function save() {
    if (!canSave) return;
    create.mutate(
      {
        name: name.trim(),
        certificate_type_id: typeId,
        fields: columns.map((column) => ({
          name: column.name.trim(),
          label: column.label.trim(),
          kind: column.kind,
          role: column.isIdentifier ? "identifier" : "none",
          required: column.required,
          // Latin and Urdu wordings go in the same box and are split by script here,
          // because asking a clerk which list a wording belongs to is asking them about
          // the reader's internals.
          labels_en: column.otherWordings.filter((item) => !isUrdu(item)),
          labels_ur: column.otherWordings.filter(isUrdu),
        })),
      },
      {
        onSuccess: (batch) => {
          toast.success(`"${batch.name}" is ready for documents.`);
          router.push(`/batches/${batch.id}`);
        },
        onError: (error) =>
          toast.error(
            error instanceof ApiError ? error.userMessage : "The batch could not be created.",
          ),
      },
    );
  }

  return (
    <AppShell>
      <Button variant="ghost" asChild className="-ml-3">
        <Link href={`/categories/${typeId}`}>
          <ChevronLeft aria-hidden="true" />
          {category?.name ?? "Back"}
        </Link>
      </Button>

      <PageHeader
        icon={FolderPlus}
        title="Create a batch"
        description={`A set of ${(category?.name ?? "certificate").toLowerCase()} certificates read together, with its own columns.`}
      />

      <Card className="mt-8">
        <CardHeader className="pb-4">
          <CardTitle>Step 1 · Name this batch</CardTitle>
          <CardDescription>
            How it appears in the list and in the name of the file you download.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Label htmlFor="batch-name" className="sr-only">
            Batch name
          </Label>
          <Input
            id="batch-name"
            className="max-w-lg"
            placeholder={`${category?.name ?? "Birth"} Certificates 2020`}
            value={name}
            disabled={create.isPending}
            onChange={(event) => setName(event.target.value)}
          />
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="pb-4">
          <CardTitle>Step 2 · Choose the columns</CardTitle>
          <CardDescription>
            These become the columns of the spreadsheet, and the only things pulled out of
            every document you upload here.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <Alert>
            <Info aria-hidden="true" />
            <AlertTitle>This is decided once</AlertTitle>
            <AlertDescription>
              Every document uploaded into this batch is read for these columns - the first
              one and the ten-thousandth. You will not be asked again. Columns cannot be
              changed afterwards, because records already filed were read under them.
            </AlertDescription>
          </Alert>

          {starting.isPending && !seeded.current ? (
            <div className="space-y-3">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-32 w-full rounded-lg" />
              ))}
            </div>
          ) : (
            <ColumnBuilder
              columns={columns}
              onChange={setColumns}
              disabled={create.isPending}
            />
          )}
        </CardContent>
      </Card>

      {problems.length > 0 && columns.length > 0 ? (
        <Alert variant="warning" className="mt-6">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>Not ready to save yet</AlertTitle>
          <AlertDescription>
            <ul className="list-disc space-y-1 pl-5">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Button size="lg" disabled={!canSave} onClick={save}>
          <FolderPlus aria-hidden="true" />
          {create.isPending ? "Creating…" : "Save and add documents"}
        </Button>
        <Button variant="outline" size="lg" asChild>
          <Link href={`/categories/${typeId}`}>Cancel</Link>
        </Button>
        {name.trim().length === 0 ? (
          <p className="text-base text-muted-foreground">Give the batch a name to continue.</p>
        ) : null}
      </div>
    </AppShell>
  );
}
