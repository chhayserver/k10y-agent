// Toasts, side panels (with Back history), action dialogs and row menus.
import type { Component } from "vue";
import { markRaw, reactive } from "vue";

export interface Toast { id: number; ok: boolean; message: string; key?: string }
export const toasts = reactive<Toast[]>([]);
let toastId = 0;
export function toast(ok: boolean, message: string, key?: string) {
  if (key) for (let i = toasts.length - 1; i >= 0; i--) if (toasts[i].key === key) toasts.splice(i, 1);
  const t = { id: ++toastId, ok, message, key };
  toasts.push(t);
  setTimeout(() => { const i = toasts.indexOf(t); if (i >= 0) toasts.splice(i, 1); }, ok ? 6000 : 12000);
}

/* Side panel with drill-down history. */
export interface PanelRef { type: "pod" | "resource" | "config"; kind: string; namespace: string; name: string; tab?: string }
export const panel = reactive<{ current: PanelRef | null; stack: PanelRef[] }>({ current: null, stack: [] });

export function openDetail(kind: string, namespace: string | null | undefined, name: string, opts: { push?: boolean; tab?: string } = {}) {
  const ns = namespace || "";
  let ref: PanelRef;
  if (opts.tab) ref = { type: "resource", kind, namespace: ns, name, tab: opts.tab };
  else if (kind === "Pod") ref = { type: "pod", kind, namespace: ns, name };
  else if (kind === "ConfigMap" || kind === "Secret") ref = { type: "config", kind, namespace: ns, name };
  else ref = { type: "resource", kind, namespace: ns, name, tab: "overview" };
  if (opts.push && panel.current) panel.stack.push(panel.current);
  else if (!opts.push) panel.stack.length = 0;
  panel.current = ref;
}
export function panelBack() { panel.current = panel.stack.pop() || null; }
export function closePanel() { panel.current = null; panel.stack.length = 0; }

/* Action dialog: a title, optional form component, and a request builder (with dry run). */
export interface ActionRequest { method?: "POST" | "DELETE"; path: string; body?: unknown; query?: Record<string, any> }
export interface ActionConfig {
  title: string;
  desc?: string;                        // may contain <b>; escaped by the caller
  danger?: boolean;
  confirmLabel?: string;
  confirmText?: string | null;          // user must type this
  noDryRun?: boolean;
  wide?: boolean;
  form?: Component;                     // edits `model`
  model?: Record<string, any>;
  /** Build the agent request (path relative to the cluster API). Throw Error for validation. */
  request?: (model: Record<string, any>, dryRun: boolean) => ActionRequest;
  /** Browser-only/hub action instead of an agent request. */
  run?: (model: Record<string, any>) => Promise<string | void> | string | void;
  after?: (res: any) => void;
}
export const action = reactive<{ cfg: ActionConfig | null }>({ cfg: null });
export function openAction(cfg: ActionConfig) {
  action.cfg = { ...cfg, form: cfg.form ? markRaw(cfg.form) : undefined, model: reactive(cfg.model || {}) };
}

/* Row action menu. */
export interface MenuItem { label?: string; icon?: string; danger?: boolean; disabled?: boolean; note?: string; sep?: boolean; run?: () => void }
export const menu = reactive<{ items: MenuItem[]; anchor: HTMLElement | null }>({ items: [], anchor: null });
export function openMenu(anchor: HTMLElement, items: MenuItem[]) {
  if (menu.anchor === anchor) { closeMenu(); return; }
  menu.items = items; menu.anchor = anchor;
}
export function closeMenu() { menu.anchor = null; menu.items = []; }
