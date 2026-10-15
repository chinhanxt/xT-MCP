# SPDX-License-Identifier: Apache-2.0
"""xT spike: MCP server (official SDK mcp 2.x) + Keycloak JWT verify qua JWKS."""
import os
import jwt  # PyJWT
from jwt import PyJWKClient
import anyio
from pydantic import AnyHttpUrl
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer

ISSUER = os.environ.get("XT_ISSUER", "http://localhost:18080/realms/xt")
RESOURCE = os.environ.get("XT_RESOURCE", "http://localhost:18000/mcp")  # = audience
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
PORT = int(os.environ.get("XT_PORT", "18000"))


class KeycloakVerifier:
    """TokenVerifier: chữ ký (JWKS), exp, iss, aud đều được kiểm tra."""

    def __init__(self) -> None:
        self._jwks = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=300)

    def _decode(self, token: str) -> dict:
        key = self._jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256", "ES256", "PS256"],
            audience=RESOURCE, issuer=ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except Exception as e:  # sai chữ ký / hết hạn / sai iss / sai aud / không lấy được JWKS
            print(f"[auth] reject: {type(e).__name__}: {e}", flush=True)
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp", ""),
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            resource=RESOURCE,          # đã verify aud ở trên
            subject=claims.get("sub"),
            claims=claims,
        )


mcp = MCPServer(
    "xt-spike",
    token_verifier=KeycloakVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER),
        resource_server_url=AnyHttpUrl(RESOURCE),
        required_scopes=["openid"],
        validate_token_resource=False,  # verifier tự check aud
    ),
)


@mcp.tool()
def whoami() -> dict:
    """Trả về claims của access token hiện tại."""
    at = get_access_token()
    return {"subject": at.subject, "client_id": at.client_id, "scopes": at.scopes, "claims": at.claims}


if __name__ == "__main__":
    import uvicorn
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
