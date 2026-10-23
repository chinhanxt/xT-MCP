// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// Be Vietnam Pro (OFL-1.1) đóng gói từ @fontsource lúc build, không tải từ CDN (CSP font-src 'self').
import "@fontsource/be-vietnam-pro/400.css";
import "@fontsource/be-vietnam-pro/500.css";
import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/700.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
