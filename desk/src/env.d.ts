// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
declare const __DESK_CONFIG__: {
  coreSkill: string;
  model: string;
  reasoningEffort: string;
  toolProfile: string;
  maxSteps: number;
  sandboxUrl: string;
  xtOrigin: string;
  oidcIssuer: string;
  oidcClientId: string;
  oidcScope: string;
  appCallsPerSec: number;
  appCallBurst: number;
  appMaxInflight: number;
  appMaxArgsBytes: number;
  appGlobalCallsPerSec: number;
  appGlobalBurst: number;
};

interface Window { xtPortal?: import("./portal").PortalHost }
