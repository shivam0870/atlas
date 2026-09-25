import { useState } from "react";
import { api, json } from "../api/client";
import { useWorkspace } from "../app/context";
import { Badge, Button, ErrorNotice, Field, Modal } from "../components/ui";
import {
  formatDate,
  SelectField,
  useWorkbenchActions,
} from "./WorkbenchShared";
export function PublicationControls({
  documentId,
  version,
}: {
  documentId: string;
  version: {
    id: string;
    number: number;
    status: string;
    publication_status?: string;
    effective_at?: string | null;
  };
}) {
  const { tenantId, canEdit } = useWorkspace();
  const actions = useWorkbenchActions();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("draft");
  const [effective, setEffective] = useState("");
  return (
    <>
      <div className="workbench-actions publication-controls">
        <Badge>{version.publication_status || "published"}</Badge>
        {version.effective_at && (
          <span className="muted">
            Effective {formatDate(version.effective_at)}
          </span>
        )}
        {canEdit && (
          <Button
            variant="secondary"
            onClick={() => {
              setStatus(version.publication_status || "published");
              setEffective(version.effective_at?.slice(0, 10) || "");
              setOpen(true);
            }}
          >
            Manage publication
          </Button>
        )}
      </div>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title={`Publication · Version ${version.number}`}
        description="Only the current, effective published version is used for answers. Indexing must complete before publishing."
      >
        <form
          className="form-stack"
          onSubmit={(event) => {
            event.preventDefault();
            void actions.run(async (signal) => {
              await api(
                `/library/${documentId}/versions/${version.id}/publication`,
                {
                  tenantId,
                  method: "PATCH",
                  signal,
                  body: json({
                    publication_status: status,
                    effective_at: effective
                      ? new Date(`${effective}T00:00:00`).toISOString()
                      : null,
                  }),
                },
              );
              setOpen(false);
            });
          }}
        >
          <ErrorNotice error={actions.error} />
          <SelectField
            label="Publication status"
            value={status}
            onChange={setStatus}
          >
            <option value="draft">Draft</option>
            <option value="published" disabled={version.status !== "ready"}>
              Published
            </option>
            <option value="superseded">Superseded</option>
            <option value="archived">Archived</option>
          </SelectField>
          <Field
            label="Effective date"
            type="date"
            value={effective}
            onChange={(event) => setEffective(event.target.value)}
            max={
              status === "published"
                ? new Date().toLocaleDateString("en-CA")
                : undefined
            }
            hint="Future effective dates can be saved on drafts. Publish when the document becomes effective."
          />
          <Button busy={actions.busy}>Save publication</Button>
        </form>
      </Modal>
    </>
  );
}
