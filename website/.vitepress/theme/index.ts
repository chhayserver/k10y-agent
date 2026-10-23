import DefaultTheme from "vitepress/theme";
import type { Theme } from "vitepress";
import Home from "./components/Home.vue";
import Roadmap from "./components/Roadmap.vue";
import Shot from "./components/Shot.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component("Home", Home);
    app.component("Roadmap", Roadmap);
    app.component("Shot", Shot);
  },
} satisfies Theme;
