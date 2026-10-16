// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// DPoP (RFC 9449) của Desk: proof ES256, khóa non-extractable, jkt RFC 7638, ath, chuẩn hóa htu.
import { describe, expect, it } from "vitest";
import { createHash, webcrypto } from "node:crypto";
import { athOf, b64url, DpopSigner, memoryKeyStore, normalizeHtu, type KeyStore } from "../src/dpop";

const subtle = webcrypto.subtle as SubtleCrypto;
const dec = (s: string) => JSON.parse(Buffer.from(s, "base64url").toString("utf8"));
const parse = (jws: string) => {
  const [h, p, s] = jws.split(".");
  return { h: dec(h), p: dec(p), sig: Buffer.from(s, "base64url"), input: `${h}.${p}`, raw: s };
};
const T0 = 1_760_000_000_123;
const signer = (store: KeyStore = memoryKeyStore()) => new DpopSigner({ store, subtle, now: () => T0, base: () => "https://desk.example/chat/x?y=1" });

describe("proof", () => {
  it("header/payload đúng dạng RFC 9449", async () => {
    const s = signer();
    const { h, p, raw } = parse(await s.proof("post", "https://xt-mcp.example/mcp?a=1#f"));
    expect(Object.keys(h)).toEqual(["typ", "alg", "jwk"]);
    expect(h).toMatchObject({ typ: "dpop+jwt", alg: "ES256" });
    expect(Object.keys(h.jwk)).toEqual(["crv", "kty", "x", "y"]);
    expect(h.jwk.crv).toBe("P-256");
    expect(h.jwk.kty).toBe("EC");
    expect(h.jwk.d).toBeUndefined();
    expect(Object.keys(p)).toEqual(["jti", "htm", "htu", "iat"]);
    expect(p.htm).toBe("POST");
    expect(p.htu).toBe("https://xt-mcp.example/mcp");
    expect(p.iat).toBe(Math.floor(T0 / 1000));
    expect(Buffer.from(p.jti, "base64url").length).toBe(16);
    expect(raw).not.toMatch(/[=+/]/);
  });
  it("chữ ký verify được bằng khóa công khai (P1363 r||s 64 byte)", async () => {
    const s = signer();
    const { h, sig, input } = parse(await s.proof("GET", "https://x.example/a"));
    expect(sig.length).toBe(64);
    const pub = await subtle.importKey("jwk", h.jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    expect(await subtle.verify({ name: "ECDSA", hash: "SHA-256" }, pub, sig, new TextEncoder().encode(input))).toBe(true);
    const bad = Buffer.from(input + "x");
    expect(await subtle.verify({ name: "ECDSA", hash: "SHA-256" }, pub, sig, bad)).toBe(false);
  });
  it("jti mới cho mỗi proof", async () => {
    const s = signer();
    const jtis = new Set<string>();
    for (let i = 0; i < 20; i++) jtis.add(parse(await s.proof("POST", "https://x.example/mcp")).p.jti);
    expect(jtis.size).toBe(20);
  });
  it("ath = base64url(sha256(access token)), khớp node:crypto; ath tính sẵn được dùng nguyên", async () => {
    const at = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2ln";
    const want = createHash("sha256").update(at, "ascii").digest("base64url");
    expect(await athOf(at, subtle)).toBe(want);
    // vector RFC 9449 §7.1
    expect(await athOf("Kz~8mXK1EalYznwH-LC-1fBAo.4Ljp~zsPE_NeO.gxU", subtle)).toBe("fUHyO2r2Z3DZ53EsNrWBb0xWXoaNy59IiKCAqksmQEo");
    const s = signer();
    expect(parse(await s.proof("GET", "https://x.example/r", { accessToken: at })).p.ath).toBe(want);
    expect(parse(await s.proof("GET", "https://x.example/r", { ath: "precomputed" })).p.ath).toBe("precomputed");
  });
});

describe("khóa", () => {
  it("private key non-extractable: exportKey ném lỗi", async () => {
    const pairs: CryptoKeyPair[] = [];
    const store: KeyStore = { get: async () => pairs[0], put: async (p) => void pairs.push(p), clear: async () => void pairs.splice(0) };
    await signer(store).thumbprint();
    expect(pairs[0].privateKey.extractable).toBe(false);
    await expect(subtle.exportKey("jwk", pairs[0].privateKey)).rejects.toThrow();
    await expect(subtle.exportKey("pkcs8", pairs[0].privateKey)).rejects.toThrow();
  });
  it("lưu qua store và dùng lại sau khi 'tải lại' (cùng thumbprint)", async () => {
    const store = memoryKeyStore();
    const a = await signer(store).thumbprint();
    const b = await signer(store).thumbprint();
    expect(b).toBe(a);
    expect(await signer(memoryKeyStore()).thumbprint()).not.toBe(a);
  });
  it("store lỗi (không có IndexedDB) => vẫn có khóa cho vòng đời trang", async () => {
    const broken: KeyStore = { get: async () => Promise.reject(new Error("no idb")), put: async () => Promise.reject(new Error("no idb")), clear: async () => Promise.reject(new Error("no idb")) };
    const s = signer(broken);
    const t = await s.thumbprint();
    expect(await s.thumbprint()).toBe(t);
    expect(parse(await s.proof("GET", "https://x.example/")).h.jwk).toBeDefined();
  });
  it("thumbprint = RFC 7638 tính độc lập", async () => {
    const s = signer();
    const { h } = parse(await s.proof("GET", "https://x.example/"));
    const canon = `{"crv":"P-256","kty":"EC","x":"${h.jwk.x}","y":"${h.jwk.y}"}`;
    expect(await s.thumbprint()).toBe(createHash("sha256").update(canon).digest("base64url"));
    expect(await s.publicJwk()).toEqual({ crv: "P-256", kty: "EC", x: h.jwk.x, y: h.jwk.y });
  });
});

describe("htu", () => {
  it("bỏ query/fragment, bỏ cổng mặc định, chữ thường scheme/host, resolve URL tương đối", () => {
    expect(normalizeHtu("https://Xt.Example:443/pii/reveal?x=1#y")).toBe("https://xt.example/pii/reveal");
    expect(normalizeHtu("HTTP://h.example:80/a")).toBe("http://h.example/a");
    expect(normalizeHtu("http://h.example:8080/a")).toBe("http://h.example:8080/a");
    expect(normalizeHtu("/mcp?s=1", "https://desk.example/chat")).toBe("https://desk.example/mcp");
  });
  it("proof resolve htu tương đối theo base", async () => {
    expect(parse(await signer().proof("get", "../mcp#z")).p).toMatchObject({ htm: "GET", htu: "https://desk.example/mcp" });
  });
  it("b64url không padding", () => {
    expect(b64url(new Uint8Array([0xfb, 0xff]))).toBe("-_8");
  });
});

describe("reset (đăng xuất)", () => {
  it("xóa cặp khóa khỏi kho, lần ký sau là khóa mới", async () => {
    const store = memoryKeyStore();
    const s = new DpopSigner({ store, subtle: webcrypto.subtle as SubtleCrypto });
    const a = await s.thumbprint();
    await s.reset();
    expect(await store.get()).toBeUndefined();
    const b = await s.thumbprint();
    expect(b).not.toBe(a);
    expect(await store.get()).toBeDefined();
  });
});

describe("đăng xuất giữa lúc sinh khóa lần đầu (DPA-08)", () => {
  it("không ghi lại khóa cũ vào kho; người sau nhận khóa mới", async () => {
    const store = memoryKeyStore();
    const s = new DpopSigner({ store, subtle: webcrypto.subtle as SubtleCrypto });
    const inflight = s.thumbprint(); // đang sinh khóa
    await s.reset(); // đăng xuất đúng lúc đó
    const old = await inflight;
    expect(await store.get()).toBeUndefined();
    const after = await new DpopSigner({ store, subtle: webcrypto.subtle as SubtleCrypto }).thumbprint();
    expect(after).not.toBe(old);
  });
});
