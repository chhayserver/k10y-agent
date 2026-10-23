import { defineConfig } from "vitepress";

const repo = "https://github.com/zorouzumaki1111-cmd/k10y-agent";

export default defineConfig({
  title: "k10y-agent",
  description: "Monitor and control every Kubernetes cluster from one dashboard: live metrics, logs, alerts, roles, audit and an AI assistant.",
  lang: "en-US",
  cleanUrls: true,
  lastUpdated: true,
  appearance: true,
  head: [
    ["link", { rel: "icon", href: "/logo.svg", type: "image/svg+xml" }],
    ["meta", { name: "theme-color", content: "#2b2a8f" }],
    ["meta", { property: "og:title", content: "k10y-agent: every cluster, one calm place" }],
    ["meta", { property: "og:description", content: "Open-source Kubernetes monitoring and control with roles, audit, alerts, live logs, pod shell and AI." }],
    ["meta", { property: "og:image", content: "/shots/overview.png" }],
  ],
  themeConfig: {
    logo: { src: "/logo.svg", alt: "" },
    siteTitle: "k10y-agent",
    nav: [
      { text: "Product", link: "/#features" },
      { text: "Use cases", link: "/#use-cases" },
      { text: "Docs", link: "/docs/", activeMatch: "/docs/" },
      { text: "Roadmap", link: "/roadmap" },
      { text: "Changelog", link: "/changelog" },
      { text: "Security", link: "/security" },
    ],
    sidebar: {
      "/docs/": [
        { text: "Getting started", items: [
          { text: "What is k10y?", link: "/docs/" },
          { text: "Quick start", link: "/docs/quick-start" },
          { text: "Architecture", link: "/docs/architecture" },
        ] },
        { text: "Install", items: [
          { text: "Hub (Docker Compose)", link: "/docs/install-hub" },
          { text: "Agent on a cluster", link: "/docs/install-agent" },
          { text: "Connect clusters", link: "/docs/clusters" },
          { text: "Upgrading", link: "/docs/upgrading" },
        ] },
        { text: "Using k10y", items: [
          { text: "Dashboard tour", link: "/docs/dashboard" },
          { text: "Logs and live logs", link: "/docs/logs" },
          { text: "Actions and safety", link: "/docs/actions" },
          { text: "Edit YAML", link: "/docs/edit-yaml" },
          { text: "Pod shell", link: "/docs/shell" },
          { text: "Alerts and Telegram", link: "/docs/alerts" },
          { text: "AI assistant", link: "/docs/ai" },
          { text: "HAProxy", link: "/docs/haproxy" },
        ] },
        { text: "Administration", items: [
          { text: "Users, roles and permissions", link: "/docs/users-roles" },
          { text: "Sessions and account security", link: "/docs/sessions" },
          { text: "Audit log", link: "/docs/audit" },
          { text: "Backups and restore", link: "/docs/backups" },
          { text: "Configuration reference", link: "/docs/configuration" },
        ] },
        { text: "Reference", items: [
          { text: "CLI", link: "/docs/cli" },
          { text: "Agent API", link: "/docs/api" },
          { text: "Troubleshooting & FAQ", link: "/docs/troubleshooting" },
        ] },
      ],
    },
    socialLinks: [{ icon: "github", link: repo }],
    search: { provider: "local" },
    editLink: { pattern: `${repo}/edit/main/website/:path`, text: "Edit this page on GitHub" },
    outline: { level: [2, 3] },
    footer: {
      message: "Free and open source. Kubernetes is a trademark of The Linux Foundation; k10y is not affiliated with it.",
      copyright: "© 2026 k10y-agent contributors",
    },
  },
});
