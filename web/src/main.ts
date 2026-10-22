import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import { setAuthHandlers } from "./api/client";
import router from "./router";
import { applyTheme } from "./stores/prefs";
import { useSession } from "./stores/session";
import "./styles/main.css";
import "./styles/app.css";

applyTheme();
const app = createApp(App).use(createPinia()).use(router);
setAuthHandlers(
  () => { const s = useSession(); if (s.me) { s.me = null; router.push({ path: "/login", query: { next: router.currentRoute.value.fullPath } }); } },
  () => router.push("/password"),
);
app.mount("#app");
