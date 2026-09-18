import { Badge } from "../components/ui";

export type EffectivePrincipal = {
  subject_type: "user" | "service";
  subject_id: string;
  name: string;
  role: string | null;
  can_read: boolean;
  can_edit: boolean;
  reasons: string[];
};

export function EffectiveAccess({ items }: { items: EffectivePrincipal[] }) {
  const readers = items.filter((item) => item.can_read).length;
  const editors = items.filter((item) => item.can_edit).length;
  return (
    <section aria-label="Effective access after saved grants">
      <h3>Effective access after saved grants</h3>
      <p className="muted">
        {readers} {readers === 1 ? "reader" : "readers"} · {editors}{" "}
        {editors === 1 ? "editor" : "editors"}. These permissions reflect the
        saved configuration. Save changes and reopen this panel to check the
        updated result.
      </p>
      <p className="muted">
        Company owners and administrators retain access to company knowledge. A
        grant cannot exceed a person’s company role, a document’s space
        boundary, or an integration’s active credential scopes.
      </p>
      <div
        className="table-wrap"
        role="region"
        tabIndex={0}
        aria-label="Effective access table"
      >
        <table className="data-table">
          <caption className="sr-only">
            Effective reader and editor permissions
          </caption>
          <thead>
            <tr>
              <th>Person or integration</th>
              <th>Read</th>
              <th>Edit</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.subject_type}:${item.subject_id}`}>
                <td>
                  <strong>{item.name}</strong>
                  <div className="muted">
                    {item.subject_type === "service"
                      ? "Integration"
                      : item.role || "Member"}
                  </div>
                </td>
                <td>
                  <Badge>{item.can_read ? "Allowed" : "Denied"}</Badge>
                </td>
                <td>
                  <Badge>{item.can_edit ? "Allowed" : "Denied"}</Badge>
                </td>
                <td>
                  {item.reasons.length ? (
                    <ul>
                      {item.reasons.map((reason, index) => (
                        <li key={index}>{reason}</li>
                      ))}
                    </ul>
                  ) : (
                    "No explanation recorded"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
