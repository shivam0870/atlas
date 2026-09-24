(() => {
  const $ = (id) => document.getElementById(id);
  const read = (key) => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  };
  const save = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* Optional preferences. */
    }
  };
  const theme = $("theme-toggle");
  function setTheme(value) {
    document.documentElement.dataset.theme = value;
    theme.setAttribute(
      "aria-label",
      `Switch to ${value === "dark" ? "light" : "dark"} mode`,
    );
  }
  setTheme(
    read("atlas-theme") ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );
  theme.addEventListener("click", () => {
    const value =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    save("atlas-theme", value);
    setTheme(value);
  });
  const menu = $("menu-toggle");
  function closeMenu() {
    $("navigation").classList.remove("open");
    menu.setAttribute("aria-expanded", "false");
    menu.setAttribute("aria-label", "Open menu");
  }
  menu.addEventListener("click", () => {
    const open = $("navigation").classList.toggle("open");
    menu.setAttribute("aria-expanded", String(open));
    menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  });
  $("navigation").addEventListener("click", (e) => {
    if (e.target.closest("a")) closeMenu();
  });
  const banner = document.querySelector(".cookie-banner");
  function capture(allowed) {
    try {
      if (!allowed) {
        sessionStorage.removeItem("atlas-campaign");
        return;
      }
      const params = new URLSearchParams(location.search),
        values = {};
      for (const key of [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
      ]) {
        const value = params.get(key);
        if (value)
          values[key] = value
            .replace(/[\u0000-\u001f\u007f]/g, "")
            .slice(0, 120);
      }
      if (Object.keys(values).length)
        sessionStorage.setItem("atlas-campaign", JSON.stringify(values));
    } catch {
      /* Attribution must never block the site. */
    }
  }
  banner.hidden = !!read("atlas-privacy-choice");
  capture(read("atlas-privacy-choice") === "allowed");
  function consent(value) {
    save("atlas-privacy-choice", value);
    capture(value === "allowed");
    banner.hidden = true;
  }
  $("essential-only").addEventListener("click", () => consent("essential"));
  $("allow-attribution").addEventListener("click", () => consent("allowed"));
  $("privacy-open").addEventListener("click", () => {
    banner.hidden = false;
    $("essential-only").focus();
  });
  let frame;
  function progress() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const height = document.documentElement.scrollHeight - innerHeight;
      document.querySelector(".scroll-progress").style.width =
        `${height > 0 ? (scrollY / height) * 100 : 0}%`;
      $("back-top").hidden = scrollY < 250;
    });
  }
  addEventListener("scroll", progress, { passive: true });
  addEventListener("resize", progress);
  progress();
  $("back-top").addEventListener("click", () => {
    scrollTo({
      top: 0,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  });
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dialog.close();
      }
    });
    dialog.addEventListener("close", () => {
      const target = dialog.dataset.focusTarget;
      if (target) {
        document.querySelector(target)?.focus();
        delete dialog.dataset.focusTarget;
      } else {
        $(dialog.id === "site-search" ? "search-open" : "contact-open").focus({
          preventScroll: true,
        });
      }
    });
    dialog
      .querySelector("[data-close]")
      .addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => {
      const b = dialog.getBoundingClientRect();
      if (
        e.target === dialog &&
        (e.clientX < b.left ||
          e.clientX > b.right ||
          e.clientY < b.top ||
          e.clientY > b.bottom)
      )
        dialog.close();
    });
  }
  const search = $("site-search");
  const entries = [...document.querySelectorAll("main section")].map(
    (section) => ({
      title: section.querySelector("h2").textContent.trim(),
      content: section.textContent.toLowerCase(),
      href: `#${section.id}`,
    }),
  );
  function searchSite() {
    const term = $("search-query").value.trim().toLowerCase();
    const matches = entries.filter(
      (entry) => !term || entry.content.includes(term),
    );
    const results = $("search-results");
    results.replaceChildren();
    for (const entry of matches) {
      const a = document.createElement("a");
      a.href = entry.href;
      a.textContent = entry.title;
      a.addEventListener("click", () => {
        search.dataset.focusTarget = entry.href;
        search.close();
        document.querySelector(entry.href).setAttribute("tabindex", "-1");
        document.querySelector(entry.href).focus();
      });
      results.append(a);
    }
    if (!matches.length) {
      const p = document.createElement("p");
      p.textContent = "No matching section. Try documents, privacy or models.";
      results.append(p);
    }
  }
  $("search-open").addEventListener("click", () => {
    search.showModal();
    searchSite();
    $("search-query").focus();
  });
  $("search-query").addEventListener("input", searchSite);
  addEventListener("keydown", (e) => {
    if (
      (e.metaKey || e.ctrlKey) &&
      e.key.toLowerCase() === "k" &&
      !$("contact").open
    ) {
      e.preventDefault();
      if (search.open) search.close();
      else $("search-open").click();
    }
    if (e.key === "Escape" && menu.getAttribute("aria-expanded") === "true") {
      closeMenu();
      menu.focus();
    }
  });
  $("copy-demo").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(
        "https://cavalry-habitable-sureness.ngrok-free.dev",
      );
      $("copy-feedback").textContent = "Demo link copied. Ready to share.";
    } catch {
      $("copy-feedback").textContent =
        "Clipboard access is unavailable. Copy the Open Atlas link instead.";
    }
  });
  $("print-page").addEventListener("click", () => print());
  $("contact-open").addEventListener("click", () => $("contact").showModal());
  $("contact-form").addEventListener("input", () => {
    $("contact-success").hidden = true;
    $("contact-error").textContent = "";
  });
  $("contact-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const title = $("issue-title").value.trim(),
      body = $("issue-body").value.trim();
    const titleInvalid = title.length < 5,
      bodyInvalid = body.length < 15;
    $("issue-title").setAttribute("aria-invalid", String(titleInvalid));
    $("issue-body").setAttribute("aria-invalid", String(bodyInvalid));
    $("issue-title").setAttribute("aria-describedby", "contact-error");
    $("issue-body").setAttribute("aria-describedby", "contact-error");
    if (titleInvalid || bodyInvalid) {
      $("contact-error").textContent =
        "Add a subject of at least 5 characters and a description of at least 15 characters.";
      $(titleInvalid ? "issue-title" : "issue-body").focus();
      return;
    }
    $("contact-error").textContent = "";
    $("issue-link").href =
      `https://github.com/shivam0870/atlas/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
    $("contact-success").hidden = false;
  });
})();
