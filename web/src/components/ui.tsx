import {
  forwardRef,
  useRef,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
  type InputHTMLAttributes,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertCircle,
  BookOpen,
  LoaderCircle,
  Eye,
  EyeOff,
  X,
} from "lucide-react";
export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "primary" | "secondary" | "ghost" | "danger";
    busy?: boolean;
  }
>(
  (
    { variant = "primary", busy, children, className = "", disabled, ...props },
    ref,
  ) => (
    <button
      ref={ref}
      className={`button ${variant} ${className}`}
      disabled={disabled || busy}
      {...props}
    >
      {busy && <LoaderCircle size={16} className="spin" />}
      {children}
    </button>
  ),
);
export function Field({
  label,
  hint,
  error,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const generatedId = useId();
  const id = props.id || generatedId;
  const [visible, setVisible] = useState(false);
  const password = props.type === "password";
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className={password ? "password-field" : undefined}>
        <input
          {...props}
          type={password && visible ? "text" : props.type}
          id={id}
          aria-invalid={!!error}
          aria-describedby={hint || error ? `${id}-hint` : undefined}
        />
        {password && (
          <button
            type="button"
            className="password-toggle"
            aria-label={
              visible
                ? `Hide ${label.toLowerCase()}`
                : `Show ${label.toLowerCase()}`
            }
            aria-pressed={visible}
            onClick={() => setVisible(!visible)}
          >
            {visible ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        )}
      </div>
      {(hint || error) && (
        <small id={`${id}-hint`} className={error ? "error-text" : ""}>
          {error || hint}
        </small>
      )}
    </div>
  );
}
export function PageHeader({
  title,
  description,
  action,
  eyebrow,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action && <div className="page-actions">{action}</div>}
    </header>
  );
}
export function EmptyState({
  title,
  description,
  action,
  icon = <BookOpen size={26} />,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-symbol">{icon}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function ErrorNotice({ error }: { error: unknown }) {
  return error ? (
    <div role="alert" className="notice error">
      <AlertCircle size={18} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  ) : null;
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="notice success">
      {children}
    </div>
  );
}
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="loading">
      <LoaderCircle className="spin" size={20} />
      {label}
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide = false,
  side = false,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  wide?: boolean;
  side?: boolean;
}) {
  const previouslyOpen = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  if (open && !previouslyOpen.current && typeof document !== "undefined") {
    returnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
  }
  previouslyOpen.current = open;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className={`modal-content ${wide ? "wide" : ""} ${side ? "side" : ""}`}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (returnFocus.current?.isConnected)
              returnFocus.current.focus({ preventScroll: true });
          }}
          {...(!description ? { "aria-describedby": undefined } : {})}
        >
          <div className="modal-header">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              {description && (
                <Dialog.Description>{description}</Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" aria-label="Close dialog">
                <X size={20} />
              </Button>
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function initials(name: string) {
  return name
    .split(/\s+/)
    .map((x) => x[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
