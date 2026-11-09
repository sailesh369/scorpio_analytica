document.addEventListener("DOMContentLoaded", () => {
  initReveal();
  initMenu();
  initUploadZone();
  initReportTabs();
  initDashboardTabs();
  initReportSearch();
  initCountUps();
});

function initReveal() {
  const items = document.querySelectorAll(".reveal");
  if (!items.length) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced || !("IntersectionObserver" in window)) {
    items.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.12 });

  items.forEach((item) => observer.observe(item));
}

function initMenu() {
  const toggle = document.querySelector("[data-menu-toggle]");
  const links = document.querySelector("[data-nav-links]");
  if (!toggle || !links) return;

  toggle.addEventListener("click", () => {
    const isOpen = links.classList.toggle("open");
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
  });
}

function initUploadZone() {
  const form = document.querySelector("[data-upload-form]");
  const input = document.querySelector("[data-file-input]");
  const zone = document.querySelector("[data-dropzone]");
  const nameNode = document.querySelector("[data-file-name]");
  const submit = document.querySelector("[data-upload-submit]");
  if (!form || !input || !zone || !nameNode) return;

  const renderName = (file) => {
    nameNode.textContent = file ? file.name : "No file selected";
    zone.classList.toggle("has-file", Boolean(file));
  };

  input.addEventListener("change", () => renderName(input.files[0]));

  ["dragenter", "dragover"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add("dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove("dragging");
    });
  });

  zone.addEventListener("drop", (event) => {
    const files = event.dataTransfer ? event.dataTransfer.files : null;
    if (!files || !files.length) return;
    input.files = files;
    renderName(files[0]);
  });

  form.addEventListener("submit", () => {
    if (submit) submit.textContent = "Analyzing...";
  });
}

function initReportTabs() {
  const buttons = document.querySelectorAll("[data-report-tab]");
  const panels = document.querySelectorAll("[data-report-panel]");
  if (!buttons.length || !panels.length) return;

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.getAttribute("data-report-tab");
      buttons.forEach((node) => node.classList.toggle("active", node === button));
      panels.forEach((panel) => {
        panel.classList.toggle("active", panel.getAttribute("data-report-panel") === target);
      });
    });
  });
}

function initDashboardTabs() {
  const buttons = document.querySelectorAll("[data-dashboard-tab]");
  const panels = document.querySelectorAll("[data-dashboard-panel]");
  if (!buttons.length || !panels.length) return;

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.getAttribute("data-dashboard-tab");
      buttons.forEach((node) => node.classList.toggle("active", node === button));
      panels.forEach((panel) => {
        panel.classList.toggle("active", panel.getAttribute("data-dashboard-panel") === target);
      });
    });
  });
}

function initReportSearch() {
  const input = document.querySelector("[data-report-search]");
  const rows = document.querySelectorAll("[data-report-name]");
  if (!input || !rows.length) return;

  input.addEventListener("input", () => {
    const term = input.value.trim().toLowerCase();
    rows.forEach((row) => {
      const name = row.getAttribute("data-report-name") || "";
      row.classList.toggle("hidden", term && !name.includes(term));
    });
  });
}

function initCountUps() {
  const items = document.querySelectorAll(".stat-card h3, .trust-item strong");
  if (!items.length) return;
  items.forEach((node) => {
    const raw = node.textContent.trim();
    if (!/^\d+(?:\.\d+)?%?$/.test(raw)) return;
    node.dataset.target = raw;
  });

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return;

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      animateCount(entry.target);
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.3 });

  items.forEach((item) => {
    if (item.dataset.target) observer.observe(item);
  });
}

function animateCount(node) {
  const raw = node.dataset.target || node.textContent.trim();
  const isPercent = raw.endsWith("%");
  const target = Number.parseFloat(raw.replace("%", ""));
  if (Number.isNaN(target)) return;

  const duration = 900;
  const start = performance.now();

  const tick = (now) => {
    const progress = Math.min(1, (now - start) / duration);
    const value = target * (1 - Math.pow(1 - progress, 3));
    node.textContent = `${Math.round(value)}${isPercent ? "%" : ""}`;
    if (progress < 1) requestAnimationFrame(tick);
  };

  requestAnimationFrame(tick);
}
