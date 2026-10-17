import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, scrypt as scryptCallback, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { Store } from './store.ts';
import { HttpError, text } from './validation.ts';
import { body, hashToken } from './http.ts';
import { routeMcp } from './mcp-routes.ts';
import { routeResources } from './resources.ts';
import { routeApps } from './apps.ts';
import { routeAppManagement } from './app-management.ts';
import { publicAppRequest } from './public-app.ts';
import type { RouteContext } from './http.ts';
import { assertionVerifier, authorizeStudioRoute, studioSecret } from './xt-auth.ts';
import { xtTools } from './xt-tools.ts';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const scrypt = promisify(scryptCallback);
async function passwordHash(value: string, salt = randomBytes(16).toString('hex')) {
  return `${salt}:${((await scrypt(value, salt, 64)) as Buffer).toString('hex')}`;
}
export function createStudio(store: Store, options: { log?: boolean; integrated?: boolean; secret?: Buffer } = {}) {
  const integrated = options.integrated ?? process.env.STUDIO_XT_MODE === '1';
  const secret = integrated ? options.secret ?? studioSecret() : undefined;
  const verify = secret ? assertionVerifier(secret) : undefined;
  const busy = new Set<string>();
  const loginAttempts = new Map<string, { count: number; until: number }>();
  return createServer(async (req, res) => {
    const started = Date.now();
    const url = new URL(req.url ?? '/', 'http://localhost');
    let path = url.pathname;
    const method = req.method ?? 'GET';
    let status = 200;
    let requestId = '';
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader(
      'Content-Security-Policy',
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    );
    const send = (value: unknown, code = 200) => {
      status = code;
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(value));
    };
    try {
      let apiAccess: RouteContext['apiAccess'];
      if (integrated && (path.startsWith('/public/') || path.startsWith('/v1/') || path.startsWith('/s/')))
        throw new HttpError(404, 'Tính năng này không hỗ trợ trong Studio tích hợp.');
      const shared = integrated ? null : publicAppRequest(store, req, res, path, method);
      if (shared) {
        if (shared.metadata) {
          send(shared.metadata);
          return;
        }
        apiAccess = shared.access;
        path = shared.path;
      } else if (path.startsWith('/public/apps/')) throw new HttpError(404, 'Link chia sẻ không hợp lệ.');
      const publicRoute = path.match(/^\/v1\/apps\/([\w-]+)(\/run)?$/);
      if (publicRoute) {
        const bearer = req.headers.authorization?.match(/^Bearer (\S+)$/)?.[1] ?? '';
        const access = store.db
          .prepare('SELECT id,workspace_id FROM app_keys WHERE token_hash=? AND app_id=?')
          .get(hashToken(bearer), publicRoute[1]);
        if (!access) throw new HttpError(401, 'API key không hợp lệ hoặc đã thu hồi.');
        apiAccess = { workspace_id: String(access.workspace_id), audience: 'key:' + access.id };
        if (method === 'GET' && !publicRoute[2]) {
          const app = store.app(apiAccess.workspace_id, publicRoute[1]);
          if (!app.published) throw new HttpError(409, 'Ứng dụng chưa publish.');
          send({ id: app.id, name: app.name, description: app.description, kind: app.kind, version: app.version });
          return;
        }
        if (method !== 'POST' || !publicRoute[2]) throw new HttpError(405, 'Chỉ hỗ trợ GET app và POST run.');
        path = `/api/apps/${publicRoute[1]}/run`;
      }
      if (!path.startsWith('/api/')) {
        if (method !== 'GET' && method !== 'HEAD') throw new HttpError(405, 'Method không được hỗ trợ.');
        const files: Record<string, [string, string]> = { '/studio/app.js': ['app.js', 'text/javascript'], '/studio/app.css': ['app.css', 'text/css'], '/app.js': ['app.js', 'text/javascript'], '/app.css': ['app.css', 'text/css'] };
        const [file, mime] = files[path] ?? ['index.html', 'text/html'];
        try {
          let bytes = await readFile(join(root, 'dist', file));
          if (integrated && file === 'index.html')
            bytes = Buffer.from(bytes.toString('utf8').replaceAll('href="/app.css"', 'href="app.css"').replaceAll('src="/app.js"', 'src="app.js"'));
          res.writeHead(200, { 'Content-Type': `${mime}; charset=utf-8`, 'Cache-Control': 'no-cache' });
          res.end(method === 'HEAD' ? undefined : bytes);
        } catch {
          if (!res.headersSent) send({ error: 'Chạy npm run build trước.' }, 503);
          else res.end();
        }
        return;
      }
      if (!integrated && !['GET', 'HEAD'].includes(method)) {
        const origin = req.headers.origin;
        if (origin && origin !== `http://${req.headers.host}` && origin !== `https://${req.headers.host}`)
          throw new HttpError(403, 'Origin không hợp lệ.');
        if (req.headers['sec-fetch-site'] === 'cross-site') throw new HttpError(403, 'Yêu cầu cross-site bị từ chối.');
      }
      const identity = verify ? await verify(req) : undefined;
      requestId = identity?.request_id ?? '';
      if (identity) authorizeStudioRoute(path, method, identity.role);
      const coreTools = identity?.capability && process.env.STUDIO_XT_CORE_URL && secret
        ? xtTools(process.env.STUDIO_XT_CORE_URL, secret, identity.capability) : undefined;
      if (path === '/api/xt/tools' && method === 'GET') {
        if (!coreTools) throw new HttpError(503, 'Kết nối công cụ xT chưa được cấu hình.');
        send({ tools: await coreTools.list() });
        return;
      }
      const token =
        req.headers.cookie
          ?.split(';')
          .map((x) => x.trim())
          .find((x) => x.startsWith('studio_session='))
          ?.slice(15) ?? '';
      const xtWorkspace = identity ? store.transaction(() => {
        let mapped = store.db.prepare('SELECT workspace_id FROM xt_workspace_map WHERE tenant_id=?').get(identity.tenant);
        if (!mapped) {
          const id = store.id();
          store.db.prepare('INSERT INTO workspaces(id,name) VALUES(?,?)').run(id, identity.workspace_name);
          store.db.prepare('INSERT INTO xt_workspace_map(tenant_id,workspace_id) VALUES(?,?)').run(identity.tenant, id);
          mapped = { workspace_id: id };
        }
        return String(mapped.workspace_id);
      }) : undefined;
      const session = identity ? { id: identity.person, workspace_id: xtWorkspace, email: identity.name, workspace_name: identity.workspace_name, role: identity.role } :
        apiAccess ??
        store.db
          .prepare(
            'SELECT u.id,u.workspace_id,u.email,w.name AS workspace_name FROM sessions s JOIN users u ON s.user_id=u.id JOIN workspaces w ON w.id=u.workspace_id WHERE s.token=? AND s.expires>?',
          )
          .get(hashToken(token), Date.now());
      if (path === '/api/session' && method === 'GET') {
        send({ needs_setup: integrated ? false : !store.db.prepare('SELECT id FROM users LIMIT 1').get(), user: session ?? null, integrated });
        return;
      }
      if (integrated && ['/api/setup', '/api/login', '/api/logout'].includes(path))
        throw new HttpError(404, 'Đăng nhập cục bộ không hỗ trợ trong Studio tích hợp.');
      const issueSession = (userId: string) => {
        const raw = randomBytes(32).toString('hex');
        store.db.prepare('DELETE FROM sessions WHERE expires<?').run(Date.now());
        store.db.prepare('INSERT INTO sessions VALUES(?,?,?)').run(hashToken(raw), userId, Date.now() + 12 * 3600000);
        res.setHeader(
          'Set-Cookie',
          `studio_session=${raw}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200${'encrypted' in req.socket && req.socket.encrypted ? '; Secure' : ''}`,
        );
      };
      if (path === '/api/setup' && method === 'POST') {
        if (store.db.prepare('SELECT id FROM users LIMIT 1').get()) throw new HttpError(409, 'Workspace đã được thiết lập.');
        const v = await body(req),
          email = text(v.email, 'Email', 200).trim().toLowerCase(),
          password = text(v.password, 'Mật khẩu', 200),
          name = text(v.workspace_name, 'Workspace', 100);
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || password.length < 8)
          throw new HttpError(400, 'Email hợp lệ và mật khẩu ít nhất 8 ký tự.');
        const hash = await passwordHash(password),
          workspace = store.id(),
          user = store.id();
        store.transaction(() => {
          if (store.db.prepare('SELECT id FROM users LIMIT 1').get()) throw new HttpError(409, 'Workspace đã được thiết lập.');
          store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run(workspace, name);
          store.db.prepare('INSERT INTO users VALUES(?,?,?,?)').run(user, workspace, email, hash);
        });
        issueSession(user);
        send({ ok: true }, 201);
        return;
      }
      if (path === '/api/login' && method === 'POST') {
        const ip = req.socket.remoteAddress ?? 'local',
          attempt = loginAttempts.get(ip);
        if (attempt && attempt.until > Date.now() && attempt.count >= 10) throw new HttpError(429, 'Thử lại sau 5 phút.');
        const v = await body(req),
          email = text(v.email, 'Email', 200).trim().toLowerCase(),
          password = text(v.password, 'Mật khẩu', 200);
        const user = store.db.prepare('SELECT id,password FROM users WHERE email=?').get(email),
          saved = String(user?.password ?? '00000000000000000000000000000000:' + '0'.repeat(128));
        const computed = await passwordHash(password, saved.split(':')[0]);
        if (!user || !timingSafeEqual(Buffer.from(saved), Buffer.from(computed))) {
          loginAttempts.set(ip, { count: attempt && attempt.until > Date.now() ? attempt.count + 1 : 1, until: Date.now() + 300000 });
          throw new HttpError(401, 'Email hoặc mật khẩu chưa đúng.');
        }
        loginAttempts.delete(ip);
        issueSession(String(user.id));
        send({ ok: true });
        return;
      }
      if (!session) throw new HttpError(401, 'Cần đăng nhập.');
      const workspace = String(session.workspace_id);
      if (path === '/api/logout' && method === 'POST') {
        store.db.prepare('DELETE FROM sessions WHERE token=?').run(hashToken(token));
        res.setHeader('Set-Cookie', 'studio_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0');
        send({ ok: true });
        return;
      }
      const context = { store, req, res, path, url, method, workspace, send, busy, apiAccess, coreTools,
        identity: identity ? { person: identity.person, role: identity.role } : undefined };
      if ((await routeMcp(context)) || (await routeResources(context)) || (await routeAppManagement(context)) || (await routeApps(context)))
        return;
      throw new HttpError(404, 'Không tìm thấy API.');
    } catch (error) {
      if (!res.headersSent && !res.destroyed)
        send(
          {
            error: error instanceof HttpError ? error.message : 'Lỗi nội bộ. Xem log server.',
            ...((error as { trace?: unknown }).trace ? { trace: (error as { trace?: unknown }).trace } : {}),
          },
          error instanceof HttpError ? error.status : 500,
        );
      if (!(error instanceof HttpError)) console.error('request failed', error instanceof Error ? error.stack : 'unknown');
    } finally {
      if (options.log)
        console.log(
          `${method} ${path.startsWith('/public/apps/') ? '/public/apps/:share_id' : path} ${status} ${Date.now() - started}ms${requestId ? ' request_id=' + requestId : ''}`,
        );
    }
  });
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const store = new Store(process.env.STUDIO_DATA_DIR || join(root, '.data')),
    server = createStudio(store, { log: process.env.LOG_LEVEL !== 'silent' });
  store.db.prepare("UPDATE runs SET status='error',error='Server đã khởi động lại khi đang chạy.' WHERE status='running'").run();
  store.db
    .prepare(
      "UPDATE documents SET status=CASE WHEN EXISTS(SELECT 1 FROM chunks WHERE document_id=documents.id) THEN 'ready' ELSE 'error' END,error='Server khởi động lại khi lập chỉ mục; hãy thử lại.' WHERE status='indexing'",
    )
    .run();
  server.listen(Number(process.env.PORT || 4317), '127.0.0.1', () =>
    console.log(`xT Studio: http://127.0.0.1:${process.env.PORT || 4317}`),
  );
  const stop = () => {
    server.close(() => {
      store.close();
      process.exit(0);
    });
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}
