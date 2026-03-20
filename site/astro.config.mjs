import { defineConfig } from "astro/config";
import tailwind from "@astrojs/tailwind";
import preact from "@astrojs/preact";

export default defineConfig({
  site: "https://claudiotancredi.github.io",
  base: "/weekly-issue-arena/",
  integrations: [tailwind(), preact()],
  output: "static",
});
