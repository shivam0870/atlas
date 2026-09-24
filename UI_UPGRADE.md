# Atlas interface upgrade

Updated: 24 September 2026

The application and public showcase now share a forest-green, warm-paper visual style, clearer typography, stronger page hierarchy, responsive spacing and consistent controls. The workspace overview uses real permission-scoped document, conversation and saved-item data.

## Requested features

All 20 items are implemented or retained and improved where Atlas already provided them. Authentication, retrieval and tenant permissions still use the existing backend.

| #   | Feature             | Implementation and verification                                                                                                                                                                               |
| --- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Dark mode           | Immediate toggle on sign-in and workspace pages; showcase toggle; preferences survive refresh. Browser and unit checks cover initialization and switching.                                                    |
| 2   | Cookie banner       | Explicit essential-only or optional attribution choice; preferences can be reopened from Help or the showcase footer. Browser checks cover consent and opt-out.                                               |
| 3   | Site search         | Existing permission-scoped Atlas command search and search page retained; searchable help added; showcase content search supports Cmd/Ctrl+K. Live permitted-document search verified.                        |
| 4   | Back to top         | Appears after scrolling; supports long pages and conversation readers. Browser checks cover page scrolling.                                                                                                   |
| 5   | Mobile menu         | Accessible workspace drawer retained; showcase menu added, including Escape and restored focus. Verified at mobile widths.                                                                                    |
| 6   | Loading animations  | Shared loading indicators and pending action controls refined; reduced-motion preference disables animations. Existing pending-action unit check retained.                                                    |
| 7   | Hover states        | Buttons, navigation, cards, rows and source cards have consistent hover and keyboard-focus treatments. Visually reviewed in Chrome.                                                                           |
| 8   | Scroll progress     | Thin progress indicator follows document or conversation scrolling. Showcase progress verified in Chrome.                                                                                                     |
| 9   | Copy buttons        | Existing answer copying retained; Help copies only the site origin, excluding account tokens; showcase copies the demo link. Real browser clipboard checks passed.                                            |
| 10  | Print stylesheet    | Printable project overview and conversation content; hides navigation, composers, overlays and password controls, including revealed passwords. Actual PDFs generated and print-media assertions passed.      |
| 11  | Sticky headers      | Sticky application topbar and showcase navigation. Shared spacing keeps linked sections clear of the header.                                                                                                  |
| 12  | Skip to content     | Root keyboard skip link on account and workspace surfaces; showcase skip link retained. Focus movement tested in Chrome.                                                                                      |
| 13  | Password visibility | Shared password fields support accessible show/hide buttons without submitting the form. Unit and browser checks passed.                                                                                      |
| 14  | UTM attribution     | Explicit opt-in, browser-session storage of five whitelisted UTM fields, control-character removal and length limits; opt-out clears attribution. No full URLs, account tokens or analytics network requests. |
| 15  | Form success        | Existing product notices retained; contact draft preparation provides an explicit success state before manual GitHub submission. Browser checked.                                                             |
| 16  | Form errors         | Existing labeled field errors and alerts retained; contact validation added. Unit and browser checks passed.                                                                                                  |
| 17  | Confirmation modals | Existing document/access confirmations receive the new shared design. Real document-trash cancellation verified without changing the document.                                                                |
| 18  | Last updated        | Release date in Help and showcase footer; existing actual document/version timestamps retained. Date markup checked.                                                                                          |
| 19  | Expandable FAQ      | Searchable questions in Help and native accessible FAQ disclosures on the showcase. Browser checked.                                                                                                          |
| 20  | Floating contact    | Help/contact launcher in the app and contact launcher on the showcase. Forms prepare GitHub issues; the user reviews and submits them. No message is sent automatically.                                      |

## Verification

- **21 frontend unit tests passed**, including attribution consent, sensitive-query exclusion, storage failures, password visibility and saved-theme initialization.
- **Production TypeScript/Vite build passed**, also in an isolated checkout containing only this UI release.
- **Frontend formatting checks passed.**
- **3 new Playwright tests passed through the real public HTTPS endpoint**: privacy/help/contact; keyboard/password/theme/mobile; print/reduced motion.
- **6 additional real Chrome review groups passed** using `scripts/verify_ui.cjs`: showcase controls; FAQ/contact/clipboard/scrolling; responsive/print; real account/workspace/search; preserved document/citation/clipboard/print; mobile conversation/evidence/navigation.
- Responsive checks covered **320, 390, 768 and 1440 px**. Screenshots were visually reviewed. Browser review recorded no uncaught page errors.
- CI also runs the new browser tests alongside existing registration, invitation, password-reset and MFA journeys. See the commit's **Verify Atlas** workflow for its result.

The public browser review uses an existing synthetic account and the real API. It does not mock retrieval, permissions or citation responses. It reads an existing generated conversation; this UI release does not claim a fresh model-generation or full backend suite run from those checks. The previous product release report remains the source for earlier backend/model verification. Human assistive-technology testing and Safari/Firefox testing were not performed.

Screenshots: [sign-in](artifacts/atlas-ui-login.png), [workspace](artifacts/atlas-upgrade-home.png), [mobile conversation](artifacts/atlas-ui-conversation-mobile.png). Detailed browser reports and PDFs stay under ignored `.local/ui-review/`; credentials remain private.

## Run and verify

Use the existing backend setup in [README](README.md) and [deployment guide](DEPLOYMENT.md). No migration, model API key, paid service or new dependency is required by this UI release.

```sh
cd web
npm ci
npm run test:unit
npm run build
npm run format:check
# With the application running on localhost:8100:
npm run test:e2e -- e2e/experience.spec.ts e2e/upgrade-auth.spec.ts
```

The new experience tests also accept `ATLAS_BROWSER_URL` for a real demo endpoint. Existing authentication tests use the local Mailpit inbox and must run against the local test environment, not the public Gmail configuration.

To review the static showcase locally, copy `site/` into an ignored preview directory and place `artifacts/atlas-upgrade-home.png` under its `assets/` directory. Serve it on loopback port 8130, then run:

```sh
node scripts/verify_ui.cjs --showcase http://127.0.0.1:8130
```

For the maintainer's existing synthetic public fixture, add `--account-file .local/public-demo/journey-account.json --journey-file .local/public-demo/journey.json`. This fixture is intentionally not committed. The script does not send contact messages, delete documents or generate new answers.

## Publication and recovery

The public application serves the built `web/dist` assets. GitHub Pages publishes only `site/` plus the reviewed screenshot; it does not receive configuration files, credentials or backend data. The Pages workflow now includes the showcase stylesheet and script.

For rollback, check out the previous release in an isolated directory, install its locked frontend dependencies and rebuild its frontend. Restore those generated assets to the serving directory; revert the UI commit and republish Pages if needed. **No database rollback is required.** Preserve current private runtime configuration and unrelated work.

The working demo still depends on the developer's Mac and ngrok tunnel staying online. Optional UTM attribution is local session metadata, not an analytics dashboard. Contact submission requires a GitHub account and an explicit submission by the visitor.
