import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  ArrowUp,
  Check,
  Copy,
  HelpCircle,
  Moon,
  Printer,
  Search,
  ShieldCheck,
  Sun,
} from "lucide-react";
import { Button, Field, Modal, Notice, ErrorNotice } from "./ui";

export const RELEASE_DATE = "2026-09-24";
const consentKey = "atlas-privacy-choice";
const campaignKey = "atlas-campaign";
export function readPreference(key: string, fallback = "") {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
export function savePreference(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Private browsing can disable storage. */
  }
}
export function captureCampaign(search: string, allowed: boolean) {
  try {
    if (!allowed) {
      sessionStorage.removeItem(campaignKey);
      return;
    }
    const params = new URLSearchParams(search);
    const campaign: Record<string, string> = {};
    for (const key of [
      "utm_source",
      "utm_medium",
      "utm_campaign",
      "utm_content",
      "utm_term",
    ]) {
      const value = params.get(key);
      if (value)
        campaign[key] = value
          .replace(/[\u0000-\u001f\u007f]/g, "")
          .slice(0, 120);
    }
    if (Object.keys(campaign).length)
      sessionStorage.setItem(campaignKey, JSON.stringify(campaign));
  } catch {
    /* Attribution is optional and must never block navigation. */
  }
}
export function ThemeToggle() {
  const [dark, setDark] = useState(
    document.documentElement.dataset.theme === "dark",
  );
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setDark(document.documentElement.dataset.theme === "dark"),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    // The parent theme effect can run before this observer is attached on reload.
    setDark(document.documentElement.dataset.theme === "dark");
    return () => observer.disconnect();
  }, []);
  function toggle() {
    const value = dark ? "light" : "dark";
    savePreference("atlas-theme", value);
    document.documentElement.dataset.theme = value;
    setDark(!dark);
    window.dispatchEvent(new Event("atlas:theme"));
  }
  return (
    <Button
      variant="ghost"
      className="theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${dark ? "light" : "dark"} mode`}
      title={`Switch to ${dark ? "light" : "dark"} mode`}
    >
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </Button>
  );
}
const questions = [
  [
    "How do I get my first answer?",
    "Open Library, add a PDF, DOCX, Markdown or text file, and wait for Ready. Open Ask Atlas and ask a specific question about that document. Open a numbered citation to check the source.",
  ],
  [
    "Who can see my conversations?",
    "Conversations and saved answers are private to your account. Company administrators do not automatically see them. Documents follow the permissions of their company, space and document.",
  ],
  [
    "What happens when a document changes?",
    "Replacing a document creates a new version. Existing citations retain their original version. Access is checked again when you open evidence, including in old conversations.",
  ],
  [
    "Why is a source unavailable?",
    "Your access may have changed, or the document may have been archived or removed. Ask a workspace administrator to review your access. Atlas does not reveal content you can no longer access.",
  ],
  [
    "How do I secure my account?",
    "Open My account to enable an authenticator app, save your recovery codes, review active sessions and sign out other devices. Never share verification links or recovery codes.",
  ],
  [
    "Why might the public demo be offline?",
    "The demo runs on the developer’s Mac. It is available while that machine, its internet connection and the tunnel are running. The project showcase and source remain on GitHub.",
  ],
];
export function ExperienceTools() {
  const location = useLocation();
  const workspace = location.pathname.startsWith("/o/");
  const [help, setHelp] = useState(false);
  const [privacy, setPrivacy] = useState(() => !readPreference(consentKey));
  const [consent, setConsent] = useState(() => readPreference(consentKey));
  const [search, setSearch] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>();
  const [progress, setProgress] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [draft, setDraft] = useState("");
  useEffect(() => {
    captureCampaign(location.search, consent === "allowed");
  }, [location.search, consent]);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const reader = document.querySelector<HTMLElement>(
          ".conversation-scroll",
        );
        const element =
          reader && reader.scrollHeight > reader.clientHeight + 1
            ? reader
            : document.documentElement;
        const available = element.scrollHeight - element.clientHeight;
        setProgress(
          available > 0
            ? Math.min(100, (element.scrollTop / available) * 100)
            : 0,
        );
        setScrolled(element.scrollTop > 250);
      });
    };
    update();
    window.addEventListener("scroll", update, true);
    window.addEventListener("resize", update);
    const resize = new ResizeObserver(update);
    resize.observe(document.body);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
  }, [location.pathname]);
  function choose(value: string) {
    savePreference(consentKey, value);
    setConsent(value);
    setPrivacy(false);
    captureCampaign(location.search, value === "allowed");
  }
  async function copyLink() {
    try {
      // Never copy a reset, verification or invitation token from the URL.
      await navigator.clipboard.writeText(window.location.origin);
      setCopied(true);
      setError(undefined);
    } catch {
      setError(
        new Error(
          "Clipboard access is unavailable. Copy the site address from your browser instead.",
        ),
      );
    }
  }
  return (
    <>
      <div className="reading-progress" aria-hidden="true">
        <span style={{ width: `${progress}%` }} />
      </div>
      {!workspace && (
        <div className="auth-tools">
          <a
            href="https://shivam0870.github.io/atlas/"
            target="_blank"
            rel="noreferrer"
          >
            About Atlas ↗
          </a>
          <ThemeToggle />
        </div>
      )}
      <div className="floating-tools">
        {scrolled && (
          <Button
            variant="secondary"
            aria-label="Back to top"
            onClick={() => {
              const behavior = matchMedia("(prefers-reduced-motion: reduce)")
                .matches
                ? "instant"
                : "smooth";
              window.scrollTo({ top: 0, behavior });
              document
                .querySelector(".conversation-scroll")
                ?.scrollTo({ top: 0, behavior });
            }}
          >
            <ArrowUp size={18} />
          </Button>
        )}
        <Button
          className="help-launcher"
          onClick={() => setHelp(true)}
          aria-label="Help and contact"
        >
          <HelpCircle size={18} />
          <span>Help</span>
        </Button>
      </div>
      {privacy && (
        <section
          className="privacy-banner"
          aria-label="Cookie and privacy preferences"
        >
          <ShieldCheck size={23} />
          <div>
            <strong>Your workspace. Your choices.</strong>
            <p>
              Atlas uses essential sign-in cookies and stores your display
              preferences. Optional campaign attribution remembers only UTM tags
              in this browser session. Nothing is sent to an analytics service.
            </p>
            <div className="row">
              <Button variant="secondary" onClick={() => choose("essential")}>
                Essential only
              </Button>
              <Button onClick={() => choose("allowed")}>
                Allow attribution
              </Button>
            </div>
          </div>
        </section>
      )}
      <Modal
        open={help}
        onOpenChange={setHelp}
        title="A little help, right here."
        description="Find your next step or get in touch with the project maintainer."
        wide
      >
        <div className="help-search">
          <Search size={18} />
          <input
            aria-label="Search help"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search guides and questions…"
          />
        </div>
        <div className="faq-list">
          {questions
            .filter((q) =>
              q.join(" ").toLowerCase().includes(search.toLowerCase()),
            )
            .map(([q, a]) => (
              <details key={q}>
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
          {!questions.some((q) =>
            q.join(" ").toLowerCase().includes(search.toLowerCase()),
          ) && (
            <p role="status">
              No matching guide. Try “sources”, “account” or “document”.
            </p>
          )}
        </div>
        <div className="help-actions">
          <Button variant="secondary" onClick={copyLink}>
            {copied ? <Check size={16} /> : <Copy size={16} />}{" "}
            {copied ? "Site link copied" : "Copy site link"}
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setHelp(false);
              requestAnimationFrame(() => window.print());
            }}
          >
            <Printer size={16} />
            Print this page
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setPrivacy(true);
              setHelp(false);
            }}
          >
            Privacy preferences
          </Button>
        </div>
        <ErrorNotice error={error} />
        <details className="contact-form">
          <summary>Contact the maintainer / report an issue</summary>
          <p>
            Prepare a GitHub issue for review. Include what happened, without
            passwords, private documents or account links.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (
                draftTitle.trim().length < 5 ||
                draftBody.trim().length < 15
              ) {
                setError(
                  new Error(
                    "Add a subject of at least 5 characters and a description of at least 15 characters.",
                  ),
                );
                setDraft("");
                return;
              }
              setError(undefined);
              setDraft(
                `https://github.com/shivam0870/atlas/issues/new?title=${encodeURIComponent(draftTitle.trim())}&body=${encodeURIComponent(draftBody.trim())}`,
              );
            }}
          >
            <Field
              label="Issue subject"
              value={draftTitle}
              maxLength={120}
              onChange={(e) => {
                setDraftTitle(e.target.value);
                setDraft("");
              }}
            />
            <label className="field">
              What happened?
              <textarea
                value={draftBody}
                maxLength={2000}
                onChange={(e) => {
                  setDraftBody(e.target.value);
                  setDraft("");
                }}
              />
            </label>
            <Button type="submit" variant="secondary">
              Prepare issue
            </Button>
          </form>
          {draft && (
            <Notice>
              Your draft is ready. Nothing has been sent.{" "}
              <a
                className="text-link"
                href={draft}
                target="_blank"
                rel="noreferrer"
              >
                Review and submit on GitHub ↗
              </a>
            </Notice>
          )}
        </details>
        <footer className="release-note">
          Atlas interface · Last updated{" "}
          <time dateTime={RELEASE_DATE}>24 September 2026</time>
          <br />
          Attribution:{" "}
          {consent === "allowed" ? "allowed in this browser session" : "off"}
        </footer>
      </Modal>
    </>
  );
}
