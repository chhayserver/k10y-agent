import { reactive } from "vue";
export interface AIRequest { scope: "cluster" | "namespace" | "object"; namespace?: string; kind?: string; name?: string; question?: string }
export const aiRequest = reactive<{ req: AIRequest; autorun: boolean }>({ req: { scope: "cluster" }, autorun: false });
