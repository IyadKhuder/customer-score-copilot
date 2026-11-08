import { copyFileSync } from "node:fs";
for (const name of ["index.html", "style.css"])
  copyFileSync(name, `dist/${name}`);
