// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// DPoP (RFC 9449): khóa ECDSA P-256 non-extractable của Desk, giữ trong IndexedDB (CryptoKey, không export private key).
// proof() ký JWT `dpop+jwt` (ES256) cho từng request; thumbprint() là jkt theo RFC 7638.

export interface KeyStore {
  get(): Promise<CryptoKeyPair | undefined>;
  put(pair: CryptoKeyPair): Promise<void>;
  /** Xóa cặp khóa (đăng xuất, audit DPA-03): lần ký sau sinh khóa mới. */
  clear(): Promise<void>;
}

export interface PublicJwk {
  crv: "P-256";
  kty: "EC";
  x: string;
  y: string;
}

/** Bề mặt tối thiểu mà oidc.ts / pii.ts dùng. */
export interface DpopProver {
  thumbprint(): Promise<string>;
  proof(htm: string, htu: string, opts?: { accessToken?: string; ath?: string }): Promise<string>;
  /** Bỏ khóa hiện tại (xóa khỏi kho); người đăng nhập sau nhận jkt khác. */
  reset?(): Promise<void>;
}

export const b64url = (b: ArrayBuffer | Uint8Array): string => {
  const u = b instanceof Uint8Array ? b : new Uint8Array(b);
  let s = "";
  for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
const b64json = (o: unknown) => b64url(new TextEncoder().encode(JSON.stringify(o)));

/** base64url(SHA-256(ASCII value)): ath của RFC 9449 §4.2. */
export async function athOf(value: string, subtle: SubtleCrypto = globalThis.crypto.subtle): Promise<string> {
  return b64url(await subtle.digest("SHA-256", new TextEncoder().encode(value)));
}

/** htu: URL tuyệt đối, bỏ query + fragment, scheme/host chữ thường, bỏ cổng mặc định. */
export function normalizeHtu(htu: string, base?: string): string {
  const u = new URL(htu, base ?? globalThis.location?.href);
  return u.origin + u.pathname;
}

/** Kho trong bộ nhớ (test, hoặc khi không có IndexedDB): sống theo vòng đời trang. */
export function memoryKeyStore(): KeyStore {
  let pair: CryptoKeyPair | undefined;
  return {
    get: async () => pair,
    put: async (p) => void (pair = p),
    clear: async () => void (pair = undefined),
  };
}

const DB = "xt-desk";
const STORE = "keys";
const KEY = "dpop";

/** IndexedDB db "xt-desk", store "keys", khóa "dpop": lưu nguyên CryptoKey (structured clone), không export. */
export function indexedDbKeyStore(idb: IDBFactory | undefined = globalThis.indexedDB): KeyStore {
  const open = () =>
    new Promise<IDBDatabase>((res, rej) => {
      if (!idb) return rej(new Error("IndexedDB không khả dụng"));
      const r = idb.open(DB, 1);
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains(STORE)) r.result.createObjectStore(STORE);
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error ?? new Error("Mở IndexedDB thất bại"));
    });
  const tx = async <T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>) => {
    const db = await open();
    try {
      return await new Promise<T>((res, rej) => {
        const t = db.transaction(STORE, mode);
        const req = fn(t.objectStore(STORE));
        t.oncomplete = () => res(req.result);
        t.onerror = t.onabort = () => rej(t.error ?? new Error("Giao dịch IndexedDB thất bại"));
      });
    } finally {
      db.close();
    }
  };
  return {
    async get() {
      const v = (await tx("readonly", (s) => s.get(KEY))) as CryptoKeyPair | undefined;
      return v?.privateKey && v.publicKey ? { privateKey: v.privateKey, publicKey: v.publicKey } : undefined;
    },
    async put(p) {
      await tx("readwrite", (s) => s.put({ privateKey: p.privateKey, publicKey: p.publicKey }, KEY));
    },
    async clear() {
      await tx("readwrite", (s) => s.delete(KEY));
    },
  };
}

export interface DpopOptions {
  store?: KeyStore;
  subtle?: SubtleCrypto;
  now?: () => number; // ms
  base?: () => string; // gốc để resolve htu tương đối (mặc định location.href)
}

interface Loaded {
  pair: CryptoKeyPair;
  jwk: PublicJwk;
  jkt: string;
}

export class DpopSigner implements DpopProver {
  private subtle: SubtleCrypto;
  private store: KeyStore;
  private loaded: Promise<Loaded> | null = null;

  constructor(private o: DpopOptions = {}) {
    this.subtle = o.subtle ?? globalThis.crypto.subtle;
    this.store = o.store ?? memoryKeyStore();
  }

  /** Đăng xuất: quên cặp khóa trong bộ nhớ và xóa khỏi kho (IndexedDB). */
  async reset(): Promise<void> {
    this.loaded = null;
    try {
      await this.store.clear();
    } catch {
      /* kho không dùng được: khóa vốn chỉ sống trong bộ nhớ, đã bỏ ở trên */
    }
  }

  private gen() {
    return this.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]) as Promise<CryptoKeyPair>;
  }

  /** Nạp cặp khóa đã lưu, chưa có thì sinh + lưu. Kho lỗi (không có IndexedDB) => cặp khóa trong bộ nhớ cho trang này. */
  private async loadPair(): Promise<CryptoKeyPair> {
    const store = this.store;
    let pair: CryptoKeyPair | undefined;
    try {
      pair = await store.get();
    } catch {
      return this.gen();
    }
    if (pair) return pair;
    pair = await this.gen();
    try {
      await store.put(pair);
    } catch {
      /* không lưu được: dùng khóa cho vòng đời trang */
    }
    return pair;
  }

  private keys(): Promise<Loaded> {
    this.loaded ??= (async () => {
      const pair = await this.loadPair();
      const j = await this.subtle.exportKey("jwk", pair.publicKey);
      if (j.kty !== "EC" || j.crv !== "P-256" || !j.x || !j.y) throw new Error("Khóa DPoP không phải EC P-256");
      const jwk: PublicJwk = { crv: "P-256", kty: "EC", x: j.x, y: j.y };
      // RFC 7638: đúng thứ tự crv,kty,x,y, không khoảng trắng
      const jkt = b64url(await this.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(jwk))));
      return { pair, jwk, jkt };
    })();
    this.loaded.catch(() => (this.loaded = null));
    return this.loaded;
  }

  async publicJwk(): Promise<PublicJwk> {
    return { ...(await this.keys()).jwk };
  }

  async thumbprint(): Promise<string> {
    return (await this.keys()).jkt;
  }

  async proof(htm: string, htu: string, opts: { accessToken?: string; ath?: string } = {}): Promise<string> {
    const { pair, jwk } = await this.keys();
    const header = { typ: "dpop+jwt", alg: "ES256", jwk };
    const payload: Record<string, unknown> = {
      jti: b64url(globalThis.crypto.getRandomValues(new Uint8Array(16))),
      htm: htm.toUpperCase(),
      htu: normalizeHtu(htu, this.o.base?.()),
      iat: Math.floor((this.o.now ?? Date.now)() / 1000),
    };
    const ath = opts.accessToken !== undefined ? await athOf(opts.accessToken, this.subtle) : opts.ath;
    if (ath !== undefined) payload.ath = ath;
    const input = `${b64json(header)}.${b64json(payload)}`;
    // WebCrypto ECDSA trả r||s 64 byte (IEEE P1363) = định dạng chữ ký JWS ES256
    const sig = await this.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, pair.privateKey, new TextEncoder().encode(input));
    return `${input}.${b64url(sig)}`;
  }
}
