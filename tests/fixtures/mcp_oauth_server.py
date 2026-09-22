"""An MCP 2 server that is also its own OAuth authorization server.

Usage: python mcp_oauth_server.py <port> [streamable-http|sse]

Authorization is approved automatically, so a test can drive the whole flow
(discovery, dynamic client registration, PKCE, authorization redirect with
RFC 9207 ``iss``, token exchange) without a person. Tools require a token.
"""

from __future__ import annotations

import secrets
import sys
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

PORT = int(sys.argv[1])
TRANSPORT = sys.argv[2] if len(sys.argv) > 2 else "streamable-http"
ISSUER = f"http://127.0.0.1:{PORT}"


class AutoApproveProvider(OAuthAuthorizationServerProvider):
    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.tokens: dict[str, AccessToken] = {}

    async def get_client(self, client_id):
        return self.clients.get(client_id)

    async def register_client(self, client_info):
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state, iss=ISSUER
        )

    async def load_authorization_code(self, client, authorization_code):
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(self, client, authorization_code):
        self.codes.pop(authorization_code.code, None)
        token = secrets.token_urlsafe(24)
        self.tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + 3600,
            resource=authorization_code.resource,
        )
        return OAuthToken(access_token=token, token_type="Bearer", expires_in=3600)

    async def load_access_token(self, token):
        return self.tokens.get(token)

    async def load_refresh_token(self, client, refresh_token) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        raise NotImplementedError

    async def exchange_identity_assertion(self, client, params):
        raise NotImplementedError

    async def revoke_token(self, token):
        self.tokens.pop(getattr(token, "token", ""), None)


app = MCPServer(
    "oauth-probe",
    auth_server_provider=AutoApproveProvider(),
    auth=AuthSettings(
        issuer_url=ISSUER,
        resource_server_url=f"{ISSUER}/mcp" if TRANSPORT == "streamable-http" else f"{ISSUER}/sse",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
)


@app.tool()
def whoami(ctx: Context) -> str:
    """Confirms the call was authorized."""
    return "authorized"


if __name__ == "__main__":
    app.run(TRANSPORT, port=PORT)
