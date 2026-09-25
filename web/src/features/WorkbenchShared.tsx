import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { useScopedQueryKey, useWorkspace } from "../app/context";

export type SpaceOption = { id: string; name: string };
export type DocumentOption = {
  id: string;
  title: string;
  current_version_id: string | null;
  space_id: string | null;
};
export type VersionOption = {
  id: string;
  number: number;
  title: string;
  status: string;
  publication_status?: string;
};
export const formatDate = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : "—";

export function useWorkbenchQuery<T>(
  key: string,
  path: string,
  enabled = true,
  poll = false,
) {
  const { tenantId } = useWorkspace();
  const result = useQuery({
    queryKey: useScopedQueryKey(key, path),
    queryFn: ({ signal }) => api<T>(path, { tenantId, signal }),
    enabled,
    refetchInterval: poll ? 5000 : false,
  });
  // A failed authorization refresh must not leave previously fetched text visible.
  return result.error instanceof ApiError &&
    [401, 403, 404].includes(result.error.status)
    ? { ...result, data: undefined }
    : result;
}

export function useWorkbenchActions() {
  const { tenantId, user, organization } = useWorkspace();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const controller = useRef(new AbortController());
  const locked = useRef(false);
  useEffect(() => {
    controller.current = new AbortController();
    locked.current = false;
    setBusy(false);
    setError(undefined);
    setMessage("");
    return () => controller.current.abort();
  }, [tenantId, organization.auth_revision]);
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    setMessage("");
    const signal = controller.current.signal;
    try {
      await action(signal);
      if (!signal.aborted)
        await client.invalidateQueries({
          queryKey: ["workspace", user.id, tenantId],
        });
    } catch (cause) {
      if (!signal.aborted) setError(cause);
    } finally {
      locked.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }
  return { busy, error, message, setError, setMessage, run };
}

export function SelectField({
  label,
  value,
  onChange,
  children,
  required = false,
  disabled = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  required?: boolean;
  disabled?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        disabled={disabled}
      >
        {children}
      </select>
    </label>
  );
}

export function useDocumentOptions(enabled = true) {
  return useWorkbenchQuery<{ items: DocumentOption[]; total: number }>(
    "workbench-documents",
    "/library?page_size=100&lifecycle=active",
    enabled,
  );
}

export function VersionSelect({
  documentId,
  value,
  onChange,
  label = "Source version",
  required = false,
}: {
  documentId: string;
  value: string;
  onChange: (value: string) => void;
  label?: string;
  required?: boolean;
}) {
  const versions = useWorkbenchQuery<VersionOption[]>(
    "versions",
    `/library/${documentId}/versions`,
    !!documentId,
  );
  return (
    <div>
      <SelectField
        label={label}
        value={value}
        onChange={onChange}
        required={required}
        disabled={!documentId || versions.isPending}
      >
        <option value="">
          {required ? "Choose a version" : "Current published version"}
        </option>
        {versions.data
          ?.filter((item) => item.status === "ready")
          .map((item) => (
            <option key={item.id} value={item.id}>
              Version {item.number}
              {item.publication_status ? ` · ${item.publication_status}` : ""}
            </option>
          ))}
      </SelectField>
      {versions.error && (
        <small className="error-text">
          Source versions could not be loaded.
        </small>
      )}
    </div>
  );
}
