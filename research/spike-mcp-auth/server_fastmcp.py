# SPDX-License-Identifier: Apache-2.0
"""So sánh: cùng việc bằng fastmcp 4.x KeycloakAuthProvider (port 18001)."""
from fastmcp import FastMCP
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from fastmcp.server.dependencies import get_access_token

auth = KeycloakAuthProvider(realm_url="http://localhost:18080/realms/xt",
                            base_url="http://localhost:18001",
                            audience="http://localhost:18000/mcp")
mcp = FastMCP("xt-fastmcp", auth=auth)

@mcp.tool
def whoami() -> dict:
    return get_access_token().claims

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=18001, stateless_http=True)
